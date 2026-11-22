"""Bounded OpenAI transport for supplied-video editorial review."""

import asyncio

import httpx2

from .highlight_models import HighlightError
from .highlight_provider import ENDPOINT, MAX_RESPONSE_BYTES, OpenAIHighlights


async def request_structured(settings, body: dict, *, transport=None) -> bytes:
    try:
        async with asyncio.timeout(settings.highlight_request_seconds):
            async with httpx2.AsyncClient(
                trust_env=False,
                follow_redirects=False,
                timeout=settings.highlight_request_seconds,
                transport=transport,
            ) as client:
                async with client.stream(
                    "POST",
                    ENDPOINT,
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                    json=body,
                ) as response:
                    OpenAIHighlights._check_status(response.status_code)
                    output = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        if len(output) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise HighlightError("OpenAI response exceeded the safety limit.", 502)
                        output.extend(chunk)
        return bytes(output)
    except (httpx2.HTTPError, TimeoutError) as exc:
        raise HighlightError(
            "OpenAI request failed or timed out. Billing may be unknown; retry explicitly after checking usage.",
            503,
        ) from exc
