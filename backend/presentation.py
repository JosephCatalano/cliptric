"""Bounded local presentation plans; no identity or semantic scene claims."""

import logging
import tempfile
import time
from pathlib import Path
from typing import Callable, Literal

import cv2
import numpy as np
from pydantic import BaseModel, Field

from .editing import FramingPoint, SmartFraming
from .files import remove_artifact
from .framing import FramingMode, SourceRegion, SplitLayout
from .media import MediaError


class PresentationPlan(BaseModel):
    framing: FramingMode = "pad"
    split_layout: SplitLayout | None = None
    smart_framing: SmartFraming | None = None
    cover_at: float | None = None
    caption_position: Literal["lower", "centre", "upper"] = "lower"
    headline_position: Literal["upper", "centre"] = "upper"
    summary: str
    warnings: list[str] = Field(default_factory=list)


def text_regions(frame) -> list[SourceRegion]:
    """Protect text-like horizontal bands conservatively, without claiming OCR."""
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    gradient = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, np.ones((2, 2), np.uint8))
    _, mask = cv2.threshold(gradient, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 13), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = gray.shape
    result = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        x, y, w, h = cv2.boundingRect(contour)
        if w >= width * 0.2 and height * 0.018 <= h <= height * 0.18 and w / h >= 4:
            left, top = max(0, x - 3) / width, max(0, y - 3) / height
            right, bottom = min(width, x + w + 3) / width, min(height, y + h + 3) / height
            result.append(SourceRegion(x=left, y=top, width=right - left, height=bottom - top))
            if len(result) == 4:
                break
    return result


def face_boxes(frame, cascade):
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    return [
        (x / width, y / height, w / width, h / height)
        for x, y, w, h in cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24)
        )
    ]


def plan_frames(
    samples, faces, width, height, start, end, mode, caption_position, headline_position
):
    """Prefer a stable single-face crop; keep group shots, gameplay and text intact."""
    protected = [region for _, frame in samples for region in text_regions(frame)]
    warnings = [
        "Sampled visual analysis does not identify active speakers or understand gameplay. Review fast action and existing text before posting."
    ]
    if protected:
        warnings.append(
            "Text-like bands were detected; the full source picture is retained. This is conservative detection, not OCR."
        )
    stable = all(len(boxes) == 1 for boxes in faces)
    prominent = stable and all(
        w >= 0.14 and w * h >= 0.025 and 0.2 <= x + w / 2 <= 0.8
        for boxes in faces
        for x, _y, w, h in boxes
    )
    crop_width = min(1.0, height * 9 / 16 / width)
    crop_height = min(1.0, width * 16 / 9 / height)
    points = []
    center = None
    if (
        stable
        and (prominent or mode == "talking_head")
        and not protected
        and mode not in {"gameplay", "podcast"}
    ):
        for (at, _), boxes in zip(samples, faces):
            x, y, w, h = boxes[0]
            desired = (x + w / 2, y + h / 2)
            center = (
                desired
                if center is None
                else tuple(
                    old + max(-0.08, min(0.08, (new - old) * 0.35))
                    for old, new in zip(center, desired)
                )
            )
            left, top = (
                max(0, min(1 - crop_width, center[0] - crop_width / 2)),
                max(0, min(1 - crop_height, center[1] - crop_height / 2)),
            )
            if x < left or y < top or x + w > left + crop_width or y + h > top + crop_height:
                points = []
                break
            points.append(FramingPoint(at=at, x=left, y=top))
    smart = (
        SmartFraming(
            detector="faces", strategy="crop", width=crop_width, height=crop_height, points=points
        )
        if points
        else None
    )
    summary = (
        "Talking-head layout with a smooth sampled face-following crop."
        if smart
        else "Full-picture layout with blur fill preserves the source context."
    )
    if mode == "podcast" or any(len(boxes) > 1 for boxes in faces):
        summary = "Podcast/group layout keeps all visible participants in the full picture."
    if mode == "gameplay":
        summary = "Gameplay layout preserves the full frame and HUD. Configure a webcam split in Trim / edit when needed."
    # Rank opening frames by sharpness, usable exposure and visible faces.
    opening = [
        (index, at, frame) for index, (at, frame) in enumerate(samples) if at < min(end, start + 8)
    ]

    def clarity(entry):
        index, _, frame = entry
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        usable = np.mean((gray > 15) & (gray < 245))
        return float(
            usable * (1 + np.log1p(cv2.Laplacian(gray, cv2.CV_64F).var()))
            + min(2, len(faces[index])) * 0.2
        )

    cover_at = max(opening, key=clarity)[1] if opening else start
    # Map sampled faces/text into the rendered portrait for placement scoring.
    obstacles = []
    for index, ((_, _frame), boxes) in enumerate(zip(samples, faces)):
        regions = [(x, y, w, h, 10) for x, y, w, h in boxes]
        regions += [(r.x, r.y, r.width, r.height, 5) for r in protected]
        for x, y, w, h, weight in regions:
            if smart:
                point = points[index]
                rect = (
                    (x - point.x) / crop_width,
                    (y - point.y) / crop_height,
                    w / crop_width,
                    h / crop_height,
                )
            else:
                scale = min(1080 / width, 1920 / height)
                rect = (
                    (1080 - width * scale) / 2160 + x * width * scale / 1080,
                    (1920 - height * scale) / 3840 + y * height * scale / 1920,
                    w * width * scale / 1080,
                    h * height * scale / 1920,
                )
            obstacles.append((*rect, weight))

    def cost(rect):
        x, y, w, h = rect
        return sum(
            max(0, min(x + w, ox + ow) - max(x, ox))
            * max(0, min(y + h, oy + oh) - max(y, oy))
            * weight
            for ox, oy, ow, oh, weight in obstacles
        )

    headline_regions = {"upper": (0.09, 0.125, 0.82, 0.20), "centre": (0.09, 0.36, 0.82, 0.22)}
    allowed_headlines = [position for position in headline_regions if position != caption_position]
    headline = (
        min(allowed_headlines, key=lambda p: cost(headline_regions[p]))
        if headline_position == "auto"
        else headline_position
    )
    caption_regions = {
        "lower": (0.09, 0.70, 0.82, 0.16),
        "upper": (0.09, 0.18, 0.82, 0.16),
        "centre": (0.09, 0.43, 0.82, 0.16),
    }
    choices = [p for p in caption_regions if p != headline]
    caption = (
        min(choices, key=lambda p: cost(caption_regions[p]))
        if caption_position == "auto"
        else caption_position
    )
    if cost(headline_regions[headline]) or cost(caption_regions[caption]):
        warnings.append(
            "Available text regions may overlap source content. Review placement or disable the headline/captions."
        )
    return PresentationPlan(
        smart_framing=smart,
        cover_at=cover_at,
        caption_position=caption,
        headline_position=headline,
        summary=summary,
        warnings=warnings,
    )


