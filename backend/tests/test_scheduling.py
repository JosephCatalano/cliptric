"""Scheduler unit tests use explicit publishing doubles; no remote posts are sent."""

from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.scheduling import Scheduler, ScheduleRequest, create_scheduling_router
from backend.storage import Store
from backend.tiktok_api import TikTokError


@pytest.fixture
def scheduled(tmp_path):
    store = Store(tmp_path)
    path = tmp_path / "exports" / "approved.mp4"
    path.write_bytes(b"explicit test export bytes")
    with store.connection() as db:
        for table in (
            "tiktok_posts",
            "youtube_uploads",
            "instagram_posts",
            "x_posts",
            "facebook_posts",
        ):
            db.execute(
                f"CREATE TABLE {table}(id TEXT,export_id TEXT,account_id TEXT,status TEXT,created_at TEXT,payload TEXT)"
            )

    class PublishingDouble:
        account = "a" * 64
        failure = None

        def __init__(self):
            self.calls = []

        def creator(self, account_id=None):
            return {"account_id": self.account}

        def export(self, identifier):
            return SimpleNamespace(), path

        def validate_post(self, request, creator=None):
            if self.account != request.account_id:
                raise HTTPException(409, "The connected account changed.")
            return self.export(str(request.export_id))

        def submit(self, request):
            if self.failure:
                raise self.failure
            self.calls.append(request)

    publishing = PublishingDouble()
    scheduler = Scheduler(
        store, publishing, publishing, instagram=publishing, x=publishing, facebook=publishing
    )
    scheduler.initialize()
    return scheduler, publishing, path


def payload(platform="tiktok", **updates):
    request = dict(id=str(uuid4()), export_id=str(uuid4()), account_id="a" * 64, consent=True)
    if platform != "youtube":
        request.update(caption="Approved caption")
        if platform == "tiktok":
            request.update(privacy="SELF_ONLY")
    else:
        request.update(
            title="Approved title",
            category_id="22",
            privacy="private",
            made_for_kids=False,
            contains_synthetic_media=False,
        )
    return ScheduleRequest.model_validate(
        {
            "scheduled_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "post": dict(platform=platform, request=request),
            **updates,
        }
    )


def make_due(scheduler, identifier, minutes=0):
    with scheduler.store.connection() as db:
        db.execute(
            "UPDATE scheduled_posts SET scheduled_at=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(), identifier),
        )


def row(scheduler, identifier):
    with scheduler.store.connection() as db:
        return dict(
            db.execute("SELECT * FROM scheduled_posts WHERE id=?", (identifier,)).fetchone()
        )


def test_publish_batch_is_atomic_and_survives_restart(scheduled):
    scheduler, publishing, _ = scheduled
    posts = [
        payload(platform).post.model_dump(mode="json")
        for platform in ("youtube", "x", "instagram", "facebook", "tiktok")
    ]
    app = FastAPI()
    app.include_router(create_scheduling_router(scheduler))
    with TestClient(app) as client:
        body = {"id": str(uuid4()), "posts": posts}
        duplicate = payload("tiktok", post=posts[-1])
        scheduler.create(duplicate)
        assert client.post("/api/schedule/batch", json=body).status_code == 409
        assert client.get(f"/api/schedule/batch/{body['id']}").status_code == 404
        assert publishing.calls == []
        scheduler.cancel(str(duplicate.post.request.id))
        # The cancelled appointment's request ID is still reserved.
        posts[-1]["request"]["id"] = str(uuid4())
        response = client.post("/api/schedule/batch", json=body)
        assert response.status_code == 202
        assert [item["status"] for item in response.json()["posts"]] == ["scheduled"] * 5
        scheduler.initialize()
        for post in posts:
            make_due(scheduler, post["request"]["id"], minutes=10)
        # Delaying one platform still lets the other approved posts start.
        youtube = type(publishing)()
        youtube.active = Future()
        scheduler.services["youtube"] = youtube
        for _ in posts:
            scheduler.tick()
        assert [call.id for call in publishing.calls] == [
            UUID(post["request"]["id"]) for post in posts[1:]
        ]
        assert not youtube.calls
        youtube.active.set_result(None)
        scheduler.tick()
        assert len(youtube.calls) == 1
        assert client.post("/api/schedule/batch", json=body).status_code == 202
        scheduler.tick()
        assert len(youtube.calls) == 1 and len(publishing.calls) == 4


@pytest.mark.parametrize("platform", ["tiktok", "youtube", "instagram", "x", "facebook"])
def test_approved_post_waits_until_due_and_dispatches_once(scheduled, platform):
    scheduler, publishing, _ = scheduled
    request = payload(platform)
    identifier = scheduler.create(request)["id"]
    scheduler.tick()
    assert publishing.calls == []
    make_due(scheduler, identifier)
    scheduler.tick()
    scheduler.tick()
    assert publishing.calls == [request.post.request]
    assert row(scheduler, identifier)["status"] == "submitted"


def test_idempotency_and_conflicting_reuse(scheduled):
    scheduler, _, _ = scheduled
    request = payload()
    assert scheduler.create(request) == scheduler.create(request)
    changed = request.model_copy(update={"scheduled_at": request.scheduled_at + timedelta(hours=1)})
    with pytest.raises(HTTPException, match="different settings"):
        scheduler.create(changed)


def test_duplicate_destination_and_cancel(scheduled):
    scheduler, publishing, _ = scheduled
    request = payload()
    identifier = scheduler.create(request)["id"]
    duplicate = request.model_copy(deep=True)
    duplicate.post.request.id = uuid4()
    with pytest.raises(HTTPException, match="already has a scheduled"):
        scheduler.create(duplicate)
    scheduler.cancel(identifier)
    with pytest.raises(HTTPException, match="changed status"):
        scheduler.create(request)
    make_due(scheduler, identifier)
    scheduler.tick()
    assert not publishing.calls
    assert scheduler.create(duplicate)
    with pytest.raises(HTTPException):
        scheduler.cancel(identifier)


