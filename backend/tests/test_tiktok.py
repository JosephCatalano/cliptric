"""TikTok is simulated at HTTP transport; Windows credential protection is real."""

import hashlib
import json
import logging
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.boundary import RequestBoundary
from backend.media import MediaInfo
from backend.models import CaptionOptions, ExportRequest, Recording
from backend.storage import Store, now
from backend.tiktok import PostJob, PostRequest, TikTok
from backend.tiktok_api import (
    SensitiveTikTokLogs,
    TikTokAPI,
    TikTokError,
    TikTokSettings,
    chunk_plan,
)
from backend.tiktok_routes import create_tiktok_router
from backend.transcription_store import TranscriptStore


class FixtureMedia:
    def probe(self, path):
        return MediaInfo(8, 1080, 1920, True)


def token_response(**updates):
    return {
        "access_token": "unit-access",
        "refresh_token": "unit-refresh",
        "open_id": "unit-account",
        "expires_in": 7200,
        "refresh_expires_in": 86400,
        "scope": "video.publish",
        **updates,
    }


@pytest.fixture
def integration(tmp_path):
    state = {
        "creator": {
            "creator_username": "fixture",
            "creator_nickname": "Fixture Creator",
            "privacy_level_options": ["SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS"],
            "comment_disabled": False,
            "duet_disabled": False,
            "stitch_disabled": True,
            "max_video_post_duration_sec": 120,
        },
        "calls": [],
        "uploads": [],
        "upload_status": 201,
        "post_status": "PUBLISH_COMPLETE",
        "revoke_status": 200,
    }

    def handle(request):
        path = request.url.path
        body = request.read()
        state["calls"].append((path, body))
        if path == "/v2/oauth/token/":
            form = parse_qs(body.decode())
            if form["grant_type"] == ["refresh_token"]:
                return httpx2.Response(
                    200,
                    json=token_response(
                        access_token="unit-access-new", refresh_token="unit-refresh-new"
                    ),
                )
            return httpx2.Response(200, json=token_response())
        if path == "/v2/oauth/revoke/":
            return httpx2.Response(state["revoke_status"], json={})
        if path == "/v2/post/publish/creator_info/query/":
            data = state["creator"]
        elif path == "/v2/post/publish/video/init/":
            data = {
                "publish_id": "unit-publish",
                "upload_url": "https://open-upload.tiktokapis.com/video/?upload_token=unit-signed",
            }
        elif path == "/video/":
            state["uploads"].append((dict(request.headers), body))
            return httpx2.Response(state["upload_status"])
        elif path == "/v2/post/publish/status/fetch/":
            data = {"status": state["post_status"], "publicaly_available_post_id": [123456789]}
        else:
            raise AssertionError("Unexpected fixture endpoint")
        return httpx2.Response(200, json={"data": data, "error": {"code": "ok"}})

    settings = TikTokSettings("unit-key", "unit-secret")
    api = TikTokAPI(
        settings,
        transport=httpx2.MockTransport(handle),
        resolver=lambda *args, **kwargs: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    store = Store(tmp_path / "data")
    service = TikTok(store, FixtureMedia(), settings, "http://127.0.0.1:3000", api=api)
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
    store.export_path(export_id).write_bytes(b"explicit upload fixture" * 200)
    store.update_clip(
        export_id, "succeeded", duration=8, size_bytes=store.export_path(export_id).stat().st_size
    )
    app = FastAPI()
    app.add_middleware(
        RequestBoundary, settings=SimpleNamespace(web_origin=service.web_origin, public_origin="")
    )
    app.include_router(create_tiktok_router(service))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        try:
            yield service, state, export_id, client
        finally:
            service.close()


def connected(service):
    service._store_token(token_response())
    return service.status()["account_id"]


def post_request(service, export_id, **updates):
    return PostRequest(
        id=uuid4(),
        export_id=export_id,
        account_id=service.status()["account_id"],
        caption="An editable caption #example",
        privacy="SELF_ONLY",
        consent=True,
        **updates,
    )


def finish_upload(service, request):
    initial = service.submit(request)
    service.active.result(timeout=5)
    return service.get(initial.id)


def test_oauth_state_pkce_cookie_and_replay(integration):
    service, state, _, client = integration
    response = client.post("/api/tiktok/connect", headers={"Origin": service.web_origin})
    assert response.status_code == 200
    assert (
        "HttpOnly" in response.headers["set-cookie"]
        and "SameSite=lax" in response.headers["set-cookie"]
    )
    query = parse_qs(urlsplit(response.json()["url"]).query)
    nonce = query["state"][0]
    verifier = service.pending[nonce][1]
    assert query["code_challenge"] == [hashlib.sha256(verifier.encode()).hexdigest()]
    assert verifier not in response.json()["url"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8765/api/tiktok/callback"]
    callback = client.get(
        "/api/tiktok/callback",
        params={"state": nonce, "code": "unit-code"},
        headers={"sec-fetch-site": "cross-site"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == service.web_origin + "/connections?tiktok=connected"
    assert callback.headers["referrer-policy"] == "no-referrer"
    assert service.status()["connected"]
    assert b"code_verifier=" in state["calls"][0][1]
    assert (
        client.get("/api/tiktok/callback", params={"state": nonce, "code": "unit-code"}).status_code
        == 400
    )


@pytest.mark.parametrize("failure", ["wrong_cookie", "expired", "cancelled"])
def test_failed_oauth_does_not_store_tokens(integration, failure):
    service, state, _, _ = integration
    nonce, _ = service.begin(service.web_origin)
    if failure == "expired":
        entry = service.pending[nonce]
        service.pending[nonce] = (-1, *entry[1:])
    if failure == "cancelled":
        assert service.finish(nonce, nonce, "", True).endswith("tiktok=cancelled")
    else:
        with pytest.raises(TikTokError):
            service.finish(nonce, "wrong" if failure == "wrong_cookie" else nonce, "code", False)
    assert not service.status()["connected"] and not state["calls"]


def test_origin_exception_is_limited_to_state_checked_callback(integration):
    service, _, _, client = integration
    headers = {"Origin": "https://foreign.example", "sec-fetch-site": "cross-site"}
    assert client.post("/api/tiktok/connect", headers=headers).status_code == 403
    assert client.post("/api/tiktok/callback", headers=headers).status_code == 403
    assert client.get("/api/tiktok/callback", headers=headers).status_code == 400
    assert client.get("/api/tiktok", headers={"Host": "foreign.example"}).status_code == 400
    service.settings = TikTokSettings()
    assert client.post("/api/tiktok/connect").status_code == 503


def test_encrypted_tokens_rotate_and_survive_reopen(integration):
    service, state, _, client = integration
    service._store_token(token_response(expires_in=1))
    service.creator()
    stored, _ = service._stored_token()
    assert stored.refresh_token == "unit-refresh-new"
    assert sum(path == "/v2/oauth/token/" for path, _ in state["calls"]) == 1
    assert b"unit-refresh" not in service.store.database.read_bytes()
    assert "unit-access" not in client.get("/api/tiktok").text
    reopened = TikTok(
        service.store, service.media, service.settings, service.web_origin, api=service.api
    )
    try:
        reopened.initialize()
        assert reopened._stored_token()[0].refresh_token == "unit-refresh-new"
    finally:
        reopened.close()


@pytest.mark.parametrize("remote_status", [200, 500])
def test_disconnect_removes_tokens_and_reports_failed_revocation(integration, remote_status):
    service, state, _, client = integration
    connected(service)
    state["revoke_status"] = remote_status
    response = client.post("/api/tiktok/disconnect")
    assert response.status_code == 200 and not service.status()["connected"]
    assert bool(response.json()["warning"]) == (remote_status == 500)


def test_upload_status_idempotence_and_export_identity(integration):
    service, state, export_id, client = integration
    connected(service)
    request = post_request(service, export_id)
    job = finish_upload(service, request)
    assert job.status == "processing" and job.publish_id == "unit-publish"
    assert job.uploaded_bytes == job.total_bytes
    headers, content = state["uploads"][0]
    assert content == service.store.export_path(export_id).read_bytes()
    assert headers["content-range"] == f"bytes 0-{len(content) - 1}/{len(content)}"
    assert "authorization" not in headers
    initial_payload = json.loads(
        next(body for path, body in state["calls"] if path == "/v2/post/publish/video/init/")
    )
    assert initial_payload["post_info"]["privacy_level"] == "SELF_ONLY"
    assert initial_payload["post_info"]["disable_comment"] is True
    assert initial_payload["source_info"]["source"] == "FILE_UPLOAD"
    assert service.submit(request).id == job.id
    with pytest.raises(TikTokError, match="different approved"):
        service.submit(request.model_copy(update={"caption": "different"}))
    with pytest.raises(TikTokError, match="already has"):
        service.submit(post_request(service, export_id))
    refreshed = client.post(f"/api/tiktok/posts/{job.id}/refresh")
    assert refreshed.status_code == 200 and refreshed.json()["status"] == "published"
    assert refreshed.json()["post_ids"] == ["123456789"]
    assert b"upload_token" not in service.store.database.read_bytes()


def test_uncertain_upload_is_reconciled_without_resending(integration):
    service, state, export_id, _ = integration
    connected(service)
    state["upload_status"] = 500
    job = finish_upload(service, post_request(service, export_id))
    assert job.status == "unknown" and job.publish_id
    with pytest.raises(TikTokError, match="already has"):
        service.submit(post_request(service, export_id))
    service.initialize()
    assert service.get(job.id).status == "unknown"
    assert service.refresh(job.id).status == "published"
    assert len(state["uploads"]) == 1


@pytest.mark.parametrize(
    "constraint", ["duration", "interaction", "privacy", "public_test_account", "changed_account"]
)
def test_latest_creator_constraints_block_before_upload(integration, constraint):
    service, state, export_id, _ = integration
    connected(service)
    request = post_request(service, export_id)
    if constraint == "duration":
        state["creator"]["max_video_post_duration_sec"] = 4
    if constraint == "interaction":
        request = request.model_copy(update={"allow_stitch": True})
    if constraint == "privacy":
        request = request.model_copy(update={"privacy": "PUBLIC_TO_EVERYONE"})
    if constraint == "public_test_account":
        state["creator"]["privacy_level_options"].append("PUBLIC_TO_EVERYONE")
    if constraint == "changed_account":
        request = request.model_copy(update={"account_id": "0" * 64})
    with pytest.raises(TikTokError):
        service.submit(request)
    assert not state["uploads"]
    assert all(path != "/v2/post/publish/video/init/" for path, _ in state["calls"])


@pytest.mark.parametrize(
    "change",
    [
        {"consent": False},
        {"commercial": True},
        {"branded_content": True, "commercial": True},
        {"caption": "😀" * 1101},
    ],
)
def test_approval_disclosure_and_utf16_limits(integration, change):
    service, _, export_id, client = integration
    connected(service)
    payload = post_request(service, export_id).model_dump(mode="json")
    payload.update(change)
    assert client.post("/api/tiktok/posts", json=payload).status_code == 422
    with pytest.raises(ValidationError):
        PostRequest.model_validate(payload)


@pytest.mark.parametrize("size", [1, 4_194_304, 16_777_216, 50_000_123, 512 * 1024**2])
def test_chunk_plan_accounts_for_every_byte_and_trailing_remainder(size):
    chunk, ranges = chunk_plan(size)
    assert ranges[0][0] == 0 and ranges[-1][1] == size
    assert len(ranges) == max(1, size // chunk)
    assert sum(end - start for start, end in ranges) == size
    assert all(left[1] == right[0] for left, right in zip(ranges, ranges[1:]))
    assert ranges[-1][1] - ranges[-1][0] <= 128 * 1024**2


@pytest.mark.parametrize(
    "url",
    [
        "http://open-upload.tiktokapis.com/video/",
        "https://open-upload.tiktokapis.com.attacker.example/video/",
        "https://127.0.0.1/video/",
        "https://user:pass@open-upload.tiktokapis.com/video/",
        "https://open-upload.tiktokapis.com:444/video/",
    ],
)
def test_upload_destination_rejected_before_network(integration, url):
    service, _, _, _ = integration
    with pytest.raises(TikTokError, match="destination"):
        service.api.validate_upload_url(url)


def test_private_upload_dns_and_oversized_responses_fail_safely(integration):
    service, _, _, _ = integration
    service.api.resolver = lambda *args, **kwargs: [(None, None, None, None, ("127.0.0.1", 443))]
    with pytest.raises(TikTokError):
        service.api.validate_upload_url("https://open-upload.tiktokapis.com/video/")
    api = TikTokAPI(
        service.settings,
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, content=b"x" * 65537)),
    )
    with pytest.raises(TikTokError, match="oversized"):
        api.request("/v2/oauth/token/")


def test_sensitive_callback_and_upload_urls_are_redacted():
    sanitizer = SensitiveTikTokLogs()
    for message in (
        "GET /api/tiktok/callback?code=secret-code&state=secret-state HTTP/1.1",
        "HTTP Request: PUT https://open-upload.tiktokapis.com/video/?upload_token=secret-token",
    ):
        record = logging.LogRecord("test", logging.INFO, __file__, 1, message, (), None)
        assert sanitizer.filter(record)
        assert "secret-" not in record.getMessage()


def test_caption_warning_follows_exact_export_snapshot(integration):
    service, _, export_id, client = integration
    recording_id = service.store.clip(export_id).recording_id
    transcripts = TranscriptStore(service.store)
    transcripts.initialize()
    transcription_id = str(uuid4())
    transcripts.create(transcription_id, recording_id)
    with service.store.connection() as db:
        db.execute(
            "UPDATE transcriptions SET warning=? WHERE id=?",
            ("Explicit timing-review fixture", transcription_id),
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
    response = client.get(f"/api/tiktok/exports/{captioned_id}")
    assert response.status_code == 200
    assert response.json()["caption_warning"] == "Explicit timing-review fixture"


def test_restart_before_media_transfer_allows_reviewed_new_attempt(integration):
    service, state, export_id, _ = integration
    account_id = connected(service)
    request = post_request(service, export_id)
    job = PostJob(
        id=str(request.id),
        export_id=export_id,
        account_id=account_id,
        created_at=now(),
        request=request,
        status="initializing",
        total_bytes=service.store.export_path(export_id).stat().st_size,
    )
    service._save(job)
    service.initialize()
    assert service.get(job.id).status == "failed" and not state["calls"]
    assert finish_upload(service, post_request(service, export_id)).status == "processing"


def test_multiple_accounts_keep_approved_upload_destination(integration):
    service, _, export_id, _ = integration
    first = connected(service)
    request = post_request(service, export_id)
    _, second = service._store_token(
        token_response(open_id="second-fixture-account", access_token="second-access")
    )
    assert service.status()["account_id"] is None
    with pytest.raises(TikTokError, match="Choose"):
        service.creator()
    assert finish_upload(service, request).account_id == first
    assert service.get(str(request.id)).status != "failed"
    second_request = request.model_copy(update={"id": uuid4(), "account_id": second})
    assert finish_upload(service, second_request).account_id == second
    service.disconnect(first)
    assert service.status()["account_id"] == second
    assert service.creator(second)["account_id"] == second
