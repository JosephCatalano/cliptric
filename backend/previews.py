import threading
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .files import remove_artifact
from .media import MediaError, RenderCancelled


class Previews:
    def __init__(self, store, media):
        self.store, self.media = store, media
        self.root = store.root / "thumbnails"
        self.root.mkdir(exist_ok=True)
        self.workers = threading.Semaphore(2)
        self.slots = threading.BoundedSemaphore(10)
        self.stop = threading.Event()
        self.frames = [threading.Lock() for _ in range(6)]

    def thumbnail(self, identifier, index):
        recording = self.store.recording(identifier)
        if not recording:
            raise HTTPException(404, "Recording not found.")
        path = self.root / f"source-{identifier}-{index}.jpg"
        if path.is_file():
            return path
        if self.stop.is_set() or not self.slots.acquire(blocking=False):
            raise HTTPException(503, "Preview generation is busy. Reload the preview shortly.")
        try:
            with self.workers, self.frames[index]:
                if path.is_file():
                    return path
                # Different requests can target the same cached frame. A controlled
                # unique partial path avoids overwriting a concurrent extraction.
                from uuid import uuid4

                temporary = self.root / f"{uuid4()}.partial.jpg"
                try:
                    timestamp = min(
                        recording.duration * index / 5, max(0, recording.duration - 0.1)
                    )
                    self.media.thumbnail(
                        self.store.source_path(identifier), temporary, timestamp, self.stop
                    )
                    temporary.replace(path)
                except (MediaError, RenderCancelled, OSError) as exc:
                    raise HTTPException(
                        503,
                        "Could not generate this source preview. Reload it or preview the video directly.",
                    ) from exc
                finally:
                    remove_artifact(temporary)
            return path
        finally:
            self.slots.release()


def create_preview_router(manager):
    router = APIRouter()

    @router.get("/api/recordings/{identifier}")
    def recording(identifier: UUID):
        value = manager.store.recording(str(identifier))
        if value is None:
            raise HTTPException(404, "Recording not found.")
        return value

    @router.get("/api/recordings/{identifier}/thumbnail")
    def thumbnail(identifier: UUID, index: int = Query(default=2, ge=0, le=5)):
        return FileResponse(
            manager.thumbnail(str(identifier), index),
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400"},
        )

    return router
