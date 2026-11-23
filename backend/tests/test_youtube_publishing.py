"""Explicit Google HTTP simulation; DPAPI protection is exercised on Windows."""

import base64
import hashlib
import json
import logging
import time
from concurrent.futures import Future
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.boundary import RequestBoundary
from backend.config import Settings
from backend.main import create_app
from backend.media import MediaInfo
from backend.models import CaptionOptions, ExportRequest, Recording
from backend.scheduling import Scheduler
from backend.social_security import protect_secret
from backend.storage import Store, now
from backend.transcription_store import TranscriptStore
from backend.youtube_api import (
    SCOPES,
    SensitiveYouTubeLogs,
    YouTubeAPI,
    YouTubeError,
    YouTubeSettings,
)
from backend.youtube_publishing import UploadRequest, YouTubePublishing
from backend.youtube_routes import create_youtube_publishing_router


class FixtureMedia:
    info = MediaInfo(8, 1080, 1920, True)

    def probe(self, path):
        return self.info


def token_response(**updates):
    return {
        "access_token": "explicit-access",
        "refresh_token": "explicit-refresh",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": " ".join(SCOPES),
        **updates,
    }


@pytest.fixture
def integration(tmp_path):
    state = {
        "calls": [],
        "uploads": [],
        "init": 0,
        "queries": 0,
        "accepted": 0,
        "total": 0,
        "mode": "complete",
        "channel": "UC_fixture",
        "privacy": "private",
        "upload_status": "uploaded",
        "revoke_status": 200,
    }

    def video():
        return {
            "id": "fixture0001",
            "snippet": {"channelId": state["channel"]},
            "status": {"uploadStatus": state["upload_status"], "privacyStatus": state["privacy"]},
        }

    def handle(request):
        body = request.read()
        state["calls"].append((request.method, request.url.path, body))
        if request.url.path == "/token":
            form = parse_qs(body.decode())
            if form["grant_type"] == ["refresh_token"]:
                response = token_response(access_token="explicit-refreshed")
                del response["refresh_token"]
                return httpx2.Response(200, json=response)
            state["token_form"] = form
            return httpx2.Response(200, json=token_response())
        if request.url.path == "/revoke":
            return httpx2.Response(state["revoke_status"])
        if request.url.path == "/youtube/v3/channels":
            return httpx2.Response(
                200,
                json={
                    "items": [
                        {"id": "UC_fixture", "snippet": {"title": "Explicit fixture channel"}}
                    ]
                },
            )
        if request.url.path == "/youtube/v3/videoCategories":
            return httpx2.Response(
                200,
                json={
                    "items": [
                        {"id": "22", "snippet": {"title": "People & Blogs", "assignable": True}},
                        {"id": "999", "snippet": {"title": "Not assignable", "assignable": False}},
                    ]
                },
            )
        if request.url.path == "/youtube/v3/videos":
            return httpx2.Response(200, json={"items": [video()]})
        if request.url.path == "/upload/youtube/v3/videos" and request.method == "POST":
            state["init"] += 1
            state["metadata"] = json.loads(body)
            state["notify"] = request.url.params["notifySubscribers"]
            state["total"] = int(request.headers["x-upload-content-length"])
            return httpx2.Response(
                200,
                headers={
                    "location": "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=explicit-session"
                },
            )
        if request.url.path == "/upload/youtube/v3/videos" and request.method == "PUT":
            if not body:
                state["queries"] += 1
                assert request.headers["content-range"] == f"bytes */{state['total']}"
                if state["mode"] == "expired":
                    return httpx2.Response(404)
                if state["accepted"] == state["total"]:
                    return httpx2.Response(201, json=video())
                headers = {"range": f"bytes=0-{state['accepted'] - 1}"} if state["accepted"] else {}
                if state["mode"] == "delay":
                    headers["retry-after"] = "120"
                return httpx2.Response(308, headers=headers)
            state["uploads"].append((dict(request.headers), body))
            assert request.headers["authorization"] == "Bearer explicit-access"
            if state["mode"] in {"interrupt", "lost-final"}:
                state["accepted"] = 256 if state["mode"] == "interrupt" else state["total"]
                state["mode"] = "complete"
                raise httpx2.ReadError("Explicit simulated lost response")
            state["accepted"] = state["total"]
            return httpx2.Response(201, json=video())
        raise AssertionError("Unexpected fixture request")

    settings = YouTubeSettings("explicit-client", "explicit-secret")
    api = YouTubeAPI(
        settings,
        transport=httpx2.MockTransport(handle),
        resolver=lambda *args, **kwargs: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    store = Store(tmp_path / "data")
    service = YouTubePublishing(store, FixtureMedia(), settings, "http://127.0.0.1:3000", api=api)
    service.initialize()
    Scheduler(store, None, service).initialize()
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
    store.export_path(export_id).write_bytes(b"explicit upload fixture" * 200)
    store.update_clip(
        export_id, "succeeded", duration=8, size_bytes=store.export_path(export_id).stat().st_size
    )
    app = FastAPI()
    app.add_middleware(
        RequestBoundary, settings=SimpleNamespace(web_origin=service.web_origin, public_origin="")
    )
    app.include_router(create_youtube_publishing_router(service))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        try:
            yield service, state, export_id, client
        finally:
            service.close()


def connected(service):
    service._store_token(token_response())
    return service.status()["account_id"]


def test_batch_approval_is_atomic_and_uploads_in_order(integration, monkeypatch):
    service, state, export_id, client = integration
    connected(service)
    monkeypatch.setattr("backend.youtube_publishing.MIN_BATCH_UPLOAD_INTERVAL", 0)
    first = upload_request(service, export_id, title="First upload")
    second_export = str(uuid4())
    second = upload_request(service, second_export, title="Second upload")
    payload = {"uploads": [item.model_dump(mode="json") for item in (first, second)]}
    assert client.post("/api/youtube-publishing/posts/batch", json=payload).status_code == 404
    assert not service.posts() and state["init"] == 0

    recording = service.store.clip(export_id).recording_id
    service.store.create_clip(second_export, recording, ExportRequest(start=0, end=8))
    path = service.store.export_path(second_export)
    path.write_bytes(b"second fixture" * 200)
    service.store.update_clip(
        second_export, "succeeded", duration=8, size_bytes=path.stat().st_size
    )
    response = client.post("/api/youtube-publishing/posts/batch", json=payload)
    assert response.status_code == 202
    service.active.result(timeout=5)
    assert [body for _, body in state["uploads"]] == [
        service.store.export_path(export_id).read_bytes(),
        path.read_bytes(),
    ]
    assert all(service.get(str(item.id)).status == "processing" for item in (first, second))
    assert client.post("/api/youtube-publishing/posts/batch", json=payload).status_code == 202
    assert state["init"] == 2


def test_batch_recovery_only_dispatches_jobs_that_have_not_started(integration, monkeypatch):
    service, state, export_id, _ = integration
    connected(service)
    request = upload_request(service, export_id)
    creator, clip, path = service.validate_post(request)
    pending = service._upload_job(request, creator, clip, path, batch_pending=True)
    interrupted = pending.model_copy(update={"id": str(uuid4()), "status": "initializing"})
    service._save(pending)
    service._save(interrupted)
    monkeypatch.setattr("backend.youtube_publishing.MIN_BATCH_UPLOAD_INTERVAL", 0)
    service.initialize()
    service.active.result(timeout=5)
    assert service.get(pending.id).status == "processing"
    assert service.get(interrupted.id).status == "failed"
    assert state["init"] == 1


def upload_request(service, export_id, **updates):
    values = {
        "id": uuid4(),
        "export_id": export_id,
        "account_id": service.status()["account_id"],
        "title": "Explicit fixture title",
        "description": "Fixture description",
        "category_id": "22",
        "privacy": "private",
        "made_for_kids": False,
        "contains_synthetic_media": False,
        "consent": True,
    }
    return UploadRequest(**{**values, **updates})


def finish(service, payload):
    job = service.submit(payload)
    service.active.result(timeout=10)
    return service.get(job.id)


def test_oauth_pkce_cookie_single_use_and_no_upload(integration):
    service, state, _, client = integration
    response = client.post("/api/youtube-publishing/connect")
    assert response.status_code == 200
    query = parse_qs(urlsplit(response.json()["url"]).query)
    pending = service.pending[query["state"][0]]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(pending[1].encode()).digest()).decode().rstrip("=")
    )
    assert query["code_challenge"] == [challenge]
    assert query["access_type"] == ["offline"]
    assert set(query["scope"][0].split()) == set(SCOPES)
    assert "HttpOnly" in response.headers["set-cookie"]
    callback = query["redirect_uri"][0] + "?" + "code=explicit-code&state=" + query["state"][0]
    response = client.get(
        callback, headers={"sec-fetch-site": "cross-site"}, follow_redirects=False
    )
    assert response.status_code == 303 and response.headers["location"].endswith(
        "youtube=connected"
    )
    assert response.headers["referrer-policy"] == "no-referrer"
    assert state["token_form"]["code_verifier"] == [pending[1]]
    assert state["token_form"]["redirect_uri"] == query["redirect_uri"]
    assert client.get(callback, follow_redirects=False).status_code == 400
    assert state["init"] == 0 and not state["uploads"]


