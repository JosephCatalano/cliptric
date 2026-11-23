#!/usr/bin/env python3
"""Evaluate source-relative temporal matches of top-K clips to human highlights."""

import argparse
import json
import math
import pathlib
import sys


def check_interval(obj, context):
    start, end = obj.get("start"), obj.get("end")
    if any(
        isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x)
        for x in (start, end)
    ):
        raise ValueError(f"{context}: start/end must be finite numbers")
    if not 0 <= start < end:
        raise ValueError(f"{context}: need 0 <= start < end")
    return (float(start), float(end))


def iou(a, b):
    intersection = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return intersection / union if union else 0


def to_map(doc, field):
    if not isinstance(doc.get("videos"), list):
        raise ValueError("Input must contain a 'videos' array")
    result = {}
    for video in doc["videos"]:
        video_id = video.get("id")
        if not isinstance(video_id, str) or not video_id.strip() or video_id in result:
            raise ValueError(f"Missing or duplicate video id: {video_id}")
        entries = video.get(field)
        if not isinstance(entries, list):
            raise ValueError(f"{video_id}: '{field}' must be an array")
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                raise ValueError(f"{video_id}/{index} must be an object")
            if field == "highlights":
                check_interval(item, f"{video_id}/{index}")
        result[video_id] = entries
    return result


def number(value, name, *, positive=False):
    if (
        type(value) not in {int, float}
        or not math.isfinite(value)
        or value < 0
        or (positive and value == 0)
    ):
        raise ValueError(
            f"{name} must be a finite {'positive' if positive else 'nonnegative'} number"
        )
    return value


def run_metadata(doc):
    result = {}
    for row in doc["videos"]:
        result[row["id"]] = {
            key: number(row[key], key, positive=key == "source_seconds")
            for key in ("source_seconds", "processing_seconds", "estimated_cost_usd")
            if row.get(key) is not None
        }
    return result


