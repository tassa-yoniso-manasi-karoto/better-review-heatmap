import math

import pytest

from review_heatmap.metrics import (
    activity_levels, activity_value, automatic_reference, baseline_key,
    reference_from_day, saved_reference,
)


def test_balanced_workload_recognizes_dense_recall():
    reading = activity_value(15, 45 * 60000, "workload")
    recall = activity_value(120, 45 * 60000, "workload")
    assert recall / reading == pytest.approx(math.sqrt(8))
    assert activity_value(30, 90 * 60000, "workload") == pytest.approx(2 * reading)
    assert activity_value(15, 90 * 60000, "workload") == pytest.approx(math.sqrt(2) * reading)


def test_measures_keep_recorded_units_and_zero_time():
    assert activity_value(15, 2700000, "reviews") == 15
    assert activity_value(15, 2700000, "time") == 45
    assert activity_value(15, 0, "workload") == 0


def test_reference_is_an_actual_day_and_ignores_zero_duration():
    rows = [(day, day, day * 60000) for day in range(1, 9)] + [(9, 500, 0)]
    reference = automatic_reference(rows, "workload")
    assert reference["day"] == 6
    assert reference["reviews"] == 6
    assert reference["source"] == "automatic"
    assert automatic_reference(rows[:6], "workload") is None


def test_reference_requires_positive_time_and_applies_only_when_supplied():
    assert reference_from_day((1, 100, 0), "time", "selected") is None
    reference = reference_from_day((1, 120, 2700000), "time", "selected")
    assert activity_levels("time")[5] == 45
    assert activity_levels("time", reference)[5] == 45
    reference["value"] = 90
    assert activity_levels("time", reference)[5] == 90
    assert activity_levels("time")[5] == 45


def test_references_are_isolated_by_measure_and_history_filters():
    conf = {"activity_metric": "workload", "limdecks": [2, 1]}
    reference = reference_from_day((1, 120, 2700000), "workload", "selected")
    conf["activity_baselines"] = {baseline_key(conf): reference}
    assert saved_reference(conf) == reference
    assert saved_reference(dict(conf, limdecks=[1, 2])) == reference
    for change in ({"activity_metric": "time"}, {"limcdel": True}, {"limhist": 30}):
        assert saved_reference(dict(conf, **change)) is None


@pytest.mark.parametrize("value", [0, -1, "bad", float("inf"), float("nan"), None])
def test_invalid_saved_reference_is_ignored(value):
    conf = {"activity_metric": "time"}
    conf["activity_baselines"] = {baseline_key(conf): {"day": 1, "value": value}}
    assert saved_reference(conf) is None
