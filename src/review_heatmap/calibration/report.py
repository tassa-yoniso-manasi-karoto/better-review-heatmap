"""The fit and its report, shown in the tab and kept as report-YYYYMMDD.json. Pure.

Sharpness and drift are left out of every prediction, here as in scoring: what
the holdout and the judge measure is the model as it would ship. Note-type
offsets belong to the profile fit alone, and only the line named for them reads them.
"""

import json
import math
import os
import time
from collections import Counter
from pathlib import Path
from statistics import median, median_low
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

from ..metrics import (
    MODEL_INPUTS, activity_value, answer_effort, effort_model_credit, fsrs_calibration,
    load_effort_model,
)
from .dataset import (
    OFFSET_PREFIX, Dataset, RatedAnswer, RatedDay, design, model_row, read_dataset,
)
from .fit import (
    OrderedFit, fit_ordered, mean_log_loss, one_sided_t_5_percent, partial_rank_t,
    predict_eta, spearman,
)
from .phone import merge_phone, read_files
from .sampling import prompt_counts
from .store import DAY_MS, Store, now_ms

HOLDOUT_SHARE = 0.2
JUDGED_SHARE = 0.3
GATE_COST_RATINGS = 800
GATE_RATED_DAYS = 60
# The partial test needs at least this many judged days for its t to mean much.
GATE_MIN_JUDGED_DAYS = 8
RICH_LEVELS = (0.0, 1.5)
MEASURES = {
    "count": "Count",
    "recorded_time": "Recorded time",
    "review_weighted": "Review-weighted",
    "fsrs": "FSRS-based",
    "fitted": "Fitted",
    "fitted_offsets": "Fitted with note-type offsets",
}
# prompts.py needs Qt, so these repeat its COST and WHY labels.
COST_LABELS = (
    "¼ of one", "⅓ of one", "½ of one", "¾ of one", "One", "One and a half",
    "Two", "Three", "Four", "Six", "Eight or more",
)
WHY_LABELS = ("I blanked", "A hint", "Partly", "Nearly", "On purpose")

MODEL_FILE = "model.json"

# Strata with fewer comparable answers on either side tell nothing about pace.
_MIN_STRATUM = 5


def run_fit(store: Store, rows: Sequence[Sequence], conf: Optional[Dict],
            template_names: Dict[Tuple[int, int], str],
            progress: Callable[[str], None], media_dir=None, phone_index=None) -> dict:
    """Read the data, fit, and save the report. Rows as in dataset.revlog_sql."""
    progress("Reading the calibration events")
    state = store.load_state()
    events = list(store.read_events())
    phone = merge_phone(read_files(media_dir), rows, phone_index or {})
    if phone.earliest_ms is not None:
        state["recording_since"] = min(state["recording_since"] or phone.earliest_ms,
                                       phone.earliest_ms)
    dataset = read_dataset([*events, *phone.events], phone.rows, state["block_gap_hours"])
    version = next_model_version(store.folder)
    report = build_report(dataset, state, conf, template_names, version, progress)
    report["phone"] = phone.diagnostics
    write_report(store.folder, report)
    return report


def build_report(dataset: Dataset, state: dict, conf: Optional[Dict],
                 template_names: Dict[Tuple[int, int], str], version: int,
                 progress: Callable[[str], None]) -> dict:
    rated = dataset.rated
    progress("Fitting every cost rating")
    fit = _fit(rated)
    local = _fit(rated, note_type_offsets=True)
    progress("Testing on the later rating days")
    holdout = _holdout(rated)
    progress("Judging against the day ratings")
    judge = _judge(dataset, conf)
    progress("Writing the report")
    rated_days = sum(day.tiring is not None for day in dataset.days)
    counts = prompt_counts(dataset.prompts)
    use = _category_use(dataset.prompts)
    names = {
        f"template:{mid}:{card_ord}": name for (mid, card_ord), name in template_names.items()
    }
    return {
        "date_ms": now_ms(),
        "counts": {
            **counts,
            "rated_answers": len(rated),
            "rated_days": rated_days,
            "logged": len(dataset.logged),
            "unlogged": dataset.unlogged,
            "undone": dataset.undone,
        },
        "category_use": use,
        "skip_rate": counts["skipped"] / counts["prompts"] if counts["prompts"] else 0.0,
        "fit": {
            "ratings": len(rated),
            "sigma": fit.sigma,
            "loglik": fit.loglik,
            "iterations": fit.iterations,
            "converged": fit.converged,
            "weights": [
                {
                    "name": name,
                    "label": names.get(name, name),
                    "weight": fit.weights[name],
                    "se": fit.se[name],
                    "control": name in fit.control_names,
                }
                for name in fit.names
            ],
            "time_exponent": {str(rich): _time_exponent(fit, rich) for rich in RICH_LEVELS},
        },
        "note_type_offsets": [
            {
                "label": names.get(name, name),
                "weight": local.weights[name],
                "se": local.se[name],
            }
            for name in local.control_names if name.startswith(OFFSET_PREFIX)
        ],
        "model": model_body(fit, version),
        "local_model": (
            local_model_body(local, version) if local.converged else model_body(fit, version)
        ),
        "holdout": holdout,
        "judge": judge,
        "templates": _front_back(dataset, template_names),
        "cost_by_reason": _cost_by_reason(rated),
        "reactivity": _reactivity(dataset, state),
        "gate": ship_gate(len(rated), rated_days, judge, fit.converged),
    }


