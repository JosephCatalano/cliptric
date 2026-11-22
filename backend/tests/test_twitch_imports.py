"""Twitch network metadata is simulated; MPEG-TS to MP4 uses real local FFmpeg."""

import io
import json
import shutil
import socket
import subprocess
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from yt_dlp.downloader.external import FFmpegFD
from yt_dlp.networking import Response

from backend.config import Settings
from backend.main import create_app
from backend.tests.test_deferred_projects import deferred as deferred
from backend.tests.test_pipeline import recordings as recordings
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_projects import context as context
from backend.tests.test_projects import finished as finished_project
from backend.tests.test_youtube_imports import finished
from backend.youtube_imports import YouTubeImports, YouTubeSource
from backend.youtube_network import (
    TwitchTransportRH,
    YouTubeDownloader,
    canonical_video_url,
    validate_destination,
)
from backend.youtube_worker import (
    QuietLogger,
    download,
    download_twitch_hls,
    select_twitch_format,
    validate_twitch_manifest,
)

URL = "https://www.twitch.tv/videos/123456789"
CDN = "https://vod-secure.twitch.tv/fixture/chunked/"
MANIFEST = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\n0.ts\n#EXTINF:4,\n1.ts\n#EXT-X-ENDLIST\n"


def format_info(**updates):
    return {
        "url": CDN + "index-dvr.m3u8",
        "protocol": "m3u8_native",
        "ext": "mp4",
        "vcodec": "avc1.64002a",
        "acodec": "mp4a.40.2",
        "height": 1080,
        "fps": 60,
        "tbr": 6000,
        **updates,
    }


@pytest.mark.parametrize(
    "url",
    [
        URL,
        URL + "?t=1h2m",
        URL.replace("www.", "m."),
        "https://player.twitch.tv/?video=123456789&parent=example.com",
    ],
)
def test_twitch_vod_canonicalization(url):
    assert canonical_video_url(url) == URL


@pytest.mark.parametrize(
    "url",
    [
        "https://www.twitch.tv/some_channel",
        "https://clips.twitch.tv/AClip",
        "https://twitch.tv/videos/123/extra",
        "https://twitch.tv.evil.test/videos/123",
        "http://twitch.tv/videos/123",
        "https://user:pass@twitch.tv/videos/123",
        "https://twitch.tv:444/videos/123",
        "https://127.0.0.1/videos/123",
        "https://player.twitch.tv/?channel=someone",
    ],
)
def test_non_vod_inputs_rejected(url):
    with pytest.raises(ValueError):
        canonical_video_url(url)


def test_twitch_destinations_remain_public_and_separate_from_youtube(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("8.8.8.8", 443))]
    )
    for host in (
        "gql.twitch.tv",
        "usher.ttvnw.net",
        "static-cdn.jtvnw.net",
        "fixture.cloudfront.net",
    ):
        validate_destination(f"https://{host}/video", "twitch")
        with pytest.raises(ValueError):
            validate_destination(f"https://{host}/video")
    for url in (
        "http://gql.twitch.tv/gql",
        "https://twitch.tv.evil.test/",
        "file:///private",
        "https://youtube.com/",
        "https://127.0.0.1/",
    ):
        with pytest.raises(ValueError):
            validate_destination(url, "twitch")
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 443))]
    )
    with pytest.raises(ValueError, match="non-public"):
        validate_destination(CDN, "twitch")


def test_twitch_uses_only_guarded_transport():
    with YouTubeDownloader(
        {"quiet": True, "logger": QuietLogger(), "proxy": "", "allowed_extractors": ["twitch:vod"]}
    ) as downloader:
        handlers = list(downloader._request_director.handlers.values())
        assert len(handlers) == 1 and isinstance(handlers[0], TwitchTransportRH)


def test_h264_aac_format_selection_and_restrictions():
    supported = format_info()
    assert select_twitch_format(
        {"formats": [format_info(vcodec="av01"), format_info(height=2160), supported]}
    ) == [("video.ts", supported)]
    for changes in (
        {"has_drm": True},
        {"protocol": "m3u8"},
        {"url": "http://vod-secure.twitch.tv/video"},
        {"acodec": "none"},
        {"language": "ja"},
    ):
        with pytest.raises(ValueError):
            select_twitch_format({"formats": [format_info(**changes)]})


