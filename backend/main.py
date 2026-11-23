import asyncio
import errno
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import anyio
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .boundary import RequestBoundary
from .caption_emphasis import create_emphasis_router
from .caption_styles import CaptionStyles, create_caption_style_router
from .captions import CaptionError, build_cues
from .config import Settings
from .deletion import Deletions
from .editorial_review import EditorialReviewer
from .facebook_api import FacebookAPI
from .files import remove_artifact, require_space
from .highlight_provider import OpenAIHighlights
from .highlights import Highlights, create_highlight_router
from .instagram import Instagram
from .instagram_api import InstagramSettings, secure_instagram_logs
from .instagram_routes import create_instagram_router
from .instance import InstanceLock
from .jobs import ExportBusy, Jobs
from .media import Media, MediaError
from .models import Clip, ExportRequest, Recording, SelectionDraft
from .previews import Previews, create_preview_router
from .project_models import PostedUpdate
from .project_routes import create_project_router
from .project_workspace import create_workspace_router
from .projects import Projects
from .prompt_settings import PromptSettings, create_prompt_router
from .quality_analysis import QualityAnalysis, create_quality_router
from .scheduling import Scheduler, create_scheduling_router
from .social_publishing import SocialPublisher
from .social_routes import create_social_router
from .social_transport import SocialSettings, secure_social_logs
from .storage import DraftConflict, Store, now
from .tiktok import TikTok
from .tiktok_api import TikTokSettings, secure_tiktok_logs
from .tiktok_routes import create_tiktok_router
from .transcription import Transcriptions, create_transcription_router
from .x_api import XAPI
from .youtube_api import YouTubeSettings, secure_youtube_logs
from .youtube_imports import YouTubeImports, create_youtube_router
from .youtube_publishing import YouTubePublishing
from .youtube_routes import create_youtube_publishing_router

