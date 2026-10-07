"""Color measures and reference selection, independent of Anki and Qt.

See LICENSE for the add-on's license and additional terms.
"""

import json
import math
from bisect import bisect_right
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
    # Experimental measures share the review-weighted exponents; everything
    # they add is calibrated in the options dialog. FSRS-based only adjusts
    # review-weighted's scores, so it is the default and carries the star.
    "fsrs": {"label": "⭐ FSRS-based (exp.)", "time_weight": 0.4},
    "concentration": {
        "label": "🧪 FSRS-based + sustained concentration (exp.)",
        "time_weight": 0.4,
    },
}
EXPERIMENTAL_METRICS = ("fsrs", "concentration")
DEFAULT_METRIC = "fsrs"
SCALES = {
    "adaptive": {"label": "Classic"},
    "baseline": {"label": "Baseline (based on a reference day)"},
}

FORMULA_VERSION = 3
DAY_GROUPING_VERSION = 1
DEFAULT_CUSTOM_TIME_WEIGHT = 0.5
# Experimental measures replace each answer's count credit of 1 with an effort
# weight and, with sustained concentration, scale the whole credit. Every
# constant below is a calibration setting. Only values a user changed are
# stored, so updated defaults reach everyone who left a setting alone.
FSRS_CALIBRATION_DEFAULTS = {
    "new_card_weight": 1.5,
    "step_weight": 1.0,
    "maturity_pivot_days": 21.0,
    "maturity_exponent": 0.25,
    "difficulty_pivot": 50.0,
    "difficulty_exponent": 0.25,
    "weight_limit": 2.0,
}
FSRS_CALIBRATION_RANGES = {
    "new_card_weight": (0.1, 10.0),
    "step_weight": (0.1, 10.0),
    "maturity_pivot_days": (1.0, 3650.0),
    "maturity_exponent": (0.0, 1.0),
    "difficulty_pivot": (0.0, 100.0),
    "difficulty_exponent": (0.0, 1.0),
    "weight_limit": (1.0, 10.0),
}
CONCENTRATION_DEFAULTS = {
    "max_bonus": 1.0,
    "sensitivity_seconds": 45.0,
    "template_cap": 1.5,
    "warm_up_minutes": 10.0,
    "full_minutes": 30.0,
    "fatigue_cap_minutes": 75.0,
    "fade_minutes": 30.0,
    "break_minutes": 5.0,
}
CONCENTRATION_RANGES = {
    "max_bonus": (0.0, 5.0),
    "sensitivity_seconds": (1.0, 600.0),
    "template_cap": (1.0, 10.0),
    "warm_up_minutes": (0.0, 180.0),
    "full_minutes": (1.0, 240.0),
    "fatigue_cap_minutes": (5.0, 600.0),
    "fade_minutes": (1.0, 600.0),
    "break_minutes": (1.0, 120.0),
}
CALIBRATIONS = {
    "fsrs_calibration": (FSRS_CALIBRATION_DEFAULTS, FSRS_CALIBRATION_RANGES),
    "concentration_calibration": (CONCENTRATION_DEFAULTS, CONCENTRATION_RANGES),
}
METRIC_CALIBRATIONS = {
    "fsrs": ("fsrs_calibration",),
    "concentration": ("fsrs_calibration", "concentration_calibration"),
}
# Anki writes FSRS difficulty to revlog.factor as ((D - 1) / 9 + 0.1) * 1000,
# i.e. 100-1100. SM-2 ease factors are 1300 and above; 0 marks SM-2 learning
# steps and cramming.
FSRS_FACTOR_RANGE = (100, 1100)
BASELINE_FRACTION = 0.85
ABOVE_BASELINE_FACTORS = (1.25, 1.5, 2.0, 3.0)
MIN_REFERENCE_DAYS = 7
AUTO_REFERENCE_PERCENTILE = 90
AUTO_REFERENCE_REFRESH_DAYS = 30
ADAPTIVE_FACTORS = (0.125, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 4.0)
COLOR_THEMES = json.loads(Path(__file__).with_name("color_themes.json").read_text())
DEFAULT_BASELINE_GRADIENT = json.loads(
    Path(__file__).with_name("config.json").read_text(encoding="utf-8")
)["baseline_gradient_default"]


def metric_name(conf: Dict) -> str:
    value = conf.get("activity_metric", DEFAULT_METRIC)
    return value if value in METRICS else DEFAULT_METRIC


def metric_weights(metric: str, conf: Optional[Dict] = None):
    """One stored custom exponent makes the complementary pair sum to one."""
    weight = METRICS[metric]["time_weight"]
    if weight is None:
        weight = (conf or {}).get("custom_time_weight", DEFAULT_CUSTOM_TIME_WEIGHT)
    weight = float(weight)
    if not math.isfinite(weight) or not 0 <= weight <= 1:
        raise ValueError("The time exponent must be between 0 and 1")
    return 1 - weight, weight


