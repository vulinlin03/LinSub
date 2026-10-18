import json
import math
import os
import struct
import subprocess
import tempfile
import time
import wave

from linsub.utils.audio import AudioSegment

from linsub.config import MEDIA_PROCESS_TIMEOUT_SECONDS
from linsub.pipeline.process_registry import check_cancellation, communicate_process, is_cancelled
from linsub.services.video_store import log_to_video
from linsub.utils.ffmpeg import _binary, get_video_duration

_FINAL_AUDIO_TAIL_MARGIN_MS = 120
_ATOMIC_REPLACE_ATTEMPTS = 8
_LONG_TIMELINE_CHUNK_MS = 10 * 60 * 1000
_LONG_TIMELINE_CHUNK_THRESHOLD_MS = 15 * 60 * 1000


def _replace_exported_audio(temporary_path: str, output_path: str) -> None:
    """Replace a generated WAV after transient Windows media-handle locks clear."""
    last_error: OSError | None = None
    for attempt in range(_ATOMIC_REPLACE_ATTEMPTS):
        try:
            os.replace(temporary_path, output_path)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt + 1 >= _ATOMIC_REPLACE_ATTEMPTS:
                break
            time.sleep(0.08 * (attempt + 1))
    if last_error is not None:
        raise last_error


def _apply_volume(audio: AudioSegment, volume: int, label: str, video_id: str, *, log: bool = True) -> AudioSegment:
    """Apply a linear percentage without changing track selection."""
    volume = max(0, min(100, int(volume)))
    if volume == 0:
        if log:
            log_to_video(video_id, f"{label} volume set to 0%. Muting this track.")
        return audio - 100
    db_change = 20 * math.log10(volume / 100.0)
    if log:
        log_to_video(video_id, f"{label} volume set to {volume}%. Applying {db_change:.2f} dB.")
    return audio + db_change


def _fit_to_duration(audio: AudioSegment, duration_ms: int) -> AudioSegment:
    """Loop a non-empty music source, then trim it exactly to the video."""
    if len(audio) <= 0:
        raise RuntimeError("Background music contains no playable audio.")
    return (audio * max(1, math.ceil(duration_ms / len(audio))))[:duration_ms]


def _fade_clip(audio: AudioSegment, fade_in_ms: int, fade_out_ms: int) -> AudioSegment:
    fade_in = min(len(audio), max(0, int(fade_in_ms)))
    fade_out = min(len(audio), max(0, int(fade_out_ms)))
    if fade_in:
        audio = audio.fade_in(fade_in)
    if fade_out:
        audio = audio.fade_out(fade_out)
    return audio


def _window_fades(audio, offset_ms, clip_duration_ms, fade_in_ms, fade_out_ms):
    """Continue the original fade envelope, not a new fade at the cut."""
    def db(gain):
        return 20 * math.log10(max(0.000001, gain))

    fade_in_ms = min(clip_duration_ms, max(0, fade_in_ms))
    fade_out_ms = min(clip_duration_ms, max(0, fade_out_ms))
    if fade_in_ms and offset_ms < fade_in_ms:
        duration = min(len(audio), fade_in_ms - offset_ms)
        audio = audio.fade(from_gain=db(offset_ms / fade_in_ms),
                           to_gain=db((offset_ms + duration) / fade_in_ms), start=0, duration=duration)
    fade_start = clip_duration_ms - fade_out_ms
    if fade_out_ms and offset_ms + len(audio) > fade_start:
        start = max(0, fade_start - offset_ms)
        audio = audio.fade(from_gain=db((clip_duration_ms - offset_ms - start) / fade_out_ms),
                           to_gain=db(max(0, clip_duration_ms - offset_ms - len(audio)) / fade_out_ms),
                           start=start, duration=len(audio) - start)
    return audio


def _decode_window(path, start_ms, duration_ms, process_id, *, loop=False):
    """Seek/decode only the requested audio interval with registered cancellation."""
    handle, temporary = tempfile.mkstemp(prefix=".audio-window-", suffix=".wav")
    os.close(handle)
    try:
        command = [_binary("ffmpeg"), "-y", "-v", "error"]
        if loop:
            command += ["-stream_loop", "-1"]
        command += ["-ss", str(start_ms / 1000), "-i", str(path), "-t", str(duration_ms / 1000),
                    "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", temporary]
        check_cancellation(process_id)
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _, error = communicate_process(process_id, process, label="Segment audio decode")
        if process.returncode:
            raise RuntimeError(f"Could not decode segment audio: {error[-900:]}")
        check_cancellation(process_id)
        return AudioSegment.from_file(temporary)
    finally:
        os.unlink(temporary)


def _duck_music(
    music: AudioSegment,
    voice_ranges: list[tuple[int, int]],
    reduction_db: float,
    attack_ms: int,
    release_ms: int,
) -> AudioSegment:
    """Apply deterministic range ducking without an effect-chain dependency."""
    if not voice_ranges or reduction_db >= 0:
        return music
    expanded = sorted(
        (
            max(0, int(start) - max(0, int(attack_ms))),
            min(len(music), int(end) + max(0, int(release_ms))),
        )
        for start, end in voice_ranges
        if int(end) > int(start)
    )
    merged: list[list[int]] = []
    for start, end in expanded:
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    result = music
    for start, end in merged:
        section = result[start:end]
        attack = min(max(0, int(attack_ms)), len(section))
        release = min(max(0, int(release_ms)), max(0, len(section) - attack))
        body_end = len(section) - release
        pieces: list[AudioSegment] = []
        if attack:
            pieces.append(section[:attack].fade(
                from_gain=0,
                to_gain=float(reduction_db),
                start=0,
                duration=attack,
            ))
        if body_end > attack:
            pieces.append(section[attack:body_end] + float(reduction_db))
        if release:
            pieces.append(section[body_end:].fade(
                from_gain=float(reduction_db),
                to_gain=0,
                start=0,
                duration=release,
            ))
        lowered = sum(pieces[1:], pieces[0]) if pieces else section
        result = result[:start] + lowered + result[end:]
    return result


