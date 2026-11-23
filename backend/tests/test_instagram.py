"""Meta is simulated at HTTP transport; local persistence and Windows DPAPI are real."""

import json
import logging
import time
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.boundary import RequestBoundary
from backend.instagram import Instagram, ReelRequest
from backend.instagram_api import (
    InstagramAPI,
    InstagramError,
    InstagramSettings,
    SensitiveInstagramLogs,
)
from backend.instagram_routes import create_instagram_router
from backend.media import MediaInfo
from backend.models import ExportRequest, Recording
from backend.storage import Store, now


@pytest.fixture
def fixture(tmp_path):
    state = {
        "calls": [],
        "status": "FINISHED",
        "publish_error": False,
        "upload_error": False,
        "profile": "123",
        "info": MediaInfo(8, 1080, 1920, True),
        "uploads": [],
    }

    def handle(request):
        body = request.read()
        state["calls"].append((request.url.host, request.url.path, body))
        path = request.url.path
        if path.endswith("/oauth/access_token"):
            return httpx2.Response(
                200, json={"access_token": "fixture-user-secret", "expires_in": 3600}
            )
        if path == "/v27.0/456":
            result = {
                "access_token": "fixture-page-secret",
                "instagram_business_account": {"id": "123"},
            }
        elif path == "/v27.0/123":
            result = {"id": state["profile"], "username": "fixture_creator"}
        elif path == "/v27.0/123/media":
            fields = parse_qs(body.decode(), keep_blank_values=True)
            assert fields["media_type"] == ["REELS"]
            assert fields["upload_type"] == ["resumable"]
            result = {"id": "789", "uri": "http://127.0.0.1/never-follow-this-url"}
        elif path == "/ig-api-upload/v27.0/789":
            state["uploads"].append((dict(request.headers), body))
            assert request.headers["authorization"] == "OAuth fixture-page-secret"
            if state["upload_error"]:
                return httpx2.Response(400, json={"error": {"message": "fixture-page-secret"}})
            result = {"success": True}
        elif path == "/v27.0/789":
            result = {"status_code": state["status"]}
        elif path == "/v27.0/123/media_publish":
            if state["publish_error"]:
                raise httpx2.ReadTimeout("fixture-page-secret")
            result = {"id": "987"}
        else:
            raise AssertionError(f"Unexpected fixture endpoint {path}")
        return httpx2.Response(200, json=result)

    settings = InstagramSettings("111", "fixture-app-secret", "222", "456")
    store = Store(tmp_path / "data")
    media = SimpleNamespace(probe=lambda path: state["info"])
    service = Instagram(
        store,
        media,
        settings,
        "http://127.0.0.1:3000",
        api=InstagramAPI(settings, transport=httpx2.MockTransport(handle)),
    )
    service.initialize()
    recording_id, export_id = str(uuid4()), str(uuid4())
    store.save_recording(
        Recording(
            id=recording_id,
            name="Explicit fixture.mp4",
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
    store.export_path(export_id).write_bytes(b"explicit simulated video bytes" * 200)
    store.update_clip(
        export_id, "succeeded", duration=8, size_bytes=store.export_path(export_id).stat().st_size
    )
    app = FastAPI()
    app.add_middleware(
        RequestBoundary, settings=SimpleNamespace(web_origin=service.web_origin, public_origin="")
    )
    app.include_router(create_instagram_router(service))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        try:
            yield service, state, export_id, client
        finally:
            service.close()


def connect(service):
    state, _ = service.begin(service.web_origin)
    service.finish(state, state, "fixture-code", False)
    return service.status()["account_id"]


def request_for(service, export_id, **updates):
    return ReelRequest.model_validate(
        {
            "id": str(uuid4()),
            "export_id": export_id,
            "account_id": service.status()["account_id"],
            "caption": "Approved caption",
            "consent": True,
            **updates,
        }
    )


def finish(service, request):
    service.submit(request)
    service.active.result(timeout=10)
    return service.get(str(request.id))


def test_browser_bound_single_use_oauth_and_encryption(fixture):
    service, state, _, client = fixture
    response = client.post("/api/instagram/connect", headers={"origin": service.web_origin})
    query = parse_qs(urlsplit(response.json()["url"]).query)
    value = query["state"][0]
    assert query["config_id"] == ["222"]
    with pytest.raises(InstagramError):
        service.finish(value, "different-browser", "code", False)
    callback = client.get(
        "/api/instagram/callback",
        params={"state": value, "code": "code"},
        headers={"sec-fetch-site": "cross-site"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    with pytest.raises(InstagramError):
        service.finish(value, value, "code", False)
    with service.store.connection() as db:
        saved = db.execute("SELECT credentials FROM instagram_account").fetchone()[0]
    assert b"fixture-page-secret" not in saved
    assert service.creator()["username"] == "fixture_creator"
    assert not state["uploads"]


def test_oauth_expiry_denial_and_cross_origin(fixture):
    service, _, _, client = fixture
    assert (
        client.post(
            "/api/instagram/connect", headers={"origin": "https://evil.invalid"}
        ).status_code
        == 403
    )
    state, _ = service.begin(service.web_origin)
    assert "cancelled" in service.finish(state, state, "", True)
    state, _ = service.begin(service.web_origin)
    _, redirect, origin, page_id = service.pending[state]
    service.pending[state] = (time.monotonic() - 1, redirect, origin, page_id)
    with pytest.raises(InstagramError):
        service.finish(state, state, "code", False)
    assert (
        client.get("/api/instagram/creator", headers={"sec-fetch-site": "cross-site"}).status_code
        == 403
    )


def test_upload_publish_idempotency_and_disconnect(fixture):
    service, state, export_id, client = fixture
    connect(service)
    request = request_for(service, export_id, share_to_feed=True)
    job = finish(service, request)
    assert job.status == "published" and job.media_id == "987"
    assert job.uploaded_bytes == job.total_bytes
    assert len(state["uploads"]) == 1
    headers, body = state["uploads"][0]
    assert body == service.store.export_path(export_id).read_bytes()
    assert headers["offset"] == "0" and int(headers["file_size"]) == len(body)
    assert service.submit(request).id == job.id
    with pytest.raises(InstagramError):
        service.submit(request_for(service, export_id))
    with pytest.raises(InstagramError):
        service.submit(request.model_copy(update={"caption": "changed"}))
    assert (
        client.get(f"/api/instagram/posts?export_id={export_id}").json()[0]["status"] == "published"
    )
    assert "secret" not in json.dumps(client.get("/api/instagram").json())
    assert "revoke" in service.disconnect()["warning"].lower()
    assert service.status()["connected"] is False
    assert service.get(job.id).status == "published"


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_strict_consent(fixture, value):
    service, _, export_id, _ = fixture
    connect(service)
    with pytest.raises(ValidationError):
        request_for(service, export_id, consent=value)


@pytest.mark.parametrize("caption", ["bad\x00caption", "\ud800", "x" * 2201, "😀" * 1101])
def test_caption_validation(fixture, caption):
    service, _, export_id, _ = fixture
    connect(service)
    with pytest.raises(ValidationError):
        request_for(service, export_id, caption=caption)


@pytest.mark.parametrize(
    "changes", [{"duration": 2}, {"duration": 901}, {"width": 1921}, {"frame_rate": 22}]
)
def test_media_limits(fixture, changes):
    service, state, export_id, _ = fixture
    connect(service)
    state["info"] = replace(state["info"], **changes)
    with pytest.raises(InstagramError):
        service.submit(request_for(service, export_id))
    assert not state["uploads"]


def test_changed_export_and_account(fixture):
    service, _, export_id, _ = fixture
    connect(service)
    request = request_for(service, export_id, account_id="0" * 64)
    with pytest.raises(InstagramError):
        service.submit(request)
    service.store.export_path(export_id).write_bytes(b"changed")
    with pytest.raises(InstagramError):
        service.submit(request_for(service, export_id))


def test_upload_failure_does_not_publish_or_leak_secrets(fixture):
    service, state, export_id, _ = fixture
    connect(service)
    state["upload_error"] = True
    job = finish(service, request_for(service, export_id))
    assert job.status == "failed" and "secret" not in job.error
    assert not any(path.endswith("media_publish") for _, path, _ in state["calls"])


def test_ambiguous_publish_reconciles_without_republishing(fixture):
    service, state, export_id, _ = fixture
    connect(service)
    state["publish_error"] = True
    job = finish(service, request_for(service, export_id))
    assert job.status == "unknown" and "secret" not in job.error
    assert service.refresh(job.id).status == "unknown"
    with pytest.raises(InstagramError):
        service.submit(request_for(service, export_id))
    state["status"] = "PUBLISHED"
    assert service.refresh(job.id).status == "published"
    assert sum(path.endswith("media_publish") for _, path, _ in state["calls"]) == 1


@pytest.mark.parametrize(
    "status,expected", [("queued", "failed"), ("processing", "failed"), ("publishing", "unknown")]
)
def test_restart_recovery(fixture, status, expected):
    service, _, export_id, _ = fixture
    connect(service)
    job = finish(service, request_for(service, export_id))
    job.status = status
    service._save(job)
    service.initialize()
    assert service.get(job.id).status == expected


def test_expired_credentials_and_config_change(fixture):
    service, _, _, _ = fixture
    connect(service)
    record, _ = service._token()
    record.expires_at = time.time() - 1
    with service.store.connection() as db:
        db.execute(
            "UPDATE instagram_account SET credentials=?",
            (service.protector(record.model_dump_json().encode()),),
        )
    with pytest.raises(InstagramError):
        service.creator()
    service.settings = replace(service.settings, page_id="999")
    assert service.status()["connected"]  # Page ID is now chosen per connection.
    service.settings = replace(service.settings, login_config_id="999")
    assert not service.status()["connected"]


def test_redaction_and_transport_bounds():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        "GET /api/instagram/callback?code=secret&state=value HTTP/1.1",
        (),
        None,
    )
    SensitiveInstagramLogs().filter(record)
    assert "secret" not in record.getMessage()
    api = InstagramAPI(
        InstagramSettings(),
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, content=b"x" * (128 * 1024 + 1))
        ),
    )
    with pytest.raises(InstagramError):
        api.request("123")
    with pytest.raises(ValueError):
        api.request("https://evil.invalid")


def test_publication_persistence_failure_stays_uncertain(fixture, monkeypatch):
    service, state, export_id, _ = fixture
    connect(service)
    save = service._save

    def fail_published_save(job, db=None):
        if job.status == "published":
            raise OSError("Explicit simulated storage failure")
        return save(job, db)

    monkeypatch.setattr(service, "_save", fail_published_save)
    job = finish(service, request_for(service, export_id))
    assert job.status == "unknown"
    with pytest.raises(InstagramError):
        service.submit(request_for(service, export_id))
    assert sum(path.endswith("media_publish") for _, path, _ in state["calls"]) == 1


def test_same_size_change_before_worker_does_not_upload(fixture, monkeypatch):
    service, state, export_id, _ = fixture
    connect(service)
    request = request_for(service, export_id)
    original_submit = service.pool.submit

    def change_before_start(fn, *args):
        path = service.store.export_path(export_id)
        path.write_bytes(b"x" * path.stat().st_size)
        return original_submit(fn, *args)

    monkeypatch.setattr(service.pool, "submit", change_before_start)
    job = finish(service, request)
    assert job.status == "failed" and not state["uploads"]


def test_processing_timeout_never_publishes(fixture, monkeypatch):
    service, state, export_id, _ = fixture
    connect(service)
    state["status"] = "IN_PROGRESS"
    clock = [0.0]
    monkeypatch.setattr("backend.instagram.time.monotonic", lambda: clock[0])

    def advance(seconds):
        clock[0] += seconds
        return False

    monkeypatch.setattr(service.stop, "wait", advance)
    job = finish(service, request_for(service, export_id))
    assert job.status == "failed" and "five minutes" in job.error
    assert not any(path.endswith("media_publish") for _, path, _ in state["calls"])


def test_multiple_instagram_accounts_preserve_approved_destination(fixture):
    service, _, export_id, _ = fixture
    first = connect(service)
    request = request_for(service, export_id)
    token, _ = service._token(first)
    token.instagram_id = "999"
    second = "b" * 64
    service.accounts.save(
        service.config,
        second,
        service.protector(token.model_dump_json().encode()),
        "Second fixture",
    )
    assert service.status()["account_id"] is None
    with pytest.raises(InstagramError, match="Choose"):
        service.creator()
    assert finish(service, request).status == "published"
    service.disconnect(first)
    assert service.status()["account_id"] == second


def test_missing_instagram_page_is_actionable_validation_error(fixture):
    service, _, _, _ = fixture
    service.settings = replace(service.settings, page_id="")
    with pytest.raises(InstagramError) as exc:
        service.begin(service.web_origin)
    assert exc.value.status_code == 422
