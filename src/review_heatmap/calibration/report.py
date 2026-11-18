"""The fit and its report, shown in the tab and kept as report-YYYYMMDD.json. Pure.

The fit has two stages. Effort ratings of single answers say which answers
are harder: an ordered logit of them on the model's inputs gives every answer
a difficulty. Ratings of sittings and days say how answers add up to work:
credit.py finds the time exponent b and the weight k of that difficulty.

Controls and time are left out of the difficulty, here as in scoring: what
the judge measures is the model as it would ship. Note-type offsets belong
to the profile fit alone, and only the lines named for them read them.
"""

import json
import math
import os
import time
from collections import Counter
from operator import mul
from pathlib import Path
from statistics import fmean, median, pstdev
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple, Union

from ..metrics import (
    MODEL_ETA_RANGE, MODEL_INPUTS, activity_value, answer_effort,
    concentration_calibration, concentration_coefficients, effort_model_credit,
    fsrs_calibration, load_effort_model,
)
from .credit import (
    DEVIANCE_90, FALLBACK_B, RULE_ENDS, TIME_INPUTS, Settled, Source, fit_credit, settle,
    usable,
)
from .dataset import (
    MIN_WORDING, OFFSET_PREFIX, PACE_DAYS, Answer, Dataset, RatedDay, Tap, design,
    prompt_counts, read_dataset,
)
from .fit import (
    ONE_SIDED_5_PERCENT, OrderedFit, added_t, fit_ordered, mean_log_loss, ordinal_t, predict_eta,
    spearman,
)
from .phone import merge_phone, read_files
from .store import DAY_MS, Store, now_ms

# Simulated on the review log this was built for: from here on a fit's day
# scores are typically within 3 to 7% of the truth behind the ratings.
FIT_EFFORT_RATINGS = 150
FIT_RATED_DAYS = 20
GATE_EFFORT_RATINGS = 300
# A fitted measure that is really better passes about half the time at the
# first, and four times in five at the second.
GATE_RATED_DAYS = 60
FAIR_TRIAL_RATED_DAYS = 90
# The rated days are judged in blocks, in time order: every block but the
# first is scored by a fit on all the days before it, so no day is scored by a
# fit that saw it, and three quarters of them are judged.
JUDGE_BLOCKS = 4
JUDGE_MIN_RATED_DAYS = 32
JUDGE_MIN_BLOCK_DAYS = 5
HOLDOUT_SHARE = 0.2
# Fewer effort ratings than this give no difficulty: every answer then counts alike.
MIN_EFFORT_RATINGS = 20
MEASURES = {
    "count": "Count",
    "recorded_time": "Recorded time",
    "review_weighted": "Review-weighted",
    "fsrs": "FSRS-based",
    "fitted": "Fitted",
    "fitted_offsets": "Fitted with note-type offsets",
}
BUTTONS = ("again", "hard", "easy")
# The kinds of rating credit is fitted to, and the sets the report compares.
SOURCES = ("amount", "pair", "work")
SOURCE_SETS = {
    "amount": "from evenings alone",
    "amount pair": "with pairs",
    "amount pair work": "with sittings too",
}

MODEL_FILE = "model.json"
# Reports of the earlier design, fitted to cost ratings, have none and aren't shown.
REPORT_FORMAT = 2

# Strata with fewer comparable answers on either side tell nothing about pace.
_MIN_STRATUM = 5
_LOG_D, _NEW, _STEP = (MODEL_INPUTS.index(name) for name in ("log_d", "new", "step"))
_SCORED = [name not in TIME_INPUTS for name in MODEL_INPUTS]


class TwoStage(NamedTuple):
    cards: Optional[OrderedFit]  # None: too few effort ratings for a difficulty
    work: Optional[Settled]  # None: too few rated sittings and days
    centre: Tuple[float, float]  # mean log_d and difficulty of the answers work was fitted on
    spread: float  # of their difficulty
    units: Dict[str, int]  # ratings of each kind work was fitted on
    answers: int
    details: Optional[dict]  # only for a fit asked for its interval


def run_fit(store: Store, rows: Sequence[Sequence], conf: Optional[Dict],
            template_names: Dict[Tuple[int, int], str],
            progress: Callable[[str], None], media_dir=None, phone_index=None,
            recall=None, card_templates: Optional[Dict[str, Tuple[int, int]]] = None) -> dict:
    """Read the data, fit, and save the report. Rows as in dataset.revlog_sql;
    card_templates as read_dataset's templates."""
    progress("Reading the calibration events")
    events = list(store.read_events())
    phone = merge_phone(read_files(media_dir), rows, phone_index or {})
    dataset = read_dataset([*events, *phone.events], phone.rows, recall, card_templates)
    version = next_model_version(store.folder)
    report = build_report(dataset, conf, template_names, version, progress)
    report["phone"] = phone.diagnostics
    write_report(store.folder, report)
    return report


