import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from uuid import uuid4

from .auto_follow import AutoFollow
from .captions import CaptionError, write_ass
from .editing import QualityOptions, mapped_overlays, mapped_words, retained_intervals
from .files import remove_artifact
from .headlines import write_headline
from .media import Media, MediaError, RenderCancelled
from .models import Clip, ExportRequest
from .overlays import resolve_caption_position, write_presentation
from .storage import Store

logger = logging.getLogger(__name__)


class ExportBusy(Exception):
    pass


class Jobs:
    def __init__(self, store: Store, media: Media):
        self.store, self.media = store, media
        self.follow = AutoFollow(media)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip-export")
        self.lock = threading.Lock()
        self.active: str | None = None
        self.cancel_event = threading.Event()
        self.stopping = False

    def current(self) -> Clip | None:
        with self.lock:
            return self.store.clip(self.active) if self.active else None

    def submit(
        self,
        recording_id: str,
        request: ExportRequest,
        caption_words: list[dict] | None = None,
        *,
        clip_id: str | None = None,
    ) -> Clip:
        with self.lock:
            if self.stopping:
                raise ExportBusy("Export service is stopping. Retry after it restarts.")
            if self.active is not None:
                raise ExportBusy("An export is already processing. Wait or cancel it first.")
            clip_id = clip_id or str(uuid4())
            follow_plan = (
                self.store.cached_follow_plan(recording_id, request.start, request.end)
                if request.mode == "follow"
                else None
            )
            clip = self.store.create_clip(
                clip_id, recording_id, request, caption_words, follow_plan
            )
            self.active = clip_id
            self.cancel_event.clear()
            try:
                future = self.pool.submit(self.run, clip)
                future.add_done_callback(self.report_failure)
            except RuntimeError:
                self.active = None
                self.store.update_clip(clip_id, "failed", "Export service is stopping.")
                raise
            return clip

    @staticmethod
    def report_failure(future: Future[None]) -> None:
        if not future.cancelled() and (error := future.exception()) is not None:
            logger.error(
                "Export worker could not persist or clean up its result",
                exc_info=(type(error), error, error.__traceback__),
            )

    def cancel(self, clip_id: str) -> None:
        with self.lock:
            clip = self.store.clip(clip_id)
            if (
                self.active != clip_id
                or clip is None
                or clip.status not in {"queued", "rendering", "cancelling"}
            ):
                raise ExportBusy("This export is no longer processing.")
            self.store.update_clip(clip_id, "cancelling")
            self.cancel_event.set()

    def run(self, clip: Clip) -> None:
        partial = self.store.root / "exports" / f"{clip.id}.partial.mp4"
        caption_path = self.store.root / "exports" / f"{clip.id}.partial.ass"
        headline_path = self.store.root / "exports" / f"{clip.id}.headline.partial.ass"
        cover_path = self.store.root / "exports" / f"{clip.id}.cover.partial.jpg"
        try:
            with self.lock:
                if self.cancel_event.is_set():
                    raise RenderCancelled()
                self.store.update_clip(clip.id, "rendering")
            render_options = {}
            if clip.mode == "follow":
                if clip.follow_plan is None:
                    plan = self.follow.analyze(
                        self.store.source_path(clip.recording_id),
                        clip.start,
                        clip.end,
                        self.cancel_event,
                        partial.parent,
                    )
                    with self.lock:
                        if self.cancel_event.is_set():
                            raise RenderCancelled()
                        self.store.save_follow_plan(clip.id, plan)
                    clip.follow_plan = plan
                render_options["follow_plan"] = clip.follow_plan
            if clip.quality.cover_at is not None:
                if not clip.start <= clip.quality.cover_at < clip.end:
                    raise MediaError("The cover frame must remain inside this clip.")
                self.media.thumbnail(
                    self.store.source_path(clip.recording_id),
                    cover_path,
                    clip.quality.cover_at,
                    self.cancel_event,
                    max_dimension=1920,
                )
                render_options["cover_path"] = cover_path
            kept = retained_intervals(clip.start, clip.end, clip.quality)
            output_duration = sum(last - first for first, last in kept)
            overlays = mapped_overlays(clip.overlays, clip.start, kept)
            caption_position = resolve_caption_position(
                clip.captions.position if clip.captions else None,
                overlays,
                output_duration,
                clip.headline,
                clip.mode == "split",
            )
            if clip.quality != QualityOptions():
                render_options["quality"] = clip.quality
            if clip.crop_region is not None:
                render_options["crop_region"] = clip.crop_region
            if clip.split_layout is not None:
                render_options["split_layout"] = clip.split_layout
            if clip.captions is not None:
                words = self.store.caption_words(clip.id)
                if words is None:
                    raise MediaError("The export's caption snapshot is missing.")
                words = mapped_words(words, kept, clip.start)
                count = write_ass(
                    words,
                    clip.start,
                    clip.start + output_duration,
                    caption_path,
                    position=caption_position,
                    preset=clip.captions.preset,
                    emphasis_words=clip.captions.emphasis_words,
                    customization=clip.captions.customization,
                    presentation_version=clip.captions.presentation_version,
                )
                if not count:
                    raise MediaError(
                        "No caption words overlap this selection. Export without captions or adjust the selection."
                    )
                render_options["caption_path"] = caption_path
            if overlays:
                write_presentation(
                    headline_path,
                    overlays,
                    output_duration,
                    clip.headline,
                    caption_position,
                )
                render_options["headline_path"] = headline_path
            elif clip.headline is not None:
                write_headline(
                    headline_path,
                    clip.headline,
                    output_duration,
                    caption_position,
                )
                render_options["headline_path"] = headline_path
            info = self.media.render(
                self.store.source_path(clip.recording_id),
                partial,
                clip.start,
                clip.end,
                clip.mode,
                self.cancel_event,
                self.store.root / "logs" / f"{clip.id}.log",
                **render_options,
            )
            with self.lock:
                if self.cancel_event.is_set():
                    raise RenderCancelled()
                partial.replace(self.store.export_path(clip.id))
                self.store.update_clip(
                    clip.id,
                    "succeeded",
                    duration=info.duration,
                    size_bytes=self.store.export_path(clip.id).stat().st_size,
                    performance={
                        "backend": info.render_backend,
                        "warning": info.render_warning,
                        "seconds": info.render_seconds,
                    }
                    if info.render_backend
                    else None,
                )
        except RenderCancelled:
            with self.lock:
                self.store.update_clip(clip.id, "cancelled")
        except (MediaError, CaptionError) as exc:
            with self.lock:
                self.store.update_clip(clip.id, "failed", str(exc))
        except Exception:
            logger.exception("Unexpected export failure for %s", clip.id)
            with self.lock:
                self.store.update_clip(
                    clip.id, "failed", "Unexpected export failure. Check the server log."
                )
        finally:
            try:
                remove_artifact(partial)
                remove_artifact(caption_path)
                remove_artifact(headline_path)
                remove_artifact(cover_path)
            finally:
                with self.lock:
                    self.active = None

    def close(self) -> None:
        with self.lock:
            self.stopping = True
            self.cancel_event.set()
        self.pool.shutdown(wait=True)
