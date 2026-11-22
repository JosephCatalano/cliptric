"""Posting-copy contracts with explicit synthetic provider/media fixtures."""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import httpx2
import pytest
from pydantic import ValidationError

from backend.highlight_models import HighlightError
from backend.social_copy import CopyOption, SocialCopy, SocialCopyWriter
from backend.tests.test_batches import batch
from backend.tests.test_projects import context as context
from backend.tests.test_projects import create, finished, payload
from backend.tests.test_projects import settings as settings
from backend.tests.test_projects import tools as tools
from backend.tests.test_workspace_ux import edit


def proposal(quote="change my weapon"):
    return SocialCopy.model_validate(
        {
            "recommended": {
                "description": "The reload timing says everything.",
                "hashtags": ["#Gaming", "#Reload", "#Timing"],
            },
            "alternatives": [
                {
                    "style": "curiosity",
                    "description": "A weapon change at the worst moment.",
                    "hashtags": ["#Gaming"],
                },
                {
                    "style": "conversation",
                    "description": "When would you have switched weapons?",
                    "hashtags": ["#Gaming"],
                },
                {
                    "style": "funny_or_relatable",
                    "description": "My timing needs its own tutorial.",
                    "hashtags": ["#Gaming"],
                },
            ],
            "reason": "A concise angle supported by the selected speech.",
            "evidence_quote": quote,
        }
    )


def response(value):
    return {
        "status": "completed",
        "error": None,
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": value.model_dump_json()}],
            }
        ],
        "usage": {"input_tokens": 100, "output_tokens": 80},
    }


def test_provider_uses_grounded_strict_schema_and_preferences(settings):
    seen = []

    def respond(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx2.Response(200, json=response(proposal()))

    writer = SocialCopyWriter(
        replace(settings, openai_api_key="synthetic"), transport=httpx2.MockTransport(respond)
    )
    value, inputs, outputs = asyncio.run(
        writer.suggest(
            [{"text": "I tried to change my weapon while the barn was under fire."}],
            ai_instructions="Use dry humor.",
        )
    )
    assert (inputs, outputs) == (100, 80)
    assert value.recommended.ready().endswith("#Gaming #Reload #Timing")
    assert seen[0]["store"] is False
    assert seen[0]["text"]["format"]["schema"]["additionalProperties"] is False
    assert json.loads(seen[0]["input"])["project_instructions"] == "Use dry humor."
    assert "not video access" in seen[0]["instructions"]


@pytest.mark.parametrize("fault", ["quote", "generic_tag", "duplicate", "bad_tag"])
def test_invalid_or_ungrounded_output_fails(settings, fault):
    data = proposal().model_dump()
    if fault == "quote":
        data["evidence_quote"] = "invented statement"
    if fault == "generic_tag":
        data["recommended"]["hashtags"] = ["#viral"]
    if fault == "duplicate":
        data["alternatives"][0]["description"] = data["recommended"]["description"]
    if fault == "bad_tag":
        data["recommended"]["hashtags"] = ["#not a tag"]
    raw = response(proposal())
    raw["output"][0]["content"][0]["text"] = json.dumps(data)
    writer = SocialCopyWriter(
        replace(settings, openai_api_key="synthetic"),
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=raw)),
    )
    with pytest.raises(HighlightError):
        asyncio.run(
            writer.suggest([{"text": "I tried to change my weapon while the barn was under fire."}])
        )


def test_sparse_speech_does_not_make_paid_request(settings):
    def unexpected(request):
        pytest.fail("Sparse speech must not be sent for paid copy generation")

    writer = SocialCopyWriter(
        replace(settings, openai_api_key="synthetic"), transport=httpx2.MockTransport(unexpected)
    )
    with pytest.raises(HighlightError, match="Too little"):
        asyncio.run(writer.suggest([{"text": "Oh no"}]))


def test_unspaced_source_language_is_not_mistaken_for_sparse_speech(settings):
    text = "\u67aa\u706b\u4e2d\u6362\u6b66\u5668\u4e0d\u662f\u4ec0\u4e48\u597d\u65f6\u673a\u4f46\u662f\u5927\u5bb6\u8fd8\u662f\u60f3\u8bd5\u4e00\u8bd5"
    writer = SocialCopyWriter(
        replace(settings, openai_api_key="synthetic"),
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, json=response(proposal(text[:4])))
        ),
    )
    result, _, _ = asyncio.run(writer.suggest([{"text": text}]))
    assert result.evidence_quote == text[:4]


