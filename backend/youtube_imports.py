import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import ROOT
from .files import read_json_snapshot, require_space
from .media import MediaError, RenderCancelled
from .models import Recording
from .storage import now
from .transcription_models import clean_text
from .youtube_network import canonical_video_url, video_provider

logger = logging.getLogger(__name__)
ACTIVE = {"queued", "downloading", "processing", "cancelling"}
AUDIO_SELECTION_VERSION = 2


class YouTubeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=2048, strict=True)
    _url = field_validator("url")(canonical_video_url)


class YouTubeImport(BaseModel):
    id: str
    url: str
    status: Literal[
        "queued",
        "downloading",
        "processing",
        "cancelling",
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
    ]
    created_at: str
    recording_id: str | None = None
    downloaded_bytes: int = Field(default=0, ge=0, strict=True)
    total_bytes: int | None = Field(default=None, ge=1, strict=True)
    error: str | None = None
    audio_selection_version: int = 0


class YouTubeSource(BaseModel):
    preview_image_url: str | None = Field(default=None, max_length=2048)
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str
    url: str
    name: str = Field(min_length=1, max_length=200)
    duration: float = Field(gt=0, le=21600)
    has_audio: bool

    @property
    def thumbnail_url(self):
        if video_provider(self.url) == "twitch":
            return self.preview_image_url
        return f"https://i.ytimg.com/vi/{self.url.rsplit('=', 1)[1]}/hqdefault.jpg"

    @field_validator("preview_image_url")
    @classmethod
    def safe_preview(cls, value):
        if value is None:
            return value
        url = urlsplit(value)
        allowed = ("jtvnw.net", "twitchcdn.net", "twitch.tv", "ytimg.com")
        if (
            url.scheme != "https"
            or url.username
            or url.password
            or url.port not in {None, 443}
            or not any(
                url.hostname == domain or (url.hostname or "").endswith("." + domain)
                for domain in allowed
            )
        ):
            raise ValueError("Unsupported video preview image.")
        return value


