import json
import re
import tempfile
import threading
import zipfile
from collections.abc import Callable
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from starlette.types import Receive, Scope, Send

from .files import require_space
from .project_models import (
    ACTIVE,
    ItemEdit,
    MoreClipsRequest,
    PostedUpdate,
    Project,
    ProjectCreate,
    ResumeRequest,
)
from .projects import Projects

MAX_BUNDLE_BYTES = 2 * 1024**3


class BundleResponse(StreamingResponse):
    def __init__(self, content, *, cleanup: Callable[[], None], **kwargs):
        super().__init__(content, **kwargs)
        self.cleanup = cleanup

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # Starlette background tasks are skipped when an ASGI send fails.
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.cleanup()


def create_project_router(manager: Projects):
    router = APIRouter()
    bundle_lock = threading.Lock()

    @router.get("/api/projects", response_model=list[Project])
    def projects(
        limit: int = Query(default=50, ge=1, le=50),
        offset: int = Query(default=0, ge=0),
        search: str = Query(default="", max_length=120),
        status: Literal[
            "all", "processing", "ready", "partial", "failed", "cancelled", "interrupted"
        ] = "all",
        sort: Literal["newest", "oldest", "title"] = "newest",
    ):
        return [
            manager.get(project.id) if project.status in ACTIVE else project
            for project in manager.records.list(
                limit, offset, search=search, status=status, sort=sort
            )
        ]

    @router.post("/api/projects", response_model=Project, status_code=202)
    def create(request: ProjectCreate):
        return manager.create(request)

    @router.get("/api/projects/{identifier}", response_model=Project)
    def project(identifier: UUID):
        return manager.get(str(identifier))

    @router.post("/api/projects/{identifier}/resume", response_model=Project, status_code=202)
    def resume(identifier: UUID, request: ResumeRequest):
        return manager.resume(str(identifier), request)

    @router.post("/api/projects/{identifier}/cancel", response_model=Project)
    def cancel(identifier: UUID):
        return manager.cancel(str(identifier))

    @router.post("/api/projects/{identifier}/more-clips", response_model=Project, status_code=202)
    def more_clips(identifier: UUID, request: MoreClipsRequest):
        return manager.more_clips(str(identifier), request)

    @router.get("/api/projects/{identifier}/clips/{item_id}/hooks")
    def hooks(identifier: UUID, item_id: UUID):
        return manager.hooks(str(identifier), str(item_id))

    @router.patch(
        "/api/projects/{identifier}/clips/{item_id}", response_model=Project, status_code=202
    )
    def edit(identifier: UUID, item_id: UUID, request: ItemEdit):
        return manager.edit(str(identifier), str(item_id), request)

    @router.patch("/api/projects/{identifier}/clips/{item_id}/posted", response_model=PostedUpdate)
    def posted(identifier: UUID, item_id: UUID, request: PostedUpdate):
        manager.set_posted(str(identifier), str(item_id), request.posted)
        return request

    @router.get("/api/projects/{identifier}/thumbnail")
    def thumbnail(identifier: UUID, item_id: UUID | None = None, download: bool = False):
        project = manager.get(str(identifier), hydrate=False)
        item = next((i for i in project.items if i.id == str(item_id)), None) if item_id else None
        if (item_id and item is None) or not (item or project).thumbnail:
            raise HTTPException(404, "Thumbnail is not available.")
        path = manager.thumbnail_path(project, item)
        if not path.is_file():
            raise HTTPException(404, "Thumbnail file is missing.")
        return FileResponse(
            path,
            media_type="image/jpeg",
            filename=f"clip-{item.id if item else project.id}.jpg" if download else None,
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/api/projects/{identifier}/bundle")
    def bundle(identifier: UUID, ids: str | None = Query(default=None, max_length=2774)):
        project = manager.get(str(identifier))
        selected = {i.id for i in project.items}
        if ids is not None:
            try:
                values = ids.split(",")
                if not 1 <= len(values) <= 75:
                    raise ValueError()
                selected = {str(UUID(value)) for value in values}
            except ValueError as exc:
                raise HTTPException(
                    422, "Select between one and seventy-five valid project clip IDs."
                ) from exc
            if not selected <= {i.id for i in project.items}:
                raise HTTPException(404, "A selected clip does not belong to this project.")
        ready, excluded = [], []
        for item in project.items:
            if item.id not in selected:
                continue
            if (
                item.export
                and item.export.status == "succeeded"
                and not item.error
                and item.review != "rejected"
            ):
                ready.append(item)
            else:
                excluded.append(
                    {
                        "id": item.id,
                        "title": item.title,
                        "reason": "rejected in project review"
                        if item.review == "rejected"
                        else item.error or (item.export.status if item.export else "not rendered"),
                    }
                )
        if not ready:
            raise HTTPException(
                409,
                "No selected outputs are ready. Unfinished or failed clips are excluded from ZIP downloads.",
            )
        paths = [manager.store.export_path(i.export.id) for i in ready]
        try:
            size = sum(path.stat().st_size for path in paths)
        except OSError as exc:
            raise HTTPException(
                409, "An output is missing. Resume the project before downloading."
            ) from exc
        if size > MAX_BUNDLE_BYTES:
            raise HTTPException(
                413, "This ZIP exceeds 2 GiB. Download smaller selections or individual clips."
            )
        if not bundle_lock.acquire(blocking=False):
            raise HTTPException(409, "Another ZIP download is active. Wait for it to finish.")
        output = None
        try:
            require_space(manager.store.root, manager.media.settings.min_free_bytes, size + 1024**2)
            output = tempfile.TemporaryFile(dir=manager.store.root)
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
                manifest = {
                    "project_id": project.id,
                    "title": project.title,
                    "included": [],
                    "excluded": excluded,
                }
                for index, (item, path) in enumerate(zip(ready, paths, strict=True), 1):
                    slug = re.sub(r"[^a-zA-Z0-9]+", "-", item.title).strip("-")[:60] or "clip"
                    filename = f"{index:02}-{slug}-{item.id[:8]}.mp4"
                    archive.write(path, arcname=filename)
                    manifest["included"].append(
                        {
                            "id": item.id,
                            "title": item.title,
                            "file": filename,
                            "start": item.start,
                            "end": item.end,
                            "export_id": item.export.id,
                            "recording_id": item.recording_id or project.recording_id,
                            "source_name": item.source_name,
                            "title_warning": item.title_error,
                            "transcription_warning": item.transcription_warning,
                        }
                    )
                archive.writestr("manifest.json", json.dumps(manifest, indent=2))
            output.seek(0)
        except Exception as exc:
            if output is not None:
                output.close()
            bundle_lock.release()
            if isinstance(exc, OSError):
                raise HTTPException(
                    503, "Cannot prepare this ZIP. Check local disk space and output files."
                ) from exc
            raise

        cleanup_lock = threading.Lock()

        def cleanup():
            with cleanup_lock:
                if not output.closed:
                    output.close()
                    bundle_lock.release()

        def chunks():
            try:
                while data := output.read(1024**2):
                    yield data
            finally:
                cleanup()

        return BundleResponse(
            chunks(),
            media_type="application/zip",
            cleanup=cleanup,
            headers={
                "Content-Disposition": f'attachment; filename="project-{identifier}.zip"',
                "X-Clips-Included": str(len(ready)),
                "X-Clips-Excluded": str(len(excluded)),
                "Cache-Control": "no-store",
            },
        )

    return router