def test_generated_copy_is_per_clip_saved_and_stale_after_trim(context, monkeypatch):
    client, manager, recording, _, _ = context
    calls = []

    async def suggest(words, **kwargs):
        calls.append(words)
        return proposal(words[0]["text"]), 100, 80

    monkeypatch.setattr(manager.copy_writer, "suggest", suggest)
    request = payload(recording)
    request.update(generate_post_copy=True, remote_consent=True)
    result = create(client, request)
    assert result["status"] == "ready", result
    assert len(calls) == 2 and len(calls[0]) < 16
    first = result["items"][0]
    assert first["post_copy"] and first["post_copy_input_tokens"] == 100
    assert manager.records.get(result["id"]).items[0].post_copy is not None
    assert client.get(f"/api/clips/{first['export_id']}/post-copy").json()["ready_to_post"]
    route = f"/api/projects/{result['id']}/clips/{first['id']}"
    response = client.patch(route, json=edit(first, start=first["start"] + 1))
    assert response.status_code == 202, response.text
    result = finished(client, result["id"])
    changed = result["items"][0]
    assert changed["post_copy_stale"] and len(calls) == 2
    assert (
        client.get(f"/api/clips/{changed['export_id']}/post-copy").json()["ready_to_post"] is None
    )
    renders = len(manager.media.renders)
    response = client.patch(
        route + "/post-copy",
        json={
            "revision": changed["revision"],
            "content": {"description": "My reviewed description.", "hashtags": ["#Gaming"]},
        },
    )
    assert response.status_code == 200, response.text
    assert (
        client.get(f"/api/clips/{changed['export_id']}/post-copy").json()["ready_to_post"]
        == "My reviewed description.\n\n#Gaming"
    )
    assert len(manager.media.renders) == renders


def test_consent_idempotency_and_explicit_retry(context, monkeypatch):
    client, manager, recording, _, _ = context
    calls = []

    async def suggest(words, **kwargs):
        calls.append(True)
        raise HighlightError("Synthetic unknown outcome", 503)

    monkeypatch.setattr(manager.copy_writer, "suggest", suggest)
    request = payload(recording)
    request.update(generate_post_copy=True, remote_consent=False)
    assert client.post("/api/projects", json=request).status_code == 422
    request["remote_consent"] = True
    result = create(client, request)
    assert result["status"] == "ready" and len(calls) == 2
    client.post(f"/api/projects/{result['id']}/resume", json={})
    result = finished(client, result["id"])
    assert len(calls) == 2
    item = result["items"][0]
    route = f"/api/projects/{result['id']}/clips/{item['id']}/post-copy"
    body = {"request_id": str(uuid4()), "revision": item["revision"], "consent": False}
    assert client.post(route, json=body).status_code == 422
    body["consent"] = True
    assert client.post(route, json=body).status_code == 200
    assert len(calls) == 3
    assert client.post(route, json=body).status_code == 200 and len(calls) == 3
    other = {**body, "request_id": str(uuid4())}
    assert client.post(route, json=other).status_code == 200 and len(calls) == 4
    assert client.post(route, json=body).status_code == 200 and len(calls) == 4


def test_bulk_copy_works_with_video_captions_disabled(context, monkeypatch):
    client, manager, recording, _, _ = context

    async def suggest(words, **kwargs):
        return proposal(words[0]["text"]), 20, 30

    monkeypatch.setattr(manager.copy_writer, "suggest", suggest)
    request = batch([recording], caption_preset="none")
    request.update(generate_post_copy=True, remote_consent=True)
    result = create(client, request)
    assert result["status"] == "ready", result
    assert result["items"][0]["post_copy"]
    assert result["items"][0]["export"]["captions"] is None


def test_manual_hashtag_validation():
    with pytest.raises(ValidationError):
        CopyOption(description="Hello", hashtags=["#One", "#one"])


def test_pacing_removals_invalidate_copy_and_exclude_removed_speech(context, monkeypatch):
    client, manager, recording, _, _ = context
    calls = []

    async def suggest(words, **kwargs):
        calls.append(words)
        return proposal(words[0]["text"]), 10, 20

    monkeypatch.setattr(manager.copy_writer, "suggest", suggest)
    request = payload(recording)
    request.update(generate_post_copy=True, remote_consent=True)
    result = create(client, request)
    item = result["items"][0]
    route = f"/api/projects/{result['id']}/clips/{item['id']}"
    quality = {
        **item["quality"],
        "removals": [{"start": 15.0, "end": 19.0, "reason": "manual", "enabled": True}],
    }
    assert client.patch(route, json=edit(item, quality=quality)).status_code == 202
    result = finished(client, result["id"])
    item = result["items"][0]
    assert item["post_copy_stale"] and len(calls) == 2
    response = client.post(
        route + "/post-copy",
        json={"request_id": str(uuid4()), "revision": item["revision"], "consent": True},
    )
    assert response.status_code == 200, response.text
    assert len(calls[-1]) == 1 and calls[-1][0]["text"] == "Evidence 2."


def test_active_copy_blocks_edit_and_delete(context):
    client, manager, recording, _, _ = context
    result = create(client, payload(recording))
    item = result["items"][0]
    key = (result["id"], item["id"])
    manager.copy_active.add(key)
    try:
        route = f"/api/projects/{result['id']}/clips/{item['id']}/post-copy"
        assert (
            client.patch(
                route,
                json={
                    "revision": item["revision"],
                    "content": {"description": "Reviewed", "hashtags": []},
                },
            ).status_code
            == 409
        )
        assert client.delete(f"/api/projects/{result['id']}").status_code == 409
    finally:
        manager.copy_active.discard(key)
