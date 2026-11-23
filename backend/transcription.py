"""Local transcription jobs and their HTTP boundary.

Inference runs in a disposable offline process so cancellation releases model memory.
The parent owns persisted state and validates every result before publishing it.
"""

import errno
import hashlib
import importlib.metadata
import json
import logging
import math
import shutil
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query

from .config import ROOT, TRANSCRIPTION_REPO, Settings
from .files import read_json_snapshot, require_space
from .media import Media, MediaError
from .model_assets import MODEL_FILES
from .models import Recording
from .storage import Store
from .transcription_models import (
    ACTIVE_STATUSES,
    MAX_RESULT_BYTES,
    MAX_WORDS,
    RevisionResponse,
    TranscriptionCancelled,
    TranscriptionError,
    TranscriptionJob,
    WordsPage,
    WordsPatch,
    validate_window,
)
from .transcription_store import TranscriptStore
from .transcription_worker import CONTEXT_SECONDS, SAMPLE_RATE, TRANSCRIBE_OPTIONS, WINDOW_SECONDS
from .warm_transcription import WarmTranscription

logger = logging.getLogger(__name__)
REQUIRED_MODEL_FILES = set(MODEL_FILES)
MAX_LOG_BYTES = 1024**2


class Transcriptions:
    def __init__(self, store: Store, media: Media, settings: Settings):
        self.store, self.media, self.settings = store, media, settings
        self.transcripts = TranscriptStore(store)
        self.work_root = store.root / "transcription-work"
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="transcription")
        self.lock = threading.Lock()
        self.active: str | None = None
        self.cancel_event = threading.Event()
        self.stopping = False
        self.inference = WarmTranscription(
            self.work_root, settings.transcription_worker_idle_seconds
        )

    def initialize(self) -> None:
        self.transcripts.initialize()
        self.work_root.mkdir(parents=True, exist_ok=True)

    def recover(self) -> None:
        self.transcripts.recover()
        for work in self.work_root.iterdir():
            try:
                UUID(work.name)
            except ValueError:
                continue
            self._cleanup(work)

    def _recording(self, recording_id: str) -> Recording:
        recording = self.store.recording(recording_id)
        if recording is None:
            raise TranscriptionError("Recording not found.", 404)
        return recording

    def latest(self, recording_id: str) -> TranscriptionJob | None:
        self._recording(recording_id)
        return self.transcripts.latest(recording_id)

    def current(self) -> TranscriptionJob | None:
        with self.lock:
            return self.transcripts.job(self.active) if self.active else None

    def submit(self, recording_id: str, *, identifier: str | None = None) -> TranscriptionJob:
        recording = self._recording(recording_id)
        if not recording.has_audio:
            raise TranscriptionError("This recording has no audio to transcribe.")
        with self.lock:
            if self.stopping:
                raise TranscriptionError(
                    "Transcription service is stopping. Retry after restart.", 503
                )
            if self.active is not None:
                current = self.transcripts.job(self.active)
                if current and current.recording_id == recording_id:
                    return current
                raise TranscriptionError(
                    "Another recording is transcribing. Wait or cancel it first.", 409
                )
            job = self.transcripts.create(identifier or str(uuid4()), recording_id)
            self.active = job.id
            self.cancel_event.clear()
            try:
                future = self.pool.submit(self.run, job)
            except RuntimeError as exc:
                self.active = None
                self.transcripts.update(
                    job.id, "failed", error="Transcription service is stopping."
                )
                raise TranscriptionError(
                    "Transcription service is stopping. Retry after restart.", 503
                ) from exc
            future.add_done_callback(self._report_failure)
            return job

    @staticmethod
    def _report_failure(future: Future[None]) -> None:
        if not future.cancelled() and (error := future.exception()) is not None:
            logger.error(
                "Transcription worker could not persist or clean up its result",
                exc_info=(type(error), error, error.__traceback__),
            )

    def cancel(self, transcription_id: str) -> TranscriptionJob:
        with self.lock:
            job = self.transcripts.job(transcription_id)
            if job is None:
                raise TranscriptionError("Transcription not found.", 404)
            if self.active != job.id or job.status not in ACTIVE_STATUSES:
                raise TranscriptionError("This transcription is no longer processing.", 409)
            self.cancel_event.set()
            self.transcripts.update(job.id, "cancelling")
            return job.model_copy(update={"status": "cancelling"})

    def words(self, transcription_id: str, start: float, end: float) -> WordsPage:
        job = self.transcripts.job(transcription_id)
        if job is None:
            raise TranscriptionError("Transcription not found.", 404)
        recording = self._recording(job.recording_id)
        validate_window(start, end, recording.duration, self.settings.max_clip_seconds)
        return self.transcripts.words(transcription_id, start, end)

    def patch_words(self, transcription_id: str, request: WordsPatch) -> RevisionResponse:
        return self.transcripts.patch(transcription_id, request)

    def export_words(
        self,
        recording_id: str,
        transcription_id: str,
        revision: int,
        start: float,
        end: float,
    ) -> list[dict]:
        # The export route calls this directly and requires HTTP boundary errors.
        try:
            recording = self._recording(recording_id)
            validate_window(start, end, recording.duration, self.settings.max_clip_seconds)
            page = self.transcripts.words(
                transcription_id,
                start,
                end,
                revision=revision,
                recording_id=recording_id,
            )
            return [word.model_dump() for word in page.words]
        except TranscriptionError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    def _check_running(self, deadline: float) -> None:
        if self.cancel_event.is_set():
            raise TranscriptionCancelled()
        if time.monotonic() >= deadline:
            raise TranscriptionError(
                f"Transcription exceeded the {self.settings.transcription_timeout_seconds:g}-second processing limit."
            )

    def _hash(self, path: Path, deadline: float) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024**2):
                self._check_running(deadline)
                digest.update(chunk)
        return digest.hexdigest()

    def _model_identity(self, deadline: float) -> dict:
        root = self.settings.transcription_model_dir
        try:
            manifest_path = root / "clipping-model.json"
            if manifest_path.stat().st_size > 16_384:
                raise ValueError("manifest size")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = manifest["files"]
            if (
                manifest["repo_id"] != TRANSCRIPTION_REPO
                or manifest["revision"] != self.settings.transcription_model_revision
                or not isinstance(files, dict)
                or files.keys() != MODEL_FILES.keys()
                or {path.name for path in root.iterdir()} != {*MODEL_FILES, "clipping-model.json"}
            ):
                raise ValueError("manifest identity")
            for name, expected in files.items():
                if (
                    not isinstance(name, str)
                    or Path(name).name != name
                    or name in {".", ".."}
                    or not isinstance(expected, str)
                    or len(expected) != 64
                    or any(char not in "0123456789abcdef" for char in expected)
                ):
                    raise ValueError("manifest file")
                path = root / name
                pinned_size, pinned_hash = MODEL_FILES[name]
                if (
                    expected != pinned_hash
                    or not path.is_file()
                    or path.is_symlink()
                    or path.stat().st_size != pinned_size
                    or self._hash(path, deadline) != pinned_hash
                ):
                    raise ValueError("model checksum")
            return {
                "repo_id": manifest["repo_id"],
                "revision": manifest["revision"],
                "files": files,
            }
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise TranscriptionError(
                "The local transcription model is missing or failed verification. Run the documented model setup command, then retry.",
                503,
            ) from exc

    def _cache_key(self, source_hash: str, model: dict) -> str:
        version = subprocess.run(
            [self.settings.ffmpeg, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.splitlines()[0]
        identity = {
            "pipeline": 2,
            "source": source_hash,
            "model": model,
            "runtime": {
                name: importlib.metadata.version(name)
                for name in ("faster-whisper", "ctranslate2", "onnxruntime", "numpy", "tokenizers")
            },
            "ffmpeg": version,
            "device": self.settings.transcription_device,
            "compute_type": "auto-int8"
            if self.settings.transcription_device == "auto"
            else "int8_float16"
            if self.settings.transcription_device == "cuda"
            else "int8",
            "threads": self.settings.transcription_threads,
            "sample_rate": SAMPLE_RATE,
            "window_seconds": WINDOW_SECONDS,
            "context_seconds": CONTEXT_SECONDS,
            "options": TRANSCRIBE_OPTIONS,
        }
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def run(self, job: TranscriptionJob) -> None:
        work = self.work_root / job.id
        deadline = time.monotonic() + self.settings.transcription_timeout_seconds
        try:
            work.mkdir()
            with self.lock:
                self._check_running(deadline)
                self.transcripts.update(job.id, "preparing", progress=0.01)
            recording = self._recording(job.recording_id)
            source = self.store.source_path(recording.id)
            source_stat = source.stat()
            source_hash = self._hash(source, deadline)
            after_hash = source.stat()
            if (source_stat.st_size, source_stat.st_mtime_ns) != (
                after_hash.st_size,
                after_hash.st_mtime_ns,
            ):
                raise TranscriptionError(
                    "The source media changed while reading it. Import it again."
                )
            model = self._model_identity(deadline)
            cache_key = self._cache_key(source_hash, model)
            with self.lock:
                self._check_running(deadline)
                if self.transcripts.reuse(job.id, source_hash, cache_key):
                    return
            info = self.media.probe(source)
            self._check_running(deadline)
            if info.audio_index is None:
                raise TranscriptionError("This recording has no audio to transcribe.")
            if abs(info.duration - recording.duration) > 0.05:
                raise TranscriptionError(
                    "The source media changed. Import it again before transcribing."
                )
            require_space(
                self.store.root,
                self.settings.min_free_bytes,
                math.ceil(recording.duration * SAMPLE_RATE * 2) + 4096,
            )
            log_path = self.store.root / "logs" / f"transcription-{job.id}.log"
            with log_path.open("wb") as log:
                self._decode(source, work, info.audio_index, recording.duration, deadline, log)
                with self.lock:
                    self._check_running(deadline)
                    self.transcripts.update(job.id, "transcribing", progress=0.08)
                request = {
                    "device": self.settings.transcription_device,
                    "work_dir": str(work.resolve()),
                    "model_dir": str(self.settings.transcription_model_dir.resolve()),
                    "threads": self.settings.transcription_threads,
                    "duration": recording.duration,
                }
                request_path = work / "request.json"
                request_path.write_text(json.dumps(request), encoding="utf-8")
                self._process(
                    [
                        sys.executable,
                        "-m",
                        "backend.transcription_worker",
                        str(request_path.resolve()),
                    ],
                    work,
                    recording.duration,
                    deadline,
                    log,
                    transcription_id=job.id,
                )
            current_stat = source.stat()
            if (source_stat.st_size, source_stat.st_mtime_ns) != (
                current_stat.st_size,
                current_stat.st_mtime_ns,
            ):
                raise TranscriptionError(
                    "The source media changed while transcribing. Import it again."
                )
            complete_path = work / "complete.json"
            if complete_path.stat().st_size > 4096:
                raise TranscriptionError("Transcription returned an invalid completion record.")
            complete = json.loads(complete_path.read_text(encoding="utf-8"))
            if not isinstance(complete, dict) or any(
                type(complete.get(field)) is not int or not 0 <= complete[field] <= MAX_WORDS
                for field in ("word_count", "skipped_timing_words", "merged_boundary_words")
            ):
                raise TranscriptionError("Transcription returned an invalid completion record.")
            warnings = []
            from .transcription_models import InferenceProfile

            runtime = (
                InferenceProfile.model_validate(complete["runtime"])
                if "runtime" in complete
                else None
            )
            if runtime and runtime.warning:
                warnings.append(runtime.warning)
            if not complete["word_count"]:
                warnings.append(
                    "No English speech was detected. Check the audio and selected language."
                )
            if complete["skipped_timing_words"]:
                warnings.append(
                    f"{complete['skipped_timing_words']} model words had no usable timing and were omitted. Review the transcript against the audio."
                )
            with self.lock:
                self._check_running(deadline)
                self.transcripts.publish(
                    job.id,
                    source_hash,
                    cache_key,
                    work / "words.ndjson",
                    recording.duration,
                    complete["word_count"],
                    warning=" ".join(warnings) or None,
                    device=runtime.device if runtime else None,
                )
        except TranscriptionCancelled:
            self.inference.close()
            with self.lock:
                self.transcripts.update(job.id, "cancelled")
        except (TranscriptionError, MediaError) as exc:
            self._log_failure(job.id)
            self._fail(job.id, str(exc))
        except OSError as exc:
            logger.exception("Local transcription storage failure for %s", job.id)
            self._log_failure(job.id)
            error = (
                "Not enough free disk space. Free space and retry."
                if exc.errno in {errno.ENOSPC, errno.EDQUOT}
                else "Cannot access local transcription files. Check the processing log and retry."
            )
            self._fail(job.id, error)
        except Exception:
            logger.exception("Unexpected transcription failure for %s", job.id)
            self._log_failure(job.id)
            self._fail(
                job.id,
                f"Cliptric could not finish transcription because of an internal processing error. "
                f"Check the local transcription log (transcription-{job.id}.log) before retrying.",
            )
        finally:
            try:
                self._cleanup(work)
            finally:
                with self.lock:
                    self.active = None

    def _log_failure(self, transcription_id: str) -> None:
        # Parent-side errors must be present in the same local log as worker errors.
        try:
            path = self.store.root / "logs" / f"transcription-{transcription_id}.log"
            with path.open("ab") as log:
                remaining = max(0, MAX_LOG_BYTES - log.tell())
                log.write(traceback.format_exc().encode("utf-8")[:remaining])
        except OSError:
            logger.exception("Could not persist transcription diagnostics for %s", transcription_id)

    def _fail(self, transcription_id: str, message: str) -> None:
        self.inference.close()
        with self.lock:
            if self.cancel_event.is_set():
                self.transcripts.update(transcription_id, "cancelled")
            else:
                self.transcripts.update(transcription_id, "failed", error=message)

    def _decode(
        self, source: Path, work: Path, audio_index: int, duration: float, deadline: float, log
    ) -> None:
        command = [
            self.settings.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-n",
            "-xerror",
            "-max_alloc",
            "268435456",
            "-threads",
            "2",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            "mov,matroska,webm",
            "-copyts",
            "-start_at_zero",
            "-i",
            str(source.resolve()),
            "-map",
            f"0:{audio_index}",
            "-vn",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-af",
            f"aresample={SAMPLE_RATE}:async=1:first_pts=0,apad",
            "-t",
            str(duration),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str((work / "audio.wav").resolve()),
        ]
        self._process(command, work, duration, deadline, log)

    def _process(
        self,
        command: list[str],
        work: Path,
        duration: float,
        deadline: float,
        log,
        *,
        transcription_id: str | None = None,
    ) -> None:
        self._check_running(deadline)
        warm = transcription_id is not None
        process = (
            self.inference.start(work / "request.json")
            if warm
            else subprocess.Popen(
                command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log
            )
        )
        next_check = 0.0
        last_progress = 0.08
        succeeded = False
        try:
            while process.poll() is None and not (
                warm
                and ((work / "complete.json").exists() or (work / "worker_failed.json").exists())
            ):
                self.cancel_event.wait(0.15)
                self._check_running(deadline)
                if time.monotonic() >= next_check:
                    require_space(self.store.root, self.settings.min_free_bytes)
                    limits = {
                        "audio.wav": math.ceil(duration * SAMPLE_RATE * 2) + 4096,
                        "words.ndjson": MAX_RESULT_BYTES,
                        "complete.json": 4096,
                        "worker-error.log": 16_384,
                        "worker_failed.json": 4096,
                    }
                    if log.tell() > MAX_LOG_BYTES or any(
                        (path := work / name).exists() and path.stat().st_size > limit
                        for name, limit in limits.items()
                    ):
                        raise TranscriptionError(
                            "Transcription exceeded its output or diagnostic size limit."
                        )
                    value = read_json_snapshot(work / "progress.json") if transcription_id else None
                    if value is not None:
                        seconds = value.get("seconds") if isinstance(value, dict) else None
                        if (
                            type(seconds) not in (int, float)
                            or not math.isfinite(seconds)
                            or not 0 <= seconds <= duration + 0.05
                        ):
                            raise TranscriptionError("Transcription returned invalid progress.")
                        progress = max(last_progress, min(0.98, 0.08 + 0.90 * seconds / duration))
                        if progress - last_progress >= 0.005:
                            with self.lock:
                                self._check_running(deadline)
                                self.transcripts.update(
                                    transcription_id, "transcribing", progress=progress
                                )
                            last_progress = progress
                    next_check = time.monotonic() + 1
            self._check_running(deadline)
            if process.returncode or (warm and (work / "worker_failed.json").exists()):
                if warm and (work / "worker-error.log").is_file():
                    log.write((work / "worker-error.log").read_bytes()[:16_384])
                raise TranscriptionError(
                    f"The speech worker stopped before transcription completed. "
                    f"Check the local transcription log (transcription-{transcription_id}.log) "
                    f"for the model or audio error, then retry transcription."
                )
            succeeded = True
            if warm:
                self.inference.mark_idle()
        finally:
            if warm:
                if not succeeded:
                    self.inference.close()
            else:
                if process.poll() is None:
                    process.kill()
                process.wait()

    def _cleanup(self, work: Path) -> None:
        try:
            if work.is_symlink() or work.parent.resolve() != self.work_root.resolve():
                raise OSError("Unsafe transcription working directory")
            if work.exists():
                shutil.rmtree(work)
        except OSError:
            logger.exception("Could not remove transcription temporary files at %s", work)

    def close(self) -> None:
        with self.lock:
            self.stopping = True
            self.cancel_event.set()
        self.pool.shutdown(wait=True)
        self.inference.close()


def create_transcription_router(manager: Transcriptions) -> APIRouter:
    router = APIRouter()

    def call(operation, *args):
        try:
            return operation(*args)
        except TranscriptionError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @router.get("/api/transcriptions/active", response_model=TranscriptionJob | None)
    def active():
        return manager.current()

    @router.get(
        "/api/recordings/{recording_id}/transcription", response_model=TranscriptionJob | None
    )
    def latest(recording_id: UUID):
        return call(manager.latest, str(recording_id))

    @router.post(
        "/api/recordings/{recording_id}/transcription",
        response_model=TranscriptionJob,
        status_code=202,
    )
    def submit(recording_id: UUID):
        return call(manager.submit, str(recording_id))

    @router.post("/api/transcriptions/{transcription_id}/cancel", response_model=TranscriptionJob)
    def cancel(transcription_id: UUID):
        return call(manager.cancel, str(transcription_id))

    @router.get("/api/transcriptions/{transcription_id}/words", response_model=WordsPage)
    def words(transcription_id: UUID, start: float = Query(ge=0), end: float = Query(gt=0)):
        return call(manager.words, str(transcription_id), start, end)

    @router.patch("/api/transcriptions/{transcription_id}/words", response_model=RevisionResponse)
    def patch_words(transcription_id: UUID, payload: WordsPatch):
        return call(manager.patch_words, str(transcription_id), payload)

    return router
