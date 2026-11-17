"""Ordered logit with free cut points, in pure Python.

A rating only says which of two things was more: nothing fixes how far apart
two of its levels are. So the cut points between levels are fitted along with
the weights, and what comes out orders answers, sittings or days without a
size of its own. credit.py gives it one.
"""

import math
from bisect import bisect_left
from operator import mul
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple


MAX_ITERATIONS = 100
MAX_HALVINGS = 40
DECREMENT_TOLERANCE = 1e-9
MIN_PROBABILITY = 1e-300
# A level nobody chose yet would start its cut point at infinity.
MIN_START_SHARE = 1e-4
ONE_SIDED_5_PERCENT = 1.6449


class Ordered(NamedTuple):
    """What ordered_logit found, in the units of the columns it was given."""
    beta: List[float]
    cuts: List[float]
    levels: tuple  # the ratings that occur: cuts[j] lies between levels j and j + 1
    loglik: float  # without the penalty
    iterations: int
    converged: bool
    covariance: Optional[List[List[float]]]  # of beta, then of the cuts


class OrderedFit(NamedTuple):
    weights: Dict[str, float]  # per raw input unit
    se: Dict[str, float]
    cuts: Tuple[float, ...]  # for raw inputs too
    levels: tuple
    loglik: float
    iterations: int
    converged: bool
    names: Tuple[str, ...]
    control_names: Tuple[str, ...]


def ordered_logit(rows: Sequence[Sequence[float]], ratings: Sequence,
                  lam: float = 0.0, start: Optional[Tuple[Sequence[float], Sequence[float]]] = None,
                  want_covariance: bool = False) -> Ordered:
    """Fit P(rating <= level j) = F(cuts[j] - row . beta) by Newton's method.

    The log likelihood is concave in beta and the cuts, so a step with halving
    gets there. Ratings the rows tell apart perfectly have no finite best:
    beta then grows until the likelihood stops moving, and comes back very
    large. A penalty prevents that. Only levels that occur get a cell, since an
    unused one has no finite cut. lam penalizes beta alone. start is the
    (beta, cuts) of a similar fit.
    """
    levels = tuple(sorted(set(ratings)))
    cell_of = {level: index for index, level in enumerate(levels)}
    cells = [cell_of[rating] for rating in ratings]
    size = len(rows[0]) if rows else 0
    last = len(levels) - 1
    if last < 1:
        return Ordered([0.0] * size, [], levels, 0.0, 0, False, None)
    theta = _start_from_shares(cells, size, last)
    value = _objective(theta, rows, cells, size, last, lam)
    if start is not None and len(start[0]) == size and len(start[1]) == last:
        # A start far from here can sit where every probability underflows.
        warm = [*start[0], *start[1]]
        warm_value = _objective(warm, rows, cells, size, last, lam)
        if warm_value > value:
            theta, value = warm, warm_value

    converged = False
    iterations = 0
    while True:
        gradient, curvature = _newton_terms(theta, rows, cells, size, last, lam)
        factor = _factor(curvature)
        step = _solve(factor, gradient)
        if math.fsum(map(mul, gradient, step)) < DECREMENT_TOLERANCE:
            converged = True
            break
        if iterations == MAX_ITERATIONS:
            break
        shrink = 1.0
        for _ in range(MAX_HALVINGS):
            moved = [t + shrink * s for t, s in zip(theta, step)]
            moved_value = _objective(moved, rows, cells, size, last, lam)
            if moved_value >= value - 1e-12:
                break
            shrink /= 2
        else:
            break
        theta, value = moved, moved_value
        iterations += 1

    covariance = None
    if want_covariance:
        total = size + last
        covariance = [
            _solve(factor, [float(k == j) for k in range(total)]) for j in range(total)
        ]
    beta, cuts = theta[:size], theta[size:]
    return Ordered(
        beta, cuts, levels, value + _penalty(beta, lam), iterations, converged, covariance,
    )


