"""Single-user Instagram connection and durable, consent-driven Reels publishing."""

import hashlib
import logging
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .instagram_api import CALLBACK_PATH, InstagramAPI, InstagramError, meta_id
from .media import MediaError
from .social_accounts import Accounts
from .social_security import CredentialError, protect_secret
from .storage import now
from .transcription_store import TranscriptStore

logger = logging.getLogger(__name__)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    access_token: str = Field(min_length=1, max_length=8192, repr=False)
    instagram_id: str = Field(pattern=r"^[0-9]{1,32}$")
    expires_at: float = Field(gt=0)


class ReelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    export_id: UUID
    account_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    caption: str = Field(max_length=2200)
    share_to_feed: bool = Field(default=False, strict=True)
    consent: Literal[True]

    @field_validator("consent", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Approve the exact video, account and Instagram settings first.")
        return value

    @field_validator("caption")
    @classmethod
    def valid_caption(cls, value):
        if any((ord(c) < 32 and c not in "\n\t") or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("Caption contains unsupported characters.")
        if len(value.encode("utf-16-le")) // 2 > 2200:
            raise ValueError("Caption exceeds 2200 characters.")
        return value


class ReelJob(BaseModel):
    id: str
    export_id: str
    account_id: str
    created_at: str
    request: ReelRequest
    status: Literal[
        "queued", "uploading", "processing", "publishing", "published", "failed", "unknown"
    ]
    total_bytes: int
    uploaded_bytes: int = 0
    sha256: str
    container_id: str | None = None
    media_id: str | None = None
    error: str | None = None


class Instagram:
    def __init__(
        self,
        store,
        media,
        settings,
        web_origin,
        *,
        public_origin="",
        api=None,
        protector=protect_secret,
    ):
        self.store, self.media, self.settings, self.web_origin = store, media, settings, web_origin
        self.api, self.protector = api or InstagramAPI(settings), protector
        self.public_origin = public_origin
        self.accounts = Accounts(store, "instagram_account", "config", InstagramError)
        self.lock, self.stop = threading.RLock(), threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="instagram")
        self.active, self.pending = None, {}

    def initialize(self):
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS instagram_account (
                    id INTEGER PRIMARY KEY CHECK(id=1), config TEXT NOT NULL,
                    account_id TEXT NOT NULL, credentials BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS instagram_posts (
                    id TEXT PRIMARY KEY, export_id TEXT NOT NULL, account_id TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS instagram_posts_export ON instagram_posts(export_id, created_at);
            """)
            for row in db.execute(
                "SELECT payload FROM instagram_posts WHERE status IN ('queued','uploading','processing','publishing')"
            ).fetchall():
                job = ReelJob.model_validate_json(row[0])
                job.status = "unknown" if job.status == "publishing" else "failed"
                job.error = "Local processing was interrupted. Check saved Instagram status before retrying."
                self._save(job, db)

        self.accounts.initialize()
        with self.store.connection() as db:
            db.execute(
                "UPDATE instagram_account SET config=? WHERE config=?",
                (self.config, f"{self.config}:{self.settings.page_id}"),
            )

    @property
    def config(self):
        return f"{self.settings.app_id}:{self.settings.login_config_id}"

    def status(self):
        with self.lock:
            accounts = self.accounts.list(self.config)
            return {
                "configured": bool(
                    self.settings.app_id
                    and self.settings.app_secret
                    and self.settings.login_config_id
                    and os.name == "nt"
                ),
                "connected": bool(accounts),
                "account_id": accounts[0]["account_id"] if len(accounts) == 1 else None,
                "accounts": accounts,
            }

    def _configured(self):
        if not self.status()["configured"]:
            raise InstagramError(
                "Configure the Meta app and linked Facebook Page in the application README first. Windows credential storage is required.",
                503,
            )

    def begin(self, origin, page_id=None):
        self._configured()
        hosted = bool(self.public_origin and origin == self.public_origin)
        if not hosted and origin not in {
            self.web_origin,
            self.web_origin.replace("127.0.0.1", "localhost"),
        }:
            raise InstagramError("Start the connection from the local app.", 403)
        page_id = page_id or self.settings.page_id
        if not page_id:
            raise InstagramError(
                "Enter the Facebook Page ID linked to this Instagram account.", 422
            )
        meta_id(page_id)
        callback_origin = origin if hosted else f"http://{urlsplit(origin).hostname}:8765"
        redirect = callback_origin + CALLBACK_PATH
        with self.lock:
            self.pending = {
                key: value for key, value in self.pending.items() if value[0] > time.monotonic()
            }
            if len(self.pending) >= 4:
                raise InstagramError("Wait ten minutes before another connection attempt.", 429)
            state = secrets.token_urlsafe(32)
            self.pending[state] = (time.monotonic() + 600, redirect, origin, page_id)
        return state, f"https://www.facebook.com/{self.settings.version}/dialog/oauth?" + urlencode(
            {
                "client_id": self.settings.app_id,
                "config_id": self.settings.login_config_id,
                "response_type": "code",
                "redirect_uri": redirect,
                "state": state,
            }
        )

    def finish(self, state, cookie, code, denied):
        self._configured()
        with self.lock:
            if not state or not cookie or not secrets.compare_digest(state, cookie):
                raise InstagramError(
                    "Instagram connection could not be verified. Start again.", 400
                )
            pending = self.pending.pop(state, None)
            if not pending or pending[0] < time.monotonic():
                raise InstagramError("Instagram connection expired or was already used.", 400)
            if denied or not code:
                return pending[2] + "/connections?instagram=cancelled"
            result = self.api.exchange(code, pending[1])
            token = result.get("access_token")
            if not isinstance(token, str) or not 1 <= len(token) <= 8192:
                raise InstagramError("Meta returned invalid credentials.")
            extended = self.api.request(
                "oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": self.settings.app_id,
                    "client_secret": self.settings.app_secret,
                    "fb_exchange_token": token,
                },
            )
            token, expires = extended.get("access_token"), extended.get("expires_in")
            if (
                not isinstance(token, str)
                or not token
                or type(expires) is not int
                or not 0 < expires <= 31536000
            ):
                raise InstagramError("Meta did not return valid long-lived credentials.")
            page = self.api.request(
                pending[3],
                token=token,
                params={
                    "fields": "access_token,instagram_business_account{id,username}",
                },
            )
            try:
                record = Credentials(
                    access_token=page["access_token"],
                    instagram_id=page["instagram_business_account"]["id"],
                    expires_at=time.time() + expires,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise InstagramError(
                    "The configured Page must be accessible and linked to an Instagram Business or Creator account.",
                    409,
                ) from exc
            account = hashlib.sha256(
                f"{self.settings.app_id}:{record.instagram_id}".encode()
            ).hexdigest()
            try:
                encrypted = self.protector(record.model_dump_json().encode())
            except (CredentialError, OSError) as exc:
                raise InstagramError("Could not protect Instagram credentials.", 503) from exc
            username = page["instagram_business_account"].get("username")
            label = "@" + username if isinstance(username, str) and 0 < len(username) < 300 else ""
            self.accounts.save(self.config, account, encrypted, label)
            return pending[2] + "/connections?instagram=connected"

    def _token(self, account_id=None):
        self._configured()
        row = self.accounts.get(self.config, account_id)
        if not row or row["config"] != self.config:
            raise InstagramError("Connect Instagram first.", 409)
        try:
            record = Credentials.model_validate_json(
                self.protector(row["credentials"], decrypt=True)
            )
        except (CredentialError, OSError, ValueError) as exc:
            raise InstagramError(
                "Cannot unlock Instagram credentials. Disconnect and reconnect.", 503
            ) from exc
        if record.expires_at <= time.time() + 60:
            raise InstagramError("Instagram authorization expired. Disconnect and reconnect.", 409)
        return record, row["account_id"]

    def creator(self, account_id=None):
        with self.lock:
            token, account = self._token(account_id)
            profile = self.api.request(
                token.instagram_id, token=token.access_token, params={"fields": "id,username"}
            )
            if profile.get("id") != token.instagram_id or not isinstance(
                profile.get("username"), str
            ):
                raise InstagramError("Instagram returned an invalid account profile.")
            self.accounts.label(account, "@" + profile["username"])
            return {"account_id": account, "username": profile["username"]}

    def disconnect(self, account_id=None):
        with self.lock:
            account_id = self.accounts.get(self.config, account_id)["account_id"]
            if self.active and not self.active.done():
                raise InstagramError(
                    "Wait for the active Reel upload to finish before disconnecting.", 409
                )
            self.accounts.delete(self.config, account_id)
            self.pending.clear()
        return {
            "connected": False,
            "warning": "Local credentials removed. Revoke Cliptric in Facebook's Business Integrations settings to remove Meta access. Published Reels remain on Instagram.",
        }

    def export(self, identifier):
        clip, path = self.store.clip(identifier), self.store.export_path(identifier)
        if not clip or clip.status != "succeeded" or path.is_symlink() or not path.is_file():
            raise InstagramError("Choose a completed, available export.", 404)
        if path.stat().st_size != clip.size_bytes:
            raise InstagramError("The export changed. Create a new export before posting.", 409)
        if not 0 < clip.size_bytes <= 300_000_000:
            raise InstagramError("Instagram Reels must be at most 300 MB.", 422)
        try:
            info = self.media.probe(path)
        except MediaError as exc:
            raise InstagramError(
                "The export could not be verified. Create a new export before posting.", 422
            ) from exc
        if not (
            3 <= info.duration <= 900
            and 23 <= info.frame_rate <= 60
            and info.width <= 1920
            and 0.01 <= info.width / info.height <= 10
        ):
            raise InstagramError(
                "Reels require 3-900 seconds, 23-60 fps, width at most 1920 pixels and a supported aspect ratio.",
                422,
            )
        return clip, path, info

    def export_info(self, identifier):
        clip, _, info = self.export(identifier)
        transcript = (
            TranscriptStore(self.store).job(str(clip.captions.transcription_id))
            if clip.captions
            else None
        )
        warning = (
            (
                transcript.warning
                if transcript
                else "Review the rendered captions; the original transcript is unavailable."
            )
            if clip.captions
            else None
        )
        return {
            "duration": info.duration,
            "width": info.width,
            "height": info.height,
            "size_bytes": clip.size_bytes,
            "caption_warning": warning,
        }

    def _save(self, job, db=None):
        if db is None:
            with self.store.connection() as connection:
                return self._save(job, connection)
        db.execute(
            "INSERT OR REPLACE INTO instagram_posts VALUES (?,?,?,?,?,?)",
            (
                job.id,
                job.export_id,
                job.account_id,
                job.status,
                job.created_at,
                job.model_dump_json(),
            ),
        )

    def get(self, identifier):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT payload FROM instagram_posts WHERE id=?", (identifier,)
            ).fetchone()
        if not row:
            raise InstagramError("Instagram post not found.", 404)
        return ReelJob.model_validate_json(row[0])

    def posts(self, export_id=None):
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT payload FROM instagram_posts WHERE (? IS NULL OR export_id=?) ORDER BY created_at DESC LIMIT 50",
                (export_id, export_id),
            ).fetchall()
        return [ReelJob.model_validate_json(row[0]) for row in rows]

    @staticmethod
    def checksum(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def validate_post(self, request):
        creator = self.creator(request.account_id)
        clip, path, _ = self.export(str(request.export_id))
        return creator, clip, path

    def submit(self, request):
        with self.lock:
            with self.store.connection() as db:
                row = db.execute(
                    "SELECT payload FROM instagram_posts WHERE id=?", (str(request.id),)
                ).fetchone()
                if row:
                    job = ReelJob.model_validate_json(row[0])
                    if job.request != request:
                        raise InstagramError(
                            "This posting token belongs to different approved content.", 409
                        )
                    return job
                if db.execute(
                    "SELECT 1 FROM instagram_posts WHERE export_id=? AND account_id=? AND status!='failed'",
                    (str(request.export_id), request.account_id),
                ).fetchone():
                    raise InstagramError(
                        "This export already has an Instagram attempt. Check its saved status to avoid duplicates.",
                        409,
                    )
            if self.stop.is_set() or (self.active and not self.active.done()):
                raise InstagramError(
                    "Another Instagram upload is active. Wait for it to finish.", 409
                )
            _, clip, path = self.validate_post(request)
            job = ReelJob(
                id=str(request.id),
                export_id=str(request.export_id),
                account_id=request.account_id,
                created_at=now(),
                request=request,
                status="queued",
                total_bytes=clip.size_bytes,
                sha256=self.checksum(path),
            )
            self._save(job)
            self.active = self.pool.submit(self._run, job.id)
            return job

    def _run(self, identifier):
        job = self.get(identifier)
        try:
            token, account = self._token(job.account_id)
            _, path, _ = self.export(job.export_id)
            if account != job.account_id or self.checksum(path) != job.sha256:
                raise InstagramError("The approved account or exported video changed.", 409)
            response = self.api.request(
                f"{token.instagram_id}/media",
                token=token.access_token,
                method="POST",
                form={
                    "media_type": "REELS",
                    "upload_type": "resumable",
                    "caption": job.request.caption,
                    "share_to_feed": str(job.request.share_to_feed).lower(),
                },
            )
            job.container_id, job.status = meta_id(response.get("id")), "uploading"
            self._save(job)
            with path.open("rb") as stream:

                def progress(sent):
                    job.uploaded_bytes = sent
                    self._save(job)

                self.api.upload(
                    job.container_id,
                    token.access_token,
                    stream,
                    job.total_bytes,
                    self.stop,
                    progress,
                )
            if self.checksum(path) != job.sha256:
                raise InstagramError(
                    "The export changed during upload. The Reel was not published."
                )
            job.uploaded_bytes, job.status = job.total_bytes, "processing"
            self._save(job)
            deadline = time.monotonic() + 300
            while (remaining := deadline - time.monotonic()) > 0:
                if self.stop.is_set():
                    raise InstagramError("Local publishing stopped before the Reel was published.")
                status = self.api.request(
                    job.container_id,
                    token=token.access_token,
                    params={"fields": "status_code"},
                    timeout=min(30, remaining),
                ).get("status_code")
                if status == "FINISHED":
                    # Persist this boundary before a potentially ambiguous non-idempotent call.
                    job.status = "publishing"
                    self._save(job)
                    result = self.api.request(
                        f"{token.instagram_id}/media_publish",
                        token=token.access_token,
                        method="POST",
                        form={"creation_id": job.container_id},
                    )
                    job.media_id = meta_id(result.get("id"))
                    job.status, job.error = "published", None
                    self._save(job)
                    return
                if status in {"ERROR", "EXPIRED"}:
                    raise InstagramError(
                        "Instagram rejected or expired the Reel container. Review the export and account before retrying."
                    )
                if status != "IN_PROGRESS":
                    raise InstagramError("Instagram returned an unexpected container status.")
                if self.stop.wait(min(5, max(0, deadline - time.monotonic()))):
                    raise InstagramError("Local publishing stopped before the Reel was published.")
            raise InstagramError(
                "Instagram processing exceeded five minutes. This attempt will not publish automatically."
            )
        except Exception as exc:
            logger.error(
                "Instagram job %s stopped (%s). Provider details were not logged.",
                identifier,
                type(exc).__name__,
            )
            job.status = "unknown" if job.status in {"publishing", "published"} else "failed"
            job.error = (
                str(exc)
                if isinstance(exc, InstagramError)
                else "Local Instagram publishing failed. Check storage and saved status."
            )
            self._save(job)

    def refresh(self, identifier):
        with self.lock:
            job = self.get(identifier)
            if job.status != "unknown" or not job.container_id:
                return job
            token, account = self._token(job.account_id)
            if account != job.account_id:
                raise InstagramError(
                    "Reconnect the original Instagram account to check this post.", 409
                )
            status = self.api.request(
                job.container_id, token=token.access_token, params={"fields": "status_code"}
            ).get("status_code")
            if status == "PUBLISHED":
                job.status, job.error = "published", None
            else:
                job.error = "Publication was not confirmed. Check Instagram before creating another export. Cliptric will not repeat the publish request."
            self._save(job)
            return job

    def close(self):
        self.stop.set()
        self.pool.shutdown(wait=True)