def test_oauth_requires_browser_cookie_expiry_and_consumes_denial(integration):
    service, _, _, _ = integration
    state, _ = service.begin(service.web_origin)
    with pytest.raises(YouTubeError, match="verified"):
        service.finish(state, "wrong-cookie", "code", False)
    assert service.finish(state, state, "", True).endswith("youtube=cancelled")
    with pytest.raises(YouTubeError, match="already used"):
        service.finish(state, state, "code", False)
    state, _ = service.begin(service.web_origin)
    value = service.pending[state]
    service.pending[state] = (time.monotonic() - 1, *value[1:])
    with pytest.raises(YouTubeError, match="expired"):
        service.finish(state, state, "code", False)


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/youtube-publishing/connect", "POST"),
        ("/api/youtube-publishing/posts", "POST"),
        ("/api/youtube-publishing/callback", "POST"),
    ],
)
def test_only_oauth_get_allows_cross_site(integration, path, method):
    _, _, _, client = integration
    assert (
        client.request(
            method,
            path,
            headers={"origin": "https://foreign.example", "sec-fetch-site": "cross-site"},
        ).status_code
        == 403
    )
    assert client.get("http://foreign.example/api/youtube-publishing/callback").status_code == 400


def test_tokens_encrypted_refresh_preserves_refresh_token_and_reopens(integration):
    service, _, _, _ = integration
    account = connected(service)
    record, _ = service._stored_token()
    record.expires_at = 0
    with service.store.connection() as db:
        db.execute(
            "UPDATE youtube_account SET credentials=?",
            (protect_secret(record.model_dump_json().encode()),),
        )
    refreshed, same_account = service._token()
    assert (
        refreshed.access_token == "explicit-refreshed"
        and refreshed.refresh_token == "explicit-refresh"
        and same_account == account
    )
    with service.store.connection() as db:
        blob = db.execute("SELECT credentials FROM youtube_account").fetchone()[0]
    assert b"explicit-refreshed" not in blob and b"explicit-refresh" not in blob
    reopened = YouTubePublishing(
        service.store, service.media, service.settings, service.web_origin, api=service.api
    )
    try:
        reopened.initialize()
        assert reopened._stored_token()[0].refresh_token == "explicit-refresh"
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "updates",
    [
        {"scope": SCOPES[0]},
        {"refresh_token": ""},
        {"expires_in": True},
        {"token_type": "DPoP"},
        {"access_token": "bad\nheader"},
    ],
)
def test_partial_or_invalid_credentials_never_saved(integration, updates):
    service, _, _, _ = integration
    with pytest.raises(YouTubeError):
        service._store_token(token_response(**updates))
    assert not service.status()["connected"]


