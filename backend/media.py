import json
import math
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path

from .config import Settings
from .editing import QualityOptions, edit_filter, retained_intervals, smart_filter
from .encoding import encoding_profile
from .files import require_space
from .framing import SourceRegion, SplitLayout, split_filter, validate_framing


class MediaError(Exception):
    pass


class RenderCancelled(Exception):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    has_audio: bool
    video_index: int = 0
    audio_index: int | None = None
    video_codec: str = ""
    audio_codec: str | None = None
    pixel_format: str = ""
    sample_aspect_ratio: str = "1:1"
    frame_rate: float = 30
    audio_start: float = 0
    audio_end: float | None = None
    render_backend: str | None = None
    render_warning: str | None = None
    render_seconds: float | None = None


class Media:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._ass_available = False

    def _require_ass(self) -> None:
        if self._ass_available:
            return
        try:
            result = subprocess.run(
                [self.settings.ffmpeg, "-hide_banner", "-filters"],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise MediaError("Could not verify FFmpeg caption support.") from exc
        if not any(
            len(fields := line.split()) >= 2 and fields[1] == "ass"
            for line in result.stdout.splitlines()
        ):
            raise MediaError("Caption exports require an FFmpeg build with the libass filter.")
        self._ass_available = True

    def probe(self, path: Path) -> MediaInfo:
        command = [
            self.settings.ffprobe,
            "-v",
            "error",
            "-max_alloc",
            "268435456",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            "mov,matroska,webm",
            "-show_entries",
            "format=duration,start_time:stream=index,codec_type,codec_name,width,height,duration,start_time,sample_aspect_ratio,avg_frame_rate,pix_fmt,color_transfer:stream_side_data=rotation:stream_disposition=attached_pic",
            "-of",
            "json",
            str(path),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
            payload = json.loads(result.stdout)
            video = next(
                stream
                for stream in payload["streams"]
                if stream.get("codec_type") == "video"
                and not stream.get("disposition", {}).get("attached_pic")
            )
            audio = next(
                (stream for stream in payload["streams"] if stream.get("codec_type") == "audio"),
                None,
            )
            duration = float(video.get("duration", payload["format"]["duration"]))
            width, height = int(video["width"]), int(video["height"])
            if not 0 < width <= 7680 or not 0 < height <= 7680 or width * height > 34_000_000:
                raise MediaError("Recording dimensions exceed the supported limit.")
            sar = video.get("sample_aspect_ratio", "1:1")
            aspect = float(Fraction(sar.replace(":", "/"))) if sar not in ("N/A", "0:1") else 1
            if not 0.1 <= aspect <= 10:
                raise MediaError("Unsupported pixel aspect ratio.")
            width = round(width * aspect)
            rate = float(Fraction(video.get("avg_frame_rate", "0/1")))
            if not 0 < rate <= 120:
                raise MediaError("Video must have a measurable frame rate no higher than 120 fps.")
            if video.get("color_transfer") in {"smpte2084", "arib-std-b67"}:
                raise MediaError(
                    "HDR video is not supported yet. Import an SDR version to preserve colours."
                )
            rotation = next(
                (
                    float(side["rotation"])
                    for side in video.get("side_data_list", [])
                    if "rotation" in side
                ),
                0,
            )
            if abs(rotation) % 180 == 90:
                width, height = height, width
            if not math.isfinite(duration) or not 0 < duration <= self.settings.max_source_seconds:
                raise MediaError(
                    f"Recording duration must be positive and no longer than {self.settings.max_source_seconds:g} seconds."
                )
            if not 0 < width <= 7680 or not 0 < height <= 7680 or width * height > 34_000_000:
                raise MediaError("Recording dimensions exceed the supported limit.")
            origin = float(payload["format"].get("start_time", 0))
            audio_start = float(audio.get("start_time", origin)) - origin if audio else 0
            audio_end = (
                audio_start + float(audio["duration"]) if audio and "duration" in audio else None
            )
            return MediaInfo(
                duration,
                width,
                height,
                audio is not None,
                video_index=int(video["index"]),
                audio_index=int(audio["index"]) if audio else None,
                video_codec=video.get("codec_name", ""),
                audio_codec=audio.get("codec_name") if audio else None,
                pixel_format=video.get("pix_fmt", ""),
                sample_aspect_ratio=sar,
                frame_rate=rate,
                audio_start=audio_start,
                audio_end=audio_end,
            )
        except MediaError:
            raise
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            StopIteration,
            ZeroDivisionError,
            OverflowError,
        ) as exc:
            raise MediaError(
                "Cannot read this video. Use a valid MP4, MOV, MKV, or WebM recording."
            ) from exc

    def render(
        self,
        source: Path,
        output: Path,
        start: float,
        end: float,
        mode: str,
        cancel: threading.Event,
        log_path: Path,
        *,
        caption_path: Path | None = None,
        headline_path: Path | None = None,
        split_layout: SplitLayout | None = None,
        crop_region: SourceRegion | None = None,
        quality: QualityOptions | None = None,
        cover_path: Path | None = None,
    ) -> MediaInfo:
        if (
            mode not in {"pad", "crop", "split"}
            or not all(math.isfinite(t) for t in (start, end))
            or start < 0
            or not 0.1 <= end - start <= self.settings.max_clip_seconds
        ):
            raise MediaError("Invalid export interval or framing mode.")
        render_started = time.monotonic()
        try:
            profile = encoding_profile(self.settings)
        except ValueError as exc:
            raise MediaError(str(exc)) from exc
        original = self.probe(source)
        quality = quality or QualityOptions()
        try:
            kept = retained_intervals(start, end, quality)
        except ValueError as exc:
            raise MediaError(str(exc)) from exc
        expected_duration = sum(last - first for first, last in kept)
        editing = (
            quality.cleanup != "off"
            or quality.normalize
            or bool([cut for cut in quality.removals if cut.enabled])
        )
        if (
            editing
            and original.has_audio
            and (
                original.audio_start > start + 0.05
                or (original.audio_end is not None and original.audio_end < end - 0.05)
            )
        ):
            raise MediaError(
                "Audio edits require an audio track covering the full selection. Shorten the selection before applying edits."
            )
        if not original.has_audio and (quality.cleanup != "off" or quality.normalize):
            raise MediaError("This source has no audio to clean or normalize.")
        if end > original.duration + 0.05:
            raise MediaError("Export exceeds the current source duration.")
        require_space(output.parent, self.settings.min_free_bytes)
        for overlay_path in (caption_path, headline_path):
            if overlay_path is None:
                continue
            self._require_ass()
            if not overlay_path.is_file() or not 0 < overlay_path.stat().st_size <= 4 * 1024**2:
                raise MediaError("The caption snapshot is missing, empty, or too large.")
        try:
            validate_framing(mode, split_layout, crop_region)
            if crop_region and quality.smart_framing:
                raise ValueError("Choose manual crop regions or smart framing, not both.")
        except ValueError as exc:
            raise MediaError(str(exc)) from exc
        active = (
            self.active_picture(source, original, start, end, cancel)
            if mode != "split" and quality.smart_framing is None and crop_region is None
            else None
        )
        prefix = f"[0:{original.video_index}]" + (f"crop={active}," if active else "")
        if crop_region is not None:
            try:
                region = crop_region.pixels(
                    original.width // 2 * 2, original.height // 2 * 2, (1080, 1920)
                )
            except ValueError as exc:
                raise MediaError(str(exc)) from exc
            framing = (
                prefix
                + f"scale={original.width // 2 * 2}:{original.height // 2 * 2},setsar=1,crop={region},scale=1080:1920"
            )
        elif quality.smart_framing and quality.smart_framing.strategy == "crop":
            if mode == "split":
                raise MediaError("Smart framing cannot be combined with a manual split layout.")
            try:
                framing = (
                    prefix
                    + f"scale={original.width // 2 * 2}:{original.height // 2 * 2},setsar=1,"
                    + smart_filter(
                        quality.smart_framing,
                        kept,
                        original.width // 2 * 2,
                        original.height // 2 * 2,
                    )
                )
            except ValueError as exc:
                raise MediaError(str(exc)) from exc
        elif mode == "split":
            try:
                framing = split_filter(
                    original.video_index, original.width, original.height, split_layout
                )
            except ValueError as exc:
                raise MediaError(str(exc)) from exc
        elif mode == "pad" or (quality.smart_framing and quality.smart_framing.strategy == "fit"):
            # Blur a small background before upscaling; keep the foreground sharp.
            framing = (
                prefix + "split=2[back][front];"
                "[back]scale=270:480:force_original_aspect_ratio=increase:force_divisible_by=2:reset_sar=1,"
                "crop=270:480,boxblur=12:2,scale=1080:1920[blur];"
                "[front]scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1[fit];"
                "[blur][fit]overlay=(W-w)/2:(H-h)/2:shortest=1"
            )
        else:
            framing = prefix + (
                "scale=1080:1920:force_original_aspect_ratio=increase:force_divisible_by=2:reset_sar=1,"
                "crop=1080:1920"
            )
        if cover_path is not None:
            if not cover_path.is_file() or not 0 < cover_path.stat().st_size <= 4 * 1024**2:
                raise MediaError("The opening cover frame is missing or too large.")
            cover_crop = ""
            path = quality.smart_framing
            if path and path.strategy == "crop":
                at = quality.cover_at if quality.cover_at is not None else start
                first, last = next(
                    ((a, b) for a, b in zip(path.points, path.points[1:]) if a.at <= at <= b.at),
                    (path.points[-1], path.points[-1]),
                )
                fraction = (at - first.at) / (last.at - first.at) if last.at > first.at else 0
                x, y = (
                    first.x + (last.x - first.x) * fraction,
                    first.y + (last.y - first.y) * fraction,
                )
                cover_crop = f"crop=iw*{path.width:.8f}:ih*{path.height:.8f}:iw*{x:.8f}:ih*{y:.8f},"
            if crop_region is not None:
                cover_crop = f"scale={original.width // 2 * 2}:{original.height // 2 * 2},setsar=1,crop={region},"
            framing += f"[main_picture];[1:v]{cover_crop}scale=iw:ih:in_range=pc:out_range=tv,format=yuv420p,setparams=range=limited,split=2[cover_back][cover_front];"
            framing += "[cover_back]scale=270:480:force_original_aspect_ratio=increase:force_divisible_by=2,crop=270:480,boxblur=12:2,scale=1080:1920[cover_blur];"
            framing += "[cover_front]scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2[cover_fit];"
            framing += "[cover_blur][cover_fit]overlay=(W-w)/2:(H-h)/2[cover_picture];"
            framing += "[main_picture][cover_picture]overlay=eof_action=repeat:enable='lt(t,0.25)'"
        framing += (
            ",setsar=1"
            + (",ass=captions.ass" if caption_path is not None else "")
            + (",ass=headline.ass" if headline_path is not None else "")
            + "[out]"
        )
        if editing:
            framing = edit_filter(
                kept, start, original.video_index, original.audio_index, quality
            ) + framing.replace(f"[0:{original.video_index}]", "[edited_video]")
        command = [
            profile.executable,
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
            "-ss",
            str(start),
            "-i",
            str(source.resolve()),
            *(
                [
                    "-protocol_whitelist",
                    "file,pipe",
                    "-format_whitelist",
                    "image2,jpeg_pipe",
                    "-i",
                    str(cover_path.resolve()),
                ]
                if cover_path is not None
                else []
            ),
            "-t",
            str(end - start),
            "-map",
            "[out]",
            *(
                ["-map", "[edited_audio]" if editing else f"0:{original.audio_index}"]
                if original.audio_index is not None
                else []
            ),
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-filter_complex",
            framing,
            "-filter_complex_threads",
            str(self.settings.render_threads),
            "-c:v",
            *(
                ["h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0"]
                if profile.backend == "nvenc"
                else ["libx264", "-preset", "veryfast", "-crf", "20"]
            ),
            "-threads",
            str(self.settings.render_threads),
            "-pix_fmt",
            "yuv420p",
            "-fps_mode",
            "vfr",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(output.resolve()),
        ]
        if cancel.is_set():
            raise RenderCancelled()
        caption_workdir = (
            tempfile.TemporaryDirectory(prefix="clipping-captions-", dir=output.parent)
            if caption_path is not None or headline_path is not None
            else nullcontext(None)
        )
        with caption_workdir as workdir, log_path.open("wb") as log:
            if caption_path is not None:
                # FFmpeg filter syntax has independent escaping rules on Windows.
                # Only this controlled filename enters the filter; arbitrary paths use cwd.
                shutil.copyfile(caption_path, Path(workdir) / "captions.ass")
            if headline_path is not None:
                shutil.copyfile(headline_path, Path(workdir) / "headline.ass")
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=log, cwd=workdir)
            timeout_seconds = self.settings.render_timeout_seconds * max(
                1, min(12, (end - start) / 120)
            )
            deadline = time.monotonic() + timeout_seconds
            next_resource_check = 0.0
            try:
                while process.poll() is None:
                    if cancel.wait(0.15):
                        raise RenderCancelled()
                    if time.monotonic() > deadline:
                        raise MediaError(
                            f"Export exceeded the {timeout_seconds:g}-second processing limit."
                        )
                    if time.monotonic() >= next_resource_check:
                        require_space(output.parent, self.settings.min_free_bytes)
                        if (
                            output.exists()
                            and output.stat().st_size > self.settings.max_export_bytes
                        ) or log.tell() > 1024**2:
                            raise MediaError("Export exceeded its output or diagnostic size limit.")
                        next_resource_check = time.monotonic() + 1
                if cancel.is_set():
                    raise RenderCancelled()
                if process.returncode != 0:
                    raise MediaError(
                        "Export failed. Details are saved in the local processing log."
                    )
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
        exported = self.probe(output)
        if (exported.width, exported.height) != (1080, 1920):
            raise MediaError("Export dimensions did not pass verification.")
        if abs(exported.duration - expected_duration) > max(0.1, 2 / original.frame_rate):
            raise MediaError("Export duration did not pass verification.")
        expects_audio = (
            original.has_audio
            and original.audio_start < end
            and (original.audio_end is None or original.audio_end > start)
        )
        if expects_audio and not exported.has_audio:
            raise MediaError("Export audio did not pass verification.")
        if (
            exported.video_codec != "h264"
            or exported.pixel_format != "yuv420p"
            or exported.sample_aspect_ratio != "1:1"
            or (exported.has_audio and exported.audio_codec != "aac")
        ):
            raise MediaError("Export codec or pixel format did not pass verification.")
        if output.stat().st_size > self.settings.max_export_bytes:
            raise MediaError("Export exceeded its output size limit.")
        return replace(
            exported,
            render_backend=profile.backend,
            render_warning=profile.warning,
            render_seconds=time.monotonic() - render_started,
        )

    def active_picture(
        self, source: Path, info: MediaInfo, start: float, end: float, cancel: threading.Event
    ) -> str | None:
        """Remove only stable black borders found at three points in this clip.

        Dark/moving scenes and non-square pixels retain their original geometry.
        This is conservative border detection, not a guarantee about picture content.
        """
        if info.sample_aspect_ratio != "1:1" or end - start < 0.3:
            return None
        rectangles = []
        for fraction in (0.1, 0.5, 0.85):
            timestamp = start + (end - start) * fraction
            command = [
                self.settings.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "info",
                "-nostdin",
                "-max_alloc",
                "268435456",
                "-threads",
                "2",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                "mov,matroska,webm",
                "-ss",
                str(timestamp),
                "-i",
                str(source.resolve()),
                "-t",
                str(min(0.4, end - timestamp)),
                "-map",
                f"0:{info.video_index}",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                "cropdetect=limit=0.0941176:round=2:reset=0:skip=0",
                "-filter_threads",
                "1",
                "-frames:v",
                "12",
                "-f",
                "null",
                "-",
            ]
            with tempfile.TemporaryFile() as log:
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=log)
                deadline = time.monotonic() + 15
                try:
                    while process.poll() is None:
                        if cancel.wait(0.1):
                            raise RenderCancelled()
                        if time.monotonic() > deadline or log.tell() > 1024**2:
                            raise MediaError("Border detection exceeded its resource limit.")
                    if process.returncode:
                        raise MediaError("Cannot inspect source borders before rendering.")
                    log.seek(0)
                    sample = [
                        tuple(map(int, match))
                        for match in re.findall(rb"crop=(\d+):(\d+):(\d+):(\d+)", log.read(1024**2))
                    ]
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
            if len(sample) < 2:
                return None
            rectangles.extend(sample)
        # Union preserves every detected picture area, rather than cropping to a dark frame.
        left = min(x for w, h, x, y in rectangles)
        top = min(y for w, h, x, y in rectangles)
        right = max(x + w for w, h, x, y in rectangles)
        bottom = max(y + h for w, h, x, y in rectangles)
        width, height = right - left, bottom - top
        if (
            not 0 <= left < right <= info.width
            or not 0 <= top < bottom <= info.height
            or width < info.width / 2
            or height < info.height / 2
            or max(left, top, info.width - right, info.height - bottom) < 2
        ):
            return None
        if any(
            abs(value - reference) > 4
            for rectangle in rectangles
            for value, reference in zip(rectangle, rectangles[0], strict=True)
        ):
            return None
        return f"{width}:{height}:{left}:{top}"

    def thumbnail(
        self,
        source: Path,
        output: Path,
        timestamp: float,
        cancel: threading.Event,
        *,
        max_dimension: int = 640,
    ):
        """Bounded local frame extraction; paths never enter a filter or shell string."""
        if (
            not math.isfinite(timestamp)
            or timestamp < 0
            or type(max_dimension) is not int
            or not 64 <= max_dimension <= 1920
        ):
            raise MediaError("Invalid thumbnail timestamp.")
        command = [
            self.settings.ffmpeg,
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
            str(timestamp),
            "-i",
            str(source.resolve()),
            "-frames:v",
            "1",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            f"scale={max_dimension}:{max_dimension}:force_original_aspect_ratio=decrease",
            "-filter_threads",
            "1",
            "-threads",
            "1",
            "-q:v",
            "5",
            str(output.resolve()),
        ]
        require_space(output.parent, self.settings.min_free_bytes)
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=log)
            deadline = time.monotonic() + 20
            try:
                while process.poll() is None:
                    if cancel.wait(0.1):
                        raise RenderCancelled()
                    if time.monotonic() > deadline or log.tell() > 1024**2:
                        raise MediaError("Thumbnail extraction exceeded its resource limit.")
                if (
                    process.returncode
                    or not output.is_file()
                    or not 0 < output.stat().st_size <= (4 if max_dimension > 640 else 1) * 1024**2
                ):
                    raise MediaError("Could not extract a preview thumbnail.")
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