def calibration_overrides(conf: Optional[Dict], name: str) -> Dict[str, float]:
    """Settings the user set manually; unknown, invalid or out-of-range ones are dropped."""
    defaults, ranges = CALIBRATIONS[name]
    saved = (conf or {}).get(name)
    overrides = {}
    for key, value in (saved.items() if isinstance(saved, dict) else ()):
        if key not in defaults:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        low, high = ranges[key]
        if low <= value <= high:
            overrides[key] = value
    return overrides


def calibration(conf: Optional[Dict], name: str) -> Dict[str, float]:
    """Current defaults, replaced only where the user calibrated manually."""
    return dict(CALIBRATIONS[name][0], **calibration_overrides(conf, name))


def fsrs_calibration(conf: Optional[Dict] = None) -> Dict[str, float]:
    return calibration(conf, "fsrs_calibration")


def concentration_calibration(conf: Optional[Dict] = None) -> Dict[str, float]:
    return calibration(conf, "concentration_calibration")


def fsrs_difficulty(factor) -> Optional[float]:
    """FSRS difficulty on its 1-10 scale, or None when the answer predates FSRS."""
    try:
        factor = int(factor)
    except (TypeError, ValueError):
        return None
    if not FSRS_FACTOR_RANGE[0] <= factor <= FSRS_FACTOR_RANGE[1]:
        return None
    return (factor / 1000 - 0.1) * 9 + 1


def answer_effort(first: bool, last_interval: int, factor: int,
                  calibration: Optional[Dict[str, float]] = None) -> float:
    """Effort of one answer relative to a routine review of a pivot card.

    first marks the card's earliest recorded answer. last_interval is
    revlog.lastIvl: days when at least 1, otherwise a same-day step. factor is
    revlog.factor; answers without an FSRS difficulty keep a neutral weight.
    """
    settings = calibration if calibration is not None else fsrs_calibration()
    if first:
        effort = settings["new_card_weight"]
    elif last_interval >= 1:
        effort = (settings["maturity_pivot_days"] / last_interval) ** settings["maturity_exponent"]
    else:
        effort = settings["step_weight"]
    difficulty = fsrs_difficulty(factor)
    if difficulty is not None:
        pivot = 1 + 9 * settings["difficulty_pivot"] / 100
        effort *= (difficulty / pivot) ** settings["difficulty_exponent"]
    limit = settings["weight_limit"]
    return min(limit, max(1 / limit, effort))


def smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def concentration_coefficients(answers: Sequence,
                               calibration: Optional[Dict] = None) -> List[float]:
    """Credit multipliers for sustained concentration on heavy material.

    answers are (end_ms, duration_ms, typical_ms) in chronological order;
    typical_ms is the card template's usual recorded duration, or None. An
    answer feeds the session's concentration clock with its duration, capped
    relative to its template's norm and gated by the sensitivity, so a slow
    day on quick cards builds nothing. The bonus rises smoothly between the
    warm-up and full-concentration marks and fades past the fatigue cap. An
    idle gap longer than the break starts a new session. Recorded durations
    themselves are never altered.
    """
    c = calibration if calibration is not None else concentration_calibration()
    sensitivity, break_ms = c["sensitivity_seconds"] * 1000, c["break_minutes"] * 60000
    warm, full, cap = c["warm_up_minutes"], c["full_minutes"], c["fatigue_cap_minutes"]
    coefficients = []
    session_start = previous_end = None
    heavy = 0.0
    for end, duration, typical in answers:
        duration = max(0, duration)
        start = end - duration
        if previous_end is None or start - previous_end > break_ms:
            session_start, heavy = start, 0.0
        previous_end = end
        limit = duration if typical is None else min(duration, c["template_cap"] * typical)
        gate = min(1.0, max(0.0, (limit - sensitivity) / sensitivity))
        heavy += limit * gate / 60000
        if full > warm:
            rise = smoothstep((heavy - warm) / (full - warm))
        else:
            rise = 1.0 if heavy >= warm else 0.0
        elapsed = (end - session_start) / 60000
        fade = 1.0 if elapsed <= cap else math.exp(-(elapsed - cap) / c["fade_minutes"])
        coefficients.append(1 + c["max_bonus"] * rise * fade)
    return coefficients


