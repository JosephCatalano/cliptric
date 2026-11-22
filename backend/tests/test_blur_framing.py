"""Actual encoded pixel checks for generated padding and stable source letterboxing."""

import subprocess
import threading

import pytest

from backend.media import Media
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools


@pytest.mark.parametrize(
    "source_size,picture_size",
    [
        ("320x180", "320x180"),
        ("180x480", "180x480"),
        ("320x240", "320x180"),
        ("320x240", "180x240"),
    ],
)
def test_blur_fills_top_bottom_and_sides_including_embedded_bars(
    settings, tmp_path, source_size, picture_size
):
    source = tmp_path / "source.mp4"
    width, height = source_size.split("x")
    subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            f"color=red:size={picture_size}:rate=24:duration=1",
            "-vf",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    output = tmp_path / "output.mp4"
    media = Media(settings)
    result = media.render(source, output, 0, 1, "pad", threading.Event(), tmp_path / "render.log")
    assert (result.width, result.height) == (1080, 1920)
    pixels = subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-ss",
            "0.5",
            "-i",
            str(output),
            "-frames:v",
            "1",
            "-vf",
            "scale=18:32",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=20,
    ).stdout
    corners = [0, 17, 18 * 31, 18 * 32 - 1]
    for index in corners:
        red, green, blue = pixels[index * 3 : index * 3 + 3]
        assert red > 150 and green < 60 and blue < 60, (source_size, picture_size, red, green, blue)
