"""How answers add up to the work of a sitting or a day. Pure.

An answer's credit is exp(b * log_d + k * difficulty): its duration to the
power b, times what its difficulty adds. b and k are the two numbers that make
summed credit explain how much work sittings and days were rated as. At k 0,
b 0 is a count, 0.4 review-weighted and 1 recorded time.

Each kind of rating is an ordered logit on the log credit of what it rates,
with a slope and cut points of its own. Those are refitted for every b and k
tried, so the search itself has only the two.
"""

import math
from operator import mul
from statistics import fmean
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from .fit import ordered_logit, rating_score

# Effort ratings follow an answer's duration too. Their weights on these inputs
# stay out of the difficulty, so that time counts once, in b: b is then the
# whole time exponent, and 0.4 really means review-weighted.
TIME_INPUTS = ("log_d", "log_d·rich")
# The exponent review-weighted uses, kept when ratings can't place b.
FALLBACK_B = 0.4
# b between these can't tell a count from time: see settle.
RULE_ENDS = (0.2, 0.8)
# Chi-square with one degree of freedom at 90%: the deviance a profile interval stops at.
DEVIANCE_90 = 2.706
PROFILE_STEP = 0.1
PROFILE_RANGE = (0.0, 1.5)
B_BOUNDS, K_BOUNDS = (-0.5, 2.0), (-2.0, 5.0)
STARTS = ((0.4, 0.0), (0.9, 0.4))
# A kind of rating with fewer rated units, or one level, says nothing about b and k.
MIN_UNITS = 5

MAX_CLIMB = 100
MAX_HALVINGS = 30
MAX_MOVE = 0.5
ARMIJO_SLOPE = 1e-4
GRADIENT_TOLERANCE = 1e-6
LOOSE_GRADIENT_TOLERANCE = 1e-3
VALUE_TOLERANCE = 1e-12


class Source(NamedTuple):
    """One kind of rating. A group holds the answers one rating is about, as
    positions in the fit's answers. A pair has two groups for each rating,
    which grows with the second."""
    name: str
    ratings: List[int]
    groups: List[List[int]]
    pair: bool = False


class CreditFit(NamedTuple):
    b: float
    k: float
    loglik: float  # every source at its own best slope and cuts
    converged: bool


class Settled(NamedTuple):
    """A fit, and the b and k to score with once the rule had its say."""
    fit: CreditFit
    interval: Optional[Tuple[float, float]]  # 90% for b, if asked for
    fell_back: bool
    b: float
    k: float


def usable(sources: Sequence[Source]) -> List[Source]:
    return [
        source for source in sources
        if len(source.ratings) >= MIN_UNITS and len(set(source.ratings)) >= 2
    ]


def fit_credit(sources: Sequence[Source], log_d: Sequence[float],
               difficulty: Sequence[float], eta_range: Tuple[float, float],
               fixed_b: Optional[float] = None,
               starts: Sequence[Tuple[float, float]] = STARTS,
               fixed_k: Optional[float] = None) -> Optional[CreditFit]:
    """Maximum likelihood of b and k, or of one of them with the other fixed.

    log_d and difficulty hold one value per answer, centred by the caller, and
    eta_range is where scoring clamps b * log_d + k * difficulty: the fit sees
    credit as the calendar will. The best of the starts is kept; a fixed b
    needs only the first. None without sources.
    """
    if not sources:
        return None
    free = [fixed_b is None, fixed_k is None]
    bounds = [bound for bound, is_free in zip((B_BOUNDS, K_BOUNDS), free) if is_free]
    warm: Dict[str, tuple] = {}

    def objective(point):
        b, k = _placed(point, fixed_b, fixed_k)
        value, gradient = _profile(sources, log_d, difficulty, eta_range, b, k, warm)
        return value, [g for g, is_free in zip(gradient, free) if is_free]

    best = None
    for start in starts:
        point, value, converged = _climb(
            objective, [x for x, is_free in zip(start, free) if is_free], bounds,
        )
        fit = CreditFit(*_placed(point, fixed_b, fixed_k), value, converged)
        if best is None or fit.loglik > best.loglik:
            best = fit
        if fixed_b is not None:
            break
    return best


