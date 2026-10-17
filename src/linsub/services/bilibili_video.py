"""Use only Bilibili-provided alternate media URLs on a bounded retry.

The pinned yt-dlp extractor exposes only baseUrl, dropping backupUrl from the
same playback response. Preserve its formats/codecs/headers and rotate that
one stream to its advertised backup, without inventing CDN hosts or signing.
"""

from urllib.parse import urlparse

from yt_dlp.extractor.bilibili import BiliBiliIE


def _stream_urls(stream):
    primary = stream.get("baseUrl") or stream.get("base_url") or stream.get("url")
    backups = stream.get("backupUrl") or stream.get("backup_url") or []
    if isinstance(backups, str):
        backups = [backups]
    if not isinstance(backups, list):
        backups = []
    urls = []
    for value in [primary, *backups]:
        if not isinstance(value, str):
            continue
        try:
            parsed = urlparse(value)
        except ValueError:
            continue
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            if value not in urls:
                urls.append(value)
    return urls


class LinSubBiliBiliIE(BiliBiliIE):
    def __init__(self, downloader=None, *, cdn_attempt=0):
        super().__init__(downloader)
        self._cdn_attempt = max(0, int(cdn_attempt))

    @classmethod
    def ie_key(cls):
        # Replace only the standard single-video extractor. b23 redirects and
        # every other supported URL keep yt-dlp's normal dispatch/validation.
        return BiliBiliIE.ie_key()

    def extract_formats(self, play_info):
        formats = super().extract_formats(play_info)
        if not self._cdn_attempt:
            return formats
        dash = play_info.get("dash") or {}
        streams = [*(dash.get("video") or []), *(dash.get("audio") or []),
                   *((dash.get("dolby") or {}).get("audio") or []),
                   *(play_info.get("durl") or [])]
        flac_audio = (dash.get("flac") or {}).get("audio")
        if isinstance(flac_audio, dict):
            streams.append(flac_audio)
        alternates = {}
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            urls = _stream_urls(stream)
            if urls:
                alternates[urls[0]] = urls[self._cdn_attempt % len(urls)]
        for item in formats:
            item["url"] = alternates.get(item.get("url"), item.get("url"))
            for fragment in item.get("fragments") or []:
                fragment["url"] = alternates.get(fragment.get("url"), fragment.get("url"))
        return formats
