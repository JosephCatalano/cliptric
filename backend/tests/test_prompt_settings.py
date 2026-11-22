"""Local prompt persistence and request wiring; no live provider calls."""

import asyncio
import json
from concurrent.futures import Future
from uuid import uuid4

import httpx2
import pytest

from backend.batch_review import BatchReview, MultiBatchReview
from backend.batch_titles import BatchTitles
from backend.caption_emphasis import EmphasisProvider
from backend.editorial_review import EditorialReviewer
from backend.highlight_models import HighlightError, HighlightRequest
from backend.highlight_provider import OpenAIHighlights
from backend.highlight_selection import Segment
from backend.prompt_settings import PROTECTED_RULES, highlight_prompt_identity, overrides
from backend.social_copy import SocialCopyWriter
from backend.tests.test_projects import context as context
from backend.tests.test_projects import settings as settings
from backend.tests.test_projects import tools as tools

URL = "/api/settings/prompts"


def save(client, key, prompt, revision=None):
    if revision is None:
        revision = client.get(URL).json()["revision"]
    return client.patch(URL, json={"key": key, "prompt": prompt, "revision": revision})


def test_persistence_reset_conflict_and_cache_identity(context):
    client, _, _, _, configured = context
    initial = client.get(URL).json()
    assert {"highlight_discovery", "highlight_editorial"} <= {
        prompt["key"] for prompt in initial["prompts"]
    }
    assert all(not p["customized"] and p["value"] == p["default"] for p in initial["prompts"])
    before = highlight_prompt_identity(configured, discovery=True)
    before_review = highlight_prompt_identity(configured, discovery=False)
    assert save(client, "highlight_editorial", "Preserve the best payoff.").status_code == 200
    assert highlight_prompt_identity(configured, discovery=False) != before_review
    assert highlight_prompt_identity(configured, discovery=True) == before
    assert save(client, "titles", "Use concise titles.").status_code == 200
    assert highlight_prompt_identity(configured, discovery=True) == before
    assert save(client, "highlight_discovery", "Prefer complete stories.").status_code == 200
    assert overrides(configured)["highlight_discovery"] == "Prefer complete stories."
    changed = highlight_prompt_identity(configured, discovery=True)
    assert changed != before
    assert save(client, "highlight_discovery", "Stale edit", revision=0).status_code == 409
    assert overrides(configured)["highlight_discovery"] == "Prefer complete stories."
    assert save(client, "project_guidance", "Prioritize project tone.").status_code == 200
    assert highlight_prompt_identity(configured, discovery=True) != changed
    default = next(p["default"] for p in initial["prompts"] if p["key"] == "highlight_discovery")
    assert save(client, "highlight_discovery", default).status_code == 200
    assert "highlight_discovery" not in overrides(configured)


@pytest.mark.parametrize(
    "key,prompt",
    [
        ("unknown", "Text"),
        ("titles", " \n "),
        ("titles", "Bad\x00text"),
        ("titles", "x" * 12001),
        ("titles", "\u4e2d" * 5000),
    ],
)
def test_invalid_prompts_do_not_mutate(context, key, prompt):
    client = context[0]
    assert save(client, key, prompt).status_code == 422
    assert client.get(URL).json()["revision"] == 0


def test_inflight_copy_blocks_prompt_edits(context):
    client, manager, *_ = context
    manager.copy_active.add(("synthetic-project", "synthetic-clip"))
    try:
        assert save(client, "titles", "New title prompt").status_code == 409
        assert client.get(URL).json()["revision"] == 0
    finally:
        manager.copy_active.clear()


def test_queued_project_blocks_prompt_edits(context):
    client, manager, *_ = context
    manager.scheduled["synthetic-project"] = Future()
    try:
        assert save(client, "titles", "New title prompt").status_code == 409
    finally:
        del manager.scheduled["synthetic-project"]


def test_active_highlight_blocks_prompt_edits(context):
    client, manager, *_ = context
    manager.highlights.active = "synthetic-analysis"
    try:
        assert save(client, "titles", "New title prompt").status_code == 409
    finally:
        manager.highlights.active = None


@pytest.mark.parametrize(
    "key,provider_type",
    [
        ("highlight_discovery", OpenAIHighlights),
        ("highlight_editorial", EditorialReviewer),
        ("batch_review", BatchReview),
        ("batch_highlights", MultiBatchReview),
        ("titles", BatchTitles),
        ("post_copy", SocialCopyWriter),
        ("emphasis", EmphasisProvider),
    ],
)
def test_each_request_uses_saved_prompt_and_protected_schema(context, key, provider_type):
    client, _, _, _, configured = context
    assert save(client, key, "Custom editorial instructions.").status_code == 200
    assert save(client, "project_guidance", "Custom project guidance.").status_code == 200
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx2.Response(400, json={"error": "synthetic stop after capturing request"})

    provider = provider_type(configured, transport=httpx2.MockTransport(respond))
    segments = [
        Segment(0, 0, 15, "This is enough source speech to support a grounded editorial request.")
    ]
    if key == "highlight_discovery":
        call = provider.suggest(
            segments,
            HighlightRequest(
                transcription_id=uuid4(),
                revision=1,
                remote_consent=True,
                ai_instructions="Dry humor",
            ),
        )
    elif key == "highlight_editorial":
        call = provider.review(
            "[]",
            HighlightRequest(
                transcription_id=uuid4(),
                revision=1,
                remote_consent=True,
                ai_instructions="Dry humor",
            ),
            1,
        )
    elif key in {"batch_review", "batch_highlights"}:
        call = provider.suggest(segments, 20, ai_instructions="Dry humor", quality_mode=False)
    elif key == "emphasis":
        call = provider.suggest(["Grounded", "speech"])
    else:
        call = provider.suggest([{"text": segments[0].text}], ai_instructions="Dry humor")
    with pytest.raises(HighlightError):
        asyncio.run(call)
    assert len(seen) == 1
    body = seen[0]
    assert body["instructions"].startswith("Custom editorial instructions.")
    assert PROTECTED_RULES in body["instructions"]
    assert ("Custom project guidance." in body["instructions"]) == (key != "emphasis")
    assert body["text"]["format"]["strict"] is True
    assert body["store"] is False
