# Human-interpretable highlight ranking rubric

A candidate's score is a rank, not a viral success probability. Human calibration on your audience comes later.

| Feature | Points | Strong signal |
| --- | ---: | --- |
| Opening hook | 0–20 | Interesting in the first 1–3 seconds without a long explanation |
| Payoff or development | 0–25 | Clear escalation, twist, punchline, or outcome |
| Standalone context | 0–20 | Understandable outside the original full stream |
| Emotional / reaction intensity | 0–15 | Genuine amusement, surprise, tension, or energy (source evidenced) |
| Novelty and specificity | 0–10 | Not generic filler or a common repeated line |
| Audience fit | 0–10 | Relevant to the specified niche and platform |

Total is 0–100. Avoid arbitrary precision: scores are intended to sort candidates, not make certainty claims. Compare selected clips to editor judgments and periodically recalibrate the scoring rubric.

## Special treatment for streams

- Spoken comedy: rank as a transcript-grounded candidate, then watch to verify delivery.
- Gameplay stunt with silence: requires a verified visual/audio cue before selection.
- Facecam reaction: preserve both expression and gameplay if needed for comprehension.
- Transcript spanning two windows: preserve all source timestamps and merge the same beat after overlap deduplication.
- Clip without payoff: score lower even if its introduction sounds interesting.
- A moment with no good hook should be eligible for manual review, not auto-published.

## Deduplication

Two clips are likely duplicates if they have high overlap and narrate the same beat. IoU = intersection duration / union duration. Test >= 0.65 as an initial threshold but inspect narrative similarity; do not blindly delete nearby, distinct moments.
