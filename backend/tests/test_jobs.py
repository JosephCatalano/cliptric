import threading
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.jobs import ExportBusy, Jobs
from backend.media import MediaInfo
from backend.models import ExportRequest, Recording
from backend.storage import Store, now


class ControlledRenderer:
    """Unit-test renderer for deliberately exercising worker interleavings."""

    def render(self, source, output, start, end, mode, cancel, log_path):
        output.write_bytes(b"unit test artifact")
        return MediaInfo(end - start, 1080, 1920, False)


def source(store):
    identifier = str(uuid4())
    store.save_recording(
        Recording(
            id=identifier,
            name="test.mp4",
            duration=8,
            width=320,
            height=180,
            has_audio=False,
            size_bytes=1,
            created_at=now(),
        ),
        ".mp4",
    )
    return identifier


def test_completion_cannot_be_changed_to_cancelling(tmp_path):
    store = Store(tmp_path)
    jobs = Jobs(store, ControlledRenderer())
    entered_cleanup, allow_cleanup = threading.Event(), threading.Event()

    def slow_cleanup(path):
        entered_cleanup.set()
        assert allow_cleanup.wait(5)
        return True

    try:
        with patch("backend.jobs.remove_artifact", side_effect=slow_cleanup):
            clip = jobs.submit(source(store), ExportRequest(start=0, end=1))
            assert entered_cleanup.wait(5)
            with pytest.raises(ExportBusy):
                jobs.cancel(clip.id)
            assert store.clip(clip.id).status == "succeeded"
            allow_cleanup.set()
            jobs.pool.submit(lambda: None).result(timeout=5)
    finally:
        allow_cleanup.set()
        jobs.close()


def test_cleanup_failure_does_not_wedge_worker(tmp_path):
    store = Store(tmp_path)
    jobs = Jobs(store, ControlledRenderer())
    recording = source(store)
    try:
        with patch.object(Path, "unlink", side_effect=PermissionError("test file is locked")):
            jobs.submit(recording, ExportRequest(start=0, end=1))
            jobs.pool.submit(lambda: None).result(timeout=5)
            assert jobs.current() is None
        next_clip = jobs.submit(recording, ExportRequest(start=1, end=2))
        jobs.pool.submit(lambda: None).result(timeout=5)
        assert store.clip(next_clip.id).status == "succeeded"
    finally:
        jobs.close()
    with pytest.raises(ExportBusy, match="stopping"):
        jobs.submit(recording, ExportRequest(start=0, end=1))
