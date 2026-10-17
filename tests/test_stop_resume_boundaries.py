"""Stop/cancel at worker handoff must not restart work or publish stale data."""

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QGuiApplication

from linsub.desktop import processing_lifecycle_controller as lifecycle
from linsub.desktop.channel_import import ChannelImportCoordinator
from linsub.desktop.media_download_controller import MediaDownloadController
from linsub.pipeline import manual_tools, process_registry, process_video
from linsub.schemas.channel_import import ChannelImportRequest, ChannelVideoCandidate
from linsub.schemas.video import VideoConfig, VideoInfo
from linsub.services import processing_runs
from linsub.services.channel_import import download_workspace, new_session


_GUI = None


@pytest.fixture(scope="module", autouse=True)
def gui():
    global _GUI
    _GUI = QGuiApplication.instance() or QGuiApplication([])
    yield _GUI


@pytest.fixture
def stopped_video(monkeypatch):
    video = VideoInfo.model_validate({**VideoConfig(project_type="manual").model_dump(),
        "video_id": "stop-boundary", "original_filename": "video.mp4", "status": "processing",
        "created_at": "2026-10-15T00:00:00Z", "updated_at": "2026-10-15T00:00:00Z",
        "manual_target_tool": "voice", "active_artifacts": {"subtitle_document": "old"}})
    def update(_identifier, **changes):
        for key, value in changes.items():
            setattr(video, key, value)
        return video
    monkeypatch.setattr(lifecycle.video_store, "get_video", lambda _: video)
    monkeypatch.setattr(lifecycle.video_store, "update_video", update)
    monkeypatch.setattr(lifecycle.video_store, "log_to_video", Mock())
    monkeypatch.setattr(processing_runs, "begin", Mock())
    monkeypatch.setattr(processing_runs, "finish", Mock())
    process_registry.prepare_video_resume(video.video_id)
    yield video
    process_registry.clean_video(video.video_id)


@pytest.mark.parametrize("kind", ["manual", "single", "batch"])
def test_pause_during_model_handoff_does_not_overwrite_paused_state(stopped_video, monkeypatch, kind):
    video = stopped_video
    video.project_type = kind
    if kind != "manual":
        video.manual_target_tool = ""
    ready = threading.Event()
    ready.set()
    def pause(**_kwargs):
        process_registry.pause_video(video.video_id)
        video.status, video.step, video.resume_step = "paused", "paused", "waiting_for_models"
    host = SimpleNamespace(_deleted_video_ids=set(), _initial_model_warmup_done=ready,
                           _model_runtime_lock=threading.Lock(), _shutdown_started=False,
                           _model_setup_state="ready", _runtime_probe_error="",
                           _smart_warmup=SimpleNamespace(quiesce_for_foreground=pause))
    monkeypatch.setattr(lifecycle, "runtime_profile", lambda: SimpleNamespace(
        total_ram_gib=16, cuda_available=False, total_vram_gib=0))
    monkeypatch.setattr(process_video, "foreground_capability", lambda *_: "voice")
    manual, auto = Mock(), Mock()
    monkeypatch.setattr(manual_tools, "run_manual_tool_sync", manual)
    monkeypatch.setattr(process_video, "process_video_sync", auto)
    lifecycle.ProcessingLifecycleController(host).execute_pipeline(video.video_id)
    manual.assert_not_called()
    auto.assert_not_called()
    assert video.status == "paused" and video.resume_step == "waiting_for_models"


def test_pause_while_runtime_lock_is_held_leaves_queue_promptly(stopped_video, monkeypatch):
    video = stopped_video
    ready, waiting = threading.Event(), threading.Event()
    ready.set()
    lock = threading.Lock()
    lock.acquire()
    host = SimpleNamespace(_deleted_video_ids=set(), _initial_model_warmup_done=ready,
                           _model_runtime_lock=lock, _shutdown_started=False,
                           _model_setup_state="ready", _runtime_probe_error="")
    def profile():
        waiting.set()
        return SimpleNamespace(total_ram_gib=16, cuda_available=False, total_vram_gib=0)
    monkeypatch.setattr(lifecycle, "runtime_profile", profile)
    monkeypatch.setattr(process_video, "foreground_capability", lambda *_: "voice")
    run = Mock()
    monkeypatch.setattr(manual_tools, "run_manual_tool_sync", run)
    worker = threading.Thread(target=lifecycle.ProcessingLifecycleController(host).execute_pipeline, args=(video.video_id,))
    try:
        worker.start()
        assert waiting.wait(2)
        process_registry.pause_video(video.video_id)
        video.status = "paused"
        worker.join(1)
        assert not worker.is_alive()
        run.assert_not_called()
        assert video.status == "paused"
    finally:
        lock.release()
        worker.join(2)


