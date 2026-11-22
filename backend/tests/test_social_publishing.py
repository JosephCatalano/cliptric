"""Provider protocols are simulated at HTTP transport; persistence and DPAPI are real."""

import base64
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.boundary import RequestBoundary
from backend.facebook_api import FacebookAPI
from backend.media import MediaInfo
from backend.models import ExportRequest, Recording
from backend.scheduling import Scheduler, ScheduleRequest
from backend.social_accounts import Accounts
from backend.social_publishing import SocialPublisher, SocialRequest
from backend.social_routes import create_social_router
from backend.social_security import protect_secret
from backend.social_transport import SensitiveSocialLogs, SocialError, SocialSettings, Transport
from backend.storage import Store, now
from backend.x_api import SCOPES, XAPI


@pytest.fixture(params=["x", "facebook"])
def publishing(tmp_path, request):
    platform = request.param
    state = {
        "calls": [],
        "uploads": [],
        "posts": [],
        "publish_error": False,
        "upload_error": False,
        "published": True,
        "processing": "succeeded",
        "refresh_subject": None,
        "info": MediaInfo(8, 1080, 1920, True),
    }

    def handle(req):
        body = req.read()
        path, headers = req.url.path, dict(req.headers)
        state["calls"].append((req.method, req.url.host, path))
        token = headers.get("authorization", "").split()[-1] if headers.get("authorization") else ""
        if path == "/2/oauth2/token":
            form = parse_qs(body.decode())
            assert token == base64.b64encode(b"111:fixture-app-secret").decode()
            subject = form.get("code", form.get("refresh_token", ["1"]))[0]
            if form["grant_type"] == ["refresh_token"]:
                subject = state["refresh_subject"] or subject.removeprefix("refresh-")
            return httpx2.Response(
                200,
                json={
                    "access_token": f"access-{subject}",
                    "refresh_token": f"refresh-{subject}",
                    "expires_in": 7200,
                    "scope": " ".join(SCOPES),
                },
            )
        if path == "/2/users/me":
            subject = token.removeprefix("access-")
            result = {"data": {"id": subject, "username": f"fixture{subject}"}}
        elif path == "/2/media/upload/initialize":
            assert json.loads(body)["media_category"] == "tweet_video"
            result = {"data": {"id": "789"}}
        elif path == "/2/media/upload/789/append":
            if state["upload_error"]:
                raise httpx2.ReadTimeout("sensitive-token")
            part = json.loads(body)
            state["uploads"].append((token, base64.b64decode(part["media"]), part["segment_index"]))
            return httpx2.Response(204)
        elif path in {"/2/media/upload/789/finalize", "/2/media/upload"}:
            result = {
                "data": {
                    "id": "789",
                    "processing_info": {"state": state["processing"], "check_after_secs": 0},
                }
            }
        elif path == "/2/tweets":
            state["posts"].append((token, json.loads(body)))
            if state["publish_error"]:
                raise httpx2.ReadTimeout("sensitive-token")
            result = {"data": {"id": "987"}}
        elif path == "/2/tweets/987":
            result = {"data": {"id": "987"}}
        elif path.endswith("/oauth/access_token"):
            result = {"access_token": "fixture-user-secret", "expires_in": 7200}
        elif path == "/v27.0/me/accounts":
            result = {
                "data": [
                    {
                        "id": subject,
                        "name": f"Fixture Page {subject}",
                        "access_token": f"page-{subject}",
                        "tasks": ["CREATE_CONTENT"],
                    }
                    for subject in ("1", "2")
                ]
            }
        elif req.url.host == "rupload.facebook.com":
            assert path == "/video-upload/v27.0/789"
            state["uploads"].append((token, body, 0))
            if state["upload_error"]:
                raise httpx2.ReadTimeout("sensitive-token")
            result = {"success": True}
        elif path in {"/v27.0/1/video_reels", "/v27.0/2/video_reels"}:
            fields = parse_qs(body.decode())
            if fields["upload_phase"] == ["start"]:
                result = {"video_id": "789", "upload_url": "https://evil.example/never-follow"}
            else:
                assert fields["upload_phase"] == ["finish"] and fields["video_state"] == [
                    "PUBLISHED"
                ]
                state["posts"].append((token, fields))
                if state["publish_error"]:
                    raise httpx2.ReadTimeout("sensitive-token")
                result = {"success": True}
        elif path == "/v27.0/789":
            result = {
                "status": {
                    "publishing_phase": {
                        "publish_status": "published" if state["published"] else "draft"
                    }
                }
            }
        elif path in {"/v27.0/1", "/v27.0/2"}:
            subject = path.rsplit("/", 1)[-1]
            result = {
                "id": subject,
                "name": f"Fixture Page {subject}",
                "access_token": f"page-{subject}",
                "tasks": ["CREATE_CONTENT"],
            }
        else:
            raise AssertionError(f"Unexpected simulated endpoint {path}")
        return httpx2.Response(200, json=result)

    settings = SocialSettings("111", "fixture-app-secret", "222" if platform == "facebook" else "")
    adapter = (XAPI if platform == "x" else FacebookAPI)(
        settings, transport=httpx2.MockTransport(handle)
    )
    store = Store(tmp_path)
    service = SocialPublisher(
        store, SimpleNamespace(probe=lambda path: state["info"]), adapter, "http://127.0.0.1:3000"
    )
    service.initialize()
    recording_id, export_id = str(uuid4()), str(uuid4())
    store.save_recording(
        Recording(
            id=recording_id,
            name="Explicit simulation.mp4",
            duration=8,
            width=1080,
            height=1920,
            has_audio=True,
            size_bytes=1,
            created_at=now(),
        ),
        ".mp4",
    )
    store.create_clip(export_id, recording_id, ExportRequest(start=0.0, end=8.0))
    store.export_path(export_id).write_bytes(b"explicit upload fixture" * 200)
    store.update_clip(
        export_id, "succeeded", duration=8, size_bytes=store.export_path(export_id).stat().st_size
    )
    app = FastAPI()
    app.add_middleware(RequestBoundary, settings=SimpleNamespace(web_origin=service.web_origin))
    app.include_router(create_social_router(service))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        try:
            yield service, state, export_id, client
        finally:
            service.close()


