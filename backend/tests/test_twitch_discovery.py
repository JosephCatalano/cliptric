"""The application routes, SQLite worker and supplied-video runner with simulated Twitch."""

import shutil
import subprocess
import time
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.tests.test_pipeline import recordings as recordings
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.twitch_helix import TwitchClip, TwitchError
from backend.twitch_publishing import publishing_error

ROOT = "/api/twitch-discovery"


def poll(client, path, predicate):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        value = response.json()
        if predicate(value):
            return value
        time.sleep(0.1)
    raise AssertionError(value)


def clip(identifier="test-clip", views=500, broadcaster="42"):
    return TwitchClip(
        id=identifier,
        broadcaster_id=broadcaster,
        broadcaster_name="Test broadcaster",
        title="Fixture Twitch clip",
        created_at=datetime.now(UTC),
        duration=3,
        view_count=views,
        language="en",
    )


def scan_config(**options):
    return {
        "name": "Test discovery",
        "source": "game",
        "game_id": "32982",
        "game_name": "Grand Theft Auto V",
        **options,
    }


def setup_api(app, monkeypatch, paged):
    service = app.state.twitch_discovery
    monkeypatch.setattr(service.api, "client_id", "test-id")
    monkeypatch.setattr(service.api, "secret", "test-secret")
    monkeypatch.setattr(service.api, "page", paged)
    return service


def run_scan(client, saved):
    started = client.post(f"{ROOT}/scans/{saved['id']}/run")
    assert started.status_code == 202, started.text
    return poll(
        client,
        f"{ROOT}/scans/{saved['id']}",
        lambda value: value["runs"] and value["runs"][0]["status"] not in {"queued", "running"},
    )


def test_automatic_preparation_uses_only_the_top_20_results(settings, monkeypatch):
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        service = setup_api(
            client.app,
            monkeypatch,
            lambda _: ([clip(f"clip-{rank}", views=rank) for rank in range(1, 26)], None, 0),
        )
        attempted = []

        def enqueue(_scan, _settings, candidates, **_):
            attempted.extend(item.id for item in candidates)
            return len(candidates)

        monkeypatch.setattr(service.intake, "enqueue", enqueue)
        scan = client.post(
            f"{ROOT}/scans", json=scan_config(auto_prepare=True, limit=20, import_limit=20)
        )
        assert scan.status_code == 201, scan.text
        result = run_scan(client, scan.json())
        assert attempted == [row["id"] for row in result["clips"]]
        assert attempted == [f"clip-{rank}" for rank in range(25, 5, -1)]


def test_routes_resolve_edit_filter_review_and_persist(settings, monkeypatch):
    app = create_app(settings)
    calls = []

    def page(params):
        calls.append(dict(params))
        return [clip(), clip("small", 5), clip("second", 99, "108")], None, 0

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        service = setup_api(app, monkeypatch, page)
        monkeypatch.setattr(
            service.api, "broadcaster_ids", lambda names: {"test_streamer": "42"} if names else {}
        )
        response = client.post(
            f"{ROOT}/scans",
            json=scan_config(
                include=["https://www.twitch.tv/test_streamer"],
                minimum_views=100,
            ),
        )
        assert response.status_code == 201, response.text
        saved = response.json()
        completed = run_scan(client, saved)
        assert [item["id"] for item in completed["clips"]] == ["test-clip"]
        assert calls[0]["game_id"] == "32982" and "started_at" in calls[0]
        path = f"{ROOT}/scans/{saved['id']}"
        assert (
            client.post(
                f"{path}/review", json={"ids": ["test-clip", "missing"], "state": "hidden"}
            ).status_code
            == 404
        )
        assert client.get(path).json()["clips"][0]["state"] == "new"
        assert (
            client.post(
                f"{path}/review", json={"ids": ["test-clip"], "state": "hidden"}
            ).status_code
            == 204
        )
        changed = client.put(
            f"{path}?revision=1", json=scan_config(enabled=True, interval_minutes=10)
        )
        assert changed.status_code == 200 and changed.json()["next_run"]
        assert client.put(f"{path}?revision=1", json=scan_config()).status_code == 409
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        persisted = client.get(path).json()
        assert persisted["revision"] == 2
        assert persisted["clips"][0]["state"] == "hidden"
        assert client.delete(path).status_code == 204
        assert client.get(path).status_code == 404