class AutoPresentation:
    def __init__(self, media):
        self.media = media
        self.media.settings.data_dir.mkdir(parents=True, exist_ok=True)

    def initialize(self):
        root = self.media.settings.data_dir.resolve()
        for folder in root.glob("presentation-*"):
            if (
                folder.is_symlink()
                or not folder.is_dir()
                or not folder.resolve().is_relative_to(root)
            ):
                continue
            for frame in folder.glob("*.jpg"):
                if frame.stem.isdigit():
                    remove_artifact(frame)
            try:
                folder.rmdir()
            except OSError:
                logging.getLogger(__name__).warning(
                    "Presentation scratch directory contains files still in use: %s", folder.name
                )

    def analyze(
        self,
        source: Path,
        recording,
        start: float,
        end: float,
        mode: str,
        caption_position: str,
        headline_position: str,
        cancel,
        check: Callable[[], None],
    ):
        cv2.setNumThreads(1)
        cascade = cv2.CascadeClassifier(
            str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        )
        if cascade.empty():
            raise MediaError("Face detector data is unavailable for automatic presentation.")
        info = self.media.probe(source)
        # Seek before the final display frame, including low-frame-rate sources.
        last = end - min(max(0.1, 1.1 / info.frame_rate), (end - start) / 2)
        times = sorted(
            set(
                float(t)
                for t in [
                    *np.linspace(start, last, 10),
                    *np.linspace(start, min(last, start + 7.5), 6),
                ]
            )
        )
        samples, faces = [], []
        deadline = time.monotonic() + 90
        with tempfile.TemporaryDirectory(
            prefix="presentation-", dir=self.media.settings.data_dir
        ) as temporary:
            if not Path(temporary).resolve().is_relative_to(self.media.settings.data_dir.resolve()):
                raise MediaError("Presentation scratch directory is outside local storage.")
            for index, at in enumerate(times):
                check()
                if time.monotonic() > deadline:
                    raise MediaError("Automatic presentation exceeded its local analysis budget.")
                path = Path(temporary) / f"{index}.jpg"
                self.media.thumbnail(source, path, at, cancel, max_dimension=384)
                frame = cv2.imread(str(path))
                if frame is None or max(frame.shape[:2]) > 384:
                    raise MediaError("Automatic presentation returned an invalid frame.")
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                samples.append((at, frame))
                faces.append(face_boxes(frame, cascade))
        check()
        return plan_frames(
            samples,
            faces,
            recording.width,
            recording.height,
            start,
            end,
            mode,
            caption_position,
            headline_position,
        )
