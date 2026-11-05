"""Ordered logit with fixed anchors, in pure Python.

Ratings sit on half doublings, so the cut points between them are known log
costs instead of free thresholds. Fixing them is what identifies the size of
the effect: intercept, weights and sigma all come out in log units, and
exp(eta) is directly an answer's credit.
"""

import math
from operator import mul
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple


# Halfway between the steps ¼ ⅓ ½ ¾ 1 1½ 2 3 4 6 8+, which round 2 ** (k / 2).
ANCHORS = tuple(math.log(2) * (cut / 2 - 1.75) for cut in range(10))

MAX_ITERATIONS = 200
MAX_HALVINGS = 40
MAX_STEP = 2.0
DAMPING_START = 1e-6
ARMIJO_SLOPE = 1e-4
DECREMENT_TOLERANCE = 1e-8
GRADIENT_TOLERANCE = 1e-7
MIN_PROBABILITY = 1e-300
MIN_START_SIGMA = 0.1

Cell = Tuple[float, float]


class OrderedFit(NamedTuple):
    intercept: float
    weights: Dict[str, float]  # per raw input unit
    se: Dict[str, float]
    sigma: float
    loglik: float
    iterations: int
    converged: bool
    names: Tuple[str, ...]
    control_names: Tuple[str, ...]
    anchors: Tuple[float, ...]


def fit_ordered(rows: Sequence[Sequence[float]], ratings: Sequence[int],
                names: Sequence[str], anchors: Sequence[float] = ANCHORS,
                lam: float = 2.0, control_names: Sequence[str] = ()) -> OrderedFit:
    """Fit cost ratings by BHHH-Newton.

    Standardizing makes lam mean the same for every input, whatever its unit.
    The intercept and sigma go unpenalized: they set the level and the noise,
    not an effect to shrink. Control columns are fitted like any other and only
    differ at prediction. A constant column gets weight and se 0.
    """
    _check_inputs(rows, ratings, names, anchors, control_names)
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
    cells = _cells(anchors)
    answer_cells = [cells[rating] for rating in ratings]

    theta = _starting_point(standardized, ratings, anchors, lam)
    iterations = 0
    while True:
        value, gradient, hessian = _newton_terms(theta, standardized, answer_cells, lam)
        factor = _factor(hessian)
        step = _solve(factor, gradient)
        converged = (
            math.fsum(map(mul, gradient, step)) < DECREMENT_TOLERANCE
            or max(map(abs, gradient)) < GRADIENT_TOLERANCE
        )
        if converged or iterations == MAX_ITERATIONS:
            break
        step = _damped_step(hessian, gradient, step)
        moved = _backtrack(theta, step, gradient, value, standardized, answer_cells, lam)
        if moved is None:
            break
        theta = moved
        iterations += 1

    beta = theta[:len(active)]
    weights = dict.fromkeys(names, 0.0)
    se = dict.fromkeys(names, 0.0)
    for j, i in enumerate(active):
        weights[names[i]] = beta[j] / scales[i]
        unit = [float(k == j) for k in range(len(theta))]
        se[names[i]] = math.sqrt(_solve(factor, unit)[j]) / scales[i]
    return OrderedFit(
        intercept=theta[-2] - math.fsum(
            beta[j] * means[i] / scales[i] for j, i in enumerate(active)
        ),
        weights=weights,
        se=se,
        sigma=math.exp(theta[-1]),
        loglik=_penalty(beta, lam) - value,
        iterations=iterations,
        converged=converged,
        names=tuple(names),
        control_names=tuple(control_names),
        anchors=tuple(anchors),
    )


def predict_eta(result: OrderedFit, row: Sequence[float]) -> float:
    """Log credit of a row aligned with result.names, controls at zero."""
    return result.intercept + math.fsum(
        result.weights[name] * value for name, value in zip(result.names, row)
        if name not in result.control_names
    )


