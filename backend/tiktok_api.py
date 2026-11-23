"""TikTok's OAuth and Direct Post transport. No automatic retries."""

import ipaddress
import json
import logging
import os
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx2
from dotenv import dotenv_values

from .config import ROOT
from .social_security import CredentialError, protect_secret

CALLBACK_PATH = "/api/tiktok/callback"
API_ORIGIN = "https://open.tiktokapis.com"
MAX_RESPONSE_BYTES = 64 * 1024


class TikTokError(ValueError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class TikTokSettings:
    client_key: str = ""
    client_secret: str = field(default="", repr=False)
    audited: bool = False

    @classmethod
    def load(cls):
        values = dotenv_values(ROOT / "backend" / ".env", interpolate=False)

        def get(name):
            return (os.environ.get(name, values.get(name)) or "").strip()

        audited = get("TIKTOK_AUDITED").lower()
        if audited not in {"", "true", "false"}:
            raise ValueError("TIKTOK_AUDITED must be true or false.")
        return cls(get("TIKTOK_CLIENT_KEY"), get("TIKTOK_CLIENT_SECRET"), audited == "true")


class SensitiveTikTokLogs(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if CALLBACK_PATH in message and "?" in message:
            record.msg = re.sub(r"(/api/tiktok/callback)\?[^\s\"]*", r"\1?[redacted]", message)
            record.args = ()
        elif "upload_token=" in message:
            record.msg, record.args = "TikTok upload request (signed URL redacted)", ()
        return True


def secure_tiktok_logs():
    for name in ("uvicorn.access", "httpx2"):
        target = logging.getLogger(name)
        if not any(isinstance(item, SensitiveTikTokLogs) for item in target.filters):
            target.addFilter(SensitiveTikTokLogs())


def protect_tokens(payload: bytes, *, decrypt: bool = False) -> bytes:
    try:
        return protect_secret(payload, decrypt=decrypt)
    except CredentialError as exc:
        raise TikTokError(str(exc), 503) from exc


def chunk_plan(size: int) -> tuple[int, list[tuple[int, int]]]:
    if not 0 < size <= 512 * 1024**2:
        raise TikTokError("Choose a ready export of at most 512 MiB.", 422)
    chunk = min(size, 16 * 1024**2)
    count = max(1, size // chunk)
    return chunk, [(i * chunk, size if i == count - 1 else (i + 1) * chunk) for i in range(count)]


class TikTokAPI:
    def __init__(self, settings: TikTokSettings, *, transport=None, resolver=socket.getaddrinfo):
        self.settings, self.transport, self.resolver = settings, transport, resolver

    def request(self, path: str, *, token: str | None = None, form=None, body=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            with httpx2.Client(
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=30,
            ) as client:
                with client.stream(
                    "POST",
                    API_ORIGIN + path,
                    headers=headers,
                    data=form,
                    json=body,
                ) as response:
                    if response.status_code != 200:
                        raise TikTokError(
                            "TikTok rejected the request. Check permissions, eligibility and rate limits.",
                            429 if response.status_code == 429 else 502,
                        )
                    payload = bytearray()
                    for part in response.iter_bytes(chunk_size=8192):
                        payload.extend(part)
                        if len(payload) > MAX_RESPONSE_BYTES:
                            raise TikTokError("TikTok returned an oversized response.")
            result = json.loads(payload or b"{}")
            if not isinstance(result, dict):
                raise TikTokError("TikTok returned an invalid response.")
            error = result.get("error")
            code = error.get("code") if isinstance(error, dict) else error
            if code is not None and not isinstance(code, str):
                raise TikTokError("TikTok returned an invalid error response.")
            if code and code != "ok":
                messages = {
                    "access_token_invalid": "TikTok access has expired or was revoked. Reconnect your account.",
                    "scope_not_authorized": "Authorize the video.publish permission before posting.",
                    "rate_limit_exceeded": "TikTok's request limit was reached. Try again later.",
                    "spam_risk_too_many_posts": "TikTok's posting limit was reached. Try again later.",
                    "unaudited_client_can_only_post_to_private_accounts": "This test app requires a private TikTok account and Only me visibility.",
                }
                raise TikTokError(
                    messages.get(
                        code,
                        "TikTok rejected this operation. Check the app's permissions and account eligibility.",
                    )
                )
            return result
        except (httpx2.HTTPError, json.JSONDecodeError, UnicodeError) as exc:
            raise TikTokError(
                "TikTok request failed. Check the saved post status before retrying."
            ) from exc

    def token(self, **fields):
        return self.request(
            "/v2/oauth/token/",
            form={
                "client_key": self.settings.client_key,
                "client_secret": self.settings.client_secret,
                **fields,
            },
        )

    def validate_upload_url(self, url: str):
        try:
            parts = urlsplit(url)
            valid = (
                parts.scheme == "https"
                and not parts.username
                and not parts.password
                and parts.port in {None, 443}
                and not parts.fragment
                and re.fullmatch(
                    r"(?:open-upload|upload\.[a-z0-9-]+)\.tiktokapis\.com", parts.hostname or ""
                )
                and parts.path == "/video/"
                and len(url) <= 2048
            )
            if not valid:
                raise ValueError("Invalid destination")
            addresses = self.resolver(parts.hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or any(
                not ipaddress.ip_address(item[4][0]).is_global for item in addresses
            ):
                raise ValueError("Non-public destination")
        except (ValueError, OSError) as exc:
            raise TikTokError("TikTok returned an unsupported upload destination.") from exc

    def upload_chunk(self, url, stream, start, end, total, *, final, stop):
        self.validate_upload_url(url)

        def content():
            remaining = end - start
            while remaining:
                if stop.is_set():
                    raise TikTokError(
                        "Upload interrupted. Reconcile its TikTok status before retrying."
                    )
                part = stream.read(min(256 * 1024, remaining))
                if not part:
                    raise TikTokError("The exported video changed or became unreadable.")
                remaining -= len(part)
                yield part

        try:
            with httpx2.Client(
                transport=self.transport, trust_env=False, follow_redirects=False, timeout=60
            ) as client:
                with client.stream(
                    "PUT",
                    url,
                    content=content(),
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(end - start),
                        "Content-Range": f"bytes {start}-{end - 1}/{total}",
                    },
                ) as response:
                    if response.status_code != (201 if final else 206):
                        raise TikTokError(
                            "TikTok did not confirm this upload. Refresh its status before retrying."
                        )
        except httpx2.HTTPError as exc:
            raise TikTokError(
                "Upload connection failed. Its outcome must be checked before retrying."
            ) from exc
