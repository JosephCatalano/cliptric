"""Durable local Twitch discovery. Remote results and review flags expire after 24 hours."""

import json
import logging
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .twitch_editor import CALLBACK
from .twitch_helix import ScanSettings, TwitchClip, TwitchError, TwitchHelix
from .twitch_intake import Grant, ImportRequest

logger = logging.getLogger(__name__)
PERIODS = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}
TITLE_PATTERNS = {
    "gameplay": r"\b(gameplay|mission|heist|clutch|speedrun)\b",
    "roleplay": r"\b(rp|roleplay|role-play|nopixel)\b",
    "funny": r"\b(funny|lmao|hilarious|comedy)\b",
    "fail": r"\b(fail|fails|crash|busted|failed)\b",
    "chase": r"\b(chase|pursuit|getaway|police|cops)\b",
    "glitch": r"\b(glitch|bug|exploit)\b",
    "reaction": r"\b(reaction|reacts|shocked|surprise)\b",
}


class ReviewRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)
    state: Literal["reviewed", "hidden", "new"]


def utc(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


class TwitchDiscovery:
    def __init__(self, store, api=None):
        self.store = store
        self.api = api if api is not None else TwitchHelix()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.intake = None

    def initialize(self):
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS twitch_scans (
                    id TEXT PRIMARY KEY, settings TEXT NOT NULL, source_ids TEXT NOT NULL,
                    revision INTEGER NOT NULL, enabled INTEGER NOT NULL, interval_minutes INTEGER NOT NULL,
                    next_run REAL, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS twitch_scan_runs (
                    id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES twitch_scans(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL, started REAL NOT NULL, finished REAL,
                    status TEXT NOT NULL, settings TEXT, source_ids TEXT, count INTEGER NOT NULL DEFAULT 0, detail TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS twitch_active_scan ON twitch_scan_runs(scan_id)
                    WHERE status IN ('queued','running');
                CREATE INDEX IF NOT EXISTS twitch_recent_runs ON twitch_scan_runs(scan_id, started DESC);
                CREATE TABLE IF NOT EXISTS twitch_scan_results (
                    run_id TEXT NOT NULL REFERENCES twitch_scan_runs(id) ON DELETE CASCADE,
                    clip_id TEXT NOT NULL, rank INTEGER NOT NULL, clip TEXT NOT NULL, expires REAL NOT NULL,
                    PRIMARY KEY (run_id, clip_id)
                );
                CREATE TABLE IF NOT EXISTS twitch_scan_review (
                    clip_id TEXT PRIMARY KEY, state TEXT NOT NULL, expires REAL NOT NULL
                );
            """)
            db.execute(
                "UPDATE twitch_scan_runs SET status='failed', finished=?, detail=?, settings=NULL, source_ids=NULL "
                "WHERE status='running'",
                (time.time(), "Scan interrupted during API shutdown or restart."),
            )
            self.purge(db, time.time())

    @staticmethod
    def purge(db, stamp):
        db.execute("DELETE FROM twitch_scan_results WHERE expires<=?", (stamp,))
        db.execute("DELETE FROM twitch_scan_review WHERE expires<=?", (stamp,))
        db.execute(
            "DELETE FROM twitch_scan_runs WHERE status!='running' AND started<=?",
            (stamp - 30 * 86400,),
        )

    def start(self):
        self.thread = threading.Thread(target=self.loop, name="twitch-discovery", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join()
        if isinstance(self.api, TwitchHelix):
            self.api.client.close()

    def loop(self):
        while not self.stop.is_set():
            try:
                if self.intake:
                    self.intake.editor.validate_due()
                with self.store.connection() as db:
                    self.purge(db, time.time())
                work = self.claim()
                if work:
                    self.execute(*work)
                if self.intake and not self.stop.is_set() and self.intake.work(self.stop):
                    continue
                if work:
                    continue
            except Exception:
                logger.exception("Twitch discovery worker could not advance.")
            self.wake.wait(15)
            self.wake.clear()

    @staticmethod
    def _scan(row):
        settings = ScanSettings.model_validate_json(row["settings"])
        return {
            "id": row["id"],
            "settings": settings.model_dump(mode="json"),
            "revision": row["revision"],
            "next_run": utc(row["next_run"]) if row["next_run"] else None,
        }

    def list_scans(self):
        with self.store.connection() as db:
            return [
                self._scan(row)
                for row in db.execute("SELECT * FROM twitch_scans ORDER BY updated DESC, id DESC")
            ]

    def save(self, settings, identifier=None, revision=None):
        with self.store.connection() as db:
            old = self.require(db, identifier) if identifier else None
        names = list(dict.fromkeys(settings.streamers + settings.include + settings.exclude))
        previous = json.loads(old["source_ids"]) if old else {}
        ids = {name: previous[name] for name in names if name in previous}
        ids.update(self.api.broadcaster_ids([name for name in names if name not in ids]))
        stamp = time.time()
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if identifier:
                old = self.require(db, identifier)
                if old["revision"] != revision:
                    raise HTTPException(409, "This scan changed. Reload it before saving.")
                next_run = self.next_run(old, settings, stamp)
                db.execute(
                    "UPDATE twitch_scans SET settings=?,source_ids=?,revision=revision+1,"
                    "enabled=?,interval_minutes=?,next_run=?,updated=? WHERE id=?",
                    (
                        settings.model_dump_json(),
                        json.dumps(ids),
                        settings.enabled,
                        settings.interval_minutes,
                        next_run,
                        stamp,
                        identifier,
                    ),
                )
            else:
                if db.execute("SELECT count(*) FROM twitch_scans").fetchone()[0] >= 50:
                    raise HTTPException(409, "Delete a saved scan before adding another.")
                identifier = str(uuid4())
                db.execute(
                    "INSERT INTO twitch_scans VALUES (?,?,?,1,?,?,?,?)",
                    (
                        identifier,
                        settings.model_dump_json(),
                        json.dumps(ids),
                        settings.enabled,
                        settings.interval_minutes,
                        stamp + settings.interval_minutes * 60 if settings.enabled else None,
                        stamp,
                    ),
                )
            result = self._scan(self.require(db, identifier))
        self.wake.set()
        return result

    @staticmethod
    def next_run(old, settings, stamp):
        if not settings.enabled:
            return None
        if old["enabled"] and old["interval_minutes"] == settings.interval_minutes:
            return old["next_run"]
        return stamp + settings.interval_minutes * 60

    @staticmethod
    def require(db, identifier):
        row = db.execute("SELECT * FROM twitch_scans WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, "Twitch scan not found.")
        return row

    def claim(self, identifier=None):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            stamp = time.time()
            active = db.execute(
                "SELECT id FROM twitch_scan_runs WHERE status='running' LIMIT 1"
            ).fetchone()
            if active:
                if identifier:
                    raise HTTPException(409, "A Twitch scan is already running.")
                return None
            pending = db.execute(
                "SELECT * FROM twitch_scan_runs WHERE status='queued' ORDER BY started LIMIT 1"
            ).fetchone()
            if pending:
                if identifier:
                    raise HTTPException(409, "A Twitch scan is already queued.")
                row = self.require(db, pending["scan_id"])
                db.execute(
                    "UPDATE twitch_scan_runs SET status='running' WHERE id=?", (pending["id"],)
                )
                return pending["id"], {
                    **dict(row),
                    "revision": pending["revision"],
                    "settings": pending["settings"],
                    "source_ids": pending["source_ids"],
                }
            if identifier:
                row = self.require(db, identifier)
            else:
                if not self.api.configured:
                    return None
                row = db.execute(
                    "SELECT * FROM twitch_scans WHERE enabled=1 AND next_run<=? ORDER BY next_run LIMIT 1",
                    (stamp,),
                ).fetchone()
                if not row:
                    return None
            run_id = str(uuid4())
            db.execute(
                "INSERT INTO twitch_scan_runs(id,scan_id,revision,started,status,settings,source_ids) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    run_id,
                    row["id"],
                    row["revision"],
                    stamp,
                    "queued" if identifier else "running",
                    row["settings"],
                    row["source_ids"],
                ),
            )
            if row["enabled"]:
                db.execute(
                    "UPDATE twitch_scans SET next_run=? WHERE id=?",
                    (stamp + row["interval_minutes"] * 60, row["id"]),
                )
            return run_id, row

    def execute(self, run_id, row):
        stamp = time.time()
        try:
            settings = ScanSettings.model_validate_json(row["settings"])
            ranked, detail = self.scan(settings, json.loads(row["source_ids"]), stamp)
            clips = ranked[: settings.limit]
            status = "partial" if detail else "complete"
            if self.intake and settings.auto_prepare:
                with self.store.connection() as db:
                    current = self.require(db, row["id"])
                    allow = current["revision"] == row["revision"]
                if allow:
                    eligible = [clip for clip in ranked if not self.is_hidden(clip.id, stamp)]
                    try:
                        processed = self.intake.enqueue(
                            row["id"], settings, eligible, automatic=True, revision=row["revision"]
                        )
                        if processed < min(settings.import_limit, len(eligible)):
                            detail = " ".join(
                                filter(
                                    None,
                                    [
                                        detail,
                                        f"Queued {processed} clips. Other clips were ineligible, already imported, or over a processing limit.",
                                    ],
                                )
                            )
                    except HTTPException as exc:
                        detail = " ".join(filter(None, [detail, str(exc.detail)]))
        except TwitchError as exc:
            clips, detail, status = [], str(exc), "failed"
        except Exception:
            logger.exception("A Twitch scan failed.")
            clips, detail, status = (
                [],
                "An unexpected scan failure occurred. See API logs.",
                "failed",
            )
        with self.store.connection() as db:
            db.executemany(
                "INSERT INTO twitch_scan_results VALUES (?,?,?,?,?)",
                [
                    (run_id, clip.id, index, clip.model_dump_json(), stamp + 86400)
                    for index, clip in enumerate(clips, 1)
                ],
            )
            db.execute(
                "UPDATE twitch_scan_runs SET status=?,count=?,detail=?,finished=?,settings=NULL,"
                "source_ids=NULL WHERE id=?",
                (status, len(clips), detail, time.time(), run_id),
            )

    def is_hidden(self, clip_id, stamp):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT state FROM twitch_scan_review WHERE clip_id=? AND expires>?",
                (clip_id, stamp),
            ).fetchone()
        return row and row[0] == "hidden"

    def scan(self, settings, ids, stamp):
        end = datetime.fromtimestamp(stamp, UTC)
        if settings.period == "custom":
            dates = [
                ("started_at", settings.start.isoformat()),
                ("ended_at", settings.end.isoformat()),
            ]
        elif settings.period == "all":
            dates = []
        else:
            dates = [
                ("started_at", (end - timedelta(days=PERIODS[settings.period])).isoformat()),
                ("ended_at", end.isoformat()),
            ]
        sources = (
            [("game_id", settings.game_id)]
            if settings.source == "game"
            else [("broadcaster_id", ids[name]) for name in settings.streamers]
        )
        included = {ids[name] for name in settings.include}
        excluded = {ids[name] for name in settings.exclude}
        found = {}
        pattern = TITLE_PATTERNS.get(settings.content_focus)
        partial = None
        target = max(settings.limit, 100) if settings.auto_prepare else settings.limit
        deadline = time.monotonic() + 45
        for raw_source in sources:
            cursor = None
            seen = set()
            count = 0
            for _ in range(10):
                if self.stop.is_set():
                    raise TwitchError(503, "Scan stopped during API shutdown.")
                if time.monotonic() > deadline:
                    partial = "Scan reached its time budget. Results may be incomplete."
                    break
                params = [raw_source, ("first", 100), *dates]
                if cursor:
                    params.append(("after", cursor))
                try:
                    clips, cursor, ignored = self.api.page(params)
                except TwitchError as exc:
                    if not found:
                        raise
                    return self.rank(settings, found, stamp), str(exc)
                if ignored:
                    partial = "Twitch returned some clips with unsupported metadata."
                for clip in clips:
                    if (
                        clip.id not in seen
                        and (not included or clip.broadcaster_id in included)
                        and clip.broadcaster_id not in excluded
                        and clip.view_count >= settings.minimum_views
                        and settings.minimum_duration <= clip.duration <= settings.maximum_duration
                        and (not settings.languages or clip.language in settings.languages)
                        and (not pattern or re.search(pattern, clip.title, re.IGNORECASE))
                    ):
                        found[clip.id] = clip
                        seen.add(clip.id)
                        count += 1
                if not cursor or (settings.order == "views" and count >= target):
                    break
            else:
                partial = "Scan reached Twitch's pagination budget. Results may be incomplete."
        return self.rank(settings, found, stamp), partial

    @staticmethod
    def rank(settings, found, stamp):
        def score(clip):
            created = clip.created_at.timestamp()
            if settings.order == "recent":
                return created, clip.view_count, clip.id
            if settings.order == "momentum":
                return clip.view_count / max(1, (stamp - created) / 3600), created, clip.id
            return clip.view_count, created, clip.id

        return sorted(found.values(), key=score, reverse=True)[:100]

    def detail(self, identifier, selected=None):
        with self.store.connection() as db:
            saved = self._scan(self.require(db, identifier))
            runs = [
                dict(row)
                for row in db.execute(
                    "SELECT id,revision,started,finished,status,count,detail FROM twitch_scan_runs "
                    "WHERE scan_id=? ORDER BY started DESC LIMIT 20",
                    (identifier,),
                )
            ]
            if selected and not any(run["id"] == selected for run in runs):
                raise HTTPException(404, "Run not found in the recent scan history.")
            latest = selected or (runs[0]["id"] if runs else None)
            stamp = time.time()
            clips = [
                {**json.loads(row["clip"]), "rank": row["rank"], "state": row["state"] or "new"}
                for row in db.execute(
                    "SELECT c.clip,c.rank,r.state FROM twitch_scan_results c LEFT JOIN "
                    "twitch_scan_review r ON r.clip_id=c.clip_id AND r.expires>? "
                    "WHERE c.run_id=? AND c.expires>? ORDER BY c.rank",
                    (stamp, latest, stamp),
                )
            ]
        intake = self.intake.statuses(identifier, clips) if self.intake else []
        return {**saved, "runs": runs, "clips": clips, "intake": intake}

    def enqueue(self, identifier, request):
        with self.store.connection() as db:
            row = self.require(db, identifier)
            ids = set(request.ids)
            clips = {}
            for result in db.execute(
                "SELECT c.clip_id,c.clip FROM twitch_scan_results c JOIN twitch_scan_runs r "
                "ON r.id=c.run_id WHERE r.scan_id=? AND c.expires>? ORDER BY r.started DESC",
                (identifier, time.time()),
            ):
                if result["clip_id"] in ids and result["clip_id"] not in clips:
                    clips[result["clip_id"]] = TwitchClip.model_validate_json(result["clip"])
            if clips.keys() != ids:
                raise HTTPException(409, "A selected Twitch clip expired. Run the scan again.")
        added = self.intake.enqueue(
            identifier,
            ScanSettings.model_validate_json(row["settings"]),
            [clips[clip_id] for clip_id in dict.fromkeys(request.ids)],
            automatic=False,
        )
        if not added:
            raise HTTPException(
                409, "No clips could be queued. Check rights, history, and processing limits."
            )
        self.wake.set()
        return {"queued": added}

    def review(self, identifier, request):
        stamp = time.time()
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.require(db, identifier)
            for clip_id in set(request.ids):
                expires = db.execute(
                    "SELECT MAX(c.expires) FROM twitch_scan_results c JOIN twitch_scan_runs r "
                    "ON r.id=c.run_id WHERE r.scan_id=? AND c.clip_id=? AND c.expires>?",
                    (identifier, clip_id, stamp),
                ).fetchone()[0]
                if expires is None:
                    raise HTTPException(404, "A selected clip expired. Run the scan again.")
                if request.state == "new":
                    db.execute("DELETE FROM twitch_scan_review WHERE clip_id=?", (clip_id,))
                else:
                    db.execute(
                        "INSERT INTO twitch_scan_review VALUES (?,?,?) ON CONFLICT(clip_id) "
                        "DO UPDATE SET state=excluded.state,expires=excluded.expires",
                        (clip_id, request.state, expires),
                    )

    def delete(self, identifier):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.require(db, identifier)
            if db.execute(
                "SELECT 1 FROM twitch_scan_runs WHERE scan_id=? AND status IN ('queued','running')",
                (identifier,),
            ).fetchone():
                raise HTTPException(409, "Wait for the active scan before deleting it.")
            db.execute("DELETE FROM twitch_scans WHERE id=?", (identifier,))
            if self.intake:
                db.execute(
                    "UPDATE twitch_intake SET status='failed',detail=? WHERE scan_id=? AND status='queued'",
                    ("The saved scan was deleted before this import ran.", identifier),
                )
            db.execute(
                "DELETE FROM twitch_scan_review WHERE clip_id NOT IN "
                "(SELECT clip_id FROM twitch_scan_results)"
            )


def create_twitch_discovery_router(service, editor, deletions, previews):
    router = APIRouter(prefix="/api/twitch-discovery")

    @router.get("")
    def scans():
        return {
            "configured": service.api.configured,
            "scans": service.list_scans(),
            "editor": editor.status(),
            "intake": service.intake.statuses(None, []),
        }

    @router.post("/connect")
    def connect(request: Request):
        try:
            state, url = editor.begin(request.headers.get("origin", ""))
        except TwitchError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        response = JSONResponse({"url": url})
        response.set_cookie(
            "twitch_oauth_state",
            state,
            max_age=600,
            httponly=True,
            secure=request.state.hosted,
            samesite="lax",
            path=CALLBACK,
        )
        return response

    @router.get("/callback")
    def callback(
        request: Request,
        state: str = Query("", max_length=128),
        code: str = Query("", max_length=4096),
    ):
        try:
            destination = editor.finish(state, request.cookies.get("twitch_oauth_state"), code)
            response = RedirectResponse(destination, status_code=303)
        except TwitchError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=exc.status)
        response.delete_cookie(
            "twitch_oauth_state", path=CALLBACK, secure=request.state.hosted, httponly=True
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @router.post("/disconnect", status_code=204)
    def disconnect():
        editor.disconnect()

    @router.post("/rights", status_code=204)
    def grant(request: Grant):
        service.intake.grant(request)

    @router.get("/rights")
    def grants():
        return service.intake.grants()

    @router.delete("/rights/{broadcaster}", status_code=204)
    def revoke(broadcaster: str):
        service.intake.revoke(broadcaster)

    @router.get("/lookup")
    def lookup(kind: Literal["streamer", "game"], query: str = Query(min_length=1, max_length=120)):
        try:
            return service.api.look_up(kind, query.strip())
        except TwitchError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.post("/scans", status_code=201)
    def create(settings: ScanSettings):
        try:
            return service.save(settings)
        except TwitchError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.get("/scans/{identifier}")
    def detail(identifier: UUID, run: UUID | None = None):
        return service.detail(str(identifier), str(run) if run else None)

    @router.put("/scans/{identifier}")
    def update(identifier: UUID, settings: ScanSettings, revision: int = Query(ge=1)):
        try:
            return service.save(settings, str(identifier), revision)
        except TwitchError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.delete("/scans/{identifier}", status_code=204)
    def delete(identifier: UUID):
        service.delete(str(identifier))

    @router.post("/scans/{identifier}/run", status_code=202)
    def run(identifier: UUID):
        if not service.api.configured:
            raise HTTPException(503, "Configure Twitch app credentials in the backend.")
        # Claim atomically and let the lifespan worker execute it.
        work = service.claim(str(identifier))
        service.wake.set()
        return {"id": work[0]}

    @router.post("/scans/{identifier}/review", status_code=204)
    def review(identifier: UUID, request: ReviewRequest):
        service.review(str(identifier), request)

    @router.post("/scans/{identifier}/prepare", status_code=202)
    def prepare(identifier: UUID, request: ImportRequest):
        return service.enqueue(str(identifier), request)

    @router.post("/scans/{identifier}/retry/{clip_id}", status_code=202)
    def retry(identifier: UUID, clip_id: str):
        service.intake.retry(str(identifier), clip_id)
        service.wake.set()

    @router.delete("/imports/{clip_id}")
    def purge(clip_id: str):
        return deletions.purge_twitch(clip_id, previews)

    return router
