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
    max_upload_bytes: int = 20 * 1024**3
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
    transcription_device: str = "auto"
    transcription_worker_idle_seconds: int = 120
    render_encoder: str = "auto"
    render_threads: int = 4
    nvenc_ffmpeg: str = ""
    openai_api_key: str = field(default_factory=read_openai_key, repr=False)
    highlight_model: str = "gpt-6-luna"
    highlight_max_windows: int = 64
    highlight_max_input_bytes: int = 2 * 1024**2
    highlight_output_tokens: int = 2048
    highlight_request_seconds: float = 60
    highlight_timeout_seconds: float = 3600
    highlight_concurrency: int = 3
    highlight_pool_size: int = 20
    highlight_duplicate_iou: float = 0.65
    highlight_duplicate_evidence: float = 0.5

    def __post_init__(self) -> None:
        if self.transcription_device not in {"auto", "cpu", "cuda"}:
            raise ValueError("Transcription device must be auto, cpu or cuda.")
        if self.render_encoder not in {"auto", "cpu", "nvenc"}:
            raise ValueError("Render encoder must be auto, cpu or nvenc.")
        if not 1 <= self.render_threads <= 16 or not 1 <= self.transcription_threads <= 16:
            raise ValueError("Processing threads must be between 1 and 16.")
        if not 1 <= self.transcription_worker_idle_seconds <= 600:
            raise ValueError("Transcription worker idle timeout must be between 1 and 600 seconds.")
        if not 1 <= self.highlight_concurrency <= 4 or not 1 <= self.highlight_pool_size <= 100:
            raise ValueError("Highlight concurrency must be 1-4 and candidate pool size 1-100.")
        if not all(
            0 < threshold <= 1
            for threshold in (self.highlight_duplicate_iou, self.highlight_duplicate_evidence)
        ):
            raise ValueError("Highlight duplicate thresholds must be in (0, 1].")
        if type(self.max_upload_bytes) is not int or not 0 < self.max_upload_bytes <= 100 * 1024**3:
            raise ValueError("Upload limit must be positive and at most 100 GiB.")
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
        values = dotenv_values(ROOT / "backend" / ".env", interpolate=False)
        raw_limit = os.environ.get(
            "CLIPPING_MAX_UPLOAD_GIB", values.get("CLIPPING_MAX_UPLOAD_GIB", "20")
        )
        try:
            gib = int(raw_limit)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "CLIPPING_MAX_UPLOAD_GIB must be an integer between 1 and 100."
            ) from exc
        if not 1 <= gib <= 100:
            raise ValueError("CLIPPING_MAX_UPLOAD_GIB must be between 1 and 100.")
        return cls(
            highlight_duplicate_iou=float(
                os.environ.get(
                    "CLIPPING_HIGHLIGHT_DUPLICATE_IOU",
                    values.get("CLIPPING_HIGHLIGHT_DUPLICATE_IOU", "0.65"),
                )
            ),
            highlight_duplicate_evidence=float(
                os.environ.get(
                    "CLIPPING_HIGHLIGHT_DUPLICATE_EVIDENCE",
                    values.get("CLIPPING_HIGHLIGHT_DUPLICATE_EVIDENCE", "0.5"),
                )
            ),
            highlight_pool_size=int(
                os.environ.get(
                    "CLIPPING_HIGHLIGHT_POOL_SIZE", values.get("CLIPPING_HIGHLIGHT_POOL_SIZE", "20")
                )
            ),
            transcription_threads=int(
                os.environ.get(
                    "CLIPPING_TRANSCRIPTION_THREADS",
                    values.get("CLIPPING_TRANSCRIPTION_THREADS", "6"),
                )
            ),
            transcription_device=os.environ.get(
                "CLIPPING_TRANSCRIPTION_DEVICE", values.get("CLIPPING_TRANSCRIPTION_DEVICE", "auto")
            ),
            transcription_worker_idle_seconds=int(
                os.environ.get(
                    "CLIPPING_TRANSCRIPTION_IDLE_SECONDS",
                    values.get("CLIPPING_TRANSCRIPTION_IDLE_SECONDS", "120"),
                )
            ),
            render_encoder=os.environ.get(
                "CLIPPING_RENDER_ENCODER", values.get("CLIPPING_RENDER_ENCODER", "auto")
            ),
            render_threads=int(
                os.environ.get(
                    "CLIPPING_RENDER_THREADS", values.get("CLIPPING_RENDER_THREADS", "4")
                )
            ),
            nvenc_ffmpeg=os.environ.get(
                "CLIPPING_NVENC_FFMPEG", values.get("CLIPPING_NVENC_FFMPEG", "")
            )
            or "",
            highlight_concurrency=int(
                os.environ.get(
                    "CLIPPING_HIGHLIGHT_CONCURRENCY",
                    values.get("CLIPPING_HIGHLIGHT_CONCURRENCY", "3"),
                )
            ),
            max_upload_bytes=gib * 1024**3,
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
