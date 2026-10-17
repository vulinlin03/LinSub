"""Publishing tasks own their project, not navigation across the whole app."""

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from linsub.desktop.project_import_controller import ProjectImportController
from linsub.desktop.project_workspace_controller import ProjectWorkspaceController
from linsub.desktop.qml_controller import LinSubController
from linsub.desktop.social_publish_controller import SocialPublishController
from linsub.services import project_store, social_publish


class Items:
    def __init__(self):
        self.items = []

    def set_items(self, items):
        self.items = list(items)

    def item_at(self, row):
        return self.items[row] if 0 <= row < len(self.items) else None

    def update_item(self, item_id, item):
        for row, current in enumerate(self.items):
            if current["id"] == item_id:
                self.items[row] = item
                return True
        return False


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    project = project_store.create_project("Posting", str(tmp_path), "publish")
    social_publish.initialize_project(project["project_root"])
    host = SimpleNamespace(
        _project_name=project["project_name"], _project_type="publish",
        _project_directory=str(tmp_path), _selected_project_key=project["key"],
        _selected_video_id=None, _settings_owner_video_id=None, _settings_language="vi",
        _project_import=None, _media_downloader=Mock(), _refresh_batch_model=Mock(),
        _clear_logs=Mock(), refreshVideos=Mock(), tiktok_publish_items=Items(),
    )
    for name in ("socialPublishStateChanged", "zernioAccountsChanged", "zernioPostOptionsChanged",
                 "tiktokPublishChanged", "projectSetupChanged", "selectedVideoChanged", "logsChanged",
                 "projectPrepared", "batchChanged", "appAlertRequested"):
        setattr(host, name, Mock())
    publisher = SocialPublishController(host)
    publisher._project_key = project["key"]
    publisher._project_root = project["project_root"]
    publisher._api_key_cache = ""
    host._tiktok_publisher = publisher
    controller = ProjectWorkspaceController(host)
    host._open_project_summary = controller.open_project_summary
    importer = ProjectImportController(host)
    monkeypatch.setattr(importer, "_reset_new_project_setup", Mock())
    monkeypatch.setattr("linsub.services.desktop_settings.save_settings", Mock())
    monkeypatch.setattr("linsub.desktop.social_publish_controller.QMessageBox.information", Mock())
    yield host, publisher, project, importer
    publisher.shutdown()


@pytest.mark.parametrize("project_type", ["manual", "single", "batch", "download"])
def test_busy_social_import_does_not_block_creating_unrelated_projects(workspace, project_type, tmp_path):
    host, publisher, owner, importer = workspace
    publisher._busy = True
    assert importer.prepare_project("Independent " + project_type, str(tmp_path), project_type)
    assert host._project_type == project_type
    assert host._selected_project_key != owner["key"]
    assert publisher._project_key == owner["key"]
    assert publisher.busy and not publisher._cancel.is_set()


@pytest.mark.parametrize("project_type", ["manual", "single", "batch", "download"])
def test_busy_social_task_does_not_block_opening_unrelated_projects(workspace, project_type, tmp_path):
    host, publisher, owner, _ = workspace
    project = project_store.create_project("Open " + project_type, str(tmp_path), project_type)
    project["videos"] = []
    host.prepareChannelImport = Mock()
    publisher._busy = True
    assert LinSubController._select_project_summary(host, project)
    host.appAlertRequested.emit.assert_not_called()
    assert host._selected_project_key == project["key"]
    assert publisher._project_key == owner["key"]
    assert publisher.busy and publisher.has_project_work(owner["key"])


def test_other_publishing_project_still_cannot_steal_the_active_worker(workspace, tmp_path):
    host, publisher, owner, importer = workspace
    other = project_store.create_project("Other posting", str(tmp_path), "publish")
    other["videos"] = []
    publisher._busy = True
    assert not LinSubController._select_project_summary(host, other)
    assert not importer.prepare_project("New posting", str(tmp_path), "publish")
    assert publisher._project_key == host._selected_project_key == owner["key"]


def test_returning_to_active_owner_preserves_worker_account_and_consent(workspace, tmp_path):
    host, publisher, owner, importer = workspace
    publisher._busy = publisher._consent_confirmed = True
    publisher._account_generation = 7
    publisher._creator_generation = 9
    publisher._worker = worker = Mock()
    assert importer.prepare_project("Independent", str(tmp_path), "manual")
    owner["videos"] = []
    assert LinSubController._select_project_summary(host, owner)
    assert publisher._worker is worker and publisher.busy
    assert publisher._consent_confirmed
    assert publisher._account_generation == 7 and publisher._creator_generation == 9


