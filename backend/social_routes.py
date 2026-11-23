"""Local OAuth and publishing API for X and Facebook."""

from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .social_publishing import SocialJob, SocialRequest
from .social_transport import SocialError


def create_social_router(service):
    router = APIRouter(prefix=f"/api/{service.platform}")
    callback_path = f"/api/{service.platform}/callback"
    cookie_name = f"{service.platform}_oauth_state"

    def call(operation, *args):
        try:
            return operation(*args)
        except SocialError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    @router.get("")
    def status():
        return service.status()

    @router.post("/connect")
    def connect(request: Request, page_id: str | None = Query(None, pattern=r"^[0-9]{1,32}$")):
        try:
            state, url = service.begin(request.headers.get("origin", service.web_origin), page_id)
            response = JSONResponse({"url": url})
            response.set_cookie(
                cookie_name,
                state,
                max_age=600,
                httponly=True,
                samesite="lax",
                path=callback_path,
                secure=getattr(request.state, "hosted", False),
            )
            return response
        except SocialError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    @router.get("/callback")
    def callback(
        request: Request,
        state: str = Query("", max_length=128),
        code: str = Query("", max_length=4096),
        error: str = Query("", max_length=128),
    ):
        try:
            destination = service.finish(state, request.cookies.get(cookie_name), code, bool(error))
            response = RedirectResponse(destination, status_code=303)
        except SocialError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
        response.delete_cookie(
            cookie_name,
            path=callback_path,
            secure=getattr(request.state, "hosted", False),
            httponly=True,
        )
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
        return call(service.export_info, str(export_id))

    @router.get("/posts", response_model=list[SocialJob])
    def posts(export_id: UUID | None = None):
        return service.posts(str(export_id) if export_id else None)

    @router.post("/posts", response_model=SocialJob, status_code=202)
    def publish(payload: SocialRequest):
        return call(service.submit, payload)

    @router.post("/posts/{identifier}/refresh", response_model=SocialJob)
    def refresh(identifier: UUID):
        return call(service.refresh, str(identifier))

    return router
