"""Controller for multi-video merging and timeline sequencing inspired by CapCut."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl
from PySide6.QtWidgets import QFileDialog, QApplication

from linsub.config import CACHE_DIR
from linsub.utils.ffmpeg import _binary, get_video_duration, get_video_dimensions


class VideoMergeController(QObject):
    """Manages the queue of video clips, timeline synchronization, and ffmpeg concatenation."""

    clipsChanged = Signal()
    activeClipIndexChanged = Signal()
    busyChanged = Signal()
    progressChanged = Signal(float)
    progressMessageChanged = Signal(str)
    mergeCompleted = Signal(str)
    errorOccurred = Signal(str)
    sortAscendingChanged = Signal(bool)

    def __init__(self, host=None, parent=None):
        super().__init__(parent)
        self._host = host
        self._clips: list[dict[str, Any]] = []
        self._active_clip_index: int = -1
        self._is_busy: bool = False
        self._progress: float = 0.0
        self._progress_message: str = ""
        self._aspect_ratio: str = "auto"
        self._merge_mode: str = "fast"
        self._sort_ascending: bool = True
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._thumb_dir = Path(CACHE_DIR) / "merge_thumbnails"
        self._thumb_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # Properties exposed to QML
    # -------------------------------------------------------------------------

    @Property("QVariantList", notify=clipsChanged)
    def clips(self) -> list[dict[str, Any]]:
        return self._clips

    @Property(int, notify=clipsChanged)
    def clipCount(self) -> int:
        return len(self._clips)

    @Property(int, notify=clipsChanged)
    def totalDurationMs(self) -> int:
        if not self._clips:
            return 0
        return self._clips[-1]["endMs"]

    @Property(str, notify=clipsChanged)
    def totalDurationFormatted(self) -> str:
        total_seconds = self.totalDurationMs / 1000.0
        return self._format_time(total_seconds)

    @Property(int, notify=activeClipIndexChanged)
    def activeClipIndex(self) -> int:
        if not self._clips:
            return -1
        return max(0, min(self._active_clip_index, len(self._clips) - 1))

    @activeClipIndex.setter
    def activeClipIndex(self, val: int) -> None:
        idx = max(0, min(int(val), len(self._clips) - 1)) if self._clips else -1
        if self._active_clip_index != idx:
            self._active_clip_index = idx
            self.activeClipIndexChanged.emit()

    @Property(str, notify=activeClipIndexChanged)
    def currentPreviewSource(self) -> str:
        idx = self.activeClipIndex
        if 0 <= idx < len(self._clips):
            path = self._clips[idx]["path"]
            return QUrl.fromLocalFile(os.path.abspath(path)).toString()
        return ""

    @Property(bool, notify=busyChanged)
    def isBusy(self) -> bool:
        return self._is_busy

    @Property(float, notify=progressChanged)
    def progress(self) -> float:
        return self._progress

    @Property(str, notify=progressMessageChanged)
    def progressMessage(self) -> str:
        return self._progress_message

    @Property(str)
    def aspectRatio(self) -> str:
        return self._aspect_ratio

    @Property(str)
    def mergeMode(self) -> str:
        return self._merge_mode

    @Property(bool, notify=sortAscendingChanged)
    def sortAscending(self) -> bool:
        return self._sort_ascending

    # -------------------------------------------------------------------------
    # Slots
    # -------------------------------------------------------------------------

    @Slot(str)
    def setAspectRatio(self, ratio: str) -> None:
        val = str(ratio or "auto").strip().lower()
        if val in {"auto", "16:9", "9:16", "1:1"}:
            self._aspect_ratio = val

    @Slot(str)
    def setMergeMode(self, mode: str) -> None:
        val = str(mode or "fast").strip().lower()
        if val in {"fast", "standard"}:
            self._merge_mode = val

    @Slot()
    def chooseVideos(self) -> None:
        """Open a native Windows multi-file chooser."""
        files, _ = QFileDialog.getOpenFileNames(
            None,
            "Chọn các video để ghép",
            "",
            "Video files (*.mp4 *.mkv *.mov *.avi *.webm *.m4v);;Tất cả tệp (*.*)"
        )
        if files:
            self.addVideos(files)

    @Slot("QVariantList")
    def addVideos(self, paths: list[str]) -> None:
        """Add one or more video file paths into the sequence."""
        if not paths:
            return
        threading.Thread(target=self._process_and_add_videos, args=(list(paths),), daemon=True).start()

    @staticmethod
    def _natural_sort_key(item: Any) -> list[Any]:
        text_val = item.get("name", "") if isinstance(item, dict) else os.path.basename(str(item or ""))
        # Normalize unicode NFKC: turns circled numbers (①..⑳, ㉑), fullwidth (１..９), parenthesized (⑴) into normal digits
        norm = unicodedata.normalize("NFKC", str(text_val or ""))

        # Convert Chinese numbers like 第1集, 第一集, 第二十集, etc.
        cn_map = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        def _parse_cn(match: re.Match) -> str:
            raw = match.group(1)
            if "十" in raw:
                parts = raw.split("十")
                left = cn_map.get(parts[0], 1) if parts[0] else 1
                right = cn_map.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
                return f"第{left * 10 + right}集"
            if raw in cn_map:
                return f"第{cn_map[raw]}集"
            return match.group(0)

        norm = re.sub(r"第([零一二两三四五六七八九十]+)集", _parse_cn, norm)

        # Split on digit sequences with type-safe tags (0 for digits, 1 for text)
        parts = re.split(r"(\d+)", norm)
        key: list[Any] = []
        for part in parts:
            if not part:
                continue
            if part.isdigit():
                key.append((0, int(part), ""))
            else:
                key.append((1, 0, part.lower()))
        return key

    def _process_and_add_videos(self, paths: list[str]) -> None:
        # Preserve the exact order of user selection (whichever file selected first is placed first)
        new_items = []
        for raw in paths:
            clean_path = str(raw or "").strip()
            if clean_path.startswith("file:///"):
                clean_path = clean_path.removeprefix("file:///")
                if os.name == "nt" and clean_path.startswith("/") and len(clean_path) > 2 and clean_path[2] == ":":
                    clean_path = clean_path[1:]
            clean_path = os.path.normpath(clean_path)
            if not os.path.isfile(clean_path):
                continue

            duration = get_video_duration(clean_path)
            if duration <= 0.05:
                continue
            width, height = get_video_dimensions(clean_path)
            clip_id = uuid.uuid4().hex[:8]
            thumb_file = self._thumb_dir / f"thumb_{clip_id}.jpg"
            self._extract_thumbnail(clean_path, str(thumb_file), min(1.0, duration / 2.0))
            thumb_url = ("file:///" + str(thumb_file).replace("\\", "/")) if thumb_file.is_file() else ""

            name = os.path.basename(clean_path)
            new_items.append({
                "id": clip_id,
                "path": clean_path,
                "name": name,
                "duration": duration,
                "durationMs": int(duration * 1000),
                "durationFormatted": self._format_time(duration),
                "width": width,
                "height": height,
                "thumbnail": thumb_url,
            })

        if not new_items:
            return

        self._clips.extend(new_items)
        self._recalculate_timings()
        if self._active_clip_index < 0 and self._clips:
            self._active_clip_index = 0
        self.clipsChanged.emit()
        self.activeClipIndexChanged.emit()

    @Slot()
    @Slot(bool)
    def sortClipsByName(self, reverse: bool = False) -> None:
        """Sort all loaded clips naturally by name/number (e.g. 1, 2, 10, 20 or reverse)."""
        if len(self._clips) < 2:
            return
        active_clip_id = self._clips[self._active_clip_index]["id"] if 0 <= self._active_clip_index < len(self._clips) else None
        self._clips.sort(key=self._natural_sort_key, reverse=reverse)
        self._sort_ascending = not reverse
        self._recalculate_timings()
        if active_clip_id:
            for idx, c in enumerate(self._clips):
                if c["id"] == active_clip_id:
                    self._active_clip_index = idx
                    break
        self.clipsChanged.emit()
        self.activeClipIndexChanged.emit()
        self.sortAscendingChanged.emit(self._sort_ascending)

    @Slot()
    def toggleSortByName(self) -> None:
        """Toggle sorting between ascending (1->9) and descending (9->1)."""
        if len(self._clips) < 2:
            return
        keys = [self._natural_sort_key(c) for c in self._clips]
        is_sorted_asc = all(keys[i] <= keys[i + 1] for i in range(len(keys) - 1))
        self.sortClipsByName(reverse=is_sorted_asc)

    @Slot()
    def reverseClips(self) -> None:
        """Reverse the sequence of clips."""
        if len(self._clips) < 2:
            return
        active_clip_id = self._clips[self._active_clip_index]["id"] if 0 <= self._active_clip_index < len(self._clips) else None
        self._clips.reverse()
        self._sort_ascending = not self._sort_ascending
        self._recalculate_timings()
        if active_clip_id:
            for idx, c in enumerate(self._clips):
                if c["id"] == active_clip_id:
                    self._active_clip_index = idx
                    break
        self.clipsChanged.emit()
        self.activeClipIndexChanged.emit()
        self.sortAscendingChanged.emit(self._sort_ascending)

    @Slot(int)
    def removeClip(self, index: int) -> None:
        if 0 <= index < len(self._clips):
            self._clips.pop(index)
            self._recalculate_timings()
            if self._active_clip_index >= len(self._clips):
                self._active_clip_index = max(-1, len(self._clips) - 1)
            self.clipsChanged.emit()
            self.activeClipIndexChanged.emit()

    @Slot(int, int)
    def moveClip(self, from_index: int, to_index: int) -> None:
        if 0 <= from_index < len(self._clips) and 0 <= to_index < len(self._clips) and from_index != to_index:
            active_clip_id = self._clips[self._active_clip_index]["id"] if 0 <= self._active_clip_index < len(self._clips) else None
            item = self._clips.pop(from_index)
            self._clips.insert(to_index, item)
            self._recalculate_timings()
            if active_clip_id:
                for idx, c in enumerate(self._clips):
                    if c["id"] == active_clip_id:
                        self._active_clip_index = idx
                        break
            self.clipsChanged.emit()
            self.activeClipIndexChanged.emit()

    @Slot()
    def clearClips(self) -> None:
        self._clips.clear()
        self._active_clip_index = -1
        self.clipsChanged.emit()
        self.activeClipIndexChanged.emit()

    @Slot(int, result="QVariantMap")
    def resolvePosition(self, position_ms: int) -> dict[str, Any]:
        """Given a total timeline position, resolve the active clip and local offset."""
        if not self._clips:
            return {"clipIndex": -1, "localMs": 0, "path": ""}
        pos = max(0, min(int(position_ms), self.totalDurationMs))
        for idx, clip in enumerate(self._clips):
            # Strict less-than on endMs ensures boundaries fall into the new clip
            if clip.get("startMs", 0) <= pos < clip.get("endMs", 0):
                return {
                    "clipIndex": idx,
                    "localMs": pos - clip.get("startMs", 0),
                    "path": clip.get("path", "")
                }
        last_clip = self._clips[-1]
        return {
            "clipIndex": len(self._clips) - 1,
            "localMs": max(0, pos - last_clip.get("startMs", 0)),
            "path": last_clip.get("path", "")
        }

    @Slot(str)
    def startMerge(self, output_path: str = "") -> None:
        """Start the concatenation workflow."""
        if self._is_busy or len(self._clips) < 2:
            if len(self._clips) < 2:
                self.errorOccurred.emit("Hãy thêm ít nhất 2 video để tiến hành ghép.")
            return

        target_file = str(output_path or "").strip()
        if not target_file:
            target_file, _ = QFileDialog.getSaveFileName(
                None, "Lưu video đã ghép", "video_ghep.mp4", "MP4 Video (*.mp4)"
            )
            if not target_file:
                return

        self._is_busy = True
        self._progress = 0.0
        self._progress_message = "Đang chuẩn bị ghép video…"
        self.busyChanged.emit()
        self.progressChanged.emit(self._progress)
        self.progressMessageChanged.emit(self._progress_message)

        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._run_merge, args=(target_file, self._merge_mode, self._aspect_ratio), daemon=True
        )
        self._worker_thread.start()

    @Slot(str)
    def mergeAndCreateProject(self, project_type: str = "single") -> None:
        """Merge videos and immediately open in LinSub Auto or Manual dubbing."""
        if self._is_busy or len(self._clips) < 2:
            if len(self._clips) < 2:
                self.errorOccurred.emit("Hãy thêm ít nhất 2 video để tiến hành ghép.")
            return

        temp_output = str(self._thumb_dir / f"merged_{uuid.uuid4().hex[:8]}.mp4")

        self._is_busy = True
        self._progress = 0.0
        self._progress_message = "Đang ghép video để chuyển sang lồng tiếng…"
        self.busyChanged.emit()
        self.progressChanged.emit(self._progress)
        self.progressMessageChanged.emit(self._progress_message)

        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._run_merge,
            args=(temp_output, self._merge_mode, self._aspect_ratio, True, project_type),
            daemon=True
        )
        self._worker_thread.start()

    @Slot()
    def cancelMerge(self) -> None:
        self._stop_event.set()

    # -------------------------------------------------------------------------
    # Internal Processing
    # -------------------------------------------------------------------------

    def _recalculate_timings(self) -> None:
        current_ms = 0
        for idx, clip in enumerate(self._clips):
            clip["index"] = idx + 1
            clip["startMs"] = current_ms
            current_ms += clip["durationMs"]
            clip["endMs"] = current_ms

    def _format_time(self, seconds: float) -> str:
        s = max(0, int(seconds))
        m = s // 60
        s = s % 60
        h = m // 60
        m = m % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _extract_thumbnail(self, video_path: str, output_path: str, time_pos: float) -> None:
        try:
            cmd = [
                _binary("ffmpeg"), "-y", "-ss", f"{time_pos:.2f}",
                "-i", video_path,
                "-frames:v", "1",
                "-vf", "scale=720:-1:flags=lanczos",
                output_path
            ]
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass

    def _run_merge(
        self,
        output_path: str,
        mode: str,
        aspect_ratio: str,
        create_project: bool = False,
        project_type: str = "single"
    ) -> None:
        total_duration = self.totalDurationMs / 1000.0
        success = False

        try:
            if mode == "fast":
                success = self._concat_fast(output_path)
                if not success:
                    # Stream copy failed (e.g. incompatible codecs), fall back to standard re-encode
                    self._progress_message = "Codec các clip khác nhau, đang tự động chuyển sang chế độ chuẩn hóa…"
                    self.progressMessageChanged.emit(self._progress_message)
                    success = self._concat_standard(output_path, aspect_ratio, total_duration)
            else:
                success = self._concat_standard(output_path, aspect_ratio, total_duration)

            if not success or not os.path.isfile(output_path) or os.path.getsize(output_path) < 1000:
                raise RuntimeError("Không thể tạo tệp video đã ghép.")

            self._progress = 1.0
            self.progressChanged.emit(self._progress)

            if create_project and self._host:
                self._progress_message = "Đang khởi tạo dự án LinSub…"
                self.progressMessageChanged.emit(self._progress_message)
                self._create_linsub_project(output_path, project_type)

            self.mergeCompleted.emit(output_path)

        except Exception as exc:
            self.errorOccurred.emit(f"Lỗi khi ghép video: {exc}")
        finally:
            self._is_busy = False
            self.busyChanged.emit()

    def _concat_fast(self, output_path: str) -> bool:
        """Fast lossless stream copy concat using ffmpeg concat demuxer."""
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as f:
            list_file = f.name
            for clip in self._clips:
                p = clip["path"].replace("\\", "/")
                escaped_p = p.replace("'", "'\\''")
                f.write(f"file '{escaped_p}'\n")

        try:
            cmd = [
                _binary("ffmpeg"), "-y", "-v", "error",
                "-f", "concat", "-safe", "0",
                "-i", list_file,
                "-c", "copy",
                "-fflags", "+genpts",
                "-avoid_negative_ts", "make_zero",
                output_path
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) < 1000:
                return False

            # Validate that stream copy actually preserved the full sequence
            total_sec = self.totalDurationMs / 1000.0
            if total_sec > 5.0:
                actual_dur = get_video_duration(output_path)
                if abs(actual_dur - total_sec) > 3.0:
                    # Duration mismatch indicates packet/codec incompatibility; fall back to standard encode
                    return False

            return True
        except Exception:
            return False
        finally:
            try:
                os.remove(list_file)
            except OSError:
                pass

    def _concat_standard(self, output_path: str, aspect_ratio: str, total_duration: float) -> bool:
        """Standard concat with video scaling, padding and audio normalization."""
        # Determine target resolution
        first_w = self._clips[0].get("width", 1920) or 1920
        first_h = self._clips[0].get("height", 1080) or 1080

        if aspect_ratio == "16:9":
            target_w, target_h = 1920, 1080
        elif aspect_ratio == "9:16":
            target_w, target_h = 1080, 1920
        elif aspect_ratio == "1:1":
            target_w, target_h = 1080, 1080
        else:
            # Auto: align to first video
            target_w, target_h = first_w, first_h

        # Force even dimensions
        target_w = target_w if target_w % 2 == 0 else target_w + 1
        target_h = target_h if target_h % 2 == 0 else target_h + 1

        cmd = [_binary("ffmpeg"), "-y", "-hide_banner"]

        # Add inputs
        for clip in self._clips:
            cmd.extend(["-i", clip["path"]])

        # Build filter_complex
        filter_parts = []
        n_clips = len(self._clips)
        for i in range(n_clips):
            # Scale and pad to target dimension with black background
            filter_parts.append(
                f"[{i}:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,format=yuv420p[v{i}];"
            )
            # Ensure stereo 48000Hz audio
            filter_parts.append(
                f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{i}];"
            )

        concat_v_inputs = "".join(f"[v{i}][a{i}]" for i in range(n_clips))
        filter_parts.append(f"{concat_v_inputs}concat=n={n_clips}:v=1:a=1[outv][outa]")

        cmd.extend([
            "-filter_complex", "".join(filter_parts),
            "-map", "[outv]",
            "-map", "[outa]",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-c:a", "aac",
            "-b:a", "192k",
            "-progress", "pipe:1",
            output_path
        ])

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            # Read progress
            while proc.poll() is None:
                if self._stop_event.is_set():
                    proc.kill()
                    return False
                line = proc.stdout.readline() if proc.stdout else ""
                if not line:
                    continue
                if line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=")[1].strip())
                        cur_sec = us / 1_000_000.0
                        if total_duration > 0:
                            self._progress = min(0.99, max(0.0, cur_sec / total_duration))
                            self._progress_message = f"Đang ghép video: {int(self._progress * 100)}%"
                            self.progressChanged.emit(self._progress)
                            self.progressMessageChanged.emit(self._progress_message)
                    except ValueError:
                        pass

            return proc.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 1000
        except Exception:
            return False

    def _create_linsub_project(self, video_path: str, project_type: str = "single") -> None:
        """Create a new LinSub project and import the merged video."""
        from linsub.services import project_store

        base_name = os.path.splitext(os.path.basename(video_path))[0]
        project_name = f"Ghep_{int(time.time())}"
        default_dir = os.path.expanduser("~/Videos/LinSubProjects")
        os.makedirs(default_dir, exist_ok=True)

        try:
            project = project_store.create_project(project_name, default_dir, project_type)
            host = self._host
            if host:
                host._project_name = project["project_name"]
                host._project_directory = project["project_directory"]
                host._project_type = project_type
                host._selected_project_key = project["key"]
                host.videoPath = video_path
                host._project_import.import_video(video_path)
                host.projectSetupChanged.emit()
                host.selectedVideoChanged.emit()
                host.refreshVideos()
                host.projectPrepared.emit()
        except Exception as exc:
            self.errorOccurred.emit(f"Không thể tạo dự án: {exc}")
