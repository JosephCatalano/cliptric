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

INSTRUCTIONS = """Review every option from this recording as Cliptric's final editor.
First assess each option on its own. Identify a concrete hook, enough context for a new viewer,
and an outcome, answer, emotional turn, useful insight or punchline that the speech supports.
A short gameplay exchange can qualify when both sides are understandable from the words alone.
If the moment starts weakly or ends early, check adjacent timed speech for a tighter opening,
brief setup or the real payoff. Preserve misdirection, useful pauses, callbacks and reactions
that complete the beat. Skip options that still depend on an unverified event or contain only
routine commentary, generic reactions or filler.
After that pass, compare the qualifying clips and remove alternate cuts of the same central beat.
Distinct moments can share a topic or participants. Keep each that resolves independently.
Use requested_candidates as a coverage target and max_candidates as the output limit. Keep all
distinct ready-to-use cuts up to max_candidates, ranked strongest first. A solid clip qualifies
on its own merits even if another is exceptional. Return fewer if the source has fewer qualifying
moments. Do not create filler, repeat a beat or stretch a cut to meet the target.
Use lead_first_segment and lead_last_segment as the starting cut when present. Keep their IDs if
the cut already works. When refining it, select inclusive IDs in that option and intersect the
original lead. Check last end minus first start, including pauses, against both duration bounds.
Use each option at most once. Give a specific title, concise reason and a short exact contiguous
quote from the selected range. Never invent speech, delivery, visual events or audience reactions.
In quality_mode, rate hook, context, payoff and standalone from 0 to 5. A 3 means the dimension
works for a new viewer without added explanation or a guessed outcome. A 4 is strong, and a 5
is exceptional. A 2 or lower means a specific weakness remains after possible refinement.
Every dimension must meet 3. Quote exact text from the chosen first and last segments for opening
and ending evidence. Set score to 5 * (hook + context + payoff + standalone). Otherwise score
0-100 for ranking and quality may be null.
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
        context_start, context_end = bisect_left(starts, start), bisect_right(starts, end)
        context = segments[context_start:context_end]
        lead_segments = segments[
            bisect_left(starts, lead.start, context_start, context_end) :
            bisect_left(starts, lead.end, context_start, context_end)
        ]
        anchored = (
            lead_segments
            and lead_segments[0].start == lead.start
            and max(segment.end for segment in lead_segments) == lead.end
        )
        option = {
            "option": len(options),
            "lead_start": lead.start,
            "lead_end": lead.end,
            "lead_evidence": lead.evidence,
            **(
                {"lead_first_segment": lead_segments[0].id, "lead_last_segment": lead_segments[-1].id}
                if anchored
                else {}
            ),
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
                    "requested_candidates": min(request.max_candidates, limit),
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
