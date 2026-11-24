"""Explicit, bounded AI word emphasis grounded in the current saved transcript."""

import asyncio
import json
import re
from typing import Annotated, Literal
from uuid import UUID

import httpx2
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .captions import CaptionCustomization, _display_text, censor_matches, clean_emphasis
from .highlight_models import HighlightError
from .highlight_provider import ENDPOINT, MAX_RESPONSE_BYTES, OpenAIHighlights
from .prompt_settings import request_prompt
from .transcription_models import TranscriptionError, validate_window

EMPHASIS_INSTRUCTIONS = """Select up to 12 individual transcript tokens for sparse caption emphasis.
Prefer the revealing word in a punchline, a meaningful contrast, a key concept or an emotional
turn. Read the surrounding words so emphasis preserves the sentence's meaning. Do not assume
vocal stress or delivery. Avoid fillers, repeated emphasis on the same idea and isolated profanity.
Return only actual token IDs. Return an empty list when emphasis would not help the viewer.
"""


class EmphasisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    revision: int = Field(ge=1, strict=True)
    start: float = Field(ge=0, strict=True)
    end: float = Field(gt=0, strict=True)
    remote_consent: Literal[True]

    @field_validator("remote_consent", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Explicit consent is required to send transcript text to OpenAI.")
        return value


class EmphasisProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token_ids: list[Annotated[int, Field(strict=True, ge=0)]] = Field(max_length=20)

    @field_validator("token_ids")
    @classmethod
    def valid_ids(cls, value):
        if any(type(identifier) is not int or identifier < 0 for identifier in value):
            raise ValueError("Token IDs must be nonnegative integers.")
        if len(value) != len(set(value)):
            raise ValueError("Token IDs must be unique.")
        return value


class EmphasisProvider:
    def __init__(self, settings, *, transport=None):
        self.settings, self.transport = settings, transport

    async def suggest(self, tokens: list[str]):
        if not self.settings.openai_api_key:
            raise HighlightError(
                "Configure the existing backend OpenAI key before suggesting emphasis.", 503
            )
        body = {
            "model": self.settings.highlight_model,
            "store": False,
            "instructions": request_prompt(self.settings, "emphasis", EMPHASIS_INSTRUCTIONS),
            "input": json.dumps(
                {"tokens": [{"id": i, "text": token} for i, token in enumerate(tokens)]}
            ),
            "max_output_tokens": 1024,
            "reasoning": {"effort": "low"},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "caption_emphasis",
                    "strict": True,
                    "schema": EmphasisProposal.model_json_schema(),
                }
            },
        }
        try:
            async with asyncio.timeout(self.settings.highlight_request_seconds):
                async with httpx2.AsyncClient(
                    trust_env=False,
                    follow_redirects=False,
                    timeout=self.settings.highlight_request_seconds,
                    transport=self.transport,
                ) as client:
                    async with client.stream(
                        "POST",
                        ENDPOINT,
                        headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                        json=body,
                    ) as response:
                        OpenAIHighlights._check_status(response.status_code)
                        output = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            if len(output) + len(chunk) > MAX_RESPONSE_BYTES:
                                raise HighlightError(
                                    "OpenAI response exceeded the safety limit.", 502
                                )
                            output.extend(chunk)
            data = json.loads(output)
            if data.get("status") != "completed" or data.get("error") is not None:
                raise HighlightError(
                    "Emphasis analysis did not complete. No suggestions were applied.", 502
                )
            texts = []
            for item in data.get("output", []):
                if item.get("type") != "message":
                    continue
                for content in item.get("content", []):
                    if content.get("type") == "refusal":
                        raise HighlightError(
                            "The model declined emphasis analysis. Manual emphasis remains available."
                        )
                    if content.get("type") == "output_text":
                        texts.append(content["text"])
            if len(texts) != 1:
                raise ValueError("Missing structured result")
            result = EmphasisProposal.model_validate_json(texts[0])
            if any(identifier >= len(tokens) for identifier in result.token_ids):
                raise ValueError("Unmatched source token")
            usage = data["usage"]
            if any(
                type(usage[key]) is not int or usage[key] < 0
                for key in ("input_tokens", "output_tokens")
            ):
                raise ValueError("Invalid usage")
            return {
                "words": clean_emphasis([tokens[i] for i in result.token_ids]),
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
            }
        except (httpx2.HTTPError, TimeoutError) as exc:
            raise HighlightError(
                "OpenAI request failed or timed out. Billing may be unknown; retry explicitly after checking connectivity.",
                503,
            ) from exc
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise HighlightError(
                "OpenAI returned invalid emphasis data. No suggestions were applied.", 502
            ) from exc


def create_emphasis_router(transcriptions, settings, provider=None):
    router = APIRouter(prefix="/api/transcriptions")
    lock = asyncio.Lock()
    provider = provider or EmphasisProvider(settings)

    class ReviewRequest(BaseModel):
        model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
        revision: int = Field(ge=1, strict=True)
        start: float = Field(ge=0, strict=True)
        end: float = Field(gt=0, strict=True)
        customization: CaptionCustomization

    @router.post("/{identifier}/caption-review")
    def review(identifier: UUID, request: ReviewRequest):
        try:
            page = transcriptions.words(str(identifier), request.start, request.end)
            if page.revision != request.revision:
                raise HTTPException(409, "Transcript changed. Reload before reviewing matches.")
            matches = [
                {
                    "id": word.id,
                    "start": word.start,
                    "end": word.end,
                    "original": word.text,
                    "display": _display_text(word.text, request.customization),
                }
                for word in page.words
                if censor_matches(word.text, request.customization)
            ]
            return {
                "matches": matches[:200],
                "total_matches": len(matches),
                "truncated": len(matches) > 200,
            }
        except TranscriptionError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @router.post("/{identifier}/emphasis")
    async def emphasis(identifier: UUID, request: EmphasisRequest):
        if lock.locked():
            raise HTTPException(
                409, "An emphasis request is already running. Wait before trying again."
            )
        async with lock:
            try:
                job = transcriptions.transcripts.job(str(identifier))
                if job is None:
                    raise HTTPException(404, "Transcript not found.")
                recording = transcriptions.store.recording(job.recording_id)
                if recording is None:
                    raise HTTPException(404, "Recording not found.")
                validate_window(
                    request.start, request.end, recording.duration, settings.max_clip_seconds
                )
                page = transcriptions.transcripts.words(
                    str(identifier), request.start, request.end, revision=request.revision
                )
                tokens = [
                    token.group()
                    for word in page.words
                    for token in re.finditer(r"\w+(?:['’]\w+)*", word.text)
                ]
                if len(tokens) > 3000 or len(json.dumps(tokens).encode("utf-8")) > 64 * 1024:
                    raise HTTPException(
                        422, "Select a smaller transcript interval for emphasis analysis."
                    )
                result = (
                    await provider.suggest(tokens)
                    if tokens
                    else {"words": [], "input_tokens": 0, "output_tokens": 0}
                )
                current = transcriptions.transcripts.job(str(identifier))
                if current is None or current.revision != request.revision:
                    raise HTTPException(
                        409,
                        "Transcript changed during analysis. Suggestions were discarded; usage may have been billed.",
                    )
                return {**result, "revision": request.revision}
            except (HighlightError, TranscriptionError) as exc:
                raise HTTPException(exc.status_code, str(exc)) from exc

    return router
