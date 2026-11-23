import asyncio
import secrets
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings

LOOPBACK_HOSTS = {"localhost", "127.0.0.1"}
OAUTH_CALLBACKS = {
    "/api/tiktok/callback",
    "/api/youtube-publishing/callback",
    "/api/instagram/callback",
    "/api/x/callback",
    "/api/facebook/callback",
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
            await JSONResponse({"detail": "Request host is not allowed."}, status_code=400)(
                scope, receive, send
            )
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
                    {"detail": "The private Cliptric gateway is required."}, status_code=403
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
