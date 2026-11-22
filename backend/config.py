import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from .limits import MAX_MEDIA_SECONDS

ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPTION_REPO = "Systran/faster-whisper-small.en"
TRANSCRIPTION_REVISION = "d1d751a5f8271d482d14ca55d9e2deeebbae577f"


def read_openai_key() -> str:
    configured = os.environ.get("OPENAI_API_KEY")
    if configured is not None:
        return configured.strip()
    values = dotenv_values(ROOT / "backend" / ".env", interpolate=False)
    return (values.get("OPENAI_API_KEY") or "").strip()


def find_tool(name: str) -> str:
    configured = os.environ.get(f"CLIPPING_{name.upper()}")
    if configured:
        path = Path(configured).resolve()
        if not path.is_file():
            raise RuntimeError(f"CLIPPING_{name.upper()} must point to an existing executable.")
        return str(path)
    installed = shutil.which(name)
    if installed:
        return installed
    bundled = sorted((ROOT / ".tools").glob(f"ffmpeg-*/bin/{name}.exe"))
    if len(bundled) == 1:
        return str(bundled[0])
    if len(bundled) > 1:
        raise RuntimeError(f"Multiple {name} builds found. Set CLIPPING_{name.upper()} explicitly.")
    raise RuntimeError(f"{name} is required. Install FFmpeg or set CLIPPING_{name.upper()}.")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ffmpeg: str
    ffprobe: str
    max_upload_bytes: int = 2 * 1024**3
    max_source_seconds: float = MAX_MEDIA_SECONDS
    max_clip_seconds: float = MAX_MEDIA_SECONDS
    render_timeout_seconds: float = 600
    upload_idle_seconds: float = 30
    upload_timeout_seconds: float = 1800
    min_free_bytes: int = 256 * 1024**2
    max_export_bytes: int = 512 * 1024**2
    web_origin: str = "http://127.0.0.1:3000"
    transcription_model_dir: Path = ROOT / ".tools" / "models" / "small.en"
    transcription_model_revision: str = TRANSCRIPTION_REVISION
    transcription_timeout_seconds: float = 7200
    transcription_threads: int = 6
    openai_api_key: str = field(default_factory=read_openai_key, repr=False)
    highlight_model: str = "gpt-6-luna"
    highlight_max_windows: int = 64
    highlight_max_input_bytes: int = 2 * 1024**2
    highlight_output_tokens: int = 2048
    highlight_request_seconds: float = 60
    highlight_timeout_seconds: float = 3600

    def __post_init__(self) -> None:
        origin = urlsplit(self.web_origin)
        if (
            origin.scheme != "http"
            or origin.hostname not in {"localhost", "127.0.0.1"}
            or origin.path
            or origin.query
            or origin.fragment
            or origin.username
        ):
            raise ValueError("CLIPPING_WEB_ORIGIN must be a local HTTP origin without a path.")
        if not origin.port:
            raise ValueError("CLIPPING_WEB_ORIGIN must specify a port.")
        if not self.highlight_model or len(self.highlight_model) > 120:
            raise ValueError("CLIPPING_HIGHLIGHT_MODEL must be a model ID.")
        if not 1 <= self.highlight_max_windows <= 192:
            raise ValueError("CLIPPING_HIGHLIGHT_MAX_WINDOWS must be between 1 and 192.")

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            data_dir=Path(os.environ.get("CLIPPING_DATA_DIR", str(ROOT / "data"))).resolve(),
            ffmpeg=find_tool("ffmpeg"),
            ffprobe=find_tool("ffprobe"),
            web_origin=os.environ.get("CLIPPING_WEB_ORIGIN", "http://127.0.0.1:3000"),
            transcription_model_dir=Path(
                os.environ.get("CLIPPING_MODEL_DIR", str(ROOT / ".tools" / "models" / "small.en"))
            ).resolve(),
            highlight_model=os.environ.get("CLIPPING_HIGHLIGHT_MODEL", "gpt-6-luna"),
            highlight_max_windows=int(os.environ.get("CLIPPING_HIGHLIGHT_MAX_WINDOWS", "64")),
        )
