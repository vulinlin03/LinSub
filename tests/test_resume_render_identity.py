"""Regenerating identical captions must not invalidate a finished render."""

import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from linsub.pipeline import subtitle
from linsub.schemas.video import VideoConfig
from linsub.services import video_export


@pytest.mark.parametrize("preserve", [False, True])
def test_identical_caption_recompile_keeps_finished_render_revision(tmp_path, monkeypatch, preserve):
    monkeypatch.setattr(subtitle, "log_to_video", Mock())
    transcript, captions = tmp_path / "segments.json", tmp_path / "captions.srt"
    transcript.write_text(json.dumps([{"start": 1, "end": 3, "text": "Chào bạn"}]), encoding="utf-8")
    subtitle.generate_srt(str(transcript), str(captions), 32, "fixture", preserve_segment_boundaries=preserve)
    os.utime(captions, ns=(1700000000000000000, 1700000000000000000))
    before = captions.stat().st_mtime_ns
    video = SimpleNamespace(**VideoConfig(project_type="single").model_dump(),
                            files={"srt_output": str(captions)}, checkpoints={"render": "finished"})
    revision = video_export.render_revision(video)
    subtitle.generate_srt(str(transcript), str(captions), 32, "fixture", preserve_segment_boundaries=preserve)
    assert captions.stat().st_mtime_ns == before
    assert video_export.render_revision(video) == revision
    assert not list(tmp_path.glob(".subtitles-*"))


def test_actual_caption_change_replaces_file_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(subtitle, "log_to_video", Mock())
    transcript, captions = tmp_path / "segments.json", tmp_path / "captions.srt"
    transcript.write_text('[{"start":1,"end":3,"text":"Old"}]', encoding="utf-8")
    subtitle.generate_srt(str(transcript), str(captions), 32, "fixture")
    transcript.write_text('[{"start":1,"end":4,"text":"New"}]', encoding="utf-8")
    subtitle.generate_srt(str(transcript), str(captions), 32, "fixture")
    cues = subtitle.parse_srt_to_segments(str(captions))
    assert cues == [{"start": 1.0, "end": 4.0, "text": "New"}]
    assert not list(tmp_path.glob(".subtitles-*"))
