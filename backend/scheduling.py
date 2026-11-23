"""Durable local scheduling of explicitly approved social posts."""

import hashlib
import json
import logging
import threading
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .instagram import ReelRequest
from .instagram_api import InstagramError
from .social_publishing import SocialRequest
from .social_transport import SocialError
from .tiktok import PostRequest
from .tiktok_api import TikTokError
from .youtube_api import YouTubeError
from .youtube_publishing import UploadRequest

logger = logging.getLogger(__name__)


class TikTokSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["tiktok"]
    request: PostRequest


class YouTubeSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["youtube"]
    request: UploadRequest


class InstagramSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["instagram"]
    request: ReelRequest


class XSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["x"]
    request: SocialRequest


class FacebookSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["facebook"]
    request: SocialRequest


POST_TABLES = {
    "tiktok": "tiktok_posts",
    "youtube": "youtube_uploads",
    "instagram": "instagram_posts",
    "x": "x_posts",
    "facebook": "facebook_posts",
}
PUBLISHING_ERRORS = (TikTokError, YouTubeError, InstagramError, SocialError)


BatchPost = Annotated[YouTubeSchedule | XSchedule | TikTokSchedule, Field(discriminator="platform")]


class BatchPostRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    posts: list[BatchPost] = Field(min_length=1, max_length=225)


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheduled_at: AwareDatetime
    post: Annotated[
        TikTokSchedule | YouTubeSchedule | InstagramSchedule | XSchedule | FacebookSchedule,
        Field(discriminator="platform"),
    ]


