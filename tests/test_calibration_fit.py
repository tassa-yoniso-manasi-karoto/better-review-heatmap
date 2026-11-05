import importlib.util
import math
import random
import time
from bisect import bisect_left
from pathlib import Path

import pytest

from review_heatmap.calibration.fit import (
    ANCHORS, fit_ordered, mean_log_loss, one_sided_t_5_percent, partial_rank_t,
    predict_eta, spearman,
)


LOG2 = math.log(2)


def simulate(weights, intercept, sigma, count, seed, anchors=ANCHORS,
             means=None, scales=None):
    """Draw ratings from the fitted model itself: latent log cost, then a cell."""
    rng = random.Random(seed)
    means = means or [0.0] * len(weights)
    scales = scales or [1.0] * len(weights)
    rows, ratings = [], []
    for _ in range(count):
        row = [rng.gauss(mean, scale) for mean, scale in zip(means, scales)]
        eta = intercept + sum(w * x for w, x in zip(weights, row))
        draw = rng.random()
        latent = eta + sigma * math.log(draw / (1 - draw))
        rows.append(row)
        ratings.append(bisect_left(anchors, latent))
    return rows, ratings


def test_anchors_are_halfway_between_the_half_doubling_ratings():
    steps = [math.log(0.25) + k * LOG2 / 2 for k in range(11)]  # up to ln 8
    assert steps[4] == pytest.approx(0) and steps[10] == pytest.approx(math.log(8))
    assert ANCHORS == pytest.approx([(low + high) / 2 for low, high in zip(steps, steps[1:])])


def test_recovers_known_weights_and_sigma_in_raw_units():
    names = ["log_d", "new", "minutes"]
    truth = [0.6, 0.9, -0.05]
    means = [1.0, 0.3, 40.0]
    rows, ratings = simulate(truth, 0.2, 0.5, 4000, seed=1, means=means, scales=[1.2, 0.5, 15.0])
    fit = fit_ordered(rows, ratings, names)
    assert fit.converged
    for name, weight in zip(names, truth):
        assert abs(fit.weights[name] - weight) < 3 * fit.se[name]
    assert fit.sigma == pytest.approx(0.5, rel=0.05)
    level = 0.2 + sum(w * mean for w, mean in zip(truth, means))
    assert predict_eta(fit, means) == pytest.approx(level, abs=0.03)


def test_saturated_fit_lands_on_the_closed_form():
    anchors = (-0.5, 0.75)
    ratings = [0] * 20 + [1] * 50 + [2] * 30
    fit = fit_ordered([[]] * 100, ratings, [], anchors=anchors)
    low, high = math.log(0.2 / 0.8), math.log(0.7 / 0.3)
    sigma = (anchors[1] - anchors[0]) / (high - low)
    assert fit.converged
    assert fit.sigma == pytest.approx(sigma, rel=1e-5)
    assert fit.intercept == pytest.approx(anchors[0] - sigma * low, rel=1e-5)
    assert fit.loglik == pytest.approx(
        20 * math.log(0.2) + 50 * math.log(0.5) + 30 * math.log(0.3)
    )


def test_a_stretched_rating_scale_scales_sigma_and_weights():
    # A rater who goes a step higher for half the real change in log cost uses
    # cells half as wide. Free thresholds would absorb that; anchors can't.
    truth = [0.3, -0.2]
    narrow = tuple(cut / 2 for cut in ANCHORS)
    honest = fit_ordered(*simulate(truth, 0.1, 0.3, 4000, seed=2), ["a", "b"])
    rows, stretched = simulate(truth, 0.1, 0.3, 4000, seed=2, anchors=narrow)
    inflated = fit_ordered(rows, stretched, ["a", "b"])
    assert inflated.sigma / honest.sigma == pytest.approx(2, rel=0.1)
    assert inflated.weights["a"] / honest.weights["a"] == pytest.approx(2, rel=0.1)
    assert inflated.weights["b"] / honest.weights["b"] == pytest.approx(2, rel=0.1)


def test_doubling_the_anchors_doubles_every_estimate_exactly():
    rows, ratings = simulate([0.5, 0.2], 0.1, 0.6, 500, seed=3)
    base = fit_ordered(rows, ratings, ["a", "b"], lam=0)
    doubled = fit_ordered(
        rows, ratings, ["a", "b"], anchors=[2 * cut for cut in ANCHORS], lam=0,
    )
    assert doubled.sigma == pytest.approx(2 * base.sigma, rel=1e-5)
    assert doubled.intercept == pytest.approx(2 * base.intercept, rel=1e-4)
    assert doubled.weights["a"] == pytest.approx(2 * base.weights["a"], rel=1e-5)
    assert doubled.loglik == pytest.approx(base.loglik)


