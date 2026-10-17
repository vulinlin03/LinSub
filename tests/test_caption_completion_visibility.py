"""Karaoke presentation ends with its narration; source timestamps stay intact."""
from datetime import timedelta
import re
import subprocess

import pytest
import srt
from PySide6.QtGui import QGuiApplication

from linsub.desktop.subtitle_overlay_renderer import SubtitleOverlayRenderer, export_events, timestamp
from linsub.pipeline import render
from linsub.schemas.video import SubtitleStyle
from linsub.utils.ffmpeg import _binary

_APP = None


@pytest.mark.parametrize("layout", [None, render.SubtitleRegionLayout(0, 0, 140, 80)])
def test_ass_last_phrase_ends_at_completion_not_original_slot(tmp_path, layout):
    source, target = tmp_path / "captions.srt", tmp_path / "captions.ass"
    original = srt.compose([srt.Subtitle(1, timedelta(seconds=2), timedelta(seconds=9),
                                       "Xin chào bạn hôm nay là một ngày đẹp trời")])
    source.write_text(original, encoding="utf-8")
    render._write_positioned_ass(str(source), str(target), SubtitleStyle(font_size=30), 640, 360,
                                 layout, fixed_font_size=True, speech_durations={1: 1.5})
    events = [line.split(",", 9) for line in target.read_text(encoding="utf-8-sig").splitlines()
              if line.startswith("Dialogue:")]
    assert timestamp(events[0][1]) == 2
    assert timestamp(events[-1][2]) == 3.5
    assert sum(int(n) for event in events for n in re.findall(r"\\kf(\d+)", event[9])) == 150
    assert all(left[2] == right[1] for left, right in zip(events, events[1:]))
    assert source.read_text(encoding="utf-8") == original


def test_preview_hides_at_completion_and_returns_when_seeking_back(tmp_path):
    global _APP
    _APP = QGuiApplication.instance() or QGuiApplication([])
    segment = dict(segment_id="one", start=2, end=9, _speech_end=3.5, text="Xin chào bạn")
    layout = dict(outputWidth=640, outputHeight=360, layoutWidth=500, layoutHeight=80,
                  fontSize=30, outline=2, positionXPercent=50, positionYPercent=80)
    header, events = export_events([segment], layout, True, tmp_path)
    controller = SubtitleOverlayRenderer()
    try:
        controller._header, controller._events, controller._layout = header, events, layout
        for event in events:
            controller._cache[controller._event_key(event)] = {"normal": "normal.png", "karaokeLines": []}
        controller.seek(3.49)
        assert controller.frame["text"]
        controller.seek(3.5)
        assert controller.frame == {}
        controller.seek(8.9)
        assert controller.frame == {}
        controller.seek(2.5)
        assert controller.frame["text"]
        assert segment["end"] == 9
    finally:
        controller.close()
        _APP.processEvents()


def test_without_narration_caption_keeps_its_window_including_very_short_cues(tmp_path):
    source, target = tmp_path / "captions.srt", tmp_path / "captions.ass"
    source.write_text("1\n00:00:00,000 --> 00:00:00,080\nHi\n", encoding="utf-8")
    render._write_positioned_ass(str(source), str(target), SubtitleStyle(), 640, 360)
    event = next(line for line in target.read_text(encoding="utf-8-sig").splitlines() if line.startswith("Dialogue:"))
    assert timestamp(event.split(",", 3)[2]) == .08


@pytest.mark.parametrize("preview", [render.subtitle_preview_frame, render.subtitle_preview_frame_for_layout])
def test_fallback_preview_does_not_hold_last_gold_frame_outside_window(preview):
    args = ("Xin chào", 2, 3.5)
    extra = (30, 72, 640) if preview is render.subtitle_preview_frame else (30, 500, 2)
    assert preview(*args, 3.49, *extra)["text"]
    assert preview(*args, 3.5, *extra)["text"] == ""
    assert preview(*args, 9, *extra)["text"] == ""
    assert preview(*args, 1, *extra)["text"] == ""


def test_export_pixels_disappear_after_narration_completes(tmp_path):
    source, target = tmp_path / "captions.srt", tmp_path / "captions.ass"
    source.write_text("1\n00:00:00,000 --> 00:00:04,000\nXin chào\n", encoding="utf-8")
    render._write_positioned_ass(str(source), str(target), SubtitleStyle(font_size=32), 384, 216,
                                 speech_durations={1: 1})
    fonts = str(render._karaoke_font_directory()).replace("\\", "/").replace(":", "\\:")
    result = subprocess.run([_binary("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
        "color=c=black:s=384x216:r=10:d=2", "-vf", f"ass=captions.ass:fontsdir='{fonts}'",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], cwd=tmp_path, capture_output=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    frame_size = 384 * 216 * 3
    assert max(result.stdout[8 * frame_size:9 * frame_size]) > 100
    assert max(result.stdout[12 * frame_size:13 * frame_size]) == 0
