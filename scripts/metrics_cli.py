#!/usr/bin/env python3
"""CLI developer tool for inspecting Review Heatmap activity and workload calculations.

Evaluates synthetic daily totals using the add-on's actual functions without
launching Anki or building the add-on.

Usage examples:
    python scripts/metrics_cli.py --reviews 100 --minutes 10
    python scripts/metrics_cli.py --reviews 100 --time-ms 600000 --json
    python scripts/metrics_cli.py --reviews 100 --minutes 10 --reference-reviews 200 --reference-minutes 40 --json
"""

import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence

# 1. Load the existing calculation module directly without initializing Anki/Qt.
METRICS_PATH = Path(__file__).resolve().parents[1] / "src/review_heatmap/metrics.py"
spec = importlib.util.spec_from_file_location("_heatmap_cli_metrics", METRICS_PATH)
if spec is None or spec.loader is None:
    sys.stderr.write(f"Error: Unable to load metrics module at {METRICS_PATH}\n")
    sys.exit(2)
metrics = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = metrics
spec.loader.exec_module(metrics)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect Review Heatmap calculations for synthetic daily totals.",
        epilog=(
            "Usage examples:\n"
            "  python scripts/metrics_cli.py --reviews 100 --minutes 10\n"
            "  python scripts/metrics_cli.py --reviews 100 --time-ms 600000 --json\n"
            "  python scripts/metrics_cli.py --reviews 100 --minutes 10 --reference-reviews 200 --reference-minutes 40 --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--reviews",
        type=int,
        required=True,
        help="Required nonnegative integer: review events, not unique cards.",
    )
    time_group = parser.add_mutually_exclusive_group(required=True)
    time_group.add_argument(
        "--minutes",
        type=float,
        help="Total recorded duration in minutes (may be fractional).",
    )
    time_group.add_argument(
        "--time-ms",
        type=int,
        help="Total recorded duration in milliseconds (integer).",
    )
    metric_choices = ["all"] + list(metrics.METRICS.keys())
    parser.add_argument(
        "--metric",
        choices=metric_choices,
        default="all",
        help="Metric key to evaluate (default: all). Choices: " + ", ".join(metric_choices),
    )
    parser.add_argument(
        "--reference-reviews",
        type=int,
        default=None,
        help="Positive integer review count for synthetic reference day.",
    )
    parser.add_argument(
        "--reference-minutes",
        type=float,
        default=None,
        help="Positive duration in minutes for synthetic reference day.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print one JSON object for LLMs/scripts; otherwise print a compact readable table.",
    )
    return parser


