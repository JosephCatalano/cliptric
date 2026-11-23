"""One bounded editorial comparison per long-form highlight job."""

import json
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from itertools import zip_longest

from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .highlight_context import PROFILES, Chapter, nearby_context, profile_score
from .highlight_models import (
    WINDOW_SECONDS,
    Candidate,
    HighlightError,
    HighlightRequest,
    ProposedCandidate,
    ProposedHighlights,
)
from .highlight_provider import parse_highlight_response
from .highlight_selection import Segment, ground_candidates, merge_ranges, overlaps, window_text
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
Use the content profile to judge the opening, pacing, original contribution and resolution.
Auto profiles infer the genre per moment from source evidence. Preferred lengths are guidance
inside the hard duration bounds; reject incomplete arcs even if they otherwise score well.
Treat chapter links, acoustic measurements and sampled visual descriptions as partial evidence.
Assess pacing through actual progression. Never assume isolated changes in energy, pixels, or
speech rate mean the clip is engaging. Do not infer missing events between frames.
After that pass, compare the qualifying clips and remove alternate cuts of the same central beat.
Distinct moments can share a topic or participants. Keep each that resolves independently.
Use the same beat_id for alternate versions of the same story, punchline, exchange or payoff,
even when separated in time. Use a different beat_id for each independent moment, or null
when a relationship cannot be established. Return only the strongest complete version of a beat.
Mark eligible true only when the moment has the essential setup, a complete payoff or answer,
valid boundaries, and enough standalone value to publish. Omit clips with an unresolved question,
misleading cut, or unsupported key event, even when other dimensions score highly.
Use requested_candidates as a coverage target and max_candidates as the output limit. Keep all
distinct ready-to-use cuts up to max_candidates, ranked strongest first. A solid clip qualifies
on its own merits even if another is exceptional. Return fewer if the source has fewer qualifying
moments. Do not create filler, repeat a beat or stretch a cut to meet the target.
Use lead_first_segment and lead_last_segment as the starting cut when present. Keep their IDs if
the cut already works. When refining it, select inclusive IDs in that option and intersect the
original lead. Check last end minus first start, including pauses, against both duration bounds.
Use each option at most once. Give a specific title, concise reason and a short exact contiguous
quote from the selected range. Never invent speech, delivery, visual events or audience reactions.
An option with visual_anchor is a separate sampled-frame lead. Its source range is fixed.
For this option, use visual_anchor as both segment IDs and copy lead_evidence exactly as the
evidence field. The surrounding transcript is context. Its frame description is an AI observation
from the listed stills. Accept the lead only if the supplied evidence supports an independently
watchable, complete beat. Use the visual evidence label for opening_evidence and ending_evidence.
In quality_mode, rate hook, context, payoff and standalone from 0 to 5. A 3 means the dimension
works for a new viewer without added explanation or a guessed outcome. A 4 is strong, and a 5
is exceptional. A 2 or lower means a specific weakness remains after possible refinement.
Every dimension must meet 3. Quote exact text from the chosen first and last segments for opening
and ending evidence. Set score to 5 * (hook + context + payoff + standalone). Otherwise score
0-100 for ranking and quality may be null.
"""


class EditorialCandidate(ProposedCandidate):
    option: int = Field(ge=0, lt=MAX_OPTIONS, strict=True)
    beat_id: int | None = Field(default=None, ge=0, lt=MAX_OPTIONS, strict=True)
    eligible: bool = Field(default=True, strict=True)


class EditorialProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[EditorialCandidate] = Field(max_length=MAX_RESULTS)


@dataclass(frozen=True)
class ReviewOption:
    lead: Candidate
    segments: list[Segment]
    visual_anchor: int | None = None


def _coverage_order(candidates: list[Candidate], start: float) -> list[Candidate]:
    if len(candidates) <= 1:
        return candidates
    # Candidates arrive in score order. Cycle through time bands when spending the input budget.
    width = max(WINDOW_SECONDS, (max(lead.start for lead in candidates) - start) / 6)
    bands: dict[tuple[int, bool], list[Candidate]] = {}
    for lead in candidates:
        band = min(5, int((lead.start - start) // width))
        bands.setdefault((band, lead.visual is not None), []).append(lead)
    return [lead for group in zip_longest(*bands.values()) for lead in group if lead is not None]


def shortlist(
    candidates: list[Candidate],
    windows: list[list[Segment]],
    request: HighlightRequest,
    chapters: list[Chapter] | None = None,
    signals: list[dict] | None = None,
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
        # Allow room for a complete question or payoff, even across discovery windows.
        margin = CONTEXT_SECONDS
        if request.content_profile in {"interview", "education", "podcast", "comedy"}:
            margin = max(margin, min(75, request.max_seconds // 2))
        start, end = lead.start - margin, lead.end + margin
        for excluded in excluded_ranges:
            if excluded.end <= lead.start:
                start = max(start, excluded.end)
            if excluded.start >= lead.end:
                end = min(end, excluded.start)
        context_start, context_end = bisect_left(starts, start), bisect_right(starts, end)
        context = segments[context_start:context_end]
        lead_segments = segments[
            bisect_left(starts, lead.start, context_start, context_end) : bisect_left(
                starts, lead.end, context_start, context_end
            )
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
                {
                    "visual_anchor": segments[-1].id + 1 if segments else 0,
                    "sampled_frame_evidence": lead.visual.model_dump(),
                }
                if lead.visual
                else {}
            ),
            **(
                {"narrative_context": nearby_context(chapters, lead.start, lead.end)}
                if chapters
                else {}
            ),
            **(
                {
                    "lead_first_segment": lead_segments[0].id,
                    "lead_last_segment": lead_segments[-1].id,
                }
                if anchored and not lead.visual
                else {}
            ),
            "segments": json.loads(window_text(context)),
        }
        if signals:
            local = [cue for cue in signals if cue["start"] < end and cue["end"] > start]
            caps = {
                "narrative_note": 4,
                "ai_sampled_frames": 3,
                "audio_energy_peak": 2,
                "quiet_audio": 2,
                "possible_scene_cut": 2,
            }
            included = []
            for cue in local:
                kind = cue["kind"]
                if caps.get(kind, 0):
                    caps[kind] -= 1
                    included.append(cue)
            if included:
                option["source_signals"] = included
        size = len(json.dumps(option, ensure_ascii=False).encode()) + 2
        if size > budget:
            continue
        budget -= size
        seen.add(lead.fingerprint)
        options.append(ReviewOption(lead, context, option.get("visual_anchor")))
        payload.append(option)
    if candidates and not options:
        raise HighlightError(
            "No editorial lead fits the review input budget. Use a shorter duration."
        )
    return options, json.dumps(payload, ensure_ascii=False)


def ground_visual(
    proposal: EditorialCandidate,
    option: ReviewOption,
    request: HighlightRequest,
    duration: float,
    job_id: str,
    rejected: Counter[str],
) -> Candidate | None:
    lead = option.lead
    visual = lead.visual
    if (
        option.visual_anchor is None
        or proposal.first_segment != option.visual_anchor
        or proposal.last_segment != option.visual_anchor
    ):
        rejected["segment_ids"] += 1
        return None
    if (
        visual is None
        or proposal.evidence != lead.evidence
        or not lead.start <= visual.frame_times[0] < visual.frame_times[-1] <= lead.end
        or any(right <= left for left, right in zip(visual.frame_times, visual.frame_times[1:]))
    ):
        rejected["evidence"] += 1
        return None
    if (
        not request.analysis_start <= lead.start < lead.end <= (request.analysis_end or duration)
        or lead.end > duration
        or not request.min_seconds <= lead.end - lead.start <= request.max_seconds
    ):
        rejected["source_bounds"] += 1
        return None
    if overlaps(lead.start, lead.end, request.excluded_ranges):
        rejected["excluded_range"] += 1
        return None
    quality = proposal.quality
    if request.quality_mode and (
        quality is None
        or min(quality.hook, quality.context, quality.payoff, quality.standalone) < 3
    ):
        rejected["quality"] += 1
        return None
    if quality is not None:
        quality = quality.model_copy(
            update={
                "opening_evidence": lead.evidence,
                "ending_evidence": lead.evidence,
            }
        )
    return lead.model_copy(
        update={
            "job_id": job_id,
            "title": proposal.title,
            "reason": proposal.reason,
            "score": proposal.score,
            "category": proposal.category,
            "quality": quality,
        }
    )


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
    beats: dict[int, Candidate] = {}
    for candidate in proposal.candidates:
        if not candidate.eligible:
            rejected["editorial_gate"] += 1
            continue
        if candidate.option >= len(options):
            rejected["unknown_option"] += 1
            continue
        if candidate.option in used:
            rejected["reused_option"] += 1
            continue
        option = options[candidate.option]
        if option.lead.visual:
            clip = ground_visual(candidate, option, request, duration, job_id, rejected)
            if clip is None:
                continue
        else:
            grounded, invalid = ground_candidates(
                ProposedHighlights(candidates=[candidate]),
                option.segments,
                request,
                duration,
                job_id,
                rejection_reasons=rejected,
                derive_boundary_evidence=True,
            )
            if invalid:
                continue
            clip = grounded[0]
            if not (clip.start < option.lead.end and clip.end > option.lead.start):
                rejected["outside_original_lead"] += 1
                continue
        used.add(candidate.option)
        if clip.quality is not None and request.quality_mode:
            clip.score = profile_score(clip.quality, request.content_profile)
        if candidate.beat_id is None:
            accepted.append(clip)
        else:
            prior = beats.get(candidate.beat_id)
            if prior is not None:
                rejected["duplicate_beat"] += 1
            if prior is None or clip.score > prior.score:
                beats[candidate.beat_id] = clip
    accepted.extend(beats.values())
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
            for field in definition["properties"].values():
                field.pop("default", None)
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
                    "content_profile": request.content_profile,
                    "profile_guidance": PROFILES[request.content_profile].guidance,
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
