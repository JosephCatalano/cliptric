import asyncio
import hashlib
import json
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable, Coroutine
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Protocol, TypeVar
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException

from .config import Settings
from .editorial_review import MAX_RESULTS, EditorialReviewer, ground_review, shortlist
from .highlight_context import Chapter, build_context, ground_narrative, nearby_context
from .highlight_media import MEDIA_VERSION, MediaSampler, sample_ranges, vision_prompt, visual_leads
from .highlight_models import (
    ACTIVE,
    DISCOVERY_VERSION,
    MAX_COVERAGE_WINDOW_SECONDS,
    OVERLAP_SECONDS,
    PROMPT_VERSION,
    WINDOW_SECONDS,
    Candidate,
    DiscoveryProposal,
    HighlightCancelled,
    HighlightError,
    HighlightJob,
    HighlightRequest,
    HighlightState,
    ProviderResult,
    ReviewRequest,
    SourceRange,
)
from .highlight_selection import (
    Segment,
    ground_candidates,
    overlaps,
    rank_unique,
    unused_windows,
    window_text,
)
from .highlight_store import HighlightStore
from .media import MediaError
from .prompt_settings import highlight_prompt_identity
from .storage import Store, now
from .transcription_models import TranscriptionError
from .transcription_store import TranscriptStore
from .twitch_rights import processing_error

logger = logging.getLogger(__name__)
Response = TypeVar("Response")


class HighlightProvider(Protocol):
    async def suggest(
        self, window: list[Segment], request: HighlightRequest, *, context: dict
    ) -> ProviderResult: ...


