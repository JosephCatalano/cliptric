"""Validated hardware encoding selection, without changing system drivers."""

import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import ROOT


@dataclass(frozen=True)
class EncodingProfile:
    executable: str
    backend: str
    warning: str | None = None


@lru_cache(maxsize=8)
def probe_nvenc(executable: str, modified: int):
    try:
        result = subprocess.run(
            [
                executable,
                "-v",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "color=s=256x256:r=1:d=1",
                "-frames:v",
                "1",
                "-an",
                "-c:v",
                "h264_nvenc",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def encoding_profile(settings):
    if settings.render_encoder == "cpu":
        return EncodingProfile(settings.ffmpeg, "cpu")
    local = ROOT / ".tools" / "nvenc" / "ffmpeg" / "bin" / "ffmpeg.exe"
    candidates = [settings.nvenc_ffmpeg] if settings.nvenc_ffmpeg else [str(local), settings.ffmpeg]
    for candidate in dict.fromkeys(candidates):
        path = Path(candidate)
        if path.is_file() and probe_nvenc(str(path.resolve()), path.stat().st_mtime_ns):
            return EncodingProfile(str(path.resolve()), "nvenc")
    if settings.render_encoder == "nvenc":
        raise ValueError(
            "NVENC could not initialize. Configure a compatible FFmpeg/driver pair or select CPU encoding."
        )
    return EncodingProfile(
        settings.ffmpeg,
        "cpu",
        "NVENC is unavailable or incompatible with the installed driver. Used CPU encoding.",
    )