@pytest.mark.parametrize(
    "manifest",
    [
        MANIFEST.replace("#EXT-X-ENDLIST", ""),
        MANIFEST.replace("#EXTM3U", "not a playlist"),
        MANIFEST.replace(
            "#EXTINF:4,", '#EXT-X-KEY:METHOD=AES-128,URI="https://evil.example/key"\n#EXTINF:4,', 1
        ),
        MANIFEST.replace("#EXTINF:4,", '#EXT-X-MAP:URI="init.mp4"\n#EXTINF:4,', 1),
        MANIFEST.replace(
            "#EXTINF:4,", '#EXT-X-KEY:METHOD=NONE,METHOD=AES-128,URI="key"\n#EXTINF:4,', 1
        ),
    ],
)
def test_unsupported_hls_is_rejected_before_downloading(manifest):
    with pytest.raises(ValueError):
        validate_twitch_manifest(manifest, format_info())


def test_native_fragments_are_downloaded_without_external_downloaders(tmp_path, monkeypatch):
    calls, progress = [], []

    def no_external(*args, **kwargs):
        raise AssertionError("The native downloader must never start an external process")

    monkeypatch.setattr(FFmpegFD, "real_download", no_external)
    with YouTubeDownloader(
        {
            "quiet": True,
            "logger": QuietLogger(),
            "proxy": "",
            "allowed_extractors": ["twitch:vod"],
            "external_downloader": {"default": "native"},
            "skip_unavailable_fragments": False,
            "retries": 0,
            "fragment_retries": 0,
        }
    ) as downloader:

        def open_url(request):
            url = request if isinstance(request, str) else request.url
            calls.append(url)
            data = (
                MANIFEST.encode()
                if url.endswith("m3u8")
                else b"a" * 188
                if url.endswith("0.ts")
                else b"b" * 188
            )
            return Response(io.BytesIO(data), url, {"Content-Length": str(len(data))})

        monkeypatch.setattr(downloader, "urlopen", open_url)
        output = tmp_path / "video.ts"
        success, _ = download_twitch_hls(downloader, format_info(), output, progress.append)
    assert success and output.read_bytes() == b"a" * 188 + b"b" * 188
    assert calls == [CDN + "index-dvr.m3u8", CDN + "0.ts", CDN + "1.ts"]
    assert progress[-1]["status"] == "finished"


@pytest.mark.parametrize("live", [False, True, None])
def test_twitch_metadata_worker_uses_only_vod_extractor(tmp_path, monkeypatch, live):
    import backend.youtube_worker as worker

    class SimulatedDownloader:
        def __init__(self, options):
            assert (
                options["allowed_extractors"] == ["twitch:vod"]
                and options["skip_unavailable_fragments"] is False
            )

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, url, **kwargs):
            assert url == URL and kwargs["ie_key"] == "TwitchVod" and kwargs["download"] is False
            return {
                "title": "Simulated VOD",
                "duration": 80,
                "is_live": live,
                "formats": [format_info()],
                "thumbnails": [{"url": "https://static-cdn.jtvnw.net/fixture.jpg"}],
            }

    monkeypatch.setattr(worker, "YouTubeDownloader", SimulatedDownloader)
    request = {
        "url": URL,
        "max_bytes": 20 * 1024**3,
        "max_seconds": 21600,
        "node": "node",
        "metadata_only": True,
    }
    if live is not False:
        with pytest.raises(ValueError):
            download(request, tmp_path)
        return
    result = download(request, tmp_path)
    assert result["duration"] == 80 and result["preview_image_url"].endswith("fixture.jpg")


def test_twitch_preview_uses_generic_source_route(settings, monkeypatch):
    def metadata(self, command, job, work, deadline, **kwargs):
        (work / "result.json").write_text(
            json.dumps(
                {
                    "name": "Twitch VOD",
                    "duration": 80,
                    "has_audio": True,
                    "preview_image_url": "https://static-cdn.jtvnw.net/fixture.jpg",
                }
            )
        )
        return 0

    monkeypatch.setattr(YouTubeImports, "_process", metadata)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        response = client.post("/api/video-sources", json={"url": URL})
        assert response.status_code == 201, response.text
        source = response.json()
        assert source["url"] == URL and source["preview_image_url"].endswith("fixture.jpg")
        assert client.get(f"/api/video-sources/{source['id']}").json() == source
        assert client.get(f"/api/youtube-sources/{source['id']}").json() == source
        assert client.get("/api/recordings").json() == []