def model_body(fit: OrderedFit, version: int) -> dict:
    """A body for effort_model.json. It stays "local" until a person ships it,
    so that pasting weights can't release them by accident."""
    return {
        "status": "local",
        "version": version,
        "intercept": fit.intercept,
        "weights": {name: fit.weights[name] for name in MODEL_INPUTS},
    }


def local_model_body(fit: OrderedFit, version: int) -> dict:
    """The same, keeping a fit's note-type offsets for scoring on this profile.
    Never ship it: other collections have other note types."""
    return {
        **model_body(fit, version),
        "offsets": {
            name[len(OFFSET_PREFIX):]: fit.weights[name]
            for name in fit.control_names if name.startswith(OFFSET_PREFIX)
        },
    }


def ship_gate(cost_ratings: int, rated_days: int, judge: Optional[dict],
              converged: bool) -> dict:
    """The fitted score ships only if, on days it never saw, it is no worse than
    review-weighted on "tiring" and explains ratings review-weighted can't.

    Day scores agree so closely that even a perfect fit gains only about 0.02 to
    0.05 in plain Spearman, so a fixed margin would turn away real gains, more
    surely with more days. The partial test asks about the part where the two
    differ, and gets stronger with data. Failing is a valid result: nothing ships.
    """
    tiring = judge["tiring"] if judge else {}
    fitted, weighted = tiring.get("fitted"), tiring.get("review_weighted")
    margin = None if fitted is None or weighted is None else fitted - weighted
    beyond = judge.get("beyond") if judge else None
    reasons = []
    if cost_ratings < GATE_COST_RATINGS:
        reasons.append(f"{cost_ratings} of {GATE_COST_RATINGS} cost ratings")
    if rated_days < GATE_RATED_DAYS:
        reasons.append(f"{rated_days} of {GATE_RATED_DAYS} rated days")
    if not converged or (judge and judge["converged"] is False):
        reasons.append("a fit did not converge")
    if margin is None or beyond is None:
        reasons.append("no fit could be judged on later days")
    elif margin < 0:
        reasons.append(f"fitted trails review-weighted on tiring by {margin:+.3f}")
    elif beyond["t"] < beyond["needed"]:
        reasons.append(
            f"fitted adds too little beyond review-weighted on tiring: "
            f"t {beyond['t']:.2f}, it needs {beyond['needed']:.2f}"
        )
    return {"passed": not reasons, "reasons": reasons, "margin": margin, "beyond": beyond}


# Sections
###########################################################################


def _fit(rated: Sequence[RatedAnswer], note_type_offsets: bool = False) -> OrderedFit:
    rows, ratings, names, controls = design(rated, note_type_offsets)
    return fit_ordered(rows, ratings, names, control_names=controls)


def _time_exponent(fit: OrderedFit, rich: float) -> float:
    """How credit grows with an answer's duration, as a power of it."""
    return fit.weights["log_d"] + rich * fit.weights["log_d·rich"]


def _later_share(days: Sequence[int], share: float) -> Optional[int]:
    """First day of the later share of days, leaving some earlier ones."""
    if len(days) < 2:
        return None
    return days[-max(1, round(len(days) * share))]


def _holdout(rated: Sequence[RatedAnswer]) -> Optional[dict]:
    days = sorted({answer.day for answer in rated})
    first_held = _later_share(days, HOLDOUT_SHARE)
    if first_held is None:
        return None
    train = [answer for answer in rated if answer.day < first_held]
    held = [answer for answer in rated if answer.day >= first_held]
    fit = _fit(train)
    train_ratings = [answer.cost for answer in train]
    intercept_only = fit_ordered([[]] * len(train), train_ratings, [])
    ratings = [answer.cost for answer in held]
    rows = [model_row(answer) for answer in held]
    return {
        "days": sum(day >= first_held for day in days),
        "ratings": len(held),
        "train_ratings": len(train),
        "log_loss": mean_log_loss(fit, rows, ratings),
        "intercept_only_log_loss": mean_log_loss(intercept_only, [[]] * len(held), ratings),
        "spearman": spearman([predict_eta(fit, row) for row in rows], ratings),
    }


