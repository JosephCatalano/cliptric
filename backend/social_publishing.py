"""Durable per-export, per-account publishing for X and Facebook Pages."""

import base64
import hashlib
import logging
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .media import MediaError
from .social_accounts import Accounts
from .social_security import CredentialError, protect_secret
from .social_transport import Credentials, SocialError, identifier
from .storage import now

logger = logging.getLogger(__name__)


class SocialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    export_id: UUID
    account_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    caption: str = Field(max_length=5000)
    is_ai_generated: bool = Field(default=False, strict=True)
    consent: Literal[True]

    @field_validator("consent", mode="before")
    @classmethod
    def approved(cls, value):
        if value is not True:
            raise ValueError("Approve the exact video, account and publishing settings first.")
        return value

    @field_validator("caption")
    @classmethod
    def valid_text(cls, value):
        if any((ord(c) < 32 and c not in "\n\t") or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("Post text contains unsupported characters.")
        return value


class SocialJob(BaseModel):
    id: str
    export_id: str
    account_id: str
    created_at: str
    request: SocialRequest
    status: Literal["queued", "uploading", "publishing", "published", "failed", "unknown"]
    total_bytes: int
    uploaded_bytes: int = 0
    sha256: str
    media_id: str | None = None
    post_id: str | None = None
    error: str | None = None


class SocialPublisher:
    def __init__(
        self, store, media, api, web_origin, *, public_origin="", protector=protect_secret
    ):
        if api.platform not in {"x", "facebook"}:
            raise ValueError("Unsupported publisher")
        self.store, self.media, self.api, self.web_origin, self.protector = (
            store,
            media,
            api,
            web_origin,
            protector,
        )
        self.platform, self.name, self.settings = api.platform, api.name, api.settings
        self.public_origin = public_origin
        self.table = f"{self.platform}_posts"
        self.accounts = Accounts(store, f"{self.platform}_account", "config", SocialError)
        self.lock, self.stop, self.pending = threading.RLock(), threading.Event(), {}
        self.pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=self.platform + "-publishing"
        )
        self.active = None

    @property
    def config(self):
        return self.settings.client_id + (
            ":" + self.settings.login_config_id if self.platform == "facebook" else ""
        )

    def initialize(self):
        self.accounts.initialize()
        with self.store.connection() as db:
            db.execute(
                f"CREATE TABLE IF NOT EXISTS {self.table} (id TEXT PRIMARY KEY, export_id TEXT NOT NULL, account_id TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            db.execute(
                f"CREATE INDEX IF NOT EXISTS {self.table}_export ON {self.table}(export_id,created_at)"
            )
            for row in db.execute(
                f"SELECT payload FROM {self.table} WHERE status IN ('queued','uploading','publishing')"
            ).fetchall():
                job = SocialJob.model_validate_json(row[0])
                job.status = "unknown" if job.status == "publishing" else "failed"
                job.error = "Local processing was interrupted. Check saved publishing status before retrying."
                self._save(job, db)

    def status(self):
        with self.lock:
            accounts = self.accounts.list(self.config)
            return {
                "configured": bool(
                    os.name == "nt"
                    and self.settings.client_id
                    and self.settings.client_secret
                    and (self.platform != "facebook" or self.settings.login_config_id)
                ),
                "connected": bool(accounts),
                "account_id": accounts[0]["account_id"] if len(accounts) == 1 else None,
                "accounts": accounts,
            }

    def _configured(self):
        if not self.status()["configured"]:
            raise SocialError(
                f"Configure {self.name} in the backend and restart the API. Windows credential storage is required. See the README.",
                503,
            )

    def begin(self, origin, page_id=None):
        self._configured()
        hosted = bool(self.public_origin and origin == self.public_origin)
        if not hosted and origin not in {
            self.web_origin,
            self.web_origin.replace("127.0.0.1", "localhost"),
        }:
            raise SocialError("Start account connection from the local app.", 403)
        if self.platform == "facebook":
            if not page_id:
                raise SocialError("Enter the Facebook Page ID to connect.", 422)
            identifier(page_id)
        callback_origin = origin if hosted else f"http://{urlsplit(origin).hostname}:8765"
        redirect = f"{callback_origin}/api/{self.platform}/callback"
        with self.lock:
            self.pending = {
                key: item for key, item in self.pending.items() if item[0] > time.monotonic()
            }
            if len(self.pending) >= 4:
                raise SocialError("Too many connection attempts. Wait ten minutes.", 429)
            state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
            self.pending[state] = (time.monotonic() + 600, verifier, redirect, origin, page_id)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        return state, self.api.authorization(state, challenge, redirect, page_id)

    def finish(self, state, cookie, code, denied):
        self._configured()
        with self.lock:
            if not state or not cookie or not secrets.compare_digest(state, cookie):
                raise SocialError("Account connection could not be verified. Start again.", 400)
            pending = self.pending.pop(state, None)
            if not pending or pending[0] < time.monotonic():
                raise SocialError("Account connection expired or was already used.", 400)
            if denied or not code:
                return pending[3] + f"/connections?{self.platform}=cancelled"
            record = self.api.connect(code, pending[1], pending[2], pending[4])
            self._store_token(record)
            return pending[3] + f"/connections?{self.platform}=connected"

    def _store_token(self, record):
        account = hashlib.sha256(f"{self.settings.client_id}:{record.subject}".encode()).hexdigest()
        try:
            encrypted = self.protector(record.model_dump_json().encode())
        except (CredentialError, OSError) as exc:
            raise SocialError("Could not protect account credentials.", 503) from exc
        self.accounts.save(self.config, account, encrypted, record.label)
        return record, account

    def _token(self, account_id):
        self._configured()
        with self.lock:
            row = self.accounts.get(self.config, account_id)
            try:
                record = Credentials.model_validate_json(
                    self.protector(row["credentials"], decrypt=True)
                )
            except (ValueError, OSError) as exc:
                raise SocialError(
                    "Cannot unlock credentials. Reconnect this account on the original Windows user.",
                    503,
                ) from exc
            if record.expires_at <= time.time() + 60:
                refreshed = self.api.refresh(record)
                if refreshed.subject != record.subject:
                    raise SocialError("The account identity changed during token refresh.", 409)
                record, _ = self._store_token(refreshed)
            return record, row["account_id"]

    def creator(self, account_id=None):
        with self.lock:
            record, account = self._token(account_id)
            label = self.api.identity(record)
            self.accounts.label(account, label)
            return {"account_id": account, "label": label}

    def disconnect(self, account_id=None):
        with self.lock:
            if self.active and not self.active.done():
                raise SocialError("Wait for the active upload to finish before disconnecting.", 409)
            self.accounts.delete(self.config, account_id)
            self.pending.clear()
            return {
                "connected": False,
                "warning": f"Local {self.name} credentials removed. Revoke Cliptric in the provider's connected-app settings to remove access. Published posts remain online.",
            }

    def export(self, export_id):
        clip, path = self.store.clip(export_id), self.store.export_path(export_id)
        if not clip or clip.status != "succeeded" or path.is_symlink() or not path.is_file():
            raise SocialError("Choose a completed, available export.", 404)
        if path.stat().st_size != clip.size_bytes:
            raise SocialError("The export changed. Create a new export before posting.", 409)
        limit = 8 * 1024**3 if self.platform == "x" else 1024**3
        if not 0 < clip.size_bytes <= limit:
            raise SocialError(f"The export exceeds Cliptric's {self.name} upload size limit.", 422)
        try:
            info = self.media.probe(path)
        except MediaError as exc:
            raise SocialError("The exported video could not be verified.", 422) from exc
        if self.platform == "facebook":
            if not (
                3 <= info.duration <= 90
                and info.width >= 540
                and info.height >= 960
                and abs(info.width / info.height - 9 / 16) < 0.01
                and 24 <= info.frame_rate <= 60
            ):
                raise SocialError(
                    "Facebook Reels require 3-90 seconds, portrait 9:16, at least 540x960 and 24-60 fps.",
                    422,
                )
        elif not 0 < info.duration <= 1200:
            raise SocialError(
                "This X integration supports videos up to 20 minutes. The provider may apply account-specific limits.",
                422,
            )
        return clip, path, info

    def export_info(self, export_id):
        clip, _, info = self.export(export_id)
        return {
            "duration": info.duration,
            "width": info.width,
            "height": info.height,
            "size_bytes": clip.size_bytes,
        }

    @staticmethod
    def checksum(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def validate_post(self, request, creator=None):
        if self.platform == "x" and (len(request.caption) > 280 or request.is_ai_generated):
            raise SocialError(
                "Use at most 280 characters for X. AI disclosure here applies only to Facebook; X also validates weighted text length.",
                422,
            )
        if creator is None:
            creator = self.creator(request.account_id)
        if creator["account_id"] != request.account_id:
            raise SocialError("The destination account changed. Review the post again.", 409)
        clip, path, _ = self.export(str(request.export_id))
        return creator, clip, path

    def _save(self, job, db=None):
        if db is None:
            with self.store.connection() as connection:
                return self._save(job, connection)
        db.execute(
            f"INSERT OR REPLACE INTO {self.table} VALUES (?,?,?,?,?,?)",
            (
                job.id,
                job.export_id,
                job.account_id,
                job.status,
                job.created_at,
                job.model_dump_json(),
            ),
        )

    def get(self, job_id):
        with self.store.connection() as db:
            row = db.execute(f"SELECT payload FROM {self.table} WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise SocialError("Publishing attempt not found.", 404)
        return SocialJob.model_validate_json(row[0])

    def posts(self, export_id=None):
        with self.store.connection() as db:
            return [
                SocialJob.model_validate_json(row[0])
                for row in db.execute(
                    f"SELECT payload FROM {self.table} WHERE (? IS NULL OR export_id=?) ORDER BY created_at DESC LIMIT 100",
                    (export_id, export_id),
                )
            ]

    def submit(self, request):
        with self.lock:
            with self.store.connection() as db:
                row = db.execute(
                    f"SELECT payload FROM {self.table} WHERE id=?", (str(request.id),)
                ).fetchone()
                if row:
                    job = SocialJob.model_validate_json(row[0])
                    if job.request != request:
                        raise SocialError(
                            "This posting token belongs to different approved content.", 409
                        )
                    return job
                if db.execute(
                    f"SELECT 1 FROM {self.table} WHERE export_id=? AND account_id=? AND status!='failed'",
                    (str(request.export_id), request.account_id),
                ).fetchone():
                    raise SocialError(
                        "This export already has a publishing attempt for this account. Check its saved status.",
                        409,
                    )
                if (
                    self.platform == "facebook"
                    and db.execute(
                        f"SELECT COUNT(*) FROM {self.table} WHERE account_id=? AND created_at>? AND status!='failed'",
                        (
                            request.account_id,
                            (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                        ),
                    ).fetchone()[0]
                    >= 30
                ):
                    raise SocialError(
                        "Facebook allows at most 30 API-published Reels per Page in 24 hours. Try later.",
                        429,
                    )
            if self.stop.is_set() or (self.active and not self.active.done()):
                raise SocialError(
                    f"Another {self.name} upload is active. Wait for it to finish.", 409
                )
            _, clip, path = self.validate_post(request)
            job = SocialJob(
                id=str(request.id),
                export_id=str(request.export_id),
                account_id=request.account_id,
                request=request,
                created_at=now(),
                status="queued",
                total_bytes=clip.size_bytes,
                sha256=self.checksum(path),
            )
            self._save(job)
            self.active = self.pool.submit(self._run, job.id)
            return job

    def _run(self, job_id):
        job = self.get(job_id)
        try:
            record, _ = self._token(job.account_id)
            _, path, _ = self.export(job.export_id)
            if self.checksum(path) != job.sha256:
                raise SocialError("The approved video changed.", 409)
            job.status = "uploading"
            self._save(job)

            def progress(sent):
                job.uploaded_bytes = sent
                self._save(job)

            job.media_id = self.api.prepare(record, path, job.total_bytes, self.stop, progress)
            if self.stop.is_set() or self.checksum(path) != job.sha256:
                raise SocialError(
                    "Publishing stopped or the approved video changed during upload.", 409
                )
            # Persist before the one non-idempotent publish call. Never retry it.
            job.status = "publishing"
            self._save(job)
            job.post_id = self.api.publish(record, job.media_id, job.request)
            self._save(job)
            job.status = (
                "published"
                if self.platform == "x" or self.api.published(record, job.media_id, job.post_id)
                else "unknown"
            )
            job.error = (
                None
                if job.status == "published"
                else "Facebook is processing publication. Refresh saved status; the publish request will not be repeated."
            )
            self._save(job)
        except Exception as exc:
            logger.error(
                "%s job %s stopped (%s); provider details redacted",
                self.name,
                job_id,
                type(exc).__name__,
            )
            job.status = (
                "unknown" if job.status in {"publishing", "published", "unknown"} else "failed"
            )
            job.error = (
                str(exc)
                if isinstance(exc, SocialError)
                else "Local publishing failed. Check saved status and storage."
            )
            self._save(job)

    def refresh(self, job_id):
        with self.lock:
            job = self.get(job_id)
            if job.status != "unknown":
                return job
            record, _ = self._token(job.account_id)
            if self.api.published(record, job.media_id, job.post_id):
                job.status, job.error = "published", None
                self._save(job)
            return job

    def close(self):
        self.stop.set()
        self.pool.shutdown(wait=True)