def connect(service, subject="1"):
    state, url = service.begin(
        service.web_origin, subject if service.platform == "facebook" else None
    )
    service.finish(state, state, subject, False)
    return service.accounts.list(service.config)[-1]["account_id"], url


def approved(export_id, account, **changes):
    return SocialRequest.model_validate(
        {
            "id": str(uuid4()),
            "export_id": export_id,
            "account_id": account,
            "caption": "Approved text",
            "consent": True,
            **changes,
        }
    )


def finish(service, request):
    job = service.submit(request)
    service.active.result(timeout=10)
    return service.get(job.id)


def test_oauth_state_cookie_pkce_and_no_post(publishing):
    service, state, _, client = publishing
    root = f"/api/{service.platform}"
    result = client.post(root + "/connect?page_id=1", headers={"origin": service.web_origin})
    assert result.status_code == 200
    assert (
        "HttpOnly" in result.headers["set-cookie"]
        and "SameSite=lax" in result.headers["set-cookie"]
    )
    query = parse_qs(urlsplit(result.json()["url"]).query)
    if service.platform == "x":
        assert query["code_challenge_method"] == ["S256"]
        assert set(query["scope"][0].split()) == SCOPES
    oauth_state = query["state"][0]
    assert client.get(root + "/callback?state=wrong&code=1").status_code == 400
    # The first rejected callback clears the cookie; verify service-bound flow separately.
    service.finish(oauth_state, oauth_state, "1", False)
    with pytest.raises(SocialError):
        service.finish(oauth_state, oauth_state, "1", False)
    with pytest.raises(SocialError):
        service.begin("https://evil.example", "1")
    assert state["uploads"] == state["posts"] == []
    assert (
        client.get(root + "/creator", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    )


def test_multiple_accounts_exact_destination_idempotency_and_disconnect(publishing):
    service, state, export_id, client = publishing
    first, _ = connect(service, "1")
    second, _ = connect(service, "2")
    assert first != second and len(service.status()["accounts"]) == 2
    assert service.status()["account_id"] is None
    with pytest.raises(SocialError, match="Choose"):
        service.creator()
    request = approved(export_id, first)
    job = finish(service, request)
    assert job.status == "published", job.error
    assert state["posts"][0][0] == ("access-1" if service.platform == "x" else "page-1")
    assert service.submit(request) == job and len(state["posts"]) == 1
    with pytest.raises(SocialError):
        service.submit(request.model_copy(update={"caption": "Changed approval"}))
    with pytest.raises(SocialError, match="already"):
        service.submit(approved(export_id, first))
    other = finish(service, approved(export_id, second))
    assert other.status == "published" and len(state["posts"]) == 2
    assert state["posts"][1][0] == ("access-2" if service.platform == "x" else "page-2")
    assert all(
        host in {"api.x.com", "graph.facebook.com", "rupload.facebook.com"}
        for _, host, _ in state["calls"]
    )
    assert client.post(f"/api/{service.platform}/disconnect").status_code == 409
    service.disconnect(first)
    assert service.status()["account_id"] == second
    assert len(service.posts(export_id)) == 2
    with pytest.raises(SocialError):
        service.creator(first)


def test_encrypted_at_rest_and_refresh_cannot_change_account(publishing):
    service, state, _, _ = publishing
    account, _ = connect(service)
    row = service.accounts.get(service.config, account)
    assert b"access-1" not in row["credentials"] and b"page-1" not in row["credentials"]
    record, _ = service._token(account)
    record.expires_at = time.time() - 1
    service._store_token(record)
    if service.platform == "x":
        state["refresh_subject"] = "2"
    with pytest.raises(SocialError):
        service.creator(account)
    assert [a["account_id"] for a in service.status()["accounts"]] == [account]


@pytest.mark.parametrize("consent", [False, 1, "true", None])
def test_strict_consent(publishing, consent):
    _, _, export_id, _ = publishing
    with pytest.raises(ValidationError):
        SocialRequest(
            id=uuid4(), export_id=export_id, account_id="a" * 64, caption="", consent=consent
        )


@pytest.mark.parametrize("caption", ["bad\x00caption", "\ud800"])
def test_text_rejects_unsafe_characters(caption):
    with pytest.raises(ValidationError):
        approved(str(uuid4()), "a" * 64, caption=caption)


def test_ambiguous_publish_never_repeats_after_restart(publishing):
    service, state, export_id, _ = publishing
    account, _ = connect(service)
    state["publish_error"] = True
    request = approved(export_id, account)
    job = finish(service, request)
    assert job.status == "unknown" and "sensitive" not in job.error
    service.initialize()
    assert service.submit(request).status == "unknown"
    service.refresh(job.id)
    assert len(state["posts"]) == 1


def test_upload_failure_never_publishes(publishing):
    service, state, export_id, _ = publishing
    account, _ = connect(service)
    state["upload_error"] = True
    job = finish(service, approved(export_id, account))
    assert job.status == "failed" and "sensitive" not in job.error
    assert state["posts"] == []


def test_publication_persistence_failure_blocks_duplicate(publishing, monkeypatch):
    service, state, export_id, _ = publishing
    account, _ = connect(service)
    save = service._save
    failed = False

    def fail_once(job, db=None):
        nonlocal failed
        if job.status == "published" and not failed:
            failed = True
            raise OSError("Explicit simulated storage failure")
        return save(job, db)

    monkeypatch.setattr(service, "_save", fail_once)
    job = finish(service, approved(export_id, account))
    assert job.status == "unknown" and len(state["posts"]) == 1
    with pytest.raises(SocialError, match="already"):
        service.submit(approved(export_id, account))
    assert service.refresh(job.id).status == "published"
    assert len(state["posts"]) == 1


def test_x_processing_failure_never_creates_post(publishing):
    service, state, export_id, _ = publishing
    if service.platform != "x":
        pytest.skip("X-only processing protocol")
    account, _ = connect(service)
    state["processing"] = "failed"
    assert finish(service, approved(export_id, account)).status == "failed"
    assert state["posts"] == []


def test_x_refresh_updates_only_selected_account(publishing):
    service, _, _, _ = publishing
    if service.platform != "x":
        pytest.skip("X-only rotating refresh tokens")
    first, _ = connect(service, "1")
    second, _ = connect(service, "2")
    second_blob = service.accounts.get(service.config, second)["credentials"]
    record, _ = service._token(first)
    record.expires_at = time.time() - 1
    service._store_token(record)
    refreshed, selected = service._token(first)
    assert selected == first and refreshed.expires_at > time.time() + 600
    assert service.accounts.get(service.config, second)["credentials"] == second_blob


def test_media_limits_and_changed_bytes(publishing):
    service, state, export_id, _ = publishing
    account, _ = connect(service)
    state["info"] = MediaInfo(1300, 1080, 1920, True)
    with pytest.raises(SocialError):
        service.submit(approved(export_id, account))
    state["info"] = MediaInfo(8, 1080, 1920, True)
    service.store.export_path(export_id).write_bytes(b"tampered")
    with pytest.raises(SocialError):
        service.submit(approved(export_id, account))
    assert state["posts"] == []


def test_schedule_retains_account_and_fails_when_disconnected(publishing):
    service, state, export_id, _ = publishing
    first, _ = connect(service, "1")
    connect(service, "2")
    # Scheduler doubles for unrelated platforms are never dispatched.
    with service.store.connection() as db:
        for table in ("tiktok_posts", "youtube_uploads"):
            db.execute(f"CREATE TABLE {table}(id TEXT,export_id TEXT,account_id TEXT,status TEXT)")
    scheduler = Scheduler(
        service.store, SimpleNamespace(), SimpleNamespace(), **{service.platform: service}
    )
    scheduler.initialize()
    payload = ScheduleRequest.model_validate(
        {
            "scheduled_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "post": {"platform": service.platform, "request": approved(export_id, first)},
        }
    )
    scheduled_id = scheduler.create(payload)["id"]
    service.disconnect(first)
    with service.store.connection() as db:
        db.execute(
            "UPDATE scheduled_posts SET scheduled_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), scheduled_id),
        )
    scheduler.tick()
    with service.store.connection() as db:
        row = db.execute(
            "SELECT status,account_id FROM scheduled_posts WHERE id=?", (scheduled_id,)
        ).fetchone()
        assert row["status"] == "failed" and row["account_id"] == first
    assert state["posts"] == []


@pytest.mark.parametrize(
    "table,column",
    [
        ("tiktok_account", "client_key"),
        ("youtube_account", "client_id"),
        ("instagram_account", "config"),
    ],
)
def test_singleton_migration_preserves_encrypted_credentials(tmp_path, table, column):
    store = Store(tmp_path)
    encrypted = protect_secret(b"explicit migration credential")
    with store.connection() as db:
        db.execute(
            f"CREATE TABLE {table}(id INTEGER PRIMARY KEY CHECK(id=1), {column} TEXT NOT NULL, account_id TEXT NOT NULL, credentials BLOB NOT NULL)"
        )
        db.execute(f"INSERT INTO {table} VALUES(1,?,?,?)", ("config", "a" * 64, encrypted))
    accounts = Accounts(store, table, column, SocialError)
    accounts.initialize()
    assert accounts.get("config", "a" * 64)["credentials"] == encrypted
    accounts.save(
        "config", "b" * 64, protect_secret(b"explicit second credential"), "Second account"
    )
    accounts.initialize()
    assert len(accounts.list("config")) == 2
    accounts.save("config", "b" * 64, protect_secret(b"explicit refreshed credential"))
    assert len(accounts.list("config")) == 2
    assert accounts.get("config", "b" * 64)["label"] == "Second account"
    with pytest.raises(SocialError):
        accounts.get("config")
    with pytest.raises(SocialError):
        accounts.delete("config", "c" * 64)
    assert len(accounts.list("config")) == 2


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(302, headers={"location": "https://evil.example"}),
        httpx2.Response(200, content=b"x" * (128 * 1024 + 1)),
        httpx2.Response(200, json={"errors": [{"detail": "sensitive"}]}),
    ],
)
def test_transport_rejects_redirects_oversize_and_provider_errors(response):
    api = Transport(SocialSettings(), transport=httpx2.MockTransport(lambda _: response))
    with pytest.raises(SocialError) as exc:
        api.send("GET", "https://api.x.com/2/users/me")
    assert "sensitive" not in str(exc.value)


