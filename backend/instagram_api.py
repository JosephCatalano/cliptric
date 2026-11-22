"""Meta Facebook Login for Business and local Reels upload transport."""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

import httpx2
from dotenv import dotenv_values

from .config import ROOT

CALLBACK_PATH = "/api/instagram/callback"


class InstagramError(ValueError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def meta_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,32}", value):
        raise InstagramError("Instagram returned an invalid identifier.")
    return value


@dataclass(frozen=True)
class InstagramSettings:
    app_id: str = ""
    app_secret: str = field(default="", repr=False)
    login_config_id: str = ""
    page_id: str = ""
    version: str = "v27.0"

    def __post_init__(self):
        for value in (self.app_id, self.login_config_id, self.page_id):
            if value and not re.fullmatch(r"[0-9]{1,32}", value):
                raise ValueError("Instagram app, login configuration and Page IDs must be numeric.")
        if not re.fullmatch(r"v[0-9]{1,3}\.0", self.version):
            raise ValueError("INSTAGRAM_API_VERSION must have the form v27.0.")

    @classmethod
    def load(cls):
        values = dotenv_values(ROOT / "backend" / ".env", interpolate=False)

        def get(name, default=""):
            return (os.environ.get(name, values.get(name)) or default).strip()

        return cls(
            get("INSTAGRAM_APP_ID"),
            get("INSTAGRAM_APP_SECRET"),
            get("INSTAGRAM_LOGIN_CONFIG_ID"),
            get("INSTAGRAM_PAGE_ID"),
            get("INSTAGRAM_API_VERSION", "v27.0"),
        )


class SensitiveInstagramLogs(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if CALLBACK_PATH in message and "?" in message:
            record.msg = re.sub(r"(/api/instagram/callback)\?[^\s\"]*", r"\1?[redacted]", message)
            record.args = ()
        elif "graph.facebook.com" in message or "rupload.facebook.com" in message:
            record.msg, record.args = "Meta API request (credentials redacted)", ()
        return True


def secure_instagram_logs():
    for name in ("uvicorn.access", "httpx2", "httpcore"):
        target = logging.getLogger(name)
        if not any(isinstance(item, SensitiveInstagramLogs) for item in target.filters):
            target.addFilter(SensitiveInstagramLogs())


class InstagramAPI:
    def __init__(self, settings, *, transport=None):
        self.settings, self.transport = settings, transport

    def _send(self, method, url, *, headers=None, params=None, data=None, content=None, timeout=60):
        try:
            with httpx2.Client(
                transport=self.transport, trust_env=False, follow_redirects=False, timeout=timeout
            ) as client:
                with client.stream(
                    method, url, headers=headers, params=params, data=data, content=content
                ) as response:
                    if response.status_code not in {200, 201}:
                        raise InstagramError(
                            "Meta rejected the request. Check account access, app permissions and publishing limits.",
                            429 if response.status_code == 429 else 502,
                        )
                    payload = bytearray()
                    for part in response.iter_bytes(chunk_size=8192):
                        payload.extend(part)
                        if len(payload) > 128 * 1024:
                            raise InstagramError("Meta returned an oversized response.")
            result = json.loads(payload)
            if not isinstance(result, dict) or "error" in result:
                raise InstagramError("Meta returned an invalid or unsuccessful response.")
            return result
        except (httpx2.HTTPError, ValueError, UnicodeError) as exc:
            if isinstance(exc, InstagramError):
                raise
            raise InstagramError(
                "Instagram request failed. Check saved status before retrying."
            ) from exc

    def request(self, path, *, token=None, params=None, form=None, method="GET", timeout=60):
        # Destinations are constructed here, never taken from provider response URLs.
        if not re.fullmatch(
            r"(?:[0-9]{1,32}(?:/(?:media|media_publish))?|oauth/access_token|me/permissions)", path
        ):
            raise ValueError("Unsupported Meta endpoint")
        return self._send(
            method,
            f"https://graph.facebook.com/{self.settings.version}/{path}",
            headers={"Authorization": f"Bearer {token}"} if token else {},
            params=params,
            data=form,
            timeout=timeout,
        )

    def exchange(self, code, redirect):
        return self.request(
            "oauth/access_token",
            params={
                "client_id": self.settings.app_id,
                "client_secret": self.settings.app_secret,
                "redirect_uri": redirect,
                "code": code,
            },
        )

    def upload(self, container, token, stream, size, stop, progress):
        deadline = time.monotonic() + 1800

        def content():
            remaining = size
            reported = 0
            while remaining:
                if stop.is_set() or time.monotonic() >= deadline:
                    raise InstagramError("Instagram upload stopped or exceeded its time budget.")
                part = stream.read(min(256 * 1024, remaining))
                if not part:
                    raise InstagramError("The exported file became unreadable.")
                remaining -= len(part)
                yield part
                sent = size - remaining
                if sent - reported >= 4 * 1024**2 or not remaining:
                    progress(sent)
                    reported = sent

        result = self._send(
            "POST",
            f"https://rupload.facebook.com/ig-api-upload/{self.settings.version}/{meta_id(container)}",
            headers={
                "Authorization": f"OAuth {token}",
                "offset": "0",
                "file_size": str(size),
                "Content-Length": str(size),
                "Content-Type": "application/octet-stream",
            },
            content=content(),
        )
        if result.get("success") is not True:
            raise InstagramError("Meta did not confirm the video upload.")
