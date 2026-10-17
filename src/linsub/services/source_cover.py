"""Lightweight source-only coverage previews, with no OCR/model/render work."""

import hashlib
import json
from pathlib import Path

from linsub.services.ocr_regions import normalize_region


def source_key(video) -> str:
    path = str((video.files or {}).get("video_input") or "")
    try:
        if not Path(path).is_file():
            return ""
        state = Path(path).stat()
        identity = [path, state.st_size, state.st_mtime_ns]
    except OSError:
        return ""
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def detected_region(video, video_dir: str) -> dict:
    cache = (video.files or {}).get("ocr_region") or str(Path(video_dir) / "temp/original_subtitle_region.json")
    try:
        # This is a tiny geometry record, never a video or a model cache.
        if Path(cache).stat().st_size > 64 * 1024:
            return {}
        payload = json.loads(Path(cache).read_text(encoding="utf-8"))
        return normalize_region(payload.get("region", payload)) if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}