def evaluate(golds, predictions, top_k, threshold, metadata=None):
    metadata = metadata or {}
    all_results = []
    total_preds = total_golds = total_matches = 0
    start_errors, end_errors = [], []
    invalid = 0
    review_flags = {
        "duplicate": [],
        "missing_setup": [],
        "missing_payoff": [],
        "standalone": [],
        "approved": [],
    }
    for video_id, truth in golds.items():
        if video_id not in predictions:
            raise ValueError(f"No predictions entry for reference video {video_id}")
        available = predictions[video_id]
        if any("score" in p for p in available):
            if not all(
                isinstance(p.get("score"), (int, float))
                and not isinstance(p.get("score"), bool)
                and math.isfinite(p["score"])
                for p in available
            ):
                raise ValueError(
                    f"{video_id}: if scoring is used, every prediction needs a finite numeric score"
                )
            available = sorted(available, key=lambda p: p["score"], reverse=True)
        selected = available[:top_k]
        source_duration = metadata.get(video_id, {}).get("source_seconds")
        if source_duration is not None and any(gold["end"] > source_duration for gold in truth):
            raise ValueError(f"{video_id}: a human reference is outside the source duration")
        used = set()
        matches = []
        for rank, p in enumerate(selected, start=1):
            review = p.get("review", {})
            if not isinstance(review, dict):
                raise ValueError(f"{video_id}/prediction-{rank}: review must be an object")
            for flag, results in review_flags.items():
                if flag in review:
                    if type(review[flag]) is not bool:
                        raise ValueError(f"{video_id}/prediction-{rank}: {flag} must be a boolean")
                    results.append(review[flag])
            try:
                candidate_range = check_interval(p, f"{video_id}/prediction-{rank}")
                if source_duration is not None and candidate_range[1] > source_duration:
                    raise ValueError("Clip exceeds the source duration.")
            except ValueError:
                invalid += 1
                continue
            options = [
                (iou(candidate_range, check_interval(t, f"{video_id}/reference-{i}")), i)
                for i, t in enumerate(truth)
                if i not in used
            ]
            best_iou, best_index = max(options, default=(0.0, -1))
            if best_iou >= threshold:
                used.add(best_index)
                reference = check_interval(truth[best_index], f"{video_id}/reference-{best_index}")
                start_error = abs(candidate_range[0] - reference[0])
                end_error = abs(candidate_range[1] - reference[1])
                start_errors.append(start_error)
                end_errors.append(end_error)
                matches.append(
                    {
                        "prediction_rank": rank,
                        "reference_index": best_index,
                        "temporal_iou": round(best_iou, 4),
                        "start_error_seconds": round(start_error, 3),
                        "end_error_seconds": round(end_error, 3),
                    }
                )
        m, n, g = len(matches), len(selected), len(truth)
        total_matches += m
        total_preds += n
        total_golds += g
        all_results.append(
            {
                "video_id": video_id,
                "evaluated_predictions": n,
                "reference_highlights": g,
                "matches": matches,
                "matched_count": m,
                "precision_at_k": round(m / n, 4) if n else 0.0,
                "recall_at_k": round(m / g, 4) if g else None,
            }
        )
    weak = [result for result in all_results if result["reference_highlights"] == 0]
    timed_runs = [
        metadata[video_id]["processing_seconds"]
        for video_id in golds
        if "processing_seconds" in metadata.get(video_id, {})
    ]
    priced_runs = [
        metadata[video_id]
        for video_id in golds
        if {"source_seconds", "estimated_cost_usd"} <= metadata.get(video_id, {}).keys()
    ]
    return {
        "top_k": top_k,
        "min_iou": threshold,
        "videos_evaluated": len(golds),
        "micro_precision_at_k": round(total_matches / total_preds, 4) if total_preds else 0.0,
        "micro_recall_at_k": round(total_matches / total_golds, 4) if total_golds else None,
        "mean_matched_start_error_seconds": round(sum(start_errors) / len(start_errors), 3)
        if start_errors
        else None,
        "mean_matched_end_error_seconds": round(sum(end_errors) / len(end_errors), 3)
        if end_errors
        else None,
        "videos_without_reference_highlights": len(weak),
        "videos_without_reference_highlights_with_predictions": sum(
            result["evaluated_predictions"] > 0 for result in weak
        ),
        "total_matches": total_matches,
        "total_predictions_evaluated": total_preds,
        "invalid_boundaries": invalid,
        "invalid_boundary_rate": round(invalid / total_preds, 4) if total_preds else None,
        "human_review_rates": {
            flag: {
                "count": len(values),
                "rate": round(sum(values) / len(values), 4) if values else None,
            }
            for flag, values in review_flags.items()
        },
        "mean_processing_seconds": round(sum(timed_runs) / len(timed_runs), 3)
        if timed_runs
        else None,
        "timed_runs": len(timed_runs),
        "estimated_usd_per_source_hour": round(
            3600
            * sum(run["estimated_cost_usd"] for run in priced_runs)
            / sum(run["source_seconds"] for run in priced_runs),
            4,
        )
        if priced_runs
        else None,
        "priced_runs": len(priced_runs),
        "total_reference_highlights": total_golds,
        "per_video": all_results,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Compare top-K predicted intervals to human gold highlights"
    )
    p.add_argument("--references", required=True, type=pathlib.Path)
    p.add_argument("--predictions", required=True, type=pathlib.Path)
    p.add_argument(
        "--baseline", type=pathlib.Path, help="Optional earlier predictions for the same sources"
    )
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--min-iou", type=float, default=0.5)
    p.add_argument("--output", type=pathlib.Path, help="Optional JSON report output")
    args = p.parse_args()
    try:
        if args.top_k < 1 or not math.isfinite(args.min_iou) or not 0 < args.min_iou <= 1:
            raise ValueError("Require top-k >= 1 and 0 < min-iou <= 1")
        golds = to_map(json.loads(args.references.read_text(encoding="utf-8")), "highlights")
        predictions_doc = json.loads(args.predictions.read_text(encoding="utf-8"))
        predictions = to_map(predictions_doc, "clips")
        if not golds:
            raise ValueError("No reference videos supplied")
        unexpected = set(predictions) - set(golds)
        if unexpected:
            print(
                f"Warning: ignoring predictions for videos without references: {', '.join(sorted(unexpected))}",
                file=sys.stderr,
            )
        report = evaluate(
            golds, predictions, args.top_k, args.min_iou, run_metadata(predictions_doc)
        )
        if args.baseline:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            report["baseline"] = evaluate(
                golds, to_map(baseline, "clips"), args.top_k, args.min_iou, run_metadata(baseline)
            )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
