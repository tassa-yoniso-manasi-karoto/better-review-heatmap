"""Each answer's chance of recall just before it, replayed from the review log.

Pure and standard library only. An answer a day or more after the card's previous
one reads FSRS-6 with the parameters of the card's deck preset, the model Anki
schedules with. FSRS-6 counts whole days and cannot tell five minutes from five
hours, so an answer on the same study day reads FSRS-7 instead, with its published
default parameters, which counts time in fractions of a day.

Both follow Anki's rules for which review-log entries make up a card's history
(rslib scheduler/fsrs/params.rs, reviews_for_fsrs): the card's last run of
learning steps starts it; without one, its first answer that set an interval of a
day or more does, from a state inferred from SM-2 (memory_state.rs,
fsrs_item_for_memory_state); a reset (Forget) discards what came before; cramming
in a filtered deck never counts. The formulas are those of the fsrs crate
(model_v6.rs, model_v7.rs).

A measure reads the recall gap, logit(desired retention) - logit(R): 0 when a card
was answered at the retention its deck schedules for, positive when more of it had
been forgotten. Answers FSRS has no state before read 0: a card's first answer,
cards without a preset, collections without FSRS.
"""

import math
from array import array
from bisect import bisect_left
from typing import Dict, Optional, Sequence, Tuple

# fsrs crate, inference_v6.rs: FSRS-6 defaults, used by a preset that has none.
FSRS6_DEFAULT_PARAMETERS = (
    0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722, 0.1666, 0.796,
    1.4835, 0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542,
)
FSRS5_DEFAULT_DECAY = 0.5
# fsrs crate, inference_v7.rs.
FSRS7_DEFAULT_PARAMETERS = (
    0.1104, 2.2395, 3.9221, 11.7841, 6.1686, 0.6457, 3.6807, 1.9795, 0.0, 1.3826, 0.7024, 0.5999,
    0.8146, 0.6398, 1.0, 1.3207, 0.6707, 3.8668, 0.4416, 0.0934, 1.8631, 0.6162, 1.0869, 0.1567,
    0.0801, 0.2421, 0.9464, 0.1433, 0.7145, 0.0, 0.5667, 0.3734, 0.5333, 0.3048,
)
# simulation.rs, parameter_initialization.rs
S_MIN, S_MAX, D_MIN, D_MAX, INIT_S_MAX = 0.0001, 36500.0, 1.0, 10.0, 100.0
# parameter_clipper_v6.rs, with one relearning step and short-term stability on.
_CLIP6 = (
    (S_MIN, INIT_S_MAX), (S_MIN, INIT_S_MAX), (S_MIN, INIT_S_MAX), (S_MIN, INIT_S_MAX),
    (D_MIN, D_MAX), (0.001, 4.0), (0.001, 4.0), (0.001, 0.75), (0.0, 4.5), (0.0, 0.8),
    (0.001, 3.5), (0.001, 5.0), (0.001, 0.25), (0.001, 0.9), (0.0, 4.0), (0.0, 1.0),
    (1.0, 6.0), (0.0, 2.0), (0.0, 2.0), (0.01, 0.8), (0.1, 0.8),
)
# Same-day stability may not fall from Hard up, as in the fsrs crate and py-fsrs 6.3.
# (Older AnkiDroid builds left Hard unfloored.)
SAME_DAY_FLOOR_FROM = 2
# revlog.type
LEARNING, REVIEW, RELEARNING, FILTERED, MANUAL, RESCHEDULED = range(6)
DAY_SECONDS = 86400
DAY_MS = 86_400_000
_R_RANGE = (1e-4, 1 - 1e-4)


def fsrs6_parameters(params: Sequence[float]) -> Optional[Tuple[float, ...]]:
    """A preset's parameters as FSRS-6 runs them, or None if they are unusable.

    model_v6.rs check_and_fill_parameters_fsrs6, then the clipper: 17 (FSRS-4.5)
    and 19 (FSRS-5) values are converted, and nothing means the defaults.
    """
    try:
        w = [float(value) for value in params]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in w):
        return None
    if not w:
        w = list(FSRS6_DEFAULT_PARAMETERS)
    elif len(w) == 17:
        w[4] = w[5] * 2.0 + w[4]
        w[5] = math.log(w[5] * 3.0 + 1.0) / 3.0
        w[6] += 0.5
        w += [0.0, 0.0, 0.0, FSRS5_DEFAULT_DECAY]
    elif len(w) == 19:
        w += [0.0, FSRS5_DEFAULT_DECAY]
    elif len(w) != 21:
        return None
    return tuple(min(max(value, low), high) for value, (low, high) in zip(w, _CLIP6))


