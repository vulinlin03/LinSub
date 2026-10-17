"""Pydub integration: media helpers must never allocate a Windows console."""
from pydub import AudioSegment
from pydub import audio_segment, utils
from linsub.utils.media_subprocess import _MediaSubprocess, _hidden_popen
from linsub.utils.ffmpeg import _binary


# Adapt only Pydub's module-local references. Do not monkeypatch the standard
# subprocess module: user-requested installers and external apps stay visible.
audio_segment.subprocess = _MediaSubprocess()
utils.Popen = _hidden_popen

# Ensure ffmpeg binary is resolved via LinSub's locator
AudioSegment.converter = _binary("ffmpeg")

_orig_mediainfo_json = utils.mediainfo_json
_orig_mediainfo = getattr(utils, "mediainfo", None)


def _safe_mediainfo_json(filepath, read_ahead_limit=-1):
    try:
        return _orig_mediainfo_json(filepath, read_ahead_limit=read_ahead_limit)
    except (FileNotFoundError, OSError):
        return None


def _safe_mediainfo(filepath):
    if _orig_mediainfo is None:
        return None
    try:
        return _orig_mediainfo(filepath)
    except (FileNotFoundError, OSError):
        return None


utils.mediainfo_json = _safe_mediainfo_json
audio_segment.mediainfo_json = _safe_mediainfo_json
if _orig_mediainfo is not None:
    utils.mediainfo = _safe_mediainfo
    audio_segment.mediainfo = _safe_mediainfo

__all__ = ["AudioSegment"]