def build_report(dataset: Dataset, conf: Optional[Dict],
                 template_names: Dict[Tuple[int, int], str], version: int,
                 progress: Callable[[str], None]) -> dict:
    progress("Fitting the effort ratings")
    shared_cards = _fit_cards(dataset.taps)
    local_cards = _fit_cards(dataset.taps, note_type_offsets=True)
    progress("Fitting the ratings of sittings and days")
    shared = _fit_work(dataset, shared_cards, with_interval=True)
    local = _fit_work(dataset, local_cards)
    progress("Judging on days no fit has seen")
    judge = _judge(dataset, conf)
    progress("Testing each question")
    questions = _questions(dataset, shared, conf)
    progress("Writing the report")
    rated_days = sum("amount" in day.ratings for day in dataset.days)
    counts = prompt_counts(dataset.prompts)
    names = {
        f"template:{mid}:{card_ord}": name for (mid, card_ord), name in template_names.items()
    }
    return {
        "format": REPORT_FORMAT,
        "date_ms": now_ms(),
        "counts": {
            **counts,
            "effort_ratings": len(dataset.taps),
            "rated_days": rated_days,
            "rated_sittings": sum("work" in sitting.ratings for sitting in dataset.sittings),
            "pair_days": sum(day.pair is not None for day in dataset.days),
            "logged": len(dataset.logged),
            "unlogged": dataset.unlogged,
            "undone": dataset.undone,
        },
        "category_use": _category_use(dataset.prompts),
        "skip_rate": counts["skipped"] / counts["prompts"] if counts["prompts"] else 0.0,
        "cards": shared.cards and {
            "ratings": len(dataset.taps),
            "loglik": shared.cards.loglik,
            "iterations": shared.cards.iterations,
            "converged": shared.cards.converged,
            "weights": [
                {
                    "name": name,
                    "label": names.get(name, name),
                    "weight": shared.cards.weights[name],
                    "se": shared.cards.se[name],
                    "control": name in shared.cards.control_names,
                }
                for name in shared.cards.names
            ],
        },
        "note_type_offsets": [
            {
                "label": names.get(name, name),
                "weight": local.cards.weights[name],
                "se": local.cards.se[name],
            }
            for name in (local.cards.control_names if local.cards else ())
            if name.startswith(OFFSET_PREFIX)
        ],
        "work": shared.work and _work_summary(shared),
        "model": model_body(shared, version),
        "local_model": (
            local_model_body(local, version) if _converged(local) else model_body(shared, version)
        ),
        "judge": judge,
        "questions": questions,
        "templates": _front_back(dataset, template_names),
        "reactivity": _reactivity(dataset),
        # Without a fit yet, the gate says so: nothing failed to converge.
        "gate": ship_gate(
            len(dataset.taps), rated_days, judge, shared.work is None or _converged(shared),
        ),
    }


def model_body(fit: TwoStage, version: int) -> Optional[dict]:
    """A body for effort_model.json, or None when there is no fit to score with.

    Its weight on log_d is b, and every other input weighs k times what it
    weighs in the difficulty. The intercept gives an average answer of the
    fitted days a credit of 1: ratings fix no size, and scoring clamps credit
    on either side of 1. It stays "local" until a person ships it, so that
    pasting weights can't release them by accident."""
    if fit.work is None:
        return None
    b, k = fit.work.b, fit.work.k
    weights = {
        name: k * fit.cards.weights[name] if fit.cards and scored else 0.0
        for name, scored in zip(MODEL_INPUTS, _SCORED)
    }
    weights["log_d"] = b
    return {
        "status": "local",
        "version": version,
        "intercept": -(b * fit.centre[0] + k * fit.centre[1]),
        "weights": weights,
    }


def local_model_body(fit: TwoStage, version: int) -> Optional[dict]:
    """The same, keeping a fit's note-type offsets for scoring on this profile.
    Never ship it: other collections have other note types."""
    body = model_body(fit, version)
    if body is None:
        return None
    return {
        **body,
        "offsets": {
            name[len(OFFSET_PREFIX):]: fit.work.k * fit.cards.weights[name]
            for name in (fit.cards.control_names if fit.cards else ())
            if name.startswith(OFFSET_PREFIX)
        },
    }


def ship_gate(effort_ratings: int, rated_days: int, judge: Optional[dict],
              converged: bool) -> dict:
    """The fitted score ships only if, on days no fit of it has seen, it is no
    worse than review-weighted on "amount" and explains ratings review-weighted
    can't.

    Day scores agree so closely that even a perfect fit gains only about 0.02 to
    0.05 in plain Spearman, so a fixed margin would turn away real gains, more
    surely with more days. The test asks about the part where the two differ,
    and gets stronger with data. Failing is a valid result: nothing ships.
    """
    judged = bool(judge and judge["blocks"])
    margin = judge["margin"] if judged else None
    z = judge["z"] if judged else None
    reasons = []
    if effort_ratings < GATE_EFFORT_RATINGS:
        reasons.append(f"{effort_ratings} of {GATE_EFFORT_RATINGS} effort ratings")
    if rated_days < GATE_RATED_DAYS:
        reasons.append(f"{rated_days} of {GATE_RATED_DAYS} rated days")
    if not converged or (judged and not judge["converged"]):
        reasons.append("a fit did not converge")
    if not judged:
        reasons.append("no fit could be judged on later days")
    elif margin < 0:
        reasons.append(f"fitted trails review-weighted on amount by {margin:+.3f}")
    elif z < ONE_SIDED_5_PERCENT:
        reasons.append(
            f"fitted adds too little beyond review-weighted on amount: "
            f"z {z:.2f}, it needs {ONE_SIDED_5_PERCENT:.2f}"
        )
    return {
        "passed": not reasons, "reasons": reasons, "margin": margin, "z": z,
        "needed": ONE_SIDED_5_PERCENT, "rated_days": rated_days,
    }


