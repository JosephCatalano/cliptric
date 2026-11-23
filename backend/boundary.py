import asyncio
import logging
import secrets
from pathlib import Path
from traceback import extract_tb
from urllib.parse import urlsplit
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings

logger = logging.getLogger(__name__)

LOOPBACK_HOSTS = {"localhost", "127.0.0.1"}
OAUTH_CALLBACKS = {
    "/api/tiktok/callback",
    "/api/youtube-publishing/callback",
    "/api/instagram/callback",
    "/api/x/callback",
    "/api/facebook/callback",
    "/api/twitch-discovery/callback",
}


def hostname(authority: str) -> str | None:
    try:
        if not authority or any(char.isspace() for char in authority):
            return None
        address = urlsplit("//" + authority)
        _ = address.port
        if address.path or address.query or address.fragment or address.username is not None:
            return None
        return address.hostname
    except ValueError:
        return None


class RequestBoundary:
    """Reject foreign browser origins and bound small control requests before parsing."""

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings
        self.public_host = urlsplit(settings.public_origin).hostname

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
        forwarded_host = request.headers.get("x-forwarded-host")
        hosts = {hostname(request.headers.get("host", ""))}
        if forwarded_host is not None:
            hosts.add(hostname(forwarded_host))
        accepted_hosts = LOOPBACK_HOSTS | ({self.public_host} if self.public_host else set())
        if not hosts <= accepted_hosts:
            await JSONResponse(
                {
                    "detail": "This address is not configured for Cliptric. Open your usual Cliptric workspace address."
                },
                status_code=400,
            )(scope, receive, send)
            return
        hosted = bool(self.public_host and self.public_host in hosts)
        request.state.hosted = hosted
        if hosted:
            peer = scope.get("client")
            token = request.headers.get("x-cliptric-gateway", "")
            if (
                not peer
                or peer[0] not in {"127.0.0.1", "::1"}
                or not token.isascii()
                or not secrets.compare_digest(token, self.settings.gateway_token)
            ):
                await JSONResponse(
                    {
                        "detail": "The private Cliptric gateway is required. Open the workspace through its HTTPS address. If it still fails, check that the gateway is running with matching configuration."
                    },
                    status_code=403,
                )(scope, receive, send)
                return
            allowed = {self.settings.public_origin}
        # OAuth's top-level redirect is cross-site by design. Only this GET is
        # exempt; its handler requires a browser-bound, expiring, single-use state.
        oauth_callback = request.method == "GET" and request.url.path in OAUTH_CALLBACKS
        if not oauth_callback and (
            (origin and origin not in allowed)
            or request.headers.get("sec-fetch-site") == "cross-site"
            or (
                hosted
                and request.method not in {"GET", "HEAD", "OPTIONS"}
                and origin not in allowed
            )
        ):
            await JSONResponse(
                {
                    "detail": "Cliptric blocked a request from another site or tab context. Open the workspace directly in a new tab and retry the action there."
                },
                status_code=403,
            )(scope, receive, send)
            return

        # Import bodies are streamed directly to disk by the endpoint. JSON control
        # requests are small and must be bounded before FastAPI buffers them.
        if request.method in {"POST", "PUT", "PATCH"} and request.url.path != "/api/recordings":
            body_limit = (
                2 * 1024 * 1024
                if request.url.path
                in {"/api/youtube-publishing/posts/batch", "/api/schedule/batch"}
                else 16 * 1024
            )
            body = bytearray()
            try:
                async with asyncio.timeout(10):
                    while True:
                        chunk = await receive()
                        if chunk["type"] == "http.disconnect":
                            return
                        body.extend(chunk.get("body", b""))
                        if len(body) > body_limit:
                            await JSONResponse(
                                {
                                    "detail": f"The request exceeds {body_limit // 1024} KiB. Shorten the submitted text or select fewer items at once."
                                },
                                status_code=413,
                            )(scope, receive, send)
                            return
                        if not chunk.get("more_body", False):
                            break
            except TimeoutError:
                await JSONResponse(
                    {
                        "detail": "Cliptric timed out while receiving the request. Check your connection and submit it again."
                    },
                    status_code=408,
                )(scope, receive, send)
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

        response_started = False

        async def secure_headers(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-content-type-options", b"nosniff"),
                    (b"cache-control", b"no-store"),
                ]
            await send(message)

        try:
            await self.app(scope, receive, secure_headers)
        except Exception as exc:
            if response_started:
                raise
            reference = uuid4().hex[:12]
            # Log code locations only. Exception text can include local paths or credentials.
            frames = extract_tb(exc.__traceback__)
            locations = " > ".join(
                f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames
            )
            logger.error("API error %s (%s) at %s", reference, type(exc).__name__, locations)
            recovery = (
                "Reload the page. If it fails again, share the reference so the backend log can be checked."
                if request.method in {"GET", "HEAD"}
                else "Refresh the saved status before trying again. Share the reference if the error persists."
            )
            await JSONResponse(
                {
                    "detail": f"Cliptric encountered an internal error. {recovery} Error reference {reference}."
                },
                status_code=500,
            )(scope, receive, secure_headers)
