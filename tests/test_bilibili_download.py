"""Bilibili short-read recovery without touching external services or projects."""

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

import pytest

from linsub.services import video_download


@pytest.mark.parametrize("avc_height,avc_fps,expected", [(1080, 30, "avc"), (720, 30, "av1"), (1080, 24, "av1")])
def test_bilibili_prefers_native_avc_without_reducing_resolution_or_fps(tmp_path, monkeypatch, avc_height, avc_fps, expected):
    import yt_dlp
    selected = []
    def extract(downloader, _url, **_kwargs):
        assert downloader.params["format_sort_force"]
        assert all(item["key"] != "FFmpegVideoConvertor" for item in downloader.params["postprocessors"])
        info = downloader.process_ie_result({"id": "sort-test", "title": "Sort test", "formats": [
            {"format_id": "avc", "url": "https://media.invalid/avc.mp4", "ext": "mp4", "height": avc_height,
             "fps": avc_fps, "vcodec": "avc1.640028", "acodec": "none", "quality": 1},
            {"format_id": "av1", "url": "https://media.invalid/av1.mp4", "ext": "mp4", "height": 1080,
             "fps": 30, "vcodec": "av01.0.08M.08", "acodec": "none", "quality": 2},
            {"format_id": "audio", "url": "https://media.invalid/audio.m4a", "ext": "m4a",
             "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128},
        ], "extractor": "test", "extractor_key": "Test"}, download=False)
        selected.extend(item["format_id"] for item in info["requested_formats"])
        return info
    monkeypatch.setattr(video_download, "_extract_video_info", extract)
    monkeypatch.setattr(video_download, "_load_yt_dlp", lambda: yt_dlp)
    monkeypatch.setattr(video_download, "_downloaded_video_path", lambda *_: str(tmp_path / "video.mp4"))
    monkeypatch.setattr(video_download, "_validate_processable_video", Mock())
    video_download.download_video(video_download.VideoMetadata(
        "https://www.bilibili.com/video/BV1fZGc6vEhe/", "Clip", "Bilibili", 10, "", ""), str(tmp_path))
    assert selected == [expected, "audio"]


@pytest.mark.parametrize("message", [
    "[download] Got error: 363415 bytes read, 185075571 more expected. Giving up after 3 retries",
    "[download] Got error: 0 bytes read, 185075571 more expected. Giving up after 3 retries",
    "Downloaded 1024 bytes, expected 1048576 bytes",
    "Failed to perform, curl: (92) HTTP/2 stream 1 reset by server",
])
def test_plain_yt_dlp_short_read_is_retryable(message):
    assert video_download._is_retryable_download_error(RuntimeError(message), "Bilibili")


@pytest.mark.parametrize("error", [
    PermissionError("Cannot write the downloaded bytes"),
    RuntimeError("Video unavailable. Login required. 10 bytes read, 100 more expected"),
    RuntimeError("Not available in your country. 10 bytes read, 100 more expected"),
    RuntimeError("Invalid subtitle text: bytes read more expected"),
])
def test_short_read_recovery_does_not_bypass_access_or_local_errors(error):
    assert not video_download._is_retryable_download_error(error, "Bilibili")


def test_bilibili_download_recovers_truncated_http_and_preserves_partial_ranges(tmp_path, monkeypatch):
    import yt_dlp

    payload = bytes(range(256)) * (5 * 1024 * 1024 // 256)
    ranges, attempts = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
            start = int(match[1]) if match else 0
            end = min(int(match[2]) if match and match[2] else len(payload) - 1, len(payload) - 1)
            ranges.append((start, end))
            self.send_response(206 if match else 200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(end - start + 1))
            if match:
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
            self.end_headers()
            body = payload[start:end + 1]
            if len(ranges) <= 2:
                body = body[:128 * 1024]  # Exhaust one inner retry before refreshing the URL/CDN.
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/media.mp4"
    metadata = video_download.VideoMetadata("https://www.bilibili.com/video/BV1fZGc6vEhe/", "Clip", "Bilibili", 10, "", "")
    options_for = video_download._youtube_dl_options
    def options(*args, **kwargs):
        result = options_for(*args, **kwargs)
        result.pop("impersonate", None)  # Keep this localhost transport test backend-independent.
        return result
    def extract(downloader, _url, *, download, **_kwargs):
        attempts.append(dict(downloader.params))
        return downloader.process_ie_result({
            "id": "bili-test", "title": "Clip", "url": url, "ext": "mp4", "protocol": "http",
            "vcodec": "h264", "acodec": "aac", "height": 720, "filesize": len(payload),
            "extractor": "test", "extractor_key": "Test", "webpage_url": metadata.url,
        }, download=download)
    monkeypatch.setattr(video_download, "_youtube_dl_options", options)
    monkeypatch.setattr(video_download, "_extract_video_info", extract)
    monkeypatch.setattr(video_download, "_load_yt_dlp", lambda: yt_dlp)
    monkeypatch.setattr(video_download, "_validate_processable_video", Mock())
    monkeypatch.setattr(video_download, "_wait_for_retry", lambda *_: None)
    try:
        output = video_download.download_video(metadata, str(tmp_path), cancel_event=threading.Event())
        assert Path(output).read_bytes() == payload
        assert len(attempts) == 2
        assert all(params.get("http_chunk_size") == 2 * 1024 * 1024 for params in attempts)
        assert all(params.get("continuedl") and not params.get("nopart") for params in attempts)
        assert all(not params.get("impersonate") for params in attempts)
        assert ranges[2][0] > 0, "The new extractor session must resume its owned .part, not restart at byte zero."
        assert all(end - start + 1 <= 2 * 1024 * 1024 for start, end in ranges)
        assert not list(tmp_path.glob("*.part"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_bilibili_audio_uses_small_http_ranges_without_changing_other_platforms(tmp_path, monkeypatch):
    captured = []
    class Downloader:
        def __init__(self, options):
            captured.append(options)
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def extract_info(self, *_args, **_kwargs):
            raise video_download.DownloadCancelled("test cancelled")
        def add_info_extractor(self, _extractor):
            pass
    monkeypatch.setattr(video_download, "_load_yt_dlp", lambda: type("Module", (), {"YoutubeDL": Downloader}))
    with pytest.raises(video_download.DownloadCancelled):
        video_download.download_audio("https://www.bilibili.com/video/BV1fZGc6vEhe/", tmp_path / "audio.m4a")
    assert captured[0]["http_chunk_size"] == 2 * 1024 * 1024
    assert not list(tmp_path.iterdir())
    assert "http_chunk_size" not in video_download._youtube_dl_options()


@pytest.mark.parametrize("key,backup_key", [("baseUrl", "backupUrl"), ("base_url", "backup_url")])
def test_cdn_retry_uses_advertised_backup_without_changing_format_or_source(key, backup_key):
    from copy import deepcopy
    from linsub.services.bilibili_video import LinSubBiliBiliIE
    primary = "https://primary.example/video-30080.m4s?token=primary"
    backup = "https://backup.example/video-30080.m4s?token=backup"
    play_info = {"dash": {
        "video": [{key: primary, backup_key: [backup], "id": 80, "width": 1920, "height": 1080,
                   "codecs": "avc1.640032", "mimeType": "video/mp4", "bandwidth": 1000000}],
        "audio": [{key: primary + "-audio", backup_key: [backup + "-audio"], "id": 30280,
                   "codecs": "mp4a.40.2", "mimeType": "audio/mp4", "bandwidth": 128000}],
    }}
    original = deepcopy(play_info)
    first = LinSubBiliBiliIE(cdn_attempt=0).extract_formats(play_info)
    second = LinSubBiliBiliIE(cdn_attempt=1).extract_formats(play_info)
    third = LinSubBiliBiliIE(cdn_attempt=2).extract_formats(play_info)
    assert play_info == original
    assert [item["url"] for item in second] == [backup + "-audio", backup]
    assert [item["url"] for item in third] == [item["url"] for item in first]
    assert [{k: v for k, v in item.items() if k != "url"} for item in first] == [
        {k: v for k, v in item.items() if k != "url"} for item in second]


def test_progressive_fragments_rotate_to_their_own_backup_not_another_track():
    from linsub.services.bilibili_video import LinSubBiliBiliIE
    play_info = {"durl": [
        {"url": "https://first.example/1.flv", "backup_url": ["https://backup.example/1.flv"], "length": 1000, "size": 500},
        {"url": "https://first.example/2.flv", "backup_url": ["https://backup.example/2.flv"], "length": 1000, "size": 500},
    ]}
    formats = LinSubBiliBiliIE(cdn_attempt=1).extract_formats(play_info)
    assert formats[0]["url"] == "https://backup.example/1.flv"
    assert [fragment["url"] for fragment in formats[0]["fragments"]] == [
        "https://backup.example/1.flv", "https://backup.example/2.flv"]
    assert play_info["durl"][0]["url"] == "https://first.example/1.flv"


@pytest.mark.parametrize("backup", [None, "file:///C:/private", "https://[", ["javascript:alert(1)", "https://user:secret@host/video"], {}])
def test_invalid_backup_is_never_used(backup):
    from linsub.services.bilibili_video import _stream_urls
    primary = "https://primary.example/video.m4s"
    assert _stream_urls({"baseUrl": primary, "backupUrl": backup}) == [primary]


@pytest.mark.parametrize("url", ["https://www.bilibili.com/video/BV1fZGc6vEhe/", "https://b23.tv/example"])
def test_bilibili_keeps_native_url_dispatch_including_short_link_redirects(url):
    from linsub.services.bilibili_video import LinSubBiliBiliIE
    downloader = Mock(params={"_linsub_bilibili_cdn_attempt": 1})
    video_download._extract_video_info(downloader, url, download=True)
    registered = downloader.add_info_extractor.call_args.args[0]
    assert isinstance(registered, LinSubBiliBiliIE) and registered.ie_key() == "BiliBili"
    assert registered._cdn_attempt == 1
    downloader.extract_info.assert_called_once_with(url, download=True)