def test_exact_metadata_bytes_idempotency_and_processing_visibility(integration):
    service, state, export_id, client = integration
    connected(service)
    payload = upload_request(
        service,
        export_id,
        privacy="unlisted",
        made_for_kids=True,
        contains_synthetic_media=True,
        notify_subscribers=True,
    )
    job = finish(service, payload)
    assert job.status == "processing" and job.actual_privacy == "private"
    assert state["metadata"] == {
        "snippet": {"title": payload.title, "description": payload.description, "categoryId": "22"},
        "status": {
            "privacyStatus": "unlisted",
            "selfDeclaredMadeForKids": True,
            "containsSyntheticMedia": True,
        },
    }
    assert state["notify"] == "true"
    assert state["uploads"][0][1] == service.store.export_path(export_id).read_bytes()
    assert service.submit(payload).id == job.id and state["init"] == 1
    with pytest.raises(YouTubeError, match="different approved"):
        service.submit(payload.model_copy(update={"title": "Changed"}))
    with pytest.raises(YouTubeError, match="already has"):
        service.submit(upload_request(service, export_id))
    state["upload_status"], state["privacy"] = "processed", "unlisted"
    service.last_check = 0
    response = client.post(f"/api/youtube-publishing/posts/{job.id}/refresh")
    assert response.json()["status"] == "ready" and response.json()["actual_privacy"] == "unlisted"
    assert "explicit-session" not in response.text and "explicit-access" not in response.text


