"""Authorized Twitch media intake using the existing supplied-video project pipeline."""

import json
import logging
import time
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx2
from fastapi import HTTPException
from pydantic import BaseModel, Field

from .autopilot import Autopilot
from .files import remove_artifact, require_space
from .models import Recording
from .project_models import ProjectCreate, ProjectSettings
from .project_workspace import PresetRequest
from .storage import now
from .transcription_models import clean_text
from .twitch_helix import MAX_IMPORTS_PER_SCAN, TwitchError
from .youtube_network import validate_destination

logger = logging.getLogger(__name__)
MAX_DOWNLOAD = 128 * 1024**2


class ImportRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=MAX_IMPORTS_PER_SCAN)


class EditorImportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class TwitchIntake:
    def __init__(self, store, editor, projects, media, settings):
        self.store, self.editor, self.projects, self.media, self.settings = (
            store,
            editor,
            projects,
            media,
            settings,
        )

    def initialize(self):
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS twitch_intake (
                    clip_id TEXT PRIMARY KEY, broadcaster_id TEXT NOT NULL, editor_id TEXT NOT NULL,
                    scan_id TEXT, recording_id TEXT NOT NULL UNIQUE, project_id TEXT NOT NULL UNIQUE,
                    queued REAL NOT NULL, status TEXT NOT NULL, detail TEXT, options TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS twitch_intake_queue ON twitch_intake(status,queued);
                CREATE TABLE IF NOT EXISTS twitch_post_claims (
                    clip_id TEXT NOT NULL, platform TEXT NOT NULL, destination TEXT NOT NULL,
                    job_id TEXT NOT NULL, PRIMARY KEY (clip_id,platform,destination)
                );
            """)
            for row in db.execute("SELECT * FROM twitch_intake WHERE status='running'"):
                recording = db.execute(
                    "SELECT id FROM recordings WHERE id=?", (row["recording_id"],)
                ).fetchone()
                if not recording:
                    remove_artifact(self.path(row["recording_id"]))
                remove_artifact(self.path(row["recording_id"]).with_suffix(".twitch-part"))
            db.execute("UPDATE twitch_intake SET status='queued' WHERE status='running'")

    def path(self, identifier):
        return self.store.root / "sources" / f"{identifier}.mp4"

    def statuses(self, scan_id, clips):
        with self.store.connection() as db:
            editor_connected = self.editor.connected_id(db) is not None
            jobs = [
                dict(row)
                for row in db.execute(
                    "SELECT j.clip_id,j.project_id,j.status,j.detail,p.status AS project_status "
                    "FROM twitch_intake j LEFT JOIN projects p ON p.id=j.project_id "
                    "WHERE (? IS NULL OR j.scan_id=?) ORDER BY j.queued DESC LIMIT 100",
                    (scan_id, scan_id),
                )
            ]
            for clip in clips:
                clip["can_request_download"] = editor_connected
                prior = db.execute(
                    "SELECT status FROM twitch_intake WHERE clip_id=?", (clip["id"],)
                ).fetchone()
                clip["intake_status"] = prior["status"] if prior else None
                clip["same_moment"] = self.same_moment(
                    db,
                    clip["id"],
                    clip["broadcaster_id"],
                    clip.get("video_id"),
                    clip.get("vod_offset"),
                    clip["duration"],
                )
        return jobs

    @staticmethod
    def same_moment(db, clip_id, broadcaster, vod_id, start, duration):
        if not vod_id or start is None:
            return False
        # Timing is evidence only when both clips refer to the same VOD.
        return bool(
            db.execute(
                "SELECT 1 FROM twitch_intake WHERE broadcaster_id=? AND clip_id!=? "
                "AND json_extract(options,'$.vod_id')=? "
                "AND json_extract(options,'$.vod_start') IS NOT NULL "
                "AND min(?,json_extract(options,'$.vod_start')+json_extract(options,'$.duration')) "
                "- max(?,json_extract(options,'$.vod_start')) >= "
                "0.8 * min(?,json_extract(options,'$.duration')) LIMIT 1",
                (broadcaster, clip_id, vod_id, start + duration, start, duration),
            ).fetchone()
        )

    def enqueue(self, scan_id, settings, clips, *, automatic, revision=None, autopilot=None):
        stamp = time.time()
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if autopilot:
                current = Autopilot.enrollment(db, scan_id)
                if (
                    not current
                    or not current["enabled"]
                    or current["mode"] != "prepare"
                    or current["revision"] != autopilot["revision"]
                ):
                    raise HTTPException(
                        409,
                        "Autopilot changed during this scan. The results remain available for review.",
                    )
            if (
                revision is not None
                and not db.execute(
                    "SELECT 1 FROM twitch_scans WHERE id=? AND revision=?", (scan_id, revision)
                ).fetchone()
            ):
                raise HTTPException(
                    409, "Scan settings changed. Automatic preparation will use the next run."
                )
            editor_id = self.editor.connected_id(db)
            slots = min(
                settings.import_limit,
                60
                - db.execute(
                    "SELECT count(*) FROM twitch_intake WHERE status IN ('queued','running')"
                ).fetchone()[0],
            )
            budget = (
                settings.daily_limit
                - db.execute(
                    "SELECT count(*) FROM twitch_intake WHERE queued>=?", (stamp - 86400,)
                ).fetchone()[0]
            )
            # One source per project, two optional paid operations per source.
            ai_review = bool(autopilot and autopilot["ai_review"])
            paid = int(settings.ai_titles) + int(settings.ai_copy) + int(ai_review)
            ai_remaining = (
                settings.daily_ai_limit
                - db.execute(
                    "SELECT COALESCE(sum(CAST(json_extract(options, '$.paid') AS INTEGER)),0) "
                    "FROM twitch_intake WHERE queued>=?",
                    (stamp - 86400,),
                ).fetchone()[0]
            )
            quota = max(0, min(slots, budget, ai_remaining // paid if paid else slots))
            if not editor_id or not quota:
                return 0
            preset = None
            if settings.preset_id:
                row = db.execute(
                    "SELECT payload FROM project_presets WHERE workflow='batch' AND id=?",
                    (str(settings.preset_id),),
                ).fetchone()
                if not row:
                    raise HTTPException(409, "Select an available supplied-video project preset.")
                preset = PresetRequest.model_validate_json(row[0]).settings
            options = {
                "preset": preset.model_dump(mode="json") if preset else None,
                "ai_titles": settings.ai_titles,
                "ai_copy": settings.ai_copy,
                "paid": paid,
                "auto": automatic,
                "ai_review": ai_review,
                "autopilot_revision": autopilot["revision"] if autopilot else None,
                "minimum_seconds": autopilot["minimum_seconds"] if autopilot else 0,
                "require_captions": bool(autopilot and autopilot["require_captions"]),
                "storage_limit_mib": settings.storage_limit_mib,
            }
            count = 0
            for clip in clips:
                if automatic and self.same_moment(
                    db, clip.id, clip.broadcaster_id, clip.video_id, clip.vod_offset, clip.duration
                ):
                    continue
                metadata = json.dumps(
                    {
                        **options,
                        "title": clean_text(clip.title)[:120] or "Twitch clip",
                        "vod_id": clip.video_id,
                        "vod_start": clip.vod_offset,
                        "duration": clip.duration,
                    }
                )
                if not automatic:
                    changed = db.execute(
                        "UPDATE twitch_intake SET scan_id=?,options=?,status='queued',queued=?,detail=NULL "
                        "WHERE clip_id=? AND editor_id=? AND broadcaster_id=? AND status='recording'",
                        (scan_id, metadata, stamp, clip.id, editor_id, clip.broadcaster_id),
                    ).rowcount
                    if changed:
                        count += changed
                        if count >= quota:
                            break
                        continue
                count += db.execute(
                    "INSERT INTO twitch_intake VALUES(?,?,?,?,?,?,?,'queued',NULL,?) "
                    "ON CONFLICT(clip_id) DO NOTHING",
                    (
                        clip.id,
                        clip.broadcaster_id,
                        editor_id,
                        scan_id,
                        str(uuid4()),
                        str(uuid4()),
                        stamp,
                        metadata,
                    ),
                ).rowcount
                if count >= quota:
                    break
            return count

    def editor_status(self, slug, *, db=None):
        if db is None:
            with self.store.connection() as connection:
                return self.editor_status(slug, db=connection)
        job = db.execute("SELECT * FROM twitch_intake WHERE clip_id=?", (slug,)).fetchone()
        if not job:
            return None
        raw = db.execute("SELECT * FROM recordings WHERE id=?", (job["recording_id"],)).fetchone()
        recording = Recording.model_validate(dict(raw)) if raw else None
        if recording:
            if (self.store.root / "sources" / f"{recording.id}.mp4").is_file():
                return {"id": slug, "status": "ready", "recording": recording, "detail": None}
            return {
                "id": slug,
                "status": "failed",
                "recording": None,
                "detail": "The saved Twitch file is missing. Check the local import in Discover.",
            }
        status = job["status"]
        detail = job["detail"]
        if not json.loads(job["options"]).get("editor_only") and status != "running":
            status = "failed"
            detail = "This clip already has a preparation job. Check Discover for its status or retry it there."
        if status in {"recording", "project"}:
            status, detail = "failed", "The saved Twitch recording is unavailable on disk."
        return {"id": slug, "status": status, "recording": None, "detail": detail}

    def open_in_editor(self, slug):
        existing = self.editor_status(slug)
        if existing and existing["status"] != "failed":
            return existing
        clip = None if existing else self.editor.api.clip(slug)
        stamp = time.time()
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = self.editor_status(slug, db=db)
            if existing and existing["status"] != "failed":
                return existing
            if (
                existing
                and not db.execute(
                    "SELECT 1 FROM twitch_intake j WHERE clip_id=? AND json_extract(options,'$.editor_only')=1 "
                    "AND NOT EXISTS(SELECT 1 FROM recordings r WHERE r.id=j.recording_id)",
                    (slug,),
                ).fetchone()
            ):
                return existing
            if (
                db.execute(
                    "SELECT count(*) FROM twitch_intake WHERE status IN ('queued','running')"
                ).fetchone()[0]
                >= 60
            ):
                raise HTTPException(
                    409, "The Twitch import queue is full. Try again after some work finishes."
                )
            if existing:
                changed = db.execute(
                    "UPDATE twitch_intake SET status='queued',detail=NULL,"
                    "options=json_set(options,'$.attempts',0,'$.retry_at',0) "
                    "WHERE clip_id=? AND editor_id=?",
                    (slug, self.editor.connected_id(db)),
                ).rowcount
                if not changed:
                    raise HTTPException(
                        403, "Reconnect the original Twitch editor to retry this download."
                    )
            else:
                if clip is None:
                    raise HTTPException(
                        409, "The Twitch import changed. Paste the link again to retry."
                    )
                if (
                    db.execute(
                        "SELECT count(*) FROM twitch_intake WHERE queued>=?", (stamp - 86400,)
                    ).fetchone()[0]
                    >= 30
                ):
                    raise HTTPException(
                        429, "The Editor's daily Twitch import limit has been reached."
                    )
                editor_id = self.editor.connected_id(db)
                if not editor_id:
                    raise HTTPException(
                        403,
                        "Connect a Twitch editor in Find clips to request this download.",
                    )
                options = {
                    "editor_only": True,
                    "auto": False,
                    "paid": 0,
                    "title": clean_text(clip.title)[:120] or "Twitch clip",
                    "vod_id": clip.video_id,
                    "vod_start": clip.vod_offset,
                    "duration": clip.duration,
                }
                db.execute(
                    "INSERT INTO twitch_intake VALUES(?,?,?,NULL,?,?,?,'queued',NULL,?)",
                    (
                        slug,
                        clip.broadcaster_id,
                        editor_id,
                        str(uuid4()),
                        str(uuid4()),
                        stamp,
                        json.dumps(options),
                    ),
                )
            return self.editor_status(slug, db=db)

    def retry(self, scan_id, clip_id):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                db.execute(
                    "SELECT count(*) FROM twitch_intake WHERE status IN ('queued','running')"
                ).fetchone()[0]
                >= 60
            ):
                raise HTTPException(409, "Wait for queued Twitch work to finish.")
            if not db.execute(
                "UPDATE twitch_intake SET status='queued',detail=NULL,"
                "options=json_set(options,'$.auto',0,'$.attempts',0,'$.retry_at',0) "
                "WHERE scan_id=? AND clip_id=? AND status='failed'",
                (scan_id, clip_id),
            ).rowcount:
                raise HTTPException(409, "Only a failed Twitch import can be retried.")

    def work(self, stop):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute(
                "SELECT j.* FROM twitch_intake j LEFT JOIN twitch_scans s ON s.id=j.scan_id "
                "WHERE j.status='queued' AND (json_extract(j.options,'$.auto')=0 OR ("
                "(json_extract(j.options,'$.autopilot_revision') IS NULL AND "
                " json_extract(s.settings,'$.auto_prepare')=1 AND "
                " NOT EXISTS(SELECT 1 FROM autopilot_scans a WHERE a.scan_id=s.id)) OR "
                "(json_extract(j.options,'$.autopilot_revision') IS NOT NULL AND s.enabled=1 AND "
                " EXISTS(SELECT 1 FROM autopilot_scans a CROSS JOIN autopilot_settings b "
                " WHERE a.scan_id=s.id AND a.scan_revision=s.revision "
                " AND b.id=1 AND b.enabled=1 AND b.mode='prepare')))) "
                "AND COALESCE(json_extract(j.options,'$.retry_at'),0)<=? ORDER BY j.queued LIMIT 1",
                (time.time(),),
            ).fetchone()
            if not job:
                return False
            db.execute(
                "UPDATE twitch_intake SET status='running' WHERE clip_id=?", (job["clip_id"],)
            )
        options = json.loads(job["options"])
        status, detail = ("recording" if options.get("editor_only") else "project"), None
        previous = options.get("attempts", 0)
        attempts, retry_at = previous + 1, 0
        try:
            self.process(job, stop)
        except TwitchError as exc:
            status, detail = "failed", str(exc)
            if stop.is_set():
                status, attempts = "queued", previous
            elif exc.status in (429, 502) and attempts < 3:
                status = "queued"
                retry_at = max(
                    time.time() + 30 * 2**previous,
                    time.time() + max(0, self.editor.api.retry_after - time.monotonic()),
                )
                detail = f"{detail} A retry is queued."
        except HTTPException as exc:
            status, detail = "failed", str(exc.detail)
        except Exception:
            logger.exception("Authorized Twitch import failed.")
            status, detail = "failed", "Could not finish the Twitch import. Check the API logs."
        with self.store.connection() as db:
            db.execute(
                "UPDATE twitch_intake SET status=?,detail=?,"
                "options=json_set(options,'$.attempts',?,'$.retry_at',?) WHERE clip_id=?",
                (status, detail, attempts, retry_at, job["clip_id"]),
            )
        return True

    def process(self, job, stop):
        options = json.loads(job["options"])
        recording = self.store.recording(job["recording_id"])
        if not recording:
            urls = self.editor.downloads(job["editor_id"], job["broadcaster_id"], [job["clip_id"]])
            url = urls.get(job["clip_id"])
            if not isinstance(url, str):
                raise TwitchError(
                    403, "Twitch did not authorize a downloadable file for this clip."
                )
            destination = self.path(job["recording_id"])
            partial = destination.with_suffix(".twitch-part")
            committed = False
            try:
                with self.store.connection() as db:
                    used = db.execute(
                        "SELECT COALESCE(sum(r.size_bytes),0) FROM recordings r JOIN twitch_intake t "
                        "ON r.id=t.recording_id"
                    ).fetchone()[0]
                budget = min(
                    MAX_DOWNLOAD,
                    self.settings.max_upload_bytes,
                    options.get("storage_limit_mib", 4096) * 1024**2 - used,
                )
                if budget <= 0:
                    raise TwitchError(507, "The Twitch source storage budget is exhausted.")
                require_space(self.store.root, self.settings.min_free_bytes, budget)
                try:
                    size = self.download(url, partial, budget, stop)
                except httpx2.HTTPError:
                    raise TwitchError(502, "Twitch clip media transfer was interrupted.") from None
                info = self.media.probe(partial)
                if not 0.1 <= info.duration <= 65:
                    raise TwitchError(
                        422, "The downloaded file is outside the Twitch clip duration limit."
                    )
                partial.replace(destination)
                recording = Recording(
                    id=job["recording_id"],
                    name=options["title"],
                    duration=info.duration,
                    width=info.width,
                    height=info.height,
                    has_audio=info.has_audio,
                    size_bytes=size,
                    created_at=now(),
                )
                self.store.save_recording(recording, ".mp4")
                committed = True
            finally:
                remove_artifact(partial)
                if not committed:
                    remove_artifact(destination)
        if options.get("editor_only"):
            return
        overrides = options.get("preset") or {"presentation_mode": "auto"}
        if not recording.has_audio:
            overrides = {**overrides, "caption_preset": "none"}
        self.projects.create(
            ProjectCreate(
                id=job["project_id"],
                title=options["title"],
                recording_ids=[recording.id],
                remote_consent=bool(options["paid"]),
                generate_titles=options["ai_titles"] and recording.has_audio,
                generate_post_copy=options["ai_copy"] and recording.has_audio,
                settings=ProjectSettings.model_validate(
                    {
                        **overrides,
                        "analysis_start": 0,
                        "analysis_end": recording.duration,
                        "duration_mode": "full",
                        "batch_mode": "auto"
                        if options.get("ai_review") and recording.has_audio
                        else "keep",
                        "multimodal": False,
                    }
                ),
            )
        )

    def download(self, url, path, limit, stop):
        deadline = time.monotonic() + 60
        for _ in range(4):
            host = urlsplit(url).hostname or ""
            if not host.endswith((".twitchcdn.net", ".ttvnw.net", ".jtvnw.net")):
                raise TwitchError(502, "Twitch returned an unsupported clip media host.")
            validate_destination(url, "twitch")
            with self.editor.api.client.stream("GET", url) as response:
                if (
                    response.status_code in (301, 302, 303, 307, 308)
                    and "location" in response.headers
                ):
                    url = urljoin(url, response.headers["location"])
                    continue
                if response.status_code != 200:
                    raise TwitchError(502, "Twitch could not deliver the clip video.")
                length = response.headers.get("content-length", "")
                if length.isdecimal() and (len(length) > 10 or int(length) > limit):
                    raise TwitchError(413, "Twitch clip exceeds the download limit.")
                size = 0
                with path.open("xb") as output:
                    for chunk in response.iter_bytes(chunk_size=64 * 1024):
                        if stop.is_set() or time.monotonic() > deadline:
                            raise TwitchError(503, "Twitch clip download interrupted or timed out.")
                        size += len(chunk)
                        if size > limit:
                            raise TwitchError(413, "Twitch clip exceeds the download limit.")
                        output.write(chunk)
                if not size or (length.isdecimal() and size != int(length)):
                    raise TwitchError(502, "Twitch returned incomplete clip media.")
                return size
        raise TwitchError(502, "Twitch clip exceeded the redirect limit.")