def test_import_finishes_in_its_owner_after_navigation_and_can_be_reopened(workspace, tmp_path, monkeypatch):
    host, publisher, owner, importer = workspace
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original source")
    started, release = threading.Event(), threading.Event()
    def duration(*_args, **_kwargs):
        started.set()
        assert release.wait(5)
        return 10.0
    monkeypatch.setattr("linsub.desktop.social_publish_controller.get_video_duration", duration)
    monkeypatch.setattr("linsub.desktop.social_publish_controller.get_video_dimensions", lambda *_a, **_k: (1080, 1920))
    monkeypatch.setattr("linsub.desktop.social_publish_controller.create_video_thumbnail_path", Mock())
    assert publisher.add_videos([str(source)])
    worker = publisher._worker
    try:
        assert started.wait(2)
        assert importer.prepare_project("Manual while importing", str(tmp_path), "manual")
        independent = host._selected_project_key
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        publisher.drain_events()
        assert not publisher.busy
        assert host._selected_project_key == independent
        items = social_publish.load_state(owner["project_root"])["items"]
        assert len(items) == 1 and Path(items[0]["file_path"]).read_bytes() == b"original source"
        assert not social_publish.load_state(project_store.project_root_for_key(independent))["items"]
        owner["videos"] = []
        assert LinSubController._select_project_summary(host, owner)
        assert publisher.count == 1 and publisher._project_key == owner["key"]
        assert not list(Path(owner["project_root"]).rglob("*.part"))
    finally:
        release.set()
        worker.join(5)


def test_publish_all_continues_oldest_first_without_redirecting_to_new_workspace(workspace, tmp_path, monkeypatch):
    host, publisher, owner, importer = workspace
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video")
    items = [social_publish.new_item(str(media), "", order, str(order), "") for order in range(3)]
    items[0]["status"] = "uploading"
    publisher._state.update(items=items, selected_account_id="account", selected_platform="youtube", privacy_level="public")
    social_publish.save_state(owner["project_root"], publisher._state)
    publisher._sync_model()
    publisher._busy = publisher._auto_continue = publisher._consent_confirmed = True
    publisher._api_key_cache = "fake-test-key"
    # Real task start/finish events, no external upload or API request.
    sent = []
    def publish(_key, project_key, item, settings):
        sent.append((project_key, item["id"], settings["account_id"]))
        publisher._events.put(dict(type="publish_finished", project_key=project_key,
            item_id=item["id"], status="published", post_id="post-" + item["id"],
            platform="youtube", url="https://www.youtube.com/watch?v=example"))
    monkeypatch.setattr(publisher, "_publish_worker", publish)
    monkeypatch.setattr(publisher, "_platform_video_error", lambda _item: "")
    assert importer.prepare_project("Work during publishing", str(tmp_path), "manual")
    independent = host._selected_project_key
    publisher._events.put(dict(type="publish_finished", project_key=owner["key"], item_id=items[0]["id"],
        status="published", post_id="first", platform="youtube", url="https://www.youtube.com/watch?v=first"))
    for _ in range(4):
        publisher.drain_events()
        if publisher._worker:
            publisher._worker.join(2)
    assert sent == [(owner["key"], items[1]["id"], "account"), (owner["key"], items[2]["id"], "account")]
    assert not publisher.busy and not publisher._auto_continue
    assert host._selected_project_key == independent
    assert all(item["status"] == "published" for item in social_publish.load_state(owner["project_root"])["items"])
    assert not social_publish.load_state(project_store.project_root_for_key(independent))["items"]


def test_cancelled_queue_does_not_continue_when_workspace_is_hidden(workspace, monkeypatch):
    _, publisher, _, _ = workspace
    publisher._auto_continue = True
    publisher._cancel.set()
    start = Mock()
    monkeypatch.setattr(publisher, "_publish_item", start)
    assert not publisher._continue_owned_queue()
    start.assert_not_called()


def test_hidden_queue_cannot_start_without_previous_publish_all_consent(workspace, monkeypatch):
    host, publisher, _, _ = workspace
    host._project_type = "manual"
    publisher._consent_confirmed = True
    publisher._auto_continue = False
    assert not publisher._ensure_ready_to_publish({}, background=True)