class Scheduler:
    def __init__(self, store, tiktok, youtube, **publishers):
        self.store = store
        self.services = {"tiktok": tiktok, "youtube": youtube, **publishers}
        if not self.services.keys() <= POST_TABLES.keys():
            raise ValueError("Unsupported scheduling platform")
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = None

    def initialize(self):
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    id TEXT PRIMARY KEY, platform TEXT NOT NULL, export_id TEXT NOT NULL,
                    account_id TEXT NOT NULL, scheduled_at TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS scheduled_posts_due ON scheduled_posts(status,scheduled_at);
                CREATE UNIQUE INDEX IF NOT EXISTS scheduled_posts_destination
                ON scheduled_posts(platform,export_id,account_id)
                WHERE status IN ('scheduled','dispatching','submitted','unknown');
            """)
            if not any(
                row["name"] == "batch_id"
                for row in db.execute("PRAGMA table_info(scheduled_posts)")
            ):
                db.execute("ALTER TABLE scheduled_posts ADD COLUMN batch_id TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS scheduled_posts_batch ON scheduled_posts(batch_id)"
            )
            # A crash may occur after provider submission but before our final write.
            # Never automatically repeat that boundary; the existing review can reconcile it.
            db.execute(
                "UPDATE scheduled_posts SET status='unknown',error=? WHERE status='dispatching'",
                (
                    "Scheduling was interrupted. Open the saved publishing review to check its status.",
                ),
            )

    def start(self):
        self.thread = threading.Thread(target=self._run, name="post-scheduler", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join()

    @staticmethod
    def digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    @staticmethod
    def _insert(db, payload, scheduled, digest, batch_id=None):
        platform, request = payload.post.platform, payload.post.request
        target = (str(request.export_id), request.account_id)
        if db.execute("SELECT 1 FROM scheduled_posts WHERE id=?", (str(request.id),)).fetchone():
            raise HTTPException(409, "This posting token already belongs to another approval.")
        if db.execute(
            "SELECT 1 FROM scheduled_posts WHERE platform=? AND export_id=? AND account_id=? "
            "AND status IN ('scheduled','dispatching','submitted','unknown')",
            (platform, *target),
        ).fetchone():
            raise HTTPException(
                409, "This export already has a scheduled or queued post. Check the calendar."
            )
        if db.execute(
            f"SELECT 1 FROM {POST_TABLES[platform]} WHERE export_id=? AND account_id=? AND status!='failed'",
            target,
        ).fetchone():
            raise HTTPException(
                409, "This export already has a publishing attempt. Check its saved status."
            )
        db.execute(
            "INSERT INTO scheduled_posts (id,platform,export_id,account_id,scheduled_at,status,payload,sha256,batch_id) "
            "VALUES (?,?,?,?,?,'scheduled',?,?,?)",
            (
                str(request.id),
                platform,
                *target,
                scheduled.isoformat(),
                payload.model_dump_json(),
                digest,
                batch_id,
            ),
        )

    def create(self, payload):
        request = payload.post.request
        identifier = str(request.id)
        scheduled = payload.scheduled_at.astimezone(timezone.utc)
        payload = payload.model_copy(update={"scheduled_at": scheduled})
        canonical = payload.model_dump_json()
        with self.lock:
            with self.store.connection() as db:
                existing = db.execute(
                    "SELECT payload,status FROM scheduled_posts WHERE id=?", (identifier,)
                ).fetchone()
                if existing:
                    if existing[0] != canonical:
                        raise HTTPException(
                            409, "This scheduling token belongs to different settings."
                        )
                    if existing[1] != "scheduled":
                        raise HTTPException(
                            409,
                            "This scheduled post has already changed status. Check the calendar before scheduling again.",
                        )
                    return {"id": identifier}
            current = datetime.now(timezone.utc)
            if not current + timedelta(seconds=60) <= scheduled <= current + timedelta(days=365):
                raise HTTPException(
                    422, "Choose a time at least one minute ahead and within one year."
                )
            service = self.services.get(payload.post.platform)
            if service is None:
                raise HTTPException(503, "This publishing platform is unavailable.")
            validated = service.validate_post(request)
            digest = self.digest(validated[-1])
            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                if (
                    db.execute(
                        "SELECT count(*) FROM scheduled_posts WHERE status='scheduled' AND batch_id IS NULL"
                    ).fetchone()[0]
                    >= 200
                ):
                    raise HTTPException(
                        409, "The calendar supports up to 200 pending posts. Cancel a post first."
                    )
                self._insert(db, payload, scheduled, digest)
        return {"id": identifier}

    def create_batch(self, payload):
        batch_id = str(payload.id)
        posts = payload.posts
        exports = {post.request.export_id for post in posts}
        if (
            len(exports) > 75
            or len({post.request.id for post in posts}) != len(posts)
            or len({(post.platform, post.request.export_id) for post in posts}) != len(posts)
        ):
            raise HTTPException(
                422, "Choose up to 75 exports and one account per clip and platform."
            )
        with self.lock:
            with self.store.connection() as db:
                saved = db.execute(
                    "SELECT payload FROM scheduled_posts WHERE batch_id=? ORDER BY rowid",
                    (batch_id,),
                ).fetchall()
            if saved:
                if [ScheduleRequest.model_validate_json(row[0]).post for row in saved] != posts:
                    raise HTTPException(
                        409, "This batch token belongs to different approved posts."
                    )
                return self.batch(batch_id)

            creators, digests, approvals = {}, {}, []
            current = datetime.now(timezone.utc)
            for post in posts:
                platform, request = post.platform, post.request
                service = self.services.get(platform)
                if service is None:
                    raise HTTPException(503, "This publishing platform is unavailable.")
                destination = (platform, request.account_id)
                if destination not in creators:
                    creators[destination] = service.creator(request.account_id)
                path = service.validate_post(request, creators[destination])[-1]
                if path not in digests:
                    digests[path] = self.digest(path)
                approvals.append((ScheduleRequest(scheduled_at=current, post=post), digests[path]))

            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                pending = db.execute(
                    "SELECT count(*) FROM scheduled_posts WHERE status='scheduled' AND batch_id IS NOT NULL"
                ).fetchone()[0]
                if pending + len(posts) > 225:
                    raise HTTPException(
                        409,
                        "Wait for queued posts to finish or cancel pending posts in the calendar.",
                    )
                for approval, digest in approvals:
                    self._insert(db, approval, current, digest, batch_id)
        return self.batch(batch_id)

    def batch(self, identifier):
        with self.store.connection() as db:
            result = []
            for row in db.execute(
                "SELECT id,platform,export_id,account_id,status,error FROM scheduled_posts WHERE batch_id=? ORDER BY rowid",
                (identifier,),
            ):
                item = dict(row)
                table = POST_TABLES[item["platform"]]
                job = db.execute(
                    f"SELECT status,payload FROM {table} WHERE id=?", (item["id"],)
                ).fetchone()
                if job:
                    item["post_status"] = job["status"]
                    item["error"] = json.loads(job["payload"]).get("error") or item["error"]
                result.append(item)
        if not result:
            raise HTTPException(404, "Posting batch not found.")
        return {"id": identifier, "posts": result}

    def list(self, start, end):
        if end <= start or end - start > timedelta(days=62):
            raise HTTPException(422, "Choose a calendar range of up to 62 days.")
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT id,platform,export_id,account_id,scheduled_at,status,error,payload,batch_id FROM scheduled_posts WHERE scheduled_at>=? AND scheduled_at<? ORDER BY scheduled_at",
                (
                    start.astimezone(timezone.utc).isoformat(),
                    end.astimezone(timezone.utc).isoformat(),
                ),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            payload = ScheduleRequest.model_validate_json(item.pop("payload"))
            request = payload.post.request
            item["title"] = (
                request.title
                if isinstance(request, UploadRequest)
                else request.caption or f"{payload.post.platform} post"
            )
            results.append(item)
        return results

    def cancel(self, identifier):
        with self.lock, self.store.connection() as db:
            changed = db.execute(
                "UPDATE scheduled_posts SET status='cancelled' WHERE id=? AND status='scheduled'",
                (identifier,),
            ).rowcount
            if not changed:
                raise HTTPException(
                    409, "Only pending posts can be cancelled. Refresh the calendar."
                )
        return {"id": identifier}

    def _batch_can_dispatch(self, platform, db):
        active = getattr(self.services[platform], "active", None)
        if active is not None and not active.done():
            return False
        if platform in {"youtube", "tiktok"}:
            table = POST_TABLES[platform]
            return (
                db.execute(
                    f"SELECT count(*) FROM {table} WHERE julianday(created_at)>julianday('now','-1 minute')"
                ).fetchone()[0]
                < 6
            )
        return True

    def tick(self):
        current = datetime.now(timezone.utc)
        with self.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            # Reconcile durable local handoffs without repeating remote requests.
            for platform in self.services:
                table = POST_TABLES[platform]
                db.execute(
                    f"UPDATE scheduled_posts SET status='failed',error=COALESCE(("
                    f"SELECT CASE WHEN json_valid(payload) THEN "
                    f"NULLIF(TRIM(json_extract(payload,'$.error')),'') END FROM {table} "
                    f"WHERE {table}.id=scheduled_posts.id AND {table}.status='failed'"
                    f"),?) WHERE platform=? "
                    f"AND status IN ('submitted','unknown') AND EXISTS "
                    f"(SELECT 1 FROM {table} WHERE {table}.id=scheduled_posts.id AND {table}.status='failed')",
                    (
                        "The publishing attempt failed. Open Publishing review before scheduling again.",
                        platform,
                    ),
                )
                db.execute(
                    f"UPDATE scheduled_posts SET status='submitted',error=NULL WHERE platform=? "
                    f"AND status='unknown' AND EXISTS "
                    f"(SELECT 1 FROM {table} WHERE {table}.id=scheduled_posts.id AND {table}.status!='failed')",
                    (platform,),
                )
            rows = db.execute(
                "SELECT * FROM scheduled_posts WHERE status='scheduled' AND scheduled_at<=? "
                "ORDER BY batch_id IS NOT NULL, scheduled_at, rowid",
                (current.isoformat(),),
            ).fetchall()
            available = {}
            row = None
            for candidate in rows:
                platform = candidate["platform"]
                if candidate["batch_id"] is not None:
                    if platform not in available:
                        available[platform] = self._batch_can_dispatch(platform, db)
                    if not available[platform]:
                        continue
                row = candidate
                break
            if not row:
                return
            db.execute("UPDATE scheduled_posts SET status='dispatching' WHERE id=?", (row["id"],))
        status, error = "submitted", None
        try:
            if row["batch_id"] is None and current - datetime.fromisoformat(
                row["scheduled_at"]
            ) > timedelta(minutes=5):
                status, error = (
                    "missed",
                    "The local API was unavailable at the scheduled time. Schedule a new post.",
                )
            else:
                payload = ScheduleRequest.model_validate_json(row["payload"])
                service = self.services[row["platform"]]
                exported = service.export(row["export_id"])
                if self.digest(exported[1]) != row["sha256"]:
                    raise HTTPException(
                        409, "The approved video changed. Review and schedule it again."
                    )
                if row["batch_id"] is None:
                    service.submit(payload.post.request)
                else:
                    with getattr(service, "lock", nullcontext()):
                        with self.store.connection() as db:
                            available = self._batch_can_dispatch(row["platform"], db)
                        if available:
                            service.submit(payload.post.request)
                        else:
                            status = "scheduled"
        except (*PUBLISHING_ERRORS, HTTPException) as exc:
            status, error = (
                "failed",
                str(exc.detail) if isinstance(exc, HTTPException) else str(exc),
            )
        except Exception:
            logger.exception("Scheduled post dispatch failed for %s", row["id"])
            status, error = (
                "unknown",
                "Publishing could not be confirmed. Open the saved review before trying again.",
            )
        with self.store.connection() as db:
            db.execute(
                "UPDATE scheduled_posts SET status=?,error=? WHERE id=?", (status, error, row["id"])
            )

    def _run(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Local post scheduler failed; pending posts remain in storage")
            self.stop.wait(5)


def create_scheduling_router(service):
    router = APIRouter(prefix="/api/schedule")

    @router.get("")
    def listing(start: AwareDatetime, end: AwareDatetime):
        return service.list(start, end)

    @router.get("/exports")
    def exports(limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
        with service.store.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT clips.id,recordings.name,clips.start,clips.end FROM clips JOIN recordings ON recordings.id=clips.recording_id WHERE clips.status='succeeded' ORDER BY clips.created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    @router.post("", status_code=201)
    def create(payload: ScheduleRequest):
        try:
            return service.create(payload)
        except PUBLISHING_ERRORS as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @router.post("/batch", status_code=202)
    def create_batch(payload: BatchPostRequest):
        try:
            return service.create_batch(payload)
        except PUBLISHING_ERRORS as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @router.get("/batch/{identifier}")
    def batch(identifier: UUID):
        return service.batch(str(identifier))

    @router.post("/{identifier}/cancel")
    def cancel(identifier: UUID):
        return service.cancel(str(identifier))

    return router
