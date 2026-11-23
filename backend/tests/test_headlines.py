import json
import threading
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.headlines import HeadlineOptions, headline_parts, write_headline
from backend.media import Media
from backend.models import ExportRequest, Recording
from backend.overlays import TextOverlay, write_presentation
from backend.storage import Store
from backend.tests.test_captions import caption_media as caption_media
from backend.tests.test_captions import frame_pixels


@pytest.mark.parametrize("text", ["", "   ", "x" * 121, "unsafe\u202etext", "null\x00text"])
def test_reject_invalid_headlines(text):
    with pytest.raises(ValidationError):
        HeadlineOptions(text=text)


@pytest.mark.parametrize("seconds", [float("nan"), float("inf"), 0, 11, True])
def test_reject_invalid_duration(seconds):
    with pytest.raises(ValidationError):
        HeadlineOptions(text="A real headline", seconds=seconds)


def test_headline_escaping_wrapping_clamping_and_caption_avoidance(tmp_path):
    path = tmp_path / "headline.ass"
    options = HeadlineOptions(text=r"{\pos(0,0)} A headline that wraps safely across several lines")
    write_headline(path, options, 2.5, "upper")
    value = path.read_text(encoding="utf-8-sig")
    assert "0:00:02.50" in value and "0:00:05.00" not in value
    assert r"{\pos(0,0)}" not in value
    assert "｛" in value and r"\N" in value
    assert r"\move(540,934,540,900,0,180)" in value
    with pytest.raises(FileExistsError):
        write_headline(path, options, 2.5)


def test_additive_migration_and_immutable_headline_snapshot(tmp_path):
    store = Store(tmp_path)
    with store.connection() as db:
        db.execute("DROP TABLE clip_headlines")
        db.execute("PRAGMA user_version=2")
    store.initialize()
    source = Recording(
        id=str(uuid4()),
        name="Source.mp4",
        duration=6,
        width=320,
        height=180,
        has_audio=True,
        size_bytes=1,
        created_at="2026-11-18T00:00:00Z",
    )
    store.save_recording(source, ".mp4")
    request = ExportRequest(start=0.0, end=3.0, headline=HeadlineOptions(text="The saved headline"))
    clip = store.create_clip(str(uuid4()), source.id, request)
    request.headline.text = "Later edits"
    reopened = Store(tmp_path)
    assert reopened.clip(clip.id).headline.text == "The saved headline"
    assert reopened.clips(source.id)[0].headline.text == "The saved headline"
    assert reopened.draft(source).headline.text == "Source"
    assert reopened.clip(clip.id).headline.fade_ms == 250
    assert reopened.clip(clip.id).headline.appearance == "pop"
    with reopened.connection() as db:
        old = json.loads(
            db.execute("SELECT options FROM clip_headlines WHERE clip_id=?", (clip.id,)).fetchone()[
                0
            ]
        )
        del old["fade_ms"]
        old.pop("appearance", None)
        db.execute(
            "UPDATE clip_headlines SET options=? WHERE clip_id=?", (json.dumps(old), clip.id)
        )
    assert reopened.clip(clip.id).headline.fade_ms == 0
    assert reopened.clips(source.id)[0].headline.fade_ms == 0
    assert reopened.clip(clip.id).headline != HeadlineOptions(text="The saved headline")
    legacy = reopened.create_clip(str(uuid4()), source.id, ExportRequest(start=0.0, end=3.0))
    assert reopened.clip(legacy.id).headline is None
    abandoned = tmp_path / "exports" / "abandoned.headline.partial.ass"
    abandoned.write_text("interrupted headline", encoding="utf-8")
    reopened.recover()
    assert not abandoned.exists()
    assert reopened.clip(clip.id).headline.text == "The saved headline"