def _judge(dataset: Dataset, conf: Optional[Dict]) -> Optional[dict]:
    """Rank the later rated days by each measure, with the fit trained only on
    ratings from before them, as the gate requires."""
    first_judged = _later_share(
        [day.day for day in dataset.days if day.tiring is not None], JUDGED_SHARE,
    )
    if first_judged is None:
        return None
    judged = [day for day in dataset.days if day.day >= first_judged]
    train = [answer for answer in dataset.rated if answer.day < first_judged]
    fit = _fit(train) if train else None
    models = {}
    if fit:
        models = {
            "fitted": model_body(fit, 0),
            "fitted_offsets": local_model_body(_fit(train, note_type_offsets=True), 0),
        }
    scores = {day.day: _day_scores(day, models, conf) for day in judged}
    return {
        "train_ratings": len(train),
        "converged": fit.converged if fit else None,
        "tiring": _rank_against("tiring", judged, scores),
        "learned": _rank_against("learned", judged, scores),
        "beyond": _beyond_review_weighted(judged, scores),
    }


def _rank_against(target: str, days: Sequence[RatedDay], scores: Dict[int, dict]) -> dict:
    rated = [day for day in days if getattr(day, target) is not None]
    ratings = [getattr(day, target) for day in rated]
    result: Dict[str, Union[int, float, None]] = {"days": len(rated)}
    for measure in MEASURES:
        values = [scores[day.day][measure] for day in rated]
        missing = None in values
        result[measure] = None if missing else spearman(values, ratings)
    return result


def _beyond_review_weighted(days: Sequence[RatedDay], scores: Dict[int, dict]) -> Optional[dict]:
    """What the shared fit explains of "tiring" that review-weighted leaves
    unexplained: the gate's test, on days the fit never saw."""
    rated = [day for day in days if day.tiring is not None]
    if len(rated) < GATE_MIN_JUDGED_DAYS or scores[rated[0].day]["fitted"] is None:
        return None
    r, t = partial_rank_t(
        [day.tiring for day in rated],
        [scores[day.day]["review_weighted"] for day in rated],
        [scores[day.day]["fitted"] for day in rated],
    )
    return {"days": len(rated), "r": r, "t": t, "needed": one_sided_t_5_percent(len(rated) - 3)}


def _day_scores(day: RatedDay, models: Dict[str, dict], conf: Optional[Dict]) -> dict:
    """Every measure as the calendar would score this day."""
    reviews = len(day.answers)
    milliseconds = sum(answer.duration_ms for answer in day.answers)
    effort = fsrs_calibration(conf)
    scores = {
        "count": float(reviews),
        "recorded_time": activity_value(reviews, milliseconds, "recorded_time", conf=conf),
        "review_weighted": activity_value(reviews, milliseconds, "workload", [
            (answer.duration_ms, 1) for answer in day.answers
        ], conf),
        "fsrs": activity_value(reviews, milliseconds, "fsrs", [
            (
                answer.duration_ms, 1,
                answer_effort(bool(answer.inputs["new"]), answer.last_ivl, answer.factor, effort),
            )
            for answer in day.answers
        ], conf),
        "fitted": None,
        "fitted_offsets": None,
    }
    for measure, model in models.items():
        scores[measure] = activity_value(reviews, milliseconds, "effort_model", [
            (answer.duration_ms, 1, effort_model_credit(answer.inputs, model, answer.template))
            for answer in day.answers
        ], conf)
    return scores


def _category_use(prompts: Sequence[dict]) -> Dict[str, Dict[str, int]]:
    use: Dict[str, Counter] = {}
    for event in prompts:
        for question, value in event["answers"].items():
            use.setdefault(question, Counter())[value] += 1
    return {
        question: {str(value): count for value, count in sorted(values.items())}
        for question, values in use.items()
    }