def _segment_slot_end_ms(
    start_ms: int,
    segment_end_ms: int,
    next_start_ms: int,
    video_duration_ms: int,
    *,
    is_last: bool,
) -> int:
    """Return the end of the original spoken window for one dubbed line.

    The previous implementation used the next line's start as the slot end.
    That prevented overlap, but let translated speech occupy pauses after the
    actor had stopped speaking.  Matching the source segment's own end keeps
    both ends of the dubbed line synchronized with fast dialogue.

    Invalid legacy timestamps fall back to the next safe boundary so an old
    project remains recoverable.
    """
    video_duration_ms = max(0, video_duration_ms)
    hard_end_ms = video_duration_ms
    if next_start_ms > start_ms:
        hard_end_ms = min(hard_end_ms, next_start_ms)

    if segment_end_ms > start_ms:
        return min(hard_end_ms, segment_end_ms)

    fallback_end_ms = min(video_duration_ms, max(start_ms, next_start_ms))
    if is_last and fallback_end_ms - start_ms > _FINAL_AUDIO_TAIL_MARGIN_MS * 2:
        fallback_end_ms -= _FINAL_AUDIO_TAIL_MARGIN_MS
    return fallback_end_ms


def calculate_speech_fitting_window(
    start_ms: int,
    segment_end_ms: int,
    next_start_ms: int,
    video_dur_ms: int,
    tts_dur_ms: int,
    *,
    is_last: bool = False,
) -> tuple[int, float]:
    """Calculate the natural speech placement window and optimal pitch-preserving tempo.

    In human dubbing:
    1. Speech is not artificially accelerated when there is natural silence
       between dialogue turns. A gentle natural tail into the following pause (up to 400-500ms)
       is visually imperceptible and preserves natural speech cadence.
    2. Overlaps with subsequent speakers are strictly prevented by reserving a safety buffer.
    3. Severe compression (>1.28x) is avoided to eliminate robotic/chipmunk distortions.
    4. Very short utterances in extended dialogue slots receive mild, natural relaxation (0.93-0.95x).
    """
    video_dur_ms = max(0, int(video_dur_ms))
    start_ms = max(0, int(start_ms))
    tts_dur_ms = max(1, int(tts_dur_ms))

    # Base source duration
    source_end = segment_end_ms if segment_end_ms > start_ms else next_start_ms
    source_dur = max(1, source_end - start_ms)

    # Absolute ceiling before next dialogue or end of video
    hard_limit = video_dur_ms
    if is_last and hard_limit - start_ms > _FINAL_AUDIO_TAIL_MARGIN_MS * 2:
        hard_limit -= _FINAL_AUDIO_TAIL_MARGIN_MS
    if next_start_ms > start_ms:
        hard_limit = min(hard_limit, next_start_ms)

    safety_margin_ms = 70  # Clean breath gap before next speaker begins
    max_room = max(1, hard_limit - start_ms - safety_margin_ms)

    # Case 1: Audio fits within the actor's original spoken window
    if tts_dur_ms <= source_dur:
        # If line is extremely short compared to actor's window (> 2.0s slot),
        # apply subtle, natural pacing relaxation so it doesn't abruptly stop
        if tts_dur_ms < source_dur * 0.60 and source_dur >= 2000:
            target_dur = min(source_dur, int(tts_dur_ms / 0.94))
            return target_dur, tts_dur_ms / target_dur
        return tts_dur_ms, 1.0

    # Case 2: Audio overruns source speech window
    gap_after = max(0, hard_limit - source_end)
    # Allow natural lip-sync tail into the idle pause following the line
    natural_tail = min(500, max(200, int(source_dur * 0.35)))
    comfortable_dur = min(max_room, source_dur + min(gap_after, natural_tail))

    # If it fits within the comfortable natural-tail window, play without compression!
    if tts_dur_ms <= comfortable_dur:
        return tts_dur_ms, 1.0

    # Case 3: Needs tempo adjustment to avoid colliding with next line
    ideal_speed = tts_dur_ms / max(1, comfortable_dur)
    if ideal_speed > 1.25 and max_room > comfortable_dur:
        # Utilize more of the idle gap before next speaker to avoid high compression
        expanded_target = min(max_room, int(tts_dur_ms / 1.22))
        target_dur = max(comfortable_dur, expanded_target)
    else:
        target_dur = comfortable_dur

    speed = tts_dur_ms / max(1, target_dur)
    # Cap speed factor at 1.28x max to preserve natural human articulation
    if speed > 1.28 and max_room > target_dur:
        target_dur = min(max_room, int(tts_dur_ms / 1.28))
        speed = tts_dur_ms / max(1, target_dur)

    return target_dur, speed


