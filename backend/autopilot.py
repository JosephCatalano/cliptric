"""Autopilot enrollment and review over the existing Twitch and project workers."""

import json
import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .project_models import ACTIVE, Project

MEMBER = "EXISTS (SELECT 1 FROM autopilot_scans a WHERE a.scan_id=s.id)"
POLL_ENABLED = (
    "(NOT "
    + MEMBER
    + " OR EXISTS (SELECT 1 FROM autopilot_scans a CROSS JOIN autopilot_settings b "
    "WHERE a.scan_id=s.id AND a.scan_revision=s.revision AND b.id=1 AND b.enabled=1))"
)


class AutopilotUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)
    enabled: bool = Field(strict=True)
    mode: Literal["discover", "prepare"]
    scan_ids: list[UUID] = Field(max_length=50)
    ai_review: bool = Field(default=False, strict=True)
    ai_consent: bool = Field(default=False, strict=True)
    minimum_seconds: int = Field(default=0, strict=True, ge=0, le=60)
    require_captions: bool = Field(default=False, strict=True)


class Autopilot:
    def __init__(self, discovery):
        self.discovery = discovery
        self.store = discovery.store

    def initialize(self):
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS autopilot_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL,
                    mode TEXT NOT NULL, revision INTEGER NOT NULL, ai_review INTEGER NOT NULL,
                    minimum_seconds INTEGER NOT NULL, require_captions INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO autopilot_settings VALUES(1,0,'prepare',0,0,0,0);
                CREATE TABLE IF NOT EXISTS autopilot_scans (
                    scan_id TEXT PRIMARY KEY REFERENCES twitch_scans(id) ON DELETE CASCADE,
                    scan_revision INTEGER NOT NULL
                );
            """)

    @staticmethod
    def enrollment(db, scan_id):
        return db.execute(
            "SELECT settings.* FROM autopilot_settings settings JOIN autopilot_scans scans "
            "ON scans.scan_id=? WHERE settings.id=1",
            (scan_id,),
        ).fetchone()

    @staticmethod
    def configuration(db):
        settings = db.execute("SELECT * FROM autopilot_settings WHERE id=1").fetchone()
        return {
            "enabled": bool(settings["enabled"]),
            "revision": settings["revision"],
            "mode": settings["mode"],
            "ai_review": bool(settings["ai_review"]),
            "minimum_seconds": settings["minimum_seconds"],
            "require_captions": bool(settings["require_captions"]),
            "scan_ids": [
                row[0] for row in db.execute("SELECT scan_id FROM autopilot_scans ORDER BY scan_id")
            ],
        }

    def update(self, payload):
        ids = [str(value) for value in payload.scan_ids]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "Choose each Twitch scan once.")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.configuration(db)
            if payload.revision != current["revision"]:
                raise HTTPException(409, "Autopilot settings changed. Refresh and try again.")
            scans = [self.discovery.require(db, identifier) for identifier in ids]
            if payload.enabled:
                self.validate(db, payload, scans, current)
            removed = set(current["scan_ids"]) - set(ids)
            if removed:
                # Unenrolling a scan must not silently restart it as a legacy schedule.
                for identifier in removed:
                    row = self.discovery.require(db, identifier)
                    settings = json.loads(row["settings"])
                    settings["enabled"] = False
                    settings["auto_prepare"] = False
                    db.execute(
                        "UPDATE twitch_scans SET enabled=0,next_run=NULL,settings=?,revision=revision+1 "
                        "WHERE id=?",
                        (json.dumps(settings), identifier),
                    )
            db.execute("DELETE FROM autopilot_scans")
            db.executemany(
                "INSERT INTO autopilot_scans VALUES(?,?)",
                [(scan["id"], scan["revision"]) for scan in scans],
            )
            db.execute(
                "UPDATE autopilot_settings SET enabled=?,mode=?,revision=revision+1,"
                "ai_review=?,minimum_seconds=?,require_captions=? WHERE id=1",
                (
                    payload.enabled,
                    payload.mode,
                    payload.ai_review and payload.mode == "prepare",
                    payload.minimum_seconds,
                    payload.require_captions,
                ),
            )
            if payload.enabled and (not current["enabled"] or set(ids) != set(current["scan_ids"])):
                db.executemany(
                    "UPDATE twitch_scans SET next_run=? WHERE id=? AND enabled=1",
                    [(time.time(), identifier) for identifier in ids],
                )
            result = self.configuration(db)
        self.discovery.wake.set()
        return result

    def validate(self, db, payload, scans, current):
        if not self.discovery.api.configured:
            raise HTTPException(409, "Configure Twitch app credentials before starting Autopilot.")
        if not scans or not any(scan["enabled"] for scan in scans):
            raise HTTPException(409, "Choose a saved Twitch scan with recurrence enabled.")
        if payload.mode != "prepare":
            return
        stamp = time.time()
        grants = {
            row[0]: bool(row[1])
            for row in db.execute(
                "SELECT r.broadcaster_id,r.can_share FROM twitch_rights r JOIN twitch_editor a "
                "ON a.editor_id=r.editor_id WHERE r.expires>? AND a.validated>?",
                (stamp, stamp - 3600),
            )
        }
        eligible = set()
        editorial_budget = False
        for scan in scans:
            if not scan["enabled"]:
                continue
            settings = json.loads(scan["settings"])
            ids = json.loads(scan["source_ids"])
            allowed = set(grants)
            if settings["source"] == "streamers":
                allowed &= {ids.get(name) for name in settings["streamers"]}
            elif settings.get("include"):
                allowed &= {ids.get(name) for name in settings["include"]}
            allowed -= {ids.get(name) for name in settings.get("exclude", [])}
            eligible |= allowed
            cost = 1 + int(settings.get("ai_titles", False)) + int(settings.get("ai_copy", False))
            editorial_budget |= (
                any(grants[identifier] for identifier in allowed)
                and settings.get("daily_ai_limit", 0) >= cost
            )
        if not eligible:
            raise HTTPException(
                409, "Connect a Twitch editor and record reuse rights for an enrolled source."
            )
        if payload.ai_review:
            prior_scope = dict(
                db.execute("SELECT scan_id,scan_revision FROM autopilot_scans").fetchall()
            )
            scope_changed = prior_scope != {scan["id"]: scan["revision"] for scan in scans}
            if (
                scope_changed or not current["ai_review"] or not current["enabled"]
            ) and not payload.ai_consent:
                raise HTTPException(422, "Approve transcript sharing and billed editorial review.")
            if not editorial_budget:
                raise HTTPException(
                    409,
                    "Editorial review needs a source with AI sharing rights and an adequate daily budget.",
                )
            if not self.discovery.intake.settings.openai_api_key:
                raise HTTPException(
                    409,
                    "Configure the existing OpenAI backend key before enabling editorial review.",
                )

    def snapshot(self):
        with self.store.connection() as db:
            configuration = self.configuration(db)
            scans = [
                {
                    "id": row["id"],
                    "name": json.loads(row["settings"])["name"],
                    "enabled": bool(row["enabled"]),
                    "next_run": row["next_run"],
                    "last_status": row["last_status"],
                    "last_detail": row["last_detail"],
                    "last_success": row["last_success"],
                    "changed": row["scan_revision"] is not None
                    and row["scan_revision"] != row["revision"],
                }
                for row in db.execute("""
                    SELECT s.*,a.scan_revision,
                        (SELECT status FROM twitch_scan_runs WHERE scan_id=s.id ORDER BY started DESC LIMIT 1) last_status,
                        (SELECT detail FROM twitch_scan_runs WHERE scan_id=s.id ORDER BY started DESC LIMIT 1) last_detail,
                        (SELECT max(finished) FROM twitch_scan_runs WHERE scan_id=s.id AND status='complete') last_success
                    FROM twitch_scans s LEFT JOIN autopilot_scans a ON a.scan_id=s.id
                    ORDER BY s.updated DESC
                """)
            ]
            rows = db.execute("""
                SELECT j.clip_id,j.scan_id,j.recording_id,j.project_id,j.status,j.detail,j.queued,
                       json_extract(j.options,'$.title') title,p.payload
                FROM twitch_intake j LEFT JOIN projects p ON p.id=j.project_id
                WHERE json_extract(j.options,'$.autopilot_revision') IS NOT NULL
                ORDER BY j.queued DESC LIMIT 100
            """).fetchall()
            posts = {
                row["status"]: row["total"]
                for row in db.execute("""
                    SELECT s.status,count(*) total FROM scheduled_posts s
                    JOIN clips c ON c.id=s.export_id
                    JOIN twitch_intake j ON j.recording_id=c.recording_id
                    WHERE json_extract(j.options,'$.autopilot_revision') IS NOT NULL
                    GROUP BY s.status
                """)
            }
            jobs = [self.job(db, row) for row in rows]
            usage = db.execute(
                """
                SELECT COALESCE(sum(json_extract(options,'$.paid')),0) request_budget,
                       COALESCE(sum(r.size_bytes),0) source_bytes
                FROM twitch_intake j LEFT JOIN recordings r ON r.id=j.recording_id
                WHERE j.queued>=? AND json_extract(j.options,'$.autopilot_revision') IS NOT NULL
            """,
                (time.time() - 86400,),
            ).fetchone()
            discovered = db.execute(
                """
                SELECT COALESCE(sum(r.count),0) FROM twitch_scan_runs r
                JOIN autopilot_scans s ON s.scan_id=r.scan_id WHERE r.started>=?
            """,
                (time.time() - 86400,),
            ).fetchone()[0]
        return {
            "configuration": configuration,
            "scans": scans,
            "jobs": jobs,
            "post_handoffs": posts,
            "usage": {**dict(usage), "results_24h": discovered},
            "configured": self.discovery.api.configured,
            "worker_running": bool(self.discovery.thread and self.discovery.thread.is_alive()),
        }

    def job(self, db, row):
        from .projects import Projects
        from .twitch_rights import active, autopilot_quality_error, source_rights

        project = Project.model_validate_json(row["payload"]) if row["payload"] else None
        source = source_rights(db, row["recording_id"])
        permitted = bool(source and active(source) and source["can_publish"])
        clips = []
        if project:
            for item in project.items:
                export = (
                    db.execute(
                        "SELECT id,status,duration FROM clips WHERE id=?", (item.export_id,)
                    ).fetchone()
                    if item.export_id
                    else None
                )
                checks = bool(
                    export
                    and export["status"] == "succeeded"
                    and self.store.export_path(item.export_id).is_file()
                )
                stale = bool(
                    (item.post_copy or item.post_copy_selected)
                    and item.post_copy_source != Projects.copy_source(project, item)
                )
                quality = autopilot_quality_error(db, source, export) if checks and source else None
                ready = (
                    checks
                    and not item.error
                    and not item.pending_export_id
                    and item.review != "rejected"
                    and not stale
                    and not quality
                )
                clips.append(
                    {
                        "id": item.id,
                        "title": item.title,
                        "export_id": item.export_id,
                        "revision": item.revision,
                        "checks": checks,
                        "quality": quality,
                        "ready": bool(ready and permitted and project.status not in ACTIVE),
                        "reason": item.analysis_reason or item.selection_reason,
                        "review": item.review,
                        "warning": item.error
                        or item.analysis_error
                        or item.post_copy_error
                        or item.transcription_warning
                        or (item.presentation_warnings or [None])[0]
                        or ("Posting copy is stale. Review it in Projects." if stale else None)
                        or (
                            "Current source rights do not permit posting."
                            if not permitted
                            else None
                        ),
                    }
                )
        return {
            "clip_id": row["clip_id"],
            "scan_id": row["scan_id"],
            "project_id": project.id if project else None,
            "title": row["title"],
            "status": project.status if project else row["status"],
            "detail": (project.error if project else None) or row["detail"],
            "clips": clips,
        }


def create_autopilot_router(service):
    router = APIRouter(prefix="/api/autopilot")

    @router.get("")
    def snapshot():
        return service.snapshot()

    @router.put("")
    def update(payload: AutopilotUpdate):
        return service.update(payload)

    return router
