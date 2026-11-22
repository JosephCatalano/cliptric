"""Deterministic test double for project and lifecycle tests. Never sends requests."""

import json

from backend.editorial_review import EditorialProposal


class SyntheticEditorialReviewer:
    def __init__(self, settings=None):
        self.calls = 0

    async def review(self, payload, request, limit):
        self.calls += 1
        candidates = []
        for option in json.loads(payload)[:limit]:
            segments = [
                s
                for s in option["segments"]
                if s["start"] >= option["lead_start"] and s["end"] <= option["lead_end"]
            ]
            candidates.append(
                {
                    "option": option["option"],
                    "first_segment": segments[0]["id"],
                    "last_segment": segments[-1]["id"],
                    "score": 85,
                    "title": f"Synthetic editorial moment {segments[0]['id']}",
                    "reason": "Synthetic editorial selection.",
                    "evidence": option["lead_evidence"],
                    "category": "insight",
                    "quality": {
                        "hook": 4,
                        "context": 4,
                        "payoff": 4,
                        "standalone": 4,
                        "opening_evidence": segments[0]["text"][:120],
                        "ending_evidence": segments[-1]["text"][:120],
                    }
                    if request.quality_mode
                    else None,
                }
            )
        return EditorialProposal(candidates=candidates), 10, 5