# The fit
###########################################################################


def _fit_cards(taps: Sequence[Tap], note_type_offsets: bool = False) -> Optional[OrderedFit]:
    """Stage 1: which answers are harder, from the effort ratings."""
    if len(taps) < MIN_EFFORT_RATINGS or len({tap.effort for tap in taps}) < 2:
        return None
    rows, ratings, names, controls = design(taps, note_type_offsets)
    return fit_ordered(rows, ratings, names, control_names=controls)


def _difficulties(cards: Optional[OrderedFit], answers: Sequence[Answer]) -> List[float]:
    """What the effort ratings make of each answer, its duration aside: credit
    counts that once, through b. Controls stay at zero, as in scoring."""
    if cards is None:
        return [0.0] * len(answers)
    weights = [
        cards.weights[name] if scored else 0.0 for name, scored in zip(MODEL_INPUTS, _SCORED)
    ]
    offsets = {
        tuple(map(int, name[len(OFFSET_PREFIX):].split(":"))): cards.weights[name]
        for name in cards.control_names if name.startswith(OFFSET_PREFIX)
    }
    return [
        math.fsum(map(mul, weights, answer.inputs)) + offsets.get(answer.template, 0.0)
        for answer in answers
    ]


def _sources(dataset: Dataset, before: Optional[int], use: Sequence[str]) -> List[Source]:
    """What each kind of rating is about, as positions in dataset.answers.
    With before, only what was rated on earlier study days."""
    days = [day for day in dataset.days if before is None or day.day < before]
    sources = []
    if "amount" in use:
        rated = [day for day in days if "amount" in day.ratings]
        sources.append(Source(
            "amount", [day.ratings["amount"] for day in rated],
            [list(range(*day.span)) for day in rated],
        ))
    if "pair" in use:
        rated = [day for day in days if day.pair]
        sources.append(Source(
            "pair", [day.ratings["pair"] for day in rated],
            [
                [index for index in range(*day.span) if dataset.answers[index].template == side]
                for day in rated for side in day.pair
            ],
            pair=True,
        ))
    if "work" in use:
        rated = [
            sitting for sitting in dataset.sittings
            if "work" in sitting.ratings and (before is None or sitting.day < before)
        ]
        sources.append(Source(
            "work", [sitting.ratings["work"] for sitting in rated],
            [list(range(*sitting.span)) for sitting in rated],
        ))
    return sources


def _fit_work(dataset: Dataset, cards: Optional[OrderedFit], before: Optional[int] = None,
              use: Sequence[str] = SOURCES, with_interval: bool = False) -> TwoStage:
    """Stage 2: how answers add up, from the ratings of sittings and days."""
    sources = usable(_sources(dataset, before, use))
    positions = sorted({index for source in sources for group in source.groups for index in group})
    if not positions:
        return TwoStage(cards, None, (0.0, 0.0), 0.0, {}, 0, None)
    answers = [dataset.answers[index] for index in positions]
    log_d = [answer.inputs[_LOG_D] for answer in answers]
    difficulty = _difficulties(cards, answers)
    centre = fmean(log_d), fmean(difficulty)
    log_d = [value - centre[0] for value in log_d]
    difficulty = [value - centre[1] for value in difficulty]
    place = {index: position for position, index in enumerate(positions)}
    placed = [
        source._replace(groups=[[place[index] for index in group] for group in source.groups])
        for source in sources
    ]
    work = settle(placed, log_d, difficulty, MODEL_ETA_RANGE, with_interval)
    details = None
    if with_interval:
        plain = fit_credit(placed, log_d, difficulty, MODEL_ETA_RANGE, fixed_k=0.0)
        low, high = MODEL_ETA_RANGE
        details = {
            "without_difficulty": 2 * (work.fit.loglik - plain.loglik),
            "clamped": sum(
                not low <= work.b * d + work.k * e <= high for d, e in zip(log_d, difficulty)
            ) / len(answers),
        }
    return TwoStage(
        cards, work, centre, pstdev(difficulty),
        {source.name: len(source.ratings) for source in sources}, len(answers), details,
    )


def _converged(fit: TwoStage) -> bool:
    return bool(fit.work and fit.work.fit.converged and (fit.cards is None or fit.cards.converged))


def _work_summary(fit: TwoStage) -> dict:
    work = fit.work
    return {
        "units": fit.units,
        "answers": fit.answers,
        "converged": work.fit.converged,
        "b": work.fit.b,
        "k": work.fit.k,
        "interval": list(work.interval),
        "fell_back": work.fell_back,
        "b_used": work.b,
        "k_used": work.k,
        # In log credit, between answers of the fitted days.
        "difficulty_spread": work.k * fit.spread,
        **fit.details,
    }


# The judge
###########################################################################