def logit(p: float) -> float:
    p = min(_R_RANGE[1], max(_R_RANGE[0], p))
    return math.log(p / (1 - p))


class Preset:
    """What the replay needs from a card's deck preset and deck."""

    __slots__ = ("w", "decay", "factor", "target", "sm2_retention", "ignore_before_ms")

    def __init__(self, params: Sequence[float], desired_retention: float,
                 sm2_retention: float = 0.9, ignore_before_ms: int = 0):
        w = fsrs6_parameters(params) or FSRS6_DEFAULT_PARAMETERS
        self.w = w
        self.decay = w[20]
        self.factor = 0.9 ** (-1.0 / w[20]) - 1.0
        self.target = logit(desired_retention)
        self.sm2_retention = sm2_retention if 0 < sm2_retention < 1 else 0.9
        self.ignore_before_ms = ignore_before_ms


# FSRS-6, model_v6.rs

def _r6(preset: Preset, days: float, stability: float) -> float:
    return (1.0 + preset.factor * days / min(max(stability, S_MIN), S_MAX)) ** -preset.decay


def _next6(w: Sequence[float], s: float, d: float, r: float, days: int,
           rating: int) -> Tuple[float, float]:
    s = min(max(s, S_MIN), S_MAX)
    d = min(max(d, D_MIN), D_MAX)
    if days == 0:
        sinc = math.exp(w[17] * (rating - 3.0 + w[18])) * s ** -w[19]
        if rating >= SAME_DAY_FLOOR_FROM and sinc < 1.0:
            sinc = 1.0
        s = s * sinc
    elif rating == 1:
        floor = s / math.exp(w[17] * w[18])
        s = min(w[11] * d ** -w[12] * ((s + 1.0) ** w[13] - 1.0) * math.exp((1.0 - r) * w[14]),
                floor)
    else:
        bonus = (w[15] if rating == 2 else 1.0) * (w[16] if rating == 4 else 1.0)
        s = s * (math.exp(w[8]) * (11.0 - d) * s ** -w[9]
                 * (math.exp((1.0 - r) * w[10]) - 1.0) * bonus + 1.0)
    reverted = w[7] * (w[4] - math.exp(w[5] * 3.0) + 1.0)
    d = d + (10.0 - d) / 9.0 * -w[6] * (rating - 3.0)
    return min(max(s, S_MIN), S_MAX), min(max(reverted + (1.0 - w[7]) * d, D_MIN), D_MAX)


def _init6(w: Sequence[float], rating: int) -> Tuple[float, float]:
    d = w[4] - math.exp(w[5] * (rating - 1)) + 1.0
    return min(max(w[rating - 1], S_MIN), S_MAX), min(max(d, D_MIN), D_MAX)


def _sm2_state6(preset: Preset, ease: float, interval: float) -> Tuple[float, float]:
    w, retention = preset.w, preset.sm2_retention
    s = max(interval, S_MIN) * preset.factor / (retention ** (-1.0 / preset.decay) - 1.0)
    d = 11.0 - (ease - 1.0) / (math.exp(w[8]) * s ** -w[9] * math.expm1((1.0 - retention) * w[10]))
    if ease <= 1.1:  # the factor holds an FSRS difficulty
        d = (ease - 0.1) * 9.0 + 1.0
    return s, min(max(d, D_MIN), D_MAX)


# FSRS-7, model_v7.rs, with the default parameters only.

_W7 = FSRS7_DEFAULT_PARAMETERS
_LN_W25 = math.log(_W7[25])
_DECAY2 = -min(max(_W7[24], 0.01), 0.95)
_FACTOR2 = _W7[26] ** (1.0 / _DECAY2) - 1.0


def _fast7(t: float, sf: float) -> float:
    decay1 = -min(max(_W7[23] * sf ** (_W7[33] - 0.3), 0.01), 0.95)
    factor1 = math.exp(min(_LN_W25 / decay1, 60.0)) - 1.0
    return (1.0 + factor1 * t / sf) ** decay1


