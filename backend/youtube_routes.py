from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .youtube_api import CALLBACK_PATH, YouTubeError
from .youtube_publishing import UploadJob, UploadRequest


def create_youtube_publishing_router(service):
    router = APIRouter(prefix="/api/youtube-publishing")

    def call(operation, *args):
        try:
            return operation(*args)
        except YouTubeError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    @router.get("")
    def status():
        return service.status()

    @router.post("/connect")
    def connect(request: Request):
        try:
            state, url = service.begin(request.headers.get("origin", service.web_origin))
        except YouTubeError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
        response = JSONResponse({"url": url})
        response.set_cookie(
            "youtube_oauth_state",
            state,
            max_age=600,
            httponly=True,
            samesite="lax",
            path=CALLBACK_PATH,
        )
        return response

    @router.get("/callback")
    def callback(
        request: Request,
        state: str = Query(default="", max_length=128),
        code: str = Query(default="", max_length=2048),
        error: str = Query(default="", max_length=128),
    ):
        try:
            destination = service.finish(
                state, request.cookies.get("youtube_oauth_state"), code, bool(error)
            )
            response = RedirectResponse(destination, status_code=303)
        except YouTubeError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
        response.delete_cookie("youtube_oauth_state", path=CALLBACK_PATH)
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @router.post("/disconnect")
    def disconnect(account_id: str | None = Query(None, pattern=r"^[0-9a-f]{64}$")):
        return call(service.disconnect, account_id)

    @router.get("/creator")
    def creator(account_id: str | None = Query(None, pattern=r"^[0-9a-f]{64}$")):
        return call(service.creator, account_id)

    @router.get("/exports/{export_id}")
    def export(export_id: UUID):
        return call(service.export_details, str(export_id))

    @router.get("/posts", response_model=list[UploadJob])
    def posts(export_id: UUID | None = None):
        return service.posts(str(export_id) if export_id else None)

    @router.post("/posts", response_model=UploadJob, status_code=202)
    def publish(payload: UploadRequest):
        return call(service.submit, payload)

    @router.post("/posts/{identifier}/refresh", response_model=UploadJob)
    def refresh(identifier: UUID):
        return call(service.refresh, str(identifier))

    @router.post("/posts/{identifier}/resume", response_model=UploadJob, status_code=202)
    def resume(identifier: UUID):
        return call(service.resume, str(identifier))

    return router
