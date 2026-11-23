"""Persistent, bounded local project workflow around the existing media services."""

import asyncio
import hashlib
import json
import logging
import math
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from .batch_review import MAX_REVIEW_BYTES, MAX_REVIEW_WORDS, BatchReview, MultiBatchReview
from .batch_titles import BatchTitles
from .caption_styles import BatchStyle
from .editing import QualityOptions, mapped_overlays, mapped_words, retained_intervals
from .files import remove_artifact
from .headlines import HeadlineOptions
from .highlight_models import ACTIVE as HIGHLIGHT_ACTIVE
from .highlight_models import HighlightError, HighlightRequest, ProposedHighlights, SourceRange
from .highlight_selection import (
    build_segments,
    ground_candidates,
    meets_quality,
    overlaps,
    rank_unique,
    window_text,
)
from .hooks import suggest_hooks
from .jobs import ExportBusy, Jobs
from .media import MediaError, RenderCancelled
from .models import CaptionOptions, ExportRequest
from .overlays import layout
from .presentation import AutoPresentation
from .project_models import (
    ACTIVE,
    ItemEdit,
    MoreClipsRequest,
    Project,
    ProjectCreate,
    ProjectItem,
    ResumeRequest,
)
from .project_store import ProjectStore
from .social_copy import SocialCopyWriter
from .storage import now
from .transcription_models import ACTIVE_STATUSES, TranscriptionError
from .youtube_imports import ACTIVE as DOWNLOAD_ACTIVE
from .youtube_imports import YouTubeRequest

logger = logging.getLogger(__name__)


class ProjectStopped(Exception):
    pass