def fit_ordered(rows: Sequence[Sequence[float]], ratings: Sequence[int],
                names: Sequence[str], lam: float = 2.0,
                control_names: Sequence[str] = ()) -> OrderedFit:
    """Fit ratings on named inputs.

    Standardizing makes lam mean the same for every input, whatever its unit.
    The cut points go unpenalized: they set where the levels sit, not an effect
    to shrink. Control columns are fitted like any other and only differ at
    prediction. A constant column gets weight and se 0.
    """
    _check_inputs(rows, ratings, names, control_names)
    columns = list(zip(*rows))
    active = [i for i, column in enumerate(columns) if min(column) != max(column)]
    means = {i: math.fsum(columns[i]) / len(rows) for i in active}
    scales = {
        i: math.sqrt(math.fsum((x - means[i]) ** 2 for x in columns[i]) / len(rows))
        for i in active
    }
    standardized = [
        [(row[i] - means[i]) / scales[i] for i in active] for row in rows
    ]
    fit = ordered_logit(standardized, ratings, lam, want_covariance=True)

    weights = dict.fromkeys(names, 0.0)
    se = dict.fromkeys(names, 0.0)
    for j, i in enumerate(active):
        weights[names[i]] = fit.beta[j] / scales[i]
        if fit.covariance:
            se[names[i]] = math.sqrt(max(fit.covariance[j][j], 0.0)) / scales[i]
    # Cuts for raw inputs: what the means added to every standardized row.
    shift = math.fsum(fit.beta[j] * means[i] / scales[i] for j, i in enumerate(active))
    return OrderedFit(
        weights=weights,
        se=se,
        cuts=tuple(cut + shift for cut in fit.cuts),
        levels=fit.levels,
        loglik=fit.loglik,
        iterations=fit.iterations,
        converged=fit.converged,
        names=tuple(names),
        control_names=tuple(control_names),
    )


def predict_eta(result: OrderedFit, row: Sequence[float],
                without: Sequence[str] = ()) -> float:
    """Index of a row aligned with result.names, controls at zero.

    It only orders rows: where the levels sit is in the cuts. without names
    inputs to leave out as well."""
    return math.fsum(
        result.weights[name] * value for name, value in zip(result.names, row)
        if name not in result.control_names and name not in without
    )


def mean_log_loss(result: OrderedFit, rows: Sequence[Sequence[float]],
                  ratings: Sequence[int]) -> float:
    """Mean -ln P(rating), controls at zero: the model as scoring would use it.

    A rating the fit never saw counts as the next level it did see, or the
    highest: every fit compared on the same ratings is treated alike."""
    last = len(result.levels) - 1
    return -math.fsum(
        math.log(_probability(
            predict_eta(result, row), min(bisect_left(result.levels, rating), last),
            result.cuts,
        )[0])
        for row, rating in zip(rows, ratings)
    ) / len(ratings)


def rating_score(eta: float, cell: int, cuts: Sequence[float]) -> float:
    """d ln P(level cell) / d eta: how a higher index would change that rating's odds."""
    probability, upper_density, lower_density = _probability(eta, cell, cuts)[:3]
    return (lower_density - upper_density) / probability


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    """Rank correlation with average ranks, 0 when either side has no spread."""
    if len(a) != len(b):
        raise ValueError("spearman needs two sequences of equal length")
    ranks_a, ranks_b = _average_ranks(a), _average_ranks(b)
    center = (len(a) + 1) / 2
    deviations_a = [rank - center for rank in ranks_a]
    deviations_b = [rank - center for rank in ranks_b]
    spread = math.sqrt(
        math.fsum(x * x for x in deviations_a) * math.fsum(x * x for x in deviations_b)
    )
    return math.fsum(map(mul, deviations_a, deviations_b)) / spread if spread else 0.0


