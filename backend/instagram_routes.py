from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .instagram import ReelJob, ReelRequest
from .instagram_api import CALLBACK_PATH, InstagramError


def create_instagram_router(service):
    router = APIRouter(prefix="/api/instagram")

    def call(operation, *args):
        try:
            return operation(*args)
        except InstagramError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    @router.get("")
    def status():
        return service.status()

    @router.post("/connect")
    def connect(request: Request):
        try:
            state, url = service.begin(request.headers.get("origin", service.web_origin))
            response = JSONResponse({"url": url})
            response.set_cookie(
                "instagram_oauth_state",
                state,
                max_age=600,
                httponly=True,
                samesite="lax",
                path=CALLBACK_PATH,
            )
            return response
        except InstagramError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    @router.get("/callback")
    def callback(
        request: Request,
        state: str = Query(default="", max_length=128),
        code: str = Query(default="", max_length=4096),
        error: str = Query(default="", max_length=128),
    ):
        try:
            destination = service.finish(
                state, request.cookies.get("instagram_oauth_state"), code, bool(error)
            )
            response = RedirectResponse(destination, status_code=303)
        except InstagramError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
        response.delete_cookie("instagram_oauth_state", path=CALLBACK_PATH)
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @router.post("/disconnect")
    def disconnect():
        return call(service.disconnect)

    @router.get("/creator")
    def creator():
        return call(service.creator)

    @router.get("/exports/{export_id}")
    def export(export_id: UUID):
        return call(service.export_info, str(export_id))

    @router.get("/posts", response_model=list[ReelJob])
    def posts(export_id: UUID | None = None):
        return service.posts(str(export_id) if export_id else None)

    @router.post("/posts", response_model=ReelJob, status_code=202)
    def publish(payload: ReelRequest):
        return call(service.submit, payload)

    @router.post("/posts/{identifier}/refresh", response_model=ReelJob)
    def refresh(identifier: UUID):
        return call(service.refresh, str(identifier))

    return router
