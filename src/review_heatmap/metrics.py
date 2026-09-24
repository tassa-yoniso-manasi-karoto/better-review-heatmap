"""Color measures and reference selection, independent of Anki and Qt.

See LICENSE for the add-on's license and additional terms.
"""

import json
import math
from colorsys import hls_to_rgb
from pathlib import Path
from statistics import median
from typing import Dict, Iterable, List, Optional, Sequence


METRICS = {
    "reviews": {"label": "Review count (classic)", "time_weight": 0.0},
    "time": {"label": "Workload (linear)", "time_weight": 0.5},
    "workload": {"label": "Workload (review-weighted)", "time_weight": 0.4},
    "custom": {"label": "Workload (custom)", "time_weight": None},
    "recorded_time": {"label": "Recorded time", "time_weight": 1.0},
}
SCALES = {
    "adaptive": {"label": "Adaptive"},
    "baseline": {"label": "Automatic baseline"},
}

FORMULA_VERSION = 3
DEFAULT_CUSTOM_TIME_WEIGHT = 0.5
BASELINE_FRACTION = 0.85
ABOVE_BASELINE_FACTORS = (1.25, 1.5, 2.0, 3.0)
MIN_REFERENCE_DAYS = 7
AUTO_REFERENCE_PERCENTILE = 90
AUTO_REFERENCE_REFRESH_DAYS = 30
DEFAULT_BASELINE_GRADIENT = json.loads(
    Path(__file__).with_name("config.json").read_text(encoding="utf-8")
)["baseline_gradient_default"]


def metric_name(conf: Dict) -> str:
    value = conf.get("activity_metric", "workload")
    return value if value in METRICS else "workload"


def metric_weights(metric: str, conf: Optional[Dict] = None):
    """One stored custom exponent makes the complementary pair sum to one."""
    weight = METRICS[metric]["time_weight"]
    if weight is None:
        weight = (conf or {}).get("custom_time_weight", DEFAULT_CUSTOM_TIME_WEIGHT)
    weight = float(weight)
    if not math.isfinite(weight) or not 0 <= weight <= 1:
        raise ValueError("The time exponent must be between 0 and 1")
    return 1 - weight, weight


def activity_value(reviews: int, milliseconds: int, metric: str,
                   duration_counts=None, conf: Optional[Dict] = None) -> float:
    """Sum per-answer credit using (milliseconds, count) duration buckets.

    Collection callers must supply actual duration buckets. Without them this
    evaluates a synthetic equal-duration scenario, used by the totals-only CLI.
    The uniform assumption is never used to migrate an actual saved reference.
    """
    reviews = max(0, reviews)
    minutes = max(0, milliseconds) / 60000
    _, time_weight = metric_weights(metric, conf)
    if time_weight == 0:
        return float(reviews)
    if time_weight == 1:
        return minutes
    if duration_counts is None:
        return reviews * (minutes / reviews) ** time_weight if reviews else 0.0
    return math.fsum(
        count * (max(0, duration) / 60000) ** time_weight
        for duration, count in duration_counts
    )


def reference_scope(deck_id: Optional[int] = None) -> str:
    return "global" if deck_id is None else f"deck:{int(deck_id)}"


def baseline_key(conf: Dict, deck_id: Optional[int] = None) -> str:
    """Keep global keys compatible; decks have independent references."""
    metric = metric_name(conf)
    parts = [
        1 if metric == "reviews" else FORMULA_VERSION,
        metric,
        sorted(conf.get("limdecks", [])),
        conf.get("limcdel", False),
        conf.get("limresched", True),
        conf.get("limdate", 0),
        conf.get("limhist", 0),
    ]
    if metric == "custom":
        parts.append(metric_weights(metric, conf)[1])
    if deck_id is not None:
        parts.append(["deck", int(deck_id)])
    return json.dumps(parts, separators=(",", ":"))


def legacy_reference(conf: Dict, deck_id: Optional[int] = None) -> Optional[Dict]:
    """Find the active measure/filter snapshot without altering older versions."""
    references = conf.get("activity_baselines", {})
    if not isinstance(references, dict):
        return None
    pending = references.get(baseline_key(conf, deck_id))
    if isinstance(pending, dict) and pending.get("needs_durations"):
        return pending
    if metric_name(conf) not in ("time", "workload"):
        return None
    parts = json.loads(baseline_key(conf, deck_id))
    for version in (2, 1):
        parts[0] = version
        reference = references.get(json.dumps(parts, separators=(",", ":")))
        if isinstance(reference, dict):
            return reference
    return None