def _judge(dataset: Dataset, conf: Optional[Dict]) -> Optional[dict]:
    """Score the later rated days by fits that never saw them.

    The rated days are cut into blocks in time order, and every block but the
    first is scored by a fit on all that was rated before it. Each block gives
    a t for what fitted adds to review-weighted, and the blocks' t add up
    (Stouffer): about standard normal when there is nothing to add.
    """
    rated = [day for day in dataset.days if "amount" in day.ratings]
    if len(rated) < JUDGE_MIN_RATED_DAYS:
        return None
    starts = [
        rated[round(len(rated) * block / JUDGE_BLOCKS)].day for block in range(1, JUDGE_BLOCKS)
    ]
    blocks = []
    for start, end in zip(starts, [*starts[1:], math.inf]):
        days = [day for day in rated if start <= day.day < end]
        if len(days) < JUDGE_MIN_BLOCK_DAYS:
            continue
        taps = [tap for tap in dataset.taps if tap.answer.day < start]
        shared = _fit_work(dataset, _fit_cards(taps), start)
        if shared.work is None:
            continue
        local = _fit_work(dataset, _fit_cards(taps, note_type_offsets=True), start)
        models = {"fitted": model_body(shared, 0), "fitted_offsets": local_model_body(local, 0)}
        scores = [_day_scores(dataset.answers[slice(*day.span)], models, conf) for day in days]
        blocks.append({
            "days": len(days),
            "train_ratings": len(taps),
            "converged": _converged(shared),
            "b": shared.work.b,
            "k": shared.work.k,
            "fell_back": shared.work.fell_back,
            "amount": _rank_against("amount", days, scores),
            "learned": _rank_against("learned", days, scores),
            "t": ordinal_t(
                [day.ratings["amount"] for day in days],
                [score["review_weighted"] for score in scores],
                [score["fitted"] for score in scores],
            ),
            "versus": _versus_t(dataset, days, scores, models["fitted"], conf),
        })
    amount = _pooled("amount", blocks)
    compared = [block["versus"] for block in blocks if block["versus"]]
    return {
        "rated_days": len(rated),
        "blocks": blocks,
        "amount": amount,
        "learned": _pooled("learned", blocks),
        "margin": (
            amount["fitted"] - amount["review_weighted"] if amount["fitted"] is not None else None
        ),
        "z": sum(block["t"] for block in blocks) / math.sqrt(len(blocks)) if blocks else None,
        "versus": {
            "days": sum(block["days"] for block in compared),
            "z": sum(block["t"] for block in compared) / math.sqrt(len(compared)),
        } if compared else None,
        "converged": all(block["converged"] for block in blocks),
    }


def _rank_against(target: str, days: Sequence[RatedDay], scores: Sequence[dict]) -> dict:
    rated = [(day.ratings[target], score) for day, score in zip(days, scores) if target in day.ratings]
    ratings = [rating for rating, _ in rated]
    result: Dict[str, Union[int, float, None]] = {"days": len(rated)}
    for measure in MEASURES:
        values = [score[measure] for _, score in rated]
        missing = None in values or len(rated) < JUDGE_MIN_BLOCK_DAYS
        result[measure] = None if missing else spearman(values, ratings)
    return result


def _pooled(target: str, blocks: Sequence[dict]) -> dict:
    """Each measure's rank correlation with a rating, averaged over the blocks
    by their days: scores of different blocks' fits don't share a scale."""
    result: Dict[str, Union[int, float, None]] = {
        "days": sum(block[target]["days"] for block in blocks),
    }
    for measure in MEASURES:
        parts = [
            (block[target][measure], block[target]["days"]) for block in blocks
            if block[target][measure] is not None
        ]
        days = sum(weight for _, weight in parts)
        # A measure missing from one block can't be compared with the others.
        complete = parts and len(parts) == sum(
            block[target]["review_weighted"] is not None for block in blocks
        )
        result[measure] = (
            sum(value * weight for value, weight in parts) / days if complete else None
        )
    return result


def _versus_t(dataset: Dataset, days: Sequence[RatedDay], scores: Sequence[dict],
              model: dict, conf: Optional[Dict]) -> Optional[dict]:
    """The same test on "versus": today's work against a named earlier day's.
    It needs no stable sense of a usual day."""
    ratings, weighted, fitted = [], [], []
    for day, score in zip(days, scores):
        if day.versus is None:
            continue
        other = _day_scores(dataset.answers[slice(*day.versus)], {"fitted": model}, conf)
        if min(other["review_weighted"], other["fitted"], score["review_weighted"]) <= 0:
            continue
        ratings.append(day.ratings["versus"])
        weighted.append(score["review_weighted"] / other["review_weighted"])
        fitted.append(score["fitted"] / other["fitted"])
    if len(ratings) < JUDGE_MIN_BLOCK_DAYS:
        return None
    return {"days": len(ratings), "t": ordinal_t(ratings, weighted, fitted)}


def _day_scores(answers: Sequence[Answer], models: Dict[str, Optional[dict]],
                conf: Optional[Dict]) -> dict:
    """Every measure as the calendar would score these answers."""
    reviews = len(answers)
    milliseconds = sum(answer.duration_ms for answer in answers)
    effort = fsrs_calibration(conf)
    scores = {
        "count": float(reviews),
        "recorded_time": activity_value(reviews, milliseconds, "recorded_time", conf=conf),
        "review_weighted": activity_value(reviews, milliseconds, "workload", [
            (answer.duration_ms, 1) for answer in answers
        ], conf),
        "fsrs": activity_value(reviews, milliseconds, "fsrs", [
            (
                answer.duration_ms, 1,
                answer_effort(bool(answer.inputs[_NEW]), answer.last_ivl, *answer.recall, effort),
            )
            for answer in answers
        ], conf),
        "fitted": None,
        "fitted_offsets": None,
    }
    for measure, model in models.items():
        if model is not None:
            scores[measure] = activity_value(reviews, milliseconds, "effort_model", [
                (
                    answer.duration_ms, 1,
                    effort_model_credit(dict(zip(MODEL_INPUTS, answer.inputs)), model, answer.template),
                )
                for answer in answers
            ], conf)
    return scores


