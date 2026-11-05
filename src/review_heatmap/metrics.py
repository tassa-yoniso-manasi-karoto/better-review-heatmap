"""Color measures and reference selection, independent of Anki and Qt.

See LICENSE for the add-on's license and additional terms.
"""

import json
import math
from bisect import bisect_right
from collections import Counter
from colorsys import hls_to_rgb
from functools import lru_cache
from itertools import groupby, repeat
from operator import mul
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
    # A fitted credit already holds each answer's time, so nothing is added.
    "effort_model": {"label": "🧪 Calibrated effort (exp.)", "time_weight": 0.0},
}
EXPERIMENTAL_METRICS = ("fsrs", "concentration", "effort_model")
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
    "template_norm_days": 60.0,
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
    "template_norm_days": (1.0, 3650.0),
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
# With fewer recent answers, a template's norm reads all its earlier ones.
TEMPLATE_NORM_MIN_ANSWERS = 30
# Anki writes FSRS difficulty to revlog.factor as ((D - 1) / 9 + 0.1) * 1000,
# i.e. 100-1100. SM-2 ease factors are 1300 and above; 0 marks SM-2 learning
# steps and cramming.
FSRS_FACTOR_RANGE = (100, 1100)
# What the fitted model reads about an answer. Fitting and scoring both go
# through answer_inputs, so they cannot drift apart.
MODEL_INPUTS = (
    "log_d", "rich", "t_med", "log_d·rich", "new", "step",
    "again", "hard", "easy", "overdue", "reps", "reps·rich",
)
MODEL_TEMPLATE_DAYS = 60
# Answers and template medians can read 0 ms, which has no logarithm.
MODEL_MIN_MS = 300
MODEL_RICH_RANGE = (0.0, 3.0)
MODEL_OVERDUE_RANGE = (0.2, 50.0)
MODEL_ETA_RANGE = (math.log(1 / 16), math.log(32))
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


def load_effort_model() -> Dict:
    """The shipped weights; status "validated" releases them to every user."""
    return json.loads(Path(__file__).with_name("effort_model.json").read_text(encoding="utf-8"))


_effort_model = load_effort_model()
_effort_model_is_local = False


def use_effort_model(model: Optional[Dict] = None):
    """Score with a local fit, or with the shipped weights again when given None."""
    global _effort_model, _effort_model_is_local
    _effort_model = load_effort_model() if model is None else model
    _effort_model_is_local = model is not None


def active_effort_model() -> Dict:
    return _effort_model


def effort_model_available() -> bool:
    return _effort_model_is_local or _effort_model.get("status") == "validated"


def metric_name(conf: Dict) -> str:
    value = conf.get("activity_metric", DEFAULT_METRIC)
    if value == "effort_model" and not effort_model_available():
        return DEFAULT_METRIC
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


def time_weighted_median(histogram: Dict[int, int]) -> int:
    """The duration that splits recorded time in half, from {duration: answers}.

    A plain median describes the typical answer. On a template mixing quick
    clicks with long reading it sits on the clicks, though the time went to
    the reading.
    """
    half = sum(map(mul, histogram, histogram.values())) / 2
    spent = 0
    for duration in sorted(histogram):
        spent += duration * histogram[duration]
        if spent >= half:
            return duration
    return 0


def histogram_median(histogram: Dict[int, int]) -> int:
    """The typical answer's duration, from {duration: answers}."""
    half = sum(histogram.values()) / 2
    seen = 0
    for duration in sorted(histogram):
        seen += histogram[duration]
        if seen >= half:
            return duration
    return 0


def _template_windows(rows: Iterable[Sequence], days: float):
    """Yield (mid, ord, day, {seconds: answers}) for each template's study days.

    rows are (mid, ord, day, seconds, count) ordered by template and day. A
    day reads the given number of days before it, or every earlier day when
    those hold too few answers, and never the day itself or later ones: one
    slow day cannot raise its own norm and later study cannot rescore it. A
    template's first day has nothing to read, so it yields nothing. The
    histogram changes between days, so read it before advancing.
    """
    for (mid, card_ord), answers in groupby(rows, key=lambda row: row[:2]):
        answers = list(answers)
        earlier, recent = Counter(), Counter()
        entered = left = 0
        for day in sorted({row[2] for row in answers}):
            while answers[entered][2] < day:
                earlier[answers[entered][3]] += answers[entered][4]
                recent[answers[entered][3]] += answers[entered][4]
                entered += 1
            while answers[left][2] < day - days * 86400:
                recent[answers[left][3]] -= answers[left][4]
                left += 1
            if earlier:
                enough = sum(recent.values()) >= TEMPLATE_NORM_MIN_ANSWERS
                yield mid, card_ord, day, recent if enough else earlier


def template_norms(rows: Iterable[Sequence], days: float) -> Dict[tuple, int]:
    """Each template's norm in milliseconds per study day it was answered on."""
    return {
        (mid, card_ord, day): time_weighted_median(histogram) * 1000
        for mid, card_ord, day, histogram in _template_windows(rows, days)
    }


