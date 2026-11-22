"""Isolated YouTube adapter tests. Network/download results are explicitly simulated."""

import json
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.media import RenderCancelled
from backend.tests.test_pipeline import recordings as recordings
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.youtube_imports import YouTubeImport, YouTubeImports
from backend.youtube_network import (
    DestinationGuard,
    YouTubeDownloader,
    YouTubeTransportRH,
    canonical_video_url,
    validate_destination,
)
from backend.youtube_worker import QuietLogger, download, select_formats

VIDEO_ID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.mark.parametrize(
    "value",
    [
        URL + "&list=playlist&t=5",
        f"https://youtu.be/{VIDEO_ID}",
        f"https://m.youtube.com/shorts/{VIDEO_ID}",
        f"https://www.youtube.com/embed/{VIDEO_ID}",
    ],
)
def test_single_video_canonicalization(value):
    assert canonical_video_url(value) == URL


@pytest.mark.parametrize(
    "value",
    [
        "http://youtube.com/watch?v=abcdefghijk",
        "https://youtube.com.evil.test/watch?v=abcdefghijk",
        "https://127.0.0.1/watch?v=abcdefghijk",
        "file:///private",
        "https://youtube.com/playlist?list=123",
        "https://user:password@youtube.com/watch?v=abcdefghijk",
        "https://youtube.com:9999/watch?v=abcdefghijk",
        "https://youtu.be/abcdefghijk/extra",
        "https://youtube.com/watch?v=short",
    ],
)
def test_unsupported_urls_rejected(value):
    with pytest.raises(ValueError):
        canonical_video_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "https://youtube.com.evil.test/",
        "http://www.youtube.com/",
        "file:///private",
        "ftp://www.youtube.com/data",
        "https://127.0.0.1/",
        "https://www.google.com/",
        "https://user:pass@www.youtube.com/",
    ],
)
def test_initial_and_redirect_destinations_fail_before_dns(value, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Disallowed destination reached DNS")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    with pytest.raises(ValueError):
        DestinationGuard().default_open(urllib.request.Request(value))


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.1"]
)
def test_private_dns_results_rejected(address, monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
    )
    with pytest.raises(ValueError, match="non-public"):
        validate_destination("https://r1.googlevideo.com/video")


