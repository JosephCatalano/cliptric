"""Bounded local single-face tracking. Never identifies who is speaking."""

import math
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from .editing import FramingPoint
from .files import remove_artifact, require_space
from .follow_models import MAX_FOLLOW_SECONDS, FollowPlan, follow_fit
from .media import Media, MediaError, RenderCancelled
from .presentation import text_regions

FaceBox = tuple[float, float, float, float]
Observation = tuple[list[FaceBox], list[FaceBox]]


def tracking_plan(
    observations: list[Observation], width: int, height: int, start: float, end: float
) -> FollowPlan:
    """Smooth one unambiguous face; brief gaps hold the previous composition."""
    if any(len(boxes) > 1 for boxes, _ in observations):
        return follow_fit(
            start, end, "Multiple faces were detected. Auto follow keeps the full picture."
        )
    valid = [(i, boxes[0]) for i, (boxes, _) in enumerate(observations) if len(boxes) == 1]
    if not valid:
        return follow_fit(start, end, "No clear face was found. The full picture is preserved.")
    full_height = min(1.0, width * 16 / (height * 9))
    crop_h = min(full_height, max(0.65, max(box[3] for _, box in valid) * 2.8))
    crop_w = crop_h * height * 9 / (width * 16)
    if min(crop_w, crop_h) < 0.01:
        return follow_fit(start, end, "This source shape cannot support a safe portrait crop.")
    if valid[0][0] > 2:
        return follow_fit(
            start, end, "The opening has no stable face. The full picture is preserved."
        )
    last_box = valid[0][1]
    center = None
    missing = 0
    points = []
    for i, (boxes, protected) in enumerate(observations):
        if boxes:
            box = boxes[0]
            # A new distant face after a dropout is ambiguous, not a new target.
            if missing and math.hypot(box[0] - last_box[0], box[1] - last_box[1]) > 0.18:
                return follow_fit(
                    start,
                    end,
                    "The visual target changed after a gap. The full picture is preserved.",
                )
            last_box = box
            missing = 0
        else:
            missing += 1
            if missing > 3:
                return follow_fit(
                    start, end, "The face was lost for too long. The full picture is preserved."
                )
        x, y, w, h = last_box
        desired = (x + w / 2, y + h / 2 + crop_h * 0.14)
        if center is None:
            center = desired
        elif boxes:
            center = tuple(
                old if abs(new - old) <= 0.015 else old + max(-0.09, min(0.09, (new - old) * 0.6))
                for old, new in zip(center, desired)
            )
        left, top = (
            max(0.0, min(1 - crop_w, center[0] - crop_w / 2)),
            max(0.0, min(1 - crop_h, center[1] - crop_h / 2)),
        )
        required = [(x - w * 0.1, y - h * 0.15, x + w * 1.1, y + h * 1.1), *protected]
        if any(
            max(0, a) < left - 1e-8
            or max(0, b) < top - 1e-8
            or min(1, c) > left + crop_w + 1e-8
            or min(1, d) > top + crop_h + 1e-8
            for a, b, c, d in required
        ):
            return follow_fit(
                start,
                end,
                "A safe crop would lose the face or existing text. The full picture is preserved.",
            )
        points.append(FramingPoint(at=start + i * 0.5, x=left, y=top))
    if points[-1].at < end:
        points.append(FramingPoint(at=end, x=points[-1].x, y=points[-1].y))
    return FollowPlan(
        start=start, end=end, strategy="crop", width=crop_w, height=crop_h, points=points
    )