def test_interruption_resumes_confirmed_remainder_without_new_insert(integration):
    service, state, export_id, _ = integration
    connected(service)
    state["mode"] = "interrupt"
    job = finish(service, upload_request(service, export_id))
    assert job.status == "unknown" and job.can_resume
    with service.store.connection() as db:
        assert (
            b"explicit-session"
            not in db.execute("SELECT secret FROM youtube_upload_sessions").fetchone()[0]
        )
    service.resume(job.id)
    service.active.result(timeout=10)
    job = service.get(job.id)
    assert job.status == "processing" and state["init"] == 1 and state["queries"] == 1
    headers, content = state["uploads"][-1]
    assert headers["content-range"] == f"bytes 256-{job.total_bytes - 1}/{job.total_bytes}"
    assert content == service.store.export_path(export_id).read_bytes()[256:]


def test_lost_final_response_reconciles_without_resending(integration):
    service, state, export_id, _ = integration
    connected(service)
    state["mode"] = "lost-final"
    job = finish(service, upload_request(service, export_id))
    assert job.status == "unknown"
    service.resume(job.id)
    service.active.result(timeout=10)
    assert service.get(job.id).status == "processing"
    assert state["init"] == 1 and len(state["uploads"]) == 1 and state["queries"] == 1


def test_restart_restores_uncertain_session_and_keeps_duplicate_block(integration):
    service, state, export_id, _ = integration
    connected(service)
    state["mode"] = "interrupt"
    job = finish(service, upload_request(service, export_id))
    job.status = "uploading"
    service._save(job)
    service.initialize()
    assert service.get(job.id).status == "unknown"
    with pytest.raises(YouTubeError, match="already has"):
        service.submit(upload_request(service, export_id))
    service.last_check = 0
    assert service.refresh(job.id).uploaded_bytes == 256


def test_resume_delay_expired_session_and_tampered_export_are_safe(integration):
    service, state, export_id, _ = integration
    connected(service)
    state["mode"] = "interrupt"
    job = finish(service, upload_request(service, export_id))
    state["mode"] = "delay"
    job = service.refresh(job.id)
    assert job.status == "paused" and job.resume_after > time.time() + 100
    with pytest.raises(YouTubeError, match="delay"):
        service.resume(job.id)
    job.resume_after = 0
    service._save(job)
    service.last_check = 0
    state["mode"] = "expired"
    with pytest.raises(YouTubeError, match="expired"):
        service.refresh(job.id)
    state["mode"] = "complete"
    path = service.store.export_path(export_id)
    path.write_bytes(b"x" * job.total_bytes)
    service.resume(job.id)
    service.active.result(timeout=10)
    assert (
        service.get(job.id).status == "unknown"
        and len(state["uploads"]) == 1
        and state["init"] == 1
    )


