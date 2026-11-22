"""Isolated HTTP transport tests; no OpenAI calls or credentials are used."""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import httpx2
import pytest

from backend.highlight_models import HighlightError, HighlightRequest
from backend.highlight_provider import ENDPOINT, MAX_RESPONSE_BYTES, OpenAIHighlights
from backend.highlight_selection import Segment
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools


def response(**changes):
    return {
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"candidates":[]}'}]}
        ],
        "usage": {"input_tokens": 100, "output_tokens": 20},
        **changes,
    }


def run(settings, handler):
    adapter = OpenAIHighlights(
        replace(settings, openai_api_key="synthetic-key"), transport=httpx2.MockTransport(handler)
    )
    payload = HighlightRequest(transcription_id=uuid4(), revision=1, remote_consent=True)
    return asyncio.run(
        adapter.suggest([Segment(4, 300, 320, "Untrusted transcript data.")], payload)
    )


def test_exact_destination_strict_schema_privacy_and_bounded_request(settings):
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == ENDPOINT
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer synthetic-key"
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["max_output_tokens"] == settings.highlight_output_tokens
        assert body["model"] == settings.highlight_model
        assert body["text"]["format"]["strict"] is True
        assert body["text"]["format"]["schema"]["additionalProperties"] is False
        assert '"start":300' in body["input"]
        assert "Untrusted transcript data." in body["input"]
        assert "untrusted" in body["instructions"]
        options = json.loads(body["input"].split("\nTRANSCRIPT DATA:\n", 1)[0])
        assert options["max_candidates"] == 5
        assert "tools" not in body and "previous_response_id" not in body
        assert "synthetic-key" not in request.content.decode()
        return httpx2.Response(200, json=response())

    result = run(settings, handler)
    assert result.candidates.candidates == []
    assert (result.input_tokens, result.output_tokens) == (100, 20)
    assert len(calls) == 1


def test_requested_clip_count_is_an_upper_bound_per_window(settings):
    counts = []

    def handler(request):
        body = json.loads(request.content)
        counts.append(
            json.loads(body["input"].split("\nTRANSCRIPT DATA:\n", 1)[0])["max_candidates"]
        )
        return httpx2.Response(200, json=response())

    adapter = OpenAIHighlights(
        replace(settings, openai_api_key="synthetic-key"), transport=httpx2.MockTransport(handler)
    )
    for maximum in (1, 3, 20):
        asyncio.run(
            adapter.suggest(
                [Segment(0, 0, 10, "Actual speech.")],
                HighlightRequest(
                    transcription_id=uuid4(),
                    revision=1,
                    remote_consent=True,
                    max_candidates=maximum,
                ),
            )
        )
    assert counts == [1, 3, 5]


@pytest.mark.parametrize("status", [302, 400, 401, 403, 429, 500, 503])
def test_errors_are_sanitized_without_following_redirects_or_retrying(settings, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(
            status,
            text="Private provider details synthetic-key",
            headers={"location": "https://example.invalid/secret"},
        )

    with pytest.raises(HighlightError) as caught:
        run(settings, handler)
    assert "synthetic-key" not in str(caught.value)
    assert "Private provider details" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "data",
    [
        response(status="incomplete"),
        response(status="failed"),
        response(output=[]),
        response(output=[None]),
        response(usage=None),
        response(usage={"input_tokens": True, "output_tokens": 1}),
        response(usage={"input_tokens": -1, "output_tokens": 1}),
        response(
            output=[{"type": "message", "content": [{"type": "output_text", "text": "not JSON"}]}]
        ),
        response(
            output=[
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": '{"candidates":[],"unexpected":true}'}
                    ],
                }
            ]
        ),
    ],
)
def test_incomplete_or_malformed_data_is_not_a_success(settings, data):
    with pytest.raises(HighlightError):
        run(settings, lambda _: httpx2.Response(200, json=data))


def test_refusal_is_explicit(settings):
    data = response(
        output=[
            {
                "type": "message",
                "content": [{"type": "refusal", "refusal": "Private refusal detail"}],
            }
        ]
    )
    with pytest.raises(HighlightError, match="declined") as caught:
        run(settings, lambda _: httpx2.Response(200, json=data))
    assert "Private refusal detail" not in str(caught.value)


def test_oversized_response_is_rejected(settings):
    with pytest.raises(HighlightError, match="safety limit"):
        run(settings, lambda _: httpx2.Response(200, content=b" " * (MAX_RESPONSE_BYTES + 1)))


def test_connection_failure_and_timeout_are_sanitized(settings):
    def disconnected(request):
        raise httpx2.ConnectError("Private detail synthetic-key", request=request)

    with pytest.raises(HighlightError, match="billing outcome") as caught:
        run(settings, disconnected)
    assert "synthetic-key" not in str(caught.value)

    async def too_slow(_):
        await asyncio.sleep(1)
        return httpx2.Response(200, json=response())

    with pytest.raises(HighlightError, match="timed out"):
        run(replace(settings, highlight_request_seconds=0.01), too_slow)


def test_missing_key_fails_before_transport_and_is_excluded_from_repr(settings):
    assert "synthetic-key" not in repr(replace(settings, openai_api_key="synthetic-key"))
    adapter = OpenAIHighlights(replace(settings, openai_api_key=""))
    with pytest.raises(HighlightError, match="backend OpenAI API key"):
        asyncio.run(
            adapter.suggest(
                [], HighlightRequest(transcription_id=uuid4(), revision=1, remote_consent=True)
            )
        )
