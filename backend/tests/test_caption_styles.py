"""Caption rendering and reusable styles; provider/inference fixtures are explicit doubles."""

import asyncio
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.caption_emphasis import EmphasisProvider, create_emphasis_router
from backend.caption_styles import CaptionStyles
from backend.captions import CaptionCustomization, build_cues, write_ass
from backend.highlight_models import HighlightError
from backend.media import Media
from backend.tests.test_animated_captions import events, text
from backend.tests.test_captions import caption_media as caption_media
from backend.tests.test_captions import word
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_projects import context as context
from backend.tests.test_projects import create, finished, payload


def test_saved_style_library_persists_limits_and_duplicate_names(context):
    client, manager, _, _, _ = context
    request = {
        "name": "My Style",
        "style": {
            "preset": "flow",
            "position": "centre",
            "customization": {
                "font_family": "Georgia",
                "font_weight": "regular",
                "text_colour": "#123456",
                "animation": "fade",
            },
        },
    }
    saved = client.post("/api/caption-styles", json=request)
    assert saved.status_code == 201, saved.text
    assert (
        client.post("/api/caption-styles", json={**request, "name": "my style"}).status_code == 409
    )
    assert client.get("/api/caption-styles").json() == [saved.json()]
    assert CaptionStyles(manager.store).list()[0].style.customization.font_family == "Georgia"
    assert client.post("/api/caption-styles", json={**request, "name": " "}).status_code == 422
    assert (
        client.post(
            "/api/caption-styles",
            json={**request, "style": {"customization": {"text_colour": "red;attack"}}},
        ).status_code
        == 422
    )
    # Fill the remaining local quota without changing any media/project settings.
    with manager.store.connection() as db:
        for index in range(49):
            db.execute(
                "INSERT INTO caption_styles VALUES (?,?,?,?)",
                (str(uuid4()), f"Style {index}", f"style {index}", "{}"),
            )
    assert client.post("/api/caption-styles", json={**request, "name": "Extra"}).status_code == 409
    assert client.delete(f"/api/caption-styles/{saved.json()['id']}").status_code == 200
    assert len(CaptionStyles(manager.store).list()) == 49
    assert client.delete(f"/api/caption-styles/{saved.json()['id']}").status_code == 404


def test_batch_style_atomic_conflicts_noop_and_preserved_exports(context):
    client, manager, recording, provider, _ = context
    project = create(client, payload(recording, emphasis_words=["Evidence"]))
    originals = [manager.store.clip(item["export_id"]) for item in project["items"]]
    counts = len(manager.media.renders), len(provider.calls)
    targets = [{"id": item["id"], "revision": item["revision"]} for item in project["items"]]
    style = {
        "preset": "glow",
        "position": "lower",
        "customization": {
            "font_family": "Courier New",
            "font_weight": "bold",
            "animation": "pop",
            "text_colour": "#33ff66",
            "background": "box",
            "background_colour": "#222222",
            "profanity": "symbols",
        },
    }
    url = f"/api/projects/{project['id']}/caption-style"
    stale = [targets[0], {**targets[1], "revision": 99}]
    assert client.post(url, json={"style": style, "targets": stale}).status_code == 409
    assert manager.records.get(project["id"]).items[0].export_id == str(originals[0].id)
    assert (
        client.post(url, json={"style": style, "targets": [targets[0], targets[0]]}).status_code
        == 422
    )
    assert client.post(url, json={"style": style, "targets": targets}).status_code == 202
    updated = finished(client, project["id"])
    assert updated["status"] == "ready", updated
    assert len(manager.media.renders) == counts[0] + 2 and len(provider.calls) == counts[1]
    for item, original in zip(updated["items"], originals):
        assert item["export_id"] != original.id
        assert item["emphasis_words"] == ["Evidence"]
        assert item["export"]["captions"]["customization"]["font_family"] == "Courier New"
        assert manager.store.clip(original.id) == original
    # An identical repeat with fresh revisions reuses media; stale retries cannot apply twice.
    fresh = [{"id": item["id"], "revision": item["revision"]} for item in updated["items"]]
    assert client.post(url, json={"style": style, "targets": fresh}).status_code == 202
    assert len(manager.media.renders) == counts[0] + 2
    assert client.post(url, json={"style": style, "targets": targets}).status_code == 409