def _placed(point: Sequence[float], fixed_b: Optional[float],
            fixed_k: Optional[float]) -> List[float]:
    """b and k, from the coordinates being searched and the fixed ones."""
    searched = iter(point)
    return [
        next(searched) if fixed is None else fixed for fixed in (fixed_b, fixed_k)
    ]


def profile_interval(sources: Sequence[Source], log_d: Sequence[float],
                     difficulty: Sequence[float], eta_range: Tuple[float, float],
                     top: CreditFit) -> Tuple[float, float]:
    """The 90% profile likelihood interval for b, walking out from the estimate.

    The root of the deviance is close to linear in b, so the crossing is found
    on it, then checked with one more fit there. An end at the edge of
    PROFILE_RANGE means the ratings allow everything up to there.
    """
    level = math.sqrt(DEVIANCE_90)

    def root_deviance(b, k):
        fit = fit_credit(sources, log_d, difficulty, eta_range, b, ((b, k),))
        return math.sqrt(max(2 * (top.loglik - fit.loglik), 0.0)), fit.k

    ends = []
    for direction, edge in zip((-1, 1), PROFILE_RANGE):
        inner_b, inner_root, k = top.b, 0.0, top.k
        b, found = top.b, edge
        while True:
            b = round(b + direction * PROFILE_STEP, 6)
            if (b - edge) * direction > 0:
                break
            root, k = root_deviance(b, k)
            if root > level:
                guess = _crossing(b, inner_b, root, inner_root, level)
                guess_root, _ = root_deviance(guess, k)
                if guess_root > level:
                    found = _crossing(guess, inner_b, guess_root, inner_root, level)
                else:
                    found = _crossing(b, guess, root, guess_root, level)
                break
            inner_b, inner_root = b, root
        ends.append(found)
    return min(ends[0], top.b), max(ends[1], top.b)


def settle(sources: Sequence[Source], log_d: Sequence[float],
           difficulty: Sequence[float], eta_range: Tuple[float, float],
           with_interval: bool = False) -> Optional[Settled]:
    """Fit b and k, then apply the rule for ratings that can't place b.

    If the 90% interval for b holds both RULE_ENDS, the ratings can't tell a
    count from time: b stays at review-weighted's exponent and k is fitted
    alone. That takes two fits, one at each end.
    """
    top = fit_credit(sources, log_d, difficulty, eta_range)
    if top is None:
        return None
    interval = None
    if with_interval:
        interval = profile_interval(sources, log_d, difficulty, eta_range, top)
    for end in RULE_ENDS:
        fixed = fit_credit(sources, log_d, difficulty, eta_range, end, ((end, top.k),))
        if 2 * (top.loglik - fixed.loglik) > DEVIANCE_90:
            return Settled(top, interval, False, top.b, top.k)
    fallback = fit_credit(sources, log_d, difficulty, eta_range, FALLBACK_B)
    return Settled(top, interval, True, FALLBACK_B, fallback.k)


def _crossing(outer: float, inner: float, outer_root: float, inner_root: float,
              level: float) -> float:
    """Where a root deviance crosses level between two probes, linearly."""
    if outer_root == inner_root:
        return inner
    return inner + (outer - inner) * (level - inner_root) / (outer_root - inner_root)


