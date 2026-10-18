import bisect
import gc
import json
import os
import re
import statistics
import tempfile
import threading
import wave
from dataclasses import replace
from pathlib import Path

from linsub.config import HF_HOME, MEDIA_PROCESS_TIMEOUT_SECONDS, MODELS_DIR, TMP_DIR

# Local VAD must not enable Pyannote's opt-out usage telemetry.
os.environ["PYANNOTE_METRICS_ENABLED"] = "false"

import numpy as np
import torch
import torchaudio
import whisperx
import whisperx.audio as _whisper_audio

from linsub.core.dependency_security import install_lightning_checkpoint_guard
from linsub.core.hardware import runtime_profile
from linsub.core.whisper_compute import whisper_compute_type
from linsub.core.model_integrity import (
    ALIGNMENT_MODELS,
    ModelIntegrityError,
    WHISPERX_VAD_SHA256,
    WHISPERX_VAD_SIZE,
    verify_alignment_model,
    verify_whisper_model,
    verify_whisper_turbo_model,
    verify_whisperx_vad_model,
)
from linsub.pipeline.process_registry import check_cancellation, communicate_process, is_cancelled
from linsub.pipeline.timing_contract import TIMING_SOURCE
from linsub.services.video_store import log_to_video
from linsub.utils.ffmpeg import _binary, get_video_duration
from linsub.utils.media_subprocess import _MediaSubprocess

# WhisperX decodes via its own subprocess.run, not our FFmpeg helpers.
_whisper_audio.subprocess = _MediaSubprocess()


# WhisperX imports Lightning before any model is opened. Backport Lightning's
# upstream checkpoint allowlist before the first LinSub model load.
install_lightning_checkpoint_guard()


_MODEL_LOCK = threading.Lock()
_ALIGNMENT_PATCH_LOCK = threading.RLock()
_WARM_ASR_MODEL = None
_WARM_DEVICE = None
_WARM_MODEL_NAME = None
_AUDIO_SAMPLE_RATE = 16000
_SEGMENT_LANGUAGE_CONFIDENCE = 0.55
_ALIGNMENT_MIN_COVERAGE_RATIO = 0.55
_ALIGNMENT_MIN_MEDIAN_WORD_SCORE = 0.03
_ALIGNMENT_GROUP_PADDING_SECONDS = 2.5
_ALIGNMENT_GROUP_SPLIT_GAP_SECONDS = 3.0
_ALIGNMENT_GROUP_MAX_SECONDS = 75.0
_MIN_SENTENCE_SPAN_SECONDS = 0.45
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
_CJK_LANGUAGE_CODES = frozenset({"zh", "ja", "ko"})
_SENTENCE_END_CHARS = frozenset(".!?\u2026\u3002\uff01\uff1f")
_SENTENCE_CLOSERS = frozenset("\"'\u2019\u201d)]}\u3009\u300b\u300d\u300f\u3011")
_WHISPERX_VAD_SIZE = WHISPERX_VAD_SIZE
_WHISPERX_VAD_SHA256 = WHISPERX_VAD_SHA256
# WhisperX's Hugging Face alignment table is mutable and most entries expose
# pickle checkpoints without safetensors. Production accepts only these fixed
# torchaudio assets, verified on every load before PyTorch deserializes them.
_VERIFIED_ALIGNMENT_MODELS = ALIGNMENT_MODELS


def _whisper_model_source(model_name: str = "small") -> tuple[str, bool]:
    """Resolve only the checksum-verified model installed by Resource Manager."""
    normalized = str(model_name or "small").strip().lower()
    if normalized not in {"small", "large-v3-turbo"}:
        raise RuntimeError(f"Unsupported Whisper model: {model_name}")
    candidate = Path(MODELS_DIR) / "whisper" / normalized
    try:
        verifier = verify_whisper_turbo_model if normalized == "large-v3-turbo" else verify_whisper_model
        return str(verifier(candidate)), True
    except ModelIntegrityError as exc:
        raise RuntimeError(
            "Whisper is missing or corrupted. Return to the model setup screen and retry the download."
        ) from exc


def _load_whisper_model(device: str, compute_type: str, threads: int, model_name: str = "small"):
    vad_model_path = _verify_whisperx_vad_asset()
    source, local_only = _whisper_model_source(model_name)
    return whisperx.load_model(
        source,
        device,
        compute_type=compute_type,
        threads=threads,
        download_root=os.path.join(HF_HOME, "hub"),
        local_files_only=local_only,
        vad_options={"model_fp": str(vad_model_path)},
    )