# The questions
###########################################################################


def _questions(dataset: Dataset, shared: TwoStage, conf: Optional[Dict]) -> dict:
    """What each question shows so far: a question that shows nothing can stop
    being asked. The evening's "amount" is the judge and has no such test."""
    taps = dataset.taps
    return {
        "effort": _held_out(taps, "effort"),
        "gained": _held_out(taps, "gained"),
        "know": _held_out(taps, "know"),
        "fresh": _held_out(taps, "fresh"),
        "richness": _richness(dataset, shared),
        "detail": _answer_detail(taps),
        "familiar": _control(shared.cards, "familiar", ["familiar" in tap.answers for tap in taps]),
        "focus": _control(shared.cards, "focus", [tap.focus is not None for tap in taps]),
        "wrong": _reasons(taps),
        "state": _state(dataset, conf),
        "versus": _versus_agreement(dataset),
        "sources": _source_sets(dataset, shared),
        "deep": _deep(dataset, conf),
    }


# A note's richness, asked once, counts for every tap of its card: fewer than this says nothing.
MIN_RICHNESS_TAPS = 30


def _richness(dataset: Dataset, shared: TwoStage) -> Optional[dict]:
    """Whether the owner's forecast of a note's total work says more about how hard its card
    felt than the inputs do: the t of richness beside the difficulty index, on every tap of a
    card whose note was rated. It is never a scoring input: no other collection has it."""
    sheets = [event for event in dataset.prompts if event["kind"] == "answer"]
    card_of = {event.get("rev_id"): event.get("cid") for event in sheets}
    rated = {event.get("cid"): event["answers"]["richness"] for event in sheets
             if "richness" in event.get("answers", {})}
    if shared.cards is None or not rated:
        return None
    rows, ratings, _, _ = design(dataset.taps)
    chosen = [(row, rating, rated[card_of.get(tap.answer.rev_id)])
              for tap, row, rating in zip(dataset.taps, rows, ratings)
              if card_of.get(tap.answer.rev_id) in rated]
    found = {"taps": len(chosen), "notes": len(rated), "t": None}
    if len(chosen) >= MIN_RICHNESS_TAPS:
        index = [predict_eta(shared.cards, row) for row, _, _ in chosen]
        found["t"] = added_t([rating for _, rating, _ in chosen],
                             [index, [float(value) for _, _, value in chosen]])
    return found


def _answer_detail(taps: Sequence[Tap]) -> dict:
    """Per question, from wording 6 on: answers given half a step between two levels, and
    how often and how sure the owner rated their own answer."""
    found: Dict[str, dict] = {}
    for tap in taps:
        for question, value in tap.answers.items():
            row = found.setdefault(question, {"answers": 0, "half_steps": 0, "sure": []})
            row["answers"] += 1
            row["half_steps"] += isinstance(value, float) and value != int(value)
            if question in tap.confidence:
                row["sure"].append(tap.confidence[question])
    return {
        question: {"answers": row["answers"], "half_steps": row["half_steps"],
                   "rated": len(row["sure"]), "median": median(row["sure"]) if row["sure"] else None}
        for question, row in sorted(found.items())
    }


def _later_share(days: Sequence[int], share: float) -> Optional[int]:
    """First day of the later share of days, leaving some earlier ones."""
    if len(days) < 2:
        return None
    return days[-max(1, round(len(days) * share))]


def _held_out(taps: Sequence[Tap], question: str) -> Optional[dict]:
    """How well the later answers to a question are predicted from the earlier
    ones: by the share of each level alone, by the button, and by every input."""
    asked = [tap for tap in taps if question in tap.answers]
    first_held = _later_share(sorted({tap.answer.day for tap in asked}), HOLDOUT_SHARE)
    train = [tap for tap in asked if first_held is not None and tap.answer.day < first_held]
    held = [tap for tap in asked if first_held is not None and tap.answer.day >= first_held]
    ratings = [tap.answers[question] for tap in train]
    if len(train) < MIN_EFFORT_RATINGS or len(set(ratings)) < 2:
        return None
    columns = {
        "levels": [],
        "buttons": [MODEL_INPUTS.index(name) for name in BUTTONS],
        "inputs": list(range(len(MODEL_INPUTS))),
    }
    losses = {}
    for name, chosen in columns.items():
        fit = fit_ordered(
            [[tap.answer.inputs[index] for index in chosen] for tap in train], ratings,
            [MODEL_INPUTS[index] for index in chosen],
        )
        losses[name] = mean_log_loss(
            fit, [[tap.answer.inputs[index] for index in chosen] for tap in held],
            [tap.answers[question] for tap in held],
        )
    return {
        "ratings": len(held),
        "train_ratings": len(train),
        "days": len({tap.answer.day for tap in held}),
        "log_loss": losses,
    }


def _control(cards: Optional[OrderedFit], name: str, answered: Sequence[bool]) -> Optional[dict]:
    if cards is None:
        return None
    return {"ratings": sum(answered), "weight": cards.weights[name], "se": cards.se[name]}