@pytest.mark.parametrize(
    "position,caption_position,top,bottom",
    [("auto", "lower", 170, 400), ("auto", "upper", 600, 950)],
)
def test_real_render_white_card_black_text_and_opening_only(
    tmp_path, caption_media, position, caption_position, top, bottom
):
    settings, source = caption_media
    headline = tmp_path / "headline [A], friend's.ass"
    write_headline(
        headline,
        HeadlineOptions(
            text="A clear opening headline", position=position, seconds=1, appearance="classic"
        ),
        3,
        caption_position,
    )
    output = tmp_path / "headline.mp4"
    info = Media(settings).render(
        source,
        output,
        0,
        3,
        "pad",
        threading.Event(),
        tmp_path / "render.log",
        headline_path=headline,
    )
    assert (info.width, info.height) == (1080, 1920)
    assert info.has_audio and info.duration == pytest.approx(3, abs=0.1)
    pixels = frame_pixels(settings, output, 0.5)
    white = [i for i, value in enumerate(pixels) if value > 220]
    assert len(white) > 30000
    assert top < min(white) // 1080 < max(white) // 1080 < bottom
    assert min(i % 1080 for i in white) >= 95
    assert max(i % 1080 for i in white) <= 985
    centre_region = [
        pixels[y * 1080 + x]
        for y in range(min(white) // 1080 + 12, max(white) // 1080 - 12)
        for x in range(200, 880)
    ]
    assert sum(value < 40 for value in centre_region) > 1000
    assert not any(value > 100 for value in frame_pixels(settings, output, 1.5))
    # The same fade applies to the white card and its text, not the underlying video.
    full_brightness = sum(pixels)
    initial = sum(frame_pixels(settings, output, 0))
    entering = sum(frame_pixels(settings, output, 0.1))
    leaving = sum(frame_pixels(settings, output, 0.9))
    assert initial < full_brightness * 0.05
    assert full_brightness * 0.1 < entering < full_brightness * 0.8
    assert full_brightness * 0.1 < leaving < full_brightness * 0.8


@pytest.mark.parametrize("duration,fade", [(0.1, 50), (0.5, 250), (1, 250), (5, 250)])
@pytest.mark.parametrize("appearance", ["classic", "cover"])
def test_fades_fit_short_clips_and_share_timing(tmp_path, duration, fade, appearance):
    path = tmp_path / "short.ass"
    write_headline(path, HeadlineOptions(text="A short title", appearance=appearance), duration)
    events = [
        line
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.startswith("Dialogue:")
    ]
    assert len(events) == (4 if appearance == "cover" else 2)
    assert all(f"\\fad({0 if appearance == 'cover' else fade},{fade})" in event for event in events)


@pytest.mark.parametrize("fade", [-1, 1001, True, 1.5])
def test_invalid_fades_are_rejected(fade):
    with pytest.raises(ValidationError):
        HeadlineOptions(text="A title", fade_ms=fade)


@pytest.mark.parametrize(
    "text", ["YOU NEED TO SEE THIS", "W" * 120, "A longer title with useful context " * 3]
)
def test_profile_title_is_centered_and_fits_a_square_preview(text):
    styles, events = headline_parts(HeadlineOptions(text=text), 10, "lower")
    profile = next(line for line in styles.splitlines() if line.startswith("Style: ProfileCover"))
    size = int(profile.split(",")[2])
    assert size > 0
    if text == "YOU NEED TO SEE THIS":
        assert size >= 100
    first = [line for line in events.splitlines() if ",ProfileCover," in line]
    assert len(first) == 4 and all(",0:00:00.00,0:00:00.25," in line for line in first)
    assert r"\pos(540,960)" in first[-1]
    assert all(r"\move(" not in line for line in first)
    assert all(
        ",0:00:00.25,0:00:04.00," in line for line in events.splitlines() if ",Headline," in line
    )
    assert 0 < len(first[-1].split(r"\N")) * (size + 10) + 64 <= 760


def test_profile_respects_centre_captions_and_timed_overlays(tmp_path):
    options = HeadlineOptions(text="An eye catching headline")
    styles, _ = headline_parts(options, 3, "centre")
    assert "ProfileCover" not in styles
    path = tmp_path / "overlays.ass"
    write_presentation(
        path,
        [TextOverlay(text="Important CTA", start=0.0, end=1.0, position="centre")],
        3,
        options,
        "lower",
    )
    assert "ProfileCover" not in path.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("position", ["upper", "centre"])
def test_real_profile_first_frame_is_large_and_centered(caption_media, tmp_path, position):
    settings, source = caption_media
    path = tmp_path / "profile.ass"
    write_headline(
        path, HeadlineOptions(text="YOU NEED TO SEE THIS", position=position), 3, "lower"
    )
    output = tmp_path / "profile.mp4"
    info = Media(settings).render(
        source, output, 0, 3, "pad", threading.Event(), tmp_path / "profile.log", headline_path=path
    )
    assert (info.width, info.height, info.has_audio) == (1080, 1920, True)
    pixels = frame_pixels(settings, output, 0)
    white = [index for index, value in enumerate(pixels) if value > 220]
    top, bottom = min(white) // 1080, max(white) // 1080
    assert 420 < top < bottom < 1500
    assert abs((top + bottom) / 2 - 960) < 3
    assert bottom - top > 250
    assert pixels[(top + 5) * 1080 + 110] < 100
    assert pixels[(top + 5) * 1080 + 300] > 220
    entering = [
        index for index, value in enumerate(frame_pixels(settings, output, 0.30)) if value > 220
    ]
    later = frame_pixels(settings, output, 0.5)
    normal = [index for index, value in enumerate(later) if value > 220]
    assert 10 < min(entering) // 1080 - min(normal) // 1080 <= 34
    assert max(normal) // 1080 - min(normal) // 1080 < bottom - top
    if position == "upper":
        assert max(normal) // 1080 < 650