@pytest.mark.parametrize("operation", ["pause", "cancel"])
@pytest.mark.parametrize("kind", ["manual", "single"])
def test_request_between_queue_handoff_and_pipeline_entry_is_not_cleared(stopped_video, monkeypatch, operation, kind):
    video = stopped_video
    video.project_type = kind
    video.resume_step = "voice"
    getattr(process_registry, operation + "_video")(video.video_id)
    reporter = Mock()
    monkeypatch.setattr(process_video, "ProgressReporter", lambda _: reporter)
    monkeypatch.setattr(process_video, "get_video", lambda _: video)
    monkeypatch.setattr(process_video, "update_video", lifecycle.video_store.update_video)
    monkeypatch.setattr(process_video, "log_to_video", Mock())
    work = Mock()
    if kind == "manual":
        monkeypatch.setitem(manual_tools._RUNNERS, "voice", work)
        monkeypatch.setattr(manual_tools, "_requested_artifact", lambda *_: ("tts_manifest", "test"))
        monkeypatch.setattr(manual_tools, "_voice_ready", lambda _: False)
        monkeypatch.setattr(manual_tools.manual_artifacts, "maintain", Mock())
        manual_tools.run_manual_tool_sync(video.video_id, "voice")
    else:
        monkeypatch.setattr(process_video, "_required_video_path", work)
        monkeypatch.setattr(process_video, "_recover_gpu_to_cpu", lambda *_: False)
        process_video.process_video_sync(video.video_id)
    work.assert_not_called()
    assert video.status == ("paused" if operation == "pause" else "cancelled")
    assert video.active_artifacts == {"subtitle_document": "old"}


def test_pause_at_manual_completion_keeps_complete_result_and_resume_target(stopped_video, monkeypatch):
    video = stopped_video
    def finish_then_pause(*_args):
        video.active_artifacts["tts_manifest"] = "finished-before-pause"
        process_registry.pause_video(video.video_id)
    monkeypatch.setitem(manual_tools._RUNNERS, "voice", finish_then_pause)
    monkeypatch.setattr(manual_tools, "_requested_artifact", lambda *_: ("tts_manifest", "test"))
    monkeypatch.setattr(manual_tools, "_voice_ready", lambda _: False)
    monkeypatch.setattr(manual_tools.manual_artifacts, "maintain", Mock())
    monkeypatch.setattr(process_video, "ProgressReporter", lambda _: Mock())
    manual_tools.run_manual_tool_sync(video.video_id, "voice")
    assert video.status == "paused" and video.resume_step == "voice"
    assert video.manual_target_tool == "voice"
    assert video.active_artifacts["tts_manifest"] == "finished-before-pause"