def test_input_units_do_not_change_the_fit():
    rows, ratings = simulate([0.5, 0.2], 0.1, 0.6, 500, seed=4)
    rescaled = [[100 * a + 7, b] for a, b in rows]
    base = fit_ordered(rows, ratings, ["a", "b"])
    fit = fit_ordered(rescaled, ratings, ["a", "b"])
    assert fit.weights["a"] == pytest.approx(base.weights["a"] / 100, rel=1e-5)
    assert fit.se["a"] == pytest.approx(base.se["a"] / 100, rel=1e-5)
    assert fit.loglik == pytest.approx(base.loglik)
    assert predict_eta(fit, rescaled[0]) == pytest.approx(predict_eta(base, rows[0]))


def test_penalty_shrinks_weights_but_not_level_or_sigma():
    rows, ratings = simulate([0.5, 0.2], 0.1, 0.6, 500, seed=5)
    alone = fit_ordered([[]] * len(rows), ratings, [])
    shrunk = fit_ordered(rows, ratings, ["a", "b"], lam=1e7)
    assert max(map(abs, shrunk.weights.values())) < 1e-3
    assert shrunk.sigma == pytest.approx(alone.sigma, rel=1e-3)
    assert shrunk.intercept == pytest.approx(alone.intercept, rel=1e-3)


def test_constant_column_gets_weight_zero():
    rows, ratings = simulate([0.5], 0.1, 0.6, 500, seed=6)
    fit = fit_ordered([[a, 3.0, 0.0] for a, in rows], ratings, ["a", "flat", "zero"])
    base = fit_ordered(rows, ratings, ["a"])
    assert fit.converged
    assert fit.weights["flat"] == fit.weights["zero"] == 0
    assert fit.se["flat"] == fit.se["zero"] == 0
    assert fit.weights["a"] == pytest.approx(base.weights["a"])
    assert fit.intercept == pytest.approx(base.intercept)


@pytest.mark.parametrize("unused", [0, 1, 3, 5, 10])
def test_unused_rating_category_changes_nothing_structural(unused):
    rows, ratings = simulate([0.5, 0.2], 0.1, 0.6, 600, seed=7)
    kept = [i for i, rating in enumerate(ratings) if rating != unused]
    fit = fit_ordered([rows[i] for i in kept], [ratings[i] for i in kept], ["a", "b"])
    assert fit.converged
    assert fit.sigma > 0 and math.isfinite(fit.loglik)
    assert fit.weights["a"] > 0


@pytest.mark.parametrize("rating", [0, 2, 5, 10])
def test_single_rating_returns_a_finite_flat_fit(rating):
    fit = fit_ordered([[float(i)] for i in range(50)], [rating] * 50, ["a"])
    assert fit.weights["a"] == pytest.approx(0, abs=1e-6)
    assert fit.loglik == pytest.approx(0, abs=1e-5)
    assert math.isfinite(fit.intercept) and math.isfinite(fit.sigma)


def test_balanced_ratings_on_two_cells_still_learn_the_weight():
    # At beta = 0 these give every answer the same log sigma score.
    rows = [[float(i)] for i in range(40)]
    fit = fit_ordered(rows, [0] * 20 + [10] * 20, ["a"])
    assert fit.converged
    assert fit.weights["a"] > 0.1
    assert fit.loglik > -20


def test_rejects_ratings_outside_the_scale():
    with pytest.raises(ValueError):
        fit_ordered([[1.0], [2.0]], [0, 11], ["a"])
    with pytest.raises(ValueError):
        fit_ordered([[1.0], [math.nan]], [0, 1], ["a"])


def test_predict_eta_sets_controls_to_zero():
    rows, ratings = simulate([0.5, 0.2], 0.1, 0.6, 500, seed=8)
    fit = fit_ordered(rows, ratings, ["a", "sharpness"], control_names=["sharpness"])
    assert fit.weights["sharpness"] != 0
    assert predict_eta(fit, [2.0, 9.0]) == pytest.approx(
        fit.intercept + 2.0 * fit.weights["a"]
    )
    assert predict_eta(fit, [2.0, 9.0]) == predict_eta(fit, [2.0, -9.0])


