"""Input-bound ASR phase checkpoints, never exposed as final subtitles."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from linsub.utils.atomic_file import atomic_json

STAGES = ("transcribed", "recognized", "aligned", "languages", "complete")


def manual_progress_path(video_id: str) -> Path:
    from linsub.services.video_store import get_video_dir

    return Path(get_video_dir(video_id)) / "temp" / "recognition-progress.json"


def clear_manual_progress(video_id: str) -> None:
    manual_progress_path(video_id).unlink(missing_ok=True)


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode("ascii")).hexdigest()


def _valid_segments(segments, *, complete: bool = False) -> bool:
    if not isinstance(segments, list) or not segments:
        return False
    try:
        return all(isinstance(row, dict) and isinstance(row.get("text"), str) and row["text"].strip()
                   and math.isfinite(float(row["start"])) and math.isfinite(float(row["end"]))
                   and (not complete or 0 <= float(row["start"]) < float(row["end"])) for row in segments)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


class RecognitionProgress:
    def __init__(self, path: str | Path, audio_path: str, *, model: str, language: str, key: str):
        from linsub.pipeline.timing_contract import TIMING_SOURCE

        self.path = Path(path)
        audio = Path(audio_path).resolve()
        state = audio.stat()
        self.signature = _digest({"audio": str(audio), "size": state.st_size, "mtime": state.st_mtime_ns,
                                  "model": model, "language": language, "key": key,
                                  "timing": TIMING_SOURCE, "policy": "recognition-phases-v1"})
        self.stage = ""
        self.segments = []
        self.language = ""
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            payload = stored["payload"]
            if (stored.get("schema") == 1 and stored.get("signature") == self.signature
                    and stored.get("digest") == _digest(payload) and payload["stage"] in STAGES
                    and isinstance(payload["language"], str)
                    and _valid_segments(payload["segments"], complete=payload["stage"] == "complete")):
                self.stage, self.segments, self.language = payload["stage"], payload["segments"], payload["language"]
        except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError):
            pass

    def reached(self, stage: str) -> bool:
        return bool(self.stage) and STAGES.index(self.stage) >= STAGES.index(stage)

    def save(self, stage: str, segments: list, language: str | None) -> None:
        if stage not in STAGES or not _valid_segments(segments, complete=stage == "complete"):
            raise ValueError("Invalid recognition checkpoint.")
        # Alignment libraries can return NumPy scalar word scores/times.
        payload = json.loads(json.dumps({"stage": stage, "segments": segments, "language": language or ""},
                                       default=lambda value: value.item(), ensure_ascii=True))
        atomic_json(self.path, {"schema": 1, "signature": self.signature,
                               "payload": payload, "digest": _digest(payload)}, ensure_ascii=True)
        self.stage, self.segments, self.language = stage, payload["segments"], payload["language"]