@pytest.mark.parametrize("boundary", ["before_publication", "after_publication"])
def test_auto_completion_cannot_replace_pause_with_done(stopped_video, monkeypatch, tmp_path, boundary):
    from linsub.services import manual_artifacts, video_export

    video = stopped_video
    video.project_type = "single"
    video.files = {key: str(tmp_path / name) for key, name in {
        "video_input": "source.mp4", "srt_output": "captions.srt", "voice_output": "mix.wav",
        "transcript_json": "captions.json"}.items()}
    Path(video.files["video_input"]).write_bytes(b"source")
    Path(video.files["transcript_json"]).write_text('[{"start":0,"end":1,"text":"Hello"}]', encoding="utf-8")
    final = tmp_path / "render.mp4"
    final.write_bytes(b"completed render")
    monkeypatch.setattr(process_video, "get_video", lambda _: video)
    monkeypatch.setattr(process_video, "update_video", lifecycle.video_store.update_video)
    monkeypatch.setattr(process_video, "log_to_video", Mock())
    monkeypatch.setattr(process_video, "_checkpoint_valid", lambda *_: True)
    monkeypatch.setattr(process_video, "_voice_checkpoint_signature", lambda *args, **kwargs: "voice")
    monkeypatch.setattr(process_video, "_prepare_audio_mix", lambda *_: (video.files["video_input"], 60))
    monkeypatch.setattr(process_video, "_original_subtitle_region_for_render", lambda *_: None)
    monkeypatch.setattr(process_video, "generate_srt", Mock())
    monkeypatch.setattr(video_export, "export_destination", lambda _: final)
    monkeypatch.setattr(video_export, "current_render", lambda _: None)
    monkeypatch.setattr(video_export, "render_revision", lambda _: "revision")
    monkeypatch.setattr(manual_artifacts, "maintain", Mock())
    def pause():
        process_registry.pause_video(video.video_id)
        video.status, video.resume_step = "paused", "rendering"
    def publish(*args, **kwargs):
        pause()
        return {"resolved_outputs": {"video": str(final)}}
    register = Mock(side_effect=publish)
    monkeypatch.setattr(manual_artifacts, "register_existing", register)
    def progress(percent, *_args):
        if boundary == "before_publication" and percent == 99:
            pause()
    with pytest.raises(RuntimeError, match="cancelled"):
        process_video._finish_after_translation(video, SimpleNamespace(update=progress), str(tmp_path), video.files["video_input"])
    assert video.status == "paused" and video.resume_step == "rendering"
    assert final.read_bytes() == b"completed render"
    assert register.call_count == (1 if boundary == "after_publication" else 0)


def test_shutdown_cannot_start_next_download_from_late_completion(monkeypatch):
    controller = MediaDownloadController()
    controller._active_task = {"kind": "video", "label": "old"}
    controller._pending_tasks.append({"kind": "video", "label": "next", "value": "url", "output": "unused"})
    launch = Mock()
    monkeypatch.setattr(threading.Thread, "start", launch)
    try:
        assert controller.shutdown()
        controller._set_failed("Download cancelled.")
        controller._set_finished("late-result.mp4")
        launch.assert_not_called()
        assert not controller._pending_tasks
    finally:
        controller.shutdown()


def test_cancelled_download_is_idle_but_already_committed_file_stays_successful():
    controller = MediaDownloadController()
    try:
        controller._active_task = {"kind": "video", "label": "download"}
        controller._cancel.set()
        controller._set_failed("Download cancelled.")
        assert controller.state == "idle" and controller._active_task is None
        controller._active_task = {"kind": "video", "label": "already committed"}
        controller._set_finished("complete.mp4")
        assert controller.state == "done" and controller._active_task is None
    finally:
        controller.shutdown()


@pytest.fixture
def channel_session(tmp_path):
    coordinator = ChannelImportCoordinator()
    session = new_session("boundary-project", str(tmp_path), ChannelImportRequest(url="https://www.youtube.com/@test"))
    candidate = ChannelVideoCandidate(remote_video_id="video-1", source_url="https://www.youtube.com/watch?v=video-1",
                                      title="test", platform="YouTube", status="downloading")
    session.candidates = [candidate]
    session.state = "downloading"
    coordinator._sessions[session.session_id] = session
    coordinator._active_session_id = session.session_id
    coordinator._rebuild_session_cache(session)
    coordinator._save_session = Mock()
    cancellation = threading.Event()
    coordinator._cancel_events[session.session_id] = cancellation
    thread = threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    coordinator._runner_threads[session.session_id] = thread
    workspace = Path(download_workspace(str(tmp_path), session.session_id, candidate.remote_video_id))
    (workspace / "partial.mp4").write_bytes(b"partial")
    coordinator._workspaces[(session.session_id, candidate.remote_video_id)] = str(workspace)
    yield coordinator, session, candidate, cancellation, workspace
    coordinator.shutdown()


