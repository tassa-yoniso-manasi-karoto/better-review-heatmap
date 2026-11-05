import random
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from review_heatmap.calibration.sampling import (
    answer_probability,
    evening_begins_ms,
    is_block_start,
    prompt_counts,
    prompts_open,
    prompts_since,
    sample_answer,
    why_probability,
)
from review_heatmap.calibration.store import DAY_MS, DEFAULT_STATE

NOW = 1793836800000
GOOD, AGAIN = 3, 1
QUICK, LONG = 5000, 61000
DRAWS = 40000


def samples_of(rng, **answer):
    answer = {"taken_ms": QUICK, "is_new": False, "ease": GOOD, "was_review": True, **answer}
    return [sample_answer(DEFAULT_STATE, rng, **answer) for _ in range(DRAWS)]


@pytest.mark.parametrize("taken_ms, is_new, expected", [
    (QUICK, False, 0.06),
    (60000, False, 0.06),  # strictly over a minute
    (LONG, False, 0.42),
    (QUICK, True, 0.18),
    (LONG, True, 0.54),
])
def test_answer_probability_follows_the_weights(taken_ms, is_new, expected):
    assert answer_probability(DEFAULT_STATE, taken_ms, is_new) == pytest.approx(expected)


def test_answer_probability_is_a_probability():
    assert answer_probability(dict(DEFAULT_STATE, answer_rate=0.5), LONG, True) == 1


@pytest.mark.parametrize("ease, was_review, expected", [
    (AGAIN, True, 0.3), (AGAIN, False, 0), (GOOD, True, 0), (2, True, 0),
])
def test_why_needs_again_on_a_card_in_review_state(ease, was_review, expected):
    assert why_probability(DEFAULT_STATE, ease, was_review) == expected


@pytest.mark.parametrize("answer, expected", [
    (dict(), 0.06),
    (dict(taken_ms=LONG), 0.42),
    (dict(is_new=True, was_review=False), 0.18),
])
def test_sampling_hits_its_rates_and_logs_them(answer, expected):
    asked = [sample for sample in samples_of(random.Random(1), **answer) if sample]
    assert len(asked) / DRAWS == pytest.approx(expected, abs=0.01)
    assert all(sample.answer_p == pytest.approx(expected) for sample in asked)


def test_lapse_sampling_is_independent_of_the_cost_question():
    samples = samples_of(random.Random(2), ease=AGAIN)
    why = sum(bool(sample and sample.why) for sample in samples) / DRAWS
    cost = sum(bool(sample and sample.rotation) for sample in samples) / DRAWS
    both = sum(bool(sample and sample.why and sample.rotation) for sample in samples)
    assert why == pytest.approx(0.3, abs=0.01)
    assert cost == pytest.approx(0.06, abs=0.01)
    assert both / DRAWS == pytest.approx(0.3 * 0.06, abs=0.005)
    assert {sample.why_p for sample in samples if sample} == {0.3}


def test_why_alone_has_no_rotating_question():
    rng = SimpleNamespace(random=iter([0.5, 0.1]).__next__, choice=None)
    sample = sample_answer(DEFAULT_STATE, rng, QUICK, False, AGAIN, True)
    assert sample.why and sample.rotation is None


def test_rotation_is_even():
    state = dict(DEFAULT_STATE, answer_rate=1)
    rng = random.Random(3)
    rotations = [
        sample_answer(state, rng, QUICK, False, GOOD, True).rotation
        for _ in range(DRAWS)
    ]
    assert rotations.count("gain") / DRAWS == pytest.approx(0.5, abs=0.01)
    assert rotations.count("grasp") == DRAWS - rotations.count("gain")


def test_the_draws_come_from_the_injected_rng():
    def draws(seed):
        rng = random.Random(seed)
        return [sample_answer(DEFAULT_STATE, rng, LONG, False, AGAIN, True) for _ in range(50)]

    assert draws(4) == draws(4) != draws(5)


def test_prompts_wait_for_the_baseline():
    state = dict(DEFAULT_STATE, recording_since=NOW)
    assert not prompts_open(state, NOW + 14 * DAY_MS - 1, 0, 0)
    assert prompts_open(state, NOW + 14 * DAY_MS, 0, 0)
    assert prompts_open(dict(state, baseline_days=0), NOW, 0, 0)


def test_prompts_respect_the_daily_cap_and_the_stop():
    state = dict(DEFAULT_STATE, recording_since=NOW - 20 * DAY_MS)
    assert prompts_open(state, NOW, 29, 1199)
    assert not prompts_open(state, NOW, 30, 0)
    assert not prompts_open(state, NOW, 0, 1200)


def test_block_gap():
    assert is_block_start(NOW, NOW + 2 * 3600000, 2)
    assert not is_block_start(NOW, NOW + 2 * 3600000 - 1, 2)
    assert is_block_start(0, NOW, 2)


def test_the_evening_is_counted_from_the_study_day_start():
    start = 1_000_000 * DAY_MS
    hour = 3_600_000
    assert evening_begins_ms(start, 4, 19) == start + 15 * hour
    assert evening_begins_ms(start, 4, 4) == start  # every hour counts
    assert evening_begins_ms(start, 4, 0) == start + 20 * hour  # midnight, not the day before
    assert evening_begins_ms(start, 20, 19) == start + 23 * hour


def prompt(kind="answer", ts=NOW, answers=None, skipped=()):
    return {"ts": ts, "kind": kind, "answers": answers or {}, "skipped": list(skipped)}


def test_cap_counts_skips_but_not_day_ratings_or_yesterday():
    events = [
        prompt(ts=NOW - 1),
        prompt(),
        prompt("sharpness", skipped=["sharpness"]),
        prompt("day"),
    ]
    assert prompts_since(events, NOW) == 2


def test_counts_add_up_per_question():
    counts = prompt_counts([
        prompt(answers={"cost": 2, "gain": 0}),
        prompt(answers={"cost": 1}, skipped=["grasp"]),
        prompt(answers={"why": 2}),
        prompt("sharpness", answers={"sharpness": 3}),
        prompt("sharpness", skipped=["sharpness"]),
        prompt("day", answers={"tiring": 3, "learned": 4}),
    ])
    assert counts == {
        "cost": 2, "gain": 1, "grasp": 0, "why": 1, "sharpness": 1, "tiring": 1,
        "prompts": 6, "skipped": 2,
    }


def test_sampling_imports_without_anki_or_qt():
    source = Path(__file__).resolve().parents[1] / "src"
    code = f"""
import importlib.util, sys, types
for name in ("aqt", "anki", "PyQt6"):
    sys.modules[name] = None
package = types.ModuleType("review_heatmap")
package.__path__ = [{str(source / "review_heatmap")!r}]
sys.modules["review_heatmap"] = package
import review_heatmap.calibration.sampling
"""
    subprocess.run([sys.executable, "-S", "-c", code], check=True)
