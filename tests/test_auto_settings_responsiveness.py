"""Option clicks snapshot memory; autosave updates one project, not the catalog."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import time

from linsub.desktop.project_commands_controller import ProjectCommandsController
from linsub.desktop.qml_controller import LinSubController
from linsub.schemas.video import VideoConfig


def setup_host():
    config = VideoConfig(project_key="project", project_id="project-id")
    fields = {"_" + key: getattr(config, key) for key in VideoConfig.model_fields}
    fields.update(_selected_video_id="one", _settings_owner_video_id="one", _selected_project_key="project",
        _selected_video_snapshot=SimpleNamespace(video_id="one", project_key="project", project_id="project-id"),
        _settings_processing_device="cpu", _crop_settings=config.crop,
        _original_volume=config.original_video_volume, _background_music_path="", _manual_settings_drafts={},
        _processing_queue=SimpleNamespace(contains=lambda _: False), subtitleSettingsChanged=Mock())
    host = SimpleNamespace(**fields)
    host._build_config = lambda: LinSubController._build_config(host)
    return host


def test_one_hundred_cover_and_other_option_clicks_do_not_read_project_index():
    host = setup_host()
    with patch("linsub.desktop.qml_controller.project_store.get_project", side_effect=AssertionError("UI must not scan all project manifests")):
        started = time.perf_counter()
        for index in range(100):
            LinSubController.removeOriginalSubtitles.fset(host, index % 2 == 0)
            host._output_speed = 1 + index % 3 / 2
            host._original_subtitle_removal_mode = "blur" if index % 2 else "patch"
            assert LinSubController.captureVideoSettingsDraft(host, "one")
        elapsed = time.perf_counter() - started
    draft = host._manual_settings_drafts["one"]
    assert draft.project_id == "project-id" and draft.output_speed == host._output_speed
    assert draft.remove_original_subtitles == host._remove_original_subtitles
    assert elapsed < 1.0
    print(f"100 Auto setting snapshots: {elapsed * 1000:.2f} ms (not an end-to-end UI frame benchmark)")


def test_foreign_project_snapshot_never_leaks_identity():
    host = setup_host()
    host._selected_video_snapshot.project_key = "old-project"
    with patch("linsub.desktop.qml_controller.project_store.get_project", return_value={"project_id": "correct-id"}):
        assert host._build_config().project_id == "correct-id"


def test_autosave_updates_only_changed_video_and_keeps_unrelated_catalog_work_out_of_ui():
    old = SimpleNamespace(video_id="one", project_type="single", value="old")
    updated = SimpleNamespace(video_id="one", project_type="single", value="new")
    host = SimpleNamespace(_selected_video_id="one", _settings_owner_video_id="one",
        _processing_queue=SimpleNamespace(contains=lambda _: False), _apply_setup_to_video=Mock(),
        _video_settings_snapshot=lambda video: {"value": video.value}, _record_video_settings_change=Mock(),
        _apply_video_metadata_changes=Mock(return_value=True), refreshVideos=Mock(), selectedVideoChanged=Mock())
    with patch("linsub.desktop.project_commands_controller.video_store.get_video", side_effect=[old, updated]):
        assert ProjectCommandsController(host).persist_selected_video_settings("one")
    host._apply_video_metadata_changes.assert_called_once_with({"one"})
    host.refreshVideos.assert_not_called()
    host.selectedVideoChanged.emit.assert_not_called()  # Incremental update owns the one notification.


def test_autosave_can_still_refresh_a_missing_catalog_entry():
    old = SimpleNamespace(video_id="one", project_type="single")
    host = SimpleNamespace(_selected_video_id="one", _settings_owner_video_id="one",
        _processing_queue=SimpleNamespace(contains=lambda _: False), _apply_setup_to_video=Mock(),
        _apply_video_metadata_changes=Mock(return_value=False), refreshVideos=Mock(), selectedVideoChanged=Mock())
    with patch("linsub.desktop.project_commands_controller.video_store.get_video", return_value=old):
        assert ProjectCommandsController(host).persist_selected_video_settings("one")
    host.refreshVideos.assert_called_once()
