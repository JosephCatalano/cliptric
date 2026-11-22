"""One bounded editorial comparison per long-form highlight job."""

import json
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .highlight_models import (
    Candidate,
    HighlightError,
    HighlightRequest,
    ProposedCandidate,
    ProposedHighlights,
)
from .highlight_provider import parse_highlight_response
from .highlight_selection import Segment, ground_candidates, merge_ranges, window_text
from .prompt_settings import request_prompt
from .structured_output import request_structured

MAX_OPTIONS = 24
MAX_RESULTS = 20
MAX_OPTION_BYTES = 96 * 1024
CONTEXT_SECONDS = 30

INSTRUCTIONS = """Compare these leads from one recording as Cliptric's final editor.
Choose only the strongest independently watchable clips, up to max_candidates.
Return fewer or none if the material is weak or the payoff is missing.
Compare the actual speech, not the discovery rank. Remove alternate versions of
the same story, central joke or payoff, including paraphrases across windows.
Preserve independent moments from the same conversation when each resolves on its own.
Check the opening, context, escalation, payoff, pacing and standalone clarity.
Preserve comedic setup, misdirection, meaningful pauses, callbacks and the ending.
For an exchange, include the answer. For a story, include the authentic resolution.
Trim weak introductions, tangents and repetition only when the complete arc survives.
Adjacent timed speech can repair window-edge cuts. Use only inclusive IDs within the
chosen option's segments, intersecting its original lead. Use each option at most once.
Duration includes gaps in speech. Never invent delivery, visual events or audience reactions.
Provide a specific title and reason, and a contiguous exact quote from the final range.
Score 0-100 as editorial quality. In quality_mode, every dimension must be at least
3 of 5. Use exact opening and ending evidence from the first and last selected segments,
and score = 5 * (hook + context + payoff + standalone). Otherwise quality may be null.
"""


class EditorialCandidate(ProposedCandidate):
    option: int = Field(ge=0, lt=MAX_OPTIONS, strict=True)


class EditorialProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[EditorialCandidate] = Field(max_length=MAX_RESULTS)


@dataclass(frozen=True)
class ReviewOption:
    lead: Candidate
    segments: list[Segment]


def shortlist(
    candidates: list[Candidate],
    windows: list[list[Segment]],
    request: HighlightRequest,
) -> tuple[list[ReviewOption], str]:
    segments = sorted({s.id: s for window in windows for s in window}.values(), key=lambda s: s.id)
    starts = [s.start for s in segments]
    excluded_ranges = merge_ranges(request.excluded_ranges)
    options: list[ReviewOption] = []
    payload: list[dict] = []
    budget = MAX_OPTION_BYTES - 2  # Outer JSON array brackets.
    seen = set()
    for lead in candidates:
        if len(options) == MAX_OPTIONS:
            break
        if lead.fingerprint in seen:
            continue
        start, end = lead.start - CONTEXT_SECONDS, lead.end + CONTEXT_SECONDS
        for excluded in excluded_ranges:
            if excluded.end <= lead.start:
                start = max(start, excluded.end)
            if excluded.start >= lead.end:
                end = min(end, excluded.start)
        context = segments[bisect_left(starts, start) : bisect_right(starts, end)]
        option = {
            "option": len(options),
            "lead_start": lead.start,
            "lead_end": lead.end,
            "lead_evidence": lead.evidence,
            "segments": json.loads(window_text(context)),
        }
        size = len(json.dumps(option, ensure_ascii=False).encode()) + 2
        if size > budget:
            continue
        budget -= size
        seen.add(lead.fingerprint)
        options.append(ReviewOption(lead, context))
        payload.append(option)
    if candidates and not options:
        raise HighlightError(
            "No editorial lead fits the review input budget. Use a shorter duration."
        )
    return options, json.dumps(payload, ensure_ascii=False)


def ground_review(
    proposal: EditorialProposal,
    options: list[ReviewOption],
    request: HighlightRequest,
    duration: float,
    job_id: str,
    limit: int,
) -> tuple[list[Candidate], int]:
    if len(proposal.candidates) > limit:
        raise HighlightError("Editorial review returned more clips than its request limit.", 502)
    accepted: list[Candidate] = []
    rejected = 0
    used = set()
    for candidate in proposal.candidates:
        if candidate.option >= len(options) or candidate.option in used:
            rejected += 1
            continue
        option = options[candidate.option]
        grounded, invalid = ground_candidates(
            ProposedHighlights(candidates=[candidate]),
            option.segments,
            request,
            duration,
            job_id,
        )
        if invalid or not (
            grounded[0].start < option.lead.end and grounded[0].end > option.lead.start
        ):
            rejected += 1
            continue
        used.add(candidate.option)
        accepted.extend(grounded)
    return accepted, rejected


class EditorialReviewer:
    def __init__(self, settings: Settings, *, transport=None):
        self.settings, self.transport = settings, transport

    async def review(
        self,
        payload: str,
        request: HighlightRequest,
        limit: int,
    ) -> tuple[EditorialProposal, int, int]:
        schema = EditorialProposal.model_json_schema()
        for name in ("EditorialCandidate", "EditorialQuality"):
            definition = schema["$defs"][name]
            definition["required"] = list(definition["properties"])
        body = {
            "model": self.settings.highlight_model,
            "store": False,
            "instructions": request_prompt(
                self.settings,
                "highlight_editorial",
                INSTRUCTIONS,
                bool(request.ai_instructions),
            ),
            "input": json.dumps(
                {
                    "min_seconds": request.min_seconds,
                    "max_seconds": request.max_seconds,
                    "max_candidates": limit,
                    "quality_mode": request.quality_mode,
                    **(
                        {"project_instructions": request.ai_instructions}
                        if request.ai_instructions
                        else {}
                    ),
                }
            )
            + "\nEDITORIAL OPTIONS DATA:\n"
            + payload,
            "reasoning": {"effort": "low"},
            "max_output_tokens": min(8192, max(self.settings.highlight_output_tokens, limit * 400)),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "editorial_review",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        raw = await request_structured(self.settings, body, transport=self.transport)
        return parse_highlight_response(raw, EditorialProposal)
