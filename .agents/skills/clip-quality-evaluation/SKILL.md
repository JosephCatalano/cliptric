---
name: clip-quality-evaluation
description: Benchmark AI-generated short clips against human-identified highlights and competing clipping workflows. Use for temporal IoU evaluation, precision@K and recall@K, editor acceptance, caption timing and readability QA, rendering QA, cost/performance analysis, A/B comparisons, and regression tracking. Do not use for generating highlight candidates or asserting real-world virality.
---

# Evaluation for AI short-form clipping

## Purpose

Measure whether AI **selects and produces clips people would actually post**, not merely whether the system can output five MP4 files. Use when testing model changes, adjusting clip thresholds, comparing with OpusClip/Vizard, investigating bad results, or deciding whether a milestone is complete.

## Principles

- Create a small, diverse *held-out* evaluation set from recordings you are permitted to process: talk-heavy stream, gameplay-heavy stream, podcast, and mixed facecam/gameplay where relevant.
- Have a human editor mark good moments using source-relative `start`/`end` seconds **without seeing AI suggestions first** where possible. Record ambiguous cases.
- Keep the same source recordings, clip-budget (`top_k`), target durations and review criteria across systems.
- Never represent `score` as a calibrated viral probability. Views, CTR, completion, shares and watch time require actual platform results and controlled comparisons.
- Keep factual reliability: don't count a candidate as a match merely because its title sounds similar. Require interval overlap and meaningful narrative match.

## Workflow

1. Create `references.json` with approved highlight ranges and `predictions.json` with generated clip ranges using [evaluation protocol and schemas](references/EVAL_PROTOCOL.md).
2. Evaluate temporal matching using `scripts/evaluate.py`. Default `top_k=5`, `min_iou=0.5`; treat these as initial settings that may need adjustment for differing clip lengths.
3. Separately manually rate each generated export for hook/payoff, standalone usefulness, correctness, audio/video sync, caption quality, and crop/layout. Temporal matching alone cannot assess clip quality.
4. Log `accepted / reviewed`, total useful clips per source-hour, edit minutes per accepted clip, end-to-end elapsed time, GPU/CPU peak use when measured, API cost, and storage cost.
5. Inspect failures: wrong timestamps, missing silent visual moments, repeated duplicates, incoherent cuts, obscured HUD, word drift, and excessive processing time.
6. Compare a changed pipeline with its previous version on the *same* held-out evaluation set. Explicitly state sample sizes, uncertainty, and any scoring subjectivity.

## Command

```powershell
python .github/skills/clip-quality-evaluation/scripts/evaluate.py --references .github/skills/clip-quality-evaluation/assets/references.json --predictions .github/skills/clip-quality-evaluation/assets/predictions.json --top-k 5 --min-iou 0.5
```

## MVP acceptance gate (set in advance)

- At least one actual source recording passes end-to-end extraction, rendering, and caption visibility tests.
- No invalid/fictional timestamps; export plays; recorded source segments match what was selected.
- At least five clips produced **if five valid candidates exist**; output count by itself is not a quality guarantee.
- Human editor explicitly rates selection quality and documents unusable clips.
- Report measured processing duration and cost; never invent benchmarks.

Read [evaluation protocol](references/EVAL_PROTOCOL.md) for definitions, thresholds, and caveats.
