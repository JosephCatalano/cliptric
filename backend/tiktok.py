"""Single-user local TikTok connection and durable, approval-driven publishing."""

import hashlib
import logging
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .storage import now
from .tiktok_api import CALLBACK_PATH, TikTokAPI, TikTokError, chunk_plan, protect_tokens
from .transcription_store import TranscriptStore

logger = logging.getLogger(__name__)
Privacy = Literal["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"]


class Creator(BaseModel):
    model_config = ConfigDict(extra="ignore")
    creator_username: str = Field(max_length=120)
    creator_nickname: str = Field(max_length=200)
    privacy_level_options: list[Privacy] = Field(min_length=1, max_length=4)
    comment_disabled: bool = Field(strict=True)
    duet_disabled: bool = Field(strict=True)
    stitch_disabled: bool = Field(strict=True)
    max_video_post_duration_sec: int = Field(strict=True, gt=0, le=86400)


class TokenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    access_token: str = Field(min_length=1, max_length=8192, repr=False)
    refresh_token: str = Field(min_length=1, max_length=8192, repr=False)
    open_id: str = Field(min_length=1, max_length=256, repr=False)
    scope: str = Field(max_length=1024)
    expires_at: float
    refresh_expires_at: float


class PostRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    export_id: UUID
    account_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    caption: str = Field(max_length=2200)
    privacy: Privacy
    allow_comments: bool = Field(default=False, strict=True)
    allow_duet: bool = Field(default=False, strict=True)
    allow_stitch: bool = Field(default=False, strict=True)
    commercial: bool = Field(default=False, strict=True)
    your_brand: bool = Field(default=False, strict=True)
    branded_content: bool = Field(default=False, strict=True)
    is_aigc: bool = Field(default=False, strict=True)
    consent: bool = Field(strict=True)

    @field_validator("caption")
    @classmethod
    def valid_caption(cls, value):
        if any((ord(c) < 32 and c not in "\n\t") or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("Caption contains unsupported characters.")
        if len(value.encode("utf-16-le")) // 2 > 2200:
            raise ValueError("Caption exceeds TikTok's 2200 UTF-16 character limit.")
        return value

    @model_validator(mode="after")
    def approved(self):
        if not self.consent:
            raise ValueError("Approve the exact video and TikTok posting terms first.")
        if self.commercial != (self.your_brand or self.branded_content):
            raise ValueError("Select the appropriate commercial content disclosure.")
        if self.branded_content and self.privacy == "SELF_ONLY":
            raise ValueError("Branded content cannot use Only me visibility.")
        return self


class PostJob(BaseModel):
    id: str
    export_id: str
    account_id: str
    created_at: str
    request: PostRequest
    status: Literal[
        "queued", "initializing", "uploading", "processing", "published", "failed", "unknown"
    ]
    uploaded_bytes: int = 0
    total_bytes: int
    publish_id: str | None = None
    post_ids: list[str] = Field(default_factory=list)
    error: str | None = None


class TikTok:
    def __init__(self, store, media, settings, web_origin, *, api=None, protector=protect_tokens):
        self.store, self.media, self.settings, self.web_origin = store, media, settings, web_origin
        self.api = api or TikTokAPI(settings)
        self.protector = protector
        self.lock = threading.RLock()
        self.pending = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tiktok")
        self.active = None
        self.stop = threading.Event()
        self.last_status_check = 0.0

    def initialize(self):
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tiktok_account (
                    id INTEGER PRIMARY KEY CHECK (id=1), client_key TEXT NOT NULL,
                    account_id TEXT NOT NULL, credentials BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tiktok_posts (
                    id TEXT PRIMARY KEY, export_id TEXT NOT NULL, account_id TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tiktok_posts_export ON tiktok_posts(export_id,created_at);
            """)
            rows = db.execute(
                "SELECT payload FROM tiktok_posts WHERE status IN ('queued','initializing','uploading','processing')"
            ).fetchall()
            for row in rows:
                job = PostJob.model_validate_json(row[0])
                job.status = "unknown" if job.publish_id else "failed"
                job.error = "Local processing was interrupted. Check TikTok status before making another attempt."
                self._save(job, db=db)

    def _configured(self):
        if not self.settings.client_key or not self.settings.client_secret:
            raise TikTokError(
                "Configure the TikTok developer app before connecting an account. See the application README.",
                503,
            )
        if os.name != "nt":
            raise TikTokError("Local TikTok credential storage currently requires Windows.", 503)

    def status(self):
        with self.lock, self.store.connection() as db:
            row = db.execute(
                "SELECT account_id,client_key FROM tiktok_account WHERE id=1"
            ).fetchone()
            return {
                "configured": bool(
                    self.settings.client_key and self.settings.client_secret and os.name == "nt"
                ),
                "connected": bool(row and row["client_key"] == self.settings.client_key),
                "account_id": row["account_id"]
                if row and row["client_key"] == self.settings.client_key
                else None,
                "audited": self.settings.audited,
            }

    def begin(self, origin):
        self._configured()
        allowed = {self.web_origin, self.web_origin.replace("127.0.0.1", "localhost")}
        if origin not in allowed:
            raise TikTokError("Start account connection from the local app.", 403)
        host = urlsplit(origin).hostname
        redirect = f"http://{host}:8765{CALLBACK_PATH}"
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        with self.lock:
            self.pending = {
                key: item for key, item in self.pending.items() if item[0] > time.monotonic()
            }
            if self.status()["connected"]:
                raise TikTokError(
                    "Disconnect the current TikTok account before connecting another.", 409
                )
            if len(self.pending) >= 4:
                raise TikTokError(
                    "Too many connection attempts. Wait ten minutes and try again.", 429
                )
            self.pending[state] = (time.monotonic() + 600, verifier, redirect, origin)
        query = urlencode(
            {
                "client_key": self.settings.client_key,
                "response_type": "code",
                "scope": "video.publish",
                "redirect_uri": redirect,
                "state": state,
                "code_challenge": hashlib.sha256(verifier.encode()).hexdigest(),
                "code_challenge_method": "S256",
            }
        )
        return state, "https://www.tiktok.com/v2/auth/authorize/?" + query

    def finish(self, state, cookie, code, denied):
        self._configured()
        with self.lock:
            if not state or not cookie or not secrets.compare_digest(state, cookie):
                raise TikTokError("TikTok connection could not be verified. Start again.", 400)
            pending = self.pending.pop(state, None)
            if not pending or pending[0] < time.monotonic():
                raise TikTokError(
                    "TikTok connection expired or was already used. Start again.", 400
                )
            if denied or not code:
                return pending[3] + "/connections?tiktok=cancelled"
            if self.status()["connected"]:
                raise TikTokError("An account is already connected. Disconnect it first.", 409)
            response = self.api.token(
                grant_type="authorization_code",
                code=code,
                code_verifier=pending[1],
                redirect_uri=pending[2],
            )
            self._store_token(response)
            return pending[3] + "/connections?tiktok=connected"

    def _store_token(self, response, expected_account=None):
        try:
            expiry, refresh = response["expires_in"], response["refresh_expires_in"]
            if (
                type(expiry) is not int
                or type(refresh) is not int
                or not 0 < expiry <= 315360000
                or not 0 < refresh <= 315360000
            ):
                raise ValueError("Invalid expiry")
            record = TokenRecord(
                access_token=response["access_token"],
                refresh_token=response["refresh_token"],
                open_id=response["open_id"],
                scope=response["scope"],
                expires_at=time.time() + expiry,
                refresh_expires_at=time.time() + refresh,
            )
            account = hashlib.sha256(
                (self.settings.client_key + ":" + record.open_id).encode()
            ).hexdigest()
            if expected_account and account != expected_account:
                raise ValueError("Account changed")
        except (KeyError, ValueError, TypeError) as exc:
            raise TikTokError("TikTok returned invalid account credentials. Reconnect.") from exc
        encrypted = self.protector(record.model_dump_json().encode())
        with self.store.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO tiktok_account VALUES (1,?,?,?)",
                (self.settings.client_key, account, encrypted),
            )
        return record, account

    def _stored_token(self):
        self._configured()
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM tiktok_account WHERE id=1").fetchone()
        if not row or row["client_key"] != self.settings.client_key:
            raise TikTokError("Connect your TikTok account first.", 409)
        try:
            record = TokenRecord.model_validate_json(
                self.protector(row["credentials"], decrypt=True)
            )
        except (ValueError, OSError) as exc:
            raise TikTokError(
                "Cannot unlock TikTok credentials. Reconnect this account.", 503
            ) from exc
        return record, row["account_id"]

    def _token(self):
        record, account = self._stored_token()
        if record.refresh_expires_at <= time.time():
            raise TikTokError(
                "TikTok authorization expired. Disconnect and reconnect your account.", 409
            )
        if record.expires_at <= time.time() + 60:
            record, _ = self._store_token(
                self.api.token(grant_type="refresh_token", refresh_token=record.refresh_token),
                account,
            )
        return record, account

    def creator(self):
        with self.lock:
            token, account = self._token()
            if "video.publish" not in token.scope.split(","):
                raise TikTokError("Reconnect and authorize TikTok's video.publish permission.", 409)
            response = self.api.request(
                "/v2/post/publish/creator_info/query/", token=token.access_token, body={}
            )
            try:
                creator = Creator.model_validate(response["data"])
            except (KeyError, ValueError) as exc:
                raise TikTokError("TikTok returned invalid creator settings.") from exc
            return {**creator.model_dump(), "account_id": account, "audited": self.settings.audited}

    def disconnect(self):
        with self.lock:
            if self.active is not None and not self.active.done():
                raise TikTokError(
                    "Wait for the active TikTok upload to stop before disconnecting.", 409
                )
            warning = None
            try:
                token, _ = self._stored_token()
                self.api.request(
                    "/v2/oauth/revoke/",
                    form={
                        "client_key": self.settings.client_key,
                        "client_secret": self.settings.client_secret,
                        "token": token.access_token,
                    },
                )
            except TikTokError:
                warning = "Local credentials were removed, but TikTok could not confirm revocation. Remove this app in TikTok's connected-app settings."
            with self.store.connection() as db:
                db.execute("DELETE FROM tiktok_account WHERE id=1")
            self.pending.clear()
        return {"connected": False, "warning": warning}

    def export(self, identifier):
        clip = self.store.clip(identifier)
        path = self.store.export_path(identifier)
        if not clip or clip.status != "succeeded" or path.is_symlink() or not path.is_file():
            raise TikTokError("Choose a completed, available clip export.", 404)
        stat = path.stat()
        if stat.st_size != clip.size_bytes:
            raise TikTokError(
                "The exported file changed. Create another export before posting.", 409
            )
        chunk_plan(stat.st_size)
        return clip, path

    def caption_warning(self, clip):
        if clip.captions:
            transcript = TranscriptStore(self.store).job(str(clip.captions.transcription_id))
            return (
                transcript.warning
                if transcript
                else "The original transcript is unavailable. Review the rendered captions."
            )
        return None

    def _save(self, job, *, db=None):
        if db is None:
            with self.store.connection() as connection:
                return self._save(job, db=connection)
        db.execute(
            "INSERT OR REPLACE INTO tiktok_posts VALUES (?,?,?,?,?,?)",
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
                "SELECT payload FROM tiktok_posts WHERE id=?", (identifier,)
            ).fetchone()
        if not row:
            raise TikTokError("TikTok post not found.", 404)
        return PostJob.model_validate_json(row[0])

    def posts(self, export_id=None):
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT payload FROM tiktok_posts WHERE (? IS NULL OR export_id=?) ORDER BY created_at DESC LIMIT 50",
                (export_id, export_id),
            ).fetchall()
        return [PostJob.model_validate_json(row[0]) for row in rows]

    def validate_post(self, request):
        creator = self.creator()
        if creator["account_id"] != request.account_id:
            raise TikTokError("The destination account changed. Review the post again.", 409)
        clip, path = self.export(str(request.export_id))
        if (clip.duration or clip.end - clip.start) > creator["max_video_post_duration_sec"]:
            raise TikTokError("This clip exceeds the connected account's duration limit.", 422)
        if request.privacy not in creator["privacy_level_options"]:
            raise TikTokError("TikTok privacy options changed. Review the post again.", 409)
        if not self.settings.audited and request.privacy != "SELF_ONLY":
            raise TikTokError("Unaudited apps can post with Only me visibility only.", 422)
        if not self.settings.audited and "PUBLIC_TO_EVERYONE" in creator["privacy_level_options"]:
            raise TikTokError("TikTok requires a private account when testing an unaudited app.", 422)
        for option, blocked in (
            ("allow_comments", "comment_disabled"),
            ("allow_duet", "duet_disabled"),
            ("allow_stitch", "stitch_disabled"),
        ):
            if getattr(request, option) and creator[blocked]:
                raise TikTokError(
                    "An interaction is disabled by this TikTok account. Review its latest settings.", 409
                )
        return clip, path

    def submit(self, request):
        with self.lock:
            with self.store.connection() as db:
                row = db.execute(
                    "SELECT payload FROM tiktok_posts WHERE id=?", (str(request.id),)
                ).fetchone()
            if row:
                existing = PostJob.model_validate_json(row[0])
                if existing.request != request:
                    raise TikTokError(
                        "This posting token belongs to different approved content.", 409
                    )
                return existing
            if self.stop.is_set() or (self.active is not None and not self.active.done()):
                raise TikTokError("Another TikTok upload is active. Wait for it to finish.", 409)
            clip, path = self.validate_post(request)
            recent = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
            with self.store.connection() as db:
                if db.execute(
                    "SELECT 1 FROM tiktok_posts WHERE export_id=? AND account_id=? AND status!='failed'",
                    (str(request.export_id), request.account_id),
                ).fetchone():
                    raise TikTokError(
                        "This export already has a TikTok posting attempt. Check its saved status to avoid duplicates.",
                        409,
                    )
                if (
                    db.execute(
                        "SELECT count(*) FROM tiktok_posts WHERE created_at>=?", (recent,)
                    ).fetchone()[0]
                    >= 6
                ):
                    raise TikTokError("Wait a minute before starting another TikTok post.", 429)
            job = PostJob(
                id=str(request.id),
                export_id=str(request.export_id),
                account_id=request.account_id,
                created_at=now(),
                request=request,
                status="queued",
                total_bytes=path.stat().st_size,
            )
            self._save(job)
            self.active = self.pool.submit(self._run, job.id)
            return job

    def _run(self, identifier):
        job = self.get(identifier)
        try:
            with self.lock:
                token, account = self._token()
                if account != job.account_id:
                    raise TikTokError("The destination account changed before upload.")
                clip, path = self.export(job.export_id)
                info = self.media.probe(path)
                if not (
                    23 <= info.frame_rate <= 60
                    and 360 <= info.width <= 4096
                    and 360 <= info.height <= 4096
                ):
                    raise TikTokError(
                        "TikTok requires 23–60 fps and dimensions between 360 and 4096 pixels. Make a compatible export.",
                        422,
                    )
                job.status = "initializing"
                self._save(job)
                chunk_size, chunks = chunk_plan(job.total_bytes)
                request = job.request
                response = self.api.request(
                    "/v2/post/publish/video/init/",
                    token=token.access_token,
                    body={
                        "post_info": {
                            "title": request.caption,
                            "privacy_level": request.privacy,
                            "disable_comment": not request.allow_comments,
                            "disable_duet": not request.allow_duet,
                            "disable_stitch": not request.allow_stitch,
                            "brand_content_toggle": request.branded_content,
                            "brand_organic_toggle": request.your_brand,
                            "is_aigc": request.is_aigc,
                        },
                        "source_info": {
                            "source": "FILE_UPLOAD",
                            "video_size": job.total_bytes,
                            "chunk_size": chunk_size,
                            "total_chunk_count": len(chunks),
                        },
                    },
                )
                data = response.get("data", {})
                publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
                if not isinstance(publish_id, str) or not 1 <= len(publish_id) <= 64:
                    raise TikTokError("TikTok did not return a valid posting identifier.")
                job.publish_id, job.status = publish_id, "uploading"
                self._save(job)  # Persist reconciliation identity before sending any media.
                if not isinstance(upload_url, str):
                    raise TikTokError("TikTok did not provide an upload destination.")
            deadline = time.monotonic() + 1800
            with path.open("rb") as stream:
                for index, (start, end) in enumerate(chunks):
                    if self.stop.is_set() or time.monotonic() >= deadline:
                        raise TikTokError(
                            "Upload stopped or exceeded its local time budget. Check TikTok status."
                        )
                    self.api.upload_chunk(
                        upload_url,
                        stream,
                        start,
                        end,
                        job.total_bytes,
                        final=index == len(chunks) - 1,
                        stop=self.stop,
                    )
                    with self.lock:
                        job.uploaded_bytes = end
                        self._save(job)
            with self.lock:
                job.status = "processing"
                self._save(job)
        except Exception as exc:
            logger.error(
                "TikTok job %s stopped (%s). Provider payloads were not logged.",
                identifier,
                type(exc).__name__,
            )
            with self.lock:
                job.status = "unknown" if job.publish_id else "failed"
                job.error = (
                    str(exc)
                    if isinstance(exc, TikTokError)
                    else "Local TikTok upload failed. Check storage and the saved posting status."
                )
                self._save(job)

    def refresh(self, identifier):
        with self.lock:
            job = self.get(identifier)
            if (
                job.status in {"published", "failed", "queued", "initializing", "uploading"}
                or not job.publish_id
            ):
                return job
            if time.monotonic() - self.last_status_check < 3:
                return job
            self.last_status_check = time.monotonic()
            token, account = self._token()
            if account != job.account_id:
                raise TikTokError(
                    "Reconnect the original destination account to check this post.", 409
                )
            response = self.api.request(
                "/v2/post/publish/status/fetch/",
                token=token.access_token,
                body={"publish_id": job.publish_id},
            )
            data = response.get("data", {})
            status = data.get("status")
            if status == "PUBLISH_COMPLETE":
                job.status, job.error = "published", None
                ids = data.get("publicaly_available_post_id", [])
                if isinstance(ids, list):
                    job.post_ids = [
                        str(item) for item in ids if str(item).isdigit() and len(str(item)) <= 32
                    ][:10]
            elif status == "FAILED":
                job.status, job.error = (
                    "failed",
                    "TikTok rejected the post. Review the media and account settings before making another attempt.",
                )
            elif status in {"PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD"}:
                # After an interrupted upload, processing does not establish that transfer finished.
                if job.status != "unknown":
                    job.status = "processing"
                job.error = (
                    "TikTok is processing this post. Visibility may take several minutes."
                    if job.status == "processing"
                    else job.error
                )
            else:
                raise TikTokError("TikTok returned an unrecognized posting status.")
            self._save(job)
            return job

    def close(self):
        with self.lock:
            self.stop.set()
        self.pool.shutdown(wait=True)
