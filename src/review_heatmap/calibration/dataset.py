"""Rated answers, from calibration events and revlog rows. Pure.

Inputs only ever come from metrics.answer_inputs, fed every answer of the
collection the way scoring feeds it, so training and scoring cannot drift
apart. The revlog decides what happened: an answer undone since it was logged
has no row any more, and takes its rating with it. No undo pairing is needed.
"""

from bisect import bisect_left, bisect_right
from collections import Counter
from statistics import fmean
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from ..metrics import (
    MODEL_INPUTS, MODEL_TEMPLATE_DAYS, CardHistory, answer_inputs, template_profiles,
)
from ..times import study_day_sql
from .store import DAY_MS

# Rows as revlog_sql selects them: answer_inputs' columns, then the factor.
CID, ID, EASE, DURATION, LAST_IVL, MID, ORD, DAY, FACTOR = range(9)

# From this wording on, cost and why sit on sliders. Earlier prompts offered
# every other position of those same scales.
SLIDER_WORDING = 4
MIN_TEMPLATE_RATINGS = 30
DRIFT_DAYS = 90
OFFSET_PREFIX = "template:"

_HOUR_MS = 3_600_000

Template = Tuple[int, int]


class RatedAnswer(NamedTuple):
    rev_id: int
    day: int
    template: Optional[Template]  # None: the card was deleted since
    cost: int  # position on the ¼ ⅓ ½ ¾ 1 1½ 2 3 4 6 8+ scale
    why: Optional[int]
    inputs: Dict[str, float]
    sharpness: Optional[int]  # as rated, for the block holding this answer
    drift: float  # periods of DRIFT_DAYS since the first rated answer


class LoggedAnswer(NamedTuple):
    rev_id: int
    template: Optional[Template]
    ease: int
    phase: str  # new, step or review
    taken_ms: Optional[int]  # uncapped; None if recording missed the answer's start
    front_ms: Optional[int]
    back_ms: Optional[int]
    after_prompt: bool  # the card right after an answer prompt


class DayAnswer(NamedTuple):
    duration_ms: int
    last_ivl: int
    factor: int
    logged: bool  # False: answered elsewhere, or while the recorder was off
    inputs: Dict[str, float]
    template: Optional[Template]


class RatedDay(NamedTuple):
    day: int
    tiring: Optional[int]
    learned: Optional[int]
    answers: List[DayAnswer]


class Dataset(NamedTuple):
    rated: List[RatedAnswer]
    days: List[RatedDay]
    logged: List[LoggedAnswer]
    prompts: List[dict]
    unlogged: int  # revlog answers since recording began without an ans event
    undone: int  # ans events whose revlog row is gone


class _Log(NamedTuple):
    shown: Dict[tuple, List[Optional[int]]]  # (run, seq) -> [q ts, a ts]
    answers: Dict[int, dict]  # by rev_id
    prompts: List[dict]


def revlog_sql(offset: int) -> str:
    """Every revlog row, oldest first, as scoring reads them plus the factor."""
    # WARNING: keep the leading SELECT (see ActivityReporter.set_collection).
    return f"""\
SELECT cid, revlog.id, ease, COALESCE(time, 0),
CASE WHEN lastIvl >= 1 THEN lastIvl ELSE 0 END, notes.mid, cards.ord,
{study_day_sql("revlog.id / 1000", offset)}, COALESCE(revlog.factor, 0)
FROM revlog LEFT JOIN cards ON cards.id = cid LEFT JOIN notes ON notes.id = cards.nid
ORDER BY revlog.id"""


