"""Bounded local speech/pause and visual framing proposals, never automatic acceptance."""

import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal
from uuid import UUID

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .editing import FramingPoint, QualityOptions, Removal, SmartFraming, retained_intervals
from .framing import SourceRegion
from .media import MediaError, RenderCancelled
from .transcription_models import TranscriptionError
from .twitch_rights import processing_error


class QualityAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    start: float = Field(ge=0, strict=True)
    end: float = Field(gt=0, strict=True)
    transcription_id: UUID | None = None
    revision: int | None = Field(default=None, ge=1, strict=True)
    silence: bool = Field(default=True, strict=True)
    fillers: bool = Field(default=False, strict=True)
    threshold_db: int = Field(default=-38, ge=-50, le=-20, strict=True)
    minimum_pause: float = Field(default=0.8, ge=0.4, le=3, strict=True)
    keep_pause: float = Field(default=0.4, ge=0.2, le=1, strict=True)
    detector: Literal["off", "faces", "motion"] = "off"
    protected_regions: list[SourceRegion] = Field(default_factory=list, max_length=4)


def merge_removals(cuts: list[Removal]) -> list[Removal]:
    merged = []
    for cut in sorted(cuts, key=lambda value: value.start):
        if merged and cut.start <= merged[-1].end:
            previous = merged[-1]
            merged[-1] = Removal(
                start=previous.start,
                end=max(previous.end, cut.end),
                reason="filler" if "filler" in (previous.reason, cut.reason) else "silence",
            )
        else:
            merged.append(cut)
    return merged


