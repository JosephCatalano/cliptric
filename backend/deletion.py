"""Local workspace/export deletion with durable, bounded file cleanup."""

from __future__ import annotations

import logging
from contextlib import ExitStack
from pathlib import PurePosixPath
from sqlite3 import Connection
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException

from .project_models import ACTIVE, Project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .instagram import Instagram
    from .jobs import Jobs
    from .projects import Projects
    from .scheduling import Scheduler
    from .social_publishing import SocialPublisher
    from .storage import Store
    from .tiktok import TikTok
    from .youtube_publishing import YouTubePublishing

logger = logging.getLogger(__name__)


class Deletions:
    def __init__(
        self,
        store: Store,
        projects: Projects,
        jobs: Jobs,
        scheduler: Scheduler,
        publishers: Sequence[TikTok | YouTubePublishing | Instagram | SocialPublisher],
    ):
        self.store, self.projects, self.jobs = store, projects, jobs
        # Match scheduler -> publisher ordering and serialize project edits/renders.
        self.locks = [projects.lock, scheduler.lock, *(p.lock for p in publishers), jobs.lock]

    def initialize(self) -> None:
        with self.store.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS deleted_files (path TEXT PRIMARY KEY)")
        self.cleanup()

    def cleanup(self) -> bool:
        pending = False
        with self.store.connection() as db:
            paths = [row[0] for row in db.execute("SELECT path FROM deleted_files")]
        for relative in paths:
            parts = PurePosixPath(relative).parts
            if len(parts) != 2 or parts[0] not in {"exports", "logs", "thumbnails"}:
                raise RuntimeError("Invalid deletion cleanup path.")
            path = self.store.root.joinpath(*parts)
            if not path.parent.resolve().is_relative_to(self.store.root.resolve()):
                raise RuntimeError("Deletion cleanup directory is outside local storage.")
            try:
                # Unlink only the named file, never a directory or symlink target.
                path.unlink(missing_ok=True)
            except OSError:
                pending = True
                logger.warning("Deleted file remains in use; cleanup will retry: %s", relative)
            else:
                with self.store.connection() as db:
                    db.execute("DELETE FROM deleted_files WHERE path=?", (relative,))
        return pending

    @staticmethod
    def _queue(db: Connection, paths: list[str]) -> None:
        db.executemany("INSERT OR IGNORE INTO deleted_files VALUES (?)", [(p,) for p in paths])

    def _idle(self, project: Project) -> None:
        future = self.projects.scheduled.get(project.id)
        if project.status in ACTIVE or future is not None:
            raise HTTPException(
                409, "Cancel project processing and wait for it to stop before deleting."
            )

    def delete(
        self,
        *,
        project_id: str | None = None,
        item_id: str | None = None,
        clip_id: str | None = None,
        revision: int | None = None,
    ) -> dict[str, bool]:
        with ExitStack() as stack:
            for lock in self.locks:
                if not lock.acquire(blocking=False):
                    raise HTTPException(
                        409,
                        "A media or publishing operation is busy. Wait for it to finish, then retry deletion.",
                    )
                stack.callback(lock.release)
            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                projects = [
                    Project.model_validate_json(r[0])
                    for r in db.execute("SELECT payload FROM projects")
                ]
                target = next((p for p in projects if p.id == project_id), None)
                if project_id and target is None:
                    raise HTTPException(404, "Project not found.")
                selected = []
                if target:
                    self._idle(target)
                    selected = [i for i in target.items if not item_id or i.id == item_id]
                    if item_id and not selected:
                        raise HTTPException(404, "Clip does not belong to this project.")
                    if item_id and selected[0].revision != revision:
                        raise HTTPException(409, "This clip changed. Refresh before deleting.")
                exports = {i.export_id for i in selected if i.export_id}
                exports.update(i.pending_export_id for i in selected if i.pending_export_id)
                if clip_id:
                    if not db.execute("SELECT 1 FROM clips WHERE id=?", (clip_id,)).fetchone():
                        raise HTTPException(404, "Export not found.")
                    exports.add(clip_id)
                # UUID-only generated names never incorporate source filenames.
                exports = {str(UUID(e)) for e in exports}
                for identifier in exports:
                    row = db.execute(
                        "SELECT status FROM clips WHERE id=?", (identifier,)
                    ).fetchone()
                    if self.jobs.active == identifier or (
                        row and row[0] in {"queued", "rendering", "cancelling"}
                    ):
                        raise HTTPException(
                            409, "Wait for clip processing to stop before deleting."
                        )
                    for table, finished in (
                        ("tiktok_posts", ("published", "failed")),
                        ("youtube_uploads", ("ready", "failed")),
                        ("instagram_posts", ("published", "failed")),
                        ("x_posts", ("published", "failed")),
                        ("facebook_posts", ("published", "failed")),
                    ):
                        placeholders = ",".join("?" for _ in finished)
                        if db.execute(
                            f"SELECT 1 FROM {table} WHERE export_id=? AND status NOT IN ({placeholders})",
                            (identifier, *finished),
                        ).fetchone():
                            raise HTTPException(
                                409,
                                "This clip has a scheduled, active, or unconfirmed post. Cancel its schedule or resolve its publishing status before deleting.",
                            )
                    for post in db.execute(
                        "SELECT id,platform,status FROM scheduled_posts WHERE export_id=? AND status IN ('scheduled','dispatching','submitted','unknown')",
                        (identifier,),
                    ):
                        table, terminal = {
                            "tiktok": ("tiktok_posts", "published"),
                            "youtube": ("youtube_uploads", "ready"),
                            "instagram": ("instagram_posts", "published"),
                            "x": ("x_posts", "published"),
                            "facebook": ("facebook_posts", "published"),
                        }[post["platform"]]
                        complete = db.execute(
                            f"SELECT 1 FROM {table} WHERE id=? AND status IN (?, 'failed')",
                            (post["id"], terminal),
                        ).fetchone()
                        if post["status"] in {"scheduled", "dispatching"} or not complete:
                            raise HTTPException(
                                409,
                                "Cancel the clip's schedule or resolve its publishing status before deleting.",
                            )
                for project in projects:
                    removed = [
                        i
                        for i in project.items
                        if (target and project.id == target.id and (not item_id or i.id == item_id))
                        or i.export_id in exports
                        or i.pending_export_id in exports
                    ]
                    if not removed and project is not target:
                        continue
                    self._idle(project)
                    for item in removed:
                        identifier = str(UUID(item.id))
                        paths = [f"thumbnails/{identifier}.jpg"]
                        paths.extend(
                            f"thumbnails/{p.name}"
                            for p in self.projects.root.glob(f"{identifier}-*.jpg")
                        )
                        self._queue(db, paths)
                    project.items = [i for i in project.items if i not in removed]
                    if project is target and not item_id:
                        db.execute("DELETE FROM projects WHERE id=?", (project.id,))
                        self._queue(db, [f"thumbnails/{UUID(project.id)}.jpg"])
                    else:
                        project.stage = f"{len(project.items)} clips remaining"
                        payload = project.model_dump_json(
                            exclude={"items": {"__all__": {"export"}}}
                        )
                        db.execute(
                            "UPDATE projects SET payload=? WHERE id=?", (payload, project.id)
                        )
                for identifier in exports:
                    for table in (
                        "clip_captions",
                        "clip_headlines",
                        "clip_overlays",
                        "clip_framing",
                        "clip_crop",
                        "clip_follow",
                        "clip_quality",
                    ):
                        db.execute(f"DELETE FROM {table} WHERE clip_id=?", (identifier,))
                    db.execute("DELETE FROM clips WHERE id=?", (identifier,))
                    self._queue(
                        db,
                        [
                            f"exports/{identifier}.mp4",
                            f"logs/{identifier}.log",
                            f"exports/{identifier}.partial.mp4",
                            f"exports/{identifier}.partial.ass",
                            f"exports/{identifier}.headline.partial.ass",
                            f"exports/{identifier}.cover.partial.jpg",
                        ],
                    )
            # Metadata removal and file-cleanup intent commit together. A locked
            # Windows video cannot restore a deleted workspace on restart.
            pending = self.cleanup()
            return {"deleted": True, "cleanup_pending": pending}