class Projects:
    def __init__(self, store, media, transcriptions, highlights, jobs: Jobs, youtube):
        self.store, self.media = store, media
        self.transcriptions, self.highlights, self.jobs = transcriptions, highlights, jobs
        self.youtube = youtube
        self.titles = BatchTitles(media.settings)
        self.copy_writer = SocialCopyWriter(media.settings)
        self.copy_active: set[tuple[str, str]] = set()
        self.batch_review = BatchReview(media.settings)
        self.batch_highlights = MultiBatchReview(media.settings)
        self.presentation = AutoPresentation(media)
        self.records = ProjectStore(store)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="projects")
        self.scheduled: dict[str, Future] = {}
        self.root = store.root / "thumbnails"
        self.root.mkdir(exist_ok=True)

    def initialize(self):
        self.records.initialize()
        self.presentation.initialize()
        for path in self.root.glob("*.partial.jpg"):
            remove_artifact(path)

    def get(self, identifier: str, *, hydrate=True) -> Project:
        with self.lock:
            project = self.records.get(identifier)
            if project is None:
                raise HTTPException(404, "Project not found.")
            if hydrate:
                if project.status == "downloading" and project.download_id:
                    try:
                        download = self.youtube.get(project.download_id)
                    except HTTPException as exc:
                        # The durable child ID is saved just before submission.
                        if exc.status_code != 404:
                            raise
                        download = None
                    if download is None:
                        project.stage = "Starting source download"
                    elif download.status == "processing":
                        project.stage = "Preparing downloaded video"
                    elif download.total_bytes:
                        fraction = min(download.downloaded_bytes / download.total_bytes, 1)
                        project.progress = fraction * 0.25
                        project.step_progress = fraction
                        project.stage = f"Downloading video: {fraction:.0%}"
                    else:
                        project.stage = (
                            f"Downloading video: {download.downloaded_bytes / 1024**2:.1f} MB"
                        )
                if project.transcription_id:
                    transcription = self.transcriptions.transcripts.job(project.transcription_id)
                    project.transcription_warning = transcription.warning if transcription else None
                    if (
                        project.status == "transcribing"
                        and transcription
                        and transcription.status in ACTIVE_STATUSES
                    ):
                        project.stage = (
                            f"Transcription: {transcription.status}, {transcription.progress:.0%}"
                        )
                        project.progress = 0.25 + 0.25 * transcription.progress
                        project.step_progress = transcription.progress
                if project.status == "analysing" and project.highlight_id:
                    analysis = self.highlights.results.job(project.highlight_id)
                    if analysis:
                        project.stage = f"AI analysis: {analysis.completed_windows} of {analysis.total_windows} windows completed"
                        if analysis.total_windows:
                            windows_done = min(
                                1, analysis.completed_windows / analysis.total_windows
                            )
                            project.step_progress = (
                                0.9 * windows_done
                                if analysis.editorial_status in {"pending", "reviewing"}
                                else windows_done
                            )
                            project.progress = 0.5 + 0.25 * project.step_progress
                        if analysis.editorial_status == "reviewing":
                            project.stage = "AI editorial review: comparing promising moments"
                for item in project.items:
                    item.post_copy_stale = bool(
                        (item.post_copy or item.post_copy_selected)
                        and item.post_copy_source != self.copy_source(project, item)
                    )
                    identifier = item.pending_export_id or item.export_id
                    item.export = self.store.clip(identifier) if identifier else None
                    if (
                        item.export
                        and item.export.status == "succeeded"
                        and not self.store.export_path(item.export.id).is_file()
                    ):
                        item.export.status = "failed"
                        item.export.error = "The export file is missing. Resume to render it again."
                active_export = next(
                    (
                        i.export
                        for i in project.items
                        if i.export and i.export.status in {"queued", "rendering", "cancelling"}
                    ),
                    None,
                )
                if active_export and project.status == "rendering":
                    project.step_progress = active_export.render_progress
                    project.step_updated_at = (
                        active_export.progress_updated_at or project.step_updated_at
                    )
                    project.stage = (
                        "Encoding video"
                        if active_export.render_progress is not None
                        else "Preparing export: layout, tracking, captions and audio"
                    )
                if project.status == "rendering" or (
                    project.recording_ids and project.status in ACTIVE
                ):
                    eligible = [item for item in project.items if item.review != "rejected"]
                    completed = sum(
                        bool(item.export and item.export.status == "succeeded" and not item.error)
                        for item in eligible
                    )
                    project.progress = (
                        (completed / max(1, len(eligible)))
                        if project.recording_ids
                        else 0.75 + 0.25 * completed / max(1, len(eligible))
                    )
            return project

    @contextmanager
    def _timed(self, identifier, stage):
        started = time.monotonic()
        try:
            yield
        finally:
            with self.lock:
                project = self.get(identifier, hydrate=False)
                project.stage_seconds[stage] = (
                    project.stage_seconds.get(stage, 0) + time.monotonic() - started
                )
                self.records.save(project)

    def _capacity(self):
        if self.stop.is_set():
            raise HTTPException(503, "Project service is stopping.")
        if len(self.scheduled) >= 8:
            raise HTTPException(
                409, "The eight-project processing queue is full. Wait or cancel a project."
            )

    def _queue(self, project: Project):
        project.status, project.stage, project.error = "queued", "Waiting for processing", None
        project.progress = 0
        project.stage_seconds = {}
        self.records.save(project)
        if project.id in self.scheduled:
            return
        try:
            future = self.pool.submit(self.run, project.id)
            self.scheduled[project.id] = future
            future.add_done_callback(lambda result: self._completed(project.id, result))
        except RuntimeError as exc:
            project.status, project.error = (
                "interrupted",
                "Project service stopped. Resume after restart.",
            )
            self.records.save(project)
            raise HTTPException(503, project.error) from exc

    def _completed(self, identifier: str, future: Future):
        if not future.cancelled() and future.exception() is not None:
            logger.error("Project worker could not persist its result. Check local storage.")
        with self.lock:
            self.scheduled.pop(identifier, None)
            project = self.get(identifier, hydrate=False)
            if not self.stop.is_set() and project.status == "queued":
                self._queue(project)

    def create(self, request: ProjectCreate):
        with self.lock:
            existing = self.records.get(str(request.id))
            if existing:
                source_matches = (
                    existing.youtube_source_id == str(request.youtube_source_id)
                    if request.youtube_source_id
                    else existing.youtube_source_id is None
                    and existing.recording_id == str(request.recording_id)
                )
                if existing.recording_ids or request.recording_ids:
                    source_matches = existing.recording_ids == [
                        str(value) for value in request.recording_ids
                    ]
                if (
                    not source_matches
                    or existing.generate_titles != request.generate_titles
                    or existing.generate_post_copy != request.generate_post_copy
                    or (existing.title, existing.settings)
                    != (
                        request.title,
                        request.settings,
                    )
                ):
                    raise HTTPException(
                        409, "This creation token already belongs to different project settings."
                    )
                return self.get(existing.id)
            if request.recording_ids:
                return self._create_batch(request)
            recording = (
                self.youtube.source(str(request.youtube_source_id))
                if request.youtube_source_id
                else self.store.recording(str(request.recording_id))
            )
            if not recording:
                raise HTTPException(404, "Recording not found.")
            if not recording.has_audio:
                raise HTTPException(
                    422,
                    "Automatic spoken highlights require a recording with audio. Use the manual studio for silent video.",
                )
            if request.settings.analysis_end > recording.duration:
                raise HTTPException(422, "Analysis range exceeds the source duration.")
            self._capacity()
            project = Project(
                generate_post_copy=request.generate_post_copy,
                id=str(request.id),
                recording_id=recording.id if request.recording_id else None,
                youtube_source_id=str(request.youtube_source_id)
                if request.youtube_source_id
                else None,
                source_thumbnail_url=recording.thumbnail_url if request.youtube_source_id else None,
                title=request.title,
                created_at=now(),
                settings=request.settings,
                status="queued",
            )
            self._queue(project)
            return project

    def more_clips(self, identifier: str, request: MoreClipsRequest):
        with self.lock:
            existing = self.records.get(str(request.id))
            if existing:
                if (
                    existing.parent_project_id != identifier
                    or existing.settings.max_clips != request.max_clips
                ):
                    raise HTTPException(
                        409, "This batch token belongs to a different project or clip count."
                    )
                return self.get(existing.id)
            source = self.get(identifier, hydrate=False)
            if source.recording_ids or not source.recording_id or not source.selection_complete:
                raise HTTPException(409, "Finish a long-form project before finding another batch.")
            if source.status in ACTIVE:
                raise HTTPException(409, "Wait for this project to stop before finding more clips.")
            if not self.store.source_path(source.recording_id).is_file():
                raise HTTPException(404, "The saved source video is unavailable.")
            self._capacity()
            project = Project(
                generate_post_copy=source.generate_post_copy,
                id=str(request.id),
                parent_project_id=source.id,
                avoid_used_clips=True,
                recording_id=source.recording_id,
                source_thumbnail_url=source.source_thumbnail_url,
                title=source.title.removesuffix(" - more clips")[:107] + " - more clips",
                settings=source.settings.model_copy(
                    deep=True, update={"max_clips": request.max_clips}
                ),
                created_at=now(),
                status="queued",
            )
            self._queue(project)
            return project

    def _create_batch(self, request: ProjectCreate):
        recordings = [self.store.recording(str(value)) for value in request.recording_ids]
        if any(recording is None for recording in recordings):
            raise HTTPException(404, "A supplied recording was not found.")
        if any(
            recording.size_bytes > self.media.settings.max_upload_bytes for recording in recordings
        ):
            raise HTTPException(
                413, "A supplied video exceeds the configured per-file upload limit."
            )
        for recording in recordings:
            if not 0.1 <= recording.duration <= self.media.settings.max_clip_seconds:
                raise HTTPException(
                    422,
                    f"{recording.name}: video duration is outside the supported source/export limits.",
                )
            if not self.store.source_path(recording.id).is_file():
                raise HTTPException(404, "A supplied source file is missing. Import it again.")
        settings = request.settings
        if settings.analysis_start != 0 or settings.analysis_end != max(
            r.duration for r in recordings
        ):
            raise HTTPException(422, "Supplied clips must use their complete source durations.")
        if settings.framing == "split":
            raise HTTPException(
                422, "Configure webcam/gameplay framing individually after batch processing."
            )
        self._capacity()
        project = Project(
            generate_post_copy=request.generate_post_copy,
            id=str(request.id),
            title=request.title,
            created_at=now(),
            status="queued",
            recording_id=recordings[0].id,
            recording_ids=[recording.id for recording in recordings],
            generate_titles=request.generate_titles,
            settings=settings,
            selection_complete=True,
            items=[
                ProjectItem(
                    id=str(uuid4()),
                    candidate_id=recording.id,
                    recording_id=recording.id,
                    source_name=recording.name,
                    source_duration=recording.duration,
                    title=recording.name.rsplit(".", 1)[0][:120].strip() or "Untitled clip",
                    start=0,
                    end=recording.duration,
                    caption_preset=settings.caption_preset,
                    caption_customization=settings.caption_customization.model_copy(deep=True),
                    caption_position=settings.caption_position,
                    emphasis_words=list(settings.emphasis_words),
                    headline_enabled=settings.headline_enabled,
                    headline_position=settings.headline_position,
                    quality=QualityOptions(
                        cleanup=settings.audio_cleanup, normalize=settings.normalize_audio
                    ),
                )
                for recording in recordings
            ],
        )
        self._queue(project)
        return project

    def resume(self, identifier: str, request: ResumeRequest):
        with self.lock:
            project = self.get(identifier, hydrate=False)
            if project.status in ACTIVE:
                return self.get(identifier)
            self._capacity()
            result = (
                self.highlights.results.job(project.highlight_id) if project.highlight_id else None
            )
            if (
                not project.selection_complete
                and project.analysis_attempted
                and not (
                    result
                    and result.status == "succeeded"
                    and result.editorial_status == "completed"
                )
            ):
                if not request.retry_analysis:
                    raise HTTPException(
                        409,
                        "The previous AI attempt did not finish. Check API usage, then explicitly allow another attempt.",
                    )
                project.analysis_attempted, project.highlight_id = False, None
            project.thumbnail_error = None
            for item in project.items:
                item.thumbnail_error = None
                if project.recording_ids and request.retry_analysis and not item.analysis_complete:
                    item.analysis_attempted, item.analysis_error = False, None
                if (
                    project.recording_ids
                    and request.retry_titles
                    and item.title_attempted
                    and not item.title_complete
                ):
                    item.title_attempted, item.title_error = False, None
            self._queue(project)
            return self.get(identifier)

    def cancel(self, identifier: str):
        with self.lock:
            project = self.get(identifier, hydrate=False)
            if project.status not in ACTIVE:
                return self.get(identifier)
            project.status = "cancelled" if project.status == "queued" else "cancelling"
            project.stage = (
                "Cancelled" if project.status == "cancelled" else "Cancelling current work"
            )
            self.records.save(project)
            if project.status == "cancelled" and (future := self.scheduled.get(identifier)):
                future.cancel()
            return self.get(identifier)

    def hooks(self, identifier: str, item_id: str):
        with self.lock:
            project = self.get(identifier, hydrate=False)
            if project.status in ACTIVE:
                raise HTTPException(409, "Wait for processing before reviewing hooks.")
            item = next((i for i in project.items if i.id == item_id), None)
            if item is None:
                raise HTTPException(404, "Project clip not found.")
            if not project.transcription_id or not project.highlight_id:
                return []
            words = self.transcriptions.export_words(
                project.recording_id,
                project.transcription_id,
                project.transcript_revision,
                item.start,
                item.end,
            )
            analysis = self.highlights.results.job(project.highlight_id)
            if (
                analysis is None
                or analysis.recording_id != project.recording_id
                or analysis.transcript_revision != project.transcript_revision
            ):
                return []
            candidate = next((c for c in analysis.candidates if c.id == item.candidate_id), None)
            return suggest_hooks(words, candidate, item.start, item.end)

    def set_posted(self, identifier: str, item_id: str, posted: bool) -> None:
        with self.lock:
            project = self.get(identifier, hydrate=False)
            if project.status in ACTIVE:
                raise HTTPException(
                    409, "Wait for project processing to finish before updating Posted."
                )
            item = next((i for i in project.items if i.id == item_id), None)
            if item is None:
                raise HTTPException(404, "Project clip not found.")
            item.posted = posted
            with self.store.connection() as db:
                self.records.save(project, db=db)
                for export_id in {item.export_id, item.pending_export_id} - {None}:
                    if db.execute("SELECT 1 FROM clips WHERE id=?", (export_id,)).fetchone():
                        self.store.set_posted(export_id, posted, db=db)

    def set_export_posted(self, identifier: str, posted: bool) -> None:
        with self.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM clips WHERE id=?", (identifier,)).fetchone():
                raise HTTPException(404, "Export not found.")
            for row in db.execute("SELECT payload FROM projects").fetchall():
                project = Project.model_validate_json(row[0])
                matches = [
                    i for i in project.items if identifier in (i.export_id, i.pending_export_id)
                ]
                if not matches:
                    continue
                if project.status in ACTIVE:
                    raise HTTPException(
                        409, "Wait for project processing to finish before updating Posted."
                    )
                for item in matches:
                    item.posted = posted
                self.records.save(project, db=db)
            self.store.set_posted(identifier, posted, db=db)

    def edit(self, identifier: str, item_id: str, request: ItemEdit):
        return self.edit_many(identifier, [(item_id, request)])

    def edit_many(self, identifier, requests, *, reuse_exports=None):
        with self.lock:
            project = self.get(identifier, hydrate=False)
            if project.status in ACTIVE:
                raise HTTPException(409, "Wait for processing or cancel it before editing clips.")
            before = project.model_copy(deep=True)
            changes = [self._edit_item(project, item_id, request) for item_id, request in requests]
            if reuse_exports:
                for item in project.items:
                    if item.id in reuse_exports:
                        clip = self.store.clip(reuse_exports[item.id])
                        if (
                            clip
                            and clip.recording_id == (item.recording_id or project.recording_id)
                            and clip.status == "succeeded"
                            and self.store.export_path(clip.id).is_file()
                        ):
                            item.export_id = clip.id
            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                for item_id, _ in requests:
                    self.records.remember_version(before, item_id, db)
                self.records.save(project, db=db)
            if any(changes):
                self._queue(project)
            return self.get(identifier)

    def _edit_item(self, project, item_id, request):
        item = next((i for i in project.items if i.id == item_id), None)
        if item is None:
            raise HTTPException(404, "Project clip not found.")
        if item.revision != request.revision:
            raise HTTPException(409, "Clip changed in another window. Reload before saving.")
        settings = project.settings
        allowed_start = 0 if project.recording_ids else settings.analysis_start
        allowed_end = item.source_duration if project.recording_ids else settings.analysis_end
        if request.start < allowed_start or request.end > allowed_end:
            raise HTTPException(422, "Trim must remain inside the saved analysis range.")
        current_export = self.store.clip(item.export_id) if item.export_id else None
        old_mode = item.framing or settings.framing
        old_split = item.split_layout if item.framing is not None else settings.split_layout
        old_crop = item.crop_region if item.framing is not None else settings.crop_region
        new_crop = request.crop_region if request.framing is not None else settings.crop_region
        new_mode = request.framing or settings.framing
        if request.quality.smart_framing and (new_mode in {"split", "follow", "auto"} or new_crop):
            raise HTTPException(
                422,
                "Staged smart framing cannot be combined with manual regions or automatic layouts.",
            )
        new_split = request.split_layout if request.framing is not None else settings.split_layout
        expected_headline = (
            HeadlineOptions(
                text=request.opening_text or request.title, position=request.headline_position
            )
            if request.headline_enabled
            else None
        )
        changed = (
            request.auto_presentation
            or bool(
                project.recording_ids
                and settings.batch_mode in {"auto", "highlights"}
                and not item.analysis_complete
            )
            or (old_mode, old_split, old_crop) != (new_mode, new_split, new_crop)
            or bool(
                current_export
                and (
                    current_export.headline != expected_headline
                    or current_export.overlays != request.overlays
                    or current_export.mode != new_mode
                    or current_export.split_layout != new_split
                    or current_export.crop_region != new_crop
                )
            )
            or any(
                getattr(item, name) != getattr(request, name)
                for name in (
                    "start",
                    "end",
                    "caption_preset",
                    "caption_customization",
                    "quality",
                    "caption_position",
                    "emphasis_words",
                    "opening_text",
                    "headline_enabled",
                    "headline_position",
                    "overlays",
                )
            )
        )
        if changed:
            self._capacity()
        title_changed = item.title != request.title
        for name in (
            "title",
            "review",
            "start",
            "end",
            "caption_preset",
            "caption_customization",
            "quality",
            "caption_position",
            "emphasis_words",
            "headline_enabled",
            "headline_position",
            "overlays",
            "opening_text",
            "framing",
            "split_layout",
            "crop_region",
        ):
            setattr(item, name, getattr(request, name))
        item.revision += 1
        # Manual edits win. Only an explicit request re-runs the local planner.
        item.presentation_complete = not request.auto_presentation
        item.presentation_requested = request.auto_presentation
        if request.auto_presentation:
            item.presentation_summary = None
            item.presentation_warnings = []
        if (
            project.recording_ids
            and settings.batch_mode in {"auto", "highlights"}
            and not item.analysis_complete
        ):
            item.analysis_complete, item.analysis_error, item.analysis_choice = (
                True,
                None,
                "manual",
            )
            item.analysis_reason = "Source interval explicitly accepted in the clip editor."
            item.title_complete, item.title_error = True, None
        if project.recording_ids and title_changed:
            item.title_complete, item.title_error = True, None
        if changed:
            item.pending_export_id, item.export_id, item.error = None, None, None
            item.thumbnail, item.thumbnail_error = False, None
            item.thumbnail_export_id = None
        return changed

    def apply_caption_style(self, identifier: str, request: BatchStyle) -> Project:
        with self.lock:
            project = self.get(identifier, hydrate=False)
            if project.status in ACTIVE:
                raise HTTPException(
                    409, "Wait for processing or cancel it before applying a style."
                )
            requests = []
            for target in request.targets:
                item = next((i for i in project.items if i.id == str(target.id)), None)
                if item is None or item.revision != target.revision:
                    raise HTTPException(
                        409, "A selected clip changed. Reload before applying styles."
                    )
                if item.review == "rejected":
                    raise HTTPException(422, "Rejected clips cannot be batch styled.")
                if (item.caption_preset, item.caption_position, item.caption_customization) == (
                    request.style.preset,
                    request.style.position,
                    request.style.customization,
                ):
                    continue
                try:
                    edit = ItemEdit.model_validate(
                        {
                            **{
                                key: getattr(item, key)
                                for key in ItemEdit.model_fields
                                if key != "auto_presentation"
                            },
                            "caption_preset": request.style.preset,
                            "caption_position": request.style.position,
                            "caption_customization": request.style.customization,
                        }
                    )
                except ValidationError as exc:
                    raise HTTPException(
                        422, "The style conflicts with a selected clip's text placement."
                    ) from exc
                requests.append((item.id, edit))
            return self.edit_many(identifier, requests) if requests else self.get(identifier)

    def _check(self, identifier: str):
        project = self.get(identifier, hydrate=False)
        if self.stop.is_set() or project.status in {"cancelling", "cancelled"}:
            raise ProjectStopped()
        return project

    def _stage(self, identifier: str, status: str, text: str):
        with self.lock:
            project = self._check(identifier)
            project.status, project.stage = status, text
            project.step_progress = None
            project.step_started_at = project.step_updated_at = now()
            project.progress = {
                "downloading": 0,
                "transcribing": 0.25,
                "analysing": 0.5,
                "rendering": 0.75,
            }.get(status, project.progress)
            self.records.save(project)
            return project

    def _wait(self, identifier: str, read, active, cancel=None):
        try:
            while True:
                with self.lock:
                    self._check(identifier)
                child = read()
                if child is None:
                    raise HTTPException(409, "Processing state is missing. Resume explicitly.")
                if child.status not in active:
                    if child.status != "succeeded":
                        raise HTTPException(
                            422, child.error or "Processing stopped before completion."
                        )
                    return child
                self.stop.wait(0.2)
        except ProjectStopped:
            if cancel:
                try:
                    cancel()
                except (HighlightError, TranscriptionError, ExportBusy) as exc:
                    if getattr(exc, "status_code", 409) != 409:
                        raise
            raise

    def _available(self, identifier: str, operation):
        while True:
            with self.lock:
                self._check(identifier)
                try:
                    return operation()
                except ExportBusy:
                    pass
                except (HighlightError, TranscriptionError) as exc:
                    if exc.status_code != 409 or not str(exc).startswith("Another"):
                        raise
                except HTTPException as exc:
                    if exc.status_code != 409 or not str(exc.detail).startswith(
                        "Another video is importing"
                    ):
                        raise
            self.stop.wait(0.25)

    def _download(self, identifier: str):
        project = self._check(identifier)
        if project.recording_id:
            return
        source = self.youtube.source(project.youtube_source_id)
        self._stage(identifier, "downloading", "Downloading source video")
        prior = None
        if project.download_id:
            try:
                prior = self.youtube.get(project.download_id)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
        owned = False
        if prior is None or prior.status not in {*DOWNLOAD_ACTIVE, "succeeded"}:
            child_id = str(uuid4())
            with self.lock:
                project = self._check(identifier)
                project.download_id = child_id
                self.records.save(project)
            prior = self._available(
                identifier,
                lambda: self.youtube.submit(YouTubeRequest(url=source.url), identifier=child_id),
            )
            owned = prior.id == child_id
            with self.lock:
                # Persist the child even if cancellation arrived during submission;
                # _wait then cancels exactly the child this project started.
                project = self.get(identifier, hydrate=False)
                project.download_id = prior.id
                self.records.save(project)
        prior = self._wait(
            identifier,
            lambda: self.youtube.get(prior.id),
            DOWNLOAD_ACTIVE,
            (lambda: self.youtube.cancel(prior.id)) if owned else None,
        )
        recording = self.store.recording(prior.recording_id)
        if not recording or not recording.has_audio:
            raise HTTPException(422, "Downloaded video has no supported audio.")
        # YouTube metadata rounds duration to seconds; probe is frame-accurate.
        if project.settings.analysis_end > recording.duration + 1:
            raise HTTPException(
                422,
                "Downloaded duration differs from the preview. Set up a new project with a shorter analysis range.",
            )
        with self.lock:
            project = self._check(identifier)
            project.recording_id = recording.id
            self.records.save(project)

    def _thumbnail(self, identifier: str, item_id: str | None = None):
        with self.lock:
            project = self._check(identifier)
            item = next((i for i in project.items if i.id == item_id), None) if item_id else None
            target = item or project
            clip = self.store.clip(item.export_id) if item and item.export_id else None
            export_id = (
                clip.id
                if clip and clip.status == "succeeded" and self.store.export_path(clip.id).is_file()
                else None
            )
            current_export_id = item.thumbnail_export_id if item else None
            if (
                target.thumbnail
                and current_export_id == export_id
                and self.thumbnail_path(project, item).is_file()
            ):
                return
            path = (
                self.root / f"{item.id}-{export_id}.jpg"
                if item and export_id
                else self.root / f"{target.id}.jpg"
            )
            timestamp = 0 if export_id else item.start if item else project.settings.analysis_start
            source = (
                self.store.export_path(export_id)
                if export_id
                else self.store.source_path(
                    item.recording_id if item and item.recording_id else project.recording_id
                )
            )
        temporary = path.with_suffix(".partial.jpg")
        try:
            self.media.thumbnail(
                source,
                temporary,
                timestamp,
                self.stop,
            )
            temporary.replace(path)
            target.thumbnail = True
            target.thumbnail_error = None
        except (MediaError, OSError) as exc:
            logger.warning(
                "Thumbnail unavailable for project %s (%s)", identifier, type(exc).__name__
            )
            target.thumbnail = False
            target.thumbnail_error = (
                "Preview thumbnail unavailable. Resume to retry; finished video remains available."
            )
        finally:
            remove_artifact(temporary)
        with self.lock:
            current = self.get(identifier, hydrate=False)
            destination = next(i for i in current.items if i.id == item_id) if item_id else current
            destination.thumbnail, destination.thumbnail_error = (
                target.thumbnail,
                target.thumbnail_error,
            )
            if item:
                destination.thumbnail_export_id = export_id
            self.records.save(current)

    def thumbnail_path(self, project: Project, item: ProjectItem | None = None):
        key = (
            f"{item.id}-{item.thumbnail_export_id}"
            if item and item.thumbnail_export_id
            else item.id
            if item
            else project.id
        )
        return self.root / f"{key}.jpg"

    def _select(self, identifier: str):
        with self._timed(identifier, "transcription"):
            project = self._stage(identifier, "transcribing", "Preparing transcript")
            if project.selection_complete:
                return
            transcript = (
                self.transcriptions.transcripts.job(project.transcription_id)
                if project.transcription_id
                else self.transcriptions.latest(project.recording_id)
            )
            if transcript is None or transcript.status not in {*ACTIVE_STATUSES, "succeeded"}:
                child_id = str(uuid4())
                with self.lock:
                    project = self._check(identifier)
                    project.transcription_id = child_id
                    self.records.save(project)
                transcript = self._available(
                    identifier,
                    lambda: self.transcriptions.submit(project.recording_id, identifier=child_id),
                )
                owned = transcript.id == child_id
            else:
                owned = False
            transcript = self._wait(
                identifier,
                lambda: self.transcriptions.transcripts.job(transcript.id),
                ACTIVE_STATUSES,
                (lambda: self.transcriptions.cancel(transcript.id)) if owned else None,
            )
            with self.lock:
                project = self._check(identifier)
                project.transcription_id, project.transcript_revision = (
                    transcript.id,
                    transcript.revision,
                )
                project.transcription_backend = transcript.device
                project.transcription_warning = transcript.warning
                self.records.save(project)
        with self._timed(identifier, "ai"):
            project = self._stage(identifier, "analysing", "Selecting source-grounded moments")
            result = (
                self.highlights.results.job(project.highlight_id) if project.highlight_id else None
            )
            owned = False
            if result is None:
                if project.analysis_attempted:
                    raise HTTPException(
                        409,
                        "AI attempt outcome is unknown. Resume with explicit permission for another attempt.",
                    )
                child_id = str(uuid4())
                with self.lock:
                    project = self._check(identifier)
                    if project.avoid_used_clips:
                        project.selection_exclusions = self.records.used_ranges(
                            project.recording_id
                        )
                    project.highlight_id, project.analysis_attempted = child_id, True
                    self.records.save(project)
                s = project.settings
                request = HighlightRequest(
                    excluded_ranges=project.selection_exclusions,
                    ai_instructions=s.ai_instructions,
                    transcription_id=transcript.id,
                    revision=transcript.revision,
                    remote_consent=True,
                    quality_mode=s.quality_mode,
                    analysis_start=s.analysis_start,
                    analysis_end=min(
                        s.analysis_end, self.store.recording(project.recording_id).duration
                    ),
                    duration_mode=s.duration_mode,
                    min_seconds=8 if s.duration_mode == "auto" else s.min_seconds,
                    max_seconds=min(
                        int(self.media.settings.max_clip_seconds),
                        max(
                            8,
                            math.ceil(
                                min(
                                    s.analysis_end,
                                    self.store.recording(project.recording_id).duration,
                                )
                                - s.analysis_start
                            ),
                        ),
                    )
                    if s.duration_mode == "auto"
                    else s.max_seconds,
                    max_candidates=s.max_clips,
                )
                result = self._available(
                    identifier,
                    lambda: self.highlights.submit(
                        project.recording_id, request, identifier=child_id
                    ),
                )
                owned = result.id == child_id
                with self.lock:
                    project = self.get(identifier, hydrate=False)
                    project.highlight_id = result.id
                    self.records.save(project)
            result = self._wait(
                identifier,
                lambda: self.highlights.results.job(result.id),
                HIGHLIGHT_ACTIVE,
                (lambda: self.highlights.cancel(result.id)) if owned else None,
            )
            if result.stale:
                raise HTTPException(
                    409,
                    "Transcript changed. Create a new project to select from the current revision.",
                )
            if result.editorial_status != "completed":
                raise HTTPException(
                    409,
                    "This analysis predates editorial review. Resume with explicit permission to analyse again.",
                )
            with self.lock:
                project = self._check(identifier)
                candidates = [
                    candidate for candidate in result.candidates if candidate.review != "rejected"
                ]
                project.analysis_cache_hit = result.cache_hit
                if project.avoid_used_clips:
                    blocked = self.records.used_ranges(project.recording_id)
                    unused = []
                    for candidate in candidates:
                        if not overlaps(candidate.start, candidate.end, blocked):
                            unused.append(candidate)
                            blocked.append(SourceRange(start=candidate.start, end=candidate.end))
                    candidates = unused
                project.items = [
                    ProjectItem(
                        quality=QualityOptions(
                            cleanup=project.settings.audio_cleanup,
                            normalize=project.settings.normalize_audio,
                        ),
                        headline_enabled=project.settings.headline_enabled,
                        headline_position=project.settings.headline_position,
                        editorial_quality=c.quality,
                        id=str(uuid4()),
                        candidate_id=c.id,
                        title=c.title,
                        start=c.start,
                        end=c.end,
                        caption_preset=project.settings.caption_preset,
                        caption_customization=project.settings.caption_customization,
                        emphasis_words=project.settings.emphasis_words,
                        caption_position=project.settings.caption_position,
                        review=c.review,
                        framing=project.settings.framing,
                        split_layout=project.settings.split_layout,
                        crop_region=project.settings.crop_region,
                    )
                    for c in candidates
                ][: project.settings.max_clips]
                project.selection_complete = True
                self.records.save(project)

    def _render(self, identifier: str, item_id: str):
        with self.lock:
            project = self._check(identifier)
            item = next(i for i in project.items if i.id == item_id)
            recording_id = item.recording_id or project.recording_id
            transcription_id = (
                item.transcription_id if project.recording_ids else project.transcription_id
            )
            transcript_revision = (
                item.transcript_revision if project.recording_ids else project.transcript_revision
            )
            if item.review == "rejected":
                return
            headline = (
                HeadlineOptions(
                    text=item.opening_text or item.title, position=item.headline_position
                )
                if item.headline_enabled
                else None
            )
            existing_id = item.pending_export_id or item.export_id
            mode = item.framing or project.settings.framing
            split_layout = (
                item.split_layout if item.framing is not None else project.settings.split_layout
            )
            crop_region = (
                item.crop_region if item.framing is not None else project.settings.crop_region
            )
            clip = self.store.clip(existing_id) if existing_id else None
            legacy_split_cover = (
                clip
                and clip.quality.cover_at is not None
                and (
                    clip.mode == "split" or (clip.auto_layout and clip.auto_layout.mode == "split")
                )
                and not (clip.performance and clip.performance.split_cover)
            )
            captions = (
                CaptionOptions(
                    transcription_id=transcription_id,
                    revision=transcript_revision,
                    position=item.caption_position,
                    preset=item.caption_preset,
                    emphasis_words=item.emphasis_words,
                    customization=item.caption_customization,
                )
                if item.caption_preset != "none"
                else None
            )
            if (
                clip
                and clip.status == "succeeded"
                and not legacy_split_cover
                and clip.quality == item.quality
                and clip.headline == headline
                and clip.captions == captions
                and clip.start == item.start
                and clip.end == item.end
                and clip.overlays == item.overlays
                and clip.mode == mode
                and clip.split_layout == split_layout
                and clip.crop_region == crop_region
                and self.store.export_path(clip.id).is_file()
            ):
                item.export_id, item.pending_export_id, item.error = clip.id, None, None
                with self.store.connection() as db:
                    self.records.save(project, db=db)
                    self.store.set_posted(clip.id, item.posted, db=db)
                return
            words = None
            if captions is not None:
                words = self.transcriptions.export_words(
                    recording_id,
                    transcription_id,
                    transcript_revision,
                    item.start,
                    item.end,
                )
            request = ExportRequest(
                quality=item.quality,
                start=item.start,
                end=item.end,
                mode=mode,
                split_layout=split_layout,
                crop_region=crop_region,
                captions=captions,
                headline=headline,
                overlays=item.overlays,
            )
            child_id = str(uuid4())
            item.pending_export_id, item.error = child_id, None
            self.records.save(project)
        self._available(
            identifier,
            lambda: self.jobs.submit(recording_id, request, words, clip_id=child_id),
        )
        self._wait(
            identifier,
            lambda: self.store.clip(child_id),
            {"queued", "rendering", "cancelling"},
            lambda: self.jobs.cancel(child_id),
        )
        with self.lock:
            project = self._check(identifier)
            item = next(i for i in project.items if i.id == item_id)
            item.export_id, item.pending_export_id, item.error = child_id, None, None
            with self.store.connection() as db:
                self.records.save(project, db=db)
                self.store.set_posted(child_id, item.posted, db=db)

    def _prepare_supplied(self, identifier: str, item_id: str):
        with self.lock:
            project = self._check(identifier)
            item = next(i for i in project.items if i.id == item_id)
            if item.review == "rejected":
                return
            needs_title = (
                project.generate_titles and not item.title_complete and not item.title_attempted
            )
            needs_review = (
                project.settings.batch_mode in {"auto", "highlights"} and not item.analysis_complete
            )
            if needs_review and item.analysis_attempted:
                raise HTTPException(
                    409,
                    item.analysis_error
                    or "The previous AI review outcome is unknown. Review manually or explicitly approve another analysis attempt.",
                )
            needs_transcript = (
                item.caption_preset != "none"
                or needs_title
                or needs_review
                or project.generate_post_copy
            )
            recording = self.store.recording(item.recording_id)
            if not recording:
                raise HTTPException(404, "Supplied recording is missing.")
            if not recording.has_audio:
                if needs_review:
                    raise HTTPException(
                        422,
                        "AI speech review requires audio. Choose No captions and save the interval manually, or use Keep full length.",
                    )
                if item.caption_preset != "none":
                    raise HTTPException(
                        422, "This clip has no audio. Choose No captions in Trim / edit, then save."
                    )
                item.title_complete = True
                item.transcription_warning = "No audio: filename title retained. Captions and AI speech titles are unavailable."
                self.records.save(project)
                return
            if not needs_transcript:
                return
            transcript = (
                self.transcriptions.transcripts.job(item.transcription_id)
                if item.transcription_id
                else self.transcriptions.latest(item.recording_id)
            )
        owned = False
        if transcript is None or transcript.status not in {*ACTIVE_STATUSES, "succeeded"}:
            child_id = str(uuid4())
            with self.lock:
                project = self._check(identifier)
                item = next(i for i in project.items if i.id == item_id)
                item.transcription_id = child_id
                self.records.save(project)
            transcript = self._available(
                identifier,
                lambda: self.transcriptions.submit(item.recording_id, identifier=child_id),
            )
            owned = transcript.id == child_id
        with self.lock:
            project = self.get(identifier, hydrate=False)
            item = next(i for i in project.items if i.id == item_id)
            item.transcription_id = transcript.id
            self.records.save(project)
        with self._timed(identifier, "transcription"):
            transcript = self._wait(
                identifier,
                lambda: self.transcriptions.transcripts.job(transcript.id),
                ACTIVE_STATUSES,
                (lambda: self.transcriptions.cancel(transcript.id)) if owned else None,
            )
        with self.lock:
            project = self._check(identifier)
            item = next(i for i in project.items if i.id == item_id)
            if (
                item.transcript_revision is not None
                and item.transcript_revision != transcript.revision
            ):
                raise HTTPException(
                    409,
                    "This clip's transcript changed. Create a new batch to use the corrected transcript without replacing earlier exports.",
                )
            item.transcription_id, item.transcript_revision = transcript.id, transcript.revision
            item.transcription_warning = transcript.warning
            item.transcription_backend = transcript.device
            self.records.save(project)
        if needs_review:
            with self._timed(identifier, "ai"):
                self._review_supplied(identifier, item_id)
            return
        if needs_title:
            words = self.transcriptions.export_words(
                item.recording_id, transcript.id, transcript.revision, item.start, item.end
            )
            with self.lock:
                project = self._check(identifier)
                item = next(i for i in project.items if i.id == item_id)
                item.title_attempted = True
                item.title_error = "Title request outcome is unconfirmed. Check API usage before explicitly retrying titles. The filename title is retained."
                self.records.save(project)
            try:
                with self._timed(identifier, "ai"):
                    title, input_tokens, output_tokens = asyncio.run(
                        self.titles.suggest(words, ai_instructions=project.settings.ai_instructions)
                    )
                # Recheck ownership/revision after remote inference before publishing the title.
                self.transcriptions.export_words(
                    item.recording_id, transcript.id, transcript.revision, item.start, item.end
                )
                with self.lock:
                    project = self.get(identifier, hydrate=False)
                    item = next(i for i in project.items if i.id == item_id)
                    item.title, item.title_complete, item.title_error = title, True, None
                    item.title_input_tokens, item.title_output_tokens = input_tokens, output_tokens
                    self.records.save(project)
            except (HighlightError, HTTPException) as exc:
                with self.lock:
                    project = self.get(identifier, hydrate=False)
                    item = next(i for i in project.items if i.id == item_id)
                    item.title_error = (
                        str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                    )
                    self.records.save(project)

    def _review_supplied(self, identifier: str, item_id: str):
        with self.lock:
            project = self._check(identifier)
            item = next(i for i in project.items if i.id == item_id)
            recording = self.store.recording(item.recording_id)
            page = self.transcriptions.transcripts.words(
                item.transcription_id,
                0,
                recording.duration,
                revision=item.transcript_revision,
                recording_id=item.recording_id,
            )
            if len(page.words) > MAX_REVIEW_WORDS:
                raise HTTPException(
                    422,
                    "AI video review supports at most 6,000 speech words per source. Choose a shorter source or keep/edit the interval manually.",
                )
            segments = build_segments(page.words, recording.duration)
            if not segments:
                raise HTTPException(
                    422, "No recognized speech for AI review. Review the interval manually."
                )
            if len(window_text(segments).encode("utf-8")) > MAX_REVIEW_BYTES:
                raise HTTPException(
                    422,
                    "The source transcript exceeds the 128 KiB video-review budget. Review a shorter source or use manual editing.",
                )
            item.analysis_attempted = True
            project.status, project.stage = (
                "analysing",
                f"AI reviewing {item.source_name or 'supplied video'}",
            )
            item.analysis_error = "AI video review outcome is unconfirmed. Check API usage before explicitly retrying analysis."
            self.records.save(project)
        try:
            multiple = project.settings.batch_mode == "highlights"
            reviewer = self.batch_highlights if multiple else self.batch_review
            proposal, input_tokens, output_tokens = asyncio.run(
                reviewer.suggest(
                    segments,
                    recording.duration,
                    ai_instructions=project.settings.ai_instructions,
                    quality_mode=project.settings.quality_mode,
                    **({"max_clips": project.settings.clips_per_source} if multiple else {}),
                )
            )
            with self.lock:
                current = self.get(identifier, hydrate=False)
                metered = next(i for i in current.items if i.id == item_id)
                metered.analysis_input_tokens, metered.analysis_output_tokens = (
                    input_tokens,
                    output_tokens,
                )
                self.records.save(current)
            self.transcriptions.export_words(
                item.recording_id,
                item.transcription_id,
                item.transcript_revision,
                0,
                recording.duration,
            )
            if proposal.decision == "review":
                raise HighlightError(
                    proposal.reason + " Review the interval manually before saving."
                )
            candidate = proposal.candidate
            candidates = proposal.candidates if multiple else [candidate]
            if multiple and len(candidates) > project.settings.clips_per_source:
                raise HighlightError("AI returned more clips than the saved per-video limit.")
            grounded = []
            if proposal.decision == "keep_full":
                text = " ".join(segment.text for segment in segments)
                if (
                    candidate.first_segment != segments[0].id
                    or candidate.last_segment != segments[-1].id
                    or any(segment.end > recording.duration for segment in segments)
                    or len(candidate.evidence) < 8
                    or candidate.evidence not in text
                ):
                    raise HighlightError(
                        "AI whole-video recommendation did not match source evidence. Review manually."
                    )
                if project.settings.quality_mode and not meets_quality(
                    candidate.quality, segments[0], segments[-1]
                ):
                    raise HighlightError(
                        "AI whole-video recommendation did not meet the saved quality/evidence requirements. Review manually."
                    )
                start, end = 0, recording.duration
            else:
                request = HighlightRequest(
                    transcription_id=item.transcription_id,
                    revision=item.transcript_revision,
                    remote_consent=True,
                    quality_mode=project.settings.quality_mode,
                    min_seconds=8,
                    max_seconds=max(8, math.ceil(recording.duration)),
                    analysis_end=recording.duration,
                )
                grounded, rejected = ground_candidates(
                    ProposedHighlights(candidates=candidates),
                    segments,
                    request,
                    recording.duration,
                    item.id,
                )
                if rejected or len(grounded) != len(candidates):
                    raise HighlightError(
                        "AI trimmed interval did not match source boundaries, evidence or quality requirements. Review manually."
                    )
                if len(
                    rank_unique(
                        grounded,
                        len(grounded),
                        iou_threshold=self.highlights.settings.highlight_duplicate_iou,
                        evidence_threshold=self.highlights.settings.highlight_duplicate_evidence,
                    )
                ) != len(grounded) or any(
                    max(0, min(a.end, b.end) - max(a.start, b.start))
                    / min(a.end - a.start, b.end - b.start)
                    > 0.5
                    for index, a in enumerate(grounded)
                    for b in grounded[index + 1 :]
                ):
                    raise HighlightError(
                        "AI returned duplicate or heavily overlapping highlights. Review manually or explicitly retry."
                    )
                start, end = grounded[0].start, grounded[0].end
                if not multiple and start == 0 and end == recording.duration:
                    raise HighlightError(
                        "AI marked the complete source as a trim. Review the interval manually."
                    )
            with self.lock:
                current = self.get(identifier, hydrate=False)
                target = next(i for i in current.items if i.id == item_id)
                target.start, target.end, target.title = start, end, candidate.title
                target.editorial_quality = candidate.quality
                target.analysis_complete, target.analysis_choice = (
                    True,
                    "trim" if proposal.decision == "clips" else proposal.decision,
                )
                target.analysis_reason, target.analysis_error = proposal.reason, None
                target.analysis_start, target.analysis_end = start, end
                target.analysis_input_tokens, target.analysis_output_tokens = (
                    input_tokens,
                    output_tokens,
                )
                target.title_complete, target.title_error = True, None
                target.thumbnail, target.thumbnail_error = False, None
                target.source_item_id = target.source_item_id or target.id
                # Save every validated result before rendering any of them.
                for selected, source_candidate in zip(grounded[1:], candidates[1:]):
                    child = target.model_copy(deep=True)
                    child.id, child.candidate_id = str(uuid4()), selected.id
                    child.posted = False
                    child.start, child.end, child.title = (
                        selected.start,
                        selected.end,
                        selected.title,
                    )
                    child.analysis_start, child.analysis_end = selected.start, selected.end
                    child.editorial_quality = source_candidate.quality
                    child.analysis_input_tokens, child.analysis_output_tokens = 0, 0
                    child.export_id, child.pending_export_id, child.export = None, None, None
                    child.thumbnail_export_id = None
                    current.items.append(child)
                self.records.save(current)
        except (HighlightError, HTTPException) as exc:
            if isinstance(exc, HighlightError):
                logger.info(
                    "Supplied-video review for item %s did not produce usable output (%d).",
                    item_id,
                    exc.status_code,
                )
            with self.lock:
                current = self.get(identifier, hydrate=False)
                target = next(i for i in current.items if i.id == item_id)
                target.analysis_error = (
                    str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                )
                self.records.save(current)
            raise HTTPException(422, target.analysis_error) from exc

    @staticmethod
    def copy_source(project, item):
        value = [
            item.recording_id or project.recording_id,
            item.transcription_id or project.transcription_id,
            item.transcript_revision if project.recording_ids else project.transcript_revision,
            float(item.start),
            float(item.end),
            [
                [float(a), float(b)]
                for a, b in retained_intervals(item.start, item.end, item.quality)
            ],
        ]
        return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()

    def write_post_copy(self, identifier, item_id, request_id=None, revision=None):
        key = (identifier, item_id)
        with self.lock:
            project = self.get(identifier, hydrate=False)
            item = next((i for i in project.items if i.id == item_id), None)
            if item is None:
                raise HTTPException(404, "Project clip not found.")
            if request_id:
                if request_id in item.post_copy_requests:
                    return self.get(identifier)
                if project.status in ACTIVE or key in self.copy_active:
                    raise HTTPException(409, "Wait for this clip's current processing to finish.")
                if len(self.copy_active) >= 2:
                    raise HTTPException(
                        409,
                        "Posting-copy generation is busy. Wait before starting another request.",
                    )
                if item.revision != revision:
                    raise HTTPException(409, "Clip changed. Reload before generating copy.")
                if len(item.post_copy_requests) >= 64:
                    raise HTTPException(
                        409, "This clip reached its copy-attempt limit. Edit posting text manually."
                    )
                item.post_copy_requests.append(request_id)
            elif item.post_copy_attempted:
                return
            source_key = self.copy_source(project, item)
            item.post_copy_attempted = True
            item.post_copy_request_id = request_id
            item.post_copy_error = (
                "Posting copy outcome is unconfirmed. Check usage before explicitly retrying."
            )
            self.records.save(project)
            self.copy_active.add(key)
            recording_id = item.recording_id or project.recording_id
            transcript_id = item.transcription_id or project.transcription_id
            transcript_revision = (
                item.transcript_revision if project.recording_ids else project.transcript_revision
            )
            start, end = item.start, item.end
            kept = retained_intervals(start, end, item.quality)
            instructions = project.settings.ai_instructions
        try:
            if not transcript_id or transcript_revision is None:
                raise HighlightError(
                    "No saved speech transcript is available. Add posting copy manually.", 422
                )
            words = self.transcriptions.export_words(
                recording_id, transcript_id, transcript_revision, start, end
            )
            words = mapped_words(words, kept, start)
            if not request_id:
                self._stage(
                    identifier, "rendering", f"Writing description and hashtags for {item.title}"
                )
            with self._timed(identifier, "ai"):
                copy, inputs, outputs = asyncio.run(
                    self.copy_writer.suggest(words, ai_instructions=instructions)
                )
            self.transcriptions.export_words(
                recording_id, transcript_id, transcript_revision, start, end
            )
            with self.lock:
                current = self.get(identifier, hydrate=False)
                target = next((i for i in current.items if i.id == item_id), None)
                if (
                    target is None
                    or self.copy_source(current, target) != source_key
                    or current.status in {"cancelled", "cancelling"}
                ):
                    raise HighlightError(
                        "Clip changed while generating posting copy. Review and regenerate explicitly.",
                        409,
                    )
                target.post_copy = copy
                target.post_copy_selected = None
                target.revision += 1
                target.post_copy_source = source_key
                target.post_copy_input_tokens, target.post_copy_output_tokens = inputs, outputs
                target.post_copy_error = None
                self.records.save(current)
        except (HighlightError, TranscriptionError) as exc:
            with self.lock:
                current = self.get(identifier, hydrate=False)
                target = next((i for i in current.items if i.id == item_id), None)
                if target:
                    target.post_copy_error = str(exc)
                    self.records.save(current)
        finally:
            with self.lock:
                self.copy_active.discard(key)
        return self.get(identifier)

    def _present(self, identifier: str, item_id: str):
        with self.lock:
            project = self._check(identifier)
            item = next(i for i in project.items if i.id == item_id)
            if (
                item.review == "rejected"
                or item.presentation_complete
                or (project.settings.presentation_mode == "off" and not item.presentation_requested)
            ):
                return
            recording_id = item.recording_id or project.recording_id
            recording = self.store.recording(recording_id)
            source = self.store.source_path(recording_id)
            mode = (
                project.settings.presentation_mode
                if project.settings.presentation_mode != "off"
                else "auto"
            )
            self._stage(identifier, "rendering", f"Choosing presentation for {item.title}")

        def visual_progress(done, total):
            with self.lock:
                current = self._check(identifier)
                current.step_progress = done / max(1, total)
                current.step_updated_at = now()
                current.stage = (
                    f"Choosing presentation: {done} of {total} frames checked for {item.title}"
                )
                self.records.save(current)

        plan = self.presentation.analyze(
            source,
            recording,
            item.start,
            item.end,
            mode,
            item.caption_position,
            item.headline_position,
            self.stop,
            lambda: self._check(identifier),
            visual_progress,
        )
        with self.lock:
            project = self._check(identifier)
            target = next(i for i in project.items if i.id == item_id)
            previous_caption, previous_headline = target.caption_position, target.headline_position
            manual_crop = (
                target.crop_region if target.framing is not None else project.settings.crop_region
            )
            current_mode = target.framing or project.settings.framing
            if current_mode in {"split", "follow", "auto"} or manual_crop:
                plan.summary = (
                    "Auto layout retained; composition is chosen per clip."
                    if current_mode == "auto"
                    else "Auto follow retained; tracking is analyzed per clip."
                    if current_mode == "follow"
                    else "Manual framing preserved; opening cover selected locally."
                )
                plan.smart_framing = None
                plan.caption_position = (
                    "lower" if target.caption_position == "auto" else target.caption_position
                )
                plan.headline_position = (
                    ("centre" if current_mode == "split" else "upper")
                    if target.headline_position == "auto"
                    else target.headline_position
                )
            else:
                target.framing, target.split_layout = plan.framing, plan.split_layout
                target.crop_region = None
            target.quality = target.quality.model_copy(
                # Cover selection must survive any already accepted pacing edits.
                update={
                    "smart_framing": plan.smart_framing,
                    "cover_at": plan.cover_at if target.headline_enabled else None,
                }
            )
            kept = retained_intervals(
                target.start, target.end, target.quality.model_copy(update={"cover_at": None})
            )
            if target.quality.cover_at is not None and not any(
                first <= target.quality.cover_at < last for first, last in kept
            ):
                target.quality.cover_at = kept[0][0]
            if target.caption_position == "auto" and current_mode != "auto":
                target.caption_position = plan.caption_position
            if target.headline_position == "auto" and current_mode != "auto":
                target.headline_position = plan.headline_position

            def validate_positions():
                headline = (
                    HeadlineOptions(
                        text=target.opening_text or target.title, position=target.headline_position
                    )
                    if target.headline_enabled
                    else None
                )
                layout(
                    mapped_overlays(target.overlays, target.start, kept),
                    sum(last - first for first, last in kept),
                    headline,
                    target.caption_position if target.caption_preset != "none" else None,
                    split=(target.framing or project.settings.framing) == "split",
                )

            try:
                validate_positions()
            except ValueError:
                target.caption_position, target.headline_position = (
                    previous_caption,
                    previous_headline,
                )
                try:
                    validate_positions()
                except ValueError as exc:
                    raise MediaError(str(exc)) from exc
                plan.warnings.append(
                    "Original text positions were preserved to avoid colliding with existing overlays."
                )
            target.presentation_complete, target.presentation_requested = True, False
            target.presentation_summary, target.presentation_warnings = plan.summary, plan.warnings
            self.records.save(project)

    def run(self, identifier: str):
        try:
            with self.lock:
                if self.get(identifier, hydrate=False).status != "queued":
                    return
            supplied = bool(self.get(identifier, hydrate=False).recording_ids)
            if not supplied:
                with self._timed(identifier, "download"):
                    self._download(identifier)
            with self._timed(identifier, "thumbnails"):
                self._thumbnail(identifier)
            if not supplied:
                self._select(identifier)
            project = self._stage(identifier, "rendering", "Preparing clip exports")
            index = 0
            while True:
                with self.lock:
                    project = self._check(identifier)
                    if index >= len(project.items):
                        break
                    item = project.items[index]
                index += 1
                self._stage(
                    identifier, "rendering", f"Rendering clip {index} of {len(project.items)}"
                )
                try:
                    if supplied:
                        self._stage(
                            identifier,
                            "transcribing",
                            f"Preparing supplied clip {index} of {len(project.items)}",
                        )
                        self._prepare_supplied(identifier, item.id)
                        self._stage(
                            identifier,
                            "rendering",
                            f"Rendering supplied clip {index} of {len(project.items)}",
                        )
                    if self.get(identifier, hydrate=False).generate_post_copy:
                        self.write_post_copy(identifier, item.id)
                    with self._timed(identifier, "visual"):
                        self._present(identifier, item.id)
                    self._stage(
                        identifier, "rendering", f"Rendering clip {index} of {len(project.items)}"
                    )
                    with self._timed(identifier, "render"):
                        self._render(identifier, item.id)
                    with self._timed(identifier, "thumbnails"):
                        self._thumbnail(identifier, item.id)
                except (HTTPException, MediaError, TranscriptionError, HighlightError) as exc:
                    with self.lock:
                        current = self._check(identifier)
                        next(i for i in current.items if i.id == item.id).error = (
                            str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                        )
                        self.records.save(current)
            with self.lock:
                current = self._check(identifier)
                eligible = [i for i in current.items if i.review != "rejected"]
                failed = sum(i.error is not None for i in eligible)
                current.status = "partial" if failed else "ready"
                current.progress = (
                    1
                    if not failed
                    else (len(eligible) - failed) / max(1, len(eligible))
                    if supplied
                    else 0.75 + 0.25 * (len(eligible) - failed) / max(1, len(eligible))
                )
                current.stage = (
                    f"{len(eligible) - failed} of {len(eligible)} eligible clips ready"
                    if current.items
                    else "No additional unused moments found in this range"
                    if current.avoid_used_clips
                    else "No suitable moments found in this range"
                )
                self.records.save(current)
        except ProjectStopped, RenderCancelled:
            with self.lock:
                project = self.get(identifier, hydrate=False)
                project.status = "interrupted" if self.stop.is_set() else "cancelled"
                project.stage = "Processing stopped; completed work preserved"
                self.records.save(project)
        except (HTTPException, HighlightError, TranscriptionError, MediaError) as exc:
            with self.lock:
                project = self.get(identifier, hydrate=False)
                project.status = "failed"
                project.stage = "Processing needs attention"
                project.error = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                self.records.save(project)
        except Exception:
            logger.error(
                "Unexpected project processing failure for %s. Check local storage/configuration.",
                identifier,
            )
            with self.lock:
                project = self.get(identifier, hydrate=False)
                project.status, project.error = (
                    "failed",
                    "Project processing failed. Check backend storage/configuration and resume explicitly.",
                )
                self.records.save(project)

    def close(self):
        self.stop.set()
        self.pool.shutdown(wait=True)
