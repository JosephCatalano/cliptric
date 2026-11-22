import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .files import remove_artifact
from .follow_models import FOLLOW_VERSION, FollowPlan
from .layout_models import LAYOUT_VERSION, AutoLayoutPlan
from .models import Clip, ExportRequest, Recording, RenderPerformance, SelectionDraft


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DraftConflict(ValueError):
    pass


def _headline_snapshot(raw: str | None) -> dict | None:
    # Old exports were rendered without fades. Keep their saved appearance distinct
    # so an explicit project update renders the new style instead of reusing them.
    return {"fade_ms": 0, "appearance": "classic", **json.loads(raw)} if raw is not None else None


def _caption_snapshot(raw: str | None) -> dict | None:
    if raw is None:
        return None
    saved = {"presentation_version": 1, **json.loads(raw)}
    customization = saved.get("customization", {})
    if isinstance(customization, dict):
        # Exports written before explicit font snapshots used 64px. New defaults
        # must not relabel their already-rendered media as 80px.
        saved["customization"] = {"font_size": 64, **customization}
    return saved


class Store:
    def __init__(self, root: Path, *, initialize: bool = True):
        self.root = root
        for directory in ("sources", "exports", "logs"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        self.database = root / "clipping.sqlite3"
        if initialize:
            self.initialize()

    def initialize(self) -> None:
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4, 5, 6):
                raise RuntimeError("Unsupported local database version.")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS recordings (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, extension TEXT NOT NULL,
                    duration REAL NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
                    has_audio INTEGER NOT NULL, size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clips (
                    id TEXT PRIMARY KEY, recording_id TEXT NOT NULL REFERENCES recordings(id),
                    start REAL NOT NULL, end REAL NOT NULL, mode TEXT NOT NULL,
                    status TEXT NOT NULL, error TEXT, duration REAL, size_bytes INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS recordings_created ON recordings(created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS clips_recording_created ON clips(recording_id, created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS clips_follow_interval ON clips(recording_id, mode, start, end, created_at DESC, id DESC);
                CREATE TABLE IF NOT EXISTS clip_posted (
                    clip_id TEXT PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
                    posted INTEGER NOT NULL CHECK(posted IN (0, 1))
                );
            """)
            # Additive caption/draft and headline tables preserve the media library.
            db.executescript("""
                CREATE TABLE IF NOT EXISTS selection_drafts (
                    recording_id TEXT PRIMARY KEY REFERENCES recordings(id),
                    payload TEXT NOT NULL, revision INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clip_captions (
                    clip_id TEXT PRIMARY KEY REFERENCES clips(id),
                    options TEXT NOT NULL, words TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clip_headlines (
                    clip_id TEXT PRIMARY KEY REFERENCES clips(id), options TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clip_overlays (
                    clip_id TEXT PRIMARY KEY REFERENCES clips(id), options TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clip_framing (
                    clip_id TEXT PRIMARY KEY REFERENCES clips(id), options TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clip_auto_layout (
                    clip_id TEXT PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clip_follow (
                    clip_id TEXT PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clip_crop (
                    clip_id TEXT PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE, options TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clip_quality (
                    clip_id TEXT PRIMARY KEY REFERENCES clips(id), options TEXT NOT NULL
                );
                PRAGMA user_version = 6;
                CREATE TABLE IF NOT EXISTS clip_performance (
                    clip_id TEXT PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
                    payload TEXT NOT NULL
                );
            """)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self) -> None:
        for pattern in ("*.follow.partial.gray", "*.follow.partial.log"):
            for path in (self.root / "exports").glob(pattern):
                remove_artifact(path)
        for path in (self.root / "exports").glob("*.cover.partial.jpg"):
            remove_artifact(path)
        with self.connection() as db:
            db.execute(
                "UPDATE clips SET status = 'failed', error = ? WHERE status IN ('queued','rendering','cancelling')",
                ("Processing was interrupted. Create a new export to retry.",),
            )
        for path in (self.root / "exports").glob("*.partial.mp4"):
            remove_artifact(path)
        for path in (self.root / "exports").glob("*.partial.ass"):
            remove_artifact(path)
        for path in (self.root / "sources").glob("*.upload"):
            remove_artifact(path)

    def recordings(self, limit: int = 100, offset: int = 0) -> list[Recording]:
        with self.connection() as db:
            return [
                Recording.model_validate(dict(row))
                for row in db.execute(
                    "SELECT * FROM recordings ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def recording(self, recording_id: str) -> Recording | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
            return Recording.model_validate(dict(row)) if row else None

    def source_path(self, recording_id: str) -> Path:
        with self.connection() as db:
            row = db.execute(
                "SELECT extension FROM recordings WHERE id = ?", (recording_id,)
            ).fetchone()
        if row is None:
            raise FileNotFoundError("Recording not found.")
        return self.root / "sources" / f"{recording_id}{row['extension']}"

    def save_recording(self, recording: Recording, extension: str) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO recordings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    recording.id,
                    recording.name,
                    extension,
                    recording.duration,
                    recording.width,
                    recording.height,
                    int(recording.has_audio),
                    recording.size_bytes,
                    recording.created_at,
                ),
            )

    def create_clip(
        self,
        clip_id: str,
        recording_id: str,
        request: ExportRequest,
        caption_words: list[dict] | None = None,
        follow_plan: FollowPlan | None = None,
        auto_layout: AutoLayoutPlan | None = None,
    ) -> Clip:
        with self.connection() as db:
            db.execute(
                "INSERT INTO clips VALUES (?, ?, ?, ?, ?, 'queued', NULL, NULL, NULL, ?)",
                (clip_id, recording_id, request.start, request.end, request.mode, now()),
            )
            db.execute(
                "INSERT INTO clip_quality VALUES (?,?)",
                (clip_id, request.quality.model_dump_json()),
            )
            if request.headline is not None:
                db.execute(
                    "INSERT INTO clip_headlines VALUES (?, ?)",
                    (clip_id, request.headline.model_dump_json()),
                )
            if auto_layout is not None:
                if request.mode != "auto" or (auto_layout.start, auto_layout.end) != (
                    request.start,
                    request.end,
                ):
                    raise ValueError("Auto layout snapshot must match its export interval.")
                db.execute(
                    "INSERT INTO clip_auto_layout VALUES (?,?)",
                    (clip_id, auto_layout.model_dump_json()),
                )
            if follow_plan is not None:
                if (
                    auto_layout.mode if request.mode == "auto" and auto_layout else request.mode
                ) != "follow" or (follow_plan.start, follow_plan.end) != (
                    request.start,
                    request.end,
                ):
                    raise ValueError(
                        "Auto follow snapshot must match the export interval and layout."
                    )
                db.execute(
                    "INSERT INTO clip_follow VALUES (?,?)", (clip_id, follow_plan.model_dump_json())
                )
            if request.crop_region is not None:
                db.execute(
                    "INSERT INTO clip_crop VALUES (?, ?)",
                    (clip_id, request.crop_region.model_dump_json()),
                )
            if request.split_layout is not None:
                db.execute(
                    "INSERT INTO clip_framing VALUES (?, ?)",
                    (clip_id, request.split_layout.model_dump_json()),
                )
            if request.overlays:
                db.execute(
                    "INSERT INTO clip_overlays VALUES (?, ?)",
                    (clip_id, json.dumps([overlay.model_dump() for overlay in request.overlays])),
                )
            if request.captions is not None:
                if caption_words is None:
                    raise ValueError("Captioned export requires a validated transcript snapshot.")
                db.execute(
                    "INSERT INTO clip_captions VALUES (?, ?, ?)",
                    (clip_id, request.captions.model_dump_json(), json.dumps(caption_words)),
                )
        clip = self.clip(clip_id)
        assert clip is not None
        return clip

    def clip(self, clip_id: str) -> Clip | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT clips.*, COALESCE(clip_posted.posted, 0) AS posted FROM clips LEFT JOIN clip_posted ON clip_posted.clip_id=clips.id WHERE clips.id = ?",
                (clip_id,),
            ).fetchone()
            return self._clip(db, row) if row else None

    @staticmethod
    def _clip(db: sqlite3.Connection, row: sqlite3.Row) -> Clip:
        auto_layout = db.execute(
            "SELECT payload FROM clip_auto_layout WHERE clip_id=?", (row["id"],)
        ).fetchone()
        follow = db.execute(
            "SELECT payload FROM clip_follow WHERE clip_id=?", (row["id"],)
        ).fetchone()
        performance = db.execute(
            "SELECT payload FROM clip_performance WHERE clip_id=?", (row["id"],)
        ).fetchone()
        caption = db.execute(
            "SELECT options FROM clip_captions WHERE clip_id = ?", (row["id"],)
        ).fetchone()
        headline = db.execute(
            "SELECT options FROM clip_headlines WHERE clip_id = ?", (row["id"],)
        ).fetchone()
        overlays = db.execute(
            "SELECT options FROM clip_overlays WHERE clip_id = ?", (row["id"],)
        ).fetchone()
        framing = db.execute(
            "SELECT options FROM clip_framing WHERE clip_id = ?", (row["id"],)
        ).fetchone()
        crop = db.execute(
            "SELECT options FROM clip_crop WHERE clip_id = ?", (row["id"],)
        ).fetchone()
        quality = db.execute(
            "SELECT options FROM clip_quality WHERE clip_id=?", (row["id"],)
        ).fetchone()
        return Clip.model_validate(
            {
                **dict(row),
                "posted": bool(row["posted"]),
                "performance": json.loads(performance[0]) if performance else None,
                "follow_plan": json.loads(follow[0]) if follow else None,
                "auto_layout": json.loads(auto_layout[0]) if auto_layout else None,
                "captions": _caption_snapshot(caption[0] if caption else None),
                "headline": _headline_snapshot(headline[0] if headline else None),
                "overlays": json.loads(overlays[0]) if overlays else [],
                "split_layout": json.loads(framing[0]) if framing else None,
                "crop_region": json.loads(crop[0]) if crop else None,
                "quality": json.loads(quality[0]) if quality else {},
            }
        )

    def clips(self, recording_id: str, limit: int = 100, offset: int = 0) -> list[Clip]:
        with self.connection() as db:
            return [
                Clip.model_validate(
                    {
                        **dict(row),
                        "posted": bool(row["posted"]),
                        "auto_layout": json.loads(row["auto_payload"])
                        if row["auto_payload"]
                        else None,
                        "follow_plan": json.loads(row["follow_payload"])
                        if row["follow_payload"]
                        else None,
                        "performance": json.loads(row["render_performance"])
                        if row["render_performance"]
                        else None,
                        "headline": _headline_snapshot(row["headline_options"]),
                        "split_layout": json.loads(row["framing_options"])
                        if row["framing_options"]
                        else None,
                        "crop_region": json.loads(row["crop_options"])
                        if row["crop_options"]
                        else None,
                        "quality": json.loads(row["quality_options"])
                        if row["quality_options"]
                        else {},
                        "overlays": json.loads(row["overlay_options"])
                        if row["overlay_options"]
                        else [],
                        "captions": _caption_snapshot(row["caption_options"]),
                    }
                )
                for row in db.execute(
                    "SELECT clips.*, clip_auto_layout.payload AS auto_payload, clip_follow.payload AS follow_payload, clip_crop.options AS crop_options, clip_performance.payload AS render_performance, COALESCE(clip_posted.posted, 0) AS posted, clip_captions.options AS caption_options, clip_headlines.options AS headline_options, clip_overlays.options AS overlay_options, clip_framing.options AS framing_options, clip_quality.options AS quality_options FROM clips "
                    "LEFT JOIN clip_auto_layout ON clips.id=clip_auto_layout.clip_id "
                    "LEFT JOIN clip_follow ON clips.id=clip_follow.clip_id "
                    "LEFT JOIN clip_crop ON clips.id=clip_crop.clip_id "
                    "LEFT JOIN clip_performance ON clips.id=clip_performance.clip_id "
                    "LEFT JOIN clip_posted ON clips.id = clip_posted.clip_id "
                    "LEFT JOIN clip_captions ON clips.id = clip_captions.clip_id "
                    "LEFT JOIN clip_headlines ON clips.id = clip_headlines.clip_id "
                    "LEFT JOIN clip_overlays ON clips.id = clip_overlays.clip_id "
                    "LEFT JOIN clip_framing ON clips.id = clip_framing.clip_id "
                    "LEFT JOIN clip_quality ON clips.id = clip_quality.clip_id "
                    "WHERE recording_id = ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                    (recording_id, limit, offset),
                )
            ]

    def save_follow_plan(self, clip_id: str, plan: FollowPlan) -> None:
        with self.connection() as db:
            clip = db.execute(
                "SELECT mode, start, end, status FROM clips WHERE id=?", (clip_id,)
            ).fetchone()
            if (
                clip is None
                or clip["mode"] not in {"follow", "auto"}
                or clip["status"] != "rendering"
                or (clip["start"], clip["end"]) != (plan.start, plan.end)
            ):
                raise ValueError("Auto follow snapshot does not match an active export.")
            if clip["mode"] == "auto":
                decision = db.execute(
                    "SELECT payload FROM clip_auto_layout WHERE clip_id=?", (clip_id,)
                ).fetchone()
                if (
                    decision is None
                    or AutoLayoutPlan.model_validate_json(decision[0]).mode != "follow"
                ):
                    raise ValueError("Auto follow requires a matching automatic follow decision.")
            db.execute("INSERT INTO clip_follow VALUES (?,?)", (clip_id, plan.model_dump_json()))

    def cached_follow_plan(self, recording_id: str, start: float, end: float) -> FollowPlan | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT clip_follow.payload FROM clip_follow JOIN clips ON clips.id=clip_follow.clip_id "
                "WHERE clips.recording_id=? AND clips.start=? AND clips.end=? AND clips.mode IN ('follow','auto') "
                "ORDER BY clips.created_at DESC, clips.id DESC LIMIT 1",
                (recording_id, start, end),
            ).fetchone()
        plan = FollowPlan.model_validate_json(row[0]) if row else None
        return plan if plan and plan.version == FOLLOW_VERSION else None

    def save_auto_layout(self, clip_id: str, plan: AutoLayoutPlan) -> None:
        with self.connection() as db:
            clip = db.execute(
                "SELECT mode,start,end,status FROM clips WHERE id=?", (clip_id,)
            ).fetchone()
            if (
                clip is None
                or clip["mode"] != "auto"
                or clip["status"] != "rendering"
                or (clip["start"], clip["end"]) != (plan.start, plan.end)
            ):
                raise ValueError("Auto layout snapshot does not match an active export.")
            db.execute(
                "INSERT INTO clip_auto_layout VALUES (?,?)", (clip_id, plan.model_dump_json())
            )

    def cached_auto_layout(
        self, recording_id: str, start: float, end: float, context: str
    ) -> AutoLayoutPlan | None:
        with self.connection() as db:
            rows = db.execute(
                "SELECT clip_auto_layout.payload FROM clip_auto_layout JOIN clips ON clips.id=clip_auto_layout.clip_id WHERE clips.recording_id=? AND clips.start=? AND clips.end=? AND clips.mode='auto' ORDER BY clips.created_at DESC,clips.id DESC LIMIT 16",
                (recording_id, start, end),
            ).fetchall()
        for row in rows:
            plan = AutoLayoutPlan.model_validate_json(row[0])
            if plan.version == LAYOUT_VERSION and plan.context == context:
                return plan
        return None

    def set_posted(self, clip_id: str, posted: bool, *, db: sqlite3.Connection) -> None:
        db.execute(
            "INSERT INTO clip_posted VALUES (?,?) ON CONFLICT(clip_id) DO UPDATE SET posted=excluded.posted",
            (clip_id, int(posted)),
        )

    def update_clip(
        self,
        clip_id: str,
        status: str,
        error: str | None = None,
        duration: float | None = None,
        size_bytes: int | None = None,
        performance: dict | None = None,
    ) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE clips SET status = ?, error = ?, duration = ?, size_bytes = ? WHERE id = ?",
                (status, error, duration, size_bytes, clip_id),
            )
            if performance is not None:
                db.execute(
                    "INSERT INTO clip_performance VALUES (?,?) ON CONFLICT(clip_id) DO UPDATE SET payload=excluded.payload",
                    (clip_id, RenderPerformance.model_validate(performance).model_dump_json()),
                )

    def export_path(self, clip_id: str) -> Path:
        return self.root / "exports" / f"{clip_id}.mp4"

    def caption_words(self, clip_id: str) -> list[dict] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT words FROM clip_captions WHERE clip_id = ?", (clip_id,)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def draft(self, recording: Recording) -> SelectionDraft:
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM selection_drafts WHERE recording_id = ?", (recording.id,)
            ).fetchone()
            return (
                SelectionDraft.model_validate_json(row[0])
                if row
                else SelectionDraft(
                    start=0,
                    end=min(recording.duration, 30),
                    caption_preset="impact",
                    caption_position="auto",
                    headline={"text": Path(recording.name).stem[:120] or "Your moment"},
                )
            )

    def save_draft(self, recording_id: str, draft: SelectionDraft) -> SelectionDraft:
        saved = draft.model_copy(update={"revision": draft.revision + 1})
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM selection_drafts WHERE recording_id = ?", (recording_id,)
            ).fetchone()
            if (row[0] if row else 0) != draft.revision:
                raise DraftConflict("Selection changed in another window. Reload it before saving.")
            db.execute(
                "INSERT INTO selection_drafts VALUES (?, ?, ?) ON CONFLICT(recording_id) "
                "DO UPDATE SET payload = excluded.payload, revision = excluded.revision",
                (recording_id, saved.model_dump_json(), saved.revision),
            )
        return saved