def test_pruning_finished_worker_keeps_cancellation_until_queued_callback(channel_session):
    coordinator, session, candidate, cancellation, workspace = channel_session
    cancellation.set()
    imported = Mock()
    coordinator.videoReady.connect(imported)
    coordinator._prune_finished_runners()
    coordinator._handle_download_resolved(session.session_id, candidate.model_dump(), str(workspace / "partial.mp4"))
    imported.assert_not_called()
    assert not workspace.exists()
    assert candidate.status == "failed"


def test_channel_shutdown_cleans_unconsumed_download_workspace(channel_session):
    coordinator, _session, _candidate, cancellation, workspace = channel_session
    assert coordinator.shutdown()
    assert cancellation.is_set()
    assert not workspace.exists()


def test_cancelled_scan_result_is_not_committed_after_stop(channel_session):
    coordinator, session, candidate, cancellation, _workspace = channel_session
    session.state = "cancelling"
    cancellation.set()
    coordinator._handle_scan_resolved(session.session_id, {"candidates": [], "channel_name": "late scan"})
    assert session.candidates == [candidate]
    assert session.state == "partial"


def test_cancel_waiting_channel_save_does_not_publish_file(tmp_path, monkeypatch):
    controller = MediaDownloadController()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"new download")
    output = tmp_path / "saved"
    output.mkdir()
    controller._active_channel_output_directory = str(output)
    cancellation = threading.Event()
    controller._channel_importer._cancel_events["session"] = cancellation
    complete = Mock()
    monkeypatch.setattr(controller._channel_importer, "complete_video", complete)
    controller._output_file_lock.acquire()
    try:
        controller._save_channel_video(str(source), str(tmp_path), {"remote_video_id": "one"}, "project", "session")
        cancellation.set()
    finally:
        controller._output_file_lock.release()
    # shutdown must also join the save worker and consume its cleanup receipt,
    # without relying on the GUI event loop still running during app close.
    assert controller.shutdown()
    assert source.read_bytes() == b"new download"
    assert not list(output.iterdir())
    complete.assert_called_once()
    assert complete.call_args.args[2] is False


def test_cross_volume_download_copy_cancel_never_leaves_partial_output(tmp_path, monkeypatch):
    import errno
    import os
    import io

    controller = MediaDownloadController()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"new download")
    output = tmp_path / "saved"
    output.mkdir()
    existing = output / "video.mp4"
    existing.write_bytes(b"old keep")
    rename = os.rename
    def cross_volume(old, new):
        if Path(old) == source:
            raise OSError(errno.EXDEV, "fixture cross volume")
        return rename(old, new)
    monkeypatch.setattr(os, "rename", cross_volume)
    original_open = Path.open
    class Reader(io.BytesIO):
        def read(self, size=-1):
            data = super().read(size)
            controller._cancel.set()
            return data
    def open_file(path, *args, **kwargs):
        return Reader(b"new download") if path == source else original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_file)
    from linsub.services.video_download import DownloadCancelled
    try:
        with pytest.raises(DownloadCancelled):
            controller._move_to_unique_path(source, existing)
    finally:
        controller.shutdown()
    assert existing.read_bytes() == b"old keep"
    assert [path.name for path in output.iterdir()] == ["video.mp4"]


def test_cross_volume_success_commits_once_and_keeps_existing_file(tmp_path, monkeypatch):
    import errno
    import os

    controller = MediaDownloadController()
    source = tmp_path / "source.mp4"
    payload = b"complete download" * 100000
    source.write_bytes(payload)
    output = tmp_path / "saved"
    output.mkdir()
    existing = output / "video.mp4"
    existing.write_bytes(b"old keep")
    rename = os.rename
    def cross_volume(old, new):
        if Path(old) == source:
            raise OSError(errno.EXDEV, "fixture cross volume")
        return rename(old, new)
    monkeypatch.setattr(os, "rename", cross_volume)
    try:
        destination = controller._move_to_unique_path(source, existing)
        assert destination.name == "video (2).mp4"
        assert destination.read_bytes() == payload and not source.exists()
        assert existing.read_bytes() == b"old keep"
        assert sorted(path.name for path in output.iterdir()) == ["video (2).mp4", "video.mp4"]
    finally:
        controller.shutdown()
