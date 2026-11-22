"""Read a bounded tail of FFmpeg's machine-readable progress output."""

from datetime import datetime, timezone
from pathlib import Path


def render_progress(root: Path, identifier: str, duration: float) -> dict:
    path = root / "logs" / f"{identifier}.progress"
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 8192))
            lines = stream.read(8192).decode("ascii", errors="replace").splitlines()
        updated = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except FileNotFoundError:
        return {}
    values = [line.partition("=")[2] for line in lines if line.startswith("out_time_us=")]
    if not values or not values[-1].lstrip("-").isdigit():
        return {}
    return {
        "render_progress": min(0.99, max(0, int(values[-1]) / 1_000_000 / max(0.1, duration))),
        "progress_updated_at": updated,
    }
