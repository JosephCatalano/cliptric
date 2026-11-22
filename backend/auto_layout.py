"""Conservative visual layout selection. No gameplay or speaker recognition claims."""

import math

import cv2
import numpy as np

from .follow_models import MAX_FOLLOW_SECONDS
from .framing import SourceRegion, SplitLayout
from .layout_models import AutoLayoutPlan
from .media import RenderCancelled
from .presentation import AutoPresentation, text_regions


def camera_panel(frame, face):
    """Require a closed, rectangular inset around a small peripheral face."""
    x, y, w, h = face
    if w > 0.16 or w * h > 0.035 or not (x + w / 2 < 0.3 or x + w / 2 > 0.7):
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    edges = cv2.Canny(gray, 50, 130)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, perimeter * 0.02, True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        a, b, c, d = cv2.boundingRect(polygon)
        left, top, rw, rh = a / width, b / height, c / width, d / height
        if not (0.1 <= rw <= 0.42 and 0.1 <= rh <= 0.5 and 0.75 <= c / d <= 2.5):
            continue
        if cv2.contourArea(polygon) / (c * d) < 0.85:
            continue
        if (
            left <= x - w * 0.08
            and top <= y - h * 0.08
            and left + rw >= x + w * 1.08
            and top + rh >= y + h * 1.08
        ):
            candidates.append((rw * rh, left, top, rw, rh))
    if not candidates:
        return None
    _, x, y, w, h = min(candidates)
    return SourceRegion(x=x, y=y, width=min(w, 1 - x), height=min(h, 1 - y))


def portrait_inside(region, boxes, width, height):
    """Center a fixed portrait on the observed face without leaving its source panel."""
    ratio = 9 * height / (16 * width)
    h = min(region.height, region.width / ratio)
    w = h * ratio
    cx = float(np.median([x + bw / 2 for x, _, bw, _ in boxes]))
    cy = float(np.median([y + bh / 2 for _, y, _, bh in boxes]))
    x = max(region.x, min(region.x + region.width - w, cx - w / 2))
    y = max(region.y, min(region.y + region.height - h, cy - h * 0.36))
    if min(w, h) < 0.01 or any(
        a < x or b < y or a + c > x + w or b + d > y + h for a, b, c, d in boxes
    ):
        return None
    return SourceRegion(x=x, y=y, width=w, height=h)


def panel_border_visible(frame, panel: SourceRegion) -> bool:
    """Verify known edges when compression breaks an otherwise closed contour."""
    edges = cv2.Canny(cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY), 30, 80)
    height, width = edges.shape
    x0, x1 = round(panel.x * width), round((panel.x + panel.width) * width) - 1
    y0, y1 = round(panel.y * height), round((panel.y + panel.height) * height) - 1
    if x1 - x0 < 8 or y1 - y0 < 8:
        return False
    sides = [
        edges[y0 + 2 : y1 - 1, max(0, x - 2) : min(width, x + 3)].max(axis=1) for x in (x0, x1)
    ] + [edges[max(0, y - 2) : min(height, y + 3), x0 + 2 : x1 - 1].max(axis=0) for y in (y0, y1)]
    coverage = [float(np.mean(side > 0)) for side in sides]
    return min(coverage) >= 0.55 and sum(coverage) / 4 >= 0.7