def _reasons(taps: Sequence[Tap]) -> dict:
    """Effort by what went wrong on a failed card. Several reasons can be given at once."""
    failed = [tap for tap in taps if "wrong" in tap.answers]
    given = Counter(reason for tap in failed for reason in tap.answers["wrong"])
    reasons = []
    for reason, count in given.most_common():
        others = [tap.effort for tap in failed if reason not in tap.answers["wrong"]]
        reasons.append({
            "reason": reason,
            "ratings": count,
            "share": count / len(failed),
            "effort": fmean(tap.effort for tap in failed if reason in tap.answers["wrong"]),
            "others_effort": fmean(others) if others else None,
        })
    return {"ratings": len(failed), "reasons": reasons}


def _state(dataset: Dataset, conf: Optional[Dict]) -> dict:
    """Whether the rest of the day shows where it should, in how hard the study
    felt, and stays out of how much study it was."""
    both = [
        (day.ratings["rest"], day.ratings["strain"]) for day in dataset.days
        if "rest" in day.ratings and "strain" in day.ratings
    ]
    amount, weighted, rest = [], [], []
    for day in dataset.days:
        if "rest" in day.ratings and "amount" in day.ratings:
            score = _day_scores(dataset.answers[slice(*day.span)], {}, conf)["review_weighted"]
            if score > 0:
                amount.append(day.ratings["amount"])
                weighted.append(math.log(score))
                rest.append(day.ratings["rest"])
    return {
        "rest_strain": {
            "days": len(both),
            "spearman": spearman(*zip(*both)) if len(both) >= 3 else None,
        },
        "rest_amount": {
            "days": len(rest),
            "t": added_t(amount, [weighted, rest]) if len(rest) >= JUDGE_MIN_BLOCK_DAYS else None,
        },
    }


def _versus_agreement(dataset: Dataset) -> dict:
    """How often "versus" says what the two days' "amount" already said."""
    amounts = {day.day: day.ratings["amount"] for day in dataset.days if "amount" in day.ratings}
    agreed = []
    for day in dataset.days:
        other = dataset.answers[day.versus[0]].day if day.versus else None
        if day.day in amounts and other in amounts:
            said = day.ratings["versus"] - 3
            implied = amounts[day.day] - amounts[other]
            agreed.append((said > 0) - (said < 0) == (implied > 0) - (implied < 0))
    return {"days": len(agreed), "agree": sum(agreed) / len(agreed) if agreed else None}


def _source_sets(dataset: Dataset, shared: TwoStage) -> List[dict]:
    """b from the evenings alone, then with each further kind of rating: what
    pairs and sittings add to placing it."""
    sets = []
    for names, label in SOURCE_SETS.items():
        use = names.split()
        fit = shared if use == list(SOURCES) else _fit_work(
            dataset, shared.cards, use=use, with_interval=True,
        )
        if fit.work:
            sets.append({
                "label": label, "units": fit.units, "b": fit.work.fit.b,
                "interval": list(fit.work.interval),
            })
    return sets


def _deep(dataset: Dataset, conf: Optional[Dict]) -> dict:
    """"deep" against what sustained concentration makes of the same sittings:
    the share of a sitting's time on answers with a coefficient above 1."""
    settings = concentration_calibration(conf)
    rated, shares = [], []
    for sitting in dataset.sittings:
        answers = dataset.answers[slice(*sitting.span)]
        total = sum(answer.duration_ms for answer in answers)
        if "deep" not in sitting.ratings or not total:
            continue
        coefficients = concentration_coefficients(
            [(answer.rev_id, answer.duration_ms, answer.norm_ms) for answer in answers], settings,
        )
        rated.append(sitting.ratings["deep"])
        shares.append(sum(
            answer.duration_ms for answer, coefficient in zip(answers, coefficients)
            if coefficient > 1
        ) / total)
    return {
        "sittings": len(rated),
        "spearman": spearman(shares, rated) if len(rated) >= 3 else None,
    }


# Other sections
###########################################################################


def _category_use(prompts: Sequence[dict]) -> Dict[str, Dict[str, int]]:
    """How often each answer of each question was chosen, in the fitted wording."""
    use: Dict[str, Counter] = {}
    for event in prompts:
        if event.get("wording_v", 0) < MIN_WORDING:
            continue
        for question, value in event["answers"].items():
            # A question answered with several choices counts each of them.
            use.setdefault(question, Counter()).update(value if isinstance(value, list) else [value])
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


def _reactivity(dataset: Dataset) -> dict:
    """Pace ratios on comparable answers: the same template, button and phase.
    Above 1, questions go with slower answers.

    Answers since the first question are compared with the PACE_DAYS before
    it, on the revlog's own times, and the answer right after a question with
    the others, on what the recordings timed."""
    began = dataset.surveys_began or 0
    groups: Dict[str, Dict[tuple, List[int]]] = {
        "before": {}, "since": {}, "after_prompt": {}, "others": {},
    }
    for answer in dataset.answers:
        if answer.duration_ms > 0 and answer.rev_id >= began - PACE_DAYS * DAY_MS:
            phase = "new" if answer.inputs[_NEW] else "step" if answer.inputs[_STEP] else "review"
            side = "since" if answer.rev_id >= began else "before"
            groups[side].setdefault((answer.template, answer.ease, phase), []).append(
                answer.duration_ms
            )
    for answer in dataset.logged:
        if answer.taken_ms and answer.rev_id >= began:
            side = "after_prompt" if answer.after_prompt else "others"
            groups[side].setdefault((answer.template, answer.ease, answer.phase), []).append(
                answer.taken_ms
            )
    return {
        "since_vs_before": _pace_ratio(groups["since"], groups["before"]),
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
        *(report["model"]["version"] for report in reports if report and report.get("model")),
    )