def test_batch_overlay_collision_changes_nothing(context):
    client, manager, recording, _, _ = context
    project = create(client, payload(recording))
    current = manager.records.get(project["id"])
    from backend.overlays import TextOverlay

    current.items[1].overlays = [TextOverlay(text="CTA", start=0.0, end=1.0, position="centre")]
    manager.records.save(current)
    before = current.model_dump_json()
    response = client.post(
        f"/api/projects/{project['id']}/caption-style",
        json={
            "style": {"position": "centre"},
            "targets": [{"id": item.id, "revision": item.revision} for item in current.items],
        },
    )
    assert response.status_code == 422, response.text
    assert manager.records.get(project["id"]).model_dump_json() == before
    current.items[1].review = "rejected"
    current.items[1].overlays = []
    manager.records.save(current)
    before = current.model_dump_json()
    response = client.post(
        f"/api/projects/{project['id']}/caption-style",
        json={
            "style": {"position": "lower", "customization": {"font_family": "Georgia"}},
            "targets": [{"id": item.id, "revision": item.revision} for item in current.items],
        },
    )
    assert response.status_code == 422 and "Rejected" in response.json()["detail"]
    assert manager.records.get(project["id"]).model_dump_json() == before


@pytest.mark.parametrize(
    "invalid",
    [
        {"font_family": "Bad,Font"},
        {"font_weight": "900"},
        {"text_colour": "#123"},
        {"outline_colour": "#ff00ff\n"},
        {"animation": "bounce"},
        {"replacement": "\n"},
        {"censor_words": ["two words"]},
    ],
)
def test_reject_unsafe_or_unsupported_controls(invalid):
    with pytest.raises(ValidationError):
        CaptionCustomization(**invalid)


def test_censoring_exact_tokens_and_safe_replacement(tmp_path):
    words = [word(0, 0.5, "Shit!"), word(0.6, 1, "shitake"), word(1.1, 1.5, "Custom.")]
    options = CaptionCustomization(profanity="symbols", censor_words=["custom"])
    cues = build_cues(words, 0, 2, options)
    assert " ".join(cue.text for cue in cues) == "****! shitake ******."
    assert words[0]["text"] == "Shit!"
    path = tmp_path / "escaped.ass"
    write_ass(
        words,
        0,
        2,
        path,
        customization=CaptionCustomization(profanity="replace", replacement=r"{\pos(0,0)}"),
    )
    assert r"\pos(0,0)" not in path.read_text(encoding="utf-8-sig")
    assert "shitake" in " ".join(text(event) for event in events(path))


def test_colours_weight_and_animation_contracts(tmp_path):
    for animation in ("none", "pop", "fade"):
        path = tmp_path / f"{animation}.ass"
        customization = CaptionCustomization(
            font_family="Georgia",
            font_weight="regular",
            animation=animation,
            text_colour="#123456",
            outline_colour="#654321",
            shadow_colour="#abcdef",
            emphasis_colour="#ff00ff",
            background_colour="#112233",
            background="box",
        )
        write_ass(
            [word(0, 0.4, "Hello"), word(0.5, 0.9, "World")],
            0,
            1,
            path,
            preset="flow",
            emphasis_words=["Hello"],
            customization=customization,
        )
        ass = path.read_text(encoding="utf-8-sig")
        assert "Style: Default,Georgia,64,&H00563412" in ass
        assert "&H00214365" in ass and "&H80EFCDAB" in ass and "&H80332211" in ass
        assert r"\1c&H00FF00FF&" in ass
        assert len(events(path)) == 2  # Background and phrase, no Flow partitioning.
        if animation == "pop":
            assert r"\fscx85" in ass and r"\fscx100" in ass
        elif animation == "fade":
            assert r"\fad(120,120)" in ass
        else:
            assert r"\fad(" not in ass and r"\t(" not in ass


def provider_response(ids):
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps({"token_ids": ids})}],
            }
        ],
        "usage": {"input_tokens": 20, "output_tokens": 5},
    }