def mean_log_loss(result: OrderedFit, rows: Sequence[Sequence[float]],
                  ratings: Sequence[int]) -> float:
    """Mean -ln P(rating), controls at zero: the model as scoring would use it."""
    cells = _cells(result.anchors)
    return -math.fsum(
        math.log(_probability(*_bounds(predict_eta(result, row), result.sigma, cells[rating])))
        for row, rating in zip(rows, ratings)
    ) / len(ratings)


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


def partial_rank_t(target: Sequence[float], rival: Sequence[float],
                   candidate: Sequence[float]) -> Tuple[float, float]:
    """(r, t) for what candidate explains of target that rival leaves unexplained.

    r is the rank correlation of the two after removing rival from each, so
    measures that mostly agree are compared on the part where they differ.
    t has len(target) - 3 degrees of freedom.
    """
    target_ranks, rival_ranks = _average_ranks(target), _average_ranks(rival)
    left = _residuals(target_ranks, rival_ranks)
    right = _residuals(_average_ranks(candidate), rival_ranks)
    spread = math.sqrt(math.fsum(x * x for x in left) * math.fsum(x * x for x in right))
    r = math.fsum(map(mul, left, right)) / spread if spread else 0.0
    return r, r * math.sqrt((len(target) - 3) / max(1 - r * r, 1e-12))


def one_sided_t_5_percent(degrees: int) -> float:
    """Student t for p = 0.05, one-sided, by the Cornish-Fisher expansion.

    The stdlib has no t distribution. This is within 0.01 from 5 degrees up.
    """
    z = 1.6449
    return (
        z
        + (z ** 3 + z) / (4 * degrees)
        + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * degrees ** 2)
    )


def _residuals(values: Sequence[float], on: Sequence[float]) -> List[float]:
    """values minus their least-squares line on on."""
    mean_x, mean_y = math.fsum(on) / len(on), math.fsum(values) / len(values)
    across = [x - mean_x for x in on]
    centered = [y - mean_y for y in values]
    spread = math.fsum(x * x for x in across)
    slope = math.fsum(map(mul, across, centered)) / spread if spread else 0.0
    return [y - slope * x for x, y in zip(across, centered)]


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


def _check_inputs(rows, ratings, names, anchors, control_names) -> None:
    if not rows or len(rows) != len(ratings):
        raise ValueError("fit_ordered needs one rating per row, and at least one row")
    if any(len(row) != len(names) for row in rows):
        raise ValueError("every row must have one value per name")
    if not all(math.isfinite(value) for row in rows for value in row):
        raise ValueError("rows must be finite")
    if len(set(names)) != len(names) or not set(control_names) <= set(names):
        raise ValueError("names must be unique and include every control")
    if any(not 0 <= rating <= len(anchors) for rating in ratings):
        raise ValueError("ratings must be between 0 and len(anchors)")


def _cells(anchors: Sequence[float]) -> List[Cell]:
    return list(zip((-math.inf, *anchors), (*anchors, math.inf)))


def _starting_point(standardized, ratings, anchors, lam) -> List[float]:
    """Ridge regression of each rating's cell center.

    Starting from beta = 0 would be a trap: ratings balanced across two cells
    then give every answer the same log sigma score, the outer product explains
    the whole gradient through sigma alone, and beta never moves.
    """
    centers = [anchors[0]]
    centers.extend((lower + upper) / 2 for lower, upper in zip(anchors, anchors[1:]))
    centers.append(anchors[-1])
    values = [centers[rating] for rating in ratings]
    mean = math.fsum(values) / len(values)
    size = len(standardized[0])
    normal = [[0.0] * (j + 1) for j in range(size)]
    target = [0.0] * size
    for row, value in zip(standardized, values):
        for j, line in enumerate(normal):
            target[j] += row[j] * (value - mean)
            head = row[j]
            line[:] = [total + head * x for total, x in zip(line, row)]
    for j in range(size):
        normal[j][j] += lam
    beta = _solve(_factor(normal), target)
    squares = math.fsum(
        (value - mean - sum(map(mul, beta, row))) ** 2
        for row, value in zip(standardized, values)
    )
    # A logistic of scale s has standard deviation s * pi / sqrt(3).
    scale = math.sqrt(squares / len(values)) * math.sqrt(3) / math.pi
    return beta + [mean, math.log(max(scale, MIN_START_SIGMA))]