def template_profiles(rows: Iterable[Sequence], days: float) -> Dict[tuple, tuple]:
    """(median_ms, time_weighted_ms) per template and study day; rows as in norms."""
    return {
        (mid, card_ord, day): (
            histogram_median(histogram) * 1000, time_weighted_median(histogram) * 1000,
        )
        for mid, card_ord, day, histogram in _template_windows(rows, days)
    }


def concentration_coefficients(answers: Sequence,
                               calibration: Optional[Dict] = None) -> List[float]:
    """Credit multipliers for sustained concentration on heavy material.

    answers are (end_ms, duration_ms, typical_ms) in chronological order;
    typical_ms is the card template's norm, or None. An answer feeds the
    session's concentration clock with its duration, capped relative to that
    norm and gated by the sensitivity, so a slow
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


class CardHistory:
    """What a card's earlier answers left behind, fed one answer at a time.

    Feed every answer of the collection in revlog order, whatever gets scored:
    a day then reads the same on any view. Reschedules (ease 0) are not answers.
    """

    def __init__(self):
        self._cards: Dict[int, tuple] = {}

    def advance(self, cid: int, answer_ms: int, last_ivl: int) -> tuple:
        """(first, days since the card's last answer, interday answers before this)."""
        previous, interday = self._cards.get(cid, (None, 0))
        self._cards[cid] = (answer_ms, interday + (last_ivl >= 1))
        if previous is None:
            return True, None, 0
        return False, (answer_ms - previous) / 86400000, interday


@lru_cache(maxsize=4096)
def _template_inputs(median_ms: int, time_weighted_ms: int) -> tuple:
    typical = max(median_ms, MODEL_MIN_MS)
    rich = math.log(max(time_weighted_ms, MODEL_MIN_MS) / typical)
    return math.log(typical / 10000), min(MODEL_RICH_RANGE[1], max(MODEL_RICH_RANGE[0], rich))


def model_inputs(duration_ms: int, ease: int, first: bool, last_ivl: int,
                 elapsed_days: Optional[float], interday_answers: int,
                 profile: Optional[Sequence[int]]) -> Dict[str, float]:
    """The fitted model's MODEL_INPUTS for one answer.

    last_ivl is revlog.lastIvl: days when at least 1, otherwise a same-day
    step. profile is the template's (median_ms, time_weighted_ms), or None
    when the card is deleted or its template has no earlier answers.
    """
    log_d = math.log(max(duration_ms, MODEL_MIN_MS) / 10000)
    t_med, rich = (0.0, 0.0) if profile is None else _template_inputs(*profile)
    interday = last_ivl >= 1
    overdue = 0.0
    if interday and elapsed_days is not None:
        low, high = MODEL_OVERDUE_RANGE
        overdue = math.log(min(high, max(low, elapsed_days / last_ivl)))
    reps = math.log1p(interday_answers)
    return {
        "log_d": log_d,
        "rich": rich,
        "t_med": t_med,
        "log_d·rich": log_d * rich,
        "new": 1.0 if first else 0.0,
        "step": 0.0 if first or interday else 1.0,
        "again": 1.0 if ease == 1 else 0.0,
        "hard": 1.0 if ease == 2 else 0.0,
        "easy": 1.0 if ease == 4 else 0.0,
        "overdue": overdue,
        "reps": reps,
        "reps·rich": reps * rich,
    }


def answer_inputs(row: Sequence, history: CardHistory, profiles: Dict) -> Dict[str, float]:
    """Inputs for a revlog row (cid, id, ease, duration_ms, last_ivl, mid, ord, day).

    profiles comes from template_profiles over MODEL_TEMPLATE_DAYS. Call this
    for every answer in revlog order, skipping only reschedules (ease 0).
    """
    cid, answer_ms, ease, duration_ms, last_ivl, mid, card_ord, day = row
    first, elapsed_days, interday_answers = history.advance(cid, answer_ms, last_ivl)
    return model_inputs(duration_ms, ease, first, last_ivl, elapsed_days, interday_answers,
                        profiles.get((mid, card_ord, day)))


def effort_model_credit(inputs: Dict[str, float], model: Dict,
                        template: Optional[Sequence] = None) -> float:
    """exp(eta) of raw, unstandardised inputs; a missing weight counts as 0.

    template is the answer's (mid, ord). Only a fit kept on its own profile has
    "offsets", keyed "mid:ord": they mean nothing on another collection, so a
    shipped model never holds any.
    """
    weights = map(model.get("weights", {}).get, inputs, repeat(0.0))
    eta = model.get("intercept", 0.0) + sum(map(mul, weights, inputs.values()))
    offsets = model.get("offsets")
    if offsets and template:
        eta += offsets.get("{}:{}".format(*template), 0.0)
    return math.exp(min(MODEL_ETA_RANGE[1], max(MODEL_ETA_RANGE[0], eta)))


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
    elif metric == "effort_model":
        parts.append(active_effort_model().get("version", 0))
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
        "time", "workload", "custom", "fsrs", "concentration", "effort_model",
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