def test_emphasis_transport_schema_grounding_and_privacy(settings):
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.openai.com/v1/responses"
        data = json.loads(request.content)
        assert data["store"] is False and data["text"]["format"]["strict"] is True
        assert "untrusted DATA" in data["instructions"]
        assert "synthetic-key" not in request.content.decode()
        return httpx2.Response(200, json=provider_response([1]))

    provider = EmphasisProvider(
        replace(settings, openai_api_key="synthetic-key"), transport=httpx2.MockTransport(handler)
    )
    result = asyncio.run(provider.suggest(["the", "amazing", "result"]))
    assert result == {"words": ["amazing"], "input_tokens": 20, "output_tokens": 5}
    assert len(calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        provider_response([999]),
        provider_response([True]),
        provider_response([1, 1]),
        provider_response([-1]),
        {"status": "incomplete"},
        {"status": "completed", "output": [None]},
    ],
)
def test_invalid_emphasis_output_is_rejected_without_retry(settings, response):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(200, json=response)

    provider = EmphasisProvider(
        replace(settings, openai_api_key="synthetic-key"), transport=httpx2.MockTransport(handler)
    )
    with pytest.raises(HighlightError):
        asyncio.run(provider.suggest(["hello", "world"]))
    assert len(calls) == 1


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_emphasis_provider_errors_sanitized(settings, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(
            status, text="synthetic-secret", headers={"location": "https://example.invalid"}
        )

    provider = EmphasisProvider(
        replace(settings, openai_api_key="synthetic-key"), transport=httpx2.MockTransport(handler)
    )
    with pytest.raises(HighlightError) as caught:
        asyncio.run(provider.suggest(["hello"]))
    assert "synthetic-secret" not in str(caught.value) and len(calls) == 1


def test_emphasis_api_requires_consent_and_current_revision(context):
    _, manager, recording, _, configured = context
    job = manager.transcriptions.transcripts.latest(recording.id)

    class Provider:
        calls = 0

        async def suggest(self, tokens):
            self.calls += 1
            return {"words": [tokens[0]], "input_tokens": 10, "output_tokens": 2}

    provider = Provider()
    app = FastAPI()
    app.include_router(create_emphasis_router(manager.transcriptions, configured, provider))
    request = {"revision": 1, "start": 0.0, "end": 10.0, "remote_consent": True}
    with TestClient(app) as client:
        url = f"/api/transcriptions/{job.id}/emphasis"
        assert client.post(url, json={**request, "remote_consent": False}).status_code == 422
        assert client.post(url, json={**request, "remote_consent": 1}).status_code == 422
        assert client.post(url, json={**request, "revision": 99}).status_code == 409
        assert provider.calls == 0
        valid = client.post(url, json=request)
        assert valid.status_code == 200 and valid.json()["words"] == ["Evidence"]
        assert manager.transcriptions.transcripts.job(job.id).revision == 1
        review = client.post(
            f"/api/transcriptions/{job.id}/caption-review",
            json={
                "revision": 1,
                "start": 0.0,
                "end": 10.0,
                "customization": {"profanity": "symbols", "censor_words": ["evidence"]},
            },
        )
        assert review.status_code == 200 and len(review.json()["matches"]) == 2
        assert review.json()["matches"][0]["display"] == "******** 0."
        replacement = client.post(
            f"/api/transcriptions/{job.id}/caption-review",
            json={
                "revision": 1,
                "start": 0.0,
                "end": 10.0,
                "customization": {
                    "profanity": "replace",
                    "capitalization": "upper",
                    "replacement": "[hidden]",
                    "censor_words": ["evidence"],
                },
            },
        )
        assert replacement.json()["matches"][0]["display"] == "[HIDDEN] 0."


@pytest.mark.parametrize(
    "font,animation", [("Arial", "fade"), ("Georgia", "pop"), ("Courier New", "none")]
)
def test_real_font_colour_background_animation_export(tmp_path, caption_media, font, animation):
    settings, source = caption_media
    subtitle, output = tmp_path / "custom.ass", tmp_path / "output.mp4"
    write_ass(
        [word(0.3, 1.2, "Visible words")],
        0,
        1.5,
        subtitle,
        emphasis_words=["words"],
        customization=CaptionCustomization(
            font_family=font,
            font_weight="bold",
            text_colour="#00ff00",
            outline_colour="#111111",
            background="box",
            background_colour="#0000ff",
            emphasis_colour="#ff00ff",
            animation=animation,
        ),
    )
    info = Media(settings).render(
        source,
        output,
        0,
        1.5,
        "pad",
        threading.Event(),
        tmp_path / "render.log",
        caption_path=subtitle,
    )
    assert info.has_audio and (info.width, info.height) == (1080, 1920)
    assert info.duration == pytest.approx(1.5, abs=0.1)

    def pixels(at):
        return subprocess.run(
            [
                settings.ffmpeg,
                "-v",
                "error",
                "-nostdin",
                "-ss",
                str(at),
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout

    assert max(pixels(0.05)) < 35 and max(pixels(1.35)) < 35
    frame = pixels(0.75)
    assert len(frame) == 1080 * 1920 * 3
    green = sum(
        frame[i + 1] > 160 and frame[i] < 80 and frame[i + 2] < 80 for i in range(0, len(frame), 3)
    )
    blue = sum(
        frame[i + 2] > 70 and frame[i] < 40 and frame[i + 1] < 40 for i in range(0, len(frame), 3)
    )
    assert green > 100 and blue > 100
    magenta = sum(
        frame[i] > 160 and frame[i + 2] > 160 and frame[i + 1] < 80 for i in range(0, len(frame), 3)
    )
    assert magenta > 100


def test_emphasis_discards_concurrent_transcript_edits(context):
    _, manager, recording, _, configured = context
    job = manager.transcriptions.transcripts.latest(recording.id)

    class Provider:
        async def suggest(self, tokens):
            with manager.store.connection() as db:
                db.execute("UPDATE transcriptions SET revision=revision+1 WHERE id=?", (job.id,))
            return {"words": [tokens[0]], "input_tokens": 10, "output_tokens": 2}

    app = FastAPI()
    app.include_router(create_emphasis_router(manager.transcriptions, configured, Provider()))
    with TestClient(app) as client:
        response = client.post(
            f"/api/transcriptions/{job.id}/emphasis",
            json={"revision": 1, "start": 0.0, "end": 10.0, "remote_consent": True},
        )
        assert response.status_code == 409 and "discarded" in response.json()["detail"]


def test_emphasis_missing_key_and_oversized_responses(settings):
    with pytest.raises(HighlightError, match="backend OpenAI key"):
        asyncio.run(EmphasisProvider(replace(settings, openai_api_key=None)).suggest(["hello"]))

    def handler(request):
        return httpx2.Response(200, content=b"x" * (256 * 1024 + 1))

    provider = EmphasisProvider(
        replace(settings, openai_api_key="synthetic-key"), transport=httpx2.MockTransport(handler)
    )
    with pytest.raises(HighlightError, match="safety limit"):
        asyncio.run(provider.suggest(["hello"]))


def test_emphasis_concurrent_requests_do_not_duplicate_billing(context):
    _, manager, recording, _, configured = context
    job = manager.transcriptions.transcripts.latest(recording.id)
    entered, release = threading.Event(), threading.Event()

    class Provider:
        calls = 0

        async def suggest(self, tokens):
            self.calls += 1
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            return {"words": [], "input_tokens": 10, "output_tokens": 2}

    provider = Provider()
    app = FastAPI()
    app.include_router(create_emphasis_router(manager.transcriptions, configured, provider))
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        url = f"/api/transcriptions/{job.id}/emphasis"
        request = {"revision": 1, "start": 0.0, "end": 10.0, "remote_consent": True}
        first = pool.submit(client.post, url, json=request)
        try:
            assert entered.wait(3)
            assert client.post(url, json=request).status_code == 409
        finally:
            release.set()
        assert first.result(timeout=3).status_code == 200
        assert provider.calls == 1


def test_boxed_preset_custom_outline_and_background_are_independent(tmp_path):
    path = tmp_path / "boxed.ass"
    write_ass(
        [word(0, 0.9, "Words")],
        0,
        1,
        path,
        preset="boxed",
        customization=CaptionCustomization(outline_colour="#00ff00", background_colour="#ff0000"),
    )
    ass = path.read_text(encoding="utf-8-sig")
    assert "&H0000FF00" in ass and "&H800000FF" in ass
    assert len(events(path)) == 2
