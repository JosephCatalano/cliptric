"""Project-specific instructions stay isolated, bounded and separate from transcript data."""

import asyncio
import hashlib
import json
from dataclasses import replace
from uuid import uuid4

import httpx2
import pytest
from pydantic import ValidationError

from backend.batch_titles import BatchTitles
from backend.highlight_models import HighlightError, HighlightRequest
from backend.highlight_provider import DISCOVERY_INSTRUCTIONS, OpenAIHighlights
from backend.highlight_selection import Segment, build_segments, build_windows, window_text
from backend.project_models import ProjectSettings
from backend.project_store import ProjectStore
from backend.tests.test_batches import batch, create
from backend.tests.test_batches import finished as finished_project
from backend.tests.test_highlight_provider import response
from backend.tests.test_highlights import finished, request
from backend.tests.test_highlights import service as service
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_projects import context as context
from backend.tests.test_projects import payload


@pytest.mark.parametrize("model", [ProjectSettings, HighlightRequest])
@pytest.mark.parametrize("value", [None, 42, "x" * 2001, "text\x00", "text\x1b", "text\u202e"])
def test_invalid_instructions_rejected(model, value):
    values = {"analysis_end": 80.0, "ai_instructions": value}
    if model is HighlightRequest:
        values.update(transcription_id=uuid4(), revision=1, remote_consent=True)
    with pytest.raises(ValidationError):
        model(**values)


def test_instructions_preserve_paragraphs_and_legacy_defaults():
    assert ProjectSettings(analysis_end=80).ai_instructions == ""
    assert (
        ProjectSettings(analysis_end=80, ai_instructions="x" * 2000).ai_instructions == "x" * 2000
    )
    assert (
        ProjectSettings(
            analysis_end=80, ai_instructions="  Focus on advice.\r\n\r\nUse short titles.  "
        ).ai_instructions
        == "Focus on advice.\n\nUse short titles."
    )


def test_highlight_transport_keeps_preferences_separate_and_preserves_base_rules(settings):
    notes = "Focus on advice.\nUse concise titles. <script>text</script>"

    def respond(http_request):
        body = json.loads(http_request.content)
        assert body["instructions"].startswith(DISCOVERY_INSTRUCTIONS)
        assert "editorial preferences" in body["instructions"]
        assert notes not in body["instructions"]
        preferences, transcript = body["input"].split("\nTRANSCRIPT DATA:\n", 1)
        assert json.loads(preferences)["project_instructions"] == notes
        assert "Spoken source words." in transcript
        assert body["text"]["format"]["strict"] is True
        assert body["store"] is False
        return httpx2.Response(200, json=response())

    provider = OpenAIHighlights(
        replace(settings, openai_api_key="synthetic"), transport=httpx2.MockTransport(respond)
    )
    asyncio.run(
        provider.suggest(
            [Segment(0, 0, 10, "Spoken source words.")],
            HighlightRequest(
                transcription_id=uuid4(), revision=1, remote_consent=True, ai_instructions=notes
            ),
        )
    )


def test_title_transport_includes_preferences_without_changing_transcript(settings):
    notes = "Use conversational titles. Avoid clickbait."

    def respond(http_request):
        body = json.loads(http_request.content)
        assert json.loads(body["input"]) == {
            "transcript": "Actual speech",
            "project_instructions": notes,
        }
        assert notes not in body["instructions"] and "editorial preferences" in body["instructions"]
        return httpx2.Response(
            200,
            json={
                **response(),
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": '{"title":"A grounded title"}'}
                        ],
                    }
                ],
            },
        )

    provider = BatchTitles(
        replace(settings, openai_api_key="synthetic"), transport=httpx2.MockTransport(respond)
    )
    assert (
        asyncio.run(provider.suggest([{"text": "Actual speech"}], ai_instructions=notes))[0]
        == "A grounded title"
    )


def test_instruction_cache_isolation_and_legacy_key_exclusion(service):
    manager, recording, transcript, provider = service
    baseline = request(transcript)
    original = finished(manager, manager.submit(recording.id, baseline).id)
    legacy_key = hashlib.sha256(
        json.dumps(
            {
                "prompt": "transcript-highlights-v4-editorial",
                "model": manager.settings.highlight_model,
                "output_tokens": manager.settings.highlight_output_tokens,
                "request": baseline.model_dump(
                    mode="json", exclude={"regenerate", "remote_consent", "ai_instructions"}
                ),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    assert manager.results.cached(legacy_key) is None
    custom = request(transcript, ai_instructions="Focus on advice")
    customized = finished(manager, manager.submit(recording.id, custom).id)
    assert customized.id != original.id and provider.calls == 2
    assert manager.submit(recording.id, custom).id == customized.id
    assert manager.submit(recording.id, baseline).id == original.id
    assert provider.calls == 2


def test_instructions_count_toward_input_budget(service):
    manager, recording, transcript, _ = service
    words = manager.transcripts.words(transcript.id, 0, recording.duration).words
    windows = build_windows(
        build_segments(words, recording.duration), manager.settings.highlight_max_windows
    )
    transcript_bytes = sum(len(window_text(window).encode("utf-8")) for window in windows)
    manager.settings = replace(manager.settings, highlight_max_input_bytes=transcript_bytes + 1)
    with pytest.raises(HighlightError, match="input budget"):
        manager.submit(recording.id, request(transcript, ai_instructions="Focus on advice"))


def test_long_form_instructions_persist_and_reach_analysis(context):
    client, manager, recording, provider, _ = context
    notes = "Prioritize practical advice.\nUse concise titles."
    body = payload(recording, ai_instructions=notes)
    project = create(client, body)
    assert project["status"] == "ready", project
    assert project["settings"]["ai_instructions"] == notes
    assert provider.calls[0][1].ai_instructions == notes
    assert ProjectStore(manager.store).get(project["id"]).settings.ai_instructions == notes
    assert client.post("/api/projects", json=body).json()["id"] == project["id"]
    changed = {**body, "settings": {**body["settings"], "ai_instructions": "Different focus"}}
    assert client.post("/api/projects", json=changed).status_code == 409
    client.post(f"/api/projects/{project['id']}/resume", json={})
    finished_project(client, project["id"])
    assert len(provider.calls) == 1
    independent = create(client, payload(recording))
    assert independent["settings"]["ai_instructions"] == ""


def test_supplied_instructions_persist_and_reach_titles_without_repeat(context, monkeypatch):
    client, manager, recording, _, _ = context
    received = []

    async def suggest(words, *, ai_instructions=""):
        received.append(ai_instructions)
        return "A grounded title", 20, 10

    monkeypatch.setattr(manager.titles, "suggest", suggest)
    notes = "Keep titles conversational."
    body = {
        **batch([recording], ai_instructions=notes),
        "generate_titles": True,
        "remote_consent": True,
    }
    project = create(client, body)
    assert project["status"] == "ready" and received == [notes]
    assert ProjectStore(manager.store).get(project["id"]).settings.ai_instructions == notes
    client.post(f"/api/projects/{project['id']}/resume", json={})
    assert finished_project(client, project["id"])["settings"]["ai_instructions"] == notes
    assert received == [notes]