def test_mean_log_loss_matches_loglik_and_beats_intercept_only():
    rows, ratings = simulate([0.8], 0.1, 0.4, 800, seed=9)
    train, held_out = rows[:600], rows[600:]
    fit = fit_ordered(train, ratings[:600], ["a"])
    alone = fit_ordered([[0.0]] * 600, ratings[:600], ["a"])
    assert mean_log_loss(fit, train, ratings[:600]) == pytest.approx(-fit.loglik / 600)
    assert mean_log_loss(fit, held_out, ratings[600:]) < (
        mean_log_loss(alone, held_out, ratings[600:]) - 0.1
    )


def test_spearman_averages_tied_ranks():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1)
    # Ranks 1, 2.5, 2.5, 4 against 1, 2, 3, 4.
    assert spearman([1, 5, 5, 9], [1, 2, 3, 4]) == pytest.approx(4.5 / math.sqrt(4.5 * 5))


def test_spearman_without_spread_is_zero():
    assert spearman([3, 3, 3], [1, 2, 3]) == 0
    with pytest.raises(ValueError):
        spearman([1, 2], [1])


def test_partial_rank_sees_what_a_measure_adds_to_its_rival():
    rng = random.Random(4)
    rival = [rng.random() for _ in range(60)]
    extra = [rng.random() for _ in range(60)]
    target = [a + b for a, b in zip(rival, extra)]
    r, t = partial_rank_t(target, rival, extra)
    assert r > 0.8 and t > 5
    r, t = partial_rank_t(target, rival, [rng.random() for _ in range(60)])
    assert abs(r) < 0.35
    r, t = partial_rank_t(target, rival, [-x for x in extra])
    assert r < -0.8 and t < -5


def test_a_measure_adds_nothing_to_itself():
    rival = [1.0, 3.0, 2.0, 5.0, 4.0]
    assert partial_rank_t([1, 2, 3, 4, 5], rival, rival) == (0.0, 0.0)


def test_partial_rank_survives_a_perfect_match():
    rival = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    candidate = [1.0, 3.0, 2.0, 5.0, 4.0, 6.0]
    r, t = partial_rank_t(candidate, rival, candidate)
    assert r == pytest.approx(1.0) and math.isfinite(t)


@pytest.mark.parametrize("degrees, table", [
    (5, 2.015), (10, 1.812), (15, 1.753), (24, 1.711), (51, 1.675), (120, 1.658),
])
def test_the_critical_t_matches_the_table(degrees, table):
    assert one_sided_t_5_percent(degrees) == pytest.approx(table, abs=0.01)


def test_two_thousand_ratings_by_twenty_five_inputs_fit_within_a_minute():
    rng = random.Random(10)
    truth = [rng.uniform(-0.3, 0.3) for _ in range(25)]
    rows, ratings = simulate(truth, 0.2, 0.6, 2000, seed=10)
    started = time.perf_counter()
    fit = fit_ordered(rows, ratings, ["x%d" % i for i in range(25)])
    assert time.perf_counter() - started < 60
    assert fit.converged


def load_reference():
    path = Path(__file__).resolve().parents[1] / "scripts" / "research" / "ordlogit.py"
    if not path.exists():  # gitignored
        pytest.skip("research scripts are not in this checkout")
    spec = importlib.util.spec_from_file_location("ordlogit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.OrderedLogit


def test_matches_the_numpy_reference():
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    rows, ratings = simulate([0.5, -0.3, 0.1], 0.2, 0.7, 2000, seed=11)
    names = ["a", "b", "c"]
    matrix = numpy.array(rows)
    means, scales = matrix.mean(axis=0), matrix.std(axis=0)
    reference = load_reference()(lam=2.0, anchors=ANCHORS).fit(
        (matrix - means) / scales, ratings, K=len(ANCHORS) + 1,
    )
    fit = fit_ordered(rows, ratings, names)
    weights = numpy.array([fit.weights[name] for name in names])
    assert weights * scales == pytest.approx(reference.beta, abs=1e-4)
    assert fit.sigma == pytest.approx(reference.sigma, abs=1e-4)
    assert fit.intercept + weights @ means == pytest.approx(reference.intercept, abs=1e-4)


def test_spearman_matches_scipy():
    stats = pytest.importorskip("scipy.stats")
    rng = random.Random(12)
    a = [rng.randint(0, 9) for _ in range(200)]
    b = [x + rng.randint(-4, 4) for x in a]
    assert spearman(a, b) == pytest.approx(stats.spearmanr(a, b).statistic)
