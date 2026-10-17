"""Windows path lengths and parent aliases must not invalidate complete audio."""
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from linsub.pipeline import manual_tools
from linsub.services import manual_artifacts


@pytest.fixture
def aliased_workspace(tmp_path, monkeypatch):
    physical = tmp_path / "physical"
    alias = tmp_path / "mapped"
    physical.mkdir()
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(physical)],
                       check=True, capture_output=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        alias.symlink_to(physical, target_is_directory=True)
    workspace = alias / "Dữ liệu dự án" / "video"
    workspace.mkdir(parents=True)
    video = SimpleNamespace(video_id="aliased-video", project_type="manual", active_artifacts={})
    monkeypatch.setattr(manual_artifacts.video_store, "get_video_dir", lambda _: str(workspace))
    monkeypatch.setattr(manual_artifacts.video_store, "get_video", lambda _: video)
    def update(_identifier, **changes):
        for key, value in changes.items():
            setattr(video, key, value)
        return video
    monkeypatch.setattr(manual_artifacts.video_store, "update_video", update)
    try:
        yield workspace, physical, video
    finally:
        # Remove only this test's alias, never its target recursively.
        assert alias.parent == tmp_path and alias.resolve() == physical.resolve()
        if os.name == "nt":
            assert alias.is_junction()
            alias.rmdir()
        else:
            alias.unlink()


@pytest.mark.parametrize("kind,outputs", [
    ("source_audio", {"audio": "audio.wav"}),
    ("separation", {"vocals": "tracks/vocals.wav", "no_vocals": "tracks/no_vocals.wav"}),
    ("translation", {"segments": "segments.json"}),
])
def test_new_cache_under_mapped_parent_is_verified(kind, outputs, aliased_workspace):
    workspace, _, video = aliased_workspace
    stage = manual_artifacts.create_staging_directory(video.video_id, kind)
    for relative in outputs.values():
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-complete-content")
    result = manual_artifacts.publish(video.video_id, kind, "fresh", stage, outputs)
    assert video.active_artifacts[kind] == "fresh"
    assert manual_artifacts.resolve(video.video_id, kind, "fresh")
    assert manual_artifacts.peek(video.video_id, kind, "fresh")
    assert all(Path(path).read_bytes() == b"test-complete-content" for path in result["resolved_outputs"].values())
    manual_artifacts.assert_root_idle(workspace)