def trim_silence(audio: AudioSegment, silence_threshold_db: float = -50.0) -> AudioSegment:
    """Trims leading and trailing silence from an AudioSegment to remove delay and trailing padding."""
    start_trim = 0
    chunk_size = 10
    for i in range(0, len(audio), chunk_size):
        chunk = audio[i:i+chunk_size]
        if chunk.dBFS > silence_threshold_db:
            # Keep a small 30ms buffer to avoid abrupt cut-off
            start_trim = max(0, i - 30)
            break
            
    end_trim = len(audio)
    for i in range(len(audio), 0, -chunk_size):
        chunk = audio[i - chunk_size : i]
        if chunk.dBFS > silence_threshold_db:
            # Keep a small 50ms buffer at the end
            end_trim = min(len(audio), i + 50)
            break
            
    if start_trim < end_trim:
        return audio[start_trim:end_trim]
    return audio


def fit_speech_to_natural_cadence(
    audio: AudioSegment,
    start_ms: int,
    segment_end_ms: int,
    next_start_ms: int,
    video_dur_ms: int,
    temp_dir: str,
    cancellation_id: str,
    *,
    is_last: bool = False,
) -> AudioSegment:
    """Adapt synthesized speech to natural dialogue pacing without breaking sentence continuity.

    1. Natural continuity:
       - Preserves the entire synthesized sentence as one cohesive, unbroken utterance.
       - NEVER arbitrarily cuts or injects artificial silence into the middle of a continuous phrase.
       - Internal breath pauses remain strictly governed by the actual punctuation marks in the text.
    2. Inter-sentence breathing pauses (nhịp nghỉ giữa các câu):
       - Overruns are gently compressed (capped at 1.28x max speed) to guarantee a clean breathing
         gap before the next speaker/sentence starts.
       - For very short utterances in extended dialogue slots (>2000ms), applies gentle, subtle
         relaxation (0.92x-0.95x) so speech is unhurried and clear, leaving a natural resting
         pause before the next line begins.
    """
    audio = trim_silence(audio)
    if len(audio) <= 0:
        return audio

    source_end = segment_end_ms if segment_end_ms > start_ms else next_start_ms
    source_dur = max(1, source_end - start_ms)
    hard_limit = video_dur_ms
    if is_last and hard_limit - start_ms > _FINAL_AUDIO_TAIL_MARGIN_MS * 2:
        hard_limit -= _FINAL_AUDIO_TAIL_MARGIN_MS
    if next_start_ms > start_ms:
        hard_limit = min(hard_limit, next_start_ms)

    safety_margin_ms = 70
    max_room = max(1, hard_limit - start_ms - safety_margin_ms)

    # Overrun: needs compression to avoid colliding with next speaker
    if len(audio) > source_dur:
        target_dur, speed = calculate_speech_fitting_window(
            start_ms, segment_end_ms, next_start_ms, video_dur_ms, len(audio), is_last=is_last
        )
        if abs(speed - 1.0) > 0.02 or len(audio) > target_dur:
            audio = fit_tempo_to_duration(audio, target_dur, temp_dir, cancellation_id)
        return audio[:max_room]

    # Underrun: speech fits well within actor's window
    # Keep the sentence completely continuous and unbroken.
    # If the utterance is very short in an extended dialogue window (> 2.0s slot),
    # apply gentle, subtle pacing relaxation (0.92-0.95x) so it sounds natural and unhurried.
    if len(audio) < source_dur * 0.60 and source_dur >= 2000:
        target_dur = min(source_dur, int(len(audio) / 0.94))
        if target_dur > len(audio):
            audio = fit_tempo_to_duration(audio, target_dur, temp_dir, cancellation_id, allow_slowdown=True)

    if len(audio) > max_room:
        audio = audio[:max_room]
    return audio



def _atempo_filters(speed_factor: float) -> str:
    """Build a quality-preserving FFmpeg tempo chain for any required speed."""
    speed_factor = max(0.01, float(speed_factor))
    filters = []
    while speed_factor < 0.5:
        filters.append("atempo=0.5")
        speed_factor /= 0.5
    while speed_factor > 2.0:
        filters.append("atempo=2.0")
        speed_factor /= 2.0
    filters.append(f"atempo={max(0.5, min(2.0, speed_factor)):.6f}")
    return ",".join(filters)


def _trim_tempo_rounding(audio: AudioSegment, max_duration_ms: int) -> AudioSegment:
    """Trim only FFmpeg's small resampling tail, never meaningful speech."""
    overflow_ms = len(audio) - max_duration_ms
    if overflow_ms <= 0:
        return audio
    tolerance_ms = max(20, int(max_duration_ms * 0.05))
    if overflow_ms > tolerance_ms:
        raise RuntimeError(
            f"FFmpeg tempo output is {len(audio)}ms, exceeding its "
            f"{max_duration_ms}ms slot by {overflow_ms}ms."
        )
    return audio[:max_duration_ms]