def write_report(folder: Path, report: dict) -> Path:
    path = Path(folder) / time.strftime("report-%Y%m%d.json")
    _write_json(path, report)
    return path


def latest_report(folder: Path) -> Optional[dict]:
    """The newest report, unless it is damaged or of the earlier design."""
    path = max(Path(folder).glob("report-*.json"), default=None)
    report = path and _read_json(path)
    return report if report and report.get("format") == REPORT_FORMAT else None


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
        return (
            f"Ship gate: PASSED, fitted adds to review-weighted on amount "
            f"(z {gate['z']:.2f}, it needed {gate['needed']:.2f})."
        )
    text = "Ship gate: NOT PASSED, nothing ships. " + "; ".join(gate["reasons"]) + "."
    if GATE_RATED_DAYS <= gate["rated_days"] < FAIR_TRIAL_RATED_DAYS:
        text += (
            f" Under {FAIR_TRIAL_RATED_DAYS} rated days a real gain fails this test about "
            "half the time: keep rating days, and fit again."
        )
    return text


def format_report(report: dict) -> str:
    return "\n".join((
        *_counts_lines(report),
        "",
        *_cards_lines(report["cards"]),
        "",
        *_offset_lines(report["note_type_offsets"]),
        "",
        *_work_lines(report["work"]),
        "",
        *_judge_lines(report["judge"]),
        "",
        *_question_lines(report["questions"]),
        "",
        *_template_lines(report["templates"]),
        "",
        *_reactivity_lines(report["reactivity"]),
    ))


def _counts_lines(report: dict) -> List[str]:
    counts = report["counts"]
    lines = [
        f"Effort ratings: {counts['effort_ratings']}. Rated days: {counts['rated_days']}. "
        f"Rated sittings: {counts['rated_sittings']}. Days with a pair: {counts['pair_days']}. "
        f"Skip rate: {report['skip_rate']:.0%}.",
        f"Answers logged: {counts['logged']}, unlogged: {counts['unlogged']}, "
        f"undone: {counts['undone']}.",
        *(
            f"{question.capitalize()} use: "
            + "  ".join(f"{value}: {count}" for value, count in values.items())
            for question, values in report["category_use"].items()
        ),
    ]
    if counts["earlier"]:
        lines.append(f"Prompts of earlier wordings, kept and not fitted: {counts['earlier']}.")
    return lines


def _cards_lines(cards: Optional[dict]) -> List[str]:
    if cards is None:
        return [
            f"Cards: no difficulty yet, it takes {MIN_EFFORT_RATINGS} effort ratings on two "
            "levels or more. Until then every answer counts alike."
        ]
    converged = "converged" if cards["converged"] else "DID NOT CONVERGE"
    return [
        f"Cards, which answers are harder, on {cards['ratings']} effort ratings: {converged}.",
        *(
            f"{row['label']}: {row['weight']:+.3f} ± {row['se']:.3f}"
            + (" (control)" if row["control"] else "")
            + (" (time, scored through b)" if row["name"] in TIME_INPUTS else "")
            for row in cards["weights"]
        ),
    ]


def _offset_lines(offsets: Sequence[dict]) -> List[str]:
    """From the fit kept on this profile. They never ship."""
    if not offsets:
        return ["Note-type offsets: no template has 30 ratings yet."]
    return ["Note-type offsets, this profile only:"] + [
        f"{row['label']}: {row['weight']:+.3f} ± {row['se']:.3f}" for row in offsets
    ]


def _work_lines(work: Optional[dict]) -> List[str]:
    if work is None:
        return ["Work: no fit yet, it takes 5 rated days or sittings on two levels or more."]
    kinds = (("amount", "days"), ("work", "sittings"), ("pair", "pairs"))
    units = ", ".join(
        f"{work['units'][name]} {label}" for name, label in kinds if name in work["units"]
    )
    converged = "converged" if work["converged"] else "DID NOT CONVERGE"
    low, high = work["interval"]
    lines = [
        f"Work, how answers add up, on {units}: {converged}.",
        f"Time exponent b: {work['b']:.2f}, 90% range {low:.2f} to {high:.2f}. "
        f"Review-weighted uses {FALLBACK_B}.",
    ]
    if work["fell_back"]:
        lines.append(
            f"That range holds both {RULE_ENDS[0]} and {RULE_ENDS[1]}: these ratings can't "
            f"tell a count from time. b stays at {FALLBACK_B}, and only the difficulty is fitted."
        )
    lines.append(
        f"Difficulty weight k: {work['k_used']:.2f}. An answer one standard deviation harder "
        f"counts ×{math.exp(work['difficulty_spread']):.2f}. Without difficulty the fit loses "
        f"{max(0.0, work['without_difficulty']):.1f} of deviance, where 3.8 is p 0.05."
    )
    if work["clamped"]:
        lines.append(f"Answers at a limit of credit: {work['clamped']:.1%}.")
    return lines


