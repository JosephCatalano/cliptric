"""One bounded editorial comparison per long-form highlight job."""

import json
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from itertools import zip_longest

from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .highlight_models import (
    WINDOW_SECONDS,
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
Assess each lead on its own. Keep every distinct clip with a clear hook, usable context
and a supported payoff, up to max_candidates. If more clips qualify, rank them and keep
the strongest. Return fewer when fewer qualify. Compare the actual speech and remove alternate
versions of the same story, central joke or payoff, including paraphrases across windows.
Preserve independent moments from the same conversation when each resolves on its own.
A quick gameplay reaction or exchange can qualify when its setup and payoff are clear from
the transcript. A setup can be as short as one line. Reject a clip whose payoff requires
assuming an unverified event. Keep clips as short as the full moment and duration bounds allow.
Check the opening, context, escalation, payoff, pacing and standalone clarity.
Preserve comedic setup, misdirection, meaningful pauses, callbacks and the ending.
For an exchange, include the answer. For a story, include the authentic resolution.
Trim weak introductions, tangents and repetition only when the complete arc survives.
Adjacent timed speech can repair window-edge cuts. Use only inclusive IDs within the
chosen option's segments, intersecting its original lead. Use each option at most once.
Duration includes gaps in speech. Never invent delivery, visual events or audience reactions.
Provide a specific title, a concise reason, and a short contiguous exact quote from the final range.
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


def _coverage_order(candidates: list[Candidate], start: float) -> list[Candidate]:
    if len(candidates) <= 1:
        return candidates
    # Candidates arrive in score order. Cycle through time bands when spending the input budget.
    width = max(WINDOW_SECONDS, (max(lead.start for lead in candidates) - start) / 6)
    bands: dict[int, list[Candidate]] = {}
    for lead in candidates:
        band = min(5, int((lead.start - start) // width))
        bands.setdefault(band, []).append(lead)
    return [lead for group in zip_longest(*bands.values()) for lead in group if lead is not None]


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
    for lead in _coverage_order(candidates, request.analysis_start):
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
) -> tuple[list[Candidate], Counter[str]]:
    if len(proposal.candidates) > limit:
        raise HighlightError("Editorial review returned more clips than its request limit.", 502)
    accepted: list[Candidate] = []
    rejected: Counter[str] = Counter()
    used = set()
    for candidate in proposal.candidates:
        if candidate.option >= len(options):
            rejected["unknown_option"] += 1
            continue
        if candidate.option in used:
            rejected["reused_option"] += 1
            continue
        option = options[candidate.option]
        grounded, invalid = ground_candidates(
            ProposedHighlights(candidates=[candidate]),
            option.segments,
            request,
            duration,
            job_id,
            rejection_reasons=rejected,
        )
        if invalid:
            continue
        if not (grounded[0].start < option.lead.end and grounded[0].end > option.lead.start):
            rejected["outside_original_lead"] += 1
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