@pytest.mark.parametrize("legacy_cache_exists", [False, True])
def test_separation_publishes_below_legacy_windows_path_limit(tmp_path, monkeypatch, legacy_cache_exists):
    # Match the workspace-root length in the user's 1.0.0 log, without changing
    # the machine's long-path policy. Staging succeeds, but the old nested
    # final output would become 278 characters after its hash is substituted.
    workspace = tmp_path / ("数" * (153 - len(str(tmp_path)) - 1))
    assert len(str(workspace)) == 153
    workspace.mkdir()
    video = SimpleNamespace(video_id="long-path-video", project_type="manual", active_artifacts={}, files={})
    monkeypatch.setattr(manual_artifacts.video_store, "get_video_dir", lambda _: str(workspace))
    monkeypatch.setattr(manual_artifacts.video_store, "get_video", lambda _: video)
    def update(_identifier, **changes):
        for key, value in changes.items():
            setattr(video, key, value)
        return video
    monkeypatch.setattr(manual_artifacts.video_store, "update_video", update)
    monkeypatch.setattr(manual_tools, "source_signature", lambda _: "a" * 64)
    monkeypatch.setattr(manual_tools, "separation_signature", lambda _: "b" * 64)
    monkeypatch.setattr(manual_tools, "_video_input", lambda _: "source.mp4")
    extract = Mock(side_effect=lambda _, target, _id: Path(target).write_bytes(b"source" * 100))
    monkeypatch.setattr(manual_tools, "extract_audio", extract)
    def separate(_source, output, _id):
        directory = Path(output) / "955717e8" / "audio"
        directory.mkdir(parents=True)
        paths = [directory / "vocals.wav", directory / "no_vocals.wav"]
        for path in paths:
            path.write_bytes(path.name.encode() * 100)
            assert len(str(path)) < 260
        return tuple(str(path) for path in paths)
    separate_mock = Mock(side_effect=separate)
    monkeypatch.setattr(manual_tools, "separate_audio", separate_mock)
    if legacy_cache_exists:
        stage = manual_artifacts.create_staging_directory(video.video_id, "separation")
        paths = separate("source.wav", stage / "tracks", video.video_id)
        manual_artifacts.publish(video.video_id, "separation", "b" * 64, stage,
                                 dict(zip(("vocals", "no_vocals"), (str(Path(path).relative_to(stage)) for path in paths))))
    original_digest = manual_artifacts._has_expected_digest
    monkeypatch.setattr(manual_artifacts, "_has_expected_digest",
                        lambda path, size, digest: len(str(path)) < 260 and original_digest(path, size, digest))
    legacy_output = manual_artifacts.artifact_directory(video.video_id, "separation", "b" * 64) / "tracks/955717e8/audio/no_vocals.wav"
    assert len(str(legacy_output)) >= 260

    manual_tools._run_separation(video, SimpleNamespace(update=Mock()))
    result = manual_artifacts.resolve(video.video_id, "separation", "b" * 64)
    assert result["outputs"] == {"vocals": "vocals.wav", "no_vocals": "no_vocals.wav"}
    assert all(len(path) < 260 for path in result["resolved_outputs"].values())
    assert sorted(path.name for path in Path(result["resolved_outputs"]["vocals"]).parent.iterdir()) == [
        "complete.json", "no_vocals.wav", "vocals.wav",
    ]
    assert Path(video.files["background_audio"]).read_bytes() == b"no_vocals.wav" * 100
    source_bytes = Path(video.files["source_audio"]).read_bytes()
    manual_tools._run_separation(video, SimpleNamespace(update=Mock()))
    assert Path(video.files["source_audio"]).read_bytes() == source_bytes
    assert extract.call_count == separate_mock.call_count == 1
    manual_artifacts.assert_root_idle(workspace)
    assert not list(workspace.rglob(".partial-*"))


def test_legacy_nested_separation_cache_remains_readable(aliased_workspace):
    _, _, video = aliased_workspace
    stage = manual_artifacts.create_staging_directory(video.video_id, "separation")
    outputs = {"vocals": "tracks/955717e8/audio/vocals.wav", "no_vocals": "tracks/955717e8/audio/no_vocals.wav"}
    for relative in outputs.values():
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"old-audio" * 100)
    manual_artifacts.publish(video.video_id, "separation", "legacy", stage, outputs)
    record = manual_artifacts.resolve(video.video_id, "separation", "legacy")
    assert record["outputs"] == outputs
    assert all(Path(path).read_bytes() == b"old-audio" * 100 for path in record["resolved_outputs"].values())


@pytest.mark.parametrize("marker", [[], {"kind": "translation", "signature": "corrupt", "outputs": {"segments": {"size": "oops"}}}])
def test_malformed_marker_is_invalid_not_a_crash(aliased_workspace, marker):
    _, _, video = aliased_workspace
    stage = manual_artifacts.create_staging_directory(video.video_id, "translation")
    (stage / "segments.json").write_text("[]", encoding="utf-8")
    record = manual_artifacts.publish(video.video_id, "translation", "corrupt", stage, {"segments": "segments.json"})
    (Path(record["resolved_outputs"]["segments"]).parent / "complete.json").write_text(json.dumps(marker), encoding="utf-8")
    assert manual_artifacts.peek(video.video_id, "translation", "corrupt") is None
    assert manual_artifacts.resolve(video.video_id, "translation", "corrupt") is None