def visual_plan(frames, source_width, source_height, start, end, detector, protected):
    """Track bounded observations with a fixed-size crop; preserve context on ambiguity."""
    cv2.setNumThreads(1)
    cascade = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    )
    if detector == "faces" and cascade.empty():
        raise MediaError(
            "The installed face detector data is missing. Reinstall the pinned OpenCV package."
        )
    crop_w = min(1.0, 9 / 16 * source_height / source_width)
    crop_h = min(1.0, source_width / source_height * 16 / 9)
    observations = []
    detections = 0
    previous_gray = None
    center = (0.5, 0.5)
    missing = 0
    deadline = time.monotonic() + 20
    for index, frame in enumerate(frames):
        if time.monotonic() > deadline:
            raise MediaError("Visual framing analysis exceeded its processing limit.")
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        boxes = []
        if detector == "faces":
            boxes = list(
                cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(20, 20))
            )
        elif previous_gray is not None:
            change = cv2.absdiff(gray, previous_gray)
            mask = cv2.threshold(change, 25, 255, cv2.THRESH_BINARY)[1]
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            boxes = [
                cv2.boundingRect(contour) for contour in contours if cv2.contourArea(contour) >= 30
            ]
        previous_gray = gray
        required = [
            (region.x, region.y, region.x + region.width, region.y + region.height)
            for region in protected
        ]
        height, width = gray.shape
        for x, y, w, h in boxes:
            # Margin retains a little context around faces/moving regions.
            required.append(
                (
                    max(0.0, (x - w * 0.25) / width),
                    max(0.0, (y - h * 0.25) / height),
                    min(1.0, (x + w * 1.25) / width),
                    min(1.0, (y + h * 1.25) / height),
                )
            )
        if not boxes:
            missing += 1
        else:
            detections += 1
            missing = 0
        if missing > 3:
            return SmartFraming(
                detector=detector,
                strategy="fit",
                width=1.0,
                height=1.0,
                points=[FramingPoint(at=start, x=0.0, y=0.0)],
                protected_regions=protected,
                warnings=["No stable visual target was found. The full picture is preserved."],
            )
        if required:
            left, top, right, bottom = (
                min(b[0] for b in required),
                min(b[1] for b in required),
                max(b[2] for b in required),
                max(b[3] for b in required),
            )
            if right - left > crop_w - 0.01 or bottom - top > crop_h - 0.01:
                return SmartFraming(
                    detector=detector,
                    strategy="fit",
                    width=1.0,
                    height=1.0,
                    points=[FramingPoint(at=start, x=0.0, y=0.0)],
                    protected_regions=protected,
                    warnings=[
                        "Targets or protected HUD/context do not fit a safe vertical crop. The full picture is preserved."
                    ],
                )
            target = ((left + right) / 2, (top + bottom) / 2)
            if index == 0:
                center = target
            else:
                center = tuple(
                    old + max(-0.08, min(0.08, 0.35 * (new - old)))
                    if abs(new - old) > 0.015
                    else old
                    for old, new in zip(center, target)
                )
        x = min(1 - crop_w, max(0.0, center[0] - crop_w / 2))
        y = min(1 - crop_h, max(0.0, center[1] - crop_h / 2))
        # If smoothing would lose a target, choose fit rather than jerk the crop.
        if any(a < x or b < y or c > x + crop_w or d > y + crop_h for a, b, c, d in required):
            return SmartFraming(
                detector=detector,
                strategy="fit",
                width=1.0,
                height=1.0,
                points=[FramingPoint(at=start, x=0.0, y=0.0)],
                protected_regions=protected,
                warnings=[
                    "Target movement could not be kept within a stable crop. The full picture is preserved."
                ],
            )
        observations.append((min(end, start + index * 0.5), x, y, required))
    if not observations:
        raise MediaError("Visual analysis returned no source frames.")
    if not detections or min(crop_w, crop_h) < 0.05:
        return SmartFraming(
            detector=detector,
            strategy="fit",
            width=1.0,
            height=1.0,
            points=[FramingPoint(at=start, x=0.0, y=0.0)],
            protected_regions=protected,
            warnings=[
                "No reliable target or sufficiently informative crop was established. The full picture is preserved."
            ],
        )
    indices = sorted(
        set(np.linspace(0, len(observations) - 1, min(24, len(observations))).astype(int).tolist())
    )
    points = [
        FramingPoint(
            at=float(observations[i][0]), x=float(observations[i][1]), y=float(observations[i][2])
        )
        for i in indices
    ]
    # Check every sampled observation against the simplified interpolation, not just keyframes.
    for at, _, _, required in observations:
        segment = next(
            ((a, b) for a, b in zip(points, points[1:]) if a.at <= at <= b.at),
            (points[-1], points[-1]),
        )
        a, b = segment
        fraction = (at - a.at) / (b.at - a.at) if b.at > a.at else 0
        x, y = a.x + (b.x - a.x) * fraction, a.y + (b.y - a.y) * fraction
        if any(
            left < x or top < y or right > x + crop_w or bottom > y + crop_h
            for left, top, right, bottom in required
        ):
            return SmartFraming(
                detector=detector,
                strategy="fit",
                width=1.0,
                height=1.0,
                points=[FramingPoint(at=start, x=0.0, y=0.0)],
                protected_regions=protected,
                warnings=[
                    "A sampled target would leave the simplified framing path. The full picture is preserved."
                ],
            )
    return SmartFraming(
        detector=detector,
        strategy="crop",
        width=crop_w,
        height=crop_h,
        points=points,
        protected_regions=protected,
        warnings=[
            "Sampled face/motion framing requires playback review. This does not identify speakers or understand gameplay events; fast unsampled action can be missed."
        ],
    )