def fit_tempo_to_duration(
    audio: AudioSegment,
    target_duration_ms: int,
    temp_dir: str,
    video_id: str,
    *,
    allow_slowdown: bool = False,
) -> AudioSegment:
    """Fit speech to a timeline slot without changing pitch.

    Automatic projects only compress overruns. The optional slowdown mode is
    retained for callers that explicitly request it; normal dubbing must not
    stretch a short utterance across silence in a subtitle slot.
    """
    target_duration_ms = max(1, int(target_duration_ms))
    speed_factor = len(audio) / target_duration_ms
    if speed_factor <= 1.0 and not allow_slowdown:
        return audio
    if abs(len(audio) - target_duration_ms) <= 12:
        return audio[:target_duration_ms]

    input_handle, input_path = tempfile.mkstemp(prefix="tempo-input-", suffix=".wav", dir=temp_dir)
    os.close(input_handle)
    output_handle, output_path = tempfile.mkstemp(prefix="tempo-output-", suffix=".wav", dir=temp_dir)
    os.close(output_handle)
    try:
        exported_audio = audio.export(input_path, format="wav")
        exported_audio.close()
        process = subprocess.Popen(
            [
                _binary("ffmpeg"), "-y", "-v", "error", "-i", input_path,
                "-filter:a", _atempo_filters(speed_factor),
                "-ac", str(audio.channels), "-ar", str(audio.frame_rate), output_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        _stdout, stderr = communicate_process(
            video_id,
            process,
            label="FFmpeg speech tempo compression",
            timeout_seconds=MEDIA_PROCESS_TIMEOUT_SECONDS,
        )
        if process.returncode != 0:
            raise RuntimeError(f"FFmpeg speech tempo compression failed with exit code {process.returncode}: {stderr}")
        check_cancellation(video_id)
        fitted = AudioSegment.from_file(output_path)
        fitted = _trim_tempo_rounding(fitted, target_duration_ms)
        if allow_slowdown and len(fitted) < target_duration_ms:
            fitted += AudioSegment.silent(
                duration=target_duration_ms - len(fitted),
                frame_rate=fitted.frame_rate,
            )
        return fitted
    finally:
        for path in (input_path, output_path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def compress_to_fit(audio: AudioSegment, max_duration_ms: int, temp_dir: str, video_id: str) -> AudioSegment:
    """Tempo-compress speech without deleting its ending or changing its pitch."""
    return fit_tempo_to_duration(
        audio,
        max(1, max_duration_ms - 20),
        temp_dir,
        video_id,
    )


def _build_long_audio_timeline(
    segments_json_path: str,
    voice_parts_dir: str,
    video_path: str,
    output_wav_path: str,
    video_id: str,
    video_duration_ms: int,
    timeline_options: dict,
    progress_callback=None,
) -> None:
    """Mix a long dub in bounded windows and append PCM without decoding the full timeline."""
    output_directory = os.path.dirname(os.path.abspath(output_wav_path))
    os.makedirs(output_directory, exist_ok=True)
    total_chunks = math.ceil(video_duration_ms / _LONG_TIMELINE_CHUNK_MS)
    source_path = timeline_options.get("background_audio_path")
    try:
        source_duration_ms = max(0, int(round(get_video_duration(source_path) * 1000))) if source_path else 0
    except Exception:
        source_duration_ms = 0
    if source_path and source_duration_ms <= 0:
        source_duration_ms = max(
            0,
            int(timeline_options.get("original_audio_duration_ms") or 0),
            video_duration_ms,
        )

    log_to_video(
        video_id,
        f"Long audio timeline: mixing in {total_chunks} windows of up to "
        f"{_LONG_TIMELINE_CHUNK_MS // 60000} minutes to keep RAM bounded.",
    )

    staged_output = ""
    try:
        with tempfile.TemporaryDirectory(prefix=".audio-timeline-windows-", dir=output_directory) as work_dir:
            handle, staged_output = tempfile.mkstemp(
                prefix=".audio-timeline-", suffix=".wav", dir=output_directory,
            )
            os.close(handle)
            estimated_data_bytes = video_duration_ms * 48 * 6
            use_rf64 = estimated_data_bytes >= 0xFFFFFF00
            with open(staged_output, "w+b") as merged:
                _write_pcm_wav_header(merged, rf64=use_rf64)
                written_frames = 0
                for chunk_index, start_ms in enumerate(
                    range(0, video_duration_ms, _LONG_TIMELINE_CHUNK_MS), start=1,
                ):
                    check_cancellation(timeline_options.get("process_registry_id") or video_id)
                    end_ms = min(video_duration_ms, start_ms + _LONG_TIMELINE_CHUNK_MS)
                    chunk_path = os.path.join(work_dir, f"timeline-{chunk_index:05d}.wav")
                    chunk_options = dict(timeline_options)
                    chunk_options.update(
                        range_ms=(start_ms, end_ms),
                        timeline_duration_ms=video_duration_ms,
                        source_audio_streaming=True,
                        source_audio_duration_ms=source_duration_ms,
                        prepared_base_audio_path=None,
                        progress_callback=progress_callback,
                    )
                    percent = int(round(start_ms * 100 / max(1, video_duration_ms)))
                    detail = (
                        f"Trộn lồng tiếng: {_format_audio_time(start_ms)} / "
                        f"{_format_audio_time(video_duration_ms)} ({percent}%) "
                        f"[đoạn {chunk_index}/{total_chunks}]"
                    )
                    log_to_video(video_id, f"Mixing audio window {chunk_index}/{total_chunks}: {detail}")
                    if progress_callback:
                        progress_callback(start_ms, video_duration_ms, detail)
                    build_audio_timeline(
                        segments_json_path,
                        voice_parts_dir,
                        video_path,
                        chunk_path,
                        video_id,
                        **chunk_options,
                    )
                    with wave.open(chunk_path, "rb") as chunk:
                        params = chunk.getparams()
                        if (params.nchannels, params.sampwidth, params.framerate) != (2, 3, 48000):
                            raise RuntimeError(
                                "A mixed audio window has an unexpected format; refusing to join it."
                            )
                        while True:
                            frames = chunk.readframes(48000 * 30)
                            if not frames:
                                break
                            if len(frames) % 6:
                                raise RuntimeError("A mixed audio window ended with an incomplete PCM frame.")
                            merged.write(frames)
                            written_frames += len(frames) // 6
                    percent = int(round(end_ms * 100 / max(1, video_duration_ms)))
                    detail = (
                        f"Trộn lồng tiếng: {_format_audio_time(end_ms)} / "
                        f"{_format_audio_time(video_duration_ms)} ({percent}%) "
                        f"[đoạn {chunk_index}/{total_chunks}]"
                    )
                    if progress_callback:
                        progress_callback(end_ms, video_duration_ms, detail)
                    check_cancellation(timeline_options.get("process_registry_id") or video_id)

                expected_frames = video_duration_ms * 48
                if written_frames != expected_frames:
                    raise RuntimeError(
                        "Chunked audio windows do not exactly cover the requested video timeline "
                        f"({written_frames} of {expected_frames} audio frames)."
                    )
                data_bytes = written_frames * 6
                file_size = merged.tell()
                if use_rf64:
                    merged.seek(20)
                    merged.write(struct.pack("<QQQ", file_size - 8, data_bytes, written_frames))
                else:
                    if data_bytes >= 0xFFFFFFFF:
                        raise RuntimeError("Audio timeline exceeded classic WAV limits; RF64 was required.")
                    merged.seek(4)
                    merged.write(struct.pack("<I", file_size - 8))
                    merged.seek(40)
                    merged.write(struct.pack("<I", data_bytes))
                merged.flush()
                os.fsync(merged.fileno())

            if os.path.getsize(staged_output) <= 44:
                raise RuntimeError("Chunked audio timeline produced an empty WAV file.")
            _replace_exported_audio(staged_output, output_wav_path)
            staged_output = ""
    finally:
        if staged_output:
            try:
                os.remove(staged_output)
            except FileNotFoundError:
                pass
    log_to_video(video_id, f"Successfully exported chunked dubbed audio to: {output_wav_path}")


def _format_audio_time(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds)) // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _write_pcm_wav_header(stream, *, rf64: bool) -> None:
    """Write a seekable PCM WAV/RF64 header for the final audio track."""
    fmt = struct.pack("<HHIIHH", 1, 2, 48000, 48000 * 2 * 3, 2 * 3, 24)
    if rf64:
        stream.write(b"RF64\xff\xff\xff\xffWAVEds64")
        stream.write(struct.pack("<IQQQI", 28, 0, 0, 0, 0))
    else:
        stream.write(b"RIFF\x00\x00\x00\x00WAVE")
    stream.write(b"fmt " + struct.pack("<I", len(fmt)) + fmt)
    stream.write(b"data" + (b"\xff\xff\xff\xff" if rf64 else b"\x00\x00\x00\x00"))


def build_audio_timeline(
    segments_json_path: str,
    voice_parts_dir: str,
    video_path: str,
    output_wav_path: str,
    video_id: str,
    background_audio_path: str | None = None,
    original_video_volume: int = 60,
    original_audio_fade_in_ms: int = 0,
    original_audio_fade_out_ms: int = 0,
    original_audio_start_ms: int = 0,
    original_audio_duration_ms: int | None = None,
    background_music_path: str | None = None,
    background_music_volume: int = 30,
    tts_volume: int = 100,
    prepared_base_audio_path: str | None = None,
    process_registry_id: str | None = None,
    require_voice_parts: bool = True,
    fit_voice_to_slots: bool = False,
    require_background_audio: bool = True,
    background_music_start_ms: int = 0,
    background_music_duration_ms: int | None = None,
    background_music_source_in_ms: int = 0,
    background_music_loop: bool = True,
    background_music_fade_in_ms: int = 0,
    background_music_fade_out_ms: int = 0,
    ducking_enabled: bool = False,
    ducking_reduction_db: float = -12.0,
    ducking_attack_ms: int = 180,
    ducking_release_ms: int = 420,
    range_ms: tuple[int, int] | None = None,
    timeline_duration_ms: int | None = None,
    source_audio_streaming: bool = False,
    source_audio_duration_ms: int | None = None,
    progress_callback=None,
):
    """Compose cached audio layers without invoking a speech model.

    ``require_voice_parts`` remains true for the automatic pipeline.  Manual
    composition can set it to false to build an original/music-only mix, so
    changing levels never becomes dependent on TTS. ``fit_voice_to_slots``
    keeps legacy callers compatible, but never lengthens a short voice clip
    into source pauses; recognition must create separate spoken windows.
    """
    cancellation_id = process_registry_id or video_id
    if range_ms is None:
        log_to_video(video_id, "Starting build of the audio timeline...")
    
    # Retrieve duration to build the baseline silence track
    video_dur = (
        max(0, int(timeline_duration_ms)) / 1000.0
        if range_ms is not None and timeline_duration_ms is not None
        else get_video_duration(video_path)
    )
    if range_ms is None:
        log_to_video(video_id, f"Base video duration: {video_dur:.2f} seconds")
    
    video_dur_ms = int(timeline_duration_ms if range_ms is not None else video_dur * 1000)
    window_start, window_end = range_ms or (0, video_dur_ms)
    output_duration_ms = window_end - window_start
    if video_dur_ms <= 0:
        raise RuntimeError("Unable to determine a positive source-video duration for the audio timeline.")

    with open(segments_json_path, encoding="utf-8") as f:
        segments = json.load(f)
    if not isinstance(segments, list):
        raise RuntimeError("Audio timeline segments must be a JSON list.")
    if require_voice_parts and not segments:
        raise RuntimeError("Audio timeline requires at least one translated voice segment.")

    if (range_ms is None and video_dur_ms > _LONG_TIMELINE_CHUNK_THRESHOLD_MS
            and not prepared_base_audio_path):
        timeline_options = {
            "background_audio_path": background_audio_path,
            "original_video_volume": original_video_volume,
            "original_audio_fade_in_ms": original_audio_fade_in_ms,
            "original_audio_fade_out_ms": original_audio_fade_out_ms,
            "original_audio_start_ms": original_audio_start_ms,
            "original_audio_duration_ms": original_audio_duration_ms,
            "background_music_path": background_music_path,
            "background_music_volume": background_music_volume,
            "tts_volume": tts_volume,
            "process_registry_id": process_registry_id,
            "require_voice_parts": require_voice_parts,
            "fit_voice_to_slots": fit_voice_to_slots,
            "require_background_audio": require_background_audio,
            "background_music_start_ms": background_music_start_ms,
            "background_music_duration_ms": background_music_duration_ms,
            "background_music_source_in_ms": background_music_source_in_ms,
            "background_music_loop": background_music_loop,
            "background_music_fade_in_ms": background_music_fade_in_ms,
            "background_music_fade_out_ms": background_music_fade_out_ms,
            "ducking_enabled": ducking_enabled,
            "ducking_reduction_db": ducking_reduction_db,
            "ducking_attack_ms": ducking_attack_ms,
            "ducking_release_ms": ducking_release_ms,
        }
        return _build_long_audio_timeline(
            segments_json_path,
            voice_parts_dir,
            video_path,
            output_wav_path,
            video_id,
            video_dur_ms,
            timeline_options,
            progress_callback=progress_callback,
        )
        
    # Editor previews reuse this decoded/volume-adjusted base. Reopening a
    # timeline or changing one sentence no longer decodes the full source and
    # background music before every TTS overlay.
    base_cache_hit = bool(
        prepared_base_audio_path
        and os.path.isfile(prepared_base_audio_path)
        and os.path.getsize(prepared_base_audio_path) > 44
    )
    if range_ms is not None and source_audio_streaming:
        # Decode only this timeline window of the source bed. This branch is
        # reserved for long full-timeline mixing; editor ranges still provide
        # an already bounded source bed through the existing path below.
        base_audio = AudioSegment.silent(
            duration=output_duration_ms, frame_rate=48000,
        ).set_channels(2)
        if background_audio_path:
            if not os.path.isfile(background_audio_path) or os.path.getsize(background_audio_path) <= 0:
                if require_background_audio:
                    raise FileNotFoundError(
                        f"Required original/background audio track is missing: {background_audio_path}"
                    )
            else:
                try:
                    source_duration = max(
                        0,
                        int(source_audio_duration_ms)
                        if source_audio_duration_ms is not None
                        else int(round(get_video_duration(background_audio_path) * 1000)),
                    )
                    effective_duration = (
                        min(source_duration, max(0, int(original_audio_duration_ms)))
                        if original_audio_duration_ms is not None
                        else source_duration
                    )
                    source_offset_ms = max(0, int(original_audio_start_ms))
                    source_timeline_start = max(window_start, source_offset_ms)
                    source_timeline_end = min(window_end, source_offset_ms + effective_duration)
                    if source_timeline_end > source_timeline_start:
                        source_start = source_timeline_start - source_offset_ms
                        source_clip = _decode_window(
                            background_audio_path,
                            source_start,
                            source_timeline_end - source_timeline_start,
                            cancellation_id,
                        )
                        source_clip = _apply_volume(
                            source_clip, original_video_volume, "Source audio", video_id, log=False,
                        )
                        source_clip = _window_fades(
                            source_clip,
                            source_start,
                            effective_duration,
                            original_audio_fade_in_ms,
                            original_audio_fade_out_ms,
                        )
                        base_audio = base_audio.overlay(
                            source_clip, position=source_timeline_start - window_start,
                        )
                except Exception as exc:
                    if is_cancelled(cancellation_id):
                        raise
                    if require_background_audio:
                        raise RuntimeError(
                            f"Could not load required original/background audio track: {exc}"
                        ) from exc
                    log_to_video(video_id, "Source audio window unavailable; using a silent base layer.")
        base_audio = base_audio.set_frame_rate(48000).set_channels(2)
    elif range_ms is not None:
        # The caller supplies a source bed already bounded/mapped to this
        # sequence window. Never decode/allocate the complete source here.
        base_audio = (AudioSegment.from_file(background_audio_path)
                      if background_audio_path else AudioSegment.silent(duration=output_duration_ms))
        base_audio = _apply_volume(base_audio, original_video_volume, "Source audio", video_id, log=False)
        base_audio = base_audio.set_frame_rate(48000).set_channels(2)
        if background_music_path:
            start = max(window_start, background_music_start_ms)
            end = min(window_end, background_music_start_ms + (
                background_music_duration_ms if background_music_duration_ms is not None else video_dur_ms))
            if end > start:
                music = _decode_window(background_music_path,
                    background_music_source_in_ms + start - background_music_start_ms,
                    end - start, cancellation_id, loop=background_music_loop)
                music = _apply_volume(music, background_music_volume, "Background music", video_id, log=False)
                music = _window_fades(music, start - background_music_start_ms,
                    background_music_duration_ms or video_dur_ms,
                    background_music_fade_in_ms, background_music_fade_out_ms)
                if ducking_enabled:
                    music = _duck_music(music, [
                        (int(float(item.get("start", 0)) * 1000) - start,
                         int(float(item.get("end", 0)) * 1000) - start)
                        for item in segments if bool(item.get("_voice_enabled", True))
                    ], ducking_reduction_db, ducking_attack_ms, ducking_release_ms)
                base_audio = base_audio.overlay(music, position=start - window_start)
    elif base_cache_hit:
        base_audio = AudioSegment.from_file(prepared_base_audio_path).set_frame_rate(48000).set_channels(2)
        log_to_video(video_id, f"Reusing prepared preview audio base: {prepared_base_audio_path}")
    elif background_audio_path:
        if not os.path.exists(background_audio_path) or os.path.getsize(background_audio_path) <= 0:
            raise FileNotFoundError(f"Required original/background audio track is missing: {background_audio_path}")
        log_to_video(video_id, f"Loading background/original audio track: {background_audio_path}")
        try:
            bg_audio = AudioSegment.from_file(background_audio_path)
            bg_audio = _apply_volume(bg_audio, original_video_volume, "Source audio", video_id)
            bg_audio = _fade_clip(
                bg_audio,
                original_audio_fade_in_ms,
                original_audio_fade_out_ms,
            )
            base_audio = bg_audio.set_frame_rate(48000).set_channels(2)
            log_to_video(video_id, f"Original/background audio loaded and pre-processed. Duration: {len(base_audio)}ms")
        except Exception as exc:
            if require_background_audio:
                raise RuntimeError(f"Could not load required original/background audio track: {exc}") from exc
            base_audio = AudioSegment.silent(duration=video_dur_ms, frame_rate=48000).set_channels(2)
            log_to_video(video_id, "Source has no decodable audio; using a silent base layer.")
    else:
        base_audio = AudioSegment.silent(duration=video_dur_ms, frame_rate=48000).set_channels(2)

    if background_audio_path and not base_cache_hit and range_ms is None:
        if original_audio_duration_ms is not None:
            base_audio = base_audio[:max(0, int(original_audio_duration_ms))]
        if original_audio_start_ms > 0:
            base_audio = AudioSegment.silent(
                duration=int(original_audio_start_ms), frame_rate=48000,
            ) + base_audio

    # The final audio must always match the video. Source tracks can occasionally
    # be a few milliseconds longer than the video container reports.
    base_audio = base_audio[:output_duration_ms]
    if len(base_audio) < output_duration_ms:
        base_audio += AudioSegment.silent(
            duration=output_duration_ms - len(base_audio),
            frame_rate=48000,
        )

    # The user-selected track never enters Demucs.  AudioSegment decodes both
    # audio and video containers through FFmpeg, so MP3, MP4 and other
    # FFmpeg-supported formats follow the same safe final-mix path.
    if background_music_path and not base_cache_hit and range_ms is None:
        if not os.path.isfile(background_music_path) or os.path.getsize(background_music_path) <= 0:
            raise FileNotFoundError(f"Required background music track is missing: {background_music_path}")
        try:
            music = AudioSegment.from_file(background_music_path)
            music = music[max(0, int(background_music_source_in_ms)):]
            music_duration = max(
                0,
                min(
                    video_dur_ms - max(0, int(background_music_start_ms)),
                    int(background_music_duration_ms)
                    if background_music_duration_ms is not None
                    else video_dur_ms,
                ),
            )
            if background_music_loop:
                music = _fit_to_duration(music, music_duration)
            else:
                music = music[:music_duration]
            music = music.set_frame_rate(48000).set_channels(2)
            music = _apply_volume(music, background_music_volume, "Background music", video_id)
            music = _fade_clip(
                music,
                background_music_fade_in_ms,
                background_music_fade_out_ms,
            )
            if ducking_enabled:
                voice_ranges = [
                    (
                        max(0, int(float(item.get("start", 0) or 0) * 1000)),
                        max(0, int(float(item.get("end", 0) or 0) * 1000)),
                    )
                    for item in segments
                    if bool(item.get("_voice_enabled", True))
                ]
                # The envelope is expressed in sequence time; translate it to
                # the local music clip before applying it.
                offset = max(0, int(background_music_start_ms))
                music = _duck_music(
                    music,
                    [(start - offset, end - offset) for start, end in voice_ranges],
                    ducking_reduction_db,
                    ducking_attack_ms,
                    ducking_release_ms,
                )
            base_audio = base_audio.overlay(
                music,
                position=max(0, int(background_music_start_ms)),
            )
            log_to_video(video_id, f"Mixed background music: {background_music_path}")
        except Exception as exc:
            raise RuntimeError(f"Could not load background music: {exc}") from exc

    if prepared_base_audio_path and not base_cache_hit:
        cache_directory = os.path.dirname(os.path.abspath(prepared_base_audio_path))
        os.makedirs(cache_directory, exist_ok=True)
        cache_handle, staged_cache = tempfile.mkstemp(
            prefix=".audio-base-",
            suffix=".wav",
            dir=cache_directory,
        )
        os.close(cache_handle)
        try:
            exported_cache = base_audio[:video_dur_ms].export(
                staged_cache,
                format="wav",
                parameters=["-ac", "2", "-ar", "48000", "-c:a", "pcm_s24le"],
            )
            exported_cache.close()
            check_cancellation(cancellation_id)
            if os.path.getsize(staged_cache) <= 44:
                raise RuntimeError("Prepared audio-base cache is empty.")
            _replace_exported_audio(staged_cache, prepared_base_audio_path)
            staged_cache = ""
        finally:
            if staged_cache:
                try:
                    os.remove(staged_cache)
                except FileNotFoundError:
                    pass
    
    total = len(segments)
    last_reported_mix_ms = window_start
    for idx, seg in enumerate(segments, 1):
        if not bool(seg.get("_voice_enabled", True)):
            continue
        part_filename = f"voice_{idx:04d}.mp3"
        part_path = os.path.join(voice_parts_dir, part_filename)
        
        if not os.path.exists(part_path) or os.path.getsize(part_path) == 0:
            if not require_voice_parts:
                continue
            raise RuntimeError(f"Missing or empty generated voice segment {idx}: {part_filename}")
            
        start_ms = max(0, int(seg["start"] * 1000))
        
        if start_ms >= video_dur_ms:
            log_to_video(video_id, f"[{idx}/{total}] Skipping TTS: its slot starts after the video ends.")
            continue
        
        # Keep the dubbed line inside the source actor's spoken window. The
        # next start remains a hard boundary for malformed/overlapping timing.
        segment_end_ms = int(float(seg.get("end", seg["start"])) * 1000)
        if idx < total:
            next_start_ms = int(segments[idx]["start"] * 1000)
        else:
            next_start_ms = video_dur_ms
            
        # Keep each line anchored to its original timestamp. A long translation
        # must not push every following line later and create a cascade of cuts.
        slot_end_ms = _segment_slot_end_ms(
            start_ms,
            segment_end_ms,
            next_start_ms,
            video_dur_ms,
            is_last=idx == total,
        )
        available_dur = slot_end_ms - start_ms
        if available_dur <= 0 or slot_end_ms <= window_start or start_ms >= window_end:
            log_to_video(video_id, f"[{idx}/{total}] Skipping TTS: no available timeline slot.")
            continue
        
        try:
            check_cancellation(cancellation_id)
            tts_segment = AudioSegment.from_file(part_path)
            clip_volume = int(seg.get("_voice_volume_percent", tts_volume))
            tts_segment = _apply_volume(tts_segment, clip_volume, "TTS", video_id)
            
            # Fit speech using human-dialogue cadence with pause expansion & pitch-preserving tempo
            tts_segment = fit_speech_to_natural_cadence(
                tts_segment,
                start_ms,
                segment_end_ms,
                next_start_ms,
                video_dur_ms,
                voice_parts_dir,
                cancellation_id,
                is_last=idx == total,
            )

            tts_segment = _fade_clip(
                tts_segment,
                int(seg.get("_voice_fade_in_ms", 0) or 0),
                int(seg.get("_voice_fade_out_ms", 0) or 0),
            )
            offset = max(0, window_start - start_ms)
            tts_segment = tts_segment[offset:max(offset, window_end - start_ms)]
            base_audio = base_audio.overlay(tts_segment, position=max(0, start_ms - window_start))
            if progress_callback and source_audio_streaming:
                current_ms = min(window_end, max(window_start, slot_end_ms))
                if current_ms - last_reported_mix_ms >= 60_000:
                    last_reported_mix_ms = current_ms
                    percent = int(round(current_ms * 100 / max(1, video_dur_ms)))
                    progress_callback(
                        current_ms,
                        video_dur_ms,
                        f"Trộn lồng tiếng: {_format_audio_time(current_ms)} / "
                        f"{_format_audio_time(video_dur_ms)} ({percent}%)",
                    )
        except Exception as exc:
            raise RuntimeError(f"Failed to overlay required voice segment {idx} ({part_filename}): {exc}") from exc

    # Publish the stereo production mix atomically for safe resume.
    check_cancellation(cancellation_id)
    output_directory = os.path.dirname(os.path.abspath(output_wav_path))
    os.makedirs(output_directory, exist_ok=True)
    handle, temporary_path = tempfile.mkstemp(
        prefix=".audio-timeline-",
        suffix=".wav",
        dir=output_directory,
    )
    os.close(handle)
    try:
        exported_timeline = base_audio[:output_duration_ms].export(
            temporary_path,
            format="wav",
            parameters=["-ac", "2", "-ar", "48000", "-c:a", "pcm_s24le"],
        )
        exported_timeline.close()
        check_cancellation(cancellation_id)
        if os.path.getsize(temporary_path) <= 44:
            raise RuntimeError("Audio timeline export produced an empty WAV file.")
        _replace_exported_audio(temporary_path, output_wav_path)
        temporary_path = ""
    finally:
        if temporary_path:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass
    log_to_video(video_id, f"Successfully exported dubbed audio to: {output_wav_path}")