def test_verification_failure_logs_real_path_and_os_error(aliased_workspace, monkeypatch):
    workspace, _, video = aliased_workspace
    stage = manual_artifacts.create_staging_directory(video.video_id, "translation")
    (stage / "segments.json").write_text("[]", encoding="utf-8")
    original_stat = Path.stat
    error = PermissionError(13, "fixture sharing violation")
    error.winerror = 32
    def stat(path, *args, **kwargs):
        if path.name == "segments.json" and path.parent.name == "unreadable":
            raise error
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", stat)
    log = Mock()
    monkeypatch.setattr(manual_artifacts.video_store, "log_to_video", log)
    with pytest.raises(RuntimeError, match="xác minh cache"):
        manual_artifacts.publish(video.video_id, "translation", "unreadable", stage, {"segments": "segments.json"})
    message = log.call_args.args[1]
    assert "fixture sharing violation" in message and "path_length=" in message
    assert "errno=13" in message and "winerror=32" in message
    assert "segments.json" in message and "kind=translation" in message
    assert "translation" not in video.active_artifacts
    assert not manual_artifacts.load_manifest(video.video_id)["artifacts"]
    manual_artifacts.assert_root_idle(workspace)


def test_corrupt_output_under_parent_alias_is_still_rejected(aliased_workspace):
    _, _, video = aliased_workspace
    stage = manual_artifacts.create_staging_directory(video.video_id, "source_audio")
    (stage / "audio.wav").write_bytes(b"valid")
    record = manual_artifacts.publish(video.video_id, "source_audio", "corrupt", stage, {"audio": "audio.wav"})
    Path(record["resolved_outputs"]["audio"]).write_bytes(b"wrong")
    assert manual_artifacts.resolve(video.video_id, "source_audio", "corrupt") is None


def test_redirect_below_cache_owner_is_still_rejected(aliased_workspace):
    workspace, physical, video = aliased_workspace
    external = physical / "not-owned"
    external.mkdir()
    (external / "audio.wav").write_bytes(b"do-not-touch")
    stage = manual_artifacts.create_staging_directory(video.video_id, "source_audio")
    (stage / "audio.wav").write_bytes(b"valid")
    record = manual_artifacts.publish(video.video_id, "source_audio", "safe", stage, {"audio": "audio.wav"})
    directory = Path(record["resolved_outputs"]["audio"]).parent
    alias = directory / "redirect"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(external)],
                       check=True, capture_output=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        alias.symlink_to(external, target_is_directory=True)
    try:
        errors = []
        assert manual_artifacts._validated_outputs(video.video_id, {
            **record, "outputs": {"audio": "redirect/audio.wav"},
        }, validation_errors=errors) is None
        assert errors and "Cannot access owned output" in errors[0]
        assert (external / "audio.wav").read_bytes() == b"do-not-touch"
    finally:
        assert alias.parent == directory and alias.resolve() == external.resolve()
        if os.name == "nt":
            assert alias.is_junction()
            alias.rmdir()
        else:
            alias.unlink()
    manual_artifacts.assert_root_idle(workspace)


def test_cancel_under_parent_alias_restores_paths_not_just_cache_references(aliased_workspace, monkeypatch):
    from linsub.services import processing_runs

    workspace, _, video = aliased_workspace
    for name, value in {"created_at": "2026-10-15T00:00:00Z", "files": {}, "checkpoints": {},
                        "manual_target_tool": "audio", "manual_completed_stages": [],
                        "manual_completed_stage": "", "status": "processing"}.items():
        setattr(video, name, value)
    (workspace / "video.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(processing_runs, "_RUNS", {})
    monkeypatch.setattr(processing_runs, "_CHECKED", set())
    processing_runs._CURRENT.run = None
    def publish(signature):
        stage = manual_artifacts.create_staging_directory(video.video_id, "audio_mix")
        (stage / "audio.wav").write_bytes(signature.encode())
        return manual_artifacts.publish(video.video_id, "audio_mix", signature, stage, {"audio": "audio.wav"})
    old = publish("before-alias-cancel")
    old_path = old["resolved_outputs"]["audio"]
    video.files = {"voice_output": old_path}
    try:
        processing_runs.begin(video.video_id)
        current = publish("during-alias-cancel")
        current_path = current["resolved_outputs"]["audio"]
        video.files = {"voice_output": current_path}
        video.status = "cancelled"
        processing_runs.request_discard(video.video_id)
        processing_runs.finish(video.video_id)
        assert video.files["voice_output"] == old_path
        assert Path(old_path).exists() and not Path(current_path).exists()
        assert video.active_artifacts == {"audio_mix": "before-alias-cancel"}
    finally:
        processing_runs._CURRENT.run = None