def test_real_twitch_transport_stream_is_remuxed_locally(settings, recordings, monkeypatch):
    original = YouTubeImports._process

    def simulated_download(self, command, job, work, deadline, **kwargs):
        if not kwargs.get("downloading"):
            assert "file,pipe" in command and "mpegts" in command
            assert all(not part.startswith(("https://", "http://")) for part in command)
            return original(self, command, job, work, deadline, **kwargs)
        subprocess.run(
            [
                settings.ffmpeg,
                "-v",
                "error",
                "-nostdin",
                "-i",
                str(recordings[True]),
                "-c",
                "copy",
                "-f",
                "mpegts",
                str(work / "video.ts"),
            ],
            capture_output=True,
            check=True,
            timeout=20,
        )
        (work / "result.json").write_text(
            json.dumps({"title": "Simulated Twitch VOD", "files": ["video.ts"]})
        )
        return 0

    monkeypatch.setattr(YouTubeImports, "_process", simulated_download)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        response = client.post("/api/video-imports", json={"url": URL})
        assert response.status_code == 202, response.text
        job = finished(client, response.json()["id"])
        assert job["status"] == "succeeded", job
        manager = client.app.state.youtube
        info = manager.media.probe(manager.store.source_path(job["recording_id"]))
        assert info.has_audio and info.video_codec == "h264" and info.audio_codec == "aac"
        assert (info.width, info.height) == (320, 180) and abs(info.duration - 8) < 0.1
        assert client.post("/api/video-imports", json={"url": URL}).json()["id"] == job["id"]


def test_large_default_and_configurable_limits(settings, monkeypatch):
    assert Settings.__dataclass_fields__["max_upload_bytes"].default == 20 * 1024**3
    monkeypatch.setenv("CLIPPING_MAX_UPLOAD_GIB", "40")
    assert Settings.load().max_upload_bytes == 40 * 1024**3
    for invalid in ("0", "101", "-1", "2.5", "bad"):
        monkeypatch.setenv("CLIPPING_MAX_UPLOAD_GIB", invalid)
        with pytest.raises(ValueError):
            Settings.load()
    import backend.main as main

    monkeypatch.setattr(main, "require_space", lambda *args: None)
    with TestClient(
        create_app(replace(settings, max_upload_bytes=20 * 1024**3)),
        base_url="http://127.0.0.1:8765",
    ) as client:
        assert client.get("/api/health").json()["max_upload_bytes"] == 20 * 1024**3
        # A declared 3-GiB upload gets past the size check; this deliberately
        # short body then fails the incomplete-transfer check without allocating GiBs.
        response = client.post(
            "/api/recordings?filename=large.mp4",
            headers={
                "content-length": str(3 * 1024**3),
                "content-type": "application/octet-stream",
            },
            content=b"explicit incomplete transfer",
        )
        assert response.status_code == 400 and "incomplete" in response.json()["detail"]
        assert (
            client.post(
                "/api/recordings?filename=oversized.mp4",
                headers={
                    "content-length": str(21 * 1024**3),
                    "content-type": "application/octet-stream",
                },
                content=b"x",
            ).status_code
            == 413
        )


def test_untrusted_preview_image_rejected():
    with pytest.raises(ValueError):
        YouTubeSource(
            id="fixture",
            url=URL,
            name="VOD",
            duration=80,
            has_audio=True,
            preview_image_url="http://127.0.0.1/private",
        )


def test_twitch_setup_download_is_reused_for_project_pipeline(deferred):
    client, _, _, request, events, _, _, _, provider, _ = deferred
    events.clear()
    response = client.post("/api/video-sources", json={"url": URL})
    assert response.status_code == 201 and events == ["preview"]
    source = response.json()
    download_response = client.post("/api/video-imports", json={"url": source["url"]})
    assert download_response.status_code == 202
    downloaded = finished(client, download_response.json()["id"])
    assert downloaded["status"] == "succeeded" and events == ["preview", "download"]
    assert not provider.calls
    request["youtube_source_id"] = source["id"]
    response = client.post("/api/projects", json=request)
    assert response.status_code == 202, response.text
    project = finished_project(client, response.json()["id"])
    assert project["status"] == "ready" and project["items"]
    assert project["recording_id"] == downloaded["recording_id"]
    assert events.count("download") == 1 and events.count("transcribe") == 1