def read_dataset(events: Iterable[dict], rows: Sequence[Sequence],
                 gap_hours: float = 2) -> Dataset:
    """Join events to rows (see revlog_sql), which must hold the whole revlog:
    reps, elapsed and template norms all read answers nobody rated."""
    log = _read_log(events)
    answers = [row for row in rows if row[EASE] >= 1]  # 0 is a reschedule
    ids = [row[ID] for row in answers]
    day_ratings = _day_ratings(log.prompts, answers, ids)
    sharpness = _block_sharpness(log.prompts, ids, gap_hours * _HOUR_MS)
    template_rows = Counter(
        (row[MID], row[ORD], row[DAY], row[DURATION] // 1000)
        for row in answers if row[MID] is not None
    )
    profiles = template_profiles(
        sorted((*row, count) for row, count in template_rows.items()), MODEL_TEMPLATE_DAYS,
    )
    ratings = {
        event["rev_id"]: event["answers"] for event in log.prompts
        if event["kind"] == "answer" and "cost" in event["answers"]
    }
    prompted = {
        (event["run"], event["seq"]) for event in log.prompts if event["kind"] == "answer"
    }
    recording_began = min(log.answers, default=None)

    history = CardHistory()
    rated: List[RatedAnswer] = []
    logged: List[LoggedAnswer] = []
    day_answers: Dict[int, List[DayAnswer]] = {day: [] for day in day_ratings}
    unlogged = 0
    for row in answers:
        inputs = answer_inputs(row[:FACTOR], history, profiles)
        rev_id, day = row[ID], row[DAY]
        template = None if row[MID] is None else (row[MID], row[ORD])
        event = log.answers.get(rev_id)
        if event:
            logged.append(_logged_answer(event, row, template, inputs, log, prompted))
        elif recording_began is not None and rev_id > recording_began:
            unlogged += 1
        if day in day_answers:
            day_answers[day].append(DayAnswer(
                row[DURATION], row[LAST_IVL], row[FACTOR], bool(event), inputs, template,
            ))
        rating = ratings.get(rev_id)
        if rating:
            drift_ms = rev_id - (rated[0].rev_id if rated else rev_id)
            rated.append(RatedAnswer(
                rev_id, day, template, rating["cost"], rating.get("why"), inputs,
                sharpness(rev_id), drift_ms / (DRIFT_DAYS * DAY_MS),
            ))
    known = set(ids)
    return Dataset(
        rated=rated,
        days=[
            RatedDay(day, rating.get("tiring"), rating.get("learned"), day_answers[day])
            for day, rating in sorted(day_ratings.items())
        ],
        logged=logged,
        prompts=log.prompts,
        unlogged=unlogged,
        undone=sum(rev_id not in known for rev_id in log.answers),
    )


def design(rated: Sequence[RatedAnswer], note_type_offsets: bool = False) -> tuple:
    """(rows, ratings, names, control_names) for fit_ordered.

    Model inputs come first, so a row cut to them still lines up with the
    names. Sharpness and drift explain the ratings and stay out of scoring.
    Both are centred, so scoring them at 0 means an average block at an average
    moment. An unrated block sits at the mean: it says nothing either way.

    Offsets are for a fit that stays on its own profile, which scores with them.
    A shared fit must leave them out: they soak up what note types differ by,
    and dropping them afterwards scored worse than never fitting them.
    """
    offsets: List[Template] = []
    if note_type_offsets:
        counts = Counter(answer.template for answer in rated if answer.template)
        offsets = sorted(
            template for template, count in counts.items() if count >= MIN_TEMPLATE_RATINGS
        )
    controls = ["sharpness", "drift"]
    controls += [f"{OFFSET_PREFIX}{mid}:{card_ord}" for mid, card_ord in offsets]
    given = [answer.sharpness for answer in rated if answer.sharpness is not None]
    center = fmean(given) if given else 0.0
    drift_center = fmean(answer.drift for answer in rated) if rated else 0.0
    rows = []
    for answer in rated:
        sharpness = 0.0 if answer.sharpness is None else answer.sharpness - center
        rows.append([
            *model_row(answer), sharpness, answer.drift - drift_center,
            *(float(answer.template == template) for template in offsets),
        ])
    return rows, [answer.cost for answer in rated], [*MODEL_INPUTS, *controls], controls


def model_row(answer: RatedAnswer) -> List[float]:
    return [answer.inputs[name] for name in MODEL_INPUTS]


def _read_log(events: Iterable[dict]) -> _Log:
    shown: Dict[tuple, List[Optional[int]]] = {}
    answers: Dict[int, dict] = {}
    prompts: List[dict] = []
    for event in events:
        kind = event["t"]
        if kind == "q":
            # A redraw of the same card repeats the hooks: the first one counts.
            shown.setdefault((event["run"], event["seq"]), [event["ts"], None])
        elif kind == "a":
            times = shown.get((event["run"], event["seq"]))
            if times and times[1] is None:
                times[1] = event["ts"]
        elif kind == "ans":
            if event["rev_id"]:
                answers[event["rev_id"]] = event
        elif kind == "prompt":
            prompts.append(_on_slider_scales(event))
    return _Log(shown, answers, prompts)


def _on_slider_scales(event: dict) -> dict:
    if event.get("wording_v", SLIDER_WORDING) >= SLIDER_WORDING:
        return event
    answers = {
        question: value * 2 if question in ("cost", "why") else value
        for question, value in event["answers"].items()
    }
    return {**event, "answers": answers}


def _logged_answer(event, row, template, inputs, log, prompted) -> LoggedAnswer:
    run, seq = event["run"], event["seq"]
    shown_at, flipped_at = log.shown.get((run, seq), (None, None))
    timed = shown_at is not None and flipped_at is not None
    return LoggedAnswer(
        row[ID],
        template,
        row[EASE],
        "new" if inputs["new"] else "step" if inputs["step"] else "review",
        event["taken_raw_ms"],
        flipped_at - shown_at if timed else None,
        event["ts"] - flipped_at if timed else None,
        seq is not None and (run, seq - 1) in prompted,
    )


def _day_ratings(prompts, answers, ids) -> Dict[int, dict]:
    """Ratings by the revlog's study day, so that a day is scored on the same
    answers as on the calendar. The prompt picked its day from the last answer
    before day_end_ms, so that answer names it, whatever the clocks did."""
    ratings = {}
    for event in prompts:
        if event["kind"] != "day" or not event["answers"]:
            continue
        end = event["day_end_ms"]
        last = bisect_left(ids, end) - 1
        if last >= 0 and ids[last] >= end - DAY_MS:
            ratings[answers[last][DAY]] = event["answers"]
    return ratings


def _block_sharpness(prompts, ids, gap_ms):
    """A lookup from answer to the sharpness rated for its study block."""
    starts = [
        answer for answer, previous in zip(ids, [None, *ids])
        if previous is None or answer - previous >= gap_ms
    ]
    by_block = {}
    for event in prompts:
        rating = event["answers"].get("sharpness")
        if rating is None:
            continue
        moment = event["ts"]
        if not event["late"]:
            # Asked before the first card: it belongs to the block that follows,
            # unless the owner left without answering anything.
            following = bisect_left(ids, moment)
            if following == len(ids) or ids[following] - moment >= gap_ms:
                continue
            moment = ids[following]
        by_block[bisect_right(starts, moment)] = rating
    return lambda rev_id: by_block.get(bisect_right(starts, rev_id))