class Highlights:
    def __init__(
        self,
        store: Store,
        transcripts: TranscriptStore,
        settings: Settings,
        provider: HighlightProvider,
        *,
        reviewer: EditorialReviewer | None = None,
    ):
        self.store, self.transcripts, self.settings, self.provider = (
            store,
            transcripts,
            settings,
            provider,
        )
        self.results = HighlightStore(store)
        self.reviewer = reviewer or EditorialReviewer(settings)
        self.sampler = MediaSampler(settings)
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
        if blocked := processing_error(self.store, recording_id, ai=True):
            raise HighlightError(blocked, 403)
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
            window_seconds=max(
                WINDOW_SECONDS,
                request.min_seconds + OVERLAP_SECONDS,
                min(
                    MAX_COVERAGE_WINDOW_SECONDS,
                    (end - request.analysis_start) / self.settings.highlight_max_windows
                    + OVERLAP_SECONDS,
                ),
            ),
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
        with self.lock:
            media_identity = (
                [
                    MEDIA_VERSION,
                    self.settings.highlight_vision_model,
                    self.settings.highlight_media_samples,
                    self.settings.highlight_media_seconds,
                    hashlib.sha256(vision_prompt(self.settings).encode()).hexdigest(),
                ]
                if request.multimodal
                else None
            )
            discovery_prompt = highlight_prompt_identity(self.settings, discovery=True)
            discovery_key = hashlib.sha256(
                json.dumps(
                    {
                        "version": DISCOVERY_VERSION,
                        "recording": recording_id,
                        "prompt": discovery_prompt,
                        "model": self.settings.highlight_model,
                        "output_tokens": self.settings.highlight_output_tokens,
                        "media": media_identity,
                        "input": request.model_dump(
                            mode="json",
                            exclude={
                                "regenerate",
                                "remote_consent",
                                "max_candidates",
                                "quality_mode",
                            },
                        ),
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            key_data = {
                "prompt": PROMPT_VERSION,
                "prompt_settings": [
                    discovery_prompt,
                    highlight_prompt_identity(self.settings, discovery=False),
                ],
                "model": self.settings.highlight_model,
                "media": media_identity,
                "output_tokens": self.settings.highlight_output_tokens,
                "pool_size": self.settings.highlight_pool_size,
                "dedup": [
                    self.settings.highlight_duplicate_iou,
                    self.settings.highlight_duplicate_evidence,
                ],
                "request": request.model_dump(
                    mode="json",
                    exclude={"regenerate", "remote_consent"}
                    | ({"ai_instructions"} if not request.ai_instructions else set())
                    | ({"excluded_ranges"} if not request.excluded_ranges else set()),
                ),
            }
            key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
            pool_data = {
                **key_data,
                "pool_policy": 1,
                "request": request.model_dump(
                    mode="json",
                    exclude={"max_candidates", "regenerate", "remote_consent", "excluded_ranges"},
                ),
            }
            pool_key = hashlib.sha256(json.dumps(pool_data, sort_keys=True).encode()).hexdigest()
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
            available = []
            if not request.regenerate and request.excluded_ranges:
                blocked = list(request.excluded_ranges)
                for candidate in self.results.pool(pool_key):
                    if not overlaps(candidate.start, candidate.end, blocked):
                        available.append(candidate)
                        blocked.append(SourceRange(start=candidate.start, end=candidate.end))
                        if len(available) >= request.max_candidates:
                            break
            if available and (
                len(available) == request.max_candidates
                or (not windows and not request.multimodal)
                or not self.settings.openai_api_key
            ):
                job = HighlightJob(
                    id=identifier or str(uuid4()),
                    recording_id=recording_id,
                    transcription_id=transcript.id,
                    transcript_revision=transcript.revision,
                    model=self.settings.highlight_model,
                    status="queued",
                    total_windows=0,
                    created_at=now(),
                    pool_key=pool_key,
                    cache_hit=True,
                    editorial_status="completed",
                )
                self.results.create(job, key)
                copies = [
                    candidate.model_copy(
                        update={
                            "id": str(uuid4()),
                            "job_id": job.id,
                            "review": "unreviewed",
                            "revision": 0,
                        }
                    )
                    for candidate in available
                ]
                self.results.publish(job, copies, 0)
                return self.results.job(job.id)
            if (windows or request.multimodal) and not self.settings.openai_api_key:
                raise HighlightError(
                    "Configure OPENAI_API_KEY in the backend environment and restart the API to enable highlight detection. Manual clipping remains available.",
                    503,
                )
            job = HighlightJob(
                pool_key=pool_key,
                id=identifier or str(uuid4()),
                recording_id=recording_id,
                transcription_id=transcript.id,
                transcript_revision=request.revision,
                model=self.settings.highlight_model,
                status="queued",
                total_windows=len(windows),
                editorial_status="pending",
                created_at=now(),
            )
            self.results.create(job, key)
            self.active, self.active_key = job.id, key
            self.cancel_event.clear()
            try:
                future = self.pool.submit(
                    self.run, job, windows, request, recording.duration, discovery_key
                )
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

    async def _discover(
        self,
        job: HighlightJob,
        windows: list[list[Segment]],
        request: HighlightRequest,
        duration: float,
        deadline: float,
        chapters: list[Chapter],
        signals: list[dict],
    ) -> tuple[list[Candidate], Counter[str], list[dict]]:
        queue = asyncio.Queue()
        for index, window in enumerate(windows):
            queue.put_nowait((index, window))
        results = {}
        narrative = {}
        rejected: Counter[str] = Counter()
        discovery_request = request.model_copy(update={"quality_mode": False, "max_candidates": 5})

        async def worker():
            while not queue.empty():
                index, window = queue.get_nowait()
                # Retain a modest pool without changing the number of requested exports.
                start, end = window[0].start, max(segment.end for segment in window)
                context = nearby_context(chapters, start, end)
                if signals:
                    context["sampled_signals"] = [
                        cue for cue in signals if cue["start"] < end and cue["end"] > start
                    ][:8]
                response = await self._provider_attempt(
                    job,
                    deadline,
                    lambda: self.provider.suggest(window, discovery_request, context=context),
                )
                job.input_tokens += response.input_tokens
                job.output_tokens += response.output_tokens
                grounded, _ = ground_candidates(
                    response.candidates,
                    window,
                    discovery_request,
                    duration,
                    job.id,
                    rejection_reasons=rejected,
                )
                results[index] = grounded
                if isinstance(response.candidates, DiscoveryProposal):
                    narrative[index] = ground_narrative(response.candidates.narrative, window)
                job.completed_windows += 1
                self.results.update(
                    job.id,
                    completed_windows=job.completed_windows,
                    input_tokens=job.input_tokens,
                    output_tokens=job.output_tokens,
                    rejected_candidates=rejected.total(),
                    discovery_rejections=dict(rejected),
                )

        tasks = [
            asyncio.create_task(worker())
            for _ in range(min(len(windows), self.settings.highlight_concurrency))
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        candidates = [candidate for index in sorted(results) for candidate in results[index]]
        if rejected:
            logger.warning(
                "Highlight job %s rejected discovery candidates by reason: %s.",
                job.id,
                dict(rejected),
            )
        notes = {}
        for index in sorted(narrative):
            for note in narrative[index]:
                key = (*note["segment_ids"], note["role"])
                notes.setdefault(key, note)
        return candidates, rejected, list(notes.values())

    async def _media_evidence(
        self, job: HighlightJob, request: HighlightRequest, duration: float, deadline: float
    ) -> tuple[list[Candidate], list[dict]]:
        if not request.multimodal:
            return [], []
        if not sample_ranges(request, duration, self.settings.highlight_media_samples):
            return [], []
        self.results.update(job.id, media_status="sampling")
        leads, signals = [], []
        warning = None
        status = "unavailable"
        try:
            samples, signals, incomplete = self.sampler.sample(
                self.store.source_path(job.recording_id),
                request,
                duration,
                lambda: self._check(deadline),
                self.cancel_event,
            )
            if samples:
                self.results.update(
                    job.id,
                    media_status="reviewing",
                    vision_model=self.settings.highlight_vision_model,
                )
                observations, inputs, outputs = await self._provider_attempt(
                    job, deadline, lambda: self.sampler.describe(samples, request)
                )
                job.input_tokens += inputs
                job.output_tokens += outputs
                job.vision_input_tokens = inputs
                job.vision_output_tokens = outputs
                leads, visual_signals = visual_leads(observations, samples, job.id)
                if observations.observations and not visual_signals:
                    raise HighlightError(
                        "Frame observations did not reference usable sampled evidence."
                    )
                signals.extend(visual_signals)
                status = "ready"
                if incomplete:
                    warning = "Some audio or frame samples could not be collected."
            else:
                warning = "No usable frame pairs could be collected in the analysis range."
        except (MediaError, OSError, HighlightError) as exc:
            self._check(deadline)
            self._check_revision(job)
            logger.warning(
                "Optional highlight media analysis unavailable for %s (%s).",
                job.id,
                type(exc).__name__,
            )
            warning = "Sampled visual analysis was unavailable. If a request reached OpenAI, check its usage before retrying."
        job.media_status = status
        job.media_warning = warning
        self.results.update(
            job.id,
            media_status=status,
            media_warning=warning,
            input_tokens=job.input_tokens,
            output_tokens=job.output_tokens,
            vision_input_tokens=job.vision_input_tokens,
            vision_output_tokens=job.vision_output_tokens,
        )
        return leads, signals

    async def _analyse(
        self,
        job: HighlightJob,
        windows: list[list[Segment]],
        request: HighlightRequest,
        duration: float,
        discovery_key: str,
    ) -> tuple[list[Candidate], int]:
        deadline = time.monotonic() + self.settings.highlight_timeout_seconds
        chapters = build_context(windows)
        saved = (
            None if request.regenerate else self.results.discovery(job.recording_id, discovery_key)
        )
        if saved is None:
            visual, signals = await self._media_evidence(job, request, duration, deadline)
            if not windows and job.media_status == "unavailable":
                raise HighlightError(
                    "This recording has no usable speech and frame analysis was unavailable. Try again or select a clip manually."
                )
            leads, discovery_rejections, notes = await self._discover(
                job, windows, request, duration, deadline, chapters, signals
            )
            signals = notes + signals
            leads.extend(visual)
            if discovery_rejections and not leads:
                raise HighlightError(
                    "All suggested moments failed source-evidence or duration checks. Review the transcript or change duration settings, then retry explicitly."
                )
            self._check_revision(job)
            self.results.save_discovery(
                job, discovery_key, leads, dict(discovery_rejections), signals
            )
        else:
            leads, saved_rejections, signals, media_status, media_warning = saved
            discovery_rejections = Counter(saved_rejections)
            self.results.update(
                job.id,
                discovery_cache_hit=True,
                completed_windows=len(windows),
                rejected_candidates=discovery_rejections.total(),
                discovery_rejections=saved_rejections,
                media_status=media_status,
                media_warning=media_warning,
            )
        self._check(deadline)
        rejected = discovery_rejections.total()
        if not leads:
            return [], rejected

        ranked = self._rank(leads, len(leads), exact_only=True)
        options, payload = shortlist(ranked, windows, request, chapters, signals)
        self.results.update(
            job.id,
            editorial_status="reviewing",
            discovered_candidates=len(leads),
            editorial_options=len(options),
            discovery_duplicates=len(leads) - len(ranked),
            discovery_shortlist=[
                {"start": option.lead.start, "end": option.lead.end} for option in options
            ],
        )
        limit = min(
            max(request.max_candidates, self.settings.highlight_pool_size),
            len(options),
            MAX_RESULTS,
        )
        try:
            proposal, inputs, outputs = await self._provider_attempt(
                job, deadline, lambda: self.reviewer.review(payload, request, limit)
            )
        except HighlightError as exc:
            raise HighlightError(f"Editorial review failed. {exc}", exc.status_code) from exc
        job.input_tokens += inputs
        job.output_tokens += outputs
        self.results.update(job.id, input_tokens=job.input_tokens, output_tokens=job.output_tokens)
        candidates, editorial_rejections = ground_review(
            proposal, options, request, duration, job.id, limit
        )
        rejected += editorial_rejections.total()
        ranked = self._rank(candidates, limit)
        self.results.update(
            job.id,
            editorial_proposals=len(proposal.candidates),
            editorial_duplicates=len(candidates) - len(ranked),
            editorial_rejections=dict(editorial_rejections),
            rejected_candidates=rejected,
        )
        if editorial_rejections:
            logger.warning(
                "Highlight job %s rejected editorial candidates by reason: %s.",
                job.id,
                dict(editorial_rejections),
            )
        invalid_editorial = editorial_rejections.copy()
        invalid_editorial.pop("editorial_gate", None)
        if invalid_editorial and not candidates:
            reason, count = invalid_editorial.most_common(1)[0]
            explanation = {
                "unknown_option": "the review referenced a clip that was outside its shortlist",
                "reused_option": "the review submitted the same shortlist option more than once",
                "segment_ids": "the proposed timestamps did not match available transcript segments",
                "quality": "the proposals did not meet the Quality mode requirements",
                "mid_thought_boundary": "the proposals split a continuous sentence",
                "boundary_evidence": "the opening or closing evidence did not match the transcript",
                "duration": "the proposals did not fit the allowed clip duration",
                "source_bounds": "the proposals were outside the selected source range",
                "evidence": "the main evidence quotes did not match the selected transcript text",
                "excluded_range": "the proposals overlapped a used or excluded section",
                "outside_original_lead": "the proposed cuts moved outside their shortlisted moments",
            }.get(reason, "the proposals did not pass source validation")
            raise HighlightError(
                f"Editorial review found no valid clips. Most common reason ({count} of "
                f"{len(proposal.candidates)} proposed): {explanation}. "
                "Review the saved AI usage before approving a retry, or choose a clip manually."
            )
        return ranked, rejected

    def _rank(
        self, candidates: list[Candidate], limit: int, *, exact_only: bool = False
    ) -> list[Candidate]:
        return rank_unique(
            candidates,
            limit,
            iou_threshold=self.settings.highlight_duplicate_iou,
            evidence_threshold=self.settings.highlight_duplicate_evidence,
            exact_only=exact_only,
        )

    def _check_revision(self, job: HighlightJob) -> None:
        current = self.transcripts.latest(job.recording_id)
        if (
            current is None
            or current.id != job.transcription_id
            or current.revision != job.transcript_revision
            or current.status != "succeeded"
        ):
            raise HighlightError(
                "The transcript changed during analysis. Further API requests were stopped; retry using its saved revision.",
                409,
            )

    async def _provider_attempt(
        self,
        job: HighlightJob,
        deadline: float,
        invoke: Callable[[], Coroutine[Any, Any, Response]],
    ) -> Response:
        self._check(deadline)
        self._check_revision(job)
        job.requests_made += 1
        self.results.update(job.id, requests_made=job.requests_made)
        task = asyncio.create_task(invoke())
        try:
            request_deadline = min(
                deadline, time.monotonic() + self.settings.highlight_request_seconds
            )
            while not task.done():
                self._check(request_deadline)
                await asyncio.wait({task}, timeout=0.1)
            response = await task
            self._check(deadline)
            self._check_revision(job)
            return response
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

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
        discovery_key: str,
    ) -> None:
        started = time.monotonic()
        try:
            if blocked := processing_error(self.store, job.recording_id, ai=True):
                raise HighlightError(blocked, 403)
            with self.lock:
                self._check(float("inf"))
                self.results.update(job.id, status="analysing")
            candidates, rejected = asyncio.run(
                self._analyse(job, windows, request, duration, discovery_key)
            )
            with self.lock:
                self._check(float("inf"))
                previous = (
                    [
                        candidate
                        for candidate in self.results.pool(job.pool_key)
                        if not overlaps(candidate.start, candidate.end, request.excluded_ranges)
                    ]
                    if job.pool_key
                    else []
                )
                pool = self._rank(candidates + previous, self.settings.highlight_pool_size)
                self.results.update(job.id, elapsed_seconds=round(time.monotonic() - started, 3))
                self.results.publish(job, candidates[: request.max_candidates], rejected, pool=pool)
        except HighlightCancelled:
            self.results.update(
                job.id,
                status="cancelled",
                error="Analysis cancelled. In-flight API usage may still be billed; prior results are preserved.",
            )
        except HighlightError as exc:
            logger.warning("Highlight job %s failed with status %d.", job.id, exc.status_code)
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