def test_public_destination_and_only_guarded_transport(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
    validate_destination("https://r1.googlevideo.com/video")
    with YouTubeDownloader(
        {"logger": QuietLogger(), "proxy": "", "allowed_extractors": ["youtube"], "js_runtimes": {}}
    ) as downloader:
        handlers = list(downloader._request_director.handlers.values())
        assert len(handlers) == 1 and isinstance(handlers[0], YouTubeTransportRH)
        opener = handlers[0]._create_instance({}, downloader.cookiejar)
        assert any(isinstance(handler, DestinationGuard) for handler in opener.handlers)


def format_info(height, *, audio=False, combined=False):
    return {
        "url": "https://r1.googlevideo.com/video",
        "protocol": "https",
        "ext": "m4a" if audio else "mp4",
        "vcodec": "none" if audio else "avc1.640028",
        "acodec": "mp4a.40.2" if audio or combined else "none",
        "height": None if audio else height,
        "fps": 30,
        "tbr": 100,
        "abr": 100,
    }


def test_format_selection_prefers_quality_and_avoids_merge_when_unnecessary():
    small = format_info(360, combined=True)
    large, audio = format_info(1080), format_info(None, audio=True)
    assert [name for name, _ in select_formats({"formats": [small, large, audio]})] == [
        "video.mp4",
        "audio.m4a",
    ]
    assert select_formats({"formats": [small]}) == [("video.mp4", small)]
    with pytest.raises(ValueError):
        select_formats({"formats": [{**small, "has_drm": True}]})


def test_worker_boundary_and_controlled_paths_without_live_network(tmp_path, monkeypatch):
    import backend.youtube_worker as worker

    class SyntheticDownloader:
        def __init__(self, options):
            self.options = options
            assert options["proxy"] == "" and not options["remote_components"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, url, **kwargs):
            assert url == URL and kwargs == {"download": False, "ie_key": "Youtube"}
            return {
                "title": "Source / title",
                "duration": 8,
                "formats": [format_info(360, combined=True)],
            }

        def dl(self, path, selected):
            from pathlib import Path

            Path(path).write_bytes(b"test media")
            self.options["progress_hooks"][0]({"status": "finished", "downloaded_bytes": 10})
            return True, True

    monkeypatch.setattr(worker, "YouTubeDownloader", SyntheticDownloader)
    result = download({"url": URL, "node": "node", "max_bytes": 100, "max_seconds": 10}, tmp_path)
    assert result == {"title": "Source / title", "files": ["video.mp4"]}
    assert json.loads((tmp_path / "progress.json").read_text())["downloaded_bytes"] == 10


def test_english_original_beats_higher_bitrate_dub_and_drc():
    video = format_info(1080)
    english = {
        **format_info(None, audio=True),
        "language": "en-US",
        "language_preference": 10,
        "format_id": "140-1",
        "abr": 129.475,
    }
    japanese = {
        **english,
        "language": "ja",
        "language_preference": -1,
        "format_id": "140-0",
        "abr": 129.476,
    }
    drc = {**english, "format_id": "140-drc", "abr": 130}
    assert select_formats({"formats": [video, japanese, drc, english]}) == [
        ("video.mp4", video),
        ("audio.m4a", english),
    ]


def test_unknown_combined_track_does_not_replace_explicit_english():
    combined = format_info(1080, combined=True)
    english = {**format_info(None, audio=True), "language": "en"}
    assert select_formats({"formats": [combined, english]}) == [
        ("video.mp4", combined),
        ("audio.m4a", english),
    ]


def test_known_non_english_only_audio_is_rejected():
    for track in [format_info(None, audio=True), format_info(1080, combined=True)]:
        with pytest.raises(ValueError, match="requires English audio"):
            select_formats({"formats": [format_info(1080), {**track, "language": "ja"}]})


def finished(client, identifier):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = client.get(f"/api/youtube-imports/{identifier}").json()
        if job["status"] not in {"queued", "downloading", "processing", "cancelling"}:
            return job
        time.sleep(0.02)
    pytest.fail("Import did not finish")


def test_import_publication_reuse_previews_and_restart(settings, recordings, monkeypatch):
    calls = []

    def synthetic_download(self, command, job, work, deadline, **kwargs):
        calls.append(job.id)
        shutil.copyfile(recordings[True], work / "video.mp4")
        (work / "result.json").write_text(
            json.dumps({"title": "Synthetic YouTube title", "files": ["video.mp4"]}),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(YouTubeImports, "_process", synthetic_download)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        assert (
            client.post("/api/youtube-imports", json={"url": "https://127.0.0.1/video"}).status_code
            == 422
        )
        response = client.post("/api/youtube-imports", json={"url": URL})
        assert response.status_code == 202, response.text
        job = finished(client, response.json()["id"])
        assert job["status"] == "succeeded", job
        identifier = job["recording_id"]
        source = client.get(f"/api/recordings/{identifier}").json()
        assert source["name"] == "Synthetic YouTube title.mp4" and source["has_audio"]
        assert client.get(f"/api/recordings/{identifier}/thumbnail").content.startswith(b"\xff\xd8")
        manager = client.app.state.youtube
        original_thumbnail = manager.media.thumbnail
        extractions = []

        def counted_thumbnail(*args, **kwargs):
            extractions.append(True)
            return original_thumbnail(*args, **kwargs)

        monkeypatch.setattr(manager.media, "thumbnail", counted_thumbnail)
        with ThreadPoolExecutor(max_workers=2) as pool:
            images = list(
                pool.map(
                    lambda _: client.get(f"/api/recordings/{identifier}/thumbnail?index=3"),
                    range(2),
                )
            )
        assert all(image.status_code == 200 for image in images) and len(extractions) == 1
        assert client.get(f"/api/recordings/{identifier}/thumbnail?index=6").status_code == 422
        assert client.post("/api/youtube-imports", json={"url": URL}).json()["id"] == job["id"]
        assert len(calls) == 1
        # Imports made before language-aware selection must not be silently reused.
        prior = manager.get(identifier)
        prior.audio_selection_version = 0
        manager.save(prior)
        replacement = finished(
            client, client.post("/api/youtube-imports", json={"url": URL}).json()["id"]
        )
        assert replacement["id"] != identifier
        assert replacement["audio_selection_version"] == 2
        assert len(calls) == 2
        job, identifier = replacement, replacement["recording_id"]
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as reopened:
        assert reopened.get("/api/youtube-imports/latest").json()["recording_id"] == identifier
        assert reopened.get(f"/api/recordings/{identifier}/media").status_code == 200
        assert reopened.post("/api/youtube-imports", json={"url": URL}).json()["id"] == job["id"]


def test_cancellation_busy_validation_and_recovery(settings, monkeypatch):
    entered = threading.Event()

    def held_download(self, command, job, work, deadline, **kwargs):
        entered.set()
        assert self.cancel_event.wait(10)
        raise RenderCancelled()

    monkeypatch.setattr(YouTubeImports, "_process", held_download)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        response = client.post("/api/youtube-imports", json={"url": URL})
        identifier = response.json()["id"]
        assert entered.wait(5)
        assert client.post("/api/youtube-imports", json={"url": URL}).json()["id"] == identifier
        assert (
            client.post(
                "/api/youtube-imports", json={"url": "https://youtu.be/123456789ab"}
            ).status_code
            == 409
        )
        assert client.post(f"/api/youtube-imports/{identifier}/cancel").status_code == 200
        assert finished(client, identifier)["status"] == "cancelled"
        assert client.get("/api/recordings").json() == []
        manager = client.app.state.youtube
        orphan = YouTubeImport(
            id=str(uuid4()), url=URL, status="downloading", created_at="2026-11-17"
        )
        manager.save(orphan)
        manager.initialize()
        assert manager.get(orphan.id).status == "interrupted"


def test_separate_downloaded_streams_are_actually_muxed(settings, recordings, monkeypatch):
    original_process = YouTubeImports._process

    def synthetic_download(self, command, job, work, deadline, **kwargs):
        if not kwargs.get("downloading"):
            return original_process(self, command, job, work, deadline, **kwargs)
        subprocess.run(
            [
                settings.ffmpeg,
                "-v",
                "error",
                "-i",
                str(recordings[True]),
                "-map",
                "0:v:0",
                "-c",
                "copy",
                str(work / "video.mp4"),
                "-map",
                "0:a:0",
                "-c",
                "copy",
                str(work / "audio.m4a"),
            ],
            check=True,
            capture_output=True,
            timeout=20,
        )
        (work / "result.json").write_text(
            json.dumps({"title": "Separate streams", "files": ["video.mp4", "audio.m4a"]}),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(YouTubeImports, "_process", synthetic_download)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        response = client.post("/api/youtube-imports", json={"url": URL})
        job = finished(client, response.json()["id"])
        assert job["status"] == "succeeded", job
        manager = client.app.state.youtube
        result = manager.media.probe(manager.store.source_path(job["recording_id"]))
        assert result.has_audio and result.video_codec == "h264" and result.audio_codec == "aac"
        assert abs(result.duration - 8) < 0.1
