"""Rated answers, sittings and days, from calibration events and revlog rows. Pure.

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

# Rows as revlog_sql selects them: answer_inputs' columns, then the factor,
# which nothing reads since the FSRS-based measure dropped difficulty.
CID, ID, EASE, DURATION, LAST_IVL, MID, ORD, DAY, FACTOR = range(9)

# From this wording on, a card is asked how hard it made its owner think, and
# sittings and days how much work they were. Earlier wordings asked for a cost
# in routine reviews: those ratings followed the button, not the time, and no
# fit reads them.
MIN_WORDING = 5
MIN_TEMPLATE_RATINGS = 30
DRIFT_DAYS = 90
OFFSET_PREFIX = "template:"
# Answers are kept from this long before the first question: the pace check
# compares with them.
PACE_DAYS = 30
# A sitting's bounds say when its events reached the recording tool, a little
# after they happened.
SITTING_SLACK_MS = 5 * 60_000

Template = Tuple[int, int]
# Answers as Dataset.answers[start:stop].
Span = Tuple[int, int]


class Answer(NamedTuple):
    rev_id: int
    day: int
    template: Optional[Template]  # None: the card was deleted since
    ease: int
    duration_ms: int
    last_ivl: int
    recall: Tuple[float, float]  # (recall, same_day_recall) gaps, unclamped
    logged: bool  # False: answered elsewhere, or while the recorder was off
    norm_ms: Optional[int]  # the template's time-weighted median before that day, if any
    inputs: Tuple[float, ...]  # in the order of MODEL_INPUTS


class Tap(NamedTuple):
    """An answer rated on effort once it was given."""
    answer: Answer
    effort: int  # 1 is None, 6 Maximal
    answers: Dict[str, object]  # all its sheet got: effort, familiar, gained, know, wrong
    know_q: Optional[str]  # which of its two wordings "know" was asked in
    focus: Optional[int]  # as rated for the sitting holding this answer
    drift: float  # periods of DRIFT_DAYS since the first rated answer


class Sitting(NamedTuple):
    span: Span
    day: int  # study day of its last answer
    ratings: Dict[str, int]  # work, focus and deep, as far as answered


class RatedDay(NamedTuple):
    day: int
    span: Span  # up to the rating: what was answered after it wasn't rated
    ratings: Dict[str, int]  # amount, strain, rest, versus, pair and learned, as far as answered
    pair: Optional[Tuple[Template, Template]]  # what "pair" compares: it grows with the second
    versus: Optional[Span]  # the whole day "versus" compares this one with


class LoggedAnswer(NamedTuple):
    rev_id: int
    template: Optional[Template]
    ease: int
    phase: str  # new, step or review
    taken_ms: Optional[int]  # uncapped; None if recording missed the answer's start
    front_ms: Optional[int]
    back_ms: Optional[int]
    after_prompt: bool  # the card right after an answer prompt


class Dataset(NamedTuple):
    answers: List[Answer]  # every answer since PACE_DAYS before the first question, oldest first
    taps: List[Tap]
    sittings: List[Sitting]
    days: List[RatedDay]
    logged: List[LoggedAnswer]
    prompts: List[dict]
    surveys_began: Optional[int]  # ms, when the first question of any wording was asked
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


def read_dataset(events: Iterable[dict], rows: Sequence[Sequence], recall=None,
                 templates: Optional[Dict[str, Template]] = None) -> Dataset:
    """Join events to rows (see revlog_sql), which must hold the whole revlog:
    reps, elapsed and template norms all read answers nobody rated.

    recall is the collection's memory.RecallIndex, which scoring reads too;
    without one every recall gap is 0. templates gives the (mid, ord) of a
    recording's card names, "Note type/Template": the "pair" question names two."""
    log = _read_log(events)
    answers = [row for row in rows if row[EASE] >= 1]  # 0 is a reschedule
    ids = [row[ID] for row in answers]
    template_rows = Counter(
        (row[MID], row[ORD], row[DAY], row[DURATION] // 1000)
        for row in answers if row[MID] is not None
    )
    profiles = template_profiles(
        sorted((*row, count) for row, count in template_rows.items()), MODEL_TEMPLATE_DAYS,
    )
    prompted = {
        (event.get("run"), event.get("seq")) for event in log.prompts
        if event["kind"] == "answer"
    }
    recording_began = min(log.answers, default=None)
    surveys_began = min((event["ts"] for event in log.prompts), default=None)
    keep_from = None if surveys_began is None else surveys_began - (PACE_DAYS + 1) * DAY_MS

    history = CardHistory()
    kept: List[Answer] = []
    logged: List[LoggedAnswer] = []
    unlogged = 0
    for row in answers:
        gaps = recall.get(row[ID]) if recall is not None else (0.0, 0.0)
        inputs = answer_inputs(row[:FACTOR], history, profiles, gaps)
        rev_id = row[ID]
        template = None if row[MID] is None else (row[MID], row[ORD])
        event = log.answers.get(rev_id)
        if event:
            logged.append(_logged_answer(event, row, template, inputs, log, prompted))
        elif recording_began is not None and rev_id > recording_began:
            unlogged += 1
        if keep_from is not None and rev_id >= keep_from:
            profile = profiles.get((row[MID], row[ORD], row[DAY]))
            kept.append(Answer(
                rev_id, row[DAY], template, row[EASE], row[DURATION], row[LAST_IVL], gaps,
                bool(event), profile and profile[1],
                tuple(inputs[name] for name in MODEL_INPUTS),
            ))
    kept_ids = [answer.rev_id for answer in kept]
    kept_days = [answer.day for answer in kept]
    sittings = _sittings(log, kept, kept_ids)
    known = set(ids)
    return Dataset(
        answers=kept,
        taps=_taps(log.prompts, kept, sittings),
        sittings=sittings,
        days=_rated_days(log.prompts, answers, ids, kept, kept_ids, kept_days, templates or {}),
        logged=logged,
        prompts=log.prompts,
        surveys_began=surveys_began,
        unlogged=unlogged,
        undone=sum(rev_id not in known for rev_id in log.answers),
    )


def design(taps: Sequence[Tap], note_type_offsets: bool = False) -> tuple:
    """(rows, ratings, names, control_names) for fit_ordered.

    Model inputs come first, so a row cut to them still lines up with the
    names. Three controls explain the ratings and stay out of scoring: how well
    the owner could concentrate in that sitting, what they knew of a new card
    before it, and drift over the months. All are centred, so scoring them at 0
    means an average sitting, an average new card and an average moment. A tap
    without such an answer sits at the mean: it says nothing either way.

    Offsets are for a fit that stays on its own profile, which scores with them.
    A shared fit must leave them out: they soak up what note types differ by,
    and dropping them afterwards scored worse than never fitting them.
    """
    offsets: List[Template] = []
    if note_type_offsets:
        counts = Counter(tap.answer.template for tap in taps if tap.answer.template)
        offsets = sorted(
            template for template, count in counts.items() if count >= MIN_TEMPLATE_RATINGS
        )
    controls = ["focus", "familiar", "drift"]
    controls += [f"{OFFSET_PREFIX}{mid}:{card_ord}" for mid, card_ord in offsets]
    focus = [tap.focus for tap in taps if tap.focus is not None]
    familiar = [tap.answers["familiar"] for tap in taps if "familiar" in tap.answers]
    focus_center = fmean(focus) if focus else 0.0
    familiar_center = fmean(familiar) if familiar else 0.0
    drift_center = fmean(tap.drift for tap in taps) if taps else 0.0
    rows = []
    for tap in taps:
        rows.append([
            *tap.answer.inputs,
            0.0 if tap.focus is None else tap.focus - focus_center,
            tap.answers["familiar"] - familiar_center if "familiar" in tap.answers else 0.0,
            tap.drift - drift_center,
            *(float(tap.answer.template == template) for template in offsets),
        ])
    return rows, [tap.effort for tap in taps], [*MODEL_INPUTS, *controls], controls


def prompt_counts(events: Iterable[dict]) -> dict:
    """Answers per question from "prompt" events in the wording that is fitted,
    how many of those prompts came up and how many left a question unanswered,
    and how many prompts of earlier wordings were answered."""
    counts = dict.fromkeys(("effort", "amount", "work", "pair"), 0)
    counts.update(prompts=0, skipped=0, earlier=0)
    for event in events:
        if not _is_fitted(event):
            counts["earlier"] += bool(event["answers"])
            continue
        counts["prompts"] += 1
        counts["skipped"] += bool(event["skipped"])
        for question in event["answers"]:
            if question in counts:
                counts[question] += 1
    return counts


def _is_fitted(event: dict) -> bool:
    version = event.get("wording_v")
    return isinstance(version, int) and version >= MIN_WORDING


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
            prompts.append(event)
    prompts.sort(key=lambda event: event["ts"])
    return _Log(shown, answers, prompts)


def _taps(prompts: Sequence[dict], kept: Sequence[Answer],
          sittings: Sequence[Sitting]) -> List[Tap]:
    sheets = {
        event.get("rev_id"): event for event in prompts
        if event["kind"] == "answer" and _is_fitted(event) and "effort" in event["answers"]
    }
    focus: Dict[int, int] = {}
    for sitting in sittings:
        if "focus" in sitting.ratings:
            focus.update(dict.fromkeys(range(*sitting.span), sitting.ratings["focus"]))
    taps: List[Tap] = []
    for index, answer in enumerate(kept):
        event = sheets.get(answer.rev_id)
        if event:
            first = taps[0].answer.rev_id if taps else answer.rev_id
            taps.append(Tap(
                answer, event["answers"]["effort"], event["answers"], event.get("know_q"),
                focus.get(index), (answer.rev_id - first) / (DRIFT_DAYS * DAY_MS),
            ))
    return taps


def _sittings(log: _Log, kept: Sequence[Answer], kept_ids: Sequence[int]) -> List[Sitting]:
    """Rated sittings, each with the answers it holds.

    A sitting is known by when its first and last card were shown. It runs
    from the first of its cards' answers to the last, with whatever was
    answered between: the last answer comes after the sitting's last event, by
    as long as that card's back was read. A sitting rated twice keeps the
    later rating, and one whose answers haven't synced yet waits for them.
    """
    shown = sorted(
        (log.shown[event["run"], event["seq"]][0], rev_id)
        for rev_id, event in log.answers.items()
        if (event["run"], event["seq"]) in log.shown
    )
    moments = [moment for moment, _ in shown]
    newest: Dict[int, dict] = {}
    for event in log.prompts:
        bounds = event.get("from_ms"), event.get("to_ms")
        if (event["kind"] == "session" and _is_fitted(event) and event["answers"]
                and all(isinstance(bound, int) for bound in bounds)):
            newest[bounds[0]] = event
    sittings = []
    for start, event in sorted(newest.items()):
        inside = [rev_id for _, rev_id in shown[
            bisect_left(moments, start - SITTING_SLACK_MS):bisect_right(moments, event["to_ms"])
        ]]
        if not inside:
            continue
        span = bisect_left(kept_ids, min(inside)), bisect_right(kept_ids, max(inside))
        if span[0] < span[1]:
            sittings.append(Sitting(span, kept[span[1] - 1].day, event["answers"]))
    return sittings


def _rated_days(prompts, answers, ids, kept, kept_ids, kept_days, templates) -> List[RatedDay]:
    """Rated days by the revlog's study day, so that a day is scored on the
    same answers as on the calendar. The last answer before the rating names
    its day, whatever the clocks did. A day rated twice keeps the later rating."""
    newest: Dict[int, dict] = {}
    for event in prompts:
        if event["kind"] != "day" or not _is_fitted(event) or not event["answers"]:
            continue
        end = event.get("day_end_ms", event["ts"] + 1)
        last = bisect_left(ids, end) - 1
        if last >= 0 and ids[last] >= end - DAY_MS:
            newest[answers[last][DAY]] = event
    days = []
    for day, event in sorted(newest.items()):
        end = event.get("day_end_ms", event["ts"] + 1)
        span = (
            bisect_left(kept_days, day),
            min(bisect_right(kept_days, day), bisect_left(kept_ids, end)),
        )
        if span[0] < span[1]:
            days.append(RatedDay(
                day, span, event["answers"], _pair(event, kept[span[0]:span[1]], templates),
                _versus(event, day, kept, kept_ids, kept_days),
            ))
    return days


def _pair(event: dict, answers: Sequence[Answer],
          templates: Dict[str, Template]) -> Optional[Tuple[Template, Template]]:
    names = event.get("pair_cards")
    if "pair" not in event["answers"] or not isinstance(names, list) or len(names) != 2:
        return None
    first, second = (templates.get(name) for name in names)
    answered = {answer.template for answer in answers}
    if first is None or second is None or first == second or not {first, second} <= answered:
        return None
    return first, second


def _versus(event: dict, day: int, kept, kept_ids, kept_days) -> Optional[Span]:
    """The day a rating was compared with: versus_day is when that day began."""
    began = event.get("versus_day")
    if "versus" not in event["answers"] or not isinstance(began, int):
        return None
    first = bisect_left(kept_ids, began)
    if first == len(kept) or kept_ids[first] >= began + DAY_MS or kept[first].day >= day:
        return None
    other = kept[first].day
    return bisect_left(kept_days, other), bisect_right(kept_days, other)


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