def test_wrong_destination_and_unassignable_category_block_submit(integration):
    service, state, export_id, _ = integration
    connected(service)
    for updates in ({"account_id": "a" * 64}, {"category_id": "999"}):
        with pytest.raises(YouTubeError):
            service.submit(upload_request(service, export_id, **updates))
    assert state["init"] == 0


def test_wrong_channel_response_keeps_outcome_unknown(integration):
    service, state, export_id, _ = integration
    connected(service)
    state["channel"] = "UC_someone_else"
    job = finish(service, upload_request(service, export_id))
    assert job.status == "unknown" and job.video_id is None and job.can_resume


@pytest.mark.parametrize(
    "info",
    [
        MediaInfo(181, 1080, 1920, True),
        MediaInfo(8, 1920, 1080, True),
        MediaInfo(8, 1080, 1920, True, sample_aspect_ratio="2:1"),
    ],
)
def test_non_shorts_media_rejected_before_upload(integration, info):
    service, state, export_id, _ = integration
    connected(service)
    service.media.info = info
    with pytest.raises(YouTubeError, match="Shorts require"):
        service.submit(upload_request(service, export_id))
    assert not state["uploads"]


@pytest.mark.parametrize(
    "updates",
    [
        {"consent": False},
        {"title": " "},
        {"title": "<invalid>"},
        {"description": "😀" * 1251},
        {"made_for_kids": "false"},
        {"privacy": ""},
        {"contains_synthetic_media": None},
    ],
)
def test_approval_metadata_and_utf8_limits(integration, updates):
    service, _, export_id, _ = integration
    connected(service)
    with pytest.raises(ValidationError):
        upload_request(service, export_id, **updates)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/upload/youtube/v3/videos?upload_id=secret",
        "http://www.googleapis.com/upload/youtube/v3/videos?upload_id=secret",
        "https://www.googleapis.com/other?upload_id=secret",
        "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=a#fragment",
        "https://user@www.googleapis.com/upload/youtube/v3/videos?upload_id=a",
    ],
)
def test_session_url_allowlist(integration, url):
    service, _, _, _ = integration
    with pytest.raises(YouTubeError, match="unsafe"):
        service.api.validate_session(url)


def test_nonpublic_dns_redirect_and_response_bound(integration):
    service, _, _, _ = integration
    private = YouTubeAPI(
        service.settings, resolver=lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 443))]
    )
    with pytest.raises(YouTubeError, match="unsafe"):
        private.validate_session("https://www.googleapis.com/upload/youtube/v3/videos?upload_id=a")
    for status, body in [(302, b""), (200, b"x" * 65537)]:
        api = YouTubeAPI(
            service.settings,
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
                    status, content=body, headers={"location": "https://evil.example"}
                )
            ),
        )
        with pytest.raises(YouTubeError):
            api.token(grant_type="refresh_token", refresh_token="explicit")


def test_disconnect_removes_local_tokens_and_reports_failed_remote_revoke(integration):
    service, state, _, _ = integration
    connected(service)
    state["revoke_status"] = 500
    result = service.disconnect()
    assert result["warning"] and not service.status()["connected"]
    service.active = Future()
    with pytest.raises(YouTubeError, match="active"):
        service.disconnect()
    service.active.set_result(None)


def test_application_initializes_and_closes_publisher_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(YouTubeSettings, "load", lambda: YouTubeSettings())
    settings = Settings(
        data_dir=tmp_path / "application", ffmpeg="unused", ffprobe="unused", openai_api_key=""
    )
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/youtube-publishing").json() == {
            "configured": False,
            "connected": False,
            "account_id": None,
            "accounts": [],
        }
        assert client.post("/api/youtube-publishing/connect").status_code == 503
        assert client.get("/api/tiktok").status_code == 200
        assert (
            client.post(
                "/api/youtube-publishing/connect", headers={"origin": "https://foreign.example"}
            ).status_code
            == 403
        )


def test_restart_before_media_transfer_allows_new_reviewed_attempt(integration):
    service, state, export_id, _ = integration
    connected(service)
    job = finish(service, upload_request(service, export_id))
    # Simulate only the durable pre-session record from an interrupted initializer.
    job.video_id, job.can_resume, job.status = None, False, "initializing"
    service._save(job)
    service.initialize()
    assert service.get(job.id).status == "failed"
    assert finish(service, upload_request(service, export_id)).status == "processing"
    assert state["init"] == 2


