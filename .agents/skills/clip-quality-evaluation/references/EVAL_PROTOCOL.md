# Evaluation protocol and data formats

## Gold references

```json
{
  "videos": [
    {
      "id": "stream-001",
      "highlights": [
        {"start": 120.0, "end": 155.0, "note": "manual choice"}
      ]
    }
  ]
}
```

These represent independent human annotations. If two reviewers disagree, keep both judgments in the raw review sheet; adjudicate into a gold set while recording the disagreement.

## Predictions

```json
{
  "videos": [
    {
      "id": "stream-001",
      "clips": [
        {"start": 122.0, "end": 155.0, "score": 88}
      ]
    }
  ]
}
```

Each prediction matches at most one reference and each reference matches at most one prediction. Predictions are sorted by score when the value is present; otherwise retain file order. Only `top_k` are evaluated.

## Metrics

- `temporal_iou`: intersection seconds / union seconds of source ranges. It measures temporal agreement, not entertainment value.
- `precision_at_k`: matched predictions / number of predictions evaluated (up to `k`).
- `recall_at_k`: matched gold references / number of gold references.
- `editor_acceptance_rate`: human-accepted exported clips / exported clips reviewed.
- `useful_clips_per_source_hour`: count of human-approved distinct clips / total hours ingested.
- `edit_minutes_per_accepted_clip`: measured manual editing duration / human-approved clips.
- `caption_error_rate`: human-verified wrong words or timing problems / reviewed caption words or events. Report the chosen denominator.
- `render_success_rate`: verified playable exports / attempted renders.

Not all metrics are reported by the bundled evaluation script. The script reports temporal matching; collect human ratings and processing/cost telemetry separately.

## Interpretation cautions

- A `0.5` IoU threshold may penalize equally useful clips of different length. Supplement with human review.
- No ground-truth highlights means recall is undefined; the script refuses empty gold sets for clarity.
- If the score field is absent, file ordering is treated as rank.
- A result from one video is anecdotal. Use several varied source recordings and maintain a held-out set to avoid optimizing to one streamer.
- Compare to the same baseline and conditions. Do not use imagined view counts as evidence.

## Human review template

| video_id | clip_id | hook 1–5 | coherent 1–5 | crop 1–5 | captions 1–5 | approved Y/N | reason | edit_minutes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| stream-001 | clip-001 | 4 | 5 | 3 | 4 | Y | Strong payoff; HUD partly obscured | 2 |

Set minimum acceptance criteria before measuring and save comparison reports in version control (excluding copyrighted raw source videos).