def test_changed_video_is_not_sent(scheduled):
    scheduler, publishing, path = scheduled
    identifier = scheduler.create(payload())["id"]
    path.write_bytes(b"tampered video")
    make_due(scheduler, identifier)
    scheduler.tick()
    assert not publishing.calls
    assert row(scheduler, identifier)["status"] == "failed"
    assert "video changed" in row(scheduler, identifier)["error"]


def test_failed_handoff_can_be_scheduled_again_after_reconciliation(scheduled):
    scheduler, _, _ = scheduled
    request = payload()
    identifier = scheduler.create(request)["id"]
    make_due(scheduler, identifier)
    scheduler.tick()
    with scheduler.store.connection() as db:
        db.execute(
            "INSERT INTO tiktok_posts(id,export_id,account_id,status,payload) VALUES (?,?,?,'failed',?)",
            (
                identifier,
                str(request.post.request.export_id),
                request.post.request.account_id,
                '{"error":"Reconnect the connected TikTok account."}',
            ),
        )
    scheduler.tick()
    assert row(scheduler, identifier)["status"] == "failed"
    assert row(scheduler, identifier)["error"] == "Reconnect the connected TikTok account."
    replacement = request.model_copy(deep=True)
    replacement.post.request.id = uuid4()
    assert scheduler.create(replacement)


def test_interrupted_handoff_with_durable_job_is_reconciled_without_resubmit(scheduled):
    scheduler, publishing, _ = scheduled
    request = payload()
    identifier = scheduler.create(request)["id"]
    with scheduler.store.connection() as db:
        db.execute("UPDATE scheduled_posts SET status='dispatching' WHERE id=?", (identifier,))
        db.execute(
            "INSERT INTO tiktok_posts(id,export_id,account_id,status) VALUES (?,?,?,'processing')",
            (identifier, str(request.post.request.export_id), request.post.request.account_id),
        )
    scheduler.initialize()
    scheduler.tick()
    assert row(scheduler, identifier)["status"] == "submitted"
    assert not publishing.calls


def test_missed_posts_and_restart_do_not_repost(scheduled):
    scheduler, publishing, _ = scheduled
    identifier = scheduler.create(payload())["id"]
    make_due(scheduler, identifier, minutes=6)
    scheduler.tick()
    assert row(scheduler, identifier)["status"] == "missed"
    second = scheduler.create(payload())["id"]
    with scheduler.store.connection() as db:
        db.execute("UPDATE scheduled_posts SET status='dispatching' WHERE id=?", (second,))
    scheduler.initialize()
    scheduler.tick()
    assert row(scheduler, second)["status"] == "unknown"
    assert not publishing.calls


@pytest.mark.parametrize(
    "failure,status",
    [
        (TikTokError("Account disconnected", 409), "failed"),
        (RuntimeError("interrupted"), "unknown"),
    ],
)
def test_dispatch_failure_is_visible_without_retry(scheduled, failure, status):
    scheduler, publishing, _ = scheduled
    identifier = scheduler.create(payload())["id"]
    publishing.failure = failure
    make_due(scheduler, identifier)
    scheduler.tick()
    scheduler.tick()
    assert row(scheduler, identifier)["status"] == status
    assert row(scheduler, identifier)["error"]
    assert not publishing.calls


@pytest.mark.parametrize("delta", [-1, 0, 366 * 86400])
def test_invalid_schedule_window(scheduled, delta):
    scheduler, _, _ = scheduled
    request = payload(scheduled_at=datetime.now(timezone.utc) + timedelta(seconds=delta))
    with pytest.raises(HTTPException):
        scheduler.create(request)


def test_account_must_match_and_prior_upload_blocks_schedule(scheduled):
    scheduler, publishing, _ = scheduled
    publishing.account = "b" * 64
    with pytest.raises(HTTPException, match="account changed"):
        scheduler.create(payload())
    publishing.account = "a" * 64
    request = payload()
    with scheduler.store.connection() as db:
        db.execute(
            "INSERT INTO tiktok_posts(export_id,account_id,status) VALUES (?,?,'unknown')",
            (str(request.post.request.export_id), publishing.account),
        )
    with pytest.raises(HTTPException, match="publishing attempt"):
        scheduler.create(request)


def test_offset_dates_are_normalized_and_listing_is_bounded(scheduled):
    scheduler, _, _ = scheduled
    request = payload()
    request.scheduled_at = request.scheduled_at.astimezone(timezone(timedelta(hours=-5)))
    scheduler.create(request)
    start = datetime.now(timezone.utc)
    listing = scheduler.list(start, start + timedelta(days=1))
    assert len(listing) == 1
    assert listing[0]["scheduled_at"].endswith("+00:00")
    assert "payload" not in listing[0]
    with pytest.raises(HTTPException):
        scheduler.list(start, start + timedelta(days=63))


def test_api_requires_consent_and_aware_dates(scheduled):
    scheduler, _, _ = scheduled
    app = FastAPI()
    app.include_router(create_scheduling_router(scheduler))
    with TestClient(app) as client:
        request = payload().model_dump(mode="json")
        request["post"]["request"]["consent"] = False
        assert client.post("/api/schedule", json=request).status_code == 422
        assert (
            client.get(
                "/api/schedule", params={"start": "2026-11-18", "end": "2026-11-19"}
            ).status_code
            == 422
        )
    with pytest.raises(ValidationError):
        payload(scheduled_at=datetime.now())
