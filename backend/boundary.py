import asyncio
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings


class RequestBoundary:
    """Reject foreign browser origins and bound small control requests before parsing."""

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        origin = request.headers.get("origin")
        allowed = {
            self.settings.web_origin,
            self.settings.web_origin.replace("127.0.0.1", "localhost"),
            "http://localhost:8765",
            "http://127.0.0.1:8765",
        }
        if request.url.hostname not in {"localhost", "127.0.0.1"}:
            await JSONResponse({"detail": "Only local requests are allowed."}, status_code=400)(
                scope, receive, send
            )
            return
        forwarded_host = request.headers.get("x-forwarded-host")
        if forwarded_host:
            try:
                forwarded = urlsplit("//" + forwarded_host)
                valid_host = forwarded.hostname in {"localhost", "127.0.0.1"} and not (
                    forwarded.path or forwarded.query or forwarded.fragment or forwarded.username
                )
            except ValueError:
                valid_host = False
            if not valid_host:
                await JSONResponse(
                    {"detail": "Only local forwarded hosts are allowed."}, status_code=400
                )(scope, receive, send)
                return
        # OAuth's top-level redirect is cross-site by design. Only this GET is
        # exempt; its handler requires a browser-bound, expiring, single-use state.
        oauth_callback = request.method == "GET" and request.url.path in {
            "/api/tiktok/callback",
            "/api/youtube-publishing/callback",
            "/api/instagram/callback",
            "/api/x/callback",
            "/api/facebook/callback",
        }
        if not oauth_callback and (
            (origin and origin not in allowed)
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            await JSONResponse(
                {"detail": "Cross-origin requests are not allowed."}, status_code=403
            )(scope, receive, send)
            return

        # Import bodies are streamed directly to disk by the endpoint. JSON control
        # requests are small and must be bounded before FastAPI buffers them.
        if request.method in {"POST", "PUT", "PATCH"} and request.url.path != "/api/recordings":
            body = bytearray()
            try:
                async with asyncio.timeout(10):
                    while True:
                        chunk = await receive()
                        if chunk["type"] == "http.disconnect":
                            return
                        body.extend(chunk.get("body", b""))
                        if len(body) > 16 * 1024:
                            await JSONResponse(
                                {"detail": "Request body exceeds 16 KiB."}, status_code=413
                            )(scope, receive, send)
                            return
                        if not chunk.get("more_body", False):
                            break
            except TimeoutError:
                await JSONResponse({"detail": "Request timed out."}, status_code=408)(
                    scope, receive, send
                )
                return
            original_receive = receive
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()

            receive = replay

        async def secure_headers(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-content-type-options", b"nosniff"),
                    (b"cache-control", b"no-store"),
                ]
            await send(message)

        await self.app(scope, receive, secure_headers)
