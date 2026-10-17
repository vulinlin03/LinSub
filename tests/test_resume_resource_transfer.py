"""Real disk checks for pausing/resuming pinned resource downloads."""

import hashlib
import io
import threading

import pytest

from linsub.services import model_bootstrap as bootstrap


class Response(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(payload)
        self.status = status
        self.headers = {"Content-Length": str(len(payload))}

    def getcode(self):
        return self.status


@pytest.mark.parametrize("range_supported", [True, False])
def test_pause_keeps_verified_files_and_resume_completes_only_unfinished_download(tmp_path, monkeypatch, range_supported):
    payloads = {"first.bin": b"first complete" * 300, "second.bin": b"remaining bytes" * 80000}
    assets = [bootstrap.ModelAsset("test", name, "https://huggingface.co/test/" + name, name, len(data),
                                    hashlib.sha256(data).hexdigest()) for name, data in payloads.items()]
    cancellation = threading.Event()
    transfers = []
    def download(asset, offset):
        transfers.append((asset.relative_path, offset))
        data = payloads[asset.relative_path]
        return Response(data[offset:], 206) if offset and range_supported else Response(data)
    monkeypatch.setattr(bootstrap, "_open_download", download)
    def progress(event):
        if event.state == "downloading" and event.component == "second.bin":
            cancellation.set()
    with pytest.raises(bootstrap.ModelBootstrapCancelled):
        bootstrap.install_model_assets(tmp_path, assets, progress=progress, cancel_event=cancellation)
    completed = tmp_path / "first.bin"
    partial = tmp_path / "second.bin.part"
    assert completed.read_bytes() == payloads["first.bin"]
    assert 0 < partial.stat().st_size < len(payloads["second.bin"])
    assert not (tmp_path / "second.bin").exists()
    offset = partial.stat().st_size
    first_mtime = completed.stat().st_mtime_ns
    cancellation.clear()
    bootstrap.install_model_assets(tmp_path, assets, progress=lambda _: None, cancel_event=cancellation)
    assert transfers == [("first.bin", 0), ("second.bin", 0), ("second.bin", offset)]
    assert completed.stat().st_mtime_ns == first_mtime
    assert (tmp_path / "second.bin").read_bytes() == payloads["second.bin"]
    assert not list(tmp_path.glob("*.part"))


def test_bad_resume_prefix_is_never_promoted_and_retry_starts_clean(tmp_path, monkeypatch):
    payload = b"verified model bytes"
    asset = bootstrap.ModelAsset("test", "model", "https://huggingface.co/test/model", "model.bin", len(payload),
                                 hashlib.sha256(payload).hexdigest())
    (tmp_path / "model.bin.part").write_bytes(b"wrong")
    offsets = []
    def download(_asset, offset):
        offsets.append(offset)
        return Response(payload[offset:], 206) if offset else Response(payload)
    monkeypatch.setattr(bootstrap, "_open_download", download)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)
    bootstrap.install_model_assets(tmp_path, [asset], progress=lambda _: None)
    assert offsets == [5, 0]
    assert (tmp_path / "model.bin").read_bytes() == payload
    assert not (tmp_path / "model.bin.part").exists()
