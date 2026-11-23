"""Twitch editor OAuth and the documented authorized clip download API."""

import os
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit

from pydantic import BaseModel, Field, ValidationError

from .social_security import CredentialError, protect_secret
from .twitch_helix import TwitchError

CALLBACK = "/api/twitch-discovery/callback"
SCOPES = {"editor:manage:clips", "channel:manage:clips"}
TOKEN_URL = "https://id.twitch.tv/oauth2/token"


class EditorToken(BaseModel):
    editor_id: str = Field(pattern=r"^[0-9]{1,24}$")
    login: str = Field(min_length=1, max_length=25)
    access: str = Field(min_length=1, max_length=4096, repr=False)
    refresh: str = Field(min_length=1, max_length=4096, repr=False)


class TwitchEditor:
    def __init__(self, store, api, settings):
        self.store, self.api, self.settings = store, api, settings
        self.lock = threading.Lock()
        self.pending = {}
        self.next_check = 0.0

    def initialize(self):
        with self.store.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS twitch_editor (slot INTEGER PRIMARY KEY CHECK(slot=1), "
                "editor_id TEXT NOT NULL, login TEXT NOT NULL, credential BLOB NOT NULL, "
                "validated REAL NOT NULL DEFAULT 0)"
            )
            if "validated" not in {
                row["name"] for row in db.execute("PRAGMA table_info(twitch_editor)")
            }:
                db.execute("ALTER TABLE twitch_editor ADD COLUMN validated REAL NOT NULL DEFAULT 0")
            db.execute("UPDATE twitch_editor SET validated=0")

    def status(self):
        with self.store.connection() as db:
            profile = db.execute(
                "SELECT editor_id,login FROM twitch_editor WHERE slot=1"
            ).fetchone()
        return {
            "available": self.api.configured and os.name == "nt",
            "profile": dict(profile) if profile else None,
        }

    def begin(self, origin):
        if not self.status()["available"]:
            raise TwitchError(503, "Twitch editor connection requires app credentials and Windows.")
        hosted = origin and origin == self.settings.public_origin
        if not hosted and origin not in {
            self.settings.web_origin,
            self.settings.web_origin.replace("127.0.0.1", "localhost"),
        }:
            raise TwitchError(403, "Start the connection from your Cliptric workspace.")
        redirect = (origin if hosted else f"http://{urlsplit(origin).hostname}:8765") + CALLBACK
        with self.lock:
            stamp = time.monotonic()
            self.pending = {key: item for key, item in self.pending.items() if item[0] > stamp}
            if len(self.pending) >= 4:
                raise TwitchError(429, "Wait for a pending Twitch connection to expire.")
            state = secrets.token_urlsafe(32)
            self.pending[state] = (stamp + 600, origin, redirect)
        return state, "https://id.twitch.tv/oauth2/authorize?" + urlencode(
            {
                "client_id": self.api.client_id,
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": " ".join(sorted(SCOPES)),
                "state": state,
            }
        )

    def finish(self, state, cookie, code):
        if (
            not state.isascii()
            or not cookie
            or not cookie.isascii()
            or not secrets.compare_digest(state, cookie)
        ):
            raise TwitchError(400, "Twitch connection could not be verified. Start again.")
        with self.lock:
            pending = self.pending.pop(state, None)
            if not pending or pending[0] <= time.monotonic():
                raise TwitchError(400, "This Twitch connection expired or was already used.")
            if not code:
                return pending[1] + "/discover?twitch=cancelled"
            response = self.exchange(
                {"grant_type": "authorization_code", "code": code, "redirect_uri": pending[2]}
            )
            self.save(self.validate(response))
        return pending[1] + "/discover?twitch=connected"

    def exchange(self, data):
        return self.api._json(
            "POST",
            TOKEN_URL,
            data={
                **data,
                "client_id": self.api.client_id,
                "client_secret": self.api.secret,
            },
        )

    def validate(self, response):
        access = response.get("access_token")
        if not isinstance(access, str) or not 1 <= len(access) <= 4096:
            raise TwitchError(502, "Twitch returned invalid editor credentials.")
        identity = self.api._json(
            "GET",
            "https://id.twitch.tv/oauth2/validate",
            headers={"Authorization": f"OAuth {access}"},
        )
        scopes = identity.get("scopes")
        if (
            identity.get("client_id") != self.api.client_id
            or not isinstance(scopes, list)
            or not any(scope in scopes for scope in SCOPES)
        ):
            raise TwitchError(403, "Twitch did not grant a clip-management scope for this app.")
        try:
            return EditorToken(
                editor_id=identity.get("user_id"),
                login=identity.get("login"),
                access=access,
                refresh=response.get("refresh_token"),
            )
        except ValidationError:
            raise TwitchError(502, "Twitch returned invalid editor credentials.") from None

    def save(self, token):
        try:
            encrypted = protect_secret(token.model_dump_json().encode())
        except CredentialError as exc:
            raise TwitchError(
                503, "Cliptric could not encrypt the Twitch editor credentials."
            ) from exc
        with self.store.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO twitch_editor VALUES(1,?,?,?,?)",
                (token.editor_id, token.login, encrypted, time.time()),
            )

    def validate_due(self):
        if not self.api.configured or time.monotonic() < self.next_check:
            return
        self.next_check = time.monotonic() + 60
        with self.lock:
            with self.store.connection() as db:
                row = db.execute(
                    "SELECT * FROM twitch_editor WHERE slot=1 AND validated<=?",
                    (time.time() - 3550,),
                ).fetchone()
            if not row:
                return
            try:
                token = EditorToken.model_validate_json(
                    protect_secret(row["credential"], decrypt=True)
                )
                try:
                    refreshed = self.validate(
                        {"access_token": token.access, "refresh_token": token.refresh}
                    )
                except TwitchError as exc:
                    if exc.status != 401:
                        raise
                    refreshed = self.validate(
                        self.exchange(
                            {"grant_type": "refresh_token", "refresh_token": token.refresh}
                        )
                    )
                if refreshed.editor_id != row["editor_id"]:
                    raise TwitchError(403, "Twitch editor identity changed.")
                self.save(refreshed)
                self.next_check = time.monotonic() + 3500
            except CredentialError, ValidationError:
                self.clear()
            except TwitchError as exc:
                if exc.status in {400, 401, 403}:
                    self.clear()

    def clear(self):
        with self.store.connection() as db:
            db.execute("DELETE FROM twitch_editor WHERE slot=1")

    def disconnect(self):
        with self.lock:
            self.pending.clear()
            self.clear()

    def downloads(self, editor_id, broadcaster, clip_ids):
        with self.lock:
            with self.store.connection() as db:
                row = db.execute(
                    "SELECT credential FROM twitch_editor WHERE slot=1 AND editor_id=?",
                    (editor_id,),
                ).fetchone()
            if not row:
                raise TwitchError(
                    403, "Reconnect the Twitch editor who authorized this broadcaster."
                )
            try:
                token = EditorToken.model_validate_json(protect_secret(row[0], decrypt=True))
            except CredentialError, ValidationError:
                raise TwitchError(
                    503, "Reconnect Twitch to repair its saved credentials."
                ) from None

            def request():
                return self.api._json(
                    "GET",
                    "https://api.twitch.tv/helix/clips/downloads",
                    params=[
                        ("editor_id", editor_id),
                        ("broadcaster_id", broadcaster),
                        *[("clip_id", clip_id) for clip_id in clip_ids],
                    ],
                    headers={
                        "Client-Id": self.api.client_id,
                        "Authorization": f"Bearer {token.access}",
                    },
                )

            try:
                result = request()
            except TwitchError as exc:
                if exc.status != 401:
                    raise
                token = self.validate(
                    self.exchange({"grant_type": "refresh_token", "refresh_token": token.refresh})
                )
                if token.editor_id != editor_id:
                    raise TwitchError(
                        403, "The refreshed Twitch identity changed. Reconnect Twitch."
                    )
                self.save(token)
                result = request()
            return {
                clip["clip_id"]: clip.get("landscape_download_url")
                or clip.get("portrait_download_url")
                for clip in self.api.rows(result)
                if clip.get("clip_id") in clip_ids
            }