def test_callback_logs_hide_tokens():
    record = logging.LogRecord(
        "test",
        logging.INFO,
        "",
        0,
        "GET /api/x/callback?code=sensitive-secret&state=private",
        (),
        None,
    )
    SensitiveSocialLogs().filter(record)
    assert "sensitive-secret" not in record.getMessage()


def test_x_processing_polls_fixed_status_endpoint(publishing, monkeypatch):
    service, state, export_id, _ = publishing
    if service.platform != "x":
        pytest.skip("X-only processing protocol")
    account, _ = connect(service)
    state["processing"] = "pending"
    request = service.api.request

    def processing(path, token, **kwargs):
        if path == "media/upload":
            assert kwargs["params"] == {"media_id": "789", "command": "STATUS"}
            state["processing"] = "succeeded"
        return request(path, token, **kwargs)

    monkeypatch.setattr(service.api, "request", processing)
    monkeypatch.setattr(service.stop, "wait", lambda _: False)
    assert finish(service, approved(export_id, account)).status == "published"
    assert ("GET", "api.x.com", "/2/media/upload") in state["calls"]


def test_facebook_cannot_connect_page_outside_authorized_list(publishing):
    service, _, _, _ = publishing
    if service.platform != "facebook":
        pytest.skip("Facebook-only Page permissions")
    state, _ = service.begin(service.web_origin, "3")
    with pytest.raises(SocialError, match="permission"):
        service.finish(state, state, "1", False)
    assert service.status()["accounts"] == []
