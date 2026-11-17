import math
import random
import time

import pytest

from review_heatmap.calibration.fit import (
    ONE_SIDED_5_PERCENT, added_t, fit_ordered, mean_log_loss, ordered_logit, ordinal_t,
    predict_eta, rating_score, spearman,
)

CUTS = (-1.5, 0.0, 1.0, 2.5)


def logistic_draw(rng):
    draw = rng.random()
    return math.log(draw / (1 - draw))


def simulate(weights, count, seed, cuts=CUTS, scales=None):
    """Rows of standard normal inputs times scales, rated through known cut points."""
    rng = random.Random(seed)
    scales = scales or [1.0] * len(weights)
    rows, ratings = [], []
    for _ in range(count):
        row = [rng.gauss(0, 1) * scale for scale in scales]
        latent = sum(w * x for w, x in zip(weights, row)) + logistic_draw(rng)
        rows.append(row)
        ratings.append(1 + sum(latent > cut for cut in cuts))
    return rows, ratings


def test_recovers_known_weights_and_cuts_in_raw_units():
    weights = (0.8, -0.5, 0.3)
    rows, ratings = simulate(weights, 3000, seed=1, scales=(1.0, 4.0, 0.25))
    fit = fit_ordered(rows, ratings, ["a", "b", "c"], lam=0.0)
    assert fit.converged and fit.levels == (1, 2, 3, 4, 5)
    for name, true in zip("abc", weights):
        assert fit.weights[name] == pytest.approx(true, abs=4 * fit.se[name])
    assert fit.cuts == pytest.approx(CUTS, abs=0.15)


def test_input_units_and_shifts_do_not_change_what_the_fit_says():
    rows, ratings = simulate((0.6, -0.4), 800, seed=2)
    base = fit_ordered(rows, ratings, ["a", "b"])
    moved = fit_ordered([[10 * a + 3, b] for a, b in rows], ratings, ["a", "b"])
    assert moved.weights["a"] == pytest.approx(base.weights["a"] / 10)
    assert moved.weights["b"] == pytest.approx(base.weights["b"])
    assert moved.loglik == pytest.approx(base.loglik)
    # The shift lands in the cut points, by the weight it was given.
    assert moved.cuts == pytest.approx([cut + 3 * moved.weights["a"] for cut in base.cuts])


def test_penalty_shrinks_weights_and_leaves_the_levels():
    rows, ratings = simulate((0.6, -0.4), 300, seed=3)
    loose = fit_ordered(rows, ratings, ["a", "b"], lam=0.0)
    tight = fit_ordered(rows, ratings, ["a", "b"], lam=500.0)
    assert abs(tight.weights["a"]) < abs(loose.weights["a"]) / 2
    assert tight.levels == loose.levels and tight.converged


def test_a_constant_column_gets_weight_and_se_zero():
    rows, ratings = simulate((0.6,), 200, seed=4)
    fit = fit_ordered([[x, 7.0] for x, in rows], ratings, ["a", "flat"])
    assert (fit.weights["flat"], fit.se["flat"]) == (0.0, 0.0)
    assert fit.weights["a"] > 0


def test_only_levels_that_occur_get_a_cell():
    rows, ratings = simulate((0.6,), 400, seed=5)
    sparse = [{1: 1, 2: 1, 3: 4, 4: 6, 5: 6}[rating] for rating in ratings]
    fit = ordered_logit(rows, sparse)
    assert fit.levels == (1, 4, 6) and len(fit.cuts) == 2
    assert fit.cuts[0] < fit.cuts[1]


def test_a_single_level_gives_a_flat_fit_flagged_unconverged():
    fit = ordered_logit([[1.0], [2.0]], [3, 3])
    assert (fit.beta, fit.cuts, fit.levels, fit.converged) == ([0.0], [], (3,), False)


def test_ratings_told_apart_perfectly_come_back_with_a_huge_weight():
    rows = [[float(x)] for x in range(-10, 10)]
    fit = ordered_logit(rows, [int(x >= 0) for x in range(-10, 10)])
    assert fit.beta[0] > 20 and math.isfinite(fit.loglik)
    # A penalty keeps it finite and modest.
    assert ordered_logit(rows, [int(x >= 0) for x in range(-10, 10)], lam=1.0).beta[0] < 5


def test_a_warm_start_far_away_is_ignored():
    rows, ratings = simulate((0.6,), 300, seed=6)
    cold = ordered_logit(rows, ratings)
    warm = ordered_logit(rows, ratings, start=([400.0], [-900.0, -800.0, 700.0, 900.0]))
    assert warm.beta == pytest.approx(cold.beta) and warm.cuts == pytest.approx(cold.cuts)


def test_predict_eta_leaves_out_controls_and_named_inputs():
    rows, ratings = simulate((0.6, -0.4, 0.3), 300, seed=7)
    fit = fit_ordered(rows, ratings, ["a", "time", "focus"], control_names=["focus"])
    row = [1.0, 2.0, 3.0]
    assert predict_eta(fit, row) == pytest.approx(fit.weights["a"] + 2 * fit.weights["time"])
    assert predict_eta(fit, row, without=("time",)) == pytest.approx(fit.weights["a"])