def _front_back(dataset: Dataset, names: Dict[Tuple[int, int], str]) -> List[dict]:
    """Medians, so that an answer left on screen over lunch counts as one."""
    templates: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    for answer in dataset.logged:
        if answer.template and answer.front_ms is not None:
            templates.setdefault(answer.template, []).append((answer.front_ms, answer.back_ms))
    report = []
    for template, times in templates.items():
        front = median(time_ms[0] for time_ms in times)
        back = median(time_ms[1] for time_ms in times)
        report.append({
            "template": names.get(template, "{}:{}".format(*template)),
            "answers": len(times),
            "front_ms": front,
            "back_ms": back,
            "front_share": front / (front + back) if front + back else 0.0,
        })
    return sorted(report, key=lambda row: -row["answers"])


def _cost_by_reason(rated: Sequence[RatedAnswer]) -> List[dict]:
    costs: Dict[int, List[int]] = {}
    for answer in rated:
        if answer.why is not None:
            costs.setdefault(answer.why, []).append(answer.cost)
    return [
        {
            "reason": WHY_LABELS[why],
            "ratings": len(values),
            "median_cost": COST_LABELS[median_low(values)],
        }
        for why, values in sorted(costs.items())
    ]


def _reactivity(dataset: Dataset, state: dict) -> dict:
    """Pace ratios on comparable answers: the same template, button and phase.
    Above 1, prompts go with slower answers."""
    since = state["recording_since"] or 0
    baseline_end = since + state["baseline_days"] * DAY_MS
    groups: Dict[str, Dict[tuple, List[int]]] = {
        "baseline": {}, "afterwards": {}, "after_prompt": {}, "others": {},
    }
    for answer in dataset.logged:
        if not answer.taken_ms:
            continue
        key = (answer.template, answer.ease, answer.phase)
        if answer.rev_id < baseline_end:
            groups["baseline"].setdefault(key, []).append(answer.taken_ms)
            continue
        groups["afterwards"].setdefault(key, []).append(answer.taken_ms)
        side = "after_prompt" if answer.after_prompt else "others"
        groups[side].setdefault(key, []).append(answer.taken_ms)
    return {
        "afterwards_vs_baseline": _pace_ratio(groups["afterwards"], groups["baseline"]),
        "after_prompt_vs_others": _pace_ratio(groups["after_prompt"], groups["others"]),
    }


def _pace_ratio(group: Dict[tuple, List[int]], control: Dict[tuple, List[int]]) -> Optional[dict]:
    """Geometric mean of the strata's median ratios, by their smaller side."""
    strata = [
        (min(len(group[key]), len(control[key])), median(group[key]) / median(control[key]))
        for key in group.keys() & control.keys()
        if min(len(group[key]), len(control[key])) >= _MIN_STRATUM
    ]
    if not strata:
        return None
    answers = sum(weight for weight, _ in strata)
    return {
        "ratio": math.exp(math.fsum(w * math.log(ratio) for w, ratio in strata) / answers),
        "strata": len(strata),
        "answers": answers,
    }


# Files
###########################################################################


def next_model_version(folder: Path) -> int:
    """Above every earlier fit and the shipped model: baseline_key appends the
    version, so a reused one would keep references scored by other weights."""
    reports = map(_read_json, Path(folder).glob("report-*.json"))
    return 1 + max(
        (read_model(folder) or {}).get("version", 0),
        load_effort_model().get("version", 0),
        *(report["model"]["version"] for report in reports if report),
    )


def write_report(folder: Path, report: dict) -> Path:
    path = Path(folder) / time.strftime("report-%Y%m%d.json")
    _write_json(path, report)
    return path


def latest_report(folder: Path) -> Optional[dict]:
    path = max(Path(folder).glob("report-*.json"), default=None)
    return path and _read_json(path)


def write_model(folder: Path, model: dict) -> None:
    _write_json(Path(folder) / MODEL_FILE, model)


def read_model(folder: Path) -> Optional[dict]:
    return _read_json(Path(folder) / MODEL_FILE)


