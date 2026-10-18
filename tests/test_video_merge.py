import os
import unittest
from pathlib import Path
from linsub.desktop.video_merge_controller import VideoMergeController

QML_DIR = Path(__file__).resolve().parent.parent / "src" / "linsub" / "desktop" / "qml"


class VideoMergeTests(unittest.TestCase):
    def test_controller_initial_state(self):
        controller = VideoMergeController()
        self.assertEqual(controller.clips, [])
        self.assertEqual(controller.clipCount, 0)
        self.assertEqual(controller.totalDurationMs, 0)
        self.assertEqual(controller.totalDurationFormatted, "00:00")
        self.assertEqual(controller.activeClipIndex, -1)
        self.assertEqual(controller.currentPreviewSource, "")
        self.assertFalse(controller.isBusy)
        self.assertEqual(controller.aspectRatio, "auto")
        self.assertEqual(controller.mergeMode, "fast")

    def test_recalculate_timings_and_positions(self):
        controller = VideoMergeController()
        controller._clips = [
            {"id": "1", "path": "test1.mp4", "name": "1.mp4", "duration": 10.0, "durationMs": 10000},
            {"id": "2", "path": "test2.mp4", "name": "2.mp4", "duration": 25.5, "durationMs": 25500},
            {"id": "3", "path": "test3.mp4", "name": "3.mp4", "duration": 5.0, "durationMs": 5000},
        ]
        controller._recalculate_timings()

        self.assertEqual(controller.clipCount, 3)
        self.assertEqual(controller.clips[0]["startMs"], 0)
        self.assertEqual(controller.clips[0]["endMs"], 10000)
        self.assertEqual(controller.clips[1]["startMs"], 10000)
        self.assertEqual(controller.clips[1]["endMs"], 35500)
        self.assertEqual(controller.clips[2]["startMs"], 35500)
        self.assertEqual(controller.clips[2]["endMs"], 40500)
        self.assertEqual(controller.totalDurationMs, 40500)
        self.assertEqual(controller.totalDurationFormatted, "00:40")

        # Test position resolution across sequence
        pos0 = controller.resolvePosition(5000)
        self.assertEqual(pos0["clipIndex"], 0)
        self.assertEqual(pos0["localMs"], 5000)

        pos1 = controller.resolvePosition(15000)
        self.assertEqual(pos1["clipIndex"], 1)
        self.assertEqual(pos1["localMs"], 5000)

        pos2 = controller.resolvePosition(37000)
        self.assertEqual(pos2["clipIndex"], 2)
        self.assertEqual(pos2["localMs"], 1500)

    def test_move_and_remove_clips(self):
        controller = VideoMergeController()
        controller._clips = [
            {"id": "1", "path": "test1.mp4", "name": "1.mp4", "duration": 10.0, "durationMs": 10000},
            {"id": "2", "path": "test2.mp4", "name": "2.mp4", "duration": 20.0, "durationMs": 20000},
            {"id": "3", "path": "test3.mp4", "name": "3.mp4", "duration": 30.0, "durationMs": 30000},
        ]
        controller._recalculate_timings()

        # Move clip 0 to 2
        controller.moveClip(0, 2)
        self.assertEqual([c["id"] for c in controller.clips], ["2", "3", "1"])
        self.assertEqual(controller.clips[0]["startMs"], 0)
        self.assertEqual(controller.clips[0]["endMs"], 20000)

        # Remove middle clip
        controller.removeClip(1)
        self.assertEqual([c["id"] for c in controller.clips], ["2", "1"])
        self.assertEqual(controller.totalDurationMs, 30000)

        # Clear
        controller.clearClips()
        self.assertEqual(controller.clipCount, 0)
        self.assertEqual(controller.totalDurationMs, 0)

    def test_natural_sort_and_reverse(self):
        controller = VideoMergeController()
        controller._clips = [
            {"id": "c20", "path": "20_ep.mp4", "name": "20_ep.mp4", "duration": 5.0, "durationMs": 5000},
            {"id": "c3", "path": "3_ep.mp4", "name": "3_ep.mp4", "duration": 10.0, "durationMs": 10000},
            {"id": "c2", "path": "2_ep.mp4", "name": "2_ep.mp4", "duration": 15.0, "durationMs": 15000},
        ]
        controller._recalculate_timings()

        # Sort naturally: 2, 3, 20
        controller.sortClipsByName()
        self.assertEqual([c["id"] for c in controller.clips], ["c2", "c3", "c20"])
        self.assertEqual(controller.clips[0]["startMs"], 0)
        self.assertEqual(controller.clips[0]["endMs"], 15000)
        self.assertEqual(controller.clips[1]["startMs"], 15000)
        self.assertEqual(controller.clips[1]["endMs"], 25000)
        self.assertEqual(controller.clips[2]["startMs"], 25000)
        self.assertEqual(controller.clips[2]["endMs"], 30000)

        # Reverse
        controller.reverseClips()
        self.assertEqual([c["id"] for c in controller.clips], ["c20", "c3", "c2",])

        # Test circled numbers (①..⑳, 21) like Chinese short dramas
        controller._clips = [
            {"id": "c10", "name": "⑩ 先天缺魂.mp4", "durationMs": 1000},
            {"id": "c1", "name": "① 先天缺魂.mp4", "durationMs": 1000},
            {"id": "c20", "name": "⑳ 先天缺魂.mp4", "durationMs": 1000},
            {"id": "c2", "name": "② 先天缺魂.mp4", "durationMs": 1000},
            {"id": "c21", "name": "21. 先天缺魂.mp4", "durationMs": 1000},
            {"id": "c11", "name": "⑪ 先天缺魂.mp4", "durationMs": 1000},
        ]
        controller.sortClipsByName()
        self.assertEqual([c["id"] for c in controller.clips], ["c1", "c2", "c10", "c11", "c20", "c21"])

        # Test mixed number-first and text-first filenames (type-safe in Python 3)
        controller._clips = [
            {"id": "c_num10", "name": "10_《七界传说》.mp4", "durationMs": 1000},
            {"id": "c_text1", "name": "《七界传说》_1.mp4", "durationMs": 1000},
            {"id": "c_num2", "name": "2_《七界传说》.mp4", "durationMs": 1000},
        ]
        controller.sortClipsByName()
        self.assertEqual([c["id"] for c in controller.clips], ["c_num2", "c_num10", "c_text1"])
        self.assertTrue(controller.sortAscending)

        # Test toggle sort order: when currently ascending, toggleSortByName sorts descending
        controller.toggleSortByName()
        self.assertEqual([c["id"] for c in controller.clips], ["c_text1", "c_num10", "c_num2"])
        self.assertFalse(controller.sortAscending)

        # Toggle back to ascending
        controller.toggleSortByName()
        self.assertEqual([c["id"] for c in controller.clips], ["c_num2", "c_num10", "c_text1"])
        self.assertTrue(controller.sortAscending)

        # Test boundary position resolution: at 1000ms (end of clip 0 / start of clip 1), it must resolve to clip 1
        pos1 = controller.resolvePosition(1000)
        self.assertEqual(pos1["clipIndex"], 1)
        self.assertEqual(pos1["localMs"], 0)

        # Test moveClip tracks activeClipIndex
        controller._active_clip_index = 2 # c_text1
        controller.moveClip(0, 1) # swap c_num2 and c_num10
        self.assertEqual(controller.activeClipIndex, 2)
        controller.moveClip(2, 0) # move c_text1 to front
        self.assertEqual(controller.activeClipIndex, 0)

    def test_navigation_and_routes_include_merge(self):
        rail = (QML_DIR / "NavigationRail.qml").read_text(encoding="utf-8")
        self.assertIn('"merge"', rail)
        self.assertIn('qsTr("Ghép video")', rail)

        main = (QML_DIR / "Main.qml").read_text(encoding="utf-8")
        self.assertIn('readonly property string routeMerge: "merge"', main)
        self.assertIn('if (currentRoute === routeMerge)', main)
        self.assertIn('else if (section === "merge")', main)
        self.assertIn('route === routeMerge', main)

        host = (QML_DIR / "RouteHost.qml").read_text(encoding="utf-8")
        self.assertIn('"merge": 14', host)
        self.assertIn("VideoMergePage {}", host)

    def test_video_merge_page_has_toolbar_under_preview(self):
        page = (QML_DIR / "VideoMergePage.qml").read_text(encoding="utf-8")
        self.assertIn("AppController.videoMerge", page)
        self.assertIn("ZONE 1: TOP - PREVIEW MONITOR", page)
        self.assertIn("ZONE 2: MIDDLE - HORIZONTAL CONTROL TOOLBAR", page)
        self.assertIn("ZONE 3: BOTTOM - MULTI-CLIP HORIZONTAL TIMELINE", page)
        self.assertIn('qsTr("Thêm video")', page)
        self.assertIn('qsTr("Xuất video")', page)
        self.assertIn('qsTr("Ghép & Lồng tiếng ngay")', page)
        self.assertIn('qsTr("Siêu tốc (Lossless)")', page)
        self.assertIn('qsTr("Chuẩn hóa tỷ lệ")', page)
        self.assertIn('qsTr("Thứ tự video")', page)
        self.assertIn('id: sideOrderList', page)


if __name__ == "__main__":
    unittest.main()