def _logistic(u: float) -> float:
    if u < 0:
        tail = math.exp(u)  # exp(-u) would overflow
        return tail / (1 + tail)
    return 1 / (1 + math.exp(-u))


def _density(u: float) -> float:
    tail = math.exp(-abs(u))
    return tail / (1 + tail) ** 2


def _moment(u: float) -> float:
    """u * density(u), which tends to 0 at the unbounded ends of the scale."""
    return u * _density(u) if math.isfinite(u) else 0.0


def _bounds(eta: float, sigma: float, cell: Cell) -> Cell:
    return (cell[0] - eta) / sigma, (cell[1] - eta) / sigma


def _probability(lower: float, upper: float) -> float:
    if lower > 0:  # Subtract upper tails, or both ends round to 1.
        probability = _logistic(-lower) - _logistic(-upper)
    else:
        probability = _logistic(upper) - _logistic(lower)
    return max(probability, MIN_PROBABILITY)


def _penalty(beta: Sequence[float], lam: float) -> float:
    return lam / 2 * math.fsum(b * b for b in beta)


def _objective(theta, standardized, cells, lam) -> float:
    beta, intercept, sigma = theta[:-2], theta[-2], math.exp(theta[-1])
    return _penalty(beta, lam) - math.fsum(
        math.log(_probability(*_bounds(intercept + sum(map(mul, beta, row)), sigma, cell)))
        for row, cell in zip(standardized, cells)
    )


def _newton_terms(theta, standardized, cells, lam):
    """Objective, gradient and BHHH curvature (lower triangle only)."""
    size = len(theta)
    beta, intercept, sigma = theta[:-2], theta[-2], math.exp(theta[-1])
    log_probabilities = []
    gradient = [lam * b for b in beta] + [0.0, 0.0]
    hessian = [[0.0] * (j + 1) for j in range(size)]
    for j in range(len(beta)):
        hessian[j][j] = lam
    for row, cell in zip(standardized, cells):
        lower, upper = _bounds(intercept + sum(map(mul, beta, row)), sigma, cell)
        probability = _probability(lower, upper)
        log_probabilities.append(math.log(probability))
        d_eta = (_density(lower) - _density(upper)) / (sigma * probability)
        d_s = (_moment(lower) - _moment(upper)) / probability
        score = [d_eta * x for x in row]
        score += (d_eta, d_s)
        for j, line in enumerate(hessian):
            gradient[j] -= score[j]
            head = score[j]
            line[:] = [total + head * x for total, x in zip(line, score)]
    return _penalty(beta, lam) - math.fsum(log_probabilities), gradient, hessian


def _damped_step(hessian, gradient, step) -> List[float]:
    """Levenberg damping until the step is short.

    Where an answer's score nears 0 its outer product vanishes faster than the
    gradient, so the raw BHHH step blows up along one axis. Scaling it down
    would freeze the other axes: damping turns it toward the gradient instead.
    """
    damping = max(line[-1] for line in hessian) * DAMPING_START
    while max(map(abs, step)) > MAX_STEP:
        damped = [line[:-1] + [line[-1] + damping] for line in hessian]
        step = _solve(_factor(damped), gradient)
        damping *= 10
    return step


def _backtrack(theta, step, gradient, value, standardized, cells, lam) -> Optional[List[float]]:
    slope = math.fsum(map(mul, gradient, step))
    shrink = 1.0
    for _ in range(MAX_HALVINGS):
        moved = [t - shrink * s for t, s in zip(theta, step)]
        target = value - ARMIJO_SLOPE * shrink * slope
        if _objective(moved, standardized, cells, lam) <= target:
            return moved
        shrink /= 2
    return None


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