class YouTubeImports:
    def __init__(self, store, media, settings):
        self.store, self.media, self.settings = store, media, settings
        self.root = store.root / "youtube-work"
        self.root.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="youtube-import")
        self.active = None
        self.stopping = False
        self.preview_lock = threading.Lock()
        self.preview_stop = threading.Event()

    def initialize(self):
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS youtube_imports (
                    id TEXT PRIMARY KEY, url TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS youtube_created ON youtube_imports(created_at DESC,id DESC);
                CREATE TABLE IF NOT EXISTS youtube_sources (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
            """)
            records = [
                YouTubeImport.model_validate_json(row[0])
                for row in db.execute("SELECT payload FROM youtube_imports")
            ]
        for job in records:
            recording = self.store.recording(job.id)
            if recording and self.store.source_path(job.id).is_file():
                job.status, job.recording_id, job.error = "succeeded", job.id, None
                self.save(job)
            elif job.status in ACTIVE:
                job.status, job.error = (
                    "interrupted",
                    "Import was interrupted. Retry the video link explicitly.",
                )
                self.save(job)
        for path in self.root.iterdir():
            try:
                UUID(path.name)
            except ValueError:
                continue
            self.cleanup(path)

    def cleanup(self, path):
        if path.is_symlink() or path.parent.resolve() != self.root.resolve():
            raise RuntimeError("Unsafe import work directory")
        if path.exists():
            shutil.rmtree(path)

    def save(self, job):
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO youtube_imports VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (job.id, job.url, job.created_at, job.model_dump_json()),
            )

    def get(self, identifier):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT payload FROM youtube_imports WHERE id=?", (identifier,)
            ).fetchone()
        if not row:
            raise HTTPException(404, "YouTube import not found.")
        return YouTubeImport.model_validate_json(row[0])

    def latest(self):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT payload FROM youtube_imports ORDER BY created_at DESC,id DESC LIMIT 1"
            ).fetchone()
        return YouTubeImport.model_validate_json(row[0]) if row else None

    def source(self, identifier):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT payload FROM youtube_sources WHERE id=?", (identifier,)
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Video preview not found. Enter the link again.")
        return YouTubeSource.model_validate_json(row[0])

    def preview(self, request):
        if self.stopping:
            raise HTTPException(503, "Import service is stopping.")
        if not self.preview_lock.acquire(blocking=False):
            raise HTTPException(409, "Another video preview is loading. Try again shortly.")
        identifier = str(uuid4())
        work = self.root / identifier
        try:
            node = shutil.which("node")
            if node is None and video_provider(request.url) == "youtube":
                raise HTTPException(503, "Video preview requires Node.js. Upload the file instead.")
            work.mkdir()
            (work / "request.json").write_text(
                json.dumps(
                    {
                        "url": request.url,
                        "node": node,
                        "max_bytes": self.settings.max_upload_bytes,
                        "max_seconds": self.settings.max_source_seconds,
                        "metadata_only": True,
                    }
                ),
                encoding="utf-8",
            )
            job = YouTubeImport(id=identifier, url=request.url, status="queued", created_at=now())
            code = self._process(
                [sys.executable, "-m", "backend.youtube_worker", str(work.resolve())],
                job,
                work,
                time.monotonic() + 60,
                cancel_event=self.preview_stop,
            )
            result = work / "result.json"
            if code or not result.is_file() or result.stat().st_size > 4096:
                raise MediaError(
                    "Could not load this public video. Check the link or upload the file."
                )
            metadata = json.loads(result.read_text(encoding="utf-8"))
            source = YouTubeSource(id=identifier, url=request.url, **metadata)
            source.name = clean_text(source.name)
            if not source.name or source.duration > self.settings.max_source_seconds:
                raise MediaError("This video is unsupported or exceeds the source duration limit.")
            with self.store.connection() as db:
                db.execute(
                    "INSERT INTO youtube_sources VALUES (?,?)",
                    (source.id, source.model_dump_json()),
                )
            return source
        except (MediaError, RenderCancelled, ValueError, OSError) as exc:
            raise HTTPException(
                422, "Could not load this public video. Check the link or upload the file."
            ) from exc
        finally:
            try:
                self.cleanup(work)
            finally:
                self.preview_lock.release()

    def submit(self, request, *, identifier=None):
        with self.lock:
            if self.stopping:
                raise HTTPException(503, "Import service is stopping.")
            if self.active:
                active = self.get(self.active)
                if active.url == request.url:
                    return active
                raise HTTPException(409, "Another video is importing. Wait or cancel it first.")
            with self.store.connection() as db:
                rows = db.execute(
                    "SELECT payload FROM youtube_imports WHERE url=? ORDER BY created_at DESC,id DESC LIMIT 1",
                    (request.url,),
                ).fetchone()
            if rows:
                prior = YouTubeImport.model_validate_json(rows[0])
                recording = self.store.recording(prior.id)
                if (
                    prior.audio_selection_version == AUDIO_SELECTION_VERSION
                    and recording
                    and self.store.source_path(recording.id).is_file()
                ):
                    prior.status, prior.recording_id, prior.error = "succeeded", recording.id, None
                    self.save(prior)
                    return prior
            if shutil.which("node") is None and video_provider(request.url) == "youtube":
                raise HTTPException(
                    503,
                    "Video import requires the project's Node.js runtime. Upload the file instead.",
                )
            job = YouTubeImport(
                id=identifier or str(uuid4()),
                url=request.url,
                status="queued",
                created_at=now(),
                audio_selection_version=AUDIO_SELECTION_VERSION,
            )
            self.save(job)
            self.active = job.id
            self.cancel_event.clear()
            try:
                self.pool.submit(self.run, job.id).add_done_callback(self.report_failure)
            except RuntimeError as exc:
                self.active = None
                job.status, job.error = "failed", "Import service stopped. Retry after restart."
                self.save(job)
                raise HTTPException(503, job.error) from exc
            return job

    @staticmethod
    def report_failure(future: Future):
        if not future.cancelled() and future.exception() is not None:
            logger.error(
                "YouTube import could not persist or clean up its state. Check local storage."
            )

    def cancel(self, identifier):
        with self.lock:
            job = self.get(identifier)
            if self.active == identifier and job.status in ACTIVE:
                self.cancel_event.set()
                job.status = "cancelling"
                self.save(job)
            return job

    def _process(
        self, command, job, work, deadline, *, downloading=False, output=None, cancel_event=None
    ):
        cancellation = self.cancel_event if cancel_event is None else cancel_event
        environment = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
        with (self.store.root / "logs" / f"youtube-{job.id}.log").open("ab") as log:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                while process.poll() is None:
                    if cancellation.wait(0.25):
                        raise RenderCancelled()
                    if time.monotonic() > deadline:
                        raise MediaError(
                            f"Video import reached its {self.settings.upload_timeout_seconds:g}-second processing limit."
                        )
                    require_space(self.store.root, self.settings.min_free_bytes)
                    if (
                        sum(
                            p.stat().st_size
                            for p in work.iterdir()
                            if p.name not in {"progress.json", "progress.json.writing"}
                            and p.is_file()
                        )
                        > self.settings.max_upload_bytes + 1024**2
                        or log.tell() > 1024**2
                    ):
                        raise MediaError("Video import exceeded its size limit.")
                    if (
                        output
                        and output.is_file()
                        and output.stat().st_size > self.settings.max_upload_bytes
                    ):
                        raise MediaError("Combined video exceeded its size limit.")
                    fields = read_json_snapshot(work / "progress.json") if downloading else None
                    if fields is not None:
                        with self.lock:
                            current = self.get(job.id)
                            current.downloaded_bytes = fields["downloaded_bytes"]
                            current.total_bytes = fields["total_bytes"]
                            self.save(YouTubeImport.model_validate(current.model_dump()))
                return process.returncode
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            timeout=10,
                            check=False,
                        )
                    if process.poll() is None:
                        process.kill()
                process.wait()

    def run(self, identifier):
        job = self.get(identifier)
        work = self.root / identifier
        destination = self.store.root / "sources" / f"{job.id}.mp4"
        published = False
        try:
            work.mkdir()
            require_space(self.store.root, self.settings.min_free_bytes)
            (work / "request.json").write_text(
                json.dumps(
                    {
                        "url": job.url,
                        "node": shutil.which("node"),
                        "max_bytes": self.settings.max_upload_bytes,
                        "max_seconds": self.settings.max_source_seconds,
                    }
                ),
                encoding="utf-8",
            )
            with self.lock:
                if self.cancel_event.is_set():
                    raise RenderCancelled()
                job.status = "downloading"
                self.save(job)
            deadline = time.monotonic() + self.settings.upload_timeout_seconds
            self._process(
                [sys.executable, "-m", "backend.youtube_worker", str(work.resolve())],
                job,
                work,
                deadline,
                downloading=True,
            )
            result_path = work / "result.json"
            if not result_path.is_file() or result_path.stat().st_size > 4096:
                raise MediaError(
                    "Video downloader did not return a valid result. Upload the file or retry explicitly."
                )
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if result.get("error"):
                raise MediaError(
                    "The source could not provide a supported public video. Check the link or upload the file. Restricted and live footage is not imported."
                )
            files = result.get("files")
            if files not in (["video.mp4"], ["video.mp4", "audio.m4a"], ["video.ts"]):
                raise MediaError("Invalid downloaded media result.")
            if files == ["video.ts"] and video_provider(job.url) != "twitch":
                raise MediaError("Unexpected segmented source format.")
            with self.lock:
                job = self.get(identifier)
                if self.cancel_event.is_set():
                    raise RenderCancelled()
                job.status = "processing"
                self.save(job)
            source = work / "video.mp4"
            if len(files) == 2 or files == ["video.ts"]:
                size = sum((work / name).stat().st_size for name in files)
                require_space(self.store.root, self.settings.min_free_bytes, size)
                # Mux local files in a separate, cancellable FFmpeg process. No remote URLs enter FFmpeg.
                merged = self.store.root / "sources" / f"{job.id}.upload"
                command = [
                    self.settings.ffmpeg,
                    "-v",
                    "error",
                    "-nostdin",
                    "-n",
                    "-max_alloc",
                    "268435456",
                ]
                for name in files:
                    command += [
                        "-protocol_whitelist",
                        "file,pipe",
                        "-format_whitelist",
                        "mpegts" if name == "video.ts" else "mov,matroska,webm",
                        "-i",
                        str((work / name).resolve()),
                    ]
                command += [
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0" if len(files) == 2 else "0:a:0",
                    "-c",
                    "copy",
                    "-map_metadata",
                    "-1",
                    "-movflags",
                    "+faststart",
                    "-f",
                    "mp4",
                    str(merged.resolve()),
                ]
                if self._process(command, job, work, deadline, output=merged):
                    raise MediaError("Could not combine the downloaded audio and video.")
                source = merged
            info = self.media.probe(source)
            if video_provider(job.url) == "twitch" and not info.has_audio:
                raise MediaError("The Twitch VOD did not contain supported audio.")
            size = source.stat().st_size
            if not 0 < size <= self.settings.max_upload_bytes:
                raise MediaError("Downloaded video exceeds the import size limit.")
            title = clean_text(result["title"])[:200]
            if not title:
                raise MediaError("The video title was not available.")
            with self.lock:
                if self.cancel_event.is_set():
                    raise RenderCancelled()
                source.replace(destination)
                self.store.save_recording(
                    Recording(
                        id=job.id,
                        name=title + ".mp4",
                        duration=info.duration,
                        width=info.width,
                        height=info.height,
                        has_audio=info.has_audio,
                        size_bytes=size,
                        created_at=now(),
                    ),
                    ".mp4",
                )
                published = True
                job.status, job.recording_id, job.error = "succeeded", job.id, None
                self.save(job)
        except RenderCancelled:
            with self.lock:
                job = self.get(identifier)
                job.status, job.error = (
                    "cancelled",
                    "Import cancelled. Completed source files remain available.",
                )
                self.save(job)
        except Exception as exc:
            logger.error(
                "YouTube import failed (%s). Provider payloads were not logged.", type(exc).__name__
            )
            with self.lock:
                job = self.get(identifier)
                job.status = "failed"
                job.error = (
                    str(exc)
                    if isinstance(exc, MediaError)
                    else "YouTube import failed. Check local storage/connectivity or upload the recording."
                )
                self.save(job)
        finally:
            try:
                from .files import remove_artifact

                remove_artifact(self.store.root / "sources" / f"{job.id}.upload")
                if not published:
                    remove_artifact(destination)
                self.cleanup(work)
            finally:
                with self.lock:
                    self.active = None

    def close(self):
        with self.lock:
            self.stopping = True
            self.cancel_event.set()
            self.preview_stop.set()
        self.pool.shutdown(wait=True)
        with self.preview_lock:
            pass  # Wait for preview worker cleanup before releasing the instance lock.


def create_youtube_router(manager):
    router = APIRouter()

    @router.post("/api/youtube-sources", response_model=YouTubeSource, status_code=201)
    @router.post("/api/video-sources", response_model=YouTubeSource, status_code=201)
    def preview(request: YouTubeRequest):
        return manager.preview(request)

    @router.get("/api/youtube-sources/{identifier}", response_model=YouTubeSource)
    @router.get("/api/video-sources/{identifier}", response_model=YouTubeSource)
    def source(identifier: UUID):
        return manager.source(str(identifier))

    @router.post("/api/youtube-imports", response_model=YouTubeImport, status_code=202)
    @router.post("/api/video-imports", response_model=YouTubeImport, status_code=202)
    def submit(request: YouTubeRequest):
        return manager.submit(request)

    @router.get("/api/youtube-imports/latest", response_model=YouTubeImport | None)
    @router.get("/api/video-imports/latest", response_model=YouTubeImport | None)
    def latest():
        return manager.latest()

    @router.get("/api/youtube-imports/{identifier}", response_model=YouTubeImport)
    @router.get("/api/video-imports/{identifier}", response_model=YouTubeImport)
    def get(identifier: UUID):
        return manager.get(str(identifier))

    @router.post("/api/youtube-imports/{identifier}/cancel", response_model=YouTubeImport)
    @router.post("/api/video-imports/{identifier}/cancel", response_model=YouTubeImport)
    def cancel(identifier: UUID):
        return manager.cancel(str(identifier))

    return router
