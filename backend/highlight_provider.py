import asyncio
import json
from typing import TypeVar

import httpx2
from pydantic import BaseModel

from .config import Settings
from .highlight_context import PROFILES
from .highlight_models import DiscoveryProposal, HighlightError, HighlightRequest, ProviderResult
from .highlight_selection import Segment, window_text
from .prompt_settings import request_prompt

ENDPOINT = "https://api.openai.com/v1/responses"
MAX_RESPONSE_BYTES = 256 * 1024
Proposal = TypeVar("Proposal", bound=BaseModel)
DISCOVERY_INSTRUCTIONS = """Scan the whole timed window from beginning to end for distinct leads.
This is discovery. Keep promising beats for review, including complete short exchanges, specific
reactions, reversals, failed plans, punchlines, callbacks, useful insights and stories with a payoff.
For gameplay, look for speech that establishes what was attempted, what changed and how someone
responded. One revealing line and its response can be as useful as a longer story. Generic
exclamations, routine gameplay narration and banter with no turn are weak leads on their own.
If a beat depends on footage, identify the missing visual context in the reason and describe only
what the speech supports. Keep a promising window-edge lead when its own speech supplies concrete
evidence and adjacent timed speech may complete its setup or ending during review.
Return up to max_candidates promising leads. Prefer different beats over alternate cuts of the
same event. Use actual inclusive segment IDs. Check last end minus first start against min_seconds
and max_seconds, including gaps. Keep meaningful adjacent setup or response when it completes the
beat and fits the bounds. Skip a beat if it only fits by adding filler. Quote 8-320 exact contiguous
characters from inside the chosen range. Give a faithful temporary title and reason. Set quality to null.
Use the chapter excerpts and shared terms to search for setups, questions, answers, turning points
and callbacks. The chapter map is an index, not proof of a story relationship. Use the actual
timed segments to propose a cut. Where provided, audio measurements and visual observations can
guide attention, but do not prove audience reactions, emotions, or events between sampled frames.
For narrative, return up to three concise notes for consequential sections across this window.
Label the narrative role and summarize what happens, with exact boundary segment IDs and an exact
contiguous supporting quote. Include noteworthy setups or questions even if their resolution is
outside the window. Return an empty array if there is no substantive narrative. Do not ascribe
identity to an unlabelled speaker.
"""


class OpenAIHighlights:
    def __init__(self, settings: Settings, *, transport: httpx2.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    async def suggest(
        self, window: list[Segment], request: HighlightRequest, *, context: dict | None = None
    ) -> ProviderResult:
        if not self.settings.openai_api_key:
            raise HighlightError(
                "Configure a backend OpenAI API key before analysing transcripts.", 503
            )
        schema = DiscoveryProposal.model_json_schema()
        schema["required"] = list(schema["properties"])
        # Strict structured output requires optional nullable fields to be listed
        # as required too; older saved/provider fixtures still load with null defaults.
        schema["$defs"]["ProposedCandidate"]["required"] = list(
            schema["$defs"]["ProposedCandidate"]["properties"]
        )
        schema["$defs"]["ProposedCandidate"]["properties"]["quality"].pop("default", None)
        body = {
            "model": self.settings.highlight_model,
            "store": False,
            "instructions": request_prompt(
                self.settings,
                "highlight_discovery",
                DISCOVERY_INSTRUCTIONS,
                bool(request.ai_instructions),
            ),
            "input": json.dumps(
                {
                    "duration_mode": request.duration_mode,
                    "min_seconds": request.min_seconds,
                    "max_seconds": request.max_seconds,
                    "max_candidates": min(5, request.max_candidates),
                    "quality_mode": False,
                    "content_profile": request.content_profile,
                    "profile_guidance": PROFILES[request.content_profile].guidance,
                    "source_context": context or {},
                    **(
                        {"project_instructions": request.ai_instructions}
                        if request.ai_instructions
                        else {}
                    ),
                }
            )
            + "\nTRANSCRIPT DATA:\n"
            + window_text(window),
            "max_output_tokens": self.settings.highlight_output_tokens,
            "reasoning": {"effort": "low"},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "highlights",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        try:
            # Fixed HTTPS destination, verified TLS, no redirects/proxies or automatic retries.
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
                        self._check_status(response.status_code)
                        output = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            if len(output) + len(chunk) > MAX_RESPONSE_BYTES:
                                raise HighlightError(
                                    "OpenAI response exceeded the safety limit.", 502
                                )
                            output.extend(chunk)
            return self.parse(bytes(output))
        except (httpx2.HTTPError, TimeoutError) as exc:
            # Provider bodies and exception strings may contain private request data.
            raise HighlightError(
                "OpenAI request failed or timed out. Its billing outcome may be unknown; check connectivity and retry explicitly.",
                503,
            ) from exc

    @staticmethod
    def _check_status(status: int) -> None:
        if status in (401, 403):
            raise HighlightError(
                "OpenAI rejected API access. Check the backend key and model permissions.", 503
            )
        if status == 429:
            raise HighlightError(
                "OpenAI quota or rate limit reached. Check API billing/limits before retrying; no automatic retry was made.",
                503,
            )
        if status != 200:
            raise HighlightError(
                f"OpenAI returned HTTP {status}. Check service availability and model configuration before retrying.",
                503,
            )

    @staticmethod
    def parse(raw: bytes) -> ProviderResult:
        return ProviderResult(*parse_highlight_response(raw, DiscoveryProposal))


def parse_highlight_response(
    raw: bytes, proposal_type: type[Proposal]
) -> tuple[Proposal, int, int]:
    try:
        data = json.loads(raw)
        if (
            not isinstance(data, dict)
            or data.get("status") != "completed"
            or data.get("error") is not None
        ):
            raise HighlightError(
                "OpenAI did not complete the analysis. No partial suggestions were published.",
                502,
            )
        texts = []
        for item in data.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "refusal":
                    raise HighlightError(
                        "The model declined to analyse this transcript. Manual selection remains available.",
                        422,
                    )
                if content.get("type") == "output_text":
                    texts.append(content["text"])
        if len(texts) != 1 or not isinstance(texts[0], str):
            raise HighlightError("OpenAI returned no single structured result.", 502)
        candidates = proposal_type.model_validate_json(texts[0])
        usage = data["usage"]
        input_tokens, output_tokens = usage["input_tokens"], usage["output_tokens"]
        if (
            type(input_tokens) is not int
            or type(output_tokens) is not int
            or min(input_tokens, output_tokens) < 0
        ):
            raise ValueError("Invalid usage")
        return candidates, input_tokens, output_tokens
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise HighlightError(
            "OpenAI returned malformed highlight data. No partial suggestions were published.",
            502,
        ) from exc