def ordinal_t(target: Sequence[int], rival: Sequence[float],
              candidate: Sequence[float]) -> float:
    """What candidate explains of target ratings that rival leaves unexplained.

    An ordered logit of target on log rival and log(candidate / rival): the t
    of the second, about standard normal when candidate adds nothing. 0 when
    it can't be told: no spread, a score at 0, or ratings told apart perfectly.

    A rank correlation with rival removed from both sides is not used: ranking
    bends the tails of target and candidate alike, a straight line on ranks
    leaves that bend behind, and rival plus pure noise then passes at p 0.05
    in 8 to 14% of simulated cases.
    """
    if not target or min(*rival, *candidate) <= 0:
        return 0.0
    log_rival = [math.log(score) for score in rival]
    return added_t(target, [
        log_rival, [math.log(score) - base for score, base in zip(candidate, log_rival)],
    ])


def added_t(target: Sequence[int], columns: Sequence[Sequence[float]]) -> float:
    """The t of the last column in an ordered logit of target on all of them:
    what it explains of the ratings once the others are known. 0 when it can't be told."""
    standardized = [_standardized(column) for column in columns]
    if len(set(target)) < 2 or None in standardized:
        return 0.0
    fit = ordered_logit(list(zip(*standardized)), target, want_covariance=True)
    last = len(columns) - 1
    variance = fit.covariance[last][last] if fit.covariance else 0.0
    if not (variance > 0 and math.isfinite(variance)):
        return 0.0
    return fit.beta[last] / math.sqrt(variance)


def _standardized(values: Sequence[float]) -> Optional[List[float]]:
    mean = math.fsum(values) / len(values)
    scale = math.sqrt(math.fsum((value - mean) ** 2 for value in values) / len(values))
    if scale < 1e-12:
        return None
    return [(value - mean) / scale for value in values]