def activity_value(reviews: int, milliseconds: int, metric: str,
                   duration_counts=None, conf: Optional[Dict] = None) -> float:
    """Sum per-answer credit using (milliseconds, count[, effort[, scale]]) buckets.

    Collection callers must supply actual duration buckets. Without them this
    evaluates a synthetic equal-duration scenario, used by the totals-only CLI.
    The uniform assumption is never used to migrate an actual saved reference.
    Only experimental measures read a bucket's effort, and only sustained
    concentration its scale; others count each answer once.
    """
    reviews = max(0, reviews)
    minutes = max(0, milliseconds) / 60000
    review_weight, time_weight = metric_weights(metric, conf)
    use_effort = metric in EXPERIMENTAL_METRICS and duration_counts is not None
    use_scale = metric == "concentration" and duration_counts is not None
    if not use_effort:
        if time_weight == 1:
            return minutes
        if time_weight == 0:
            return float(reviews)
        if duration_counts is None:
            return reviews * (minutes / reviews) ** time_weight if reviews else 0.0
    return math.fsum(
        bucket[1]
        * (bucket[2] ** review_weight if use_effort and len(bucket) > 2 else 1.0)
        * (bucket[3] if use_scale and len(bucket) > 3 else 1.0)
        * (max(0, bucket[0]) / 60000) ** time_weight
        for bucket in duration_counts
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
    elif metric in METRIC_CALIBRATIONS:
        parts.append({name: calibration(conf, name) for name in METRIC_CALIBRATIONS[metric]})
    if deck_id is not None:
        parts.append(["deck", int(deck_id)])
    return json.dumps(parts, separators=(",", ":"))


def legacy_reference(conf: Dict, deck_id: Optional[int] = None) -> Optional[Dict]:
    """Find the active measure/filter snapshot without altering older versions."""
    references = conf.get("activity_baselines", {})
    if not isinstance(references, dict):
        return None
    shared = workload_reference_day(conf, deck_id)
    if shared is not None:
        return shared  # Recalculate this day using the active formula's durations.
    pending = references.get(baseline_key(conf, deck_id))
    if isinstance(pending, dict) and (
        pending.get("needs_durations")
        or pending.get("day_grouping_version") != DAY_GROUPING_VERSION
    ):
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
    migrate when used. Keep the chosen day and source when correcting grouping.
    If its reviews are unavailable, retain the old snapshot for recovery.
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
    if not isinstance(reference, dict) or reference.get("needs_durations") or (
        reference.get("day_grouping_version") != DAY_GROUPING_VERSION
    ):
        return None
    shared = workload_reference_day(conf, deck_id)
    if shared is not None and (
        reference.get("day") != shared["day"] or reference.get("source") != "selected"
    ):
        return None
    try:
        value = float(reference["value"])
        int(reference["day"])
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    return reference if math.isfinite(value) and value > 0 else None


def _workload_day_key(parts):
    if not isinstance(parts, list) or len(parts) < 7 or parts[1] not in (
        "time", "workload", "custom", "fsrs", "concentration",
    ):
        return None
    scope = parts[-1:] if isinstance(parts[-1], list) else []
    return json.dumps(["workload-day", *parts[2:7], *scope], separators=(",", ":"))


def workload_reference_day(conf: Dict, deck_id: Optional[int] = None) -> Optional[Dict]:
    """Share a manual day, but never its formula-specific score or deck scope."""
    references = conf.get("activity_baselines", {})
    key = baseline_key(conf, deck_id)
    shared_key = _workload_day_key(json.loads(key))
    if shared_key is None or not isinstance(references, dict):
        return None
    if shared_key in references:
        selected = references[shared_key]
        # An explicit automatic choice disables inheritance from old manual picks.
        return selected if isinstance(selected, dict) and selected.get("source") == "selected" else None
    # Recover existing installations without deleting their old snapshots.
    # Prefer this mode's own manual choice if older modes disagree.
    parts = json.loads(key)
    candidates = [key]
    for version in (2, 1):
        candidates.append(json.dumps([version, *parts[1:]], separators=(",", ":")))
    candidates.extend(reversed(references))
    for candidate in candidates:
        try:
            if _workload_day_key(json.loads(candidate)) != shared_key:
                continue
            reference = references[candidate]
            if reference.get("source") != "selected":
                continue
            int(reference["day"])
            value = float(reference["value"])
            if math.isfinite(value) and value > 0:
                return reference
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
            continue
    return None


def set_reference(conf: Dict, reference: Dict, deck_id: Optional[int] = None):
    """Save an explicit pick; workload modes share the selected day."""
    if not isinstance(conf.get("activity_baselines"), dict):
        conf["activity_baselines"] = {}
    key = baseline_key(conf, deck_id)
    conf["activity_baselines"][key] = reference
    shared_key = _workload_day_key(json.loads(key))
    if shared_key is not None:
        conf["activity_baselines"][shared_key] = dict(reference)


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
        "day_grouping_version": DAY_GROUPING_VERSION,
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
    """Custom goal gradient, with the intentional jump at the target."""
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


def adaptive_color_level(value: float, anchor: Optional[float]) -> int:
    ratio = max(0.0, value / anchor) if anchor else 0.0
    return bisect_right(ADAPTIVE_FACTORS, ratio) + 1


def adaptive_color(value: float, anchor: Optional[float], theme: str = "lime",
                   night_mode: bool = False) -> str:
    """Original theme shades, interpolated without a goal boundary at the median."""
    colors = COLOR_THEMES.get(theme, COLOR_THEMES["lime"])
    if night_mode:
        colors = colors[::-1]
    ratio = max(0.0, value / anchor) if anchor else 0.0
    stops = list(zip((0.0,) + ADAPTIVE_FACTORS, colors))
    for (start, low), (end, high) in zip(stops, stops[1:]):
        if ratio <= end:
            fraction = (ratio - start) / (end - start)
            channels = (
                round(int(low[i:i + 2], 16) * (1 - fraction)
                      + int(high[i:i + 2], 16) * fraction)
                for i in (1, 3, 5)
            )
            return "#" + "".join(f"{channel:02x}" for channel in channels)
    return colors[-1]
