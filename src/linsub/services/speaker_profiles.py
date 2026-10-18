"""Speaker profile extraction and mapping for multiple-speaker dubbing."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from linsub.core.vieneu_voices import speaker_voice


def classify_gender(timbre_style: str = "", pitch_hz: float = 0.0, default_voice: str = "") -> str:
    """Classify speaker strictly into 'male' or 'female' based on timbre style, pitch, or voice preset."""
    raw = str(timbre_style or "").lower()
    if "female" in raw:
        return "female"
    if "male" in raw:
        return "male"
    voice_raw = str(default_voice or "").lower()
    if "female" in voice_raw or "truc ly" in voice_raw or "trúc ly" in voice_raw:
        return "female"
    if "male" in voice_raw or "hai dang" in voice_raw or "hải đăng" in voice_raw:
        return "male"
    if pitch_hz >= 200.0:
        return "female"
    return "male"


def load_video_speakers(
    video: Any,
    provider: str = "vieneu",
    overrides: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return structured speaker profiles with turn counts, pitches, and assigned voices."""
    if not video:
        return []
    if overrides is None:
        overrides = dict((getattr(video, "files", {}) or {}).get("speaker_voice_overrides") or {})

    files = dict(getattr(video, "files", {}) or {})
    source_segments_path = str(files.get("source_segments") or "")
    video_input = str(files.get("video_input") or "")
    video_root = os.path.dirname(os.path.dirname(video_input)) if video_input else ""
    if not source_segments_path and video_root:
        candidate = os.path.join(video_root, "temp", "source_segments.json")
        if os.path.isfile(candidate):
            source_segments_path = candidate

    source_audio_path = str(files.get("speech_audio") or "")
    if not source_audio_path and video_root:
        candidate = os.path.join(video_root, "temp", "audio.wav")
        if os.path.isfile(candidate):
            source_audio_path = candidate
    if not source_audio_path and video_input:
        source_audio_path = video_input

    identified: list[dict[str, Any]] | None = None

    # Search for cached speaker-map-*.json near audio, input, or video temp/root
    search_dirs = []
    if source_audio_path:
        search_dirs.append(Path(source_audio_path).parent)
    if video_input:
        search_dirs.append(Path(video_input).parent)
    if video_root:
        search_dirs.append(Path(video_root) / "temp")
        search_dirs.append(Path(video_root))

    seen_dirs = set()
    for directory in search_dirs:
        resolved = str(directory.resolve()) if directory.is_dir() else ""
        if not resolved or resolved in seen_dirs:
            continue
        seen_dirs.add(resolved)
        for map_file in directory.glob("speaker-map-*.json"):
            try:
                data = json.loads(map_file.read_text(encoding="utf-8"))
                if isinstance(data, list) and data:
                    identified = data
                    break
            except Exception:
                pass
        if identified:
            break

    if not identified:
        return []

    turns = [turn for source in identified for turn in (source.get("speaker_turns") or [source])]
    speakers: dict[str, dict[str, Any]] = {}

    for turn in turns:
        sid = str(turn.get("speaker_id") or "")
        if not sid:
            continue
        if sid not in speakers:
            raw_timbre = str(turn.get("speaker_timbre_style") or "")
            pitch = round(float(turn.get("speaker_pitch_hz") or 0.0), 1)
            gender = classify_gender(raw_timbre, pitch)

            raw_voice = str(turn.get("speaker_voice") or "")
            if provider.startswith("vieneu"):
                default_voice = speaker_voice(raw_voice)
                if gender == "female" and default_voice == "vieneu:Hải Đăng":
                    default_voice = "vieneu:Trúc Ly"
                elif gender == "male" and default_voice == "vieneu:Trúc Ly":
                    default_voice = "vieneu:Hải Đăng"
            else:
                if gender == "female":
                    default_voice = raw_voice if raw_voice.startswith("omnivoice:female") else "omnivoice:female"
                else:
                    default_voice = raw_voice if raw_voice.startswith("omnivoice:male") else "omnivoice:male"

            if gender == "male" and overrides.get("male"):
                assigned_voice = str(overrides["male"])
            elif gender == "female" and overrides.get("female"):
                assigned_voice = str(overrides["female"])
            else:
                assigned_voice = str(overrides.get(sid) or default_voice)
            if provider.startswith("vieneu") and not assigned_voice.startswith("vieneu:"):
                assigned_voice = speaker_voice(assigned_voice)
            elif provider.startswith("omnivoice") and not assigned_voice.startswith("omnivoice:"):
                assigned_voice = default_voice

            speakers[sid] = {
                "speakerId": sid,
                "gender": gender,
                "pitchHz": pitch,
                "turnCount": 0,
                "sampleText": str(turn.get("text") or "").strip(),
                "defaultVoice": default_voice,
                "assignedVoice": assigned_voice,
            }
        speakers[sid]["turnCount"] += 1
        if not speakers[sid]["sampleText"] and str(turn.get("text") or "").strip():
            speakers[sid]["sampleText"] = str(turn.get("text") or "").strip()

    def sort_order(item: dict[str, Any]) -> tuple[int, int]:
        sid = str(item["speakerId"])
        match = re.search(r"\d+", sid)
        num = int(match.group()) if match else 999
        return (1 if sid.startswith("unresolved") else 0, num)

    sorted_speakers = sorted(speakers.values(), key=sort_order)
    for idx, item in enumerate(sorted_speakers, 1):
        if item["speakerId"].startswith("unresolved"):
            item["label"] = f"Chưa rõ ({idx})"
        else:
            item["label"] = f"Người nói {idx}"

        gender_str = "Nam" if item["gender"] == "male" else "Nữ"
        pitch_str = f" · F0 ~ {int(item['pitchHz'])}Hz" if item["pitchHz"] > 0 else ""
        item["description"] = f"{gender_str}{pitch_str} · {item['turnCount']} câu"

    return sorted_speakers


def identify_video_speakers(video: Any, progress_callback=None) -> list[dict[str, Any]]:
    """Execute speaker identification on source segments and cache the result."""
    if not video:
        return []
    files = dict(getattr(video, "files", {}) or {})
    source_segments_path = str(files.get("source_segments") or "")
    video_input = str(files.get("video_input") or "")
    video_root = os.path.dirname(os.path.dirname(video_input)) if video_input else ""
    if not source_segments_path and video_root:
        candidate = os.path.join(video_root, "temp", "source_segments.json")
        if os.path.isfile(candidate):
            source_segments_path = candidate

    source_audio_path = str(files.get("speech_audio") or "")
    if not source_audio_path and video_root:
        candidate = os.path.join(video_root, "temp", "audio.wav")
        if os.path.isfile(candidate):
            source_audio_path = candidate
    if not source_audio_path and video_input:
        source_audio_path = video_input

    if not source_segments_path or not os.path.isfile(source_segments_path):
        raise RuntimeError("Chưa có phụ đề nguồn để nhận diện người nói. Hãy tạo hoặc dịch phụ đề trước.")
    if not source_audio_path or not os.path.isfile(source_audio_path):
        raise RuntimeError("Không tìm thấy tệp audio gốc để phân tích người nói.")

    with open(source_segments_path, "r", encoding="utf-8") as f:
        source_segments = json.load(f)
    if not isinstance(source_segments, list) or not source_segments:
        raise RuntimeError("Phụ đề nguồn trống hoặc không hợp lệ.")

    from linsub.pipeline.speaker_identity import prepare_speakers
    prepare_speakers(source_audio_path, source_segments, video.video_id, progress=progress_callback)
    return load_video_speakers(video, getattr(video, "tts_provider", "vieneu"))