class QualityAnalysis:
    def __init__(self, store, media, transcriptions):
        self.store, self.media, self.transcriptions = store, media, transcriptions
        self.stop = threading.Event()
        self.slot = threading.Semaphore(1)
        self.root = store.root / "quality-work"
        self.root.mkdir(exist_ok=True)

    def initialize(self):
        # Recover only known scratch files, without recursive deletion or external links.
        root = self.root.resolve()
        for folder in self.root.glob("analysis-*"):
            if folder.resolve().parent != root or not folder.is_dir():
                raise RuntimeError(
                    "Unsafe quality-analysis recovery path. Inspect local scratch storage."
                )
            for name in ("silence.log", "visual.log", "frames.rgb"):
                artifact = folder / name
                if artifact.exists():
                    if artifact.resolve().parent != folder.resolve():
                        raise RuntimeError(
                            "Unsafe quality-analysis artifact. Inspect local scratch storage."
                        )
                    artifact.unlink()
            folder.rmdir()

    def run_process(self, command, log, output=None):
        with log.open("wb") as diagnostics:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=diagnostics)
            deadline = time.monotonic() + 30
            try:
                while process.poll() is None:
                    if self.stop.wait(0.05):
                        raise RenderCancelled()
                    if (
                        time.monotonic() > deadline
                        or diagnostics.tell() > 1024**2
                        or (
                            output is not None
                            and output.exists()
                            and output.stat().st_size > 96 * 1024**2
                        )
                    ):
                        raise MediaError("Quality analysis exceeded its time or output limit.")
                if process.returncode:
                    raise MediaError("Cannot analyze this source for output quality.")
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()

    def analyze(self, identifier, request):
        if blocked := processing_error(self.store, identifier):
            raise HTTPException(403, blocked)
        if self.stop.is_set() or not self.slot.acquire(blocking=False):
            raise HTTPException(409, "Quality analysis is busy. Wait before trying another clip.")
        try:
            recording = self.store.recording(identifier)
            if not recording:
                raise HTTPException(404, "Recording not found.")
            if not 0.1 <= request.end - request.start <= 120 or request.end > recording.duration:
                raise HTTPException(422, "Choose a valid source interval of at most 120 seconds.")
            words = []
            if request.transcription_id is not None:
                if request.revision is None:
                    raise HTTPException(422, "Supply the saved transcript revision.")
                words = self.transcriptions.transcripts.words(
                    str(request.transcription_id),
                    max(0, request.start - 6),
                    min(recording.duration, request.end + 6),
                    revision=request.revision,
                    recording_id=identifier,
                ).words
                if len(words) > 3000:
                    raise HTTPException(
                        422, "Select a smaller interval for bounded speech-edit analysis."
                    )
            elif request.fillers:
                raise HTTPException(422, "Save a transcript before proposing filler-word removals.")
            source = self.store.source_path(identifier)
            info = self.media.probe(source)
            warnings = [
                "Review every proposed removal. Silence detection cannot determine whether a pause is intentional."
            ]
            common = [
                self.media.settings.ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-n",
                "-max_alloc",
                "268435456",
                "-threads",
                "2",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                "mov,matroska,webm",
                "-ss",
                str(request.start),
                "-i",
                str(source.resolve()),
                "-t",
                str(request.end - request.start),
            ]
            cuts = []
            with tempfile.TemporaryDirectory(prefix="analysis-", dir=self.root) as temporary:
                root = Path(temporary)
                if request.silence:
                    if info.audio_index is None:
                        raise HTTPException(422, "This source has no audio for silence analysis.")
                    log = root / "silence.log"
                    self.run_process(
                        [
                            *common,
                            "-map",
                            f"0:{info.audio_index}",
                            "-vn",
                            "-sn",
                            "-dn",
                            "-af",
                            f"silencedetect=noise={request.threshold_db}dB:d={request.minimum_pause}",
                            "-f",
                            "null",
                            "-",
                        ],
                        log,
                    )
                    data = log.read_text(encoding="utf-8", errors="replace")
                    silence_start = None
                    for kind, raw in re.findall(r"silence_(start|end):\s*(-?[0-9.]+)", data):
                        value = max(request.start, min(request.end, request.start + float(raw)))
                        if kind == "start":
                            silence_start = value
                        elif silence_start is not None:
                            first, last = (
                                silence_start + request.keep_pause / 2,
                                value - request.keep_pause / 2,
                            )
                            # Protect recognized speech, even when the detector calls it quiet.
                            if last - first >= 0.1 and not any(
                                word.start < last and word.end > first for word in words
                            ):
                                cuts.append(Removal(start=first, end=last, reason="silence"))
                            silence_start = None
                if request.fillers:
                    for word in words:
                        if (
                            re.sub(r"\W+", "", word.text.casefold()) not in {"um", "uh"}
                            or word.start < request.start
                            or word.end > request.end
                        ):
                            continue
                        if any(
                            other.id != word.id
                            and other.start < word.end + 0.02
                            and other.end > word.start - 0.02
                            for other in words
                        ):
                            continue
                        if word.end - word.start >= 0.02:
                            cuts.append(Removal(start=word.start, end=word.end, reason="filler"))
                cuts = merge_removals(cuts)
                if len(cuts) > 40:
                    warnings.append(
                        "Only the first 40 removal proposals are shown. Analyze a shorter interval for more."
                    )
                    cuts = cuts[:40]
                try:
                    kept = retained_intervals(
                        request.start, request.end, QualityOptions(removals=cuts)
                    )
                except ValueError:
                    cuts = [cut.model_copy(update={"enabled": False}) for cut in cuts]
                    warnings.append(
                        "The combined plan removes too much or leaves tiny fragments. Removals are disabled; select a smaller subset."
                    )
                    kept = [(request.start, request.end)]
                framing = None
                if request.detector != "off":
                    width = min(320, info.width) // 2 * 2
                    height = max(2, int(width * info.height / info.width) // 2 * 2)
                    if height > 320:
                        height = 320
                        width = max(2, int(height * info.width / info.height) // 2 * 2)
                    raw = root / "frames.rgb"
                    self.run_process(
                        [
                            *common,
                            "-map",
                            f"0:{info.video_index}",
                            "-an",
                            "-sn",
                            "-dn",
                            "-vf",
                            f"fps=2,scale={width}:{height},setsar=1",
                            "-frames:v",
                            "240",
                            "-pix_fmt",
                            "rgb24",
                            "-f",
                            "rawvideo",
                            str(raw),
                        ],
                        root / "visual.log",
                        raw,
                    )
                    size = raw.stat().st_size
                    if not size or size % (width * height * 3):
                        raise MediaError("Visual analysis returned incomplete frames.")
                    frames = np.fromfile(raw, dtype=np.uint8).reshape((-1, height, width, 3))
                    framing = visual_plan(
                        frames,
                        info.width,
                        info.height,
                        request.start,
                        request.end,
                        request.detector,
                        request.protected_regions,
                    )
                if request.transcription_id:
                    current = self.transcriptions.transcripts.job(str(request.transcription_id))
                    if current is None or current.revision != request.revision:
                        raise HTTPException(
                            409,
                            "Transcript changed during quality analysis. Reanalyze the saved revision.",
                        )
                selected = [
                    word
                    for word in words
                    if word.start < request.end and word.end > request.start and word.text
                ]
                suggested_start = (
                    max(0, min(request.start, selected[0].start - 0.12))
                    if selected
                    else request.start
                )
                suggested_end = (
                    min(recording.duration, max(request.end, selected[-1].end + 0.18))
                    if selected
                    else request.end
                )
                if suggested_end - suggested_start > 120:
                    suggested_start, suggested_end = request.start, request.end
                return {
                    "removals": cuts,
                    "smart_framing": framing,
                    "retained_intervals": kept,
                    "output_duration": sum(b - a for a, b in kept),
                    "suggested_start": suggested_start,
                    "suggested_end": suggested_end,
                    "warnings": warnings,
                }
        except (MediaError, TranscriptionError, ValueError) as exc:
            raise HTTPException(getattr(exc, "status_code", 422), str(exc)) from exc
        finally:
            self.slot.release()


def create_quality_router(manager):
    router = APIRouter()

    @router.post("/api/recordings/{identifier}/quality-plan")
    def analyze(identifier: UUID, request: QualityAnalysisRequest):
        return manager.analyze(str(identifier), request)

    return router