def _judge_lines(judge: Optional[dict]) -> List[str]:
    if judge is None:
        return [f"Judge: it takes {JUDGE_MIN_RATED_DAYS} rated days."]
    blocks = judge["blocks"]
    if not blocks:
        return ["Judge: no fit could be judged on later days."]
    lines = [
        f"Judge, {judge['amount']['days']} of {judge['rated_days']} rated days in "
        f"{len(blocks)} blocks, each scored by a fit on the days before it:"
    ]
    for target in ("amount", "learned"):
        ranks = judge[target]
        scores = ", ".join(
            f"{label} {'n/a' if ranks[measure] is None else format(ranks[measure], '.3f')}"
            for measure, label in MEASURES.items()
        )
        lines.append(f"{target} ({ranks['days']} days): {scores}")
    lines.append(
        f"Beyond review-weighted on amount: z {judge['z']:.2f}, the gate needs "
        f"{ONE_SIDED_5_PERCENT:.2f}. By block: "
        + ", ".join(format(block["t"], "+.2f") for block in blocks) + "."
    )
    versus = judge["versus"]
    if versus:
        lines.append(
            f"Beyond review-weighted on versus ({versus['days']} days): z {versus['z']:.2f}."
        )
    lines.append("The blocks' fits: " + "; ".join(
        f"b {block['b']:.2f}{' kept' if block['fell_back'] else ''}, k {block['k']:.2f}, "
        f"{block['train_ratings']} effort ratings"
        for block in blocks
    ) + ".")
    return lines


def _question_lines(questions: dict) -> List[str]:
    lines = []
    for question in ("effort", "gained", "know", "fresh"):
        held = questions.get(question)
        if held:
            loss = held["log_loss"]
            lines.append(
                f"{question}, log loss on the later {HOLDOUT_SHARE:.0%} of its days "
                f"({held['ratings']} ratings on {held['days']} days): {loss['inputs']:.3f} from "
                f"every input, {loss['buttons']:.3f} from the button, {loss['levels']:.3f} "
                "from neither."
            )
    for control in ("familiar", "focus"):
        row = questions[control]
        if row and row["ratings"]:
            lines.append(
                f"{control} in effort: {row['weight']:+.3f} ± {row['se']:.3f} "
                f"({row['ratings']} ratings)."
            )
    richness = questions.get("richness")
    if richness and richness["t"] is not None:
        lines.append(
            f"richness in effort, beyond the inputs: t {richness['t']:+.2f} on {richness['taps']} "
            f"ratings of {richness['notes']} rated notes."
        )
    elif richness:
        lines.append(f"richness: {richness['notes']} notes rated, {richness['taps']} effort "
                     f"ratings on their cards, too few to test.")
    detail = [
        f"{question} {row['half_steps']}/{row['answers']} half steps"
        + (f", {row['rated']} sure at median {row['median']:.0f}%" if row["rated"] else "")
        for question, row in (questions.get("detail") or {}).items()
        if row["half_steps"] or row["rated"]
    ]
    if detail:
        lines.append("Half steps and confidence: " + "; ".join(detail) + ".")
    wrong = questions["wrong"]
    if wrong["ratings"]:
        lines.append(f"wrong, on {wrong['ratings']} failed cards: " + "; ".join(
            f"{row['reason']} {row['share']:.0%}, effort {row['effort']:.1f}"
            + ("" if row["others_effort"] is None else f" against {row['others_effort']:.1f}")
            for row in wrong["reasons"]
        ) + ".")
    state = questions["state"]
    if state["rest_strain"]["spearman"] is not None:
        lines.append(
            f"rest with strain: rank correlation {state['rest_strain']['spearman']:+.2f} "
            f"on {state['rest_strain']['days']} days."
        )
    if state["rest_amount"]["t"] is not None:
        lines.append(
            f"rest in amount, beyond the work done: t {state['rest_amount']['t']:+.2f} "
            f"on {state['rest_amount']['days']} days."
        )
    versus = questions["versus"]
    if versus["days"]:
        lines.append(
            f"versus agrees with the amount of its two days on {versus['agree']:.0%} "
            f"of {versus['days']} days."
        )
    for row in questions["sources"]:
        low, high = row["interval"]
        lines.append(f"b {row['label']}: {row['b']:.2f}, 90% range {low:.2f} to {high:.2f}.")
    deep = questions["deep"]
    if deep["spearman"] is not None:
        lines.append(
            f"deep with the share of a sitting that sustained concentration rewards: rank "
            f"correlation {deep['spearman']:+.2f} on {deep['sittings']} sittings."
        )
    return ["Questions:", *lines] if lines else ["Questions: nothing to test yet."]


def _template_lines(templates: Sequence[dict]) -> List[str]:
    return ["Front share of each answer:"] + [
        f"{row['template']}: {row['front_share']:.0%} "
        f"({row['front_ms'] / 1000:.1f} s front, {row['back_ms'] / 1000:.1f} s back, "
        f"{row['answers']} answers)"
        for row in templates
    ]


def _reactivity_lines(reactivity: dict) -> List[str]:
    labels = {
        "since_vs_before": f"Pace since the first question vs the {PACE_DAYS} days before it",
        "after_prompt_vs_others": "Pace right after a question vs others",
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