def migrate_activity_references(conf: Dict, read_day=None,
                                deck_id: Optional[int] = None) -> bool:
    """Upgrade the active snapshot using real durations, never inferred ones.

    A caller with an ActivityReporter supplies read_day. Other measures/filters
    migrate when used; original snapshots and existing v3 snapshots are retained.
    """
    if read_day is None or saved_reference(conf, deck_id) is not None:
        return False
    reference = legacy_reference(conf, deck_id)
    if reference is None:
        return False
    try:
        day = int(reference["day"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    rows = read_day(day)
    for row in rows:
        if row[0] != day or len(row) < 4:
            continue  # count and total time cannot reconstruct individual durations
        try:
            updated = reference_from_day(
                row, metric_name(conf), reference.get("source", "selected"), conf,
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if updated is not None:
            migrated = dict(reference, **updated)
            migrated.pop("needs_durations", None)
            conf["activity_baselines"][baseline_key(conf, deck_id)] = migrated
            return True
    return False


def saved_reference(conf: Dict, deck_id: Optional[int] = None) -> Optional[Dict]:
    references = conf.get("activity_baselines", {})
    if not isinstance(references, dict):
        return None
    reference = references.get(baseline_key(conf, deck_id))
    if not isinstance(reference, dict) or reference.get("needs_durations"):
        return None
    try:
        value = float(reference["value"])
        int(reference["day"])
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    return reference if math.isfinite(value) and value > 0 else None


def reference_from_day(row: Sequence, metric: str, source: str,
                       conf: Optional[Dict] = None) -> Optional[Dict]:
    day, reviews, milliseconds = row[:3]
    durations = row[3] if len(row) > 3 else None
    value = activity_value(reviews, milliseconds, metric, durations, conf)
    if not math.isfinite(value) or value <= 0:
        return None
    return {
        "day": day,
        "reviews": reviews,
        "time_ms": milliseconds,
        "value": value,
        "source": source,
    }


def automatic_reference(rows: Sequence[Sequence], metric: str,
                        conf: Optional[Dict] = None, *,
                        today: Optional[int] = None) -> Optional[Dict]:
    candidates = [reference_from_day(row, metric, "automatic", conf) for row in rows]
    candidates = sorted(
        (ref for ref in candidates if ref is not None),
        key=lambda ref: (ref["value"], ref["day"]),
    )
    if len(candidates) < MIN_REFERENCE_DAYS:
        return None
    # Nearest-rank P90 selects an actual, completed study day.
    reference = candidates[math.ceil(AUTO_REFERENCE_PERCENTILE / 100 * len(candidates)) - 1]
    reference = dict(reference, percentile=AUTO_REFERENCE_PERCENTILE)
    if today is not None:
        reference["selected_on"] = today
    return reference


def show_reference_reminder(conf: Dict, deck_id: Optional[int] = None) -> bool:
    reference = saved_reference(conf, deck_id)
    dismissed = conf.get("activity_reference_reminders_dismissed", {})
    return (
        metric_name(conf) != "reviews"
        and conf.get("activity_scale") == "baseline"
        and reference is not None and reference.get("source") == "automatic"
        and not dismissed.get(reference_scope(deck_id), False)
    )


def adaptive_anchor(scores: Iterable[float]) -> Optional[float]:
    """Median score of active days supplied by the caller's history range.

    Empty days and forecasts are excluded by the caller. This selects a color
    benchmark only; neither recorded durations nor daily scores are modified.
    """
    values = list(scores)
    if not values:
        return None
    anchor = float(median(values))
    return anchor if math.isfinite(anchor) and anchor > 0 else None


def activity_levels() -> List[float]:
    """Calendar palette levels; actual scores are colored against an anchor."""
    return list(range(1, 10))


def baseline_value(reference: Dict) -> float:
    """Use a gentler target without repeatedly discounting saved references."""
    return BASELINE_FRACTION * float(reference["value"])


def gradient_stops(gradient: Optional[Dict], side: str):
    """Read editable HSL points; malformed settings fall back to defaults."""
    try:
        points = (gradient or DEFAULT_BASELINE_GRADIENT)[side]
        if len(points) < 2:
            raise ValueError("A gradient needs at least two points")
        stops = []
        for point in points:
            ratio = float(point["workload_ratio"])
            h, s, l = map(float, point["hsl"])
            if not all(math.isfinite(v) for v in (ratio, h, s, l)):
                raise ValueError("Gradient values must be finite")
            if not (0 <= h <= 360 and 0 <= s <= 100 and 0 <= l <= 100):
                raise ValueError("HSL values are out of range")
            if stops and ratio <= stops[-1][0]:
                raise ValueError("Gradient ratios must increase")
            rgb = hls_to_rgb(h / 360, l / 100, s / 100)
            stops.append((ratio, "".join(f"{round(c * 255):02x}" for c in rgb)))
        if stops[0][0] != (0 if side == "below" else 1):
            raise ValueError("Invalid gradient starting ratio")
        if side == "below" and stops[-1][0] != 1:
            raise ValueError("Below-baseline gradient must end at 1")
        return stops
    except (KeyError, TypeError, ValueError, OverflowError):
        if gradient is None:
            raise
        return gradient_stops(None, side)


def point_opacity(point, side):
    ratio = float(point["workload_ratio"])
    default = min(100, 30 + max(0, ratio) * 105) if side == "below" else 100
    try:
        opacity = float(point.get("opacity", default))
        return max(0, min(100, opacity)) if math.isfinite(opacity) else default
    except (TypeError, ValueError):
        return default


def gradient_opacity(gradient, side, ratio):
    stops = gradient_stops(gradient, side)
    points = (gradient or DEFAULT_BASELINE_GRADIENT).get(side, [])
    values = []
    for position, _ in stops:
        point = next((p for p in points if isinstance(p, dict)
                      and p.get("workload_ratio") == position), {"workload_ratio": position})
        values.append((position, point_opacity(point, side)))
    for (start, low), (end, high) in zip(values, values[1:]):
        if ratio <= end:
            return (low + (high - low) * (ratio - start) / (end - start)) / 100
    return values[-1][1] / 100


def baseline_color(value: float, reference: Dict, reference_day: bool = False,
                   gradient: Optional[Dict] = None) -> str:
    """Interpolate continuously; white marks only the reference date."""
    if reference_day:
        return "#ffffff"
    return activity_color(value, baseline_value(reference), gradient)


def activity_color(value: float, anchor: Optional[float],
                   gradient: Optional[Dict] = None) -> str:
    """Shared continuous gradient for adaptive and reference-day scales."""
    ratio = max(0.0, value / anchor) if anchor else 0.0
    side = "below" if ratio < 1 else "above"
    stops = gradient_stops(gradient, side)
    opacity = gradient_opacity(gradient, side, ratio)

    def with_opacity(color):
        if opacity >= 1:
            return "#" + color
        r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
        return f"rgba({r}, {g}, {b}, {opacity:.4f})"
    for (start, low), (end, high) in zip(stops, stops[1:]):
        if ratio <= end:
            fraction = (ratio - start) / (end - start)
            channels = (
                round(int(low[i:i + 2], 16) * (1 - fraction)
                      + int(high[i:i + 2], 16) * fraction)
                for i in (0, 2, 4)
            )
            return with_opacity("".join(f"{channel:02x}" for channel in channels))
    return with_opacity(stops[-1][1])


def baseline_color_level(value: float, reference: Dict, reference_day: bool = False) -> int:
    """Orange, yellow, two darker greens, white, then five lighter greens.

    Only reviewed days call this function. The chosen reference date is a
    white marker even though its full workload exceeds the reduced baseline.
    Other days exactly at baseline enter the first lighter-green shade.
    """
    if reference_day:
        return 5
    return activity_color_level(value, baseline_value(reference))


def activity_color_level(value: float, anchor: Optional[float]) -> int:
    ratio = value / anchor if anchor else 0.0
    if ratio < 1:
        return max(1, min(4, math.ceil(ratio * 4)))
    for level, threshold in enumerate(ABOVE_BASELINE_FACTORS, start=6):
        if ratio <= threshold:
            return level
    return 10