def _r7(t: float, s: float, d: float, sf: float) -> float:
    s, sf, d = max(s, S_MIN), max(sf, S_MIN), min(max(d, D_MIN), D_MAX)
    w = _W7
    long_term = (1.0 + _FACTOR2 * math.exp((d - 5.0) * (w[32] - 0.3)) * t / s) ** _DECAY2
    weight1 = w[27] * sf ** -w[29]
    weight2 = w[28] * s ** w[30] * math.exp((d - 5.0) * (w[31] - 0.5))
    retention = (weight1 * _fast7(t, sf) + weight2 * long_term) / (weight1 + weight2)
    return retention * (1.0 - 2e-5) + 1e-5


def _stability7(s: float, d: float, r: float, rating: int, start: int) -> float:
    w = _W7
    fail = w[start + 3] * ((s + 1.0) ** w[start + 4] - 1.0) * math.exp((1.0 - r) * w[start + 5])
    fail = min(s, fail)
    if rating == 1:
        return min(max(fail, S_MIN), S_MAX)
    bonus = (w[start + 6] if rating == 2 else 1.0) * (w[start + 7] if rating == 4 else 1.0)
    grown = s * (math.exp(w[start] - 1.5) * (11.0 - d) * s ** -w[start + 1]
                 * (math.exp((1.0 - r) * w[start + 2]) - 1.0) * bonus + 1.0)
    return min(max(max(fail, grown), S_MIN), S_MAX)


def _next7(s: float, d: float, sf: float, t: float,
           rating: int) -> Tuple[float, float, float]:
    s, d, sf = min(max(s, S_MIN), S_MAX), min(max(d, D_MIN), D_MAX), min(max(sf, S_MIN), S_MAX)
    w = _W7
    r = _r7(t, s, d, sf)
    slow = _stability7(s, d, r, rating, 7)
    fast = _stability7(sf, d, _fast7(t, sf), rating, 15)
    if rating == 1:
        fast = min(fast, slow * 0.8)
    change = -w[6] * (rating - 3.0) * ((r + 0.1) if rating == 1 else 1.0)
    d = d + (10.0 - d) * change / 9.0
    d = (w[4] - math.exp(w[5] * 3.0) + 1.0) * 0.01 + d * 0.99
    return slow, min(max(d, D_MIN), D_MAX), min(max(fast, S_MIN), S_MAX)


def _init7(rating: int) -> Tuple[float, float, float]:
    s = min(max(_W7[rating - 1], S_MIN), S_MAX)
    d = _W7[4] - math.exp(_W7[5] * (rating - 1)) + 1.0
    return s, min(max(d, D_MIN), D_MAX), min(max(s * 0.8, S_MIN), S_MAX)


def _sm2_state7(interval: float, retention: float, d: float) -> Tuple[float, float, float]:
    """The FSRS-7 state that recalls at `retention` after `interval` days.

    memory_state_from_sm2 searches log-stability with difficulty 5 and a fast
    stability of 0.8 times the slow one. When the factor holds a difficulty, Anki
    sets it and rescales the state to the same interval; one search with that
    difficulty finds the same state.
    """
    interval = min(max(interval, S_MIN), S_MAX)
    low, high = math.log(S_MIN), math.log(S_MAX)
    for _ in range(50):
        middle = (low + high) / 2
        s = math.exp(middle)
        if _r7(interval, s, d, s * 0.8) < retention:
            low = middle
        else:
            high = middle
    s = math.exp((low + high) / 2)
    return s, d, s * 0.8


# Fields of a card's record, a list for speed: whether FSRS holds a state for it,
# whether its last entry continued a run of learning steps, the FSRS-6 state and
# day count, the FSRS-7 state and the time of the last counted answer.
_ACTIVE, _RUN, _S6, _D6, _DAYS, _S7, _D7, _F7, _LAST = range(9)


