import asyncio
import errno
import subprocess
import threading
import time
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.instance import InstanceLock
from backend.main import create_app
from backend.media import Media, MediaError
from backend.models import ExportRequest
from backend.storage import Store


@pytest.fixture(scope="session")
def tools():
    return Settings.load()


@pytest.fixture(scope="session")
def recordings(tmp_path_factory, tools):
    folder = tmp_path_factory.mktemp("media")
    result = {}
    for has_audio in (True, False):
        path = folder / ("speech.mp4" if has_audio else "silent.mp4")
        command = [
            tools.ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24:duration=8",
        ]
        if has_audio:
            command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8"]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast"]
        if has_audio:
            command += ["-c:a", "aac", "-shortest"]
        subprocess.run(command + [str(path)], check=True, timeout=30, capture_output=True)
        result[has_audio] = path
    return result


@pytest.fixture
def settings(tmp_path, tools):
    return replace(tools, data_dir=tmp_path / "data")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        yield client


def import_video(client, path, name=None):
    response = client.post(
        "/api/recordings",
        params={"filename": name or path.name},
        content=path.read_bytes(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def await_export(client, clip_id):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        response = client.get(f"/api/clips/{clip_id}")
        assert response.status_code == 200
        clip = response.json()
        if clip["status"] not in {"queued", "rendering", "cancelling"}:
            return clip
        time.sleep(0.05)
    pytest.fail("Export did not finish within the test deadline.")


@pytest.mark.parametrize("has_audio,mode", [(True, "pad"), (False, "crop")])
def test_import_render_download_and_reopen(settings, recordings, has_audio, mode):
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        recording = import_video(client, recordings[has_audio], "../../safe recording.mp4")
        assert recording["name"] == "safe recording.mp4"
        assert recording["has_audio"] == has_audio
        assert (recording["width"], recording["height"]) == (320, 180)
        media = client.get(
            f"/api/recordings/{recording['id']}/media", headers={"Range": "bytes=0-99"}
        )
        assert media.status_code == 206
        assert len(media.content) == 100
        submitted = client.post(
            f"/api/recordings/{recording['id']}/clips",
            json={"start": 1.25, "end": 3.25, "mode": mode},
        )
        assert submitted.status_code == 202, submitted.text
        clip = await_export(client, submitted.json()["id"])
        assert clip["status"] == "succeeded", clip
        output = Store(settings.data_dir).export_path(clip["id"])
        info = Media(settings).probe(output)
        assert (info.width, info.height) == (1080, 1920)
        assert abs(info.duration - 2) <= 0.25
        assert info.has_audio == has_audio
        download = client.get(f"/api/clips/{clip['id']}/media?download=true")
        assert download.status_code == 200
        assert "attachment" in download.headers["content-disposition"]
        assert download.content == output.read_bytes()
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as reopened:
        assert reopened.get("/api/recordings").json()[0]["id"] == recording["id"]
        assert (
            reopened.get(f"/api/recordings/{recording['id']}/clips").json()[0]["status"]
            == "succeeded"
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"start": -1, "end": 3},
        {"start": 3, "end": 3},
        {"start": 5, "end": 3},
        {"start": 0, "end": 9},
        {"start": 0, "end": 0.01},
        {"start": "1", "end": 3},
        {"start": 0, "end": 3, "mode": "tracking"},
        {"start": 0, "end": 3, "output": "evil.mp4"},
    ],
)
def test_invalid_intervals_rejected(client, recordings, payload):
    recording = import_video(client, recordings[True])
    response = client.post(f"/api/recordings/{recording['id']}/clips", json=payload)
    assert response.status_code == 422
    assert client.get(f"/api/recordings/{recording['id']}/clips").json() == []