class AutoFollow:
    def __init__(self, media: Media):
        self.media = media

    def analyze(self, source: Path, start: float, end: float, cancel, root: Path) -> FollowPlan:
        if cancel.is_set():
            raise RenderCancelled()
        if not all(math.isfinite(t) for t in (start, end)) or start < 0 or end <= start:
            raise MediaError("Choose a valid source interval for Auto follow.")
        if end - start > MAX_FOLLOW_SECONDS:
            return follow_fit(
                start,
                end,
                "Auto follow analyzes clips up to 10 minutes. The full picture is preserved for this longer clip.",
            )
        if end - start < 0.5:
            return follow_fit(
                start, end, "This clip is too short to establish a stable face track."
            )
        info = self.media.probe(source)
        if end > info.duration + 0.05:
            raise MediaError("Auto follow exceeds the current source duration.")
        scale = min(1.0, 384 / max(info.width, info.height))
        width, height = (
            max(2, int(info.width * scale) // 2 * 2),
            max(2, int(info.height * scale) // 2 * 2),
        )
        detector = cv2.CascadeClassifier(
            str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        )
        if detector.empty():
            raise MediaError(
                "Auto follow face detector data is missing. Reinstall the pinned OpenCV package."
            )
        deadline = time.monotonic() + 120
        require_space(root, self.media.settings.min_free_bytes)
        identifier = uuid4()
        raw, log = (
            root / f"{identifier}.follow.partial.gray",
            root / f"{identifier}.follow.partial.log",
        )
        try:
            count = math.ceil((end - start) * 2)
            command = [
                self.media.settings.ffmpeg,
                "-v",
                "error",
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
                str(start),
                "-i",
                str(source.resolve()),
                "-t",
                str(end - start),
                "-map",
                f"0:{info.video_index}",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                f"fps=2:start_time=0,scale={width}:{height},setsar=1",
                "-frames:v",
                str(count),
                "-filter_threads",
                "1",
                "-threads",
                "1",
                "-pix_fmt",
                "gray",
                "-f",
                "rawvideo",
                str(raw),
            ]
            with log.open("xb") as diagnostics:
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=diagnostics)
                try:
                    next_space_check = 0.0
                    while process.poll() is None:
                        if cancel.wait(0.05):
                            raise RenderCancelled()
                        if time.monotonic() >= next_space_check:
                            require_space(root, self.media.settings.min_free_bytes)
                            next_space_check = time.monotonic() + 1
                        if (
                            time.monotonic() > deadline
                            or diagnostics.tell() > 1024**2
                            or (raw.exists() and raw.stat().st_size > width * height * count)
                        ):
                            raise MediaError("Auto follow exceeded its processing or output limit.")
                    if process.returncode:
                        raise MediaError("Could not decode this clip for Auto follow.")
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
            frame_bytes = width * height
            size = raw.stat().st_size
            if (
                size % frame_bytes
                or not max(1, math.floor((end - start) * 2)) <= size // frame_bytes <= count
            ):
                raise MediaError("Auto follow returned incomplete source frames.")
            observations = []
            previous = None
            with raw.open("rb") as frames:
                for _ in range(size // frame_bytes):
                    if cancel.is_set():
                        raise RenderCancelled()
                    if time.monotonic() > deadline:
                        raise MediaError("Auto follow exceeded its processing limit.")
                    gray = np.frombuffer(frames.read(frame_bytes), dtype=np.uint8).reshape(
                        height, width
                    )
                    scene = cv2.resize(gray, (32, 32))
                    if previous is not None and np.mean(cv2.absdiff(scene, previous)) > 65:
                        return follow_fit(
                            start,
                            end,
                            "A large scene change was detected. The full picture is preserved.",
                        )
                    previous = scene
                    boxes = [
                        (x / width, y / height, w / width, h / height)
                        for x, y, w, h in detector.detectMultiScale(
                            gray, scaleFactor=1.1, minNeighbors=6, minSize=(24, 24)
                        )
                    ]
                    protected = [
                        (r.x, r.y, r.x + r.width, r.y + r.height)
                        for r in text_regions(cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB))
                    ]
                    observations.append((boxes, protected))
            return tracking_plan(observations, info.width, info.height, start, end)

        finally:
            remove_artifact(raw)
            remove_artifact(log)
