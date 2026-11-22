import asyncio
import hashlib
import json
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Protocol
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException

from .config import Settings
from .highlight_models import (
    ACTIVE,
    MAX_AUTO_WINDOW_SECONDS,
    OVERLAP_SECONDS,
    PROMPT_VERSION,
    WINDOW_SECONDS,
    Candidate,
    HighlightCancelled,
    HighlightError,
    HighlightJob,
    HighlightRequest,
    HighlightState,
    ProviderResult,
    ReviewRequest,
)
from .highlight_selection import (
    Segment,
    ground_candidates,
    rank_unique,
    unused_windows,
    window_text,
)
from .highlight_store import HighlightStore
from .storage import Store, now
from .transcription_models import TranscriptionError
from .transcription_store import TranscriptStore

logger = logging.getLogger(__name__)


class HighlightProvider(Protocol):
    async def suggest(self, window: list[Segment], request: HighlightRequest) -> ProviderResult: ...


class Highlights:
    def __init__(
        self,
        store: Store,
        transcripts: TranscriptStore,
        settings: Settings,
        provider: HighlightProvider,
    ):
        self.store, self.transcripts, self.settings, self.provider = (
            store,
            transcripts,
            settings,
            provider,
        )
        self.results = HighlightStore(store)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="highlights")
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.active: str | None = None
        self.active_key: str | None = None
        self.stopping = False

    def initialize(self) -> None:
        self.results.initialize()
        self.results.recover()

    def state(self, recording_id: str) -> HighlightState:
        if self.store.recording(recording_id) is None:
            raise HighlightError("Recording not found.", 404)
        latest = self.results.latest(recording_id)
        return HighlightState(
            latest=latest,
            result=latest
            if latest and latest.status == "succeeded"
            else self.results.latest(recording_id, succeeded=True),
            transcription=self.transcripts.latest(recording_id),
        )

    def submit(
        self, recording_id: str, request: HighlightRequest, *, identifier: str | None = None
    ) -> HighlightJob:
        recording = self.store.recording(recording_id)
        if recording is None:
            raise HighlightError("Recording not found.", 404)
        end = request.analysis_end if request.analysis_end is not None else recording.duration
        if end > recording.duration or end <= request.analysis_start:
            raise HighlightError("Analysis range must be inside the recording.")
        if any(interval.end > recording.duration for interval in request.excluded_ranges):
            raise HighlightError("Excluded sections must be inside the recording.")
        if end - request.analysis_start < request.min_seconds:
            raise HighlightError(
                "This recording is shorter than the requested minimum highlight duration. Lower the minimum or use a longer recording."
            )
        transcript = self.transcripts.latest(recording_id)
        if (
            transcript is None
            or transcript.id != str(request.transcription_id)
            or transcript.status != "succeeded"
            or transcript.revision != request.revision
        ):
            raise HighlightError(
                "Finish transcription and reload its current saved revision before finding highlights.",
                409,
            )
        try:
            snapshot = self.transcripts.words(
                transcript.id,
                request.analysis_start,
                end,
                revision=request.revision,
                recording_id=recording_id,
            )
        except TranscriptionError as exc:
            raise HighlightError(str(exc), exc.status_code) from exc
        words = [w for w in snapshot.words if w.start >= request.analysis_start and w.end <= end]
        windows = unused_windows(
            words,
            recording.duration,
            request,
            self.settings.highlight_max_windows,
            window_seconds=min(
                MAX_AUTO_WINDOW_SECONDS, max(WINDOW_SECONDS, request.max_seconds + OVERLAP_SECONDS)
            )
            if request.duration_mode == "auto"
            else max(WINDOW_SECONDS, request.max_seconds + OVERLAP_SECONDS),
        )
        instruction_bytes = (
            len(json.dumps({"project_instructions": request.ai_instructions}).encode("utf-8"))
            if request.ai_instructions
            else 0
        )
        if (
            sum(len(window_text(window).encode("utf-8")) + instruction_bytes for window in windows)
            > self.settings.highlight_max_input_bytes
        ):
            raise HighlightError(
                "This transcript exceeds the highlight input budget. Use a shorter recording."
            )
        key_data = {
            "prompt": PROMPT_VERSION,
            "model": self.settings.highlight_model,
            "output_tokens": self.settings.highlight_output_tokens,
            "request": request.model_dump(
                mode="json",
                exclude={"regenerate", "remote_consent"}
                | ({"ai_instructions"} if not request.ai_instructions else set())
                | ({"excluded_ranges"} if not request.excluded_ranges else set()),
            ),
        }
        key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
        with self.lock:
            if self.stopping:
                raise HighlightError("Highlight service is stopping. Retry after restart.", 503)
            if self.active:
                if self.active_key == key:
                    current = self.results.job(self.active)
                    if current is None:
                        raise RuntimeError("Active highlight job is missing.")
                    return current
                raise HighlightError(
                    "Another highlight analysis is active. Wait or cancel it first.", 409
                )
            if not request.regenerate and (cached := self.results.cached(key)) and not cached.stale:
                return cached
            if windows and not self.settings.openai_api_key:
                raise HighlightError(
                    "Configure OPENAI_API_KEY in the backend environment and restart the API to enable highlight detection. Manual clipping remains available.",
                    503,
                )
            job = HighlightJob(
                id=identifier or str(uuid4()),
                recording_id=recording_id,
                transcription_id=transcript.id,
                transcript_revision=request.revision,
                model=self.settings.highlight_model,
                status="queued",
                total_windows=len(windows),
                created_at=now(),
            )
            self.results.create(job, key)
            self.active, self.active_key = job.id, key
            self.cancel_event.clear()
            try:
                future = self.pool.submit(self.run, job, windows, request, recording.duration)
            except RuntimeError as exc:
                self.active, self.active_key = None, None
                self.results.update(job.id, status="failed", error="Highlight service is stopping.")
                raise HighlightError("Highlight service is stopping.", 503) from exc
            future.add_done_callback(self._report_failure)
            return job

    @staticmethod
    def _report_failure(future: Future[None]) -> None:
        if not future.cancelled() and future.exception() is not None:
            # Unknown exceptions may contain a request or credentials; never dump them.
            logger.error(
                "Highlight worker could not persist its result. Check local storage availability."
            )

    def cancel(self, identifier: str) -> HighlightJob:
        with self.lock:
            job = self.results.job(identifier)
            if job is None:
                raise HighlightError("Highlight job not found.", 404)
            if self.active != identifier or job.status not in ACTIVE:
                raise HighlightError("This highlight job is no longer processing.", 409)
            self.cancel_event.set()
            self.results.update(identifier, status="cancelling")
            return job.model_copy(update={"status": "cancelling"})

    async def _analyse(
        self,
        job: HighlightJob,
        windows: list[list[Segment]],
        request: HighlightRequest,
        duration: float,
    ) -> tuple[list[Candidate], int]:
        deadline = time.monotonic() + self.settings.highlight_timeout_seconds
        candidates: list[Candidate] = []
        rejected = 0
        for index, window in enumerate(windows):
            self._check(deadline)
            transcript = self.transcripts.latest(job.recording_id)
            if (
                transcript is None
                or transcript.id != job.transcription_id
                or transcript.revision != job.transcript_revision
                or transcript.status != "succeeded"
            ):
                raise HighlightError(
                    "The transcript changed during analysis. Further API requests were stopped; retry using its saved revision.",
                    409,
                )
            # Charge the attempt before sending. Unknown provider outcomes never auto-retry.
            self.results.update(job.id, requests_made=index + 1)
            task = asyncio.create_task(self.provider.suggest(window, request))
            try:
                request_deadline = min(
                    deadline, time.monotonic() + self.settings.highlight_request_seconds
                )
                while not task.done():
                    self._check(request_deadline)
                    await asyncio.wait({task}, timeout=0.1)
                response = await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            self._check(deadline)
            grounded, invalid = ground_candidates(
                response.candidates, window, request, duration, job.id
            )
            candidates.extend(grounded)
            rejected += invalid
            job.input_tokens += response.input_tokens
            job.output_tokens += response.output_tokens
            self.results.update(
                job.id,
                completed_windows=index + 1,
                input_tokens=job.input_tokens,
                output_tokens=job.output_tokens,
                rejected_candidates=rejected,
            )
        if rejected and not candidates:
            raise HighlightError(
                "All suggested moments failed source-evidence or duration checks. Review the transcript or change duration settings, then retry explicitly."
            )
        return rank_unique(candidates, request.max_candidates), rejected

    def _check(self, deadline: float) -> None:
        if self.cancel_event.is_set():
            raise HighlightCancelled()
        if time.monotonic() >= deadline:
            raise HighlightError(
                "Highlight analysis reached its time limit. No partial results were published; check API usage before retrying.",
                503,
            )

    def run(
        self,
        job: HighlightJob,
        windows: list[list[Segment]],
        request: HighlightRequest,
        duration: float,
    ) -> None:
        try:
            with self.lock:
                self._check(float("inf"))
                self.results.update(job.id, status="analysing")
            candidates, rejected = asyncio.run(self._analyse(job, windows, request, duration))
            with self.lock:
                self._check(float("inf"))
                self.results.publish(job, candidates, rejected)
        except HighlightCancelled:
            self.results.update(
                job.id,
                status="cancelled",
                error="Analysis cancelled. In-flight API usage may still be billed; prior results are preserved.",
            )
        except HighlightError as exc:
            self.results.update(job.id, status="failed", error=str(exc))
        except Exception:
            logger.error(
                "Highlight processing failed unexpectedly. No provider payload was logged."
            )
            self.results.update(
                job.id,
                status="failed",
                error="Highlight processing failed unexpectedly. Check backend configuration and local storage before retrying.",
            )
        finally:
            with self.lock:
                self.active, self.active_key = None, None

    def close(self) -> None:
        with self.lock:
            self.stopping = True
            self.cancel_event.set()
        self.pool.shutdown(wait=True)


def create_highlight_router(manager: Highlights) -> APIRouter:
    router = APIRouter()

    def call(operation, *args):
        try:
            return operation(*args)
        except HighlightError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @router.get("/api/highlights/config")
    def config():
        return {
            "configured": bool(manager.settings.openai_api_key),
            "model": manager.settings.highlight_model,
            "max_requests": manager.settings.highlight_max_windows,
            "max_output_tokens_per_request": manager.settings.highlight_output_tokens,
        }

    @router.get("/api/recordings/{recording_id}/highlights", response_model=HighlightState)
    def state(recording_id: UUID):
        return call(manager.state, str(recording_id))

    @router.post(
        "/api/recordings/{recording_id}/highlights", response_model=HighlightJob, status_code=202
    )
    def submit(recording_id: UUID, payload: HighlightRequest):
        return call(manager.submit, str(recording_id), payload)

    @router.post("/api/highlights/{identifier}/cancel", response_model=HighlightJob)
    def cancel(identifier: UUID):
        return call(manager.cancel, str(identifier))

    @router.patch("/api/highlight-candidates/{identifier}", response_model=Candidate)
    def review(identifier: UUID, payload: ReviewRequest):
        return call(manager.results.review, str(identifier), payload)

    return router
