"""Fixed-destination Google OAuth and YouTube resumable-upload transport."""

import ipaddress
import json
import logging
import os
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit

import httpx2
from dotenv import dotenv_values

from .config import ROOT

CALLBACK_PATH = "/api/youtube-publishing/callback"
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)
API_ROOT = "https://www.googleapis.com/youtube/v3/"
UPLOAD_ROOT = "https://www.googleapis.com/upload/youtube/v3/videos"
MAX_RESPONSE_BYTES = 65536


class YouTubeError(ValueError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class YouTubeSettings:
    client_id: str = ""
    client_secret: str = field(default="", repr=False)

    @classmethod
    def load(cls):
        values = dotenv_values(ROOT / "backend" / ".env", interpolate=False)

        def get(name):
            return (os.environ.get(name, values.get(name)) or "").strip()

        return cls(get("YOUTUBE_CLIENT_ID"), get("YOUTUBE_CLIENT_SECRET"))


class SensitiveYouTubeLogs(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if CALLBACK_PATH in message and "?" in message:
            record.msg = re.sub(
                r"(/api/youtube-publishing/callback)\?[^\s\"]*", r"\1?[redacted]", message
            )
            record.args = ()
        elif "upload_id=" in message or "uploadType=resumable" in message:
            record.msg, record.args = "YouTube resumable request (session URL redacted)", ()
        return True


def secure_youtube_logs():
    for name in ("uvicorn.access", "httpx2"):
        target = logging.getLogger(name)
        if not any(isinstance(item, SensitiveYouTubeLogs) for item in target.filters):
            target.addFilter(SensitiveYouTubeLogs())


def response_json(response):
    body = bytearray()
    for chunk in response.iter_bytes(8192):
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise YouTubeError("Google returned an oversized response.")
    try:
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("Not an object")
        return value
    except (ValueError, UnicodeError) as exc:
        raise YouTubeError("Google returned an invalid response.") from exc


def provider_error(response):
    code = response.status_code
    if code == 401:
        return YouTubeError(
            "Google authorization is no longer valid. Disconnect and reconnect YouTube.", 409
        )
    if code in {400, 403, 429}:
        reasons = {
            "quotaExceeded": "The YouTube Data API quota was exceeded. Check the Google Cloud project’s quota before retrying.",
            "dailyLimitExceeded": "The Google Cloud project reached its daily YouTube API limit. Check its quota before retrying.",
            "uploadLimitExceeded": "The channel reached its YouTube upload limit. Check YouTube Studio before retrying.",
            "accessNotConfigured": "The Google Cloud project lacks YouTube Data API access. Enable YouTube Data API v3 and verify the configured OAuth client.",
            "insufficientPermissions": "The connected account did not grant the required YouTube access. Reconnect it and grant the permissions.",
            "invalidCategoryId": "YouTube rejected the video category. Choose a category available to the connected channel.",
        }
        try:
            error = response_json(response).get("error")
            entries = error.get("errors") if isinstance(error, dict) else None
            if isinstance(entries, list):
                for entry in entries:
                    reason = entry.get("reason") if isinstance(entry, dict) else None
                    if isinstance(reason, str) and reason in reasons:
                        return YouTubeError(f"{reasons[reason]} (HTTP {code})", code)
        except YouTubeError, httpx2.HTTPError:
            pass
    if code == 429:
        return YouTubeError(
            "Google rate-limited the request (HTTP 429). Check the saved upload status and wait before retrying.",
            429,
        )
    if code == 403:
        return YouTubeError(
            "Google refused the YouTube request (HTTP 403). Check the project’s API access and the channel’s permissions in Google Cloud and YouTube Studio.",
            403,
        )
    return YouTubeError(
        f"Google did not confirm the request (HTTP {code}). Check saved upload status before retrying."
    )


class YouTubeAPI:
    def __init__(self, settings, *, transport=None, resolver=socket.getaddrinfo):
        self.settings, self.transport, self.resolver = settings, transport, resolver

    def client(self, timeout=30):
        return httpx2.Client(
            transport=self.transport, trust_env=False, follow_redirects=False, timeout=timeout
        )

    def token(self, **form):
        form["client_id"] = self.settings.client_id
        if self.settings.client_secret:
            form["client_secret"] = self.settings.client_secret
        return self._json("POST", "https://oauth2.googleapis.com/token", data=form)

    def revoke(self, token):
        with self.client() as client:
            try:
                with client.stream(
                    "POST", "https://oauth2.googleapis.com/revoke", data={"token": token}
                ) as response:
                    if response.status_code != 200:
                        raise provider_error(response)
            except httpx2.HTTPError as exc:
                raise YouTubeError("Google revocation could not be confirmed.") from exc

    def get(self, resource, token, **params):
        if resource not in {"channels", "videos", "videoCategories"}:
            raise YouTubeError("Unsupported YouTube resource.")
        return self._json(
            "GET", API_ROOT + resource, headers={"Authorization": f"Bearer {token}"}, params=params
        )

    def _json(self, method, url, **kwargs):
        try:
            with self.client() as client:
                with client.stream(method, url, **kwargs) as response:
                    if response.status_code != 200:
                        raise provider_error(response)
                    return response_json(response)
        except httpx2.HTTPError as exc:
            raise YouTubeError(
                "Google connection failed. Check saved upload status before retrying."
            ) from exc

    def validate_session(self, url):
        try:
            parsed = urlsplit(url)
            valid = (
                len(url) <= 8192
                and parsed.scheme == "https"
                and parsed.hostname == "www.googleapis.com"
                and parsed.port in {None, 443}
                and not (parsed.username or parsed.password or parsed.fragment)
                and parsed.path == "/upload/youtube/v3/videos"
                and len(parse_qs(parsed.query).get("upload_id", [])) == 1
                and bool(parse_qs(parsed.query)["upload_id"][0])
                and not any(ord(char) <= 32 or ord(char) == 127 for char in url)
            )
            if not valid:
                raise ValueError("Invalid session")
            addresses = self.resolver(parsed.hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or any(
                not ipaddress.ip_address(item[4][0]).is_global for item in addresses
            ):
                raise ValueError("Non-public destination")
        except (ValueError, OSError, TypeError, KeyError) as exc:
            raise YouTubeError("Google returned an unsafe upload destination.") from exc

    def initiate(self, token, metadata, size, notify):
        try:
            with self.client() as client:
                with client.stream(
                    "POST",
                    UPLOAD_ROOT,
                    params={
                        "uploadType": "resumable",
                        "part": "snippet,status",
                        "notifySubscribers": str(notify).lower(),
                    },
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Upload-Content-Length": str(size),
                        "X-Upload-Content-Type": "video/mp4",
                    },
                    json=metadata,
                ) as response:
                    if response.status_code != 200:
                        raise provider_error(response)
                    url = response.headers.get("location", "")
                    self.validate_session(url)
                    return url
        except httpx2.HTTPError as exc:
            raise YouTubeError(
                "Upload initialization was not confirmed. No media was sent."
            ) from exc

    def transfer(self, url, token, total, *, stream=None, offset=0, stop=None):
        """An empty PUT reconciles; a streaming PUT sends only the confirmed remainder."""
        self.validate_session(url)
        if not 0 <= offset < total <= 512 * 1024**2:
            raise YouTubeError("Invalid resumable upload size.")
        deadline = time.monotonic() + 1800

        def content():
            remaining = total - offset
            while remaining:
                if stop.is_set() or time.monotonic() > deadline:
                    raise YouTubeError(
                        "Upload interrupted. Check its saved status before resuming."
                    )
                block = stream.read(min(256 * 1024, remaining))
                if not block:
                    raise YouTubeError("The exported video became unreadable.")
                remaining -= len(block)
                yield block

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Length": str(total - offset if stream else 0),
            "Content-Range": f"bytes {offset}-{total - 1}/{total}"
            if stream
            else f"bytes */{total}",
        }
        if stream:
            headers["Content-Type"] = "video/mp4"
        try:
            with self.client(60) as client:
                with client.stream(
                    "PUT", url, headers=headers, content=content() if stream else b""
                ) as response:
                    if response.status_code in {200, 201}:
                        return {"video": response_json(response)}
                    if response.status_code == 308:
                        accepted = response.headers.get("range")
                        match = re.fullmatch(r"bytes=0-(\d{1,12})", accepted or "")
                        if accepted and not match:
                            raise YouTubeError("Google returned an invalid upload offset.")
                        count = int(match[1]) + 1 if match else 0
                        if not 0 <= count < total:
                            raise YouTubeError("Google returned an invalid upload offset.")
                        retry = response.headers.get("retry-after", "0")
                        try:
                            delay = (
                                int(retry)
                                if retry.isdigit()
                                else max(
                                    0,
                                    (
                                        parsedate_to_datetime(retry) - datetime.now(timezone.utc)
                                    ).total_seconds(),
                                )
                            )
                            if not 0 <= delay <= 7 * 86400:
                                raise ValueError("Invalid delay")
                        except (ValueError, TypeError, OverflowError) as exc:
                            raise YouTubeError("Google returned an invalid resume delay.") from exc
                        return {"offset": count, "resume_after": time.time() + delay}
                    if response.status_code in {404, 410}:
                        raise YouTubeError(
                            "The upload session expired or is unavailable. Check YouTube Studio before creating another upload; the previous outcome is unknown.",
                            409,
                        )
                    raise provider_error(response)
        except httpx2.HTTPError as exc:
            raise YouTubeError(
                "Upload connection was interrupted. Check status, then resume the same upload."
            ) from exc
