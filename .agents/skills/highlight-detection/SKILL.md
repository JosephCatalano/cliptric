---
name: highlight-detection
description: Discover and rank engaging source-grounded highlights in hour-long gaming streams, Twitch VODs, podcasts, debates, and interviews. Use for transcript chunking, LLM scoring prompts, timestamp validation, duplicate removal, clip candidate generation, and improving highlight recall. Do not use for rendering files or claiming a score predicts virality.
---

# Highlight detection for long-form video

## Scope and activation

Use this skill when selecting **what to clip**. Prefer high recall in candidate generation followed by explicit ranking and human review. Transcript-only analysis is a baseline: it misses silent GTA stunts, visual punchlines, sound-only reactions, and gameplay context. Never infer a visual event from transcript text.

## Inputs and output

- Input: `video_id`, validated `duration_seconds`, and ordered transcript `segments` with source-relative `start`, `end`, and `text`.
- Optional later-stage inputs: diarized speaker turns, audio events, scene boundaries, user-provided event markers, visual descriptions from sampled frames (with provenance).
- Output: JSON object with `video_id` and `candidates`, each with `id`, `start`, `end`, `score` (0–100 heuristic rank), `title`, `reason`, `evidence` (verbatim short excerpt), and `category`.
- **Every candidate must be traceable to real source intervals**. LLMs may suggest candidates but cannot be trusted to invent or repair timestamps.

See [contract and prompting](references/CONTRACT.md) and [scoring rubric](references/SCORING_RUBRIC.md).

## Recommended workflow

1. Validate transcript ordering and source duration. Preserve real times; never compress silence out of the timeline when using timestamps.
2. Generate **overlapping windows** using `scripts/build_windows.py` (e.g. 180 seconds with 30 seconds overlap). Preserve segment IDs and absolute time. For windows covering boundaries, include full overlapping segments.
3. Ask an LLM for structured candidate ranges, compelling exact excerpts, category, short hook/title, and evidence for why each moment can stand alone. Tell it to return `[]` if the source has no compelling moments; never force exactly five.
4. Check each range and evidence against the actual transcript. Run `scripts/validate_candidates.py` and reject unsupported/malformed candidates. If a candidate's exact hook is real but timing is off, correct using segment/word timestamps and verify again.
5. Merge duplicate and heavily overlapping candidates with the same narrative beat. Preserve distinct moments even when near each other; avoid redundant clips from overlapping analysis windows.
6. Rank using the rubric: coherent setup/payoff, initial hook, novelty, emotional energy, standalone comprehensibility, and target audience relevance. `score` is a **ranking heuristic**, not a calibrated probability of going viral.
7. Review output in the player against actual footage. Save rejected examples with reasons, and compare against manual highlights through clip-quality-evaluation.
8. For gaming, roadmap multimodal signals (audio peaks/laughter, rapid reaction, OCR/HUD, facecam, scene motion) as independent evidence. Never label them as implemented until tested.

## Commands

```powershell
python .github/skills/highlight-detection/scripts/build_windows.py --input .github/skills/highlight-detection/assets/transcript.json --output windows.json --window-seconds 180 --overlap-seconds 30
python .github/skills/highlight-detection/scripts/validate_candidates.py --input .github/skills/highlight-detection/assets/candidates.json --duration 120 --transcript .github/skills/highlight-detection/assets/transcript.json
```

## Guardrails

- Avoid promoting violence, harassment, slurs, or misinformation simply because those moments trigger engagement; support appropriate content moderation.
- Avoid hallucinated punchlines, invented quotes, fake audience reactions, and using unverified engagement claims such as "95% chance of viral".
- Never publish third-party clips without considering content permissions and platform rules.
- Real success metrics: human usefulness ratings, editor acceptance rate, temporal match with gold highlights, amount of manual correction, and throughput.

## Definition of done

- Every selected candidate has valid, source-relative boundaries and a corresponding real transcript passage (or explicitly marked visual/audio evidence from another detector).
- Candidates are unique enough to be meaningfully different, are ordered by score, and include interpretable reasons.
- At least one real sample is visually reviewed, not only syntactically validated.
