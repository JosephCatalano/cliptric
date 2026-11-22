from backend.render_progress import render_progress
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_split_framing import stream_source as stream_source


def test_progress_reads_latest_complete_numeric_time_and_bounds_tail(tmp_path):
    root = tmp_path
    (root / "logs").mkdir()
    path = root / "logs" / "clip.progress"
    assert render_progress(root, "clip", 10) == {}
    path.write_text(
        "x" * 20000 + "\nout_time_us=N/A\nout_time_us=5000000\nprogress=continue\n",
        encoding="ascii",
    )
    value = render_progress(root, "clip", 10)
    assert value["render_progress"] == 0.5 and value["progress_updated_at"]
    path.write_text("out_time_us=20000000\n", encoding="ascii")
    assert render_progress(root, "clip", 10)["render_progress"] == 0.99


def test_render_input_has_duration_bound_before_source(
    settings, stream_source, tmp_path, monkeypatch
):
    import subprocess
    import threading

    from backend.media import Media

    seen = []
    original = subprocess.Popen

    def capture(command, *args, **kwargs):
        if "-filter_complex" in command:
            seen.append(command)
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", capture)
    Media(settings).render(
        stream_source,
        tmp_path / "clip.mp4",
        0.25,
        1.25,
        "pad",
        threading.Event(),
        tmp_path / "clip.log",
    )
    command = seen[0]
    assert command.index("-t") < command.index("-i")
    assert command[command.index("-t") + 1] == "1.0"