def _load_long_audio_memmap(audio_path: str, video_id: str, progress_callback=None):
    """Decode long recordings to a disk-backed float array instead of a RAM buffer."""
    os.makedirs(TMP_DIR, exist_ok=True)
    handle, mapped_path = tempfile.mkstemp(prefix="linsub-asr-", suffix=".f32", dir=TMP_DIR)
    os.close(handle)
    try:
        check_cancellation(video_id)
        if progress_callback:
            _report_progress(
                progress_callback,
                "loading_audio",
                "Đang chuẩn bị âm thanh dài theo cách tiết kiệm RAM",
            )
        command = [
            _binary("ffmpeg"),
            "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(audio_path), "-map", "0:a:0", "-vn",
            "-ac", "1", "-ar", str(_AUDIO_SAMPLE_RATE),
            "-c:a", "pcm_f32le", "-f", "f32le", mapped_path,
        ]
        process = _whisper_audio.subprocess.Popen(
            command,
            stdout=_whisper_audio.subprocess.DEVNULL,
            stderr=_whisper_audio.subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(_whisper_audio.subprocess, "CREATE_NO_WINDOW", 0),
        )
        _stdout, stderr = communicate_process(
            video_id,
            process,
            label="Long audio preparation",
            timeout_seconds=MEDIA_PROCESS_TIMEOUT_SECONDS,
        )
        check_cancellation(video_id)
        size = os.path.getsize(mapped_path)
        if process.returncode != 0 or size == 0 or size % np.dtype(np.float32).itemsize:
            detail = " ".join((stderr or "").split())[-700:]
            raise RuntimeError(
                "Không thể chuẩn bị âm thanh dài để nhận dạng."
                + (f" FFmpeg: {detail}" if detail else "")
            )
        return np.memmap(mapped_path, dtype=np.float32, mode="r+", shape=(size // 4,)), mapped_path
    except Exception:
        try:
            os.remove(mapped_path)
        except OSError:
            pass
        raise


def _load_recognition_audio(audio_path: str, video_id: str, progress_callback=None):
    """Keep short-clip startup fast; memory-map recordings long enough to pressure RAM."""
    try:
        duration = get_video_duration(str(audio_path))
    except Exception:
        duration = 0.0
    if duration >= 15 * 60:
        return _load_long_audio_memmap(audio_path, video_id, progress_callback)
    return whisperx.load_audio(audio_path), None


def _constrain_four_gb_vad(asr_model, profile, device: str) -> None:
    """Leave CUDA for Whisper Small; Pyannote VAD can run on the CPU."""
    total_vram_bytes = getattr(profile, "total_vram_bytes", None)
    if device != "cuda" or total_vram_bytes is None or not 0 < total_vram_bytes < 5 * 1024**3:
        return
    if getattr(asr_model, "_linsub_vad_cpu", False) is True:
        return
    vad_pipeline = getattr(getattr(asr_model, "vad_model", None), "vad_pipeline", None)
    if vad_pipeline is not None:
        vad_pipeline.to(torch.device("cpu"))
        asr_model._linsub_vad_cpu = True
        gc.collect()
        torch.cuda.empty_cache()


def warm_whisperx_model(model_name: str = "small", *, device_preference: str | None = None):
    """Load the ASR model once in the background so the first video starts promptly."""
    global _WARM_ASR_MODEL, _WARM_DEVICE, _WARM_MODEL_NAME
    model_name = str(model_name or "small").strip().lower()
    if model_name not in {"small", "large-v3-turbo"}:
        raise RuntimeError(f"Unsupported Whisper model: {model_name}")
    with _MODEL_LOCK:
        from linsub.core.hardware import cpu_runtime_profile

        profile = cpu_runtime_profile(runtime_profile(), force_cpu=device_preference == "cpu")
        device = "cuda" if profile.cuda_available and device_preference != "cpu" else "cpu"
        if _WARM_ASR_MODEL is not None and _WARM_MODEL_NAME == model_name and _WARM_DEVICE == device:
            return True
        if _WARM_ASR_MODEL is not None:
            del _WARM_ASR_MODEL
            _WARM_ASR_MODEL = None
        compute_type = whisper_compute_type(device)
        try:
            model = _load_whisper_model(device, compute_type, profile.cpu_threads, model_name)
            _constrain_four_gb_vad(model, profile, device)
        except Exception:
            _WARM_ASR_MODEL = None
            _WARM_DEVICE = None
            _WARM_MODEL_NAME = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            raise
        _WARM_ASR_MODEL = model
        _WARM_DEVICE = device
        _WARM_MODEL_NAME = model_name
        return True


def release_warm_whisperx_model():
    global _WARM_ASR_MODEL, _WARM_DEVICE, _WARM_MODEL_NAME
    with _MODEL_LOCK:
        if _WARM_ASR_MODEL is not None:
            del _WARM_ASR_MODEL
        _WARM_ASR_MODEL = None
        _WARM_DEVICE = None
        _WARM_MODEL_NAME = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _release_cuda(video_id: str, stage: str) -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        log_to_video(video_id, f"Released WhisperX VRAM after {stage}.")


def _transcribe_with_vad_recovery(asr_model, audio, profile, device: str, video_id: str):
    """Keep Pyannote's separate VAD batch within the available memory.

    WhisperX's ``batch_size`` only controls ASR; its Pyannote VAD otherwise
    retains an independent default of 32. That default can exhaust an 8 GiB
    GPU before transcription even starts. Pyannote exposes the inference batch
    on the loaded pipeline, so reduce it without altering ASR model quality.
    """
    vad_pipeline = getattr(getattr(asr_model, "vad_model", None), "vad_pipeline", None)
    vad_inference = getattr(vad_pipeline, "_segmentation", None)
    if vad_inference is None:
        return asr_model.transcribe(audio, batch_size=profile.whisper_batch_size, language=None)

    vram_bytes = int(getattr(profile, "total_vram_bytes", 0) or 0)
    low_vram = device == "cuda" and (not vram_bytes or vram_bytes <= 8 * 1024**3)
    initial_batch = min(int(profile.whisper_batch_size), 4 if low_vram else 8)
    vad_inference.batch_size = max(1, min(int(vad_inference.batch_size), initial_batch))
    log_to_video(video_id, f"WhisperX VAD batch size: {vad_inference.batch_size}.")
    vad_on_cpu = device != "cuda" or getattr(asr_model, "_linsub_vad_cpu", False) is True

    while True:
        check_cancellation(video_id)
        try:
            return asr_model.transcribe(audio, batch_size=profile.whisper_batch_size, language=None)
        except MemoryError as exc:
            # Pyannote wraps its CUDA OOM in a batch-size MemoryError. Other
            # memory failures must not be masked as recoverable VAD errors.
            if "batch_size" not in str(exc):
                raise
            current_batch = int(vad_inference.batch_size)
            if current_batch > 1:
                vad_inference.batch_size = max(1, current_batch // 2)
                log_to_video(
                    video_id,
                    f"WhisperX VAD ran out of memory; retrying batch size {vad_inference.batch_size}.",
                )
            elif not vad_on_cpu and vad_pipeline is not None:
                log_to_video(video_id, "WhisperX VAD still lacks GPU memory; moving VAD to CPU.")
                vad_pipeline.to(torch.device("cpu"))
                vad_on_cpu = True
                vad_inference.batch_size = max(1, min(int(profile.whisper_batch_size), 4))
            else:
                raise RuntimeError(
                    "Không đủ bộ nhớ để nhận dạng lời thoại, kể cả khi VAD chạy với batch 1. "
                    "Hãy đóng ứng dụng đang dùng nhiều bộ nhớ rồi thử lại."
                ) from exc
        # Let the failed inference traceback release its tensors before
        # clearing the allocator and starting the next attempt.
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _long_form_cpu_profile(profile, audio_path: str, device: str, video_id: str):
    """Use more physical CPU cores for long ASR only when RAM headroom is ample."""
    if (device != "cpu" or getattr(profile, "key", "") != "cpu_low_memory"
            or int(getattr(profile, "logical_cpu_count", 0) or 0) < 16):
        return profile
    try:
        with wave.open(str(audio_path), "rb") as source:
            duration = source.getnframes() / max(1, source.getframerate())
    except (OSError, wave.Error, EOFError):
        return profile
    if duration < 3600.0:
        return profile

    from linsub.core.memory import GIB, memory_snapshot

    snapshot = memory_snapshot()
    if (snapshot.available_bytes is None or snapshot.available_bytes < 8 * GIB
            or snapshot.commit_available_bytes is None or snapshot.commit_available_bytes < 8 * GIB):
        return profile
    threads = min(6, max(profile.cpu_threads, profile.logical_cpu_count // 2))
    batch_size = max(profile.whisper_batch_size, 4)
    if threads <= profile.cpu_threads and batch_size <= profile.whisper_batch_size:
        return profile
    log_to_video(
        video_id,
        f"Long-form CPU recognition: using {threads} Whisper threads and batch size {batch_size} "
        "after confirming memory headroom.",
        component="WHISPERX",
    )
    return replace(profile, cpu_threads=threads, whisper_batch_size=batch_size)


def _format_time(seconds: float) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    s = int(max(0, seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _report_progress(callback, stage: str, detail: str, current: int = 0, total: int = 0) -> None:
    """Invoke progress callback with variable argument compatibility."""
    if not callback:
        return
    try:
        callback(stage, detail, current, total)
    except TypeError:
        try:
            callback(stage, detail)
        except TypeError:
            pass


def _find_optimal_audio_split(
    audio: np.ndarray,
    target_sample: int,
    search_half_window_sec: float = 15.0,
    min_slice_sec: float = 0.5,
) -> int:
    """Find the point of minimum acoustic energy (silence/pause) near target_sample.

    This ensures cuts are placed between spoken phrases/sentences, never in the
    middle of words.
    """
    sr = _AUDIO_SAMPLE_RATE
    half_samples = int(search_half_window_sec * sr)
    start_sample = max(0, target_sample - half_samples)
    end_sample = min(len(audio), target_sample + half_samples)
    window = audio[start_sample:end_sample]
    slice_samples = int(min_slice_sec * sr)
    if len(window) < slice_samples * 2:
        return target_sample

    step_samples = int(0.05 * sr)  # 50ms step
    min_energy = float("inf")
    best_offset = len(window) // 2

    for i in range(0, len(window) - slice_samples, step_samples):
        segment = window[i:i + slice_samples]
        energy = float(np.mean(segment * segment))
        if energy < min_energy:
            min_energy = energy
            best_offset = i + slice_samples // 2
            if energy < 1e-6:
                break

    return start_sample + best_offset


def _calculate_audio_chunks(
    audio: np.ndarray,
    target_chunk_sec: float = 600.0,
) -> list[tuple[int, int]]:
    """Split audio into chunks between silence points near target boundaries.

    Returns a list of (start_sample, end_sample) tuples covering the full audio
    without any gaps or overlapping audio.
    """
    sr = _AUDIO_SAMPLE_RATE
    total_samples = len(audio)
    target_samples = int(target_chunk_sec * sr)
    if total_samples <= int(target_chunk_sec * 1.2 * sr):
        return [(0, total_samples)]

    splits = [0]
    current = target_samples
    while current < total_samples - int(target_chunk_sec * 0.4 * sr):
        optimal = _find_optimal_audio_split(audio, current)
        if optimal > splits[-1] + int(target_chunk_sec * 0.5 * sr):
            splits.append(optimal)
            current = optimal + target_samples
        else:
            splits.append(current)
            current += target_samples

    if splits[-1] < total_samples:
        splits.append(total_samples)

    chunks = [(splits[i], splits[i + 1]) for i in range(len(splits) - 1)]
    return chunks


def _transcribe_chunks(
    asr_model,
    audio: np.ndarray,
    profile,
    device: str,
    video_id: str,
    *,
    checkpoint_path: str | None = None,
    checkpoint_key: str = "",
    progress_callback=None,
) -> dict:
    """Transcribe audio in progressive chunks with intermediate checkpointing."""
    from linsub.utils.atomic_file import atomic_json

    sr = _AUDIO_SAMPLE_RATE
    total_samples = len(audio)
    total_duration = total_samples / sr
    # Long-form videos need shorter ASR/VAD jobs so the UI can report progress
    # regularly and a pause/crash loses at most a few minutes of work.
    if profile.key == "cpu_minimum":
        target_chunk_sec = 120.0
    elif profile.key in {"cpu_low_memory", "cuda_low_memory"}:
        target_chunk_sec = 180.0
    else:
        target_chunk_sec = 300.0 if total_duration >= 3600.0 else 600.0
    chunks = _calculate_audio_chunks(audio, target_chunk_sec=target_chunk_sec)
    n_chunks = len(chunks)

    if n_chunks == 1:
        _report_progress(progress_callback, "transcribing", "Transcribing speech", 0, int(total_duration))
        return _transcribe_with_vad_recovery(asr_model, audio, profile, device, video_id)

    chunk_state_path = (
        Path(checkpoint_path).with_name("recognition-chunks.json")
        if checkpoint_path
        else None
    )

    completed_chunks = []
    detected_languages = []
    if chunk_state_path and chunk_state_path.is_file():
        try:
            stored = json.loads(chunk_state_path.read_text(encoding="utf-8"))
            if stored.get("key") == checkpoint_key and isinstance(stored.get("chunks"), list):
                # Chunk sizing can change for long videos. Reuse only checkpoints
                # whose time range still matches the current split plan.
                for item in stored["chunks"]:
                    try:
                        index = int(item["index"])
                        if index < 0:
                            continue
                        expected_start, expected_end = chunks[index]
                        start_matches = abs(float(item["start_time"]) - expected_start / sr) <= 1 / sr
                        end_matches = abs(float(item["end_time"]) - expected_end / sr) <= 1 / sr
                        if start_matches and end_matches:
                            completed_chunks.append(item)
                    except (IndexError, KeyError, TypeError, ValueError):
                        continue
                detected_languages = [
                    str(item.get("language")) for item in completed_chunks if item.get("language")
                ]
                log_to_video(
                    video_id,
                    f"Resuming chunked transcription: {len(completed_chunks)}/{n_chunks} chunks already completed.",
                    component="WHISPERX",
                )
        except Exception as exc:
            log_to_video(video_id, f"Could not read chunk checkpoint: {exc}; restarting transcription.", level="WARNING")
            completed_chunks = []
            detected_languages = []

    completed_indices = {c["index"] for c in completed_chunks}

    for idx, (start_sample, end_sample) in enumerate(chunks):
        check_cancellation(video_id)
        if idx in completed_indices:
            continue

        chunk_start_sec = start_sample / sr
        chunk_end_sec = end_sample / sr
        chunk_audio = audio[start_sample:end_sample]

        percent = int(round((chunk_start_sec / total_duration) * 100)) if total_duration > 0 else 0
        detail = (
            f"Nhận dạng giọng nói: {_format_time(chunk_start_sec)} / {_format_time(total_duration)} "
            f"({percent}%) [đoạn {idx + 1}/{n_chunks}]"
        )

        log_to_video(
            video_id,
            f"Transcribing audio chunk {idx + 1}/{n_chunks} "
            f"({_format_time(chunk_start_sec)} - {_format_time(chunk_end_sec)} / {_format_time(total_duration)})...",
            component="WHISPERX",
        )
        if progress_callback:
            _report_progress(
                progress_callback,
                "transcribing",
                detail,
                current=int(chunk_start_sec),
                total=int(total_duration),
            )

        chunk_result = _transcribe_with_vad_recovery(asr_model, chunk_audio, profile, device, video_id)
        chunk_lang = chunk_result.get("language")
        if chunk_lang:
            detected_languages.append(chunk_lang)

        chunk_segments = []
        for raw_seg in chunk_result.get("segments", []):
            text = str(raw_seg.get("text") or "").strip()
            if not text:
                continue
            seg_start = round(float(raw_seg["start"]) + chunk_start_sec, 3)
            seg_end = round(float(raw_seg["end"]) + chunk_start_sec, 3)
            chunk_segments.append({
                **raw_seg,
                "start": seg_start,
                "end": seg_end,
                "language": chunk_lang or "en",
                "language_confidence": 1.0,
            })

        completed_chunks.append({
            "index": idx,
            "start_time": chunk_start_sec,
            "end_time": chunk_end_sec,
            "segments": chunk_segments,
            "language": chunk_lang,
        })
        completed_chunks.sort(key=lambda c: c["index"])

        if chunk_state_path:
            try:
                atomic_json(
                    chunk_state_path,
                    {
                        "key": checkpoint_key,
                        "chunks": completed_chunks,
                        "detected_languages": detected_languages,
                    },
                    ensure_ascii=False,
                )
            except Exception as exc:
                log_to_video(video_id, f"Failed to persist chunk state: {exc}", level="WARNING")

        log_to_video(
            video_id,
            f"Finished chunk {idx + 1}/{n_chunks}: {len(chunk_segments)} speech segments.",
            component="WHISPERX",
        )
        completed_seconds = end_sample / sr
        completed_percent = int(round((completed_seconds / total_duration) * 100)) if total_duration > 0 else 0
        _report_progress(
            progress_callback,
            "transcribing",
            f"Đã nhận dạng đến {_format_time(completed_seconds)} / {_format_time(total_duration)} "
            f"({completed_percent}% âm thanh) · xong đoạn {idx + 1}/{n_chunks}",
            current=int(completed_seconds),
            total=int(total_duration),
        )
        gc.collect()

    all_segments = []
    for c in completed_chunks:
        all_segments.extend(c["segments"])
    all_segments.sort(key=lambda s: float(s["start"]))

    if detected_languages:
        from collections import Counter
        primary_language = Counter(detected_languages).most_common(1)[0][0]
    else:
        primary_language = "en"

    if chunk_state_path and chunk_state_path.is_file():
        try:
            chunk_state_path.unlink(missing_ok=True)
        except OSError:
            pass

    return {
        "segments": all_segments,
        "language": primary_language,
    }


def _value(item, name: str, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _merge_transcript_text(left: str, right: str) -> str:
    """Join adjacent model fragments without inserting spaces into CJK text."""
    left = (left or "").strip()
    raw_right = right or ""
    right = raw_right.strip()
    if not left:
        return right
    if not right:
        return left
    if raw_right[:1].isspace():
        return f"{left} {right}"
    if _CJK_RE.search(left[-1:]) and _CJK_RE.match(right[:1]):
        return left + right
    if right[:1] in ",.;:!?%)]}\u3001\u3002\uff0c\uff01\uff1f":
        return left + right
    return f"{left} {right}"


def _detect_segment_languages(
    asr_model, audio, segments, fallback_language: str, video_id: str, progress_callback=None,
):
    """Detect sentence languages, batching long recordings into 30-second windows."""
    fallback_language = fallback_language or "en"
    audio_duration = len(audio) / _AUDIO_SAMPLE_RATE
    detected_segments = [None] * len(segments)
    counts = {}

    if audio_duration >= 3600.0:
        # CTranslate2 language detection runs a Whisper encoder pass. Running it
        # once per sentence repeats padded work thousands of times on a feature
        # film. One bounded window keeps mixed-language changes reasonably
        # local while reducing those encoder passes to at most one per 30 sec.
        window_seconds = 30.0
        windows = {}
        for index, segment in enumerate(segments):
            start = max(0.0, float(segment.get("start", 0.0)))
            end = min(audio_duration, float(segment.get("end", start)))
            midpoint = max(0.0, (start + end) / 2.0)
            windows.setdefault(int(midpoint // window_seconds), []).append((index, segment))

        total_windows = len(windows)
        for window_index, (bucket, items) in enumerate(sorted(windows.items()), start=1):
            clip_start = bucket * window_seconds
            clip_end = min(audio_duration, clip_start + window_seconds)
            clip = audio[int(clip_start * _AUDIO_SAMPLE_RATE):int(clip_end * _AUDIO_SAMPLE_RATE)]
            fallback_counts = {}
            for _index, segment in items:
                language = str(segment.get("language") or fallback_language)
                fallback_counts[language] = fallback_counts.get(language, 0) + 1
            window_fallback = max(fallback_counts, key=fallback_counts.get) if fallback_counts else fallback_language
            language, confidence = window_fallback, 0.0
            try:
                detected, detected_confidence, _all_probabilities = asr_model.model.detect_language(
                    audio=clip,
                    language_detection_threshold=0.0,
                )
                confidence = float(detected_confidence or 0.0)
                if detected and confidence >= _SEGMENT_LANGUAGE_CONFIDENCE:
                    language = detected
                else:
                    language = window_fallback
            except Exception as exc:
                log_to_video(
                    video_id,
                    f"Language detection for {_format_time(clip_start)} failed; using '{window_fallback}': {exc}",
                )

            for index, segment in items:
                segment_with_language = dict(segment)
                segment_with_language["language"] = language
                segment_with_language["language_confidence"] = round(confidence, 3)
                detected_segments[index] = segment_with_language
                counts[language] = counts.get(language, 0) + 1
            if progress_callback and (window_index % 10 == 0 or window_index == total_windows):
                _report_progress(
                    progress_callback,
                    "detecting_languages",
                    f"Nhận diện ngôn ngữ: {_format_time(clip_end)} / {_format_time(audio_duration)}",
                    current=int(clip_end),
                    total=int(audio_duration),
                )
    else:
        for index, segment in enumerate(segments, start=1):
            start = max(0.0, float(segment.get("start", 0.0)))
            end = min(audio_duration, float(segment.get("end", start)))
            language = fallback_language
            confidence = 0.0

            if end > start:
                clip = audio[int(start * _AUDIO_SAMPLE_RATE) : int(end * _AUDIO_SAMPLE_RATE)]
                try:
                    detected, confidence, _all_probabilities = asr_model.model.detect_language(
                        audio=clip,
                        language_detection_threshold=0.0,
                    )
                    if detected and confidence >= _SEGMENT_LANGUAGE_CONFIDENCE:
                        language = detected
                    else:
                        log_to_video(
                            video_id,
                            f"Sentence {index} language confidence {confidence:.2f} is low; using '{fallback_language}'.",
                        )
                except Exception as exc:
                    log_to_video(
                        video_id, f"Sentence {index} language detection failed; using '{fallback_language}': {exc}"
                    )

            segment_with_language = dict(segment)
            segment_with_language["language"] = language
            segment_with_language["language_confidence"] = round(float(confidence), 3)
            detected_segments[index - 1] = segment_with_language
            counts[language] = counts.get(language, 0) + 1
            if progress_callback and (index % 50 == 0 or index == len(segments)):
                current = int(end)
                _report_progress(
                    progress_callback,
                    "detecting_languages",
                    f"Nhận diện ngôn ngữ: {_format_time(current)} / {_format_time(audio_duration)}",
                    current=current,
                    total=int(audio_duration),
                )

    if counts:
        summary = ", ".join(f"{language}={count}" for language, count in sorted(counts.items()))
        log_to_video(video_id, f"Detected languages per sentence: {summary}.")
    return [segment for segment in detected_segments if segment is not None]


def _retranscription_language(segment: dict, primary_language: str) -> tuple[str, str | None]:
    """Choose a forced language without turning a detector outlier into new text.

    Whisper's full-pass transcript is produced with the primary language detected
    for the video.  A short Vietnamese sentence can occasionally receive a CJK
    language label during the later, per-sentence detector pass.  Re-running that
    Latin-script sentence with ``zh`` replaces otherwise usable Vietnamese text
    with a hallucinated Chinese phrase.  Prefer the primary language for that
    conflicting case, while retaining real CJK speech whose original transcript
    already uses a CJK script.
    """
    detected_language = str(segment.get("language") or primary_language).lower()
    normalized_primary = (primary_language or "en").lower()
    text_has_cjk = bool(_CJK_RE.search(str(segment.get("text") or "")))
    detected_is_cjk = detected_language in _CJK_LANGUAGE_CODES
    primary_is_cjk = normalized_primary in _CJK_LANGUAGE_CODES

    if detected_is_cjk and not primary_is_cjk and not text_has_cjk:
        return normalized_primary, (
            f"detector reported '{detected_language}' for a non-CJK transcript; "
            f"using primary language '{normalized_primary}'"
        )
    return detected_language, None


def _retranscribe_mixed_language_segments(asr_model, audio, segments, primary_language: str, video_id: str):
    """Correct switched-language text while preserving every original timestamp."""
    primary_language = (primary_language or "en").lower()
    corrected_segments = []

    for index, segment in enumerate(segments, start=1):
        language, correction_reason = _retranscription_language(segment, primary_language)
        confidence = float(segment.get("language_confidence", 0.0))
        start = max(0.0, float(segment.get("start", 0.0)))
        end = min(len(audio) / _AUDIO_SAMPLE_RATE, float(segment.get("end", start)))
        corrected_segment = dict(segment)
        if language == primary_language or confidence < _SEGMENT_LANGUAGE_CONFIDENCE or end <= start:
            if correction_reason:
                corrected_segment["language"] = language
                log_to_video(video_id, f"Sentence {index}: {correction_reason}.")
            corrected_segments.append(corrected_segment)
            continue

        try:
            if correction_reason:
                log_to_video(video_id, f"Sentence {index}: {correction_reason}.")
            log_to_video(video_id, f"Re-transcribing sentence {index} with language '{language}'.")
            clip = audio[int(start * _AUDIO_SAMPLE_RATE) : int(end * _AUDIO_SAMPLE_RATE)]
            local_result = asr_model.transcribe(clip, batch_size=1, language=language)
            corrected_text = ""
            for local_segment in local_result.get("segments", []):
                corrected_text = _merge_transcript_text(
                    corrected_text,
                    str(_value(local_segment, "text", "") or ""),
                )
            if corrected_text.strip():
                corrected_segment["text"] = corrected_text.strip()
            corrected_segment["language"] = language
        except Exception as exc:
            log_to_video(video_id, f"Could not re-transcribe sentence {index} in '{language}'; keeping its text: {exc}")
        corrected_segments.append(corrected_segment)

    return corrected_segments


def _language_for_aligned_segment(segment, source_segments, fallback_language: str) -> tuple[str, float]:
    """Carry language metadata to the aligned sentence with the greatest overlap."""
    start = float(segment.get("start", 0.0))
    end = float(segment.get("end", start))
    midpoint = (start + end) / 2
    best_source = None
    best_overlap = -1.0

    for source in source_segments:
        source_start = float(source.get("start", 0.0))
        source_end = float(source.get("end", source_start))
        overlap = max(0.0, min(end, source_end) - max(start, source_start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_source = source
        elif overlap == best_overlap and best_source is not None:
            source_midpoint = (source_start + source_end) / 2
            best_midpoint = (float(best_source.get("start", 0.0)) + float(best_source.get("end", 0.0))) / 2
            if abs(midpoint - source_midpoint) < abs(midpoint - best_midpoint):
                best_source = source

    if not best_source:
        return fallback_language, 0.0
    return (
        best_source.get("language") or fallback_language,
        float(best_source.get("language_confidence", 0.0)),
    )


def _split_sentence_text(text: str) -> list[str]:
    """Split complete sentences without assuming that the language uses spaces."""
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return []

    sentences = []
    sentence_start = 0
    index = 0
    while index < len(normalized):
        character = normalized[index]
        if character not in _SENTENCE_END_CHARS:
            index += 1
            continue
        if (
            character == "."
            and index > 0
            and index + 1 < len(normalized)
            and normalized[index - 1].isdigit()
            and normalized[index + 1].isdigit()
        ):
            index += 1
            continue

        sentence_end = index + 1
        while sentence_end < len(normalized) and (
            normalized[sentence_end] in _SENTENCE_END_CHARS or normalized[sentence_end] in _SENTENCE_CLOSERS
        ):
            sentence_end += 1

        has_boundary = sentence_end >= len(normalized)
        if not has_boundary:
            has_boundary = normalized[sentence_end].isspace() or character in "\u2026\u3002\uff01\uff1f"
        if has_boundary:
            sentence = normalized[sentence_start:sentence_end].strip()
            if sentence:
                sentences.append(sentence)
            while sentence_end < len(normalized) and normalized[sentence_end].isspace():
                sentence_end += 1
            sentence_start = sentence_end
            index = sentence_end
            continue
        index += 1

    remainder = normalized[sentence_start:].strip()
    if remainder:
        sentences.append(remainder)
    return sentences


def _speech_weight(text: str) -> int:
    """Estimate spoken length consistently for spaced and unspaced languages."""
    return max(1, sum(character.isalnum() for character in str(text or "")))


def _coalesce_short_sentence_segments(segments: list[dict]) -> list[dict]:
    """Join unusably short contiguous speech fragments to a neighbour."""
    coalesced = []
    for source in segments:
        current = dict(source)
        duration = float(current.get("end", 0.0)) - float(current.get("start", 0.0))
        if duration < _MIN_SENTENCE_SPAN_SECONDS and coalesced:
            previous = coalesced[-1]
            gap = float(current.get("start", 0.0)) - float(previous.get("end", 0.0))
            if gap <= 0.15:
                previous["end"] = max(float(previous.get("end", 0.0)), float(current.get("end", 0.0)))
                previous["text"] = (
                    f"{str(previous.get('text') or '').rstrip()} {str(current.get('text') or '').lstrip()}"
                ).strip()
                previous.pop("words", None)
                continue
        coalesced.append(current)

    if len(coalesced) > 1:
        first = coalesced[0]
        first_duration = float(first.get("end", 0.0)) - float(first.get("start", 0.0))
        forward_gap = float(coalesced[1].get("start", 0.0)) - float(first.get("end", 0.0))
        if first_duration < _MIN_SENTENCE_SPAN_SECONDS and forward_gap <= 0.15:
            first = coalesced.pop(0)
            coalesced[0]["start"] = min(float(first.get("start", 0.0)), float(coalesced[0].get("start", 0.0)))
            coalesced[0]["text"] = (
                f"{str(first.get('text') or '').rstrip()} {str(coalesced[0].get('text') or '').lstrip()}"
            ).strip()
            coalesced[0].pop("words", None)
    return coalesced


def _split_segment_proportionally(segment: dict) -> list[dict]:
    """Keep Whisper's semantic span and split only explicit sentence boundaries.

    In particular, never cut unpunctuated CJK at an estimated character
    position.  Without a verified word aligner such a cut can land inside a
    phrase and remove the context HY-MT2 needs to disambiguate technical terms.
    Long TTS network requests are bounded later by the TTS layer.
    """
    sentences = _split_sentence_text(segment.get("text", ""))
    if len(sentences) <= 1:
        return [dict(segment)]

    start = float(segment.get("start", 0.0))
    end = float(segment.get("end", start))
    duration = max(0.0, end - start)
    weights = [_speech_weight(sentence) for sentence in sentences]
    total_weight = sum(weights)
    elapsed_weight = 0
    fallback_segments = []
    for index, (sentence, weight) in enumerate(zip(sentences, weights)):
        sentence_start = start + duration * elapsed_weight / total_weight
        elapsed_weight += weight
        sentence_end = end if index == len(sentences) - 1 else start + duration * elapsed_weight / total_weight
        fallback_segment = dict(segment)
        fallback_segment.update(
            {
                "start": round(sentence_start, 3),
                "end": round(max(sentence_start + 0.001, sentence_end), 3),
                "text": sentence,
            }
        )
        fallback_segment.pop("words", None)
        fallback_segments.append(fallback_segment)
    # Punctuation-only sentence splitting can otherwise create impossible
    # 100ms voice slots from a perfectly valid multi-second Whisper span.
    return _coalesce_short_sentence_segments(fallback_segments)


def _speech_windows_in_segment(
    speech_regions: list[tuple[float, float]], start: float, end: float, *, region_starts=None,
) -> list[tuple[float, float]]:
    """Keep real pauses between short utterances in a long ASR segment."""
    if region_starts is None:
        relevant_regions = speech_regions
    else:
        first = max(0, bisect.bisect_left(region_starts, start) - 1)
        stop = bisect.bisect_left(region_starts, end)
        relevant_regions = speech_regions[first:stop]
    windows: list[list[float]] = []
    for region_start, region_end in relevant_regions:
        left = max(start, region_start)
        right = min(end, region_end)
        if right - left < 0.25:
            continue
        if windows and left - windows[-1][1] <= 0.35 and right - windows[-1][0] <= 8.0:
            windows[-1][1] = right
        else:
            windows.append([left, right])
    return [(round(left, 3), round(right, 3)) for left, right in windows]


def _speech_regions_in_audio_windows(asr_model, audio, video_id: str, progress_callback=None):
    """Run Pyannote VAD on bounded windows so long recordings never become one tensor."""
    from whisperx.vads.pyannote import Binarize

    vad = asr_model.vad_model
    duration = len(audio) / _AUDIO_SAMPLE_RATE
    window_seconds = 180.0
    regions: list[tuple[float, float]] = []
    for start in range(0, int(np.ceil(duration)), int(window_seconds)):
        check_cancellation(video_id)
        end = min(duration, start + window_seconds)
        first_sample = int(start * _AUDIO_SAMPLE_RATE)
        last_sample = int(end * _AUDIO_SAMPLE_RATE)
        waveform = vad.preprocess_audio(audio[first_sample:last_sample])
        scores = vad({"waveform": waveform, "sample_rate": _AUDIO_SAMPLE_RATE})
        speech = Binarize(
            max_duration=8.0,
            onset=asr_model._vad_params["vad_onset"],
            offset=asr_model._vad_params["vad_offset"],
        )(scores)
        for region in speech.get_timeline():
            left = max(float(start), float(start) + float(region.start))
            right = min(float(end), float(start) + float(region.end))
            if right <= left:
                continue
            if regions and left - regions[-1][1] <= 0.35:
                regions[-1] = (regions[-1][0], right)
            else:
                regions.append((left, right))
        del speech, scores, waveform
        if progress_callback:
            _report_progress(
                progress_callback,
                "splitting",
                f"Quét vùng có lời: {_format_time(end)} / {_format_time(duration)}",
                current=int(end),
                total=int(duration),
            )
    return regions


def _split_long_unaligned_speech(
    asr_model,
    audio,
    segments,
    batch_size,
    video_id,
    language,
    *,
    progress_callback=None,
    checkpoint_path: str | None = None,
    checkpoint_key: str = "",
):
    """Split long spans without a verified word aligner at real speech pauses.

    WhisperX merges VAD regions into up-to-30-second transcription chunks.
    Chinese and Vietnamese have no verified word aligner here, so a long
    unpunctuated chunk would otherwise include every internal pause in one
    translation, subtitle and TTS slot.
    """
    try:
        if len(audio) / _AUDIO_SAMPLE_RATE >= 15 * 60:
            regions = _speech_regions_in_audio_windows(
                asr_model, audio, video_id, progress_callback=progress_callback,
            )
        else:
            from whisperx.vads.pyannote import Binarize

            vad = asr_model.vad_model
            scores = vad({"waveform": vad.preprocess_audio(audio), "sample_rate": _AUDIO_SAMPLE_RATE})
            speech = Binarize(
                max_duration=8.0,
                onset=asr_model._vad_params["vad_onset"],
                offset=asr_model._vad_params["vad_offset"],
            )(scores)
            regions = [(float(region.start), float(region.end)) for region in speech.get_timeline()]
        region_starts = [start for start, _end in regions]
    except Exception as exc:
        if is_cancelled(video_id):
            raise
        log_to_video(video_id, f"WARNING: Could not resolve {language} speech pauses: {exc}")
        return segments

    from linsub.utils.atomic_file import atomic_json

    split_state_path = (
        Path(checkpoint_path).with_name("recognition-splits.json")
        if checkpoint_path
        else None
    )

    completed_results: list[dict] = []
    start_index = 0
    if split_state_path and split_state_path.is_file():
        try:
            stored = json.loads(split_state_path.read_text(encoding="utf-8"))
            if (
                stored.get("key") == checkpoint_key
                and isinstance(stored.get("result"), list)
                and isinstance(stored.get("next_index"), int)
            ):
                completed_results = stored["result"]
                start_index = stored["next_index"]
                log_to_video(
                    video_id,
                    f"Resuming sentence splitting checkpoint: {start_index}/{len(segments)} segments already processed.",
                    component="WHISPERX",
                )
        except Exception as exc:
            log_to_video(video_id, f"Could not read split checkpoint: {exc}; restarting split phase.", level="WARNING")
            completed_results = []
            start_index = 0

    result = list(completed_results)
    total_segments = len(segments)

    for idx in range(start_index, total_segments):
        check_cancellation(video_id)
        segment = segments[idx]

        if progress_callback:
            percent = int(round(((idx + 1) / total_segments) * 100)) if total_segments > 0 else 0
            _report_progress(
                progress_callback,
                "splitting",
                f"Đồng bộ tách câu {language}: {idx + 1}/{total_segments} ({percent}%)",
                current=idx + 1,
                total=total_segments,
            )

        if str(segment.get("language")) != language:
            result.append(segment)
            continue
        start = float(segment["start"])
        end = float(segment["end"])
        if end - start <= 8.0:
            result.append(segment)
            continue
        windows = _speech_windows_in_segment(regions, start, end, region_starts=region_starts)
        if len(windows) < 2:
            result.append(segment)
            continue
        utterances = []
        for left, right in windows:
            clip = audio[int(left * _AUDIO_SAMPLE_RATE):int(right * _AUDIO_SAMPLE_RATE)]
            if len(clip) < int(0.25 * _AUDIO_SAMPLE_RATE):
                continue
            try:
                recognized = asr_model.transcribe(clip, batch_size=batch_size, language=language)
            except Exception as exc:
                log_to_video(video_id, f"WARNING: {language} utterance {left:.2f}-{right:.2f}s failed: {exc}")
                utterances = []
                break
            text = " ".join(
                str(item.get("text") or "").strip()
                for item in recognized.get("segments", [])
                if str(item.get("text") or "").strip()
            ).strip()
            if text:
                utterances.append({**segment, "start": left, "end": right, "text": text})
        if len(utterances) < 2:
            log_to_video(video_id, f"WARNING: {language} pause split was incomplete; preserving the original transcript.")
            result.append(segment)
        else:
            log_to_video(video_id, f"Split {language} {end - start:.1f}s speech span into {len(utterances)} utterances.")
            result.extend(utterances)

        if split_state_path:
            try:
                atomic_json(
                    split_state_path,
                    {
                        "key": checkpoint_key,
                        "next_index": idx + 1,
                        "result": result,
                    },
                    ensure_ascii=False,
                )
            except Exception as exc:
                log_to_video(video_id, f"Failed to persist split state: {exc}", level="WARNING")

    if split_state_path and split_state_path.is_file():
        try:
            split_state_path.unlink(missing_ok=True)
        except OSError:
            pass

    # VAD can identify a short, isolated utterance which the full-video ASR
    # omitted.  Recheck only uncovered speech windows, especially the tail of
    # short-form videos; a gap in the transcript otherwise becomes a gap in
    # translation, captions and narration alike.
    recovered = _recover_uncovered_speech(asr_model, audio, result, regions, batch_size, video_id, language)
    return sorted([*result, *recovered], key=lambda item: (float(item["start"]), float(item["end"])))


def _timeline_interval_index(segments):
    ordered = sorted(segments, key=lambda item: float(item.get("start", 0.0)))
    starts = []
    prefix_max_ends = []
    maximum_end = float("-inf")
    for item in ordered:
        starts.append(float(item.get("start", 0.0)))
        maximum_end = max(maximum_end, float(item.get("end", item.get("start", 0.0))))
        prefix_max_ends.append(maximum_end)
    return ordered, starts, prefix_max_ends


def _recover_uncovered_speech(asr_model, audio, segments, regions, batch_size, video_id, language):
    recovered = []
    ordered, starts, prefix_max_ends = _timeline_interval_index(segments)
    for left, right in sorted(regions):
        check_cancellation(video_id)
        if not 0.35 <= right - left <= 8.0:
            continue
        stop = bisect.bisect_left(starts, right)
        begin = bisect.bisect_right(prefix_max_ends, left, 0, stop)
        overlap = sum(
            max(0.0, min(right, float(item["end"])) - max(left, float(item["start"])))
            for item in ordered[begin:stop]
        )
        # VAD regions arrive in time order, so only the most recent recovered
        # spans can overlap this region. This avoids rescanning all prior work.
        for item in reversed(recovered):
            if float(item["end"]) <= left:
                break
            overlap += max(0.0, min(right, float(item["end"])) - max(left, float(item["start"])))
        if overlap > 0.1 or overlap >= (right - left) * 0.2:
            continue
        clip = audio[int(left * _AUDIO_SAMPLE_RATE):int(right * _AUDIO_SAMPLE_RATE)]
        if len(clip) < int(0.35 * _AUDIO_SAMPLE_RATE):
            continue
        try:
            recognized = asr_model.transcribe(clip, batch_size=min(batch_size, 2), language=language)
        except Exception as exc:
            log_to_video(video_id, f"WARNING: Uncovered speech {left:.2f}-{right:.2f}s could not be checked: {exc}")
            continue
        text = " ".join(str(item.get("text") or "").strip()
                        for item in recognized.get("segments", []) if str(item.get("text") or "").strip()).strip()
        if not text:
            continue
        log_to_video(video_id, f"Recovered omitted speech at {left:.2f}-{right:.2f}s: {text}")
        recovered.append({"start": left, "end": right, "text": text,
                          "language": language, "language_confidence": 1.0})
    return recovered


def _energetic_spans(audio, start: float, end: float) -> list[tuple[float, float]]:
    """Find sustained foreground sound in uncovered gaps of a vocal stem."""
    frame_samples = int(0.1 * _AUDIO_SAMPLE_RATE)
    left = int(start * _AUDIO_SAMPLE_RATE)
    right = int(end * _AUDIO_SAMPLE_RATE)
    levels = np.asarray([
        float(np.sqrt(np.mean(np.square(audio[offset:min(offset + frame_samples, right)]))))
        for offset in range(left, right, frame_samples)
        if min(offset + frame_samples, right) - offset >= frame_samples // 2
    ])
    if len(levels) == 0 or float(np.max(levels)) < 0.03:
        return []
    threshold = max(0.012, float(np.median(levels)) * 2.4, float(np.max(levels)) * 0.3)
    active = np.flatnonzero(levels >= threshold)
    if len(active) == 0:
        return []
    spans = []
    first = previous = int(active[0])
    for index in map(int, active[1:]):
        if index - previous > 5:
            if (previous - first + 1) * 0.1 >= 0.5:
                spans.append((round(start + first * 0.1, 3), round(start + (previous + 1) * 0.1, 3)))
            first = index
        previous = index
    if (previous - first + 1) * 0.1 >= 0.5:
        spans.append((round(start + first * 0.1, 3), round(start + (previous + 1) * 0.1, 3)))
    return spans


def _recover_speech_without_vad(asr_model, audio, segments, language: str, video_id: str):
    """Check the final uncovered speech, including gaps before a tiny last VAD hit."""
    stable = [item for item in segments if float(item["end"]) - float(item["start"]) >= 0.25]
    rejected = len(segments) - len(stable)
    if rejected:
        log_to_video(video_id, f"Ignored {rejected} sub-250ms ASR timestamp artifact(s); checking the audio directly.")
    raw_model = getattr(getattr(asr_model, "model", None), "transcribe", None)
    if not callable(raw_model):
        return stable

    duration = len(audio) / _AUDIO_SAMPLE_RATE
    recovered = []
    # The last VAD result may itself be a short word after a missed sentence.
    # Searching only after its end misses that sentence (observed on Chinese
    # speech at 155s followed by a 320ms result at 156s). Restrict the direct
    # check to the outro and to energetic spans mostly outside known speech.
    tail_start = max(0.0, duration - (12.0 if stable else 20.0))
    candidates = _energetic_spans(audio, tail_start, duration)
    for left, right in sorted(candidates, reverse=True)[:8]:
        check_cancellation(video_id)
        covered = sum(max(0.0, min(right, float(item["end"])) - max(left, float(item["start"])))
                      for item in stable)
        if covered >= (right - left) * 0.45:
            continue
        clip_start = max(tail_start, left - 0.3)
        clip_end = min(duration, right + 0.3)
        clip = audio[int(clip_start * _AUDIO_SAMPLE_RATE):int(clip_end * _AUDIO_SAMPLE_RATE)]
        if len(clip) < int(0.5 * _AUDIO_SAMPLE_RATE):
            continue
        try:
            result, _info = raw_model(
                clip, language=language, vad_filter=False, beam_size=5,
                condition_on_previous_text=False,
            )
            proposals = list(result)
        except Exception as exc:
            log_to_video(video_id, f"Direct speech check at {left:.2f}-{right:.2f}s failed: {exc}")
            continue
        if not proposals or any(
            float(getattr(item, "avg_logprob", -10.0)) < -0.9
            or float(getattr(item, "no_speech_prob", 1.0)) > 0.35
            for item in proposals
        ):
            continue
        text = ""
        for item in proposals:
            text = _merge_transcript_text(text, str(getattr(item, "text", "") or ""))
        text = text.strip()
        if len(text) < 2:
            continue
        if any(text == str(item.get("text") or "").strip() for item in stable):
            continue
        recovered_start = max(tail_start, left - 0.15)
        recovered_end = min(duration, right + 0.15)
        # Do not turn a recovered sentence and the following short reply into
        # overlapping subtitle/voice slots. Keep the existing reply intact.
        for item in stable:
            known_start, known_end = float(item["start"]), float(item["end"])
            if recovered_start < known_start < recovered_end:
                recovered_end = known_start
            elif known_start <= recovered_start < known_end:
                recovered_start = known_end
        if recovered_end - recovered_start < 0.35:
            continue
        log_to_video(video_id, f"Recovered speech missed by VAD at {left:.2f}-{right:.2f}s: {text}")
        recovered.append({
            "start": recovered_start,
            "end": recovered_end,
            "text": text,
            "language": language,
            "language_confidence": 1.0,
        })
    return sorted([*stable, *recovered], key=lambda item: float(item["start"]))


def _alignment_groups(segments: list[dict]) -> list[list[dict]]:
    """Group consecutive same-language sentences for context-aware alignment."""
    groups = []
    current = []
    for segment in segments:
        if current:
            previous = current[-1]
            gap = float(segment.get("start", 0.0)) - float(previous.get("end", 0.0))
            group_span = float(segment.get("end", 0.0)) - float(current[0].get("start", 0.0))
            language_changed = str(segment.get("language") or "en") != str(current[0].get("language") or "en")
            if (
                language_changed
                or gap >= _ALIGNMENT_GROUP_SPLIT_GAP_SECONDS
                or group_span > _ALIGNMENT_GROUP_MAX_SECONDS
            ):
                groups.append(current)
                current = []
        current.append(segment)
    if current:
        groups.append(current)
    return groups


def _split_context_alignment(
    source_segments: list[dict],
    candidate_segments: list[dict],
    search_start: float,
    search_end: float,
) -> tuple[list[dict] | None, str]:
    """Split one context alignment back into its original ordered sentences."""
    expected_word_counts = [len(str(segment.get("text") or "").split()) for segment in source_segments]
    if not expected_word_counts or any(count <= 0 for count in expected_word_counts):
        return None, "source sentence has no alignable words"

    words = [
        word
        for candidate in candidate_segments
        for word in candidate.get("words", [])
        if word.get("start") is not None and word.get("end") is not None
    ]
    expected_total = sum(expected_word_counts)
    if len(words) != expected_total:
        return None, f"aligned word count {len(words)} does not match source count {expected_total}"

    previous_end = search_start
    scores = []
    for word in words:
        start = float(word["start"])
        end = float(word["end"])
        if start < search_start - 0.25 or end > search_end + 0.25:
            return None, "aligned words escaped the context search window"
        if end <= start or start < previous_end - 0.05:
            return None, "aligned words have invalid or overlapping timestamps"
        previous_end = end
        if word.get("score") is not None:
            scores.append(float(word["score"]))

    if scores:
        median_score = statistics.median(scores)
        if median_score < _ALIGNMENT_MIN_MEDIAN_WORD_SCORE:
            return None, (f"median word score {median_score:.3f} is below {_ALIGNMENT_MIN_MEDIAN_WORD_SCORE:.3f}")

    aligned = []
    offset = 0
    for source, count in zip(source_segments, expected_word_counts):
        sentence_words = words[offset : offset + count]
        offset += count
        sentence = dict(source)
        sentence.update(
            {
                "start": float(sentence_words[0]["start"]),
                "end": float(sentence_words[-1]["end"]),
                "words": sentence_words,
            }
        )
        aligned.append(sentence)
    return aligned, f"aligned {len(aligned)} sentences with {len(words)} timed words"


def _alignment_intrusion_detail(
    source_group: list[dict],
    aligned_group: list[dict],
    all_source_segments: list[dict],
    timeline_index=None,
) -> str | None:
    """Describe alignment that newly intrudes into another spoken sentence.

    Language-specific alignment runs against padded audio context.  A short
    phrase can therefore lock onto similar sounds inside a neighbouring
    sentence.  Only reject overlap introduced by alignment; overlap already
    present in Whisper's source spans is left for the final boundary validator.
    """
    if len(source_group) != len(aligned_group):
        return "aligned sentence count changed"

    group_ids = {id(segment) for segment in source_group}
    for source, aligned in zip(source_group, aligned_group):
        source_start = float(source.get("start", 0.0))
        source_end = float(source.get("end", source_start))
        aligned_start = float(aligned.get("start", source_start))
        aligned_end = float(aligned.get("end", aligned_start))
        if timeline_index is None:
            candidates = all_source_segments
        else:
            ordered, starts, prefix_max_ends = timeline_index
            stop = bisect.bisect_left(starts, aligned_end)
            begin = bisect.bisect_right(prefix_max_ends, aligned_start, 0, stop)
            candidates = ordered[begin:stop]
        for other in candidates:
            if id(other) in group_ids:
                continue
            other_start = float(other.get("start", 0.0))
            other_end = float(other.get("end", other_start))
            if other_end <= aligned_start:
                continue
            source_overlap = max(0.0, min(source_end, other_end) - max(source_start, other_start))
            aligned_overlap = max(0.0, min(aligned_end, other_end) - max(aligned_start, other_start))
            if aligned_overlap > source_overlap + 0.05:
                return (
                    f"new {aligned_overlap:.3f}s overlap with another source sentence "
                    f"(previously {source_overlap:.3f}s)"
                )
    return None


def _resolve_context_alignments(groups: list[tuple[list[dict], list[dict] | None]], video_id: str) -> list[dict]:
    """Validate group boundaries against corrected neighbours, not rough ASR spans.

    Splitting a long Whisper span proportionally can put a neighbour's end
    inside the next real sentence. Rejecting against that provisional boundary
    would throw away an entire good alignment group. Resolve all locally valid
    candidates together; a rejected group falls back before checking again.
    """
    accepted = [candidate for _source, candidate in groups]
    while True:
        check_cancellation(video_id)
        rejected = []
        timeline_segments = [
            segment
            for index, (original, _aligned) in enumerate(groups)
            for segment in (accepted[index] or original)
        ]
        timeline_index = _timeline_interval_index(timeline_segments)
        for index, (source, _candidate) in enumerate(groups):
            candidate = accepted[index]
            if candidate is None:
                continue
            detail = _alignment_intrusion_detail(source, candidate, timeline_segments, timeline_index)
            if detail:
                rejected.append(index)
                log_to_video(video_id, f"WARNING: Rejected context alignment at {source[0]['start']:.3f}s "
                             f"against resolved neighbouring sentences ({detail}). Preserving Whisper timing.")
        if not rejected:
            break
        for index in rejected:
            accepted[index] = None
    return [segment for (source, _candidate), candidate in zip(groups, accepted) for segment in (candidate or source)]


def _repair_alignment_group_boundary(previous_source, previous_aligned, source, aligned,
                                     audio, align_model, metadata, device, video_id):
    """Jointly align only the two sentences at a disputed same-language boundary."""
    if float(previous_aligned[-1]["end"]) <= float(aligned[0]["start"]) + 0.05:
        return
    start = max(0.0, float(previous_aligned[-1]["start"]) - 1.0)
    end = min(len(audio) / _AUDIO_SAMPLE_RATE, float(aligned[0]["end"]) + 1.0)
    if len(previous_aligned) > 1:
        start = max(start, float(previous_aligned[-2]["end"]))
    if len(aligned) > 1:
        end = min(end, float(aligned[1]["start"]))
    if end <= start or end - start > _ALIGNMENT_GROUP_MAX_SECONDS:
        return
    check_cancellation(video_id)
    boundary_source = [previous_source[-1], source[0]]
    result = _align_without_nltk_download(
        [{"start": start, "end": end, "text": " ".join(item["text"] for item in boundary_source)}],
        align_model, metadata, audio, device, return_char_alignments=False,
    )
    boundary, _detail = _split_context_alignment(boundary_source, result.get("segments", []), start, end)
    if not boundary:
        return
    if len(previous_aligned) > 1 and boundary[0]["start"] < previous_aligned[-2]["end"] - 0.05:
        return
    if len(aligned) > 1 and boundary[1]["end"] > aligned[1]["start"] + 0.05:
        return
    previous_aligned[-1] = boundary[0]
    aligned[0] = boundary[1]
    log_to_video(video_id, f"Resolved context-group boundary with joint sentence alignment "
                 f"at {boundary[0]['end']:.3f}-{boundary[1]['start']:.3f}s; kept both groups' word timing.")


def _alignment_quality(source_segment: dict, aligned_segments: list[dict]) -> tuple[bool, str]:
    """Reject aligners that return legal-looking but physically impossible timing."""
    if not aligned_segments:
        return False, "no aligned sentences"

    source_start = float(source_segment.get("start", 0.0))
    source_end = float(source_segment.get("end", source_start))
    source_duration = source_end - source_start
    aligned_start = min(float(segment.get("start", source_start)) for segment in aligned_segments)
    aligned_end = max(float(segment.get("end", aligned_start)) for segment in aligned_segments)
    aligned_duration = aligned_end - aligned_start
    if source_duration <= 0 or aligned_duration <= 0:
        return False, "non-positive duration"
    if aligned_start < source_start - 0.25 or aligned_end > source_end + 0.25:
        return False, "timestamps escaped the Whisper source span"

    coverage_ratio = aligned_duration / source_duration
    if coverage_ratio < _ALIGNMENT_MIN_COVERAGE_RATIO:
        return False, f"coverage {coverage_ratio:.2f} is below {_ALIGNMENT_MIN_COVERAGE_RATIO:.2f}"

    word_scores = [
        float(word["score"])
        for segment in aligned_segments
        for word in segment.get("words", [])
        if word.get("score") is not None
    ]
    if word_scores:
        median_score = statistics.median(word_scores)
        if median_score < _ALIGNMENT_MIN_MEDIAN_WORD_SCORE:
            return False, (f"median word score {median_score:.3f} is below {_ALIGNMENT_MIN_MEDIAN_WORD_SCORE:.3f}")
    return True, f"coverage={coverage_ratio:.2f}"


def _refine_suspicious_sentence_timing(asr_model, audio, segments: list[dict], video_id: str) -> None:
    """Verify stretched forced alignment with the already-loaded ASR model.

    A forced aligner can stretch the last word through a silent shot without
    changing the recognized text. Only outliers are decoded again, and their
    bounds are replaced only if the entire sentence is recognized verbatim.
    """
    decoder = getattr(asr_model, "model", None)
    if decoder is None or not hasattr(decoder, "transcribe"):
        return
    def normalize(value):
        return re.sub(r"[^\w]", "", str(value).casefold())
    duration = len(audio) / _AUDIO_SAMPLE_RATE
    for segment in segments:
        start, end = float(segment["start"]), float(segment["end"])
        words = segment.get("words") or []
        count = len(str(segment.get("text") or "").split())
        word_lengths = [float(word["end"]) - float(word["start"]) for word in words
                        if word.get("start") is not None and word.get("end") is not None]
        stretched_word = bool(word_lengths and max(word_lengths) > max(1.2, statistics.median(word_lengths) * 5))
        if not stretched_word and not (count >= 4 and end - start > max(4.0, count * 0.8)):
            continue
        check_cancellation(video_id)
        left, right = max(0.0, start - 0.6), min(duration, end + 0.4)
        window = audio[round(left * _AUDIO_SAMPLE_RATE):round(right * _AUDIO_SAMPLE_RATE)]
        try:
            candidates, _info = decoder.transcribe(
                window, language=str(segment.get("language") or "en"),
                beam_size=1, word_timestamps=True, condition_on_previous_text=False,
            )
            candidates = list(candidates)
            text = " ".join(candidate.text for candidate in candidates)
            if normalize(text) != normalize(segment["text"]):
                continue
            timed = [word for candidate in candidates for word in (candidate.words or [])]
            if not timed or min(float(word.probability) for word in timed) < 0.15:
                continue
            corrected_start, corrected_end = left + float(timed[0].start), left + float(timed[-1].end)
            if corrected_end - corrected_start < 0.25 or corrected_end > end + 0.4:
                continue
            if corrected_end < end - 0.6:
                segment.update(start=corrected_start, end=corrected_end)
                log_to_video(video_id, f"Corrected stretched sentence timing {start:.3f}-{end:.3f}s "
                             f"to {corrected_start:.3f}-{corrected_end:.3f}s using matching ASR words.")
        except Exception as exc:
            if is_cancelled(video_id):
                raise
            log_to_video(video_id, f"WARNING: Sentence timing verification was unavailable: {exc}")


def _verify_whisperx_vad_asset() -> Path:
    """Reject a missing or modified bootstrap-installed VAD checkpoint."""
    try:
        return verify_whisperx_vad_model(Path(MODELS_DIR) / "whisperx-vad")
    except ModelIntegrityError as exc:
        raise RuntimeError(
            "WhisperX VAD is missing or corrupted. Return to the model setup screen and retry the download."
        ) from exc


class _SingleSentenceSplitter:
    """NLTK-compatible splitter for a source span already split by LinSub."""

    @staticmethod
    def span_tokenize(text: str):
        return [(0, len(text))] if text else []


def _align_without_nltk_download(*args, **kwargs):
    """Run WhisperX alignment without its mutable punkt_tab download path."""
    import whisperx.alignment as alignment_module

    with _ALIGNMENT_PATCH_LOCK:
        original_loader = alignment_module.nltk_load
        alignment_module.nltk_load = lambda _resource: _SingleSentenceSplitter()
        try:
            return whisperx.align(*args, **kwargs)
        finally:
            alignment_module.nltk_load = original_loader


def _verified_alignment_asset(language: str, video_id: str) -> tuple[object, dict]:
    """Load one fixed torchaudio asset after size/SHA-256 verification."""
    bundle_name, _filename, _expected_size, _expected_sha256 = _VERIFIED_ALIGNMENT_MODELS[language]
    cache_directory = Path(MODELS_DIR) / "alignment"
    try:
        verify_alignment_model(cache_directory, language)
    except ModelIntegrityError as exc:
        raise RuntimeError(
            f"The '{language}' alignment model is missing or corrupted. "
            "Return to the model setup screen and retry the download."
        ) from exc
    check_cancellation(video_id)
    # Verify immediately before the pickle-compatible official state
    # dictionary is loaded. weights_only further narrows the deserializer.
    bundle = getattr(torchaudio.pipelines, bundle_name)
    align_model = bundle.get_model(
        dl_kwargs={
            "model_dir": str(cache_directory),
            "weights_only": True,
        }
    )
    labels = bundle.get_labels()
    return align_model, {
        "language": language,
        "dictionary": {character.lower(): index for index, character in enumerate(labels)},
        "type": "torchaudio",
    }


def _align_segments_by_language(audio, segments, device: str, video_id: str, progress_callback=None):
    """Align ordered sentence groups with enough context to correct ASR drift."""
    sentence_segments = _coalesce_short_sentence_segments(
        [sentence for source_segment in segments for sentence in _split_segment_proportionally(source_segment)]
    )
    context_groups = _alignment_groups(sentence_segments)
    previous_groups = {id(group): context_groups[index - 1] if index else None
                       for index, group in enumerate(context_groups)}
    grouped_segments = {}
    ordered_languages = []
    for group in context_groups:
        language = group[0].get("language") or "en"
        if language not in grouped_segments:
            grouped_segments[language] = []
            ordered_languages.append(language)
        grouped_segments[language].append(group)

    alignment_candidates = []
    for language in ordered_languages:
        check_cancellation(video_id)
        language_groups = grouped_segments[language]
        if language not in _VERIFIED_ALIGNMENT_MODELS:
            log_to_video(
                video_id,
                f"No verified word-alignment model for '{language}'; "
                "using the available ASR sentence timing.",
            )
            for group in language_groups:
                alignment_candidates.append((group, None))
            continue
        align_model = None
        try:
            log_to_video(video_id, f"Loading alignment model for language '{language}'.")
            if progress_callback:
                progress_callback("loading_alignment", f"Loading subtitle alignment for {language}")
            align_model, metadata = _verified_alignment_asset(language, video_id)
            align_model = align_model.to(device)
            if progress_callback:
                progress_callback("aligning", f"Aligning {language} subtitles")
            source_index = 0
            audio_duration = len(audio) / _AUDIO_SAMPLE_RATE
            total_groups = len(language_groups)
            for g_idx, group in enumerate(language_groups):
                check_cancellation(video_id)
                if progress_callback and total_groups > 0 and (g_idx % 10 == 0 or g_idx == total_groups - 1):
                    group_time = float(group[0].get("start", 0.0))
                    percent = int(round((g_idx / total_groups) * 100))
                    detail = f"Đồng bộ phụ đề {language}: {_format_time(group_time)} / {_format_time(audio_duration)} ({percent}%)"
                    _report_progress(progress_callback, "aligning", detail, current=int(group_time), total=int(audio_duration))
                group_start_index = source_index + 1
                source_index += len(group)
                group_end_index = source_index
                search_start = max(
                    0.0,
                    float(group[0].get("start", 0.0)) - _ALIGNMENT_GROUP_PADDING_SECONDS,
                )
                search_end = min(
                    audio_duration,
                    float(group[-1].get("end", 0.0)) + _ALIGNMENT_GROUP_PADDING_SECONDS,
                )
                context_segment = {
                    **group[0],
                    "start": search_start,
                    "end": search_end,
                    "text": " ".join(str(segment.get("text") or "").strip() for segment in group),
                }
                try:
                    aligned_result = _align_without_nltk_download(
                        [context_segment],
                        align_model,
                        metadata,
                        audio,
                        device,
                        return_char_alignments=False,
                    )
                    candidate_segments = aligned_result.get("segments", [])
                    context_aligned, quality_detail = _split_context_alignment(
                        group,
                        candidate_segments,
                        search_start,
                        search_end,
                    )
                    if context_aligned:
                        previous_group = previous_groups[id(group)]
                        previous_candidate = next((candidate for source, candidate in alignment_candidates
                                                   if source is previous_group), None)
                        if previous_candidate and previous_group[0].get("language") == language:
                            try:
                                _repair_alignment_group_boundary(previous_group, previous_candidate, group,
                                    context_aligned, audio, align_model, metadata, device, video_id)
                            except Exception as exc:
                                if is_cancelled(video_id):
                                    raise
                                log_to_video(video_id, f"WARNING: Joint group-boundary alignment unavailable: {exc}")
                        alignment_candidates.append((group, context_aligned))
                        log_to_video(
                            video_id,
                            f"Aligned '{language}' source sentences {group_start_index}-{group_end_index} "
                            f"with surrounding speech context ({quality_detail}).",
                        )
                        continue
                    log_to_video(
                        video_id,
                        f"WARNING: Rejected '{language}' context alignment for source sentences "
                        f"{group_start_index}-{group_end_index} "
                        f"({quality_detail}). Preserving Whisper timing with proportional sentence boundaries.",
                    )
                except Exception as exc:
                    if is_cancelled(video_id):
                        raise
                    log_to_video(
                        video_id,
                        f"WARNING: Context alignment failed for '{language}' source sentences "
                        f"{group_start_index}-{group_end_index}. "
                        f"Preserving Whisper timing: {exc}",
                    )
                alignment_candidates.append((group, None))
        except Exception as exc:
            if is_cancelled(video_id):
                raise
            log_to_video(
                video_id,
                f"WARNING: Alignment model failed or is unsupported for '{language}'. "
                f"Preserving Whisper spans with proportional sentence boundaries: {exc}",
            )
            for group in language_groups:
                if not any(source is group for source, _candidate in alignment_candidates):
                    alignment_candidates.append((group, None))
        finally:
            if align_model is not None:
                del align_model
            _release_cuda(video_id, f"{language} alignment")

    aligned_segments = _resolve_context_alignments(alignment_candidates, video_id)
    aligned_segments.sort(key=lambda segment: float(segment.get("start", 0.0)))
    return aligned_segments


def _normalize_sentence_timestamps(segments: list[dict], audio_duration: float) -> int:
    """Make independently aligned sentence spans safe for one sequential timeline.

    WhisperX can align a short foreign-language sentence against surrounding
    context.  That improves the word timing, but its independently aligned span
    may overlap the preceding primary-language span.  Two spoken sentences
    cannot be rendered concurrently by the downstream TTS/subtitle pipeline, so
    split just the ambiguous overlap at its midpoint rather than aborting an
    otherwise valid transcription.

    The function mutates ``segments`` in place and returns the number of joined
    boundaries.  Invalid spans remain the responsibility of the validator below.
    """
    if len(segments) < 2:
        return 0

    minimum_duration = 0.25
    repaired_boundaries = 0
    for previous, current in zip(segments, segments[1:]):
        previous_start = float(previous.get("start", 0.0))
        previous_end = float(previous.get("end", previous_start))
        current_start = float(current.get("start", 0.0))
        current_end = float(current.get("end", current_start))
        overlap = previous_end - current_start
        if overlap <= 0.05:
            continue

        # Keep a short, usable duration for both sentences whenever their
        # source timings make that possible.  The midpoint only affects the
        # region where two independently derived timings disagree.
        previous_minimum = min(minimum_duration, max(0.001, (previous_end - previous_start) / 2.0))
        current_minimum = min(minimum_duration, max(0.001, (current_end - current_start) / 2.0))
        lower_bound = previous_start + previous_minimum
        upper_bound = min(current_end - current_minimum, audio_duration)
        if lower_bound > upper_bound:
            # Leave genuinely malformed spans for _validate_timestamp_invariants
            # so the error remains explicit instead of silently discarding text.
            continue
        boundary = min(max((previous_end + current_start) / 2.0, lower_bound), upper_bound)
        previous["end"] = round(boundary, 3)
        current["start"] = round(boundary, 3)
        repaired_boundaries += 1
    return repaired_boundaries


def _validate_timestamp_invariants(segments: list[dict], audio_duration: float) -> None:
    """Reject timestamp corruption before translation, subtitles or TTS can use it."""
    if not segments:
        raise RuntimeError("WhisperX found no speech segments. The video cannot be dubbed without spoken content.")
    previous_start = -1.0
    previous_end = -1.0
    for index, segment in enumerate(segments, start=1):
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start))
        if not segment.get("text", "").strip():
            raise RuntimeError(f"Whisper sentence {index} has no text.")
        if start < previous_start or end <= start:
            raise RuntimeError(f"Whisper sentence {index} has invalid or non-monotonic timestamps.")
        if start < previous_end - 0.05:
            raise RuntimeError(f"Whisper sentence {index} overlaps the previous sentence timestamp.")
        if end > audio_duration + 0.5:
            raise RuntimeError(f"Whisper sentence {index} ends outside the source audio.")
        previous_start = start
        previous_end = end


def transcribe(
    audio_path: str,
    output_json_path: str,
    source_language: str,
    video_id: str,
    progress_callback=None,
    *,
    model_name: str = "small",
    device_preference: str | None = None,
    checkpoint_path: str | None = None,
    checkpoint_key: str = "",
):
    """Transcribe through WhisperX and align sentence timestamps per language."""
    global _WARM_ASR_MODEL, _WARM_DEVICE, _WARM_MODEL_NAME
    model_name = str(model_name or "small").strip().lower()
    from linsub.services.recognition_progress import RecognitionProgress
    from linsub.utils.atomic_file import atomic_json

    checkpoint = RecognitionProgress(checkpoint_path, audio_path, model=model_name,
                                     language=source_language, key=checkpoint_key) if checkpoint_path else None
    check_cancellation(video_id)
    if checkpoint and checkpoint.stage:
        log_to_video(video_id, f"Resuming WhisperX after saved phase '{checkpoint.stage}' "
                     f"({len(checkpoint.segments)} sentences); completed phases will not be repeated.")
        if checkpoint.reached("complete"):
            atomic_json(output_json_path, checkpoint.segments, indent=2)
            if progress_callback:
                progress_callback("saved", f"Prepared {len(checkpoint.segments)} timestamp-locked sentences")
            return checkpoint.segments, checkpoint.language
    log_to_video(video_id, f"Initializing WhisperX with model '{model_name}'.")
    from linsub.core.hardware import cpu_runtime_profile

    profile = cpu_runtime_profile(runtime_profile(), force_cpu=device_preference == "cpu")
    device = "cuda" if profile.cuda_available and device_preference != "cpu" else "cpu"
    profile = _long_form_cpu_profile(profile, audio_path, device, video_id)
    if model_name == "large-v3-turbo" and device == "cuda" and 0 < profile.total_vram_bytes < 5 * 1024**3:
        raise RuntimeError("Card 4 GB dùng Whisper Small trên GPU; Whisper Turbo cần thêm VRAM.")
    if model_name == "large-v3-turbo" and device != "cuda":
        raise RuntimeError(
            "Whisper large-v3-turbo requires an available NVIDIA GPU. "
            "Choose WhisperX small for CPU or low-VRAM processing."
        )
    compute_type = whisper_compute_type(device)
    log_to_video(
        video_id,
        f"WhisperX device: {device}, compute type: {compute_type}, "
        f"batch size: {profile.whisper_batch_size}, threads: {profile.cpu_threads}.",
    )

    asr_model = None
    using_warm_model = False
    audio = None
    audio_memmap_path = None
    try:
        log_to_video(video_id, "Loading WhisperX transcription model.")
        if progress_callback:
            progress_callback("loading_model", "Loading WhisperX speech model")
        with _MODEL_LOCK:
            if _WARM_ASR_MODEL is not None and _WARM_DEVICE == device and _WARM_MODEL_NAME == model_name:
                asr_model = _WARM_ASR_MODEL
                using_warm_model = True
                log_to_video(video_id, "Reusing warmed WhisperX speech model.")
            else:
                # Startup warms the lightweight model so the common path is
                # responsive.  A project can subsequently select turbo.  Do
                # not keep both models resident: on an 8 GB GPU that can make
                # the turbo load fail even though turbo fits on its own.
                if _WARM_ASR_MODEL is not None:
                    del _WARM_ASR_MODEL
                    _WARM_ASR_MODEL = None
                    _WARM_DEVICE = None
                    _WARM_MODEL_NAME = None
                    gc.collect()
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    log_to_video(
                        video_id,
                        "Released the warmed WhisperX model before switching ASR models.",
                    )
                asr_model = _load_whisper_model(device, compute_type, profile.cpu_threads, model_name)
        _constrain_four_gb_vad(asr_model, profile, device)
        if getattr(asr_model, "_linsub_vad_cpu", False):
            log_to_video(video_id, "Four GB GPU profile: Whisper runs on CUDA; VAD runs on CPU.")
        if progress_callback:
            progress_callback(
                "loading_audio",
                "Đang nạp âm thanh để nhận dạng; video dài có thể mất vài phút",
            )
        log_to_video(video_id, "Loading audio for speech recognition.", component="WHISPERX")
        audio, audio_memmap_path = _load_recognition_audio(
            audio_path, video_id, progress_callback=progress_callback,
        )
        log_to_video(
            video_id,
            f"Audio ready for recognition: {len(audio) / _AUDIO_SAMPLE_RATE / 3600:.2f} hours.",
            component="WHISPERX",
        )
        if source_language != "auto":
            log_to_video(video_id, f"Ignoring legacy source language '{source_language}'; using automatic detection.")

        if checkpoint and checkpoint.reached("transcribed"):
            detected_language, initial_segments = checkpoint.language, checkpoint.segments
        else:
            log_to_video(video_id, "Running WhisperX batched transcription with automatic language detection.")
            result = _transcribe_chunks(
                asr_model,
                audio,
                profile,
                device,
                video_id,
                checkpoint_path=checkpoint_path,
                checkpoint_key=checkpoint_key,
                progress_callback=progress_callback,
            )
            detected_language = result.get("language")
            initial_segments = [
                {
                    **segment,
                    "language": segment.get("language") or detected_language or "en",
                    "language_confidence": float(segment.get("language_confidence", 1.0)),
                }
                for segment in result.get("segments", []) if segment.get("text", "").strip()
            ]
            if checkpoint and initial_segments:
                checkpoint.save("transcribed", initial_segments, detected_language)
        if not initial_segments:
            raise RuntimeError("WhisperX did not return any speech segments.")
        log_to_video(video_id, f"Transcription completed. Primary detected language: '{detected_language}'.")
        if progress_callback and not (checkpoint and checkpoint.reached("aligned")):
            progress_callback("transcribed", f"Detected {detected_language or 'unknown'} speech")

        if detected_language in {"zh", "vi"} and not (checkpoint and checkpoint.reached("recognized")):
            initial_segments = _split_long_unaligned_speech(
                asr_model,
                audio,
                initial_segments,
                profile.whisper_batch_size,
                video_id,
                detected_language,
                progress_callback=progress_callback,
                checkpoint_path=checkpoint_path,
                checkpoint_key=checkpoint_key,
            )
            initial_segments = _recover_speech_without_vad(
                asr_model, audio, initial_segments, detected_language, video_id,
            )
        if checkpoint and not checkpoint.reached("recognized"):
            checkpoint.save("recognized", initial_segments, detected_language)

        alignment_device = "cpu" if device == "cuda" and 0 < profile.total_vram_bytes < 5 * 1024**3 else device
        if checkpoint and checkpoint.reached("aligned"):
            sentence_segments = checkpoint.segments
        else:
            check_cancellation(video_id)
            sentence_segments = _align_segments_by_language(
                audio, initial_segments, alignment_device, video_id, progress_callback=progress_callback,
            )
            if checkpoint:
                checkpoint.save("aligned", sentence_segments, detected_language)
        if progress_callback:
            progress_callback("segmenting", f"Prepared {len(sentence_segments)} complete sentences")

        if checkpoint and checkpoint.reached("languages"):
            source_segments = checkpoint.segments
        else:
            check_cancellation(video_id)
            source_segments = _detect_segment_languages(
                asr_model, audio, sentence_segments, detected_language or "en", video_id,
                progress_callback=progress_callback,
            )
            source_segments = _retranscribe_mixed_language_segments(
                asr_model, audio, source_segments, detected_language or "en", video_id,
            )
            if checkpoint:
                checkpoint.save("languages", source_segments, detected_language)
        has_language_switch = any(
            (segment.get("language") or detected_language or "en") != (detected_language or "en")
            for segment in source_segments
        )
        if has_language_switch:
            aligned_segments = _align_segments_by_language(
                audio,
                source_segments,
                alignment_device,
                video_id,
                progress_callback=None,
            )
        else:
            aligned_segments = source_segments
            log_to_video(video_id, "Keeping validated sentence timestamps; no language-switch realignment is needed.")

        _refine_suspicious_sentence_timing(asr_model, audio, aligned_segments, video_id)
        output_segments = []
        for segment in aligned_segments:
            # Both proportional fallback and context alignment preserve the
            # originating sentence dictionary.  Keep its language metadata;
            # inferring it again from a corrected timestamp can assign a
            # neighbouring sentence's language after a legitimate time shift.
            language = segment.get("language") or detected_language or "en"
            confidence = float(segment.get("language_confidence", 0.0))
            if float(segment["end"]) - float(segment["start"]) < 0.25:
                log_to_video(
                    video_id,
                    "WARNING: Discarded an implausibly short speech timestamp "
                    "that cannot contain its recognized sentence.",
                )
                continue
            output_segments.append(
                {
                    "start": round(float(segment["start"]), 3),
                    "end": round(float(segment["end"]), 3),
                    "text": segment["text"].strip(),
                    "language": language,
                    "language_confidence": round(confidence, 3),
                    "timing_source": TIMING_SOURCE,
                }
            )

        audio_duration = len(audio) / _AUDIO_SAMPLE_RATE
        repaired_boundaries = _normalize_sentence_timestamps(output_segments, audio_duration)
        if repaired_boundaries:
            log_to_video(
                video_id,
                "Normalized "
                f"{repaired_boundaries} overlapping WhisperX sentence boundary/boundaries "
                "after mixed-language alignment.",
            )
        _validate_timestamp_invariants(output_segments, audio_duration)
        if checkpoint:
            checkpoint.save("complete", output_segments, detected_language)
        check_cancellation(video_id)
        if progress_callback:
            progress_callback("detecting_languages", f"Validated {len(output_segments)} timed sentences")
        output_directory = os.path.dirname(os.path.abspath(output_json_path))
        os.makedirs(output_directory, exist_ok=True)
        handle, temporary_path = tempfile.mkstemp(
            prefix=".transcript-",
            suffix=".json.tmp",
            dir=output_directory,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(output_segments, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, output_json_path)
        except Exception:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass
            raise

        log_to_video(video_id, f"Saved {len(output_segments)} timestamp-locked source sentences to: {output_json_path}")
        if progress_callback:
            progress_callback("saved", f"Prepared {len(output_segments)} timestamp-locked sentences")
        return output_segments, detected_language
    finally:
        if asr_model is not None and not using_warm_model:
            del asr_model
        if audio is not None:
            audio_mapping = getattr(audio, "_mmap", None)
            del audio
            if audio_mapping is not None:
                try:
                    audio_mapping.close()
                except OSError:
                    pass
        if audio_memmap_path:
            try:
                os.remove(audio_memmap_path)
            except OSError:
                pass
        _release_cuda(video_id, "WhisperX cleanup")
