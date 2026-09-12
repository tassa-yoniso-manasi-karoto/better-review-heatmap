"""Color measures and reference selection, independent of Anki and Qt.

See LICENSE for the add-on's license and additional terms.
"""

import json
import math
from typing import Dict, List, Optional, Sequence


METRICS = {
    "reviews": {"label": "Classic — review count"},
    "time": {"label": "Study time"},
    "workload": {"label": "Workload — reviews and time"},
}
SCALES = {
    "fixed": {"label": "Fixed scale"},
    "baseline": {"label": "Automatic baseline"},
}

# Nine boundaries preserve the existing ten activity shades. These fixed
# scales never depend on another day's activity or on the visible date range.
FIXED_LEVELS = {
    "time": (1, 5, 10, 20, 30, 45, 60, 90, 120),
    "workload": (2, 5, 10, 20, 30, 45, 60, 90, 120),
}
BASELINE_FACTORS = (0.1, 0.2, 0.35, 0.5, 0.7, 1.0, 1.4, 2.0, 3.0)
MIN_REFERENCE_DAYS = 7


def metric_name(conf: Dict) -> str:
    value = conf.get("activity_metric", "reviews")
    return value if value in METRICS else "reviews"


def activity_value(reviews: int, milliseconds: int, metric: str) -> float:
    """Workload gives count and time equal proportional influence.

    Doubling both doubles workload. Doubling time alone increases it by only
    sqrt(2), limiting the influence of long readings and interrupted timers.
    Zero recorded duration is preserved; no missing time is estimated.
    """
    reviews = max(0, reviews)
    minutes = max(0, milliseconds) / 60000
    if metric == "time":
        return minutes
    if metric == "workload":
        return math.sqrt(reviews * minutes)
    return float(reviews)


def baseline_key(conf: Dict) -> str:
    """References are specific to a measure and its included history."""
    return json.dumps(
        [
            1,  # formula/reference format version
            metric_name(conf),
            sorted(conf.get("limdecks", [])),
            conf.get("limcdel", False),
            conf.get("limresched", True),
            conf.get("limdate", 0),
            conf.get("limhist", 0),
        ],
        separators=(",", ":"),
    )


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
        return [factor * float(reference["value"]) for factor in BASELINE_FACTORS]
    return list(FIXED_LEVELS[metric])
