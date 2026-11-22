# Canonical transcript and candidate JSON contracts

## Transcript (seconds in the original source)

```json
{
  "video_id": "stream-001",
  "duration_seconds": 3600,
  "segments": [
    {"id": "s1", "start": 0.0, "end": 4.5, "text": "I thought the race was over."},
    {"id": "s2", "start": 4.5, "end": 9.2, "text": "Then the car flew into the river!"}
  ]
}
```

Segment IDs are stable, and segments are ordered by start. Small diarization overlaps are allowed, but the overall source clock must never be reset across chunks. Segment text is evidence of speech only, never evidence of a visual event.

## Candidate output

```json
{
  "video_id": "stream-001",
  "candidates": [
    {
      "id": "clip-001",
      "start": 0.0,
      "end": 9.2,
      "score": 76,
      "category": "unexpected-outcome",
      "title": "A race goes very wrong",
      "reason": "The setup and surprise are understandable without the full stream.",
      "evidence": "Then the car flew into the river!"
    }
  ]
}
```

For real clips, try 15–60 seconds when the context requires that length. The example above is deliberately shorter to demonstrate the schema; validator defaults allow 8–120 seconds.

## Prompt skeleton for a text model

> Analyze the following timestamped, source-relative transcript segments. Select zero or more independently watchable moments. Do not invent dialogue, visuals, or evidence. Return JSON matching the candidate schema with each time boundary supported by segment timestamps. Quote a short exact excerpt from the transcript in `evidence`. Score 0–100 as a **relative ranking heuristic**, not a probability. Prefer moments with a strong hook, context, and payoff. Include empty `candidates` if nothing compelling exists. Distinguish transcript-based inference from confirmed visual observations.

Validate the result in code. Treat LLM JSON as untrusted data. If structured output/parsing fails, retry or mark failure rather than creating fictional clips.