def test_mean_log_loss_beats_the_levels_alone_and_takes_unseen_levels():
    rows, ratings = simulate((1.2,), 600, seed=8)
    fit = fit_ordered(rows[:400], ratings[:400], ["a"])
    levels = fit_ordered([[0.0]] * 400, ratings[:400], ["a"])
    held_rows, held = rows[400:], ratings[400:]
    assert mean_log_loss(fit, held_rows, held) < mean_log_loss(levels, held_rows, held)
    # A level the fit never saw counts as the next one it did, or the highest.
    capped = fit_ordered(rows[:400], [min(rating, 3) for rating in ratings[:400]], ["a"])
    assert capped.levels == (1, 2, 3)
    assert mean_log_loss(capped, [[0.0]], [5]) == pytest.approx(mean_log_loss(capped, [[0.0]], [3]))


@pytest.mark.parametrize("cell", [0, 1, 4])
def test_rating_score_is_the_derivative_of_the_rating_log_probability(cell):
    from review_heatmap.calibration.fit import _probability

    eta, step = 0.37, 1e-6
    numeric = (
        math.log(_probability(eta + step, cell, CUTS)[0])
        - math.log(_probability(eta - step, cell, CUTS)[0])
    ) / (2 * step)
    assert rating_score(eta, cell, CUTS) == pytest.approx(numeric, rel=1e-6)


def test_spearman_averages_tied_ranks():
    assert spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9486833)


def test_spearman_without_spread_is_zero():
    assert spearman([1, 1, 1], [1, 2, 3]) == 0.0
    with pytest.raises(ValueError):
        spearman([1, 2], [1])


def judged_world(rng, days, added):
    """Days whose rating follows log truth = log rival + added * gap, on 7 levels."""
    cuts = [math.log(c) for c in (0.25, 0.55, 0.85, 1.2, 1.8, 3.0)]
    rival, candidate, target = [], [], []
    for _ in range(days):
        base, gap = rng.gauss(0, 0.6), rng.gauss(0, 0.15)
        target.append(sum(base + added * gap + rng.gauss(0, 0.3) > cut for cut in cuts))
        rival.append(math.exp(base))
        candidate.append(math.exp(base + 0.8 * gap + rng.gauss(0, 0.05)))
    return target, rival, candidate


def test_ordinal_t_finds_what_a_candidate_adds_to_its_rival():
    rng = random.Random(9)
    assert ordinal_t(*judged_world(rng, 200, 1.0)) > 3


def test_ordinal_t_of_a_rival_plus_noise_stays_standard_normal():
    """The partial rank test it replaces passed such candidates 8 to 14% of the time."""
    rng = random.Random(10)
    t = [ordinal_t(*judged_world(rng, 40, 0.0)) for _ in range(400)]
    mean = sum(t) / len(t)
    assert abs(mean) < 0.15
    assert sum(value >= ONE_SIDED_5_PERCENT for value in t) / len(t) < 0.09


def test_ordinal_t_is_zero_when_it_cannot_be_told():
    target, rival, _ = judged_world(random.Random(11), 30, 0.0)
    assert ordinal_t(target, rival, [2 * score for score in rival]) == 0.0  # no spread
    assert ordinal_t(target, rival, [0.0, *rival[1:]]) == 0.0  # a score at 0
    assert ordinal_t([3] * 30, rival, rival[::-1]) == 0.0  # one level
    assert ordinal_t([], [], []) == 0.0


def test_added_t_reads_the_last_column_once_the_others_are_known():
    rng = random.Random(12)
    first = [rng.gauss(0, 1) for _ in range(300)]
    second = [rng.gauss(0, 1) for _ in range(300)]
    target = [int(a + 0.8 * b + logistic_draw(rng) > 0) for a, b in zip(first, second)]
    assert added_t(target, [first, second]) > 3
    assert abs(added_t(target, [first, [rng.gauss(0, 1) for _ in range(300)]])) < 3


def test_two_thousand_ratings_by_twenty_inputs_fit_in_seconds():
    rows, ratings = simulate([0.3] * 20, 2000, seed=13)
    started = time.perf_counter()
    fit = fit_ordered(rows, ratings, [f"x{i}" for i in range(20)])
    assert fit.converged
    assert time.perf_counter() - started < 10


def test_matches_a_general_optimizer():
    optimize = pytest.importorskip("scipy.optimize")
    rows, ratings = simulate((0.9, -0.6, 0.2), 500, seed=14)
    fit = ordered_logit(rows, ratings)

    def negative(theta):
        beta, start, gaps = theta[:3], theta[3], theta[4:]
        cuts = [start]
        for gap in gaps:
            cuts.append(cuts[-1] + math.exp(gap))
        from review_heatmap.calibration.fit import _objective
        return -_objective([*beta, *cuts], rows, [r - 1 for r in ratings], 3, 4, 0.0)

    best = optimize.minimize(negative, [0, 0, 0, -1, 0, 0, 0], method="BFGS",
                             options={"gtol": 1e-8})
    assert fit.loglik == pytest.approx(-best.fun, abs=1e-6)
    assert fit.beta == pytest.approx(list(best.x[:3]), abs=1e-4)
