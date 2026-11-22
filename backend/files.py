import errno
import json
import logging
import os
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class StorageFull(OSError):
    pass


def read_json_snapshot(path: Path, limit: int = 4096) -> dict | None:
    """A not-yet-published progress file is absent; corrupt/oversized state fails."""
    for attempt, delay in enumerate((0, 0.01, 0.02, 0.04, 0.08, 0.16, 0.32)):
        if delay:
            time.sleep(delay)
        try:
            with path.open("rb") as stream:
                contents = stream.read(limit + 1)
            break
        except FileNotFoundError:
            return None
        except PermissionError as exc:
            # Atomic replacement can briefly deny a concurrent Windows open.
            # Retry the snapshot read, not the completed inference or download.
            # CPython's CRT open may expose a Windows sharing conflict only as
            # EACCES, without winerror. Persistent access denials still fail.
            sharing = getattr(exc, "winerror", None) in {5, 32, 33} or (
                getattr(exc, "winerror", None) is None and exc.errno == errno.EACCES
            )
            if os.name != "nt" or not sharing or attempt == 6:
                raise
    if len(contents) > limit:
        raise ValueError("Worker snapshot exceeds its size limit.")
    value = json.loads(contents)
    if not isinstance(value, dict):
        raise ValueError("Worker snapshot must be an object.")
    return value


def write_json_atomic(path: Path, value: dict) -> None:
    """Publish worker state, allowing brief Windows reader/scanner locks."""
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
    delays = (0, 0.01, 0.02, 0.04, 0.08, 0.16, 0.32)
    for attempt, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        try:
            temporary.replace(path)
            return
        except PermissionError as exc:
            # Retry publication only, never inference/download work or unrelated failures.
            if (
                os.name != "nt"
                or getattr(exc, "winerror", None) not in {5, 32, 33}
                or attempt == len(delays) - 1
            ):
                raise


def require_space(root: Path, reserve: int, additional: int = 0) -> None:
    if shutil.disk_usage(root).free < reserve + additional:
        raise StorageFull(errno.ENOSPC, "Insufficient free space in local storage.")


def remove_artifact(path: Path) -> bool:
    """Best-effort cleanup with visible diagnostics; never hide the original failure."""
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        logger.exception("Could not remove temporary artifact %s", path)
        return False
