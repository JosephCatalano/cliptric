"""One sequential inference subprocess, with race-free parent-owned idle expiry."""

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from .config import ROOT

logger = logging.getLogger(__name__)


class WarmTranscription:
    def __init__(self, root: Path, idle_seconds: int):
        self.root = root
        self.idle_seconds = idle_seconds
        self.process: subprocess.Popen | None = None
        self.lock = threading.RLock()
        self.idle_timer: threading.Timer | None = None

    def start(self, request_path: Path) -> subprocess.Popen:
        with self.lock:
            self._cancel_timer()
            if self.process is None or self.process.poll() is not None:
                self.close()
                self.process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "backend.transcription_worker",
                        "--serve",
                        str(self.root.resolve()),
                    ],
                    cwd=ROOT,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            try:
                assert self.process.stdin is not None
                self.process.stdin.write(
                    (json.dumps({"request": str(request_path.resolve())}) + "\n").encode()
                )
                self.process.stdin.flush()
            except OSError:
                self.close()
                raise
            return self.process

    def mark_idle(self) -> None:
        with self.lock:
            self._cancel_timer()

            def expire():
                with self.lock:
                    if self.idle_timer is timer:
                        self.close()

            timer = threading.Timer(self.idle_seconds, expire)
            timer.daemon = True
            self.idle_timer = timer
            timer.start()

    def _cancel_timer(self) -> None:
        if self.idle_timer is not None:
            self.idle_timer.cancel()
            self.idle_timer = None

    def close(self) -> None:
        with self.lock:
            self._cancel_timer()
            process, self.process = self.process, None
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()
                if process.stdin:
                    try:
                        process.stdin.close()
                    except BrokenPipeError:
                        # Expected when a terminated worker did not consume buffered input.
                        logger.debug("Inference worker pipe closed after termination.")