@pytest.mark.parametrize(
    "headers", [{"range": "bytes=2-9"}, {"range": "bytes=0-100"}, {"retry-after": "invalid"}]
)
def test_malformed_resume_responses_fail_safely(integration, headers):
    service, _, _, _ = integration
    api = YouTubeAPI(
        service.settings,
        transport=httpx2.MockTransport(lambda request: httpx2.Response(308, headers=headers)),
        resolver=service.api.resolver,
    )
    with pytest.raises(YouTubeError):
        api.transfer(
            "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=explicit",
            "explicit-access",
            100,
        )


def test_malformed_nested_provider_data_returns_controlled_errors(integration, monkeypatch):
    service, _, export_id, client = integration
    connected(service)
    job = finish(service, upload_request(service, export_id))
    malformed = {
        "id": job.video_id,
        "snippet": {"channelId": job.channel.id},
        "status": {"uploadStatus": "uploaded", "privacyStatus": "private"},
        "processingDetails": [],
    }
    monkeypatch.setattr(service.api, "get", lambda *args, **kwargs: {"items": [malformed]})
    response = client.post(f"/api/youtube-publishing/posts/{job.id}/refresh")
    assert response.status_code == 502 and "invalid video status" in response.json()["detail"]
    monkeypatch.setattr(
        service.api, "get", lambda *args, **kwargs: {"items": [{"id": "22", "snippet": []}]}
    )
    service.category_cache = None
    with pytest.raises(YouTubeError, match="invalid upload categories"):
        service.categories("explicit-token")
    assert service.get(job.id).status == "processing"


def test_sensitive_urls_are_redacted():
    sanitizer = SensitiveYouTubeLogs()
    for message in (
        "GET /api/youtube-publishing/callback?code=secret-code&state=secret-state HTTP/1.1",
        "HTTP Request: PUT https://www.googleapis.com/upload/youtube/v3/videos?upload_id=secret-session",
    ):
        record = logging.LogRecord("test", logging.INFO, __file__, 1, message, (), None)
        assert sanitizer.filter(record) and "secret-" not in record.getMessage()


def test_exact_caption_warning_available_before_approval(integration):
    service, _, export_id, client = integration
    transcripts = TranscriptStore(service.store)
    transcripts.initialize()
    recording_id = service.store.clip(export_id).recording_id
    transcription_id = str(uuid4())
    transcripts.create(transcription_id, recording_id)
    with service.store.connection() as db:
        db.execute(
            "UPDATE transcriptions SET warning=? WHERE id=?",
            ("Explicit timing fixture warning", transcription_id),
        )
    captioned_id = str(uuid4())
    service.store.create_clip(
        captioned_id,
        recording_id,
        ExportRequest(
            start=0.0,
            end=8.0,
            captions=CaptionOptions(transcription_id=transcription_id, revision=0),
        ),
        caption_words=[],
    )
    service.store.export_path(captioned_id).write_bytes(b"explicit fixture")
    service.store.update_clip(captioned_id, "succeeded", duration=8, size_bytes=16)
    response = client.get(f"/api/youtube-publishing/exports/{captioned_id}")
    assert (
        response.status_code == 200
        and response.json()["caption_warning"] == "Explicit timing fixture warning"
    )


def test_multiple_channels_preserve_approved_destination_and_local_disconnect(integration):
    service, state, export_id, _ = integration
    first = connected(service)
    request = upload_request(service, export_id)
    record, _ = service._stored_token(first)
    second_record = record.model_copy(deep=True)
    second_record.channel.id = "UC_second_fixture"
    _, second = service._store_token(token_response(), previous=second_record)
    assert service.status()["account_id"] is None
    with pytest.raises(YouTubeError, match="Choose"):
        service.creator()
    job = finish(service, request)
    assert job.account_id == first and job.status != "failed", job.error
    service.disconnect(first)
    assert service.status()["account_id"] == second
    assert not any(path == "/revoke" for _, path, _ in state["calls"])
