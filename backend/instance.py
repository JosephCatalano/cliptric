from pathlib import Path


class InstanceLock:
    """Keep restart recovery from touching another server's active files."""

    def __init__(self, root: Path):
        self.path = root / "server.lock"
        self.file = None

    def acquire(self) -> None:
        import os

        self.file = self.path.open("a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError("Another Cliptric API is using this data directory.") from exc

    def release(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None
