import math
import random

import pytest

from review_heatmap.calibration.credit import (
    DEVIANCE_90, FALLBACK_B, PROFILE_RANGE, RULE_ENDS, Source, _profile, fit_credit,
    profile_interval, settle, usable,
)

# Wide enough that no credit is clamped, unless a test asks for it.
WIDE = (-50.0, 50.0)


def centred(values):
    mean = sum(values) / len(values)
    return [value - mean for value in values]


def world(days, b, k, seed, noise=0.2, same_duration=False):
    """Days rated on 5 levels by their summed credit exp(b * log_d + k * difficulty).

    Days differ in pace and size, so count and time disagree between them."""
    rng = random.Random(seed)
    log_d, difficulty, groups, latent = [], [], [], []
    for _ in range(days):
        pace = rng.gauss(0, 0.5)
        start = len(log_d)
        total = 0.0
        for _ in range(rng.randint(15, 45)):
            duration = 0.0 if same_duration else pace + rng.gauss(0, 0.7)
            hard = rng.gauss(0, 1)
            log_d.append(duration)
            difficulty.append(hard)
            total += math.exp(b * duration + k * hard)
        groups.append(list(range(start, len(log_d))))
        latent.append(math.log(total) + rng.gauss(0, noise))
    order = sorted(latent)
    cuts = [order[int(len(order) * share)] for share in (0.2, 0.4, 0.6, 0.8)]
    ratings = [1 + sum(value > cut for cut in cuts) for value in latent]
    return centred(log_d), centred(difficulty), [Source("amount", ratings, groups)]


@pytest.fixture(scope="module")
def planted():
    return world(120, 0.8, 0.5, seed=1)


def test_recovers_the_planted_time_exponent_and_difficulty_weight(planted):
    log_d, difficulty, sources = planted
    fit = fit_credit(sources, log_d, difficulty, WIDE)
    assert fit.converged
    assert fit.b == pytest.approx(0.8, abs=0.1)
    assert fit.k == pytest.approx(0.5, abs=0.15)


def test_a_fixed_b_or_k_leaves_the_other_to_the_ratings(planted):
    log_d, difficulty, sources = planted
    free = fit_credit(sources, log_d, difficulty, WIDE)
    at_b = fit_credit(sources, log_d, difficulty, WIDE, fixed_b=FALLBACK_B)
    at_k = fit_credit(sources, log_d, difficulty, WIDE, fixed_k=0.0)
    assert (at_b.b, at_k.k) == (FALLBACK_B, 0.0)
    assert at_b.k > 0 and at_k.b > 0
    assert free.loglik >= max(at_b.loglik, at_k.loglik) - 1e-9


def test_pairs_alone_place_b():
    """Many quick answers against a few slow ones: how they compare is set by b."""
    rng = random.Random(3)
    log_d, groups, ratings = [], [], []
    for _ in range(120):
        sides = []
        for mean, low, high in ((-1.0, 20, 60), (1.2, 3, 12)):
            start = len(log_d)
            log_d += [rng.gauss(mean, 0.3) for _ in range(rng.randint(low, high))]
            sides.append(list(range(start, len(log_d))))
        first, second = (math.log(sum(math.exp(0.7 * log_d[i]) for i in side)) for side in sides)
        gap = second - first + rng.gauss(0, 0.2)
        ratings.append(1 + sum(gap > cut for cut in (-0.9, -0.25, 0.25, 0.9)))
        groups += sides
    pair = Source("pair", ratings, groups, pair=True)
    fit = fit_credit([pair], centred(log_d), [0.0] * len(log_d), WIDE, fixed_k=0.0)
    assert fit.b == pytest.approx(0.7, abs=0.1)


@pytest.mark.parametrize("eta_range", [WIDE, (-0.6, 0.6)], ids=["free", "clamped"])
def test_the_gradient_is_the_derivative_of_the_profile(planted, eta_range):
    log_d, difficulty, sources = planted
    _, gradient = _profile(sources, log_d, difficulty, eta_range, 0.6, 0.3, {})
    step = 1e-5
    for index, (move_b, move_k) in enumerate(((step, 0.0), (0.0, step))):
        up = _profile(sources, log_d, difficulty, eta_range, 0.6 + move_b, 0.3 + move_k, {})[0]
        down = _profile(sources, log_d, difficulty, eta_range, 0.6 - move_b, 0.3 - move_k, {})[0]
        assert gradient[index] == pytest.approx((up - down) / (2 * step), rel=1e-3, abs=1e-5)


def test_clamped_answers_no_longer_move_with_b_or_k(planted):
    log_d, difficulty, sources = planted
    # Every credit sits at the lower limit: only the number of answers is left.
    value, gradient = _profile(sources, log_d, difficulty, (40.0, 50.0), 0.6, 0.3, {})
    assert gradient == [0.0, 0.0]
    assert value == pytest.approx(
        _profile(sources, log_d, difficulty, (40.0, 50.0), 1.1, -0.2, {})[0]
    )


def test_the_interval_holds_the_estimate_and_ends_at_the_90_percent_deviance(planted):
    log_d, difficulty, sources = planted
    top = fit_credit(sources, log_d, difficulty, WIDE)
    low, high = profile_interval(sources, log_d, difficulty, WIDE, top)
    assert PROFILE_RANGE[0] < low < top.b < high < PROFILE_RANGE[1]
    for end in (low, high):
        fixed = fit_credit(sources, log_d, difficulty, WIDE, fixed_b=end, starts=((end, top.k),))
        assert 2 * (top.loglik - fixed.loglik) == pytest.approx(DEVIANCE_90, abs=0.15)


def test_ratings_that_place_b_keep_their_estimate(planted):
    settled = settle(planted[2], planted[0], planted[1], WIDE, with_interval=True)
    assert not settled.fell_back
    assert (settled.b, settled.k) == (settled.fit.b, settled.fit.k)
    assert settled.interval[0] < settled.b < settled.interval[1]


def test_ratings_that_cannot_tell_count_from_time_keep_review_weighted():
    """Every answer took as long as every other: b changes nothing they rate."""
    log_d, difficulty, sources = world(60, 0.8, 0.5, seed=2, same_duration=True)
    settled = settle(sources, log_d, difficulty, WIDE, with_interval=True)
    assert settled.fell_back and settled.b == FALLBACK_B
    assert settled.interval == (PROFILE_RANGE[0], PROFILE_RANGE[1])
    assert RULE_ENDS[0] > PROFILE_RANGE[0] and RULE_ENDS[1] < PROFILE_RANGE[1]
    fixed = fit_credit(sources, log_d, difficulty, WIDE, fixed_b=FALLBACK_B)
    assert settled.k == pytest.approx(fixed.k)
    assert settled.k == pytest.approx(0.5, abs=0.2)


def test_thin_or_flat_kinds_of_rating_say_nothing():
    thin = Source("amount", [1, 2, 3, 1], [[0]] * 4)
    flat = Source("work", [3] * 10, [[0]] * 10)
    kept = Source("pair", [1, 2] * 5, [[0]] * 20, pair=True)
    assert usable([thin, flat, kept]) == [kept]
    assert fit_credit([], [], [], WIDE) is None
    assert settle([], [], [], WIDE) is None
