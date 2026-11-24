"""Twitch Helix public discovery. App credentials never enter the browser."""

import os
import re
import threading
import time
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx2
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config import ROOT

MAX_IMPORTS_PER_SCAN = 20


class TwitchError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def clip_slug(value: str) -> str:
    # Extract the identifier only. The pasted URL is never a network destination.
    try:
        parts = urlsplit(value.strip())
        if (
            parts.scheme == "https"
            and not parts.username
            and not parts.password
            and parts.port in (None, 443)
        ):
            if parts.hostname == "clips.twitch.tv":
                match = re.fullmatch(r"/([A-Za-z0-9_-]{1,150})/?", parts.path)
                if match and match[1].lower() != "embed":
                    return match[1]
            elif parts.hostname in {"twitch.tv", "www.twitch.tv"}:
                match = re.fullmatch(
                    r"/[A-Za-z0-9_]{1,25}/clip/([A-Za-z0-9_-]{1,150})/?", parts.path
                )
                if match:
                    return match[1]
    except ValueError:
        pass
    raise TwitchError(
        422, "Paste a Twitch Clip share link, such as https://clips.twitch.tv/ClipName."
    )


def login(value: str) -> str:
    value = value.strip().lower()
    if "://" in value:
        if not re.fullmatch(r"https://(?:www\.)?twitch\.tv/[a-z0-9_]{1,25}/?", value):
            raise ValueError("Enter a Twitch username or HTTPS profile URL.")
        value = value.rstrip("/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"[a-z0-9_]{1,25}", value):
        raise ValueError("Enter a Twitch username or HTTPS profile URL.")
    return value


class ScanSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str = Field(min_length=1, max_length=100)
    source: Literal["streamers", "game"]
    streamers: list[str] = Field(default_factory=list, max_length=8)
    game_id: str = Field(default="", pattern=r"^\d{0,24}$")
    game_name: str = Field(default="", max_length=120)
    include: list[str] = Field(default_factory=list, max_length=8)
    exclude: list[str] = Field(default_factory=list, max_length=8)
    period: Literal["24h", "7d", "30d", "90d", "all", "custom"] = "24h"
    start: datetime | None = None
    end: datetime | None = None
    order: Literal["views", "recent", "momentum"] = "views"
    content_focus: Literal[
        "any", "gameplay", "roleplay", "funny", "fail", "chase", "glitch", "reaction"
    ] = "any"
    limit: int = Field(default=15, ge=1, le=100)
    minimum_views: int = Field(default=0, ge=0, le=2_147_483_647)
    minimum_duration: float = Field(default=0, ge=0, le=60)
    maximum_duration: float = Field(default=60, gt=0, le=60)
    languages: list[str] = Field(default_factory=list, max_length=12)
    enabled: bool = False
    interval_minutes: int = Field(default=60, ge=10, le=10080)
    auto_prepare: bool = Field(default=False, strict=True)
    import_limit: int = Field(default=15, ge=1, le=MAX_IMPORTS_PER_SCAN)
    daily_limit: int = Field(default=30, ge=1, le=100)
    daily_ai_limit: int = Field(default=10, ge=0, le=100)
    storage_limit_mib: int = Field(default=4096, ge=128, le=65536)
    preset_id: UUID | None = None
    ai_titles: bool = Field(default=False, strict=True)
    ai_copy: bool = Field(default=False, strict=True)
    ai_consent: bool = Field(default=False, strict=True)

    @field_validator("streamers", "include", "exclude")
    @classmethod
    def normalize_logins(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(login(value) for value in values))

    @field_validator("languages")
    @classmethod
    def language_codes(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[a-z]{2}|other", value) for value in values):
            raise ValueError("Use two-letter lowercase language codes.")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_scan(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Enter a scan name.")
        if (self.source == "streamers" and not self.streamers) or (
            self.source == "game" and not self.game_id
        ):
            raise ValueError("Choose at least one streamer or a game category.")
        if set(self.include) & set(self.exclude):
            raise ValueError("A broadcaster cannot be both included and excluded.")
        if self.minimum_duration > self.maximum_duration:
            raise ValueError("Minimum duration must not exceed maximum duration.")
        if (self.ai_titles or self.ai_copy) and not self.ai_consent:
            raise ValueError("Approve transcript sharing and possible charges to enable AI copy.")
        if self.period == "custom" and (
            not self.start
            or not self.end
            or self.start.utcoffset() is None
            or self.end.utcoffset() is None
            or self.start >= self.end
        ):
            raise ValueError("Choose a valid custom start and end with a time zone.")
        return self


class TwitchClip(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,150}$")
    broadcaster_id: str = Field(pattern=r"^[0-9]{1,24}$")
    broadcaster_name: str = Field(max_length=120)
    title: str = Field(max_length=250)
    thumbnail_url: str = Field(default="", max_length=2048)
    created_at: datetime
    duration: float = Field(gt=0, le=120)
    view_count: int = Field(ge=0)
    language: str = Field(max_length=12)
    video_id: str = Field(default="", pattern=r"^\d{0,24}$")
    vod_offset: int | None = Field(default=None, ge=0)

    @field_validator("created_at")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Twitch creation time must have a time zone.")
        return value.astimezone(UTC)

    @field_validator("thumbnail_url")
    @classmethod
    def thumbnail(cls, value: str) -> str:
        try:
            parts = urlsplit(value)
            host = parts.hostname or ""
            if (
                parts.scheme == "https"
                and not parts.username
                and not parts.password
                and parts.port in (None, 443)
                and host.endswith((".twitchcdn.net", ".ttvnw.net", ".jtvnw.net"))
            ):
                return value
        except ValueError:
            pass
        return ""


class TwitchHelix:
    def __init__(self):
        values = dotenv_values(ROOT / "backend" / ".env.twitch", interpolate=False)
        self.client_id = os.environ.get("TWITCH_CLIENT_ID", values.get("TWITCH_CLIENT_ID") or "")
        self.secret = os.environ.get(
            "TWITCH_CLIENT_SECRET", values.get("TWITCH_CLIENT_SECRET") or ""
        )
        self.lock = threading.Lock()
        self.token = ""
        self.expires = 0.0
        self.retry_after = 0.0
        self.client = httpx2.Client(timeout=5, trust_env=False, follow_redirects=False)

    @property
    def configured(self):
        return bool(self.client_id and self.secret)

    def _json(self, method, url, **kwargs):
        try:
            with self.client.stream(method, url, **kwargs) as response:
                if response.status_code == 429:
                    reset = response.headers.get("Ratelimit-Reset", "")
                    delay = (
                        int(reset) - time.time() if reset.isdecimal() and len(reset) < 13 else 60
                    )
                    self.retry_after = time.monotonic() + max(1, min(3600, delay))
                    raise TwitchError(429, "Twitch rate limit reached. Try again after it resets.")
                if response.status_code == 401:
                    raise TwitchError(401, "Twitch rejected the app credentials.")
                if response.status_code == 400 and url == "https://id.twitch.tv/oauth2/token":
                    raise TwitchError(400, "Twitch rejected the token grant.")
                if response.status_code == 403:
                    raise TwitchError(403, "Twitch did not authorize this request.")
                if response.status_code != 200:
                    raise TwitchError(502, "Twitch did not complete the request. Try again later.")
                from json import loads

                data = bytearray()
                deadline = time.monotonic() + 8
                for chunk in response.iter_bytes(chunk_size=4096):
                    if len(data) + len(chunk) > 1024**2 or time.monotonic() > deadline:
                        raise TwitchError(502, "Twitch returned an oversized or slow response.")
                    data.extend(chunk)
                result = loads(data)
                if not isinstance(result, dict):
                    raise ValueError("Expected a JSON object.")
                return result
        except (httpx2.HTTPError, ValueError) as exc:
            raise TwitchError(502, "Could not read the Twitch response.") from exc

    def get(self, endpoint, params):
        with self.lock:
            if not self.configured:
                raise TwitchError(503, "Configure Twitch app credentials in the backend.")
            if time.monotonic() < self.retry_after:
                raise TwitchError(429, "Twitch rate limit reached. Try again after it resets.")
            for attempt in range(2):
                if time.monotonic() >= self.expires:
                    data = self._json(
                        "POST",
                        "https://id.twitch.tv/oauth2/token",
                        data={
                            "client_id": self.client_id,
                            "client_secret": self.secret,
                            "grant_type": "client_credentials",
                        },
                    )
                    token, expires = data.get("access_token"), data.get("expires_in")
                    if not isinstance(token, str) or not token or not isinstance(expires, int):
                        raise TwitchError(502, "Twitch returned an invalid app token.")
                    identity = self._json(
                        "GET",
                        "https://id.twitch.tv/oauth2/validate",
                        headers={"Authorization": f"OAuth {token}"},
                    )
                    if identity.get("client_id") != self.client_id or identity.get("user_id"):
                        raise TwitchError(502, "Twitch returned a token for a different identity.")
                    self.token, self.expires = (
                        token,
                        time.monotonic() + max(0, min(3600, expires - 60)),
                    )
                try:
                    return self._json(
                        "GET",
                        f"https://api.twitch.tv/helix/{endpoint}",
                        params=params,
                        headers={
                            "Client-Id": self.client_id,
                            "Authorization": f"Bearer {self.token}",
                        },
                    )
                except TwitchError as exc:
                    if exc.status != 401 or attempt:
                        raise
                    self.expires = 0

    @staticmethod
    def rows(body):
        rows = body.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise TwitchError(502, "Twitch returned an invalid result.")
        return rows

    def look_up(self, kind, query):
        if kind == "streamer":
            try:
                username = login(query)
            except ValueError as exc:
                raise TwitchError(422, str(exc)) from exc
            body = self.get("users", {"login": username})
        else:
            body = self.get("search/categories", {"query": query, "first": 12})
        return [
            {
                "id": row["id"],
                "name": row.get("display_name") or row.get("name") or row["id"],
                "login": row.get("login"),
            }
            for row in self.rows(body)
            if isinstance(row.get("id"), str) and re.fullmatch(r"[0-9]{1,24}", row["id"])
        ]

    def broadcaster_ids(self, names):
        if not names:
            return {}
        rows = self.rows(self.get("users", [("login", name) for name in names]))
        ids = {
            row["login"]: row["id"]
            for row in rows
            if row.get("login") in names
            and isinstance(row.get("id"), str)
            and re.fullmatch(r"[0-9]{1,24}", row["id"])
        }
        missing = [name for name in names if name not in ids]
        if missing:
            raise TwitchError(422, f"Twitch could not find: {', '.join(missing)}.")
        return ids

    def page(self, params):
        body = self.get("clips", params)
        clips, ignored = [], 0
        for row in self.rows(body):
            try:
                clips.append(TwitchClip.model_validate(row))
            except ValidationError:
                ignored += 1
        paging = body.get("pagination") or {}
        cursor = paging.get("cursor") if isinstance(paging, dict) else None
        return clips, (cursor if isinstance(cursor, str) and len(cursor) < 4096 else None), ignored

    def clip(self, slug):
        clips, _, ignored = self.page({"id": slug})
        item = next((clip for clip in clips if clip.id == slug), None)
        if not item:
            if ignored:
                raise TwitchError(502, "Twitch returned invalid clip information.")
            raise TwitchError(404, "That Twitch clip is unavailable. Check the share link.")
        return item
