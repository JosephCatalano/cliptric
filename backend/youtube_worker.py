"""Disposable downloader process. No browser cookies, plugins, remote scripts, or API keys."""

import json
import math
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from yt_dlp.downloader.hls import HlsFD
from yt_dlp.globals import plugin_dirs

from .files import write_json_atomic as write_json
from .youtube_network import YouTubeDownloader, canonical_video_url, video_provider


class QuietLogger:
    # yt-dlp diagnostics can contain signed URLs. Return only controlled errors.
    def debug(self, message):
        return None

    def warning(self, message):
        return None

    def error(self, message):
        return None


def audio_rank(format: dict) -> tuple:
    # This release transcribes English. A dub's marginally higher bitrate must
    # never outrank an English original (yt-dlp marks originals with preference 10).
    language = (format.get("language") or "und").lower().replace("_", "-")
    supported = (
        2 if language == "en" or language.startswith("en-") else 1 if language == "und" else 0
    )
    return (
        supported,
        format.get("language_preference", -1)
        if format.get("language_preference") is not None
        else -1,
        not (format.get("format_id") or "").endswith("-drc"),
        format.get("abr") or format.get("tbr") or 0,
    )


def select_formats(info: dict):
    formats = [
        f
        for f in info.get("formats", [])
        if not f.get("has_drm")
        and f.get("protocol") == "https"
        and urlsplit(f.get("url", "")).scheme == "https"
    ]
    video = [
        f
        for f in formats
        if (f.get("vcodec") or "").startswith("avc1")
        and 0 < (f.get("height") or 0) <= 1080
        and (f.get("fps") or 30) <= 60
    ]
    audio = [
        f
        for f in formats
        if f.get("vcodec") == "none"
        and f.get("ext") == "m4a"
        and (f.get("acodec") or "").startswith("mp4a")
    ]
    if not video:
        raise ValueError("No supported video format")
    best = max(video, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
    combined = [f for f in video if (f.get("acodec") or "").startswith("mp4a")]
    tracks = audio + combined
    if tracks:
        language_rank = max(audio_rank(f)[:2] for f in tracks)
        if language_rank[0] == 0:
            raise ValueError(
                "This release requires English audio. Upload an English version instead."
            )
        audio = [f for f in audio if audio_rank(f)[:2] == language_rank]
        combined = [f for f in combined if audio_rank(f)[:2] == language_rank]
    if combined:
        choice = max(combined, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
        if choice.get("height", 0) >= best.get("height", 0) or not audio:
            return [("video.mp4", choice)]
    if not audio:
        raise ValueError("No supported audio format")
    return [
        ("video.mp4", best),
        ("audio.m4a", max(audio, key=audio_rank)),
    ]


def select_twitch_format(info):
    formats = [
        format
        for format in info.get("formats", [])
        if format.get("protocol") == "m3u8_native"
        and urlsplit(format.get("url", "")).scheme == "https"
        and not format.get("has_drm")
        and (format.get("vcodec") or "").startswith(("avc1", "h264"))
        and (format.get("acodec") or "").startswith(("mp4a", "aac"))
        and 0 < (format.get("height") or 0) <= 1080
        and 0 < (format.get("fps") or 30) <= 60
        and audio_rank(format)[0] > 0
    ]
    if not formats:
        raise ValueError("No supported public H.264/AAC Twitch VOD format")
    best = max(formats, key=lambda f: (audio_rank(f)[:2], f.get("height") or 0, f.get("tbr") or 0))
    return [("video.ts", best)]


def download_twitch_hls(downloader, selected, output, progress):
    # Inspect a bounded complete media playlist before invoking the native
    # downloader. Unsupported/encrypted playlists must never fall back to a
    # network-enabled FFmpeg process, outside our destination guard.
    with downloader.urlopen(selected["url"]) as response:
        raw = response.read(2 * 1024**2 + 1)
    if len(raw) > 2 * 1024**2:
        raise ValueError("Twitch playlist exceeded its size limit")
    manifest = raw.decode("utf-8-sig")
    validate_twitch_manifest(manifest, selected)
    native = HlsFD(downloader, downloader.params)
    native.add_progress_hook(progress)
    return native.download(
        str(output),
        {
            **selected,
            "extractor_key": "TwitchVod",
            "is_live": False,
            "hls_media_playlist_data": manifest,
        },
    )


def validate_twitch_manifest(manifest, selected):
    lines = [line.strip() for line in manifest.splitlines()]
    unsupported = any(
        line.startswith(("#EXT-X-SESSION-KEY", "#EXT-X-MAP", "#ANVATO-SEGMENT", "#UPLYNK-SEGMENT"))
        or (line.startswith("#EXT-X-KEY") and line != "#EXT-X-KEY:METHOD=NONE")
        for line in lines
    )
    if (
        not manifest.startswith("#EXTM3U")
        or "#EXT-X-ENDLIST" not in lines
        or unsupported
        or selected.get("hls_aes")
        or not HlsFD.can_download(manifest, selected)
    ):
        raise ValueError("Only complete, unencrypted native Twitch VOD playlists are supported")


def download(request: dict, root: Path):
    platform = video_provider(request["url"])
    plugin_dirs.value = []
    last_update = 0.0
    completed_bytes = 0
    total_bytes = None

    def progress(value):
        nonlocal last_update
        if time.monotonic() - last_update < 0.25 and value["status"] != "finished":
            return
        downloaded = completed_bytes + int(value.get("downloaded_bytes") or 0)
        if downloaded > request["max_bytes"]:
            raise ValueError("Source exceeded size limit")
        write_json(
            root / "progress.json", {"downloaded_bytes": downloaded, "total_bytes": total_bytes}
        )
        last_update = time.monotonic()

    options = {
        "quiet": True,
        "no_warnings": True,
        "logger": QuietLogger(),
        "noplaylist": True,
        "allowed_extractors": ["twitch:vod"] if platform == "twitch" else ["youtube"],
        "proxy": "",
        "socket_timeout": 15,
        "retries": 0,
        "fragment_retries": 0,
        "extractor_retries": 0,
        "cachedir": False,
        "remote_components": [],
        "plugin_dirs": [],
        "js_runtimes": {"node": {"path": request["node"]}} if platform == "youtube" else {},
        "progress_hooks": [progress],
        "max_filesize": request["max_bytes"],
        "overwrites": False,
        "continuedl": False,
        "concurrent_fragment_downloads": 1,
        "external_downloader": {"default": "native"},
        "skip_unavailable_fragments": False,
    }
    with YouTubeDownloader(options) as downloader:
        info = downloader.extract_info(
            canonical_video_url(request["url"]),
            download=False,
            ie_key="TwitchVod" if platform == "twitch" else "Youtube",
        )
        duration = info.get("duration")
        if (
            info.get("_type", "video") != "video"
            or info.get("is_live")
            or info.get("live_status") in {"is_live", "is_upcoming"}
            or type(duration) not in (float, int)
            or not math.isfinite(duration)
            or not 0 < duration <= request["max_seconds"]
            or (platform == "twitch" and info.get("is_live") is not False)
        ):
            raise ValueError("Unsupported live, unavailable, or oversized video")
        formats = select_twitch_format(info) if platform == "twitch" else select_formats(info)
        title = info.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Video title unavailable")
        if request.get("metadata_only"):
            return {
                "name": title[:200],
                "duration": duration,
                "has_audio": True,
                **(
                    {
                        "preview_image_url": info.get("thumbnail")
                        or next(
                            (
                                item.get("url")
                                for item in info.get("thumbnails", [])
                                if item.get("url")
                            ),
                            None,
                        )
                    }
                    if platform == "twitch"
                    else {}
                ),
            }
        sizes = [f.get("filesize") or f.get("filesize_approx") for _, f in formats]
        if all(type(size) in (int, float) and size > 0 for size in sizes):
            total_bytes = int(sum(sizes))
            if total_bytes > request["max_bytes"]:
                raise ValueError("Source exceeded size limit")
        for filename, selected in formats:
            success, _ = (
                download_twitch_hls(
                    downloader,
                    {**selected, "http_headers": info.get("http_headers", {})},
                    root / filename,
                    progress,
                )
                if platform == "twitch"
                else downloader.dl(
                    str(root / filename), {**selected, "http_headers": info.get("http_headers", {})}
                )
            )
            path = root / filename
            if not success or not path.is_file() or path.stat().st_size <= 0:
                raise ValueError("Download did not produce media")
            completed_bytes += path.stat().st_size
            if completed_bytes > request["max_bytes"]:
                raise ValueError("Source exceeded size limit")
        return {"title": title[:200], "files": [name for name, _ in formats]}


def main():
    root = Path(sys.argv[1]).resolve()
    try:
        request = json.loads((root / "request.json").read_text(encoding="utf-8"))
        result = download(request, root)
    except Exception:
        write_json(
            root / "result.json",
            {
                "error": "Could not import supported public YouTube or Twitch footage with English audio. Check the link or upload the recording. Private, restricted, live, encrypted and DRM-protected footage is not imported."
            },
        )
        return 1
    write_json(root / "result.json", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