def _profile(sources, log_d, difficulty, eta_range, b, k, warm) -> Tuple[float, List[float]]:
    """Log likelihood at b and k with every source's slope and cuts at their
    best, and its gradient in b and k: at that best, the plain partial derivative."""
    low, high = eta_range
    values = [b * d + k * e for d, e in zip(log_d, difficulty)]
    if min(values) < low or max(values) > high:
        # A clamped answer's credit no longer moves with b or k.
        credit = [math.exp(min(high, max(low, value))) for value in values]
        moving = [c if low <= value <= high else 0.0 for c, value in zip(credit, values)]
    else:
        credit = moving = list(map(math.exp, values))
    by_time = list(map(mul, moving, log_d))
    by_difficulty = list(map(mul, moving, difficulty))
    total, gradient = 0.0, [0.0, 0.0]
    for source in sources:
        sums = [sum(map(credit.__getitem__, group)) for group in source.groups]
        z = list(map(math.log, sums))
        d_b = [sum(map(by_time.__getitem__, group)) / s for group, s in zip(source.groups, sums)]
        d_k = [
            sum(map(by_difficulty.__getitem__, group)) / s
            for group, s in zip(source.groups, sums)
        ]
        if source.pair:
            z, d_b, d_k = (
                [second - first for first, second in zip(column[0::2], column[1::2])]
                for column in (z, d_b, d_k)
            )
        # Any shift of z goes into the cuts: centring keeps the inner fit well scaled.
        centre = fmean(z)
        rows = [[value - centre] for value in z]
        fit = ordered_logit(rows, source.ratings, start=warm.get(source.name))
        if not fit.converged:
            fit = ordered_logit(rows, source.ratings)
        warm[source.name] = (fit.beta, fit.cuts)
        total += fit.loglik
        slope = fit.beta[0]
        cell_of = {level: index for index, level in enumerate(fit.levels)}
        for row, rating, in_b, in_k in zip(rows, source.ratings, d_b, d_k):
            score = slope * rating_score(slope * row[0], cell_of[rating], fit.cuts)
            gradient[0] += score * in_b
            gradient[1] += score * in_k
    return total, gradient


def _climb(objective: Callable, start: Sequence[float],
           bounds: Sequence[Tuple[float, float]]) -> Tuple[List[float], float, bool]:
    """Quasi-Newton ascent inside a box. objective gives (value, gradient)."""
    size = len(start)
    point = [min(high, max(low, x)) for x, (low, high) in zip(start, bounds)]
    value, gradient = objective(point)
    inverse = [[float(i == j) for j in range(size)] for i in range(size)]
    for _ in range(MAX_CLIMB):
        # At a bound, the part of the gradient pointing outside can't be followed.
        slope = [
            0.0 if (x <= low and g < 0) or (x >= high and g > 0) else g
            for x, g, (low, high) in zip(point, gradient, bounds)
        ]
        if max(map(abs, slope)) < GRADIENT_TOLERANCE:
            return point, value, True
        direction = [
            sum(map(mul, line, slope)) if s else 0.0 for line, s in zip(inverse, slope)
        ]
        if sum(map(mul, direction, slope)) <= 0:
            inverse = [[float(i == j) for j in range(size)] for i in range(size)]
            direction = slope
        scale = min(1.0, MAX_MOVE / max(map(abs, direction)))
        for _ in range(MAX_HALVINGS):
            moved = [
                min(high, max(low, x + scale * d))
                for x, d, (low, high) in zip(point, direction, bounds)
            ]
            step = [new - old for new, old in zip(moved, point)]
            moved_value, moved_gradient = objective(moved)
            if moved_value >= value + ARMIJO_SLOPE * sum(map(mul, slope, step)):
                break
            scale /= 2
        else:
            break
        change = [old - new for old, new in zip(gradient, moved_gradient)]
        gained = moved_value - value
        point, value, gradient = moved, moved_value, moved_gradient
        if gained < VALUE_TOLERANCE * (1 + abs(value)):
            break
        curvature = sum(map(mul, step, change))
        if curvature > 1e-12:
            inverse = _bfgs(inverse, step, change, curvature)
    slope = [
        0.0 if (x <= low and g < 0) or (x >= high and g > 0) else g
        for x, g, (low, high) in zip(point, gradient, bounds)
    ]
    return point, value, max(map(abs, slope)) < LOOSE_GRADIENT_TOLERANCE


def _bfgs(inverse, step, change, curvature) -> List[List[float]]:
    """The inverse Hessian estimate after a step that changed the gradient by -change."""
    size = len(step)
    carried = [sum(map(mul, line, change)) for line in inverse]
    spread = sum(map(mul, change, carried))
    return [
        [
            inverse[i][j]
            - (step[i] * carried[j] + carried[i] * step[j]) / curvature
            + (1 + spread / curvature) * step[i] * step[j] / curvature
            for j in range(size)
        ]
        for i in range(size)
    ]
