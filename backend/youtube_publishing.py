"""Approval-driven, single-user YouTube Shorts uploads with durable recovery."""

import base64
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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .media import MediaError
from .social_accounts import Accounts
from .social_security import CredentialError, protect_secret
from .storage import now
from .transcription_store import TranscriptStore
from .twitch_publishing import publishing_error
from .youtube_api import CALLBACK_PATH, SCOPES, YouTubeAPI, YouTubeError

logger = logging.getLogger(__name__)
Privacy = Literal["private", "unlisted", "public"]
MIN_BATCH_UPLOAD_INTERVAL = 10


class Channel(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    title: str = Field(min_length=1, max_length=200)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    access_token: str = Field(min_length=1, max_length=8192, pattern=r"^\S+$", repr=False)
    refresh_token: str = Field(min_length=1, max_length=8192, pattern=r"^\S+$", repr=False)
    scope: str = Field(max_length=2048)
    expires_at: float
    channel: Channel


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    export_id: UUID
    account_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    category_id: str = Field(pattern=r"^\d{1,6}$")
    privacy: Privacy
    made_for_kids: bool = Field(strict=True)
    contains_synthetic_media: bool = Field(strict=True)
    notify_subscribers: bool = Field(default=False, strict=True)
    consent: bool = Field(strict=True)

    @field_validator("title", "description")
    @classmethod
    def valid_text(cls, value, info):
        if any(
            c in "<>" or (ord(c) < 32 and c not in "\n\t") or 0xD800 <= ord(c) <= 0xDFFF
            for c in value
        ):
            raise ValueError("Text contains unsupported YouTube characters.")
        if info.field_name == "title" and not value.strip():
            raise ValueError("Enter a video title.")
        if info.field_name == "description" and len(value.encode("utf-8")) > 5000:
            raise ValueError("YouTube descriptions must fit within 5000 UTF-8 bytes.")
        return value

    @model_validator(mode="after")
    def approved(self):
        if not self.consent:
            raise ValueError("Approve this video and its YouTube settings first.")
        return self


class UploadJob(BaseModel):
    id: str
    export_id: str
    account_id: str
    channel: Channel
    created_at: str
    request: UploadRequest
    status: Literal[
        "queued", "initializing", "uploading", "paused", "processing", "ready", "failed", "unknown"
    ]
    total_bytes: int
    uploaded_bytes: int = 0
    sha256: str
    video_id: str | None = None
    actual_privacy: Privacy | None = None
    error: str | None = None
    resume_after: float = 0
    can_resume: bool = False
    batch_pending: bool = False


class BatchUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uploads: list[UploadRequest] = Field(min_length=1, max_length=75)


class YouTubePublishing:
    def __init__(self, store, media, settings, web_origin, *, api=None):
        self.store, self.media, self.settings, self.web_origin = store, media, settings, web_origin
        self.api = api or YouTubeAPI(settings)
        self.accounts = Accounts(store, "youtube_account", "client_id", YouTubeError)
        self.lock = threading.RLock()
        self.pending = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="youtube-publishing")
        self.active = None
        self.stop = threading.Event()
        self.last_check = 0.0
        self.category_cache = None

    def initialize(self):
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS youtube_account (
                    id INTEGER PRIMARY KEY CHECK(id=1), client_id TEXT NOT NULL,
                    account_id TEXT NOT NULL, credentials BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS youtube_uploads (
                    id TEXT PRIMARY KEY, export_id TEXT NOT NULL, account_id TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS youtube_uploads_export ON youtube_uploads(export_id,created_at);
                CREATE TABLE IF NOT EXISTS youtube_upload_sessions (
                    id TEXT PRIMARY KEY REFERENCES youtube_uploads(id), secret BLOB NOT NULL
                );
            """)
            for row in db.execute(
                "SELECT payload FROM youtube_uploads WHERE status IN ('queued','initializing','uploading')"
            ).fetchall():
                job = UploadJob.model_validate_json(row[0])
                if job.status == "queued" and job.batch_pending:
                    continue
                job.status = "unknown" if job.can_resume else "failed"
                job.error = (
                    "Local upload was interrupted. Check its status before resuming."
                    if job.can_resume
                    else "Interrupted before media transfer. Review and start a new attempt."
                )
                self._save(job, db)

        self.accounts.initialize()
        with self.store.connection() as db:
            if self._next_batch_upload(db):
                self.active = self.pool.submit(self._drain_batch)

    def _configured(self):
        if not self.settings.client_id or os.name != "nt":
            raise YouTubeError(
                "Configure a Google Desktop OAuth client for this Windows installation. See YouTube setup in the README.",
                503,
            )

    def status(self):
        with self.lock:
            accounts = self.accounts.list(self.settings.client_id)
            return {
                "configured": bool(self.settings.client_id and os.name == "nt"),
                "connected": bool(accounts),
                "account_id": accounts[0]["account_id"] if len(accounts) == 1 else None,
                "accounts": accounts,
            }

    def begin(self, origin):
        self._configured()
        if origin not in {self.web_origin, self.web_origin.replace("127.0.0.1", "localhost")}:
            raise YouTubeError("Start account connection from the local app.", 403)
        redirect = f"http://{urlsplit(origin).hostname}:8765{CALLBACK_PATH}"
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        with self.lock:
            self.pending = {
                key: value for key, value in self.pending.items() if value[0] > time.monotonic()
            }
            if len(self.pending) >= 4:
                raise YouTubeError("Too many connection attempts. Wait ten minutes.", 429)
            self.pending[state] = (time.monotonic() + 600, verifier, redirect, origin)
        query = {
            "client_id": self.settings.client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "state": state,
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("="),
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent select_account",
        }
        return state, "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(query)

    def finish(self, state, cookie, code, denied):
        self._configured()
        with self.lock:
            if not state or not cookie or not secrets.compare_digest(state, cookie):
                raise YouTubeError("YouTube connection could not be verified. Start again.", 400)
            pending = self.pending.pop(state, None)
            if not pending or pending[0] < time.monotonic():
                raise YouTubeError("YouTube connection expired or was already used.", 400)
            if denied or not code:
                return pending[3] + "/connections?youtube=cancelled"
            response = self.api.token(
                grant_type="authorization_code",
                code=code,
                code_verifier=pending[1],
                redirect_uri=pending[2],
            )
            self._store_token(response)
            return pending[3] + "/connections?youtube=connected"

    def _channel(self, token):
        data = self.api.get("channels", token, part="snippet", mine="true", maxResults=50)
        try:
            items = data["items"]
            if not isinstance(items, list) or len(items) != 1 or data.get("nextPageToken"):
                raise ValueError("Select one channel")
            return Channel(id=items[0]["id"], title=items[0]["snippet"]["title"])
        except (KeyError, TypeError, ValueError) as exc:
            raise YouTubeError(
                "Select a Google identity with exactly one YouTube channel. Create or select your channel in YouTube, then reconnect.",
                409,
            ) from exc

    def _store_token(self, response, previous=None, account=None):
        try:
            expiry = response["expires_in"]
            if (
                type(expiry) is not int
                or not 0 < expiry <= 31536000
                or response.get("token_type", "").lower() != "bearer"
            ):
                raise ValueError("Invalid token response")
            scope = response.get("scope", previous.scope if previous else "")
            if not isinstance(scope, str) or not set(SCOPES).issubset(scope.split()):
                raise ValueError("Missing permission")
            token = response["access_token"]
            record = Credentials(
                access_token=token,
                refresh_token=response.get(
                    "refresh_token", previous.refresh_token if previous else ""
                ),
                scope=scope,
                expires_at=time.time() + expiry,
                channel=previous.channel if previous else Channel(id="pending", title="pending"),
            )
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise YouTubeError(
                "Google did not grant valid upload/read permissions and offline access. Reconnect and allow both YouTube permissions.",
                409,
            ) from exc
        if previous is None:
            record.channel = self._channel(record.access_token)
        fingerprint = hashlib.sha256(
            (self.settings.client_id + ":" + record.channel.id).encode()
        ).hexdigest()
        if account and fingerprint != account:
            raise YouTubeError("The destination channel changed. Reconnect.", 409)
        try:
            encrypted = protect_secret(record.model_dump_json().encode())
        except CredentialError as exc:
            raise YouTubeError(str(exc), 503) from exc
        self.accounts.save(self.settings.client_id, fingerprint, encrypted, record.channel.title)
        return record, fingerprint

    def _stored_token(self, account_id=None):
        self._configured()
        row = self.accounts.get(self.settings.client_id, account_id)
        if not row or row["client_id"] != self.settings.client_id:
            raise YouTubeError("Connect your YouTube channel first.", 409)
        try:
            return Credentials.model_validate_json(
                protect_secret(row["credentials"], decrypt=True)
            ), row["account_id"]
        except (ValueError, OSError) as exc:
            raise YouTubeError(
                "Cannot unlock YouTube credentials. Reconnect on this Windows account.", 503
            ) from exc

    def _token(self, account_id=None):
        record, account = self._stored_token(account_id)
        if record.expires_at <= time.time() + 60:
            record, account = self._store_token(
                self.api.token(grant_type="refresh_token", refresh_token=record.refresh_token),
                record,
                account,
            )
        return record, account

    def creator(self, account_id=None):
        with self.lock:
            record, account = self._token(account_id)
            channel = self._channel(record.access_token)
            if channel.id != record.channel.id:
                raise YouTubeError(
                    "The connected YouTube channel changed. Reconnect before uploading.", 409
                )
            self.accounts.label(account, channel.title)
            return {
                "account_id": account,
                "channel": channel.model_dump(),
                "categories": self.categories(record.access_token),
            }

    def categories(self, token):
        if self.category_cache and self.category_cache[0] > time.monotonic():
            return self.category_cache[1]
        data = self.api.get("videoCategories", token, part="snippet", regionCode="CA")
        try:
            items = data["items"]
            if not isinstance(items, list) or not 0 < len(items) <= 100:
                raise ValueError("Invalid categories")
            categories = [
                {"id": item["id"], "title": item["snippet"]["title"]}
                for item in items
                if item["snippet"].get("assignable") is True
            ]
            if not categories or any(
                not isinstance(c["id"], str)
                or not c["id"].isdigit()
                or not isinstance(c["title"], str)
                or len(c["title"]) > 200
                for c in categories
            ):
                raise ValueError("Invalid categories")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise YouTubeError("YouTube returned invalid upload categories.") from exc
        self.category_cache = (time.monotonic() + 3600, categories)
        return categories

    def disconnect(self, account_id=None):
        with self.lock:
            self._idle()
            account_id = self.accounts.get(self.settings.client_id, account_id)["account_id"]
            # Google revocation can revoke other channels sharing the same grant.
            warning = "Local channel disconnected. Revoke app access in Google Account settings if desired; this can affect other connected channels."
            self.accounts.delete(self.settings.client_id, account_id)
            self.pending.clear()
            self.category_cache = None
            return {"connected": False, "warning": warning}

    def export(self, identifier):
        clip, path = self.store.clip(identifier), self.store.export_path(identifier)
        if not clip or clip.status != "succeeded" or path.is_symlink() or not path.is_file():
            raise YouTubeError("Choose a completed, available clip export.", 404)
        size = path.stat().st_size
        if size != clip.size_bytes or not 0 < size <= 512 * 1024**2:
            raise YouTubeError("The export changed or exceeds 512 MiB. Create another export.", 409)
        try:
            info = self.media.probe(path)
        except MediaError as exc:
            raise YouTubeError(
                "The exported video cannot be read. Create another export.", 422
            ) from exc
        if (
            not 0 < info.duration <= 180
            or not 0 < info.width <= info.height
            or info.sample_aspect_ratio != "1:1"
        ):
            raise YouTubeError(
                "Shorts require a square or vertical video, square pixels, and a duration of at most three minutes. Export with vertical framing first.",
                422,
            )
        return clip, path, info

    def export_details(self, identifier):
        clip, _, info = self.export(identifier)
        warning = None
        if clip.captions:
            transcript = TranscriptStore(self.store).job(str(clip.captions.transcription_id))
            warning = (
                transcript.warning
                if transcript
                else "The original transcript is unavailable. Review the rendered captions."
            )
        return {
            "id": clip.id,
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
            "INSERT INTO youtube_uploads VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,payload=excluded.payload",
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
                "SELECT payload FROM youtube_uploads WHERE id=?", (identifier,)
            ).fetchone()
        if not row:
            raise YouTubeError("YouTube upload not found.", 404)
        return UploadJob.model_validate_json(row[0])

    def posts(self, export_id=None):
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT payload FROM youtube_uploads WHERE (? IS NULL OR export_id=?) ORDER BY created_at DESC LIMIT 50",
                (export_id, export_id),
            ).fetchall()
        return [UploadJob.model_validate_json(row[0]) for row in rows]

    def _idle(self):
        if self.stop.is_set() or (self.active is not None and not self.active.done()):
            raise YouTubeError("Another YouTube upload is active. Wait for it to finish.", 409)

    def validate_post(self, request, creator=None):
        blocked = publishing_error(
            self.store, request.export_id, "youtube", request.account_id, request.id
        )
        if blocked:
            raise YouTubeError(blocked, 409)
        if creator is None:
            creator = self.creator(request.account_id)
        if creator["account_id"] != request.account_id:
            raise YouTubeError("The destination account changed. Review again.", 409)
        if request.category_id not in {item["id"] for item in creator["categories"]}:
            raise YouTubeError("Select a currently assignable YouTube category.", 422)
        clip, path, _ = self.export(str(request.export_id))
        return creator, clip, path

    @staticmethod
    def _upload_job(request, creator, clip, path, *, batch_pending=False):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        return UploadJob(
            id=str(request.id),
            export_id=str(request.export_id),
            account_id=request.account_id,
            channel=Channel.model_validate(creator["channel"]),
            created_at=now(),
            request=request,
            status="queued",
            total_bytes=clip.size_bytes,
            sha256=digest,
            batch_pending=batch_pending,
        )

    def submit_batch(self, payload):
        requests = payload.uploads
        account = requests[0].account_id
        if (
            len({request.id for request in requests}) != len(requests)
            or len({request.export_id for request in requests}) != len(requests)
            or any(request.account_id != account for request in requests)
        ):
            raise YouTubeError(
                "Choose distinct exports and one YouTube channel for the batch.", 422
            )

        with self.lock:
            identifiers = [str(request.id) for request in requests]
            placeholders = ",".join("?" for _ in identifiers)
            with self.store.connection() as db:
                existing = {
                    row["id"]: UploadJob.model_validate_json(row["payload"])
                    for row in db.execute(
                        f"SELECT id,payload FROM youtube_uploads WHERE id IN ({placeholders})",
                        identifiers,
                    )
                }
            if existing:
                if any(
                    job_id not in existing or existing[job_id].request != request
                    for job_id, request in zip(identifiers, requests, strict=True)
                ):
                    raise YouTubeError("These upload tokens belong to different approvals.", 409)
                return [existing[job_id] for job_id in identifiers]

            self._idle()
            creator = self.creator(account)
            jobs = []
            for request in requests:
                _, clip, path = self.validate_post(request, creator)
                jobs.append(self._upload_job(request, creator, clip, path, batch_pending=True))

            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                for job in jobs:
                    if (
                        db.execute(
                            "SELECT 1 FROM youtube_uploads WHERE export_id=? AND account_id=? AND status!='failed'",
                            (job.export_id, account),
                        ).fetchone()
                        or db.execute(
                            "SELECT 1 FROM scheduled_posts WHERE platform='youtube' AND export_id=? "
                            "AND account_id=? AND status IN ('scheduled','dispatching','submitted','unknown')",
                            (job.export_id, account),
                        ).fetchone()
                    ):
                        raise YouTubeError(
                            f"{job.request.title} already has an upload or scheduled post for this channel.",
                            409,
                        )
                    self._save(job, db)
            self.active = self.pool.submit(self._drain_batch)
            return jobs

    @staticmethod
    def _next_batch_upload(db):
        return db.execute(
            "SELECT id FROM youtube_uploads WHERE status='queued' "
            "AND json_extract(payload,'$.batch_pending')=1 ORDER BY rowid LIMIT 1"
        ).fetchone()

    def _drain_batch(self):
        while not self.stop.is_set():
            with self.store.connection() as db:
                row = self._next_batch_upload(db)
            if row is None:
                return
            started = time.monotonic()
            self._run(row["id"], False)
            self.stop.wait(max(0, MIN_BATCH_UPLOAD_INTERVAL - (time.monotonic() - started)))

    def submit(self, request):
        with self.lock:
            with self.store.connection() as db:
                row = db.execute(
                    "SELECT payload FROM youtube_uploads WHERE id=?", (str(request.id),)
                ).fetchone()
            if row:
                job = UploadJob.model_validate_json(row[0])
                if job.request != request:
                    raise YouTubeError(
                        "This upload token belongs to different approved settings.", 409
                    )
                return job
            self._idle()
            creator, clip, path = self.validate_post(request)
            with self.store.connection() as db:
                if db.execute(
                    "SELECT 1 FROM youtube_uploads WHERE export_id=? AND account_id=? AND status!='failed'",
                    (str(request.export_id), request.account_id),
                ).fetchone():
                    raise YouTubeError(
                        "This export already has an upload attempt. Check its saved status to avoid duplicates.",
                        409,
                    )
                if (
                    db.execute(
                        "SELECT count(*) FROM youtube_uploads WHERE julianday(created_at)>julianday('now','-1 minute')"
                    ).fetchone()[0]
                    >= 6
                ):
                    raise YouTubeError("Wait a minute before starting another upload.", 429)
            job = self._upload_job(request, creator, clip, path)
            self._save(job)
            self.active = self.pool.submit(self._run, job.id, False)
            return job

    def _session(self, identifier):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT secret FROM youtube_upload_sessions WHERE id=?", (identifier,)
            ).fetchone()
        if not row:
            raise YouTubeError(
                "No saved resumable session is available. Check YouTube Studio before starting again.",
                409,
            )
        try:
            return protect_secret(row[0], decrypt=True).decode("utf-8")
        except (ValueError, OSError) as exc:
            raise YouTubeError(
                "Cannot unlock the upload session on this Windows account.", 503
            ) from exc

    def _apply(self, job, result):
        if "video" in result:
            video = result["video"]
            try:
                identifier = video["id"]
                if (
                    not isinstance(identifier, str)
                    or len(identifier) != 11
                    or not all(c.isascii() and (c.isalnum() or c in "-_") for c in identifier)
                ):
                    raise ValueError("Invalid video ID")
                if video["snippet"]["channelId"] != job.channel.id:
                    raise ValueError("Wrong channel")
                status = video["status"]
                privacy = status["privacyStatus"]
                if privacy not in {"private", "unlisted", "public"}:
                    raise ValueError("Invalid visibility")
                state = status["uploadStatus"]
                processing = video.get("processingDetails", {}).get("processingStatus")
                if state not in {
                    "uploaded",
                    "processed",
                    "failed",
                    "rejected",
                    "deleted",
                } or processing not in {None, "processing", "succeeded", "failed", "terminated"}:
                    raise ValueError("Unknown processing status")
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise YouTubeError(
                    "YouTube returned an invalid video status. Check YouTube Studio before retrying."
                ) from exc
            if job.video_id and job.video_id != identifier:
                raise YouTubeError("YouTube returned a different video. Check YouTube Studio.")
            job.video_id, job.actual_privacy = identifier, privacy
            job.uploaded_bytes, job.can_resume = job.total_bytes, False
            failed = state in {"failed", "rejected", "deleted"} or processing in {
                "failed",
                "terminated",
            }
            job.status = (
                "failed"
                if failed
                else "ready"
                if state == "processed" or processing == "succeeded"
                else "processing"
            )
            job.error = (
                "YouTube rejected, removed, or could not process this video. Review it in YouTube Studio."
                if failed
                else None
            )
            with self.store.connection() as db:
                self._save(job, db)
                db.execute("DELETE FROM youtube_upload_sessions WHERE id=?", (job.id,))
        else:
            job.uploaded_bytes = result["offset"]
            job.resume_after = result["resume_after"]
            job.status, job.can_resume, job.error = (
                "paused",
                True,
                "Upload is incomplete. Resume sends only the bytes YouTube has not confirmed.",
            )
            self._save(job)

    def _run(self, identifier, resume):
        job = self.get(identifier)
        job.batch_pending = False
        try:
            blocked = publishing_error(
                self.store, job.export_id, "youtube", job.account_id, job.id, reserve=True
            )
            if blocked:
                raise YouTubeError(blocked, 409)
            path = self.store.export_path(job.export_id)
            if path.is_symlink() or not path.is_file():
                raise YouTubeError("The approved export is unavailable.")
            with path.open("rb") as stream:
                if (
                    os.fstat(stream.fileno()).st_size != job.total_bytes
                    or hashlib.file_digest(stream, "sha256").hexdigest() != job.sha256
                ):
                    raise YouTubeError(
                        "The approved export changed. No replacement media was sent."
                    )
                with self.lock:
                    record, account = self._token(job.account_id)
                    if account != job.account_id:
                        raise YouTubeError("Reconnect the original destination account.", 409)
                    if resume:
                        url = self._session(job.id)
                        self._apply(
                            job, self.api.transfer(url, record.access_token, job.total_bytes)
                        )
                        if job.status != "paused" or job.resume_after > time.time() + 1:
                            return
                    else:
                        job.status = "initializing"
                        self._save(job)
                        payload = job.request
                        metadata = {
                            "snippet": {
                                "title": payload.title,
                                "description": payload.description,
                                "categoryId": payload.category_id,
                            },
                            "status": {
                                "privacyStatus": payload.privacy,
                                "selfDeclaredMadeForKids": payload.made_for_kids,
                                "containsSyntheticMedia": payload.contains_synthetic_media,
                            },
                        }
                        url = self.api.initiate(
                            record.access_token,
                            metadata,
                            job.total_bytes,
                            payload.notify_subscribers,
                        )
                        encrypted = protect_secret(url.encode())
                        job.can_resume = True
                        with self.store.connection() as db:
                            self._save(job, db)
                            db.execute(
                                "INSERT INTO youtube_upload_sessions VALUES (?,?)",
                                (job.id, encrypted),
                            )
                    job.status, job.error = "uploading", None
                    self._save(job)
                stream.seek(job.uploaded_bytes)
                result = self.api.transfer(
                    url,
                    record.access_token,
                    job.total_bytes,
                    stream=stream,
                    offset=job.uploaded_bytes,
                    stop=self.stop,
                )
                with self.lock:
                    self._apply(job, result)
        except Exception as exc:
            logger.error(
                "YouTube upload %s stopped (%s). Provider payloads were not logged.",
                identifier,
                type(exc).__name__,
            )
            with self.lock:
                job.status = "unknown" if job.can_resume else "failed"
                job.error = (
                    str(exc)
                    if isinstance(exc, (YouTubeError, CredentialError))
                    else "Local upload failed. Check storage and saved upload status."
                )
                self._save(job)

    def refresh(self, identifier):
        with self.lock:
            job = self.get(identifier)
            if (
                job.status in {"queued", "initializing", "uploading", "ready", "failed"}
                or time.monotonic() - self.last_check < 3
            ):
                return job
            if job.resume_after > time.time():
                return job
            self.last_check = time.monotonic()
            record, account = self._token(job.account_id)
            if account != job.account_id:
                raise YouTubeError("Reconnect the original channel to check this upload.", 409)
            if job.video_id:
                data = self.api.get(
                    "videos",
                    record.access_token,
                    part="snippet,status,processingDetails",
                    id=job.video_id,
                )
                items = data.get("items")
                if not isinstance(items, list) or len(items) != 1:
                    raise YouTubeError(
                        "YouTube cannot confirm this video. Check YouTube Studio.", 409
                    )
                self._apply(job, {"video": items[0]})
            elif job.can_resume:
                self._apply(
                    job,
                    self.api.transfer(self._session(job.id), record.access_token, job.total_bytes),
                )
            return self.get(identifier)

    def resume(self, identifier):
        with self.lock:
            job = self.get(identifier)
            self._idle()
            if job.status not in {"paused", "unknown"} or not job.can_resume or job.video_id:
                raise YouTubeError("This upload cannot be resumed. Refresh its saved status.", 409)
            if job.resume_after > time.time():
                raise YouTubeError(
                    "Google requested a delay. Wait before resuming this upload.", 429
                )
            creator = self.creator(job.account_id)
            if creator["account_id"] != job.account_id:
                raise YouTubeError("Reconnect the original channel before resuming.", 409)
            job.status = "queued"
            self._save(job)
            self.active = self.pool.submit(self._run, job.id, True)
            return job

    def close(self):
        self.stop.set()
        self.pool.shutdown(wait=True)