def test_partial_rate_limit_and_coalesced_schedule(settings, monkeypatch):
    app = create_app(settings)
    hit = []

    def page(params):
        hit.append(params)
        if len(hit) > 1:
            raise TwitchError(429, "Twitch rate limit reached.")
        return [clip()], "next", 0

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        service = setup_api(app, monkeypatch, page)
        response = client.post(f"{ROOT}/scans", json=scan_config(enabled=True))
        assert response.status_code == 201, response.text
        saved = response.json()
        with service.store.connection() as db:
            db.execute("UPDATE twitch_scans SET next_run=?", (time.time() - 4 * 3600,))
        service.wake.set()
        result = poll(
            client,
            f"{ROOT}/scans/{saved['id']}",
            lambda data: bool(data["runs"]) and data["runs"][0]["status"] == "partial",
        )
        assert len(result["runs"]) == 1 and len(result["clips"]) == 1
        assert "rate limit" in result["runs"][0]["detail"]
        assert datetime.fromisoformat(result["next_run"]).timestamp() > time.time() + 3500


def test_authorized_intake_makes_real_silent_portrait_and_guards_publishing(
    settings, monkeypatch, tmp_path
):
    fixture = tmp_path / "permitted.mp4"
    subprocess.run(
        [
            settings.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=25:d=1",
            "-an",
            "-c:v",
            "libx264",
            str(fixture),
        ],
        check=True,
    )
    app = create_app(replace(settings, render_encoder="cpu"))
    downloads = []
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        service = setup_api(app, monkeypatch, lambda _: ([clip()], None, 0))
        intake = service.intake

        def download(_url, dest, _limit, _stop):
            downloads.append(dest)
            shutil.copyfile(fixture, dest)
            return fixture.stat().st_size

        monkeypatch.setattr(intake, "download", download)
        monkeypatch.setattr(
            intake.editor, "downloads", lambda *args: {"test-clip": "https://example.test/media"}
        )
        with service.store.connection() as db:
            db.execute(
                "INSERT INTO twitch_editor VALUES(1,'42','fixture',?,?)",
                (b"test credential", time.time()),
            )
        saved = client.post(f"{ROOT}/scans", json=scan_config()).json()
        run_scan(client, saved)
        path = f"{ROOT}/scans/{saved['id']}"
        queued = client.post(f"{path}/prepare", json={"ids": ["test-clip"]})
        assert queued.status_code == 202, queued.text
        assert client.post(f"{path}/prepare", json={"ids": ["test-clip"]}).status_code == 409
        detail = poll(
            client,
            path,
            lambda data: data["intake"][0]["project_status"] in {"ready", "failed", "partial"},
        )
        created = client.get(f"/api/projects/{detail['intake'][0]['project_id']}").json()
        assert created["status"] == "ready", created
        assert client.delete(f"{ROOT}/imports/test-clip").status_code == 409
        assert created["items"][0]["caption_preset"] == "none"
        export_id = created["items"][0]["export_id"]
        exported = intake.media.probe(service.store.export_path(export_id))
        assert (exported.width, exported.height, exported.has_audio) == (1080, 1920, False)
        assert len(downloads) == 1
        owner, destination = str(uuid4()), "a" * 64
        assert (
            publishing_error(service.store, export_id, "youtube", destination, owner, reserve=True)
            is None
        )
        assert "already" in publishing_error(
            service.store, export_id, "youtube", destination, uuid4()
        )
        assert client.post(f"{ROOT}/disconnect").status_code == 204
        assert publishing_error(service.store, export_id, "youtube", destination, owner) is None
        recording_id = created["items"][0]["recording_id"]
        source = service.store.source_path(recording_id)
        project_removed = client.delete(f"/api/projects/{created['id']}")
        assert project_removed.status_code == 200, project_removed.text
        purged = client.delete(f"{ROOT}/imports/test-clip")
        assert purged.status_code == 200, purged.text
        assert not source.exists() and service.store.recording(recording_id) is None
        assert client.get(path).json()["clips"] == []
        with service.store.connection() as db:
            assert db.execute("SELECT count(*) FROM twitch_post_claims").fetchone()[0] == 1