def choose_layout(samples, faces, width, height, start, end):
    def plan(mode, summary, **kwargs):
        return AutoLayoutPlan(start=start, end=end, mode=mode, summary=summary, **kwargs)

    if len(samples) < 2 or len(samples) != len(faces) or any(len(boxes) != 1 for boxes in faces):
        return plan("pad", "Full picture: no single consistent face or multiple participants.")
    boxes = [values[0] for values in faces]
    panels = [
        panel
        for (_, frame), box in zip(samples, boxes)
        if (panel := camera_panel(frame, box)) is not None
    ]
    if len(panels) >= max(2, math.ceil(len(samples) * 0.25)):
        first = panels[0]
        if all(
            max(abs(getattr(panel, k) - getattr(first, k)) for k in ("x", "y", "width", "height"))
            <= 0.025
            for panel in panels
        ):
            # Use the intersection so slight border-detection variation cannot
            # pull gameplay pixels into the enlarged webcam panel.
            x, y = max(p.x for p in panels), max(p.y for p in panels)
            right, bottom = min(p.x + p.width for p in panels), min(p.y + p.height for p in panels)
            camera = SourceRegion(x=x, y=y, width=right - x, height=bottom - y)
            if not all(panel_border_visible(frame, camera) for _, frame in samples):
                return plan(
                    "pad", "Full picture: the webcam boundary was not consistently visible."
                )
            if not all(
                camera.x <= a
                and camera.y <= b
                and camera.x + camera.width >= a + c
                and camera.y + camera.height >= b + d
                for a, b, c, d in boxes
            ):
                return plan("pad", "Full picture: the webcam border could not be confirmed safely.")
            activity = []
            for (_, a), (_, b) in zip(samples, samples[1:]):
                delta = (
                    cv2.absdiff(
                        cv2.cvtColor(a, cv2.COLOR_RGB2GRAY), cv2.cvtColor(b, cv2.COLOR_RGB2GRAY)
                    ).astype(np.float32)
                    / 255
                )
                fh, fw = delta.shape
                mask = np.ones(delta.shape, dtype=bool)
                mask[
                    int(camera.y * fh) : math.ceil((camera.y + camera.height) * fh),
                    int(camera.x * fw) : math.ceil((camera.x + camera.width) * fw),
                ] = False
                activity.append(float(np.mean(delta[mask])))
            protected = [
                r
                for _, frame in samples
                for r in text_regions(frame)
                if not (
                    r.x >= camera.x
                    and r.y >= camera.y
                    and r.x + r.width <= camera.x + camera.width
                    and r.y + r.height <= camera.y + camera.height
                )
            ]
            crop = portrait_inside(camera, boxes, width, height)
            if max(activity, default=1) < 0.008 and not protected and crop:
                return plan(
                    "crop",
                    "Webcam focus: stable surrounding picture and a clear webcam inset.",
                    crop_region=crop,
                    warnings=[
                        "The surrounding picture is hidden in this webcam-focused layout. Review the choice."
                    ],
                )
            return plan(
                "split",
                "Webcam above source: stable inset with surrounding activity or text.",
                split_layout=SplitLayout(
                    webcam=camera,
                    gameplay=SourceRegion(x=0, y=0, width=1, height=1),
                    gameplay_fit="fit",
                ),
                warnings=[
                    "The lower panel preserves the original picture and its inset to retain source context."
                ],
            )
    prominent = all(w >= 0.14 and w * h >= 0.025 for _, _, w, h in boxes)
    if not prominent:
        return plan("pad", "Full picture: a small peripheral face has no confirmed webcam panel.")
    protected = [r for _, frame in samples for r in text_regions(frame)]
    full = SourceRegion(x=0, y=0, width=1, height=1)
    crop = portrait_inside(full, boxes, width, height)
    if crop and any(
        r.x < crop.x
        or r.y < crop.y
        or r.x + r.width > crop.x + crop.width
        or r.y + r.height > crop.y + crop.height
        for r in protected
    ):
        return plan("pad", "Full picture: a portrait crop would discard detected text.")
    travel = max(x + w / 2 for x, _, w, _ in boxes) - min(x + w / 2 for x, _, w, _ in boxes)
    if crop and travel <= 0.025:
        return plan("crop", "Face focus: a clear, steady single-person shot.", crop_region=crop)
    return plan("follow", "Auto follow: a clear single person moves through the picture.")


class AutoLayout:
    def __init__(self, media):
        self.presentation = AutoPresentation(media)

    def analyze(self, source, recording, start, end, cancel):
        if cancel.is_set():
            raise RenderCancelled()
        if end - start > MAX_FOLLOW_SECONDS:
            return AutoLayoutPlan(
                start=start,
                end=end,
                mode="pad",
                summary="Full picture: automatic layout analyzes clips up to 10 minutes.",
            )
        samples, faces = self.presentation.sample(source, start, end, cancel, lambda: None)
        return choose_layout(samples, faces, recording.width, recording.height, start, end)
