import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import numpy as np

# Mock heavy DL dependencies if running in lightweight environment
sys.modules.setdefault("torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None)))
sys.modules.setdefault("torchaudio", SimpleNamespace())
sys.modules.setdefault("whisperx", SimpleNamespace(audio=SimpleNamespace(subprocess=None)))
sys.modules.setdefault("whisperx.audio", SimpleNamespace(subprocess=None))

from linsub.pipeline.transcribe import (
    _format_time,
    _find_optimal_audio_split,
    _calculate_audio_chunks,
    _recognition_chunk_duration,
    _adaptive_cpu_asr_profile,
    _detect_segment_languages,
    _transcribe_with_vad_recovery,
    _transcribe_chunks,
    _split_long_unaligned_speech,
    _pause_split_workers,
    _load_whisper_model,
    _correct_contextual_zh_asr_terms,
    _AUDIO_SAMPLE_RATE,
)


@dataclass(frozen=True)
class _AsrProfile:
    key: str
    cpu_threads: int
    whisper_batch_size: int
    logical_cpu_count: int


class TestTranscribeChunking(unittest.TestCase):
    def test_corrects_ginseng_homophones_only_with_strong_cultivation_context(self):
        source = [
            {
                "start": 75.631,
                "end": 87.258,
                "text": "你这小鬼倒是机灵这是围尸是有一位你准备的万年身亡练画后可增十个甲子的绣位",
            },
            {"start": 92.607, "end": 97.4, "text": "我都望眼欲穿了少给我刷嘴皮速速扶下身亡"},
        ]
        with patch("linsub.pipeline.transcribe.log_to_video") as log:
            corrected = _correct_contextual_zh_asr_terms(source, "zh", "test-video")

        self.assertIn("这是为师为你准备的万年参王炼化后可增十个甲子的修为", corrected[0]["text"])
        self.assertTrue(corrected[0]["asr_quality"]["status"] == "review")
        self.assertIn("contextual_cultivation_term_correction", corrected[0]["asr_quality"]["reasons"])
        self.assertIn("速速服下参王", corrected[1]["text"])
        self.assertEqual(log.call_count, 2)

    def test_does_not_change_ginseng_homophone_without_cultivation_evidence(self):
        source = [{"start": 0, "end": 3, "text": "他已经身亡"}]
        with patch("linsub.pipeline.transcribe.log_to_video") as log:
            corrected = _correct_contextual_zh_asr_terms(source, "zh", "test-video")

        self.assertEqual(corrected, source)
        log.assert_not_called()

    def test_pause_split_worker_count_scales_with_cpu_and_stays_capped_at_five(self):
        self.assertEqual(_pause_split_workers(_AsrProfile("cpu", 8, 8, 16), "cpu"), 5)
        self.assertEqual(_pause_split_workers(_AsrProfile("cpu", 8, 8, 12), "cpu"), 4)
        self.assertEqual(_pause_split_workers(_AsrProfile("cpu", 4, 2, 8), "cpu"), 2)
        self.assertEqual(_pause_split_workers(_AsrProfile("gpu", 4, 2, 16), "cuda"), 1)

    def test_cpu_whisper_model_configures_five_concurrent_decode_workers(self):
        import types

        from linsub.pipeline import transcribe

        asr_module = types.ModuleType("whisperx.asr")
        captured = {}

        def make_model(*_args, **kwargs):
            captured.update(kwargs)
            return "cpu-whisper-model"

        asr_module.WhisperModel = make_model
        with (
            patch.dict(sys.modules, {"whisperx.asr": asr_module}),
            patch("linsub.pipeline.transcribe._verify_whisperx_vad_asset", return_value="vad.bin"),
            patch("linsub.pipeline.transcribe._whisper_model_source", return_value=("small-model", True)),
            patch.object(transcribe.whisperx, "load_model", return_value="loaded", create=True) as load_model,
        ):
            model = _load_whisper_model("cpu", "int8", 8)

        self.assertEqual(model, "loaded")
        self.assertEqual(captured["num_workers"], 5)
        self.assertEqual(captured["cpu_threads"], 8)
        self.assertEqual(load_model.call_args.kwargs["model"], "cpu-whisper-model")

    def test_short_cpu_asr_uses_batch_six_with_moderate_memory_headroom(self):
        profile = _AsrProfile("cpu_low_memory", 4, 2, 16)
        memory = SimpleNamespace(
            available_bytes=int(2.5 * 1024**3),
            commit_available_bytes=int(4 * 1024**3),
        )
        with patch("linsub.core.memory.memory_snapshot", return_value=memory), patch(
            "linsub.pipeline.transcribe.log_to_video"
        ) as log:
            result = _adaptive_cpu_asr_profile(profile, "cpu", "test-video")

        self.assertEqual((result.cpu_threads, result.whisper_batch_size), (8, 6))
        log.assert_called_once()
        self.assertIn("Adaptive CPU recognition", log.call_args.args[1])

    def test_short_cpu_asr_uses_batch_eight_with_three_gib_free(self):
        profile = _AsrProfile("cpu_low_memory", 4, 2, 16)
        memory = SimpleNamespace(
            available_bytes=int(3.5 * 1024**3),
            commit_available_bytes=int(6 * 1024**3),
        )
        with patch("linsub.core.memory.memory_snapshot", return_value=memory), patch(
            "linsub.pipeline.transcribe.log_to_video"
        ):
            result = _adaptive_cpu_asr_profile(profile, "cpu", "test-video")

        self.assertEqual((result.cpu_threads, result.whisper_batch_size), (8, 8))

    def test_short_cpu_asr_keeps_batch_eight_with_high_memory_headroom(self):
        profile = _AsrProfile("cpu_low_memory", 4, 2, 16)
        memory = SimpleNamespace(
            available_bytes=5 * 1024**3,
            commit_available_bytes=9 * 1024**3,
        )
        with patch("linsub.core.memory.memory_snapshot", return_value=memory), patch(
            "linsub.pipeline.transcribe.log_to_video"
        ):
            result = _adaptive_cpu_asr_profile(profile, "cpu", "test-video")

        self.assertEqual((result.cpu_threads, result.whisper_batch_size), (8, 8))

    def test_cpu_asr_uses_physical_cores_but_keeps_batch_when_memory_headroom_is_low(self):
        profile = _AsrProfile("cpu_low_memory", 4, 2, 16)
        memory = SimpleNamespace(
            available_bytes=int(1.5 * 1024**3),
            commit_available_bytes=6 * 1024**3,
        )
        with patch("linsub.core.memory.memory_snapshot", return_value=memory), patch(
            "linsub.pipeline.transcribe.log_to_video"
        ):
            result = _adaptive_cpu_asr_profile(profile, "cpu", "test-video")

        self.assertEqual((result.cpu_threads, result.whisper_batch_size), (8, 2))

    def test_balanced_cpu_profile_also_uses_physical_cores_and_live_memory_headroom(self):
        profile = _AsrProfile("cpu_balanced", 4, 2, 16)
        memory = SimpleNamespace(
            available_bytes=int(3.5 * 1024**3),
            commit_available_bytes=int(6 * 1024**3),
        )
        with patch("linsub.core.memory.memory_snapshot", return_value=memory), patch(
            "linsub.pipeline.transcribe.log_to_video"
        ):
            result = _adaptive_cpu_asr_profile(profile, "cpu", "test-video")

        self.assertEqual((result.cpu_threads, result.whisper_batch_size), (8, 8))

    def test_cpu_minimum_profile_still_uses_physical_cores(self):
        profile = _AsrProfile("cpu_minimum", 2, 1, 16)
        memory = SimpleNamespace(
            available_bytes=1024**3,
            commit_available_bytes=2 * 1024**3,
        )
        with patch("linsub.core.memory.memory_snapshot", return_value=memory):
            result = _adaptive_cpu_asr_profile(profile, "cpu", "test-video")

        self.assertEqual((result.cpu_threads, result.whisper_batch_size), (8, 1))

    def test_cpu_asr_reduces_batch_and_retries_after_memory_pressure(self):
        class Model:
            vad_model = SimpleNamespace(vad_pipeline=None)

            def __init__(self):
                self.batches = []

            def transcribe(self, _audio, *, batch_size, language):
                self.batches.append(batch_size)
                if len(self.batches) == 1:
                    raise RuntimeError("DefaultCPUAllocator: not enough memory")
                return {"segments": [], "language": language}

        model = Model()
        profile = SimpleNamespace(whisper_batch_size=8, total_vram_bytes=0)
        with patch("linsub.pipeline.transcribe.log_to_video"):
            result = _transcribe_with_vad_recovery(
                model, np.zeros(8, dtype=np.float32), profile, "cpu", "test-video",
            )

        self.assertEqual(model.batches, [8, 4])
        self.assertEqual(result["segments"], [])

    def test_long_recordings_use_five_minute_chunks_with_verified_cpu_headroom(self):
        profile = SimpleNamespace(key="cpu_low_memory", cpu_threads=6, whisper_batch_size=4)

        self.assertEqual(_recognition_chunk_duration(profile, "cpu", 3 * 3600), 300.0)
        self.assertEqual(_recognition_chunk_duration(profile, "cpu", 20 * 3600), 300.0)

    def test_long_recordings_cap_chunks_at_five_minutes_with_constrained_cpu_headroom(self):
        profile = SimpleNamespace(key="cpu_low_memory", cpu_threads=4, whisper_batch_size=2)

        self.assertEqual(_recognition_chunk_duration(profile, "cpu", 3 * 3600), 300.0)
        self.assertEqual(_recognition_chunk_duration(profile, "cpu", 20 * 3600), 300.0)

    def test_long_recordings_cap_chunks_at_five_minutes_at_medium_cpu_headroom(self):
        profile = SimpleNamespace(key="cpu_low_memory", cpu_threads=4, whisper_batch_size=3)

        self.assertEqual(_recognition_chunk_duration(profile, "cpu", 3 * 3600), 300.0)
        self.assertEqual(_recognition_chunk_duration(profile, "cpu", 20 * 3600), 300.0)

    def test_long_consistent_chunks_skip_the_second_language_encoder_pass(self):
        class AudioLengthOnly:
            def __len__(self):
                return 3 * 3600 * _AUDIO_SAMPLE_RATE

        class Model:
            def detect_language(self, **_kwargs):
                raise AssertionError("consistent WhisperX chunk languages should skip this pass")

        source = [{"start": 1.0, "end": 2.0, "text": "Hello", "language": "en"}]
        progress = []
        with patch("linsub.pipeline.transcribe.log_to_video"):
            result = _detect_segment_languages(
                SimpleNamespace(model=Model()), AudioLengthOnly(), source,
                "en", "test-video", lambda *args: progress.append(args),
            )

        self.assertEqual(len(result), len(source))
        self.assertEqual(result[0]["text"], source[0]["text"])
        self.assertEqual(result[0]["language"], source[0]["language"])
        self.assertIsNot(result[0], source[0])
        self.assertTrue(progress)

    def test_format_time(self):
        self.assertEqual(_format_time(0), "00:00")
        self.assertEqual(_format_time(45), "00:45")
        self.assertEqual(_format_time(600), "10:00")
        self.assertEqual(_format_time(3600), "01:00:00")
        self.assertEqual(_format_time(11396), "03:09:56")

    def test_find_optimal_audio_split(self):
        sr = _AUDIO_SAMPLE_RATE
        # 30 seconds of audio with loud noise except a 2-second silence at 15s
        audio = np.ones(30 * sr, dtype=np.float32)
        # silence at 14s-16s
        audio[14 * sr : 16 * sr] = 0.0

        split = _find_optimal_audio_split(audio, target_sample=15 * sr, search_half_window_sec=5.0)
        # Split point should be within the silent region [14s, 16s]
        self.assertGreaterEqual(split, 14 * sr)
        self.assertLessEqual(split, 16 * sr)

    def test_calculate_audio_chunks_short_audio(self):
        sr = _AUDIO_SAMPLE_RATE
        # 5 minutes audio (less than 12m threshold)
        audio = np.zeros(300 * sr, dtype=np.float32)
        chunks = _calculate_audio_chunks(audio, target_chunk_sec=600.0)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], (0, 300 * sr))

    def test_short_low_memory_video_uses_one_asr_vad_pass(self):
        profile = _AsrProfile("cpu_low_memory", 4, 2, 16)
        target = _recognition_chunk_duration(profile, "cpu", 228.42)
        chunks = _calculate_audio_chunks(
            np.zeros(round(228.42 * _AUDIO_SAMPLE_RATE), dtype=np.float32),
            target_chunk_sec=target,
        )

        self.assertEqual(target, 600.0)
        self.assertEqual(len(chunks), 1)

    def test_calculate_audio_chunks_long_audio_continuity(self):
        sr = _AUDIO_SAMPLE_RATE
        # 35 minutes of audio
        total_samples = 35 * 60 * sr
        audio = np.zeros(total_samples, dtype=np.float32)
        chunks = _calculate_audio_chunks(audio, target_chunk_sec=600.0)

        # Chunks must cover start to end continuously without gaps
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0][0], 0)
        self.assertEqual(chunks[-1][1], total_samples)

        for i in range(len(chunks) - 1):
            # End of chunk i must equal start of chunk i + 1
            self.assertEqual(chunks[i][1], chunks[i + 1][0])

    def test_transcribe_chunks_timestamp_shifting_and_checkpoint(self):
        sr = _AUDIO_SAMPLE_RATE
        # 25 minutes audio: should create ~2-3 chunks of 10m
        total_samples = 25 * 60 * sr
        audio = np.zeros(total_samples, dtype=np.float32)

        calls = []

        languages = []

        def fake_vad_transcribe(asr_model, chunk_audio, profile, device, video_id, *, language=None):
            idx = len(calls)
            calls.append(idx)
            languages.append(language)
            return {
                "segments": [
                    {"start": 1.0, "end": 4.0, "text": f"Phrase in chunk {idx}"}
                ],
                "language": "vi",
            }

        with tempfile.TemporaryDirectory() as td:
            ckpt_path = Path(td) / "progress.json"

            with patch("linsub.pipeline.transcribe._transcribe_with_vad_recovery", side_effect=fake_vad_transcribe):
                profile = MagicMock(whisper_batch_size=1)
                result = _transcribe_chunks(
                    asr_model=None,
                    audio=audio,
                    profile=profile,
                    device="cpu",
                    video_id="test-vid",
                    checkpoint_path=str(ckpt_path),
                    checkpoint_key="key123",
                )

            # Check that segments were shifted appropriately and merged
            self.assertGreaterEqual(len(result["segments"]), 2)
            self.assertEqual(result["language"], "vi")
            self.assertEqual(languages[0], None)
            self.assertTrue(all(language == "vi" for language in languages[1:]))
            self.assertAlmostEqual(result["segments"][0]["start"], 1.0, places=1)
            self.assertGreater(result["segments"][1]["start"], 500.0)

            # Ensure all segments are strictly sorted by start
            starts = [s["start"] for s in result["segments"]]
            self.assertEqual(starts, sorted(starts))

    def test_transcribe_chunks_resumes_from_checkpoint(self):
        sr = _AUDIO_SAMPLE_RATE
        total_samples = 25 * 60 * sr
        audio = np.zeros(total_samples, dtype=np.float32)

        with tempfile.TemporaryDirectory() as td:
            ckpt_path = Path(td) / "progress.json"
            chunk_file = Path(td) / "recognition-chunks.json"

            chunks = _calculate_audio_chunks(audio, target_chunk_sec=600.0)
            first_chunk_end = chunks[0][1] / sr
            import json
            chunk_file.write_text(json.dumps({
                "key": "key123",
                "chunks": [
                    {
                        "index": 0,
                        "start_time": 0.0,
                        "end_time": first_chunk_end,
                        "segments": [{"start": 2.0, "end": 5.0, "text": "Pre-existing chunk 0 segment"}],
                        "language": "vi"
                    }
                ],
                "detected_languages": ["vi"]
            }))

            calls = []

            received_languages = []

            def fake_vad_transcribe(asr_model, chunk_audio, profile, device, video_id, *, language=None):
                calls.append(len(calls))
                received_languages.append(language)
                return {
                    "segments": [
                        {"start": 1.0, "end": 3.0, "text": "New segment"}
                    ],
                    "language": "vi",
                }

            with patch("linsub.pipeline.transcribe._transcribe_with_vad_recovery", side_effect=fake_vad_transcribe):
                profile = MagicMock(whisper_batch_size=1)
                result = _transcribe_chunks(
                    asr_model=None,
                    audio=audio,
                    profile=profile,
                    device="cpu",
                    video_id="test-vid",
                    checkpoint_path=str(ckpt_path),
                    checkpoint_key="key123",
                )

            chunks = _calculate_audio_chunks(audio, target_chunk_sec=600.0)
            self.assertEqual(len(calls), len(chunks) - 1)
            self.assertTrue(all(language == "vi" for language in received_languages))
            self.assertEqual(result["segments"][0]["text"], "Pre-existing chunk 0 segment")
            self.assertEqual(result["segments"][0]["start"], 2.0)
            self.assertFalse(chunk_file.exists())

    def test_pause_split_uses_five_workers_across_spans_and_bounds_inflight_clips(self):
        class LazyAudio:
            def __len__(self):
                return 15 * 60 * _AUDIO_SAMPLE_RATE

            def __getitem__(self, key):
                start_seconds = key.start / _AUDIO_SAMPLE_RATE
                with lock:
                    nonlocal_materialized[0] += 1
                    outstanding = nonlocal_materialized[0] - nonlocal_completed[0]
                    materialized_peak[0] = max(materialized_peak[0], outstanding)
                return np.full(key.stop - key.start, start_seconds, dtype=np.float32)

        windows = (
            [(1.0, 3.0), (4.0, 6.0), (7.0, 9.0), (10.0, 12.0), (13.0, 15.0), (16.0, 18.0)]
            + [(21.0, 23.0), (24.0, 26.0), (27.0, 29.0), (30.0, 32.0), (33.0, 35.0), (36.0, 38.0)]
        )
        active = 0
        peak_active = 0
        active_spans_at_peak = set()
        lock = threading.Lock()
        nonlocal_materialized = [0]
        nonlocal_completed = [0]
        materialized_peak = [0]

        def recognize_clip(_model, clip, _batch_size, _language, **kwargs):
            nonlocal active, peak_active, active_spans_at_peak
            span_id = 0 if float(clip[0]) < 20.0 else 1
            with lock:
                active += 1
                if active > peak_active:
                    peak_active = active
                    active_spans_at_peak = set()
                if active == peak_active:
                    active_spans_at_peak.add(span_id)
            time.sleep(0.05)
            try:
                text = f"utterance-{float(clip[0]):.1f}"
                return (text, None) if kwargs.get("return_quality") else text
            finally:
                with lock:
                    nonlocal_completed[0] += 1
                    active -= 1

        segments = [
            {"start": 0.0, "end": 19.0, "text": "first long span", "language": "zh"},
            {"start": 20.0, "end": 39.0, "text": "second long span", "language": "zh"},
        ]
        def windows_for_segment(_regions, start, _end, **_kwargs):
            return windows[:6] if start < 20 else windows[6:]

        log_messages = []
        with patch("linsub.pipeline.transcribe._speech_regions_in_audio_windows", return_value=windows), patch(
            "linsub.pipeline.transcribe._speech_windows_in_segment", side_effect=windows_for_segment
        ), patch("linsub.pipeline.transcribe._transcribe_isolated_speech_clip", side_effect=recognize_clip), patch(
            "linsub.pipeline.transcribe._recover_uncovered_speech", return_value=[]
        ), patch("linsub.pipeline.transcribe.log_to_video", side_effect=lambda _video, message, **_kwargs: log_messages.append(message)):
            result = _split_long_unaligned_speech(
                None, LazyAudio(), segments, 8, "test-video", "zh", parallel_workers=5,
            )

        self.assertEqual(peak_active, 5)
        self.assertEqual(active_spans_at_peak, {0, 1})
        self.assertLessEqual(materialized_peak[0], 10)
        self.assertEqual(len(result), len(windows))
        self.assertEqual([item["start"] for item in result], [item[0] for item in windows])
        self.assertTrue(any("at most 10 in-flight clips" in message for message in log_messages))


if __name__ == "__main__":
    unittest.main()

