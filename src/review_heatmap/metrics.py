"""Color measures and reference selection, independent of Anki and Qt.

See LICENSE for the add-on's license and additional terms.
"""

import json
import math
from colorsys import hls_to_rgb
from pathlib import Path
from typing import Dict, List, Optional, Sequence


METRICS = {
    "reviews": {"label": "Review count (classic)"},
    "time": {"label": "Workload (linear)"},
    "workload": {"label": "Workload (review-weighted)"},
}
SCALES = {
    "fixed": {"label": "Fixed scale"},
    "baseline": {"label": "Automatic baseline"},
}

# Nine boundaries preserve the existing ten activity shades. These fixed
# scales never depend on another day's activity or on the visible date range.
FIXED_LEVELS = {
    # Unadjusted count × minutes uses squared score units compared with the
    # original square-root workload; keep practical fixed-scale boundaries.
    "time": (4, 25, 100, 400, 900, 2025, 3600, 8100, 14400),
    "workload": (2, 5, 10, 20, 30, 45, 60, 90, 120),
}
BASELINE_FRACTION = 0.85
ABOVE_BASELINE_FACTORS = (1.25, 1.5, 2.0, 3.0)
MIN_REFERENCE_DAYS = 7
DEFAULT_BASELINE_GRADIENT = json.loads(
    Path(__file__).with_name("config.json").read_text(encoding="utf-8")
)["baseline_gradient_default"]


def metric_name(conf: Dict) -> str:
    value = conf.get("activity_metric", "workload")
    return value if value in METRICS else "workload"


def activity_value(reviews: int, milliseconds: int, metric: str) -> float:
    """Linear workload multiplies count and total minutes without adjustment.

    Review-weighted workload uses count times the cube root of average duration:
    count^(2/3) * total_minutes^(1/3). Doubling both doubles workload, while
    doubling time alone multiplies it by the cube root of two.
    Zero recorded duration is preserved; no missing time is estimated.
    """
    reviews = max(0, reviews)
    minutes = max(0, milliseconds) / 60000
    if metric == "time":
        # Keep the stored mode key so existing selections carry over.
        return reviews * minutes
    if metric == "workload":
        return reviews * (minutes / reviews) ** (1 / 3) if reviews else 0.0
    return float(reviews)


def baseline_key(conf: Dict) -> str:
    """References are specific to a measure and its included history."""
    return json.dumps(
        [
            1 if metric_name(conf) == "reviews" else 2,  # formula version
            metric_name(conf),
            sorted(conf.get("limdecks", [])),
            conf.get("limcdel", False),
            conf.get("limresched", True),
            conf.get("limdate", 0),
            conf.get("limhist", 0),
        ],
        separators=(",", ":"),
    )


def migrate_activity_references(conf: Dict) -> bool:
    """Recalculate old time/workload references from their saved raw totals.

    Keep the original snapshots for compatibility with older installations.
    Selected dates, filters, sources, and existing new-format references stay
    intact; this migration never reads or changes review history.
    """
    references = conf.get("activity_baselines", {})
    if not isinstance(references, dict):
        return False
    changed = False
    for key, reference in list(references.items()):
        if not isinstance(reference, dict):
            continue
        try:
            parts = json.loads(key)
            if (not isinstance(parts, list) or len(parts) != 7
                    or parts[0] != 1 or parts[1] not in ("time", "workload")):
                continue
            parts[0] = 2
            new_key = json.dumps(parts, separators=(",", ":"))
            if new_key in references:
                continue
            int(reference["day"])
            value = activity_value(
                int(reference["reviews"]), int(reference["time_ms"]), parts[1]
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(value) or value <= 0:
            continue
        references[new_key] = dict(reference, value=value)
        changed = True
    return changed


def saved_reference(conf: Dict) -> Optional[Dict]:
    references = conf.get("activity_baselines", {})
    if not isinstance(references, dict):
        return None
    reference = references.get(baseline_key(conf))
    if not isinstance(reference, dict):
        return None
    try:
        value = float(reference["value"])
        int(reference["day"])
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    return reference if math.isfinite(value) and value > 0 else None


def reference_from_day(row: Sequence[int], metric: str, source: str) -> Optional[Dict]:
    day, reviews, milliseconds = row
    value = activity_value(reviews, milliseconds, metric)
    if value <= 0:
        return None
    return {
        "day": day,
        "reviews": reviews,
        "time_ms": milliseconds,
        "value": value,
        "source": source,
    }


def automatic_reference(rows: Sequence[Sequence[int]], metric: str) -> Optional[Dict]:
    candidates = [reference_from_day(row, metric, "automatic") for row in rows]
    candidates = sorted(
        (ref for ref in candidates if ref is not None),
        key=lambda ref: (ref["value"], ref["day"]),
    )
    if len(candidates) < MIN_REFERENCE_DAYS:
        return None
    # Nearest-rank 75th percentile selects an actual, completed study day.
    return candidates[math.ceil(0.75 * len(candidates)) - 1]


def activity_levels(metric: str, reference: Optional[Dict] = None) -> List[float]:
    if reference is not None:
        # Baseline days are assigned palette levels, leaving the underlying
        # counts, recorded time, and saved reference snapshot untouched.
        return list(range(1, 10))
    return list(FIXED_LEVELS[metric])


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
    ratio = max(0.0, value / baseline_value(reference))
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
    ratio = value / baseline_value(reference)
    if reference_day:
        return 5
    if ratio < 1:
        return max(1, min(4, math.ceil(ratio * 4)))
    for level, threshold in enumerate(ABOVE_BASELINE_FACTORS, start=6):
        if ratio <= threshold:
            return level
    return 10