ALLOWED_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".webm"}
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    store, media = Store(settings.data_dir, initialize=False), Media(settings)
    transcriptions = Transcriptions(store, media, settings)
    highlights = Highlights(
        store,
        transcriptions.transcripts,
        settings,
        OpenAIHighlights(settings),
        reviewer=EditorialReviewer(settings),
    )
    jobs = Jobs(store, media)
    youtube = YouTubeImports(store, media, settings)
    projects = Projects(store, media, transcriptions, highlights, jobs, youtube)
    caption_styles = CaptionStyles(store)
    previews = Previews(store, media)
    quality_analysis = QualityAnalysis(store, media, transcriptions)
    tiktok = TikTok(store, media, TikTokSettings.load(), settings.web_origin)
    secure_tiktok_logs()
    youtube_publishing = YouTubePublishing(
        store, media, YouTubeSettings.load(), settings.web_origin
    )
    secure_youtube_logs()
    instagram = Instagram(
        store,
        media,
        InstagramSettings.load(),
        settings.web_origin,
        public_origin=settings.public_origin,
    )
    secure_instagram_logs()
    x_publishing = SocialPublisher(
        store,
        media,
        XAPI(SocialSettings.load("x")),
        settings.web_origin,
        public_origin=settings.public_origin,
    )
    facebook = SocialPublisher(
        store,
        media,
        FacebookAPI(SocialSettings.load("facebook")),
        settings.web_origin,
        public_origin=settings.public_origin,
    )
    secure_social_logs()
    scheduler = Scheduler(
        store, tiktok, youtube_publishing, instagram=instagram, x=x_publishing, facebook=facebook
    )
    deletions = Deletions(
        store,
        projects,
        jobs,
        scheduler,
        [tiktok, youtube_publishing, instagram, x_publishing, facebook],
    )
    prompt_settings = PromptSettings(store)
    upload_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        instance = InstanceLock(settings.data_dir)
        instance.acquire()
        try:
            store.initialize()
            prompt_settings.initialize()
            store.recover()
            transcriptions.initialize()
            transcriptions.recover()
            highlights.initialize()
            youtube.initialize()
            projects.initialize()
            quality_analysis.initialize()
            caption_styles.initialize()
            tiktok.initialize()
            youtube_publishing.initialize()
            instagram.initialize()
            x_publishing.initialize()
            facebook.initialize()
            scheduler.initialize()
            deletions.initialize()
            app.state.jobs = jobs
            app.state.transcriptions = transcriptions
            app.state.highlights = highlights
            app.state.projects = projects
            app.state.youtube = youtube
            app.state.tiktok = tiktok
            app.state.youtube_publishing = youtube_publishing
            app.state.instagram = instagram
            app.state.x_publishing = x_publishing
            app.state.facebook = facebook
            try:
                scheduler.start()
                yield
            finally:
                await run_in_threadpool(scheduler.close)
                previews.stop.set()
                quality_analysis.stop.set()
                try:
                    try:
                        try:
                            try:
                                try:
                                    await run_in_threadpool(facebook.close)
                                finally:
                                    try:
                                        await run_in_threadpool(x_publishing.close)
                                    finally:
                                        await run_in_threadpool(instagram.close)
                            finally:
                                await run_in_threadpool(youtube_publishing.close)
                        finally:
                            await run_in_threadpool(tiktok.close)
                    finally:
                        projects.stop.set()
                        await run_in_threadpool(youtube.close)
                finally:
                    try:
                        await run_in_threadpool(projects.close)
                    finally:
                        try:
                            await run_in_threadpool(highlights.close)
                        finally:
                            try:
                                await run_in_threadpool(transcriptions.close)
                            finally:
                                await run_in_threadpool(jobs.close)
        finally:
            instance.release()

    app = FastAPI(title="Cliptric local API", lifespan=lifespan)
    app.add_middleware(RequestBoundary, settings=settings)
    app.include_router(create_transcription_router(transcriptions))
    app.include_router(create_highlight_router(highlights))
    app.include_router(create_project_router(projects))
    app.include_router(create_prompt_router(prompt_settings, projects, highlights))
    app.include_router(create_workspace_router(projects))
    app.include_router(create_caption_style_router(caption_styles, projects))
    app.include_router(create_emphasis_router(transcriptions, settings))
    app.include_router(create_youtube_router(youtube))
    app.include_router(create_preview_router(previews))
    app.include_router(create_quality_router(quality_analysis))
    app.include_router(create_tiktok_router(tiktok))
    app.include_router(create_youtube_publishing_router(youtube_publishing))
    app.include_router(create_scheduling_router(scheduler))
    app.include_router(create_instagram_router(instagram))
    app.include_router(create_social_router(x_publishing))
    app.include_router(create_social_router(facebook))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Return field errors without echoing untrusted or non-JSON input values.
        errors = [
            {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse({"detail": errors}, status_code=422)

    def recording_or_404(recording_id: UUID) -> Recording:
        recording = store.recording(str(recording_id))
        if recording is None:
            raise HTTPException(404, "Recording not found.")
        return recording

    def clip_or_404(clip_id: UUID) -> Clip:
        clip = store.clip(str(clip_id))
        if clip is None:
            raise HTTPException(404, "Export not found.")
        return clip

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "max_upload_bytes": settings.max_upload_bytes,
            "max_clip_seconds": settings.max_clip_seconds,
        }

    @app.get("/api/exports/active", response_model=Clip | None)
    def active_export():
        return app.state.jobs.current()

    @app.get("/api/recordings", response_model=list[Recording])
    def recordings(
        limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0)
    ):
        return store.recordings(limit, offset)

    @app.post("/api/recordings", response_model=Recording, status_code=201)
    async def import_recording(
        request: Request, filename: str = Query(min_length=1, max_length=240)
    ):
        if (
            request.headers.get("content-type", "").split(";")[0].strip().lower()
            != "application/octet-stream"
        ):
            raise HTTPException(415, "Import requires an application/octet-stream video body.")
        name = filename.replace("\\", "/").split("/")[-1]
        if not name or any(ord(char) < 32 for char in name):
            raise HTTPException(400, "Invalid filename.")
        extension = Path(name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(415, "Use an MP4, MOV, MKV, or WebM video.")
        length = request.headers.get("content-length")
        expected_size = None
        if length is not None:
            try:
                expected_size = int(length)
            except ValueError as exc:
                raise HTTPException(400, "Invalid upload size.") from exc
            if expected_size <= 0 or expected_size > settings.max_upload_bytes:
                raise HTTPException(
                    413,
                    f"Recording must be non-empty and no larger than {settings.max_upload_bytes} bytes.",
                )
        if upload_lock.locked():
            raise HTTPException(409, "Another recording is importing. Wait for it to finish.")
        async with upload_lock:
            recording_id = str(uuid4())
            temporary = store.root / "sources" / f"{recording_id}.upload"
            destination = store.root / "sources" / f"{recording_id}{extension}"
            saved = False
            try:
                await run_in_threadpool(
                    require_space, store.root, settings.min_free_bytes, expected_size or 0
                )
                size = 0
                next_space_check = 0
                async with asyncio.timeout(settings.upload_timeout_seconds):
                    async with await anyio.open_file(temporary, "xb") as output:
                        chunks = request.stream().__aiter__()
                        while True:
                            try:
                                chunk = await asyncio.wait_for(
                                    anext(chunks), settings.upload_idle_seconds
                                )
                            except StopAsyncIteration:
                                break
                            size += len(chunk)
                            if size > settings.max_upload_bytes:
                                raise HTTPException(
                                    413,
                                    f"Recording exceeds the {settings.max_upload_bytes}-byte import limit.",
                                )
                            if size >= next_space_check:
                                await run_in_threadpool(
                                    require_space, store.root, settings.min_free_bytes, len(chunk)
                                )
                                next_space_check = size + 16 * 1024**2
                            await output.write(chunk)
                if not size:
                    raise HTTPException(400, "Recording is empty.")
                if expected_size is not None and size != expected_size:
                    raise HTTPException(
                        400, "Import is incomplete. Select the file again to retry."
                    )
                info = await run_in_threadpool(media.probe, temporary)
                temporary.replace(destination)
                recording = Recording(
                    id=recording_id,
                    name=name,
                    duration=info.duration,
                    width=info.width,
                    height=info.height,
                    has_audio=info.has_audio,
                    size_bytes=size,
                    created_at=now(),
                )
                await run_in_threadpool(store.save_recording, recording, extension)
                saved = True
                return recording
            except MediaError as exc:
                raise HTTPException(422, str(exc)) from exc
            except ClientDisconnect as exc:
                raise HTTPException(
                    400, "Import interrupted. Select the file again to retry."
                ) from exc
            except TimeoutError as exc:
                raise HTTPException(
                    408, "Import timed out. Select the file again to retry."
                ) from exc
            except OSError as exc:
                logger.exception("Local import storage failure")
                if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
                    raise HTTPException(
                        507, "Not enough free disk space. Free space and retry."
                    ) from exc
                raise HTTPException(
                    503, "Cannot write to local storage. Check the server log and retry."
                ) from exc
            finally:
                remove_artifact(temporary)
                if not saved:
                    remove_artifact(destination)

    @app.get("/api/recordings/{recording_id}/media")
    def recording_media(recording_id: UUID):
        recording_or_404(recording_id)
        path = store.source_path(str(recording_id))
        if not path.is_file():
            raise HTTPException(404, "Source media is missing from local storage.")
        return FileResponse(path)

    @app.get("/api/recordings/{recording_id}/clips", response_model=list[Clip])
    def clips(
        recording_id: UUID,
        limit: int = Query(default=100, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        recording_or_404(recording_id)
        return store.clips(str(recording_id), limit, offset)

    @app.post("/api/recordings/{recording_id}/clips", response_model=Clip, status_code=202)
    def export_clip(recording_id: UUID, payload: ExportRequest):
        recording = recording_or_404(recording_id)
        if payload.end > recording.duration + 0.05:
            raise HTTPException(422, "End exceeds the recording duration.")
        end = min(payload.end, recording.duration)
        if not 0.1 <= end - payload.start <= settings.max_clip_seconds:
            raise HTTPException(
                422, f"Choose an export between 0.1 and {settings.max_clip_seconds:g} seconds long."
            )
        validated = payload.model_copy(update={"end": end})
        words = None
        if validated.captions is not None:
            words = transcriptions.export_words(
                str(recording_id),
                str(validated.captions.transcription_id),
                validated.captions.revision,
                validated.start,
                validated.end,
            )
            try:
                if not build_cues(words, validated.start, validated.end):
                    raise HTTPException(
                        422,
                        "No caption words overlap this selection. Export without captions or adjust the selection.",
                    )
            except CaptionError as exc:
                raise HTTPException(422, str(exc)) from exc
        try:
            return app.state.jobs.submit(str(recording_id), validated, words)
        except ExportBusy as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/recordings/{recording_id}/draft", response_model=SelectionDraft)
    def get_draft(recording_id: UUID):
        return store.draft(recording_or_404(recording_id))

    @app.put("/api/recordings/{recording_id}/draft", response_model=SelectionDraft)
    def save_draft(recording_id: UUID, payload: SelectionDraft):
        recording = recording_or_404(recording_id)
        if payload.end > recording.duration:
            raise HTTPException(422, "End exceeds the recording duration.")
        if payload.end - payload.start < 0.1:
            raise HTTPException(422, "Choose a clip at least 0.1 seconds long.")
        try:
            return store.save_draft(str(recording_id), payload)
        except DraftConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/clips/{clip_id}", response_model=Clip)
    def clip_status(clip_id: UUID):
        return clip_or_404(clip_id)

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: UUID):
        return deletions.delete(project_id=str(project_id))

    @app.delete("/api/projects/{project_id}/clips/{item_id}")
    def delete_project_clip(project_id: UUID, item_id: UUID, revision: int = Query(ge=0)):
        return deletions.delete(project_id=str(project_id), item_id=str(item_id), revision=revision)

    @app.delete("/api/clips/{clip_id}")
    def delete_clip(clip_id: UUID):
        return deletions.delete(clip_id=str(clip_id))

    @app.patch("/api/clips/{clip_id}/posted", response_model=PostedUpdate)
    def set_export_posted(clip_id: UUID, request: PostedUpdate):
        projects.set_export_posted(str(clip_id), request.posted)
        return request

    @app.post("/api/clips/{clip_id}/cancel", response_model=Clip)
    def cancel_clip(clip_id: UUID):
        clip_or_404(clip_id)
        try:
            app.state.jobs.cancel(str(clip_id))
        except ExportBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        return clip_or_404(clip_id)

    @app.get("/api/clips/{clip_id}/media")
    def clip_media(clip_id: UUID, download: bool = False):
        clip = clip_or_404(clip_id)
        path = store.export_path(str(clip_id))
        if clip.status != "succeeded" or not path.is_file():
            raise HTTPException(409, "This export is not ready to play or download.")
        return FileResponse(
            path, media_type="video/mp4", filename=f"clip-{clip_id}.mp4" if download else None
        )

    return app