def test_editor_oauth_binds_callback_and_expires_revoked_session(settings, monkeypatch):
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        service = setup_api(app, monkeypatch, lambda _: ([], None, 0))
        editor = service.intake.editor
        rejected = False

        def oauth(method, url, **options):
            assert url.startswith("https://id.twitch.tv/oauth2/")
            if rejected:
                raise TwitchError(401 if method == "GET" else 400, "Revoked test token.")
            if method == "POST":
                assert options["data"]["grant_type"] == "authorization_code"
                return {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh"}
            assert options["headers"]["Authorization"] == "OAuth synthetic-access"
            return {
                "user_id": "42",
                "login": "fixture",
                "client_id": "test-id",
                "scopes": ["channel:manage:clips"],
            }

        monkeypatch.setattr(service.api, "_json", oauth)
        assert (
            client.get(f"{ROOT}/callback", params={"state": "wrong", "code": "test"}).status_code
            == 400
        )
        start = client.post(f"{ROOT}/connect", headers={"Origin": settings.web_origin})
        assert start.status_code == 200, start.text
        state = client.cookies["twitch_oauth_state"]
        finished = client.get(
            f"{ROOT}/callback", params={"state": state, "code": "test"}, follow_redirects=False
        )
        assert finished.status_code == 303, finished.text
        assert client.get(ROOT).json()["editor"]["profile"]["login"] == "fixture"
        assert (
            client.get(f"{ROOT}/callback", params={"state": state, "code": "test"}).status_code
            == 400
        )
        with service.store.connection() as db:
            stored = db.execute("SELECT credential FROM twitch_editor").fetchone()[0]
            assert b"synthetic-access" not in stored
            db.execute("UPDATE twitch_editor SET validated=0")
        rejected = True
        editor.next_check = 0
        editor.validate_due()
        assert client.get(ROOT).json()["editor"]["profile"] is None


def test_editor_link_import_requires_twitch_download_access_and_reuses_recording(
    settings, recordings, monkeypatch
):
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        calls = []
        downloads = []

        def page(params):
            calls.append(params)
            return [clip()], None, 0

        service = setup_api(app, monkeypatch, page)
        endpoint = f"{ROOT}/editor-imports"
        share_url = "https://clips.twitch.tv/test-clip?tt_content=share"
        assert (
            client.post(
                endpoint, json={"url": "https://clips.twitch.tv.evil.test/test-clip"}
            ).status_code
            == 422
        )
        assert not calls
        assert client.post(endpoint, json={"url": share_url}).status_code == 403
        assert calls == [{"id": "test-clip"}]

        intake = service.intake
        with service.store.connection() as db:
            db.execute(
                "INSERT INTO twitch_editor VALUES(1,'42','fixture',?,?)",
                (b"test credential", time.time()),
            )
        monkeypatch.setattr(intake.editor, "downloads", lambda *_: {})

        def download(_url, dest, _limit, _stop):
            downloads.append(dest)
            shutil.copyfile(recordings[False], dest)
            return dest.stat().st_size

        monkeypatch.setattr(intake, "download", download)
        queued = client.post(endpoint, json={"url": share_url})
        assert queued.status_code == 202, queued.text
        denied = poll(client, f"{endpoint}/test-clip", lambda data: data["status"] == "failed")
        assert "Twitch did not authorize" in denied["detail"]
        assert not downloads
        monkeypatch.setattr(
            intake.editor, "downloads", lambda *_: {"test-clip": "https://example.test/media"}
        )
        assert client.post(endpoint, json={"url": share_url}).status_code == 202
        ready = poll(client, f"{endpoint}/test-clip", lambda data: data["status"] == "ready")
        recording = ready["recording"]
        reused = client.post(endpoint, json={"url": "https://www.twitch.tv/fixture/clip/test-clip"})
        assert reused.status_code == 202 and reused.json()["recording"]["id"] == recording["id"]
        assert len(downloads) == 1 and len(calls) == 2
        assert client.get(f"/api/recordings/{recording['id']}/draft").status_code == 200
        assert client.get(f"/api/recordings/{recording['id']}/media").status_code == 200
        with service.store.connection() as db:
            assert db.execute("SELECT count(*) FROM projects").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM transcriptions").fetchone()[0] == 0
        assert client.post(f"{ROOT}/disconnect").status_code == 204
        assert client.get(f"{endpoint}/test-clip").json()["recording"]["id"] == recording["id"]