@pytest.mark.parametrize(
    "filename,body,status",
    [
        ("fake.mp4", b"not a video", 422),
        ("remote.m3u8", b"https://example.com", 415),
        ("script.exe", b"not a video", 415),
        ("empty.mp4", b"", 413),
    ],
)
def test_bad_import_cleans_up(client, settings, filename, body, status):
    assert (
        client.post(
            "/api/recordings",
            params={"filename": filename},
            content=body,
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == status
    )
    assert client.get("/api/recordings").json() == []
    assert list((settings.data_dir / "sources").iterdir()) == []


def test_upload_limit(settings):
    with TestClient(
        create_app(replace(settings, max_upload_bytes=4)), base_url="http://127.0.0.1:8765"
    ) as client:
        response = client.post(
            "/api/recordings?filename=large.mp4",
            content=b"12345",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 413


def test_cross_origin_and_host_rejected(client):
    assert (
        client.get("/api/recordings", headers={"Origin": "https://attacker.example"}).status_code
        == 403
    )
    assert (
        client.get("/api/recordings", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    )
    assert client.get("/api/recordings", headers={"Host": "attacker.example"}).status_code == 400
    assert (
        client.get("/api/recordings", headers={"Origin": "http://127.0.0.1:3000"}).status_code
        == 200
    )


def test_busy_and_cancel(client, recordings, settings):
    recording = import_video(client, recordings[True])
    url = f"/api/recordings/{recording['id']}/clips"
    first = client.post(url, json={"start": 0, "end": 8})
    assert first.status_code == 202
    clip_id = first.json()["id"]
    assert client.post(url, json={"start": 1, "end": 3}).status_code == 409
    cancelled = client.post(f"/api/clips/{clip_id}/cancel")
    assert cancelled.status_code == 200
    assert await_export(client, clip_id)["status"] == "cancelled"
    assert not Store(settings.data_dir).export_path(clip_id).exists()
    assert list((settings.data_dir / "exports").glob("*.partial.mp4")) == []


def test_interrupted_jobs_recovered(settings):
    store = Store(settings.data_dir)
    source_id = str(uuid4())
    with store.connection() as db:
        db.execute(
            "INSERT INTO recordings VALUES (?, 'sample.mp4', '.mp4', 8, 320, 180, 1, 100, '2026')",
            (source_id,),
        )
    clip = store.create_clip(str(uuid4()), source_id, ExportRequest(start=0, end=3))
    partial = settings.data_dir / "exports" / f"{clip.id}.partial.mp4"
    partial.write_bytes(b"incomplete")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        recovered = client.get(f"/api/clips/{clip.id}").json()
        assert recovered["status"] == "failed"
        assert "interrupted" in recovered["error"]
        assert not partial.exists()


def test_missing_recording_and_invalid_identifier(client):
    assert client.get(f"/api/recordings/{uuid4()}/media").status_code == 404
    assert client.get("/api/recordings/not-a-uuid/media").status_code == 422


def test_second_server_cannot_recover_active_data(settings):
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765"):
        other = InstanceLock(settings.data_dir)
        with pytest.raises(RuntimeError, match="Another Cliptric API"):
            other.acquire()
    other.acquire()
    other.release()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_timestamps_fail_safely(client, recordings, value):
    recording = import_video(client, recordings[True])
    response = client.post(
        f"/api/recordings/{recording['id']}/clips",
        content='{"start":' + value + ',"end":3}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_render_timeout_is_recorded(settings, recordings):
    with TestClient(
        create_app(replace(settings, render_timeout_seconds=0)), base_url="http://127.0.0.1:8765"
    ) as client:
        recording = import_video(client, recordings[True])
        response = client.post(
            f"/api/recordings/{recording['id']}/clips", json={"start": 0, "end": 8}
        )
        result = await_export(client, response.json()["id"])
        assert result["status"] == "failed"
        assert "processing limit" in result["error"]


def test_import_requires_binary_body_and_bounded_controls(client):
    assert (
        client.post("/api/recordings?filename=video.mp4", content=b"form data").status_code == 415
    )
    assert client.post(f"/api/recordings/{uuid4()}/clips", content=b"x" * 20_000).status_code == 413
    assert (
        client.get("/api/recordings", headers={"X-Forwarded-Host": "attacker.example"}).status_code
        == 400
    )


def test_disk_failure_cleans_upload_and_allows_retry(client, settings, recordings, monkeypatch):
    from backend import main

    original = main.require_space

    def disk_full(*args):
        raise OSError(errno.ENOSPC, "Test disk full")

    monkeypatch.setattr(main, "require_space", disk_full)
    response = client.post(
        "/api/recordings?filename=video.mp4",
        content=b"123",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 507
    assert list((settings.data_dir / "sources").iterdir()) == []
    monkeypatch.setattr(main, "require_space", original)
    import_video(client, recordings[True])


def test_incomplete_upload_not_saved(client, settings, recordings):
    body = recordings[True].read_bytes()
    response = client.post(
        "/api/recordings?filename=video.mp4",
        content=body,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(body) + 100),
        },
    )
    assert response.status_code == 400
    assert list((settings.data_dir / "sources").iterdir()) == []


def test_stalled_and_chunked_upload_cleanup(settings):
    async def exercise():
        app = create_app(replace(settings, upload_idle_seconds=0.02, max_upload_bytes=8))
        async with app.router.lifespan_context(app):
            for stalled in (True, False):
                sent = []
                count = 0

                async def receive():
                    nonlocal count
                    count += 1
                    if count == 1:
                        return {"type": "http.request", "body": b"1234", "more_body": True}
                    if stalled:
                        await asyncio.sleep(0.1)
                    return {"type": "http.request", "body": b"56789", "more_body": False}

                async def send(message):
                    sent.append(message)

                scope = {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "POST",
                    "scheme": "http",
                    "path": "/api/recordings",
                    "raw_path": b"/api/recordings",
                    "query_string": b"filename=video.mp4",
                    "root_path": "",
                    "server": ("127.0.0.1", 8765),
                    "client": ("127.0.0.1", 12345),
                    "headers": [
                        (b"host", b"127.0.0.1:8765"),
                        (b"content-type", b"application/octet-stream"),
                    ],
                }
                await app(scope, receive, send)
                assert next(
                    item["status"] for item in sent if item["type"] == "http.response.start"
                ) == (408 if stalled else 413)
                assert list((settings.data_dir / "sources").iterdir()) == []

    asyncio.run(exercise())


def test_anamorphic_framing_preserves_display_geometry(settings, tmp_path):
    source = tmp_path / "anamorphic.mp4"
    subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "nullsrc=size=320x240:rate=24:duration=1,geq=lum='if(mod(floor(Y/8),2),235,16)':cb=128:cr=128",
            "-vf",
            "setsar=2",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    media = Media(settings)
    assert media.probe(source).width == 640
    output = tmp_path / "padded.mp4"
    media.render(source, output, 0, 1, "pad", threading.Event(), tmp_path / "render.log")
    frame = subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-ss",
            "0.5",
            "-i",
            str(output),
            "-frames:v",
            "1",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout
    sharp_rows = sum(abs(frame[row * 1080 + 540] - 128) > 90 for row in range(1920))
    # The sharp foreground is about 405 pixels high, not 810. Blurred background
    # averages the alternating stripes, so padding does not change this measurement.
    assert 360 <= sharp_rows <= 410


def test_rotated_video_exports_upright(settings, recordings, tmp_path):
    source = tmp_path / "rotated.mp4"
    subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-display_rotation",
            "90",
            "-i",
            str(recordings[False]),
            "-c",
            "copy",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    media = Media(settings)
    info = media.probe(source)
    assert (info.width, info.height) == (180, 320)
    exported = media.render(
        source, tmp_path / "upright.mp4", 1, 2, "pad", threading.Event(), tmp_path / "rotate.log"
    )
    assert (exported.width, exported.height) == (1080, 1920)


def test_no_video_export_past_short_video_stream(settings, tmp_path):
    source = tmp_path / "long-audio.mp4"
    subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    assert Media(settings).probe(source).duration == pytest.approx(1, abs=0.1)
    with pytest.raises(MediaError, match="source duration"):
        Media(settings).render(
            source, tmp_path / "bad.mp4", 1.5, 2, "pad", threading.Event(), tmp_path / "bad.log"
        )


def test_recording_pagination(client, settings):
    store = Store(settings.data_dir)
    with store.connection() as db:
        db.executemany(
            "INSERT INTO recordings VALUES (?, ?, '.mp4', 8, 320, 180, 0, 10, ?)",
            [
                (str(uuid4()), f"video-{index}.mp4", f"2026-11-17T00:{index:03d}")
                for index in range(105)
            ],
        )
    first = client.get("/api/recordings?limit=100").json()
    second = client.get("/api/recordings?offset=100").json()
    assert len(first) == 100 and len(second) == 5
    assert len({row["id"] for row in first + second}) == 105