def _average_ranks(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        for i in order[start:end]:
            ranks[i] = (start + 1 + end) / 2
        start = end
    return ranks


def _check_inputs(rows, ratings, names, control_names) -> None:
    if not rows or len(rows) != len(ratings):
        raise ValueError("fit_ordered needs one rating per row, and at least one row")
    if any(len(row) != len(names) for row in rows):
        raise ValueError("every row must have one value per name")
    if not all(math.isfinite(value) for row in rows for value in row):
        raise ValueError("rows must be finite")
    if len(set(names)) != len(names) or not set(control_names) <= set(names):
        raise ValueError("names must be unique and include every control")


def _start_from_shares(cells: Sequence[int], size: int, last: int) -> List[float]:
    """No effect of any column, and cuts that give each level its share of the ratings."""
    counts = [0] * (last + 1)
    for cell in cells:
        counts[cell] += 1
    theta = [0.0] * size
    below = 0
    for count in counts[:last]:
        below += count
        share = min(1 - MIN_START_SHARE, max(MIN_START_SHARE, below / len(cells)))
        theta.append(math.log(share / (1 - share)))
    return theta


def _logistic(u: float) -> float:
    if u < 0:
        tail = math.exp(u)  # exp(-u) would overflow
        return tail / (1 + tail)
    return 1 / (1 + math.exp(-u))


def _probability(eta: float, cell: int, cuts: Sequence[float]) -> tuple:
    """P(level cell), then the density and distribution at its upper and lower cut.

    The lowest level has no lower cut and the highest no upper one."""
    upper_cdf, upper_density, lower_cdf, lower_density = 1.0, 0.0, 0.0, 0.0
    lower = -math.inf
    if cell < len(cuts):
        upper_cdf = _logistic(cuts[cell] - eta)
        upper_density = upper_cdf * (1 - upper_cdf)
    if cell > 0:
        lower = cuts[cell - 1] - eta
        lower_cdf = _logistic(lower)
        lower_density = lower_cdf * (1 - lower_cdf)
    if lower > 0:  # Subtract upper tails, or both ends round to 1.
        upper_tail = _logistic(eta - cuts[cell]) if cell < len(cuts) else 0.0
        probability = _logistic(-lower) - upper_tail
    else:
        probability = upper_cdf - lower_cdf
    return (
        max(probability, MIN_PROBABILITY), upper_density, lower_density, upper_cdf, lower_cdf,
    )


def _penalty(beta: Sequence[float], lam: float) -> float:
    return lam / 2 * math.fsum(b * b for b in beta)


def _objective(theta, rows, cells, size, last, lam) -> float:
    """Penalized log likelihood, or -inf where the cuts are out of order."""
    beta, cuts = theta[:size], theta[size:]
    if any(upper <= lower for lower, upper in zip(cuts, cuts[1:])):
        return -math.inf
    return math.fsum(
        math.log(_probability(sum(map(mul, beta, row)), cell, cuts)[0])
        for row, cell in zip(rows, cells)
    ) - _penalty(beta, lam)


def _newton_terms(theta, rows, cells, size, last, lam):
    """Gradient of the penalized log likelihood, and minus its Hessian (lower triangle)."""
    beta, cuts = theta[:size], theta[size:]
    gradient = [-lam * b for b in beta] + [0.0] * last
    curvature = [[0.0] * (j + 1) for j in range(size + last)]
    for j in range(size):
        curvature[j][j] = lam
    for row, cell in zip(rows, cells):
        probability, upper_density, lower_density, upper_cdf, lower_cdf = _probability(
            sum(map(mul, beta, row)), cell, cuts,
        )
        # First and second derivatives of ln P in its upper and lower cut.
        d_upper = upper_density / probability
        d_lower = -lower_density / probability
        d_upper_upper = upper_density * (1 - 2 * upper_cdf) / probability - d_upper * d_upper
        d_lower_lower = -lower_density * (1 - 2 * lower_cdf) / probability - d_lower * d_lower
        d_upper_lower = -d_upper * d_lower
        # The index moves both cuts the other way.
        d_eta = -(d_upper + d_lower)
        d_eta_eta = d_upper_upper + 2 * d_upper_lower + d_lower_lower
        for j in range(size):
            head = row[j]
            gradient[j] += d_eta * head
            head *= -d_eta_eta
            line = curvature[j]
            line[:] = [total + head * x for total, x in zip(line, row)]
        if cell < last:
            upper = size + cell
            gradient[upper] += d_upper
            line = curvature[upper]
            head = d_upper_upper + d_upper_lower
            line[:size] = [total + head * x for total, x in zip(line, row)]
            line[upper] -= d_upper_upper
            if cell > 0:
                line[upper - 1] -= d_upper_lower
        if cell > 0:
            lower = size + cell - 1
            gradient[lower] += d_lower
            line = curvature[lower]
            head = d_upper_lower + d_lower_lower
            line[:size] = [total + head * x for total, x in zip(line, row)]
            line[lower] -= d_lower_lower
    return gradient, curvature


def _cholesky(matrix: Sequence[Sequence[float]]) -> Optional[List[List[float]]]:
    """Lower factor of a matrix given by its lower triangle, if positive definite."""
    factor: List[List[float]] = []
    for i, row in enumerate(matrix):
        line: List[float] = []
        for j in range(i):
            line.append((row[j] - sum(map(mul, line, factor[j]))) / factor[j][j])
        pivot = row[i] - sum(map(mul, line, line))
        if pivot <= 0:
            return None
        line.append(math.sqrt(pivot))
        factor.append(line)
    return factor


def _factor(matrix: Sequence[Sequence[float]]) -> List[List[float]]:
    """Cholesky factor, adding just enough diagonal when scores are collinear."""
    scale = max((row[-1] for row in matrix), default=0.0) or 1.0
    jitter = 0.0
    while True:
        factor = _cholesky([row[:-1] + [row[-1] + jitter] for row in matrix])
        if factor is not None:
            return factor
        jitter = jitter * 10 or scale * 1e-12


def _solve(factor: Sequence[Sequence[float]], target: Sequence[float]) -> List[float]:
    forward: List[float] = []
    for line, value in zip(factor, target):
        forward.append((value - sum(map(mul, line, forward))) / line[-1])
    solution = [0.0] * len(factor)
    for i in reversed(range(len(factor))):
        tail = sum(factor[k][i] * solution[k] for k in range(i + 1, len(factor)))
        solution[i] = (forward[i] - tail) / factor[i][i]
    return solution