def _write_json(path: Path, content: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(pretty_json(content), encoding="utf-8")
    os.replace(temporary, path)


def pretty_json(content: dict) -> str:
    return json.dumps(content, indent=2, ensure_ascii=False) + "\n"


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# Text
###########################################################################


def gate_text(gate: dict) -> str:
    if gate["passed"]:
        beyond = gate["beyond"]
        return (
            f"Ship gate: PASSED, fitted adds to review-weighted on tiring "
            f"(t {beyond['t']:.2f}, it needed {beyond['needed']:.2f})."
        )
    return "Ship gate: NOT PASSED, nothing ships. " + "; ".join(gate["reasons"]) + "."


def format_report(report: dict) -> str:
    return "\n".join((
        *_counts_lines(report),
        "",
        *_fit_lines(report["fit"]),
        "",
        *_offset_lines(report["note_type_offsets"]),
        "",
        _holdout_line(report["holdout"]),
        "",
        *_judge_lines(report["judge"]),
        "",
        *_template_lines(report["templates"]),
        "",
        *_reason_lines(report["cost_by_reason"]),
        "",
        *_reactivity_lines(report["reactivity"]),
    ))


def _counts_lines(report: dict) -> List[str]:
    counts = report["counts"]
    use = report["category_use"].get("cost", {})
    return [
        f"Cost ratings: {counts['rated_answers']}. Rated days: {counts['rated_days']}. "
        f"Skip rate: {report['skip_rate']:.0%}.",
        f"Answers logged: {counts['logged']}, unlogged: {counts['unlogged']}, "
        f"undone: {counts['undone']}.",
        "Cost use: " + "  ".join(
            f"{label} {use.get(str(index), 0)}" for index, label in enumerate(COST_LABELS)
        ),
        *(
            f"{question.capitalize()} use: "
            + "  ".join(f"{value}: {count}" for value, count in values.items())
            for question, values in report["category_use"].items() if question != "cost"
        ),
    ]


def _fit_lines(fit: dict) -> List[str]:
    converged = "converged" if fit["converged"] else "DID NOT CONVERGE"
    exponents = ", ".join(
        f"{value:.3f} at rich {rich}" for rich, value in fit["time_exponent"].items()
    )
    return [
        f"Fit on {fit['ratings']} ratings: sigma {fit['sigma']:.3f}, {converged}.",
        f"Time exponent: {exponents}.",
        *(
            f"{row['label']}: {row['weight']:+.3f} ± {row['se']:.3f}"
            + (" (control)" if row["control"] else "")
            for row in fit["weights"]
        ),
    ]


def _offset_lines(offsets: Sequence[dict]) -> List[str]:
    """From the fit kept on this profile. They never ship."""
    if not offsets:
        return ["Note-type offsets: no template has 30 ratings yet."]
    return ["Note-type offsets, this profile only:"] + [
        f"{row['label']}: {row['weight']:+.3f} ± {row['se']:.3f}" for row in offsets
    ]


def _holdout_line(holdout: Optional[dict]) -> str:
    if holdout is None:
        return "Holdout: too few rating days."
    return (
        f"Holdout, later {HOLDOUT_SHARE:.0%} of rating days ({holdout['ratings']} ratings "
        f"on {holdout['days']} days): log loss {holdout['log_loss']:.3f}, intercept-only "
        f"{holdout['intercept_only_log_loss']:.3f}, Spearman {holdout['spearman']:.3f}."
    )


def _judge_lines(judge: Optional[dict]) -> List[str]:
    if judge is None:
        return ["Judge: too few rated days."]
    lines = [
        f"Judge, later {JUDGED_SHARE:.0%} of rated days, "
        f"fit on {judge['train_ratings']} earlier ratings:"
    ]
    for target in ("tiring", "learned"):
        ranks = judge[target]
        scores = ", ".join(
            f"{label} {'n/a' if ranks[measure] is None else format(ranks[measure], '.3f')}"
            for measure, label in MEASURES.items()
        )
        lines.append(f"{target} ({ranks['days']} days): {scores}")
    beyond = judge["beyond"]
    if beyond:
        lines.append(
            f"Beyond review-weighted on tiring ({beyond['days']} days): partial r "
            f"{beyond['r']:+.3f}, t {beyond['t']:.2f}, the gate needs {beyond['needed']:.2f}."
        )
    return lines


def _template_lines(templates: Sequence[dict]) -> List[str]:
    return ["Front share of each answer:"] + [
        f"{row['template']}: {row['front_share']:.0%} "
        f"({row['front_ms'] / 1000:.1f} s front, {row['back_ms'] / 1000:.1f} s back, "
        f"{row['answers']} answers)"
        for row in templates
    ]


def _reason_lines(reasons: Sequence[dict]) -> List[str]:
    return ["Cost by Again reason:"] + [
        f"{row['reason']}: median {row['median_cost']} ({row['ratings']} ratings)"
        for row in reasons
    ]


def _reactivity_lines(reactivity: dict) -> List[str]:
    labels = {
        "afterwards_vs_baseline": "Pace after the baseline vs during it",
        "after_prompt_vs_others": "Pace right after a prompt vs others",
    }
    lines = []
    for key, label in labels.items():
        pace = reactivity[key]
        lines.append(
            f"{label}: too few comparable answers." if pace is None else
            f"{label}: ×{pace['ratio']:.2f} ({pace['answers']} answers, "
            f"{pace['strata']} strata)."
        )
    return lines