class Replay:
    """Feed every review-log entry in id order; each answer gets its recall gaps.

    presets maps card ids to their Preset; a card missing from it reads 0
    throughout. next_day_at is the scheduler's next day cutoff, in seconds: whole
    days count from it, as Anki counts them.
    """

    def __init__(self, presets: Dict[int, Preset], next_day_at: int):
        self.presets = presets
        self.next_day_at = next_day_at
        self.cards: Dict[int, list] = {}

    def feed(self, cid: int, rev_id: int, rtype: int, ease: int, ivl: int,
             factor: int) -> Tuple[float, float]:
        """(gap a day or more later, gap on the same study day); at most one is set."""
        preset = self.presets.get(cid)
        if preset is None or (rtype == FILTERED and not factor):
            return 0.0, 0.0  # no preset, or cramming: never part of the history
        card = self.cards.get(cid)
        if card is None:
            card = self.cards[cid] = [False, False, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0]
        if ease < 1:
            card[_RUN] = False
            if rtype == MANUAL and not factor:  # reset: the history starts again
                card[_ACTIVE] = False
            return 0.0, 0.0
        rating = min(ease, 4)
        days = (self.next_day_at - rev_id // 1000) // DAY_SECONDS
        if days < 0:
            days = 0
        if rtype == LEARNING and not card[_RUN]:
            # A new run of learning steps: the card's history starts here.
            card[_RUN] = True
            if rev_id < preset.ignore_before_ms:
                card[_ACTIVE] = False  # then the first answer after the cutoff starts it
                return 0.0, 0.0
            card[_ACTIVE] = True
            card[_S6], card[_D6] = _init6(preset.w, rating)
            card[_S7], card[_D7], card[_F7] = _init7(rating)
            card[_DAYS], card[_LAST] = days, rev_id
            return 0.0, 0.0
        card[_RUN] = rtype == LEARNING
        if not card[_ACTIVE]:
            if (ivl >= 1 or ivl <= -DAY_SECONDS) and rev_id > preset.ignore_before_ms:
                # Without learning steps, the first answer setting a day or more
                # starts the history from SM-2's state.
                ease_factor = (factor or 2500) / 1000.0
                interval = float(max(ivl, 1))
                card[_ACTIVE] = True
                card[_S6], card[_D6] = _sm2_state6(preset, ease_factor, interval)
                d7 = (ease_factor - 0.1) * 9.0 + 1.0 if ease_factor <= 1.1 else 5.0
                card[_S7], card[_D7], card[_F7] = _sm2_state7(
                    interval, preset.sm2_retention, min(max(d7, D_MIN), D_MAX))
                card[_DAYS], card[_LAST] = days, rev_id
            return 0.0, 0.0
        elapsed = card[_DAYS] - days
        t = max(rev_id - card[_LAST], 1) / DAY_MS
        r6 = _r6(preset, elapsed, card[_S6])
        later = same = 0.0
        if elapsed:
            later = preset.target - logit(r6)
        else:
            same = preset.target - logit(_r7(t, card[_S7], card[_D7], card[_F7]))
        card[_S6], card[_D6] = _next6(preset.w, card[_S6], card[_D6], r6, elapsed, rating)
        card[_S7], card[_D7], card[_F7] = _next7(card[_S7], card[_D7], card[_F7], t, rating)
        card[_DAYS], card[_LAST] = days, rev_id
        return later, same


_NO_GAP = (0.0, 0.0)


class RecallIndex:
    """Recall gaps by revlog id, kept only for answers that have one."""

    def __init__(self):
        self.ids = array("q")
        self.later = array("d")
        self.same = array("d")

    def add(self, rev_id: int, later: float, same: float):
        """Ids must arrive in increasing order."""
        if later or same:
            self.ids.append(rev_id)
            self.later.append(later)
            self.same.append(same)

    def get(self, rev_id: int) -> Tuple[float, float]:
        index = bisect_left(self.ids, rev_id)
        if index < len(self.ids) and self.ids[index] == rev_id:
            return self.later[index], self.same[index]
        return _NO_GAP

    def reader(self):
        """get() for ids asked in increasing order, walking forward instead of searching."""
        ids, later, same = self.ids, self.later, self.same
        size, position = len(ids), 0

        def get(rev_id: int) -> Tuple[float, float]:
            nonlocal position
            while position < size and ids[position] < rev_id:
                position += 1
            if position < size and ids[position] == rev_id:
                return later[position], same[position]
            return _NO_GAP
        return get

    def __len__(self) -> int:
        return len(self.ids)
