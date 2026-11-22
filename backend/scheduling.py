"""Durable local scheduling of explicitly approved social posts."""

import hashlib
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

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


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheduled_at: AwareDatetime
    post: Annotated[TikTokSchedule | YouTubeSchedule, Field(discriminator="platform")]


class Scheduler:
    def __init__(self, store, tiktok, youtube):
        self.store = store
        self.services = {"tiktok": tiktok, "youtube": youtube}
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

    def create(self, payload):
        request = payload.post.request
        identifier = str(request.id)
        scheduled = payload.scheduled_at.astimezone(timezone.utc)
        canonical = payload.model_copy(update={"scheduled_at": scheduled}).model_dump_json()
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
            service = self.services[payload.post.platform]
            validated = service.validate_post(request)
            digest = self.digest(validated[-1])
            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                duplicate = db.execute(
                    "SELECT 1 FROM scheduled_posts WHERE platform=? AND export_id=? AND account_id=? AND status IN ('scheduled','dispatching','submitted','unknown')",
                    (payload.post.platform, str(request.export_id), request.account_id),
                ).fetchone()
                if duplicate:
                    raise HTTPException(
                        409, "This export already has a scheduled post. Check the calendar."
                    )
                table = "tiktok_posts" if payload.post.platform == "tiktok" else "youtube_uploads"
                if db.execute(
                    f"SELECT 1 FROM {table} WHERE export_id=? AND account_id=? AND status!='failed'",
                    (str(request.export_id), request.account_id),
                ).fetchone():
                    raise HTTPException(
                        409, "This export already has a publishing attempt. Check its saved status."
                    )
                if (
                    db.execute(
                        "SELECT count(*) FROM scheduled_posts WHERE status='scheduled'"
                    ).fetchone()[0]
                    >= 200
                ):
                    raise HTTPException(
                        409, "The calendar supports up to 200 pending posts. Cancel a post first."
                    )
                db.execute(
                    "INSERT INTO scheduled_posts VALUES (?,?,?,?,?,'scheduled',?,?,NULL)",
                    (
                        identifier,
                        payload.post.platform,
                        str(request.export_id),
                        request.account_id,
                        scheduled.isoformat(),
                        canonical,
                        digest,
                    ),
                )
        return {"id": identifier}

    def list(self, start, end):
        if end <= start or end - start > timedelta(days=62):
            raise HTTPException(422, "Choose a calendar range of up to 62 days.")
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT id,platform,export_id,scheduled_at,status,error,payload FROM scheduled_posts WHERE scheduled_at>=? AND scheduled_at<? ORDER BY scheduled_at",
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
                else request.caption or "TikTok post"
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

    def tick(self):
        current = datetime.now(timezone.utc)
        with self.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            # Reconcile durable local handoffs without repeating remote requests.
            for platform, table in (("tiktok", "tiktok_posts"), ("youtube", "youtube_uploads")):
                db.execute(
                    f"UPDATE scheduled_posts SET status='failed',error=? WHERE platform=? "
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
            row = db.execute(
                "SELECT * FROM scheduled_posts WHERE status='scheduled' AND scheduled_at<=? ORDER BY scheduled_at LIMIT 1",
                (current.isoformat(),),
            ).fetchone()
            if not row:
                return
            db.execute("UPDATE scheduled_posts SET status='dispatching' WHERE id=?", (row["id"],))
        status, error = "submitted", None
        try:
            if current - datetime.fromisoformat(row["scheduled_at"]) > timedelta(minutes=5):
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
                service.submit(payload.post.request)
        except (TikTokError, YouTubeError, HTTPException) as exc:
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
        except (TikTokError, YouTubeError) as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @router.post("/{identifier}/cancel")
    def cancel(identifier: UUID):
        return service.cancel(str(identifier))

    return router