def parse_and_validate(parser: argparse.ArgumentParser, argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    args = parser.parse_args(argv)

    if args.reviews < 0:
        parser.error("--reviews must be a nonnegative integer.")

    if args.minutes is not None:
        if not math.isfinite(args.minutes) or args.minutes < 0:
            parser.error("--minutes must be a finite nonnegative number.")
    if args.time_ms is not None:
        if args.time_ms < 0:
            parser.error("--time-ms must be a nonnegative integer.")

    has_ref_rev = args.reference_reviews is not None
    has_ref_min = args.reference_minutes is not None
    if has_ref_rev != has_ref_min:
        parser.error("Both --reference-reviews and --reference-minutes must be provided together.")

    if has_ref_rev and has_ref_min:
        if args.reference_reviews <= 0:
            parser.error("--reference-reviews must be a positive integer.")
        if not math.isfinite(args.reference_minutes) or args.reference_minutes <= 0:
            parser.error("--reference-minutes must be a finite positive number.")
        ref_time_ms = round(args.reference_minutes * 60000)
        if ref_time_ms <= 0:
            parser.error(
                "--reference-minutes is too small and rounds to 0 milliseconds; "
                "cannot supply a valid workload reference."
            )

    return args


def evaluate(
    reviews: int,
    time_ms: int,
    minutes: float,
    metric_key: str = "all",
    ref_reviews: Optional[int] = None,
    ref_time_ms: Optional[int] = None,
    ref_minutes: Optional[float] = None,
) -> Dict[str, Any]:
    has_reference = ref_reviews is not None and ref_time_ms is not None and ref_minutes is not None

    if metric_key == "all":
        keys = list(metrics.METRICS.keys())
    else:
        keys = [metric_key]

    results: Dict[str, Any] = {}
    for key in keys:
        label = metrics.METRICS[key]["label"]
        score = metrics.activity_value(reviews, time_ms, key)
        if not math.isfinite(score):
            raise ValueError(f"Nonfinite score produced for metric {key}: {score}")

        if key == "reviews":
            results[key] = {
                "label": label,
                "score": score,
                "fixed_thresholds": None,
                "reference_score": None,
                "target_score": None,
                "ratio": None,
                "percent": None,
                "color": None,
                "palette": None,
            }
        else:
            fixed_thresholds = list(metrics.activity_levels(key))
            if has_reference:
                ref = metrics.reference_from_day((0, ref_reviews, ref_time_ms), key, "cli")
                if ref is None:
                    raise ValueError(f"Unable to create reference for metric {key}")
                ref_score = float(ref["value"])
                target_score = float(metrics.baseline_value(ref))
                if target_score <= 0 or not math.isfinite(target_score):
                    raise ValueError(f"Invalid target score for metric {key}: {target_score}")
                ratio = score / target_score
                percent = 100.0 * ratio
                if not math.isfinite(ratio) or not math.isfinite(percent):
                    raise ValueError(f"Nonfinite ratio/percent for metric {key}")

                if reviews > 0:
                    color = metrics.baseline_color(score, ref, reference_day=False)
                else:
                    color = None

                results[key] = {
                    "label": label,
                    "score": score,
                    "fixed_thresholds": fixed_thresholds,
                    "reference_score": ref_score,
                    "target_score": target_score,
                    "ratio": ratio,
                    "percent": percent,
                    "color": color,
                    "palette": "bundled_default",
                }
            else:
                results[key] = {
                    "label": label,
                    "score": score,
                    "fixed_thresholds": fixed_thresholds,
                    "reference_score": None,
                    "target_score": None,
                    "ratio": None,
                    "percent": None,
                    "color": None,
                    "palette": None,
                }

    data: Dict[str, Any] = {
        "schema_version": 1,
        "metric_source_path": str(METRICS_PATH),
        "inputs": {
            "reviews": reviews,
            "minutes": minutes,
            "milliseconds": time_ms,
        },
        "reference": {
            "reviews": ref_reviews,
            "minutes": ref_minutes,
            "milliseconds": ref_time_ms,
        } if has_reference else None,
        "palette": "bundled_default" if has_reference else None,
        "results": results,
    }
    return data


def _format_num(val: Optional[float], decimals: int = 2) -> str:
    if val is None:
        return "-"
    if isinstance(val, int) or val.is_integer():
        return str(int(val))
    return f"{val:.{decimals}f}"


def format_table(data: Dict[str, Any]) -> str:
    lines: List[str] = []
    inp = data["inputs"]
    ref = data["reference"]

    lines.append("Review Heatmap Metric Calculations")
    lines.append("=" * 35)
    lines.append(f"Inputs:    {inp['reviews']} reviews, {inp['minutes']} min ({inp['milliseconds']:,} ms)")
    if ref:
        lines.append(
            f"Reference: {ref['reviews']} reviews, {ref['minutes']} min ({ref['milliseconds']:,} ms) "
            f"[palette: {data.get('palette')}]"
        )
    lines.append("")

    results = data["results"]
    if ref:
        headers = ["Metric", "Score", "Ref Score", "Target (85%)", "Ratio", "Percent", "Color"]
        rows: List[List[str]] = []
        for res in results.values():
            label = res["label"]
            score_str = _format_num(res["score"], 2)
            ref_str = _format_num(res["reference_score"], 2)
            target_str = _format_num(res["target_score"], 2)
            ratio_str = f"{res['ratio']:.4f}" if res["ratio"] is not None else "-"
            pct_str = f"{res['percent']:.1f}%" if res["percent"] is not None else "-"
            color_str = str(res["color"]) if res["color"] is not None else "-"
            rows.append([label, score_str, ref_str, target_str, ratio_str, pct_str, color_str])

        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, val in enumerate(row):
                col_widths[i] = max(col_widths[i], len(val))

        header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
        separator_line = "  ".join("-" * col_widths[i] for i in range(len(headers)))
        lines.append(header_line)
        lines.append(separator_line)
        for row in rows:
            lines.append("  ".join(val.ljust(col_widths[i]) for i, val in enumerate(row)))

        threshold_lines = []
        for res in results.values():
            if res["fixed_thresholds"]:
                thresh_str = ", ".join(str(x) for x in res["fixed_thresholds"])
                threshold_lines.append(f"  {res['label']}: [{thresh_str}]")
        if threshold_lines:
            lines.append("")
            lines.append("Fixed scale thresholds:")
            lines.extend(threshold_lines)
    else:
        headers = ["Metric", "Score", "Fixed Thresholds"]
        rows = []
        for res in results.values():
            label = res["label"]
            score_str = _format_num(res["score"], 2)
            thresh_str = ", ".join(str(x) for x in res["fixed_thresholds"]) if res["fixed_thresholds"] else "-"
            rows.append([label, score_str, thresh_str])

        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, val in enumerate(row):
                col_widths[i] = max(col_widths[i], len(val))

        header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
        separator_line = "  ".join("-" * col_widths[i] for i in range(len(headers)))
        lines.append(header_line)
        lines.append(separator_line)
        for row in rows:
            lines.append("  ".join(val.ljust(col_widths[i]) for i, val in enumerate(row)))

    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = create_parser()
    args = parse_and_validate(parser, argv)

    if args.minutes is not None:
        time_ms = round(args.minutes * 60000)
        minutes = args.minutes
    else:
        time_ms = args.time_ms
        minutes = time_ms / 60000

    if args.reference_reviews is not None and args.reference_minutes is not None:
        ref_reviews = args.reference_reviews
        ref_minutes = args.reference_minutes
        ref_time_ms = round(ref_minutes * 60000)
    else:
        ref_reviews = None
        ref_minutes = None
        ref_time_ms = None

    try:
        data = evaluate(
            reviews=args.reviews,
            time_ms=time_ms,
            minutes=minutes,
            metric_key=args.metric,
            ref_reviews=ref_reviews,
            ref_time_ms=ref_time_ms,
            ref_minutes=ref_minutes,
        )
    except (ValueError, OverflowError) as exc:
        sys.stderr.write(f"Error during calculation: {exc}\n")
        return 2

    if args.json:
        # Strict JSON output: reject NaN / Infinity, no extra stdout
        print(json.dumps(data, indent=2, allow_nan=False))
    else:
        print(format_table(data))

    return 0


if __name__ == "__main__":
    sys.exit(main())
