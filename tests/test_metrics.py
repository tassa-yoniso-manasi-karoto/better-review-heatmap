import json
import math
from copy import deepcopy

import pytest

from review_heatmap.metrics import (
    activity_levels, activity_value, automatic_reference, baseline_key,
    baseline_color, baseline_color_level, baseline_value,
    migrate_workload_references, reference_from_day, saved_reference,
)


def test_review_weighted_workload_recognizes_dense_recall():
    reading = activity_value(15, 45 * 60000, "workload")
    recall = activity_value(120, 45 * 60000, "workload")
    assert recall / reading == pytest.approx(4)
    assert activity_value(30, 90 * 60000, "workload") == pytest.approx(2 * reading)
    assert activity_value(15, 90 * 60000, "workload") == pytest.approx(2 ** (1 / 3) * reading)
    assert activity_value(30, 45 * 60000, "workload") == pytest.approx(2 ** (2 / 3) * reading)


def test_measures_keep_recorded_units_and_zero_time():
    assert activity_value(15, 2700000, "reviews") == 15
    assert activity_value(15, 2700000, "time") == 45
    assert activity_value(15, 0, "workload") == 0
    assert activity_value(0, 2700000, "workload") == 0


def test_old_references_keep_dates_and_raw_totals_across_formula_upgrade():
    conf = {"activity_metric": "workload", "activity_reference_date": 1}
    references = conf["activity_baselines"] = {}
    old_references = {}
    for exclusions, source in (([], "selected"), ([17], "automatic")):
        key_parts = json.loads(baseline_key(dict(conf, limdecks=exclusions)))
        key_parts[0] = 1
        key = json.dumps(key_parts, separators=(",", ":"))
        old_references[key] = {
            "day": 1, "reviews": 120, "time_ms": 2700000,
            "value": math.sqrt(120 * 45), "source": source,
        }
    references.update(deepcopy(old_references))
    time_key = baseline_key(dict(conf, activity_metric="time"))
    references[time_key] = reference_from_day((2, 15, 2700000), "time", "selected")
    old_time = deepcopy(references[time_key])

    assert migrate_workload_references(conf)
    for exclusions in ([], [17]):
        reference = saved_reference(dict(conf, limdecks=exclusions))
        assert reference["value"] == pytest.approx(120 * 0.375 ** (1 / 3))
        assert reference["day"] == 1
        assert reference["reviews"] == 120
        assert reference["time_ms"] == 2700000
        assert reference["source"] == ("selected" if not exclusions else "automatic")
    assert conf["activity_reference_date"] == 1
    assert references[time_key] == old_time
    for key, reference in old_references.items():
        assert references[key] == reference
    migrated = deepcopy(conf)
    assert not migrate_workload_references(conf)
    assert conf == migrated


def test_reference_upgrade_preserves_newer_selections_and_invalid_old_entries():
    conf = {"activity_metric": "workload"}
    key = baseline_key(conf)
    old_parts = json.loads(key)
    old_parts[0] = 1
    old_key = json.dumps(old_parts, separators=(",", ":"))
    reference = reference_from_day((2, 40, 2700000), "workload", "selected")
    conf["activity_baselines"] = {
        key: reference,
        old_key: {"day": 1, "reviews": 15, "time_ms": 2700000, "value": 25},
        "invalid": {"value": 1},
    }
    original = deepcopy(conf)
    assert not migrate_workload_references(conf)
    assert conf == original
    del conf["activity_baselines"][key]
    del conf["activity_baselines"][old_key]["time_ms"]
    original = deepcopy(conf)
    assert not migrate_workload_references(conf)
    assert conf == original


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
    assert activity_levels("time", reference) == list(range(1, 10))
    assert baseline_value(reference) == pytest.approx(38.25)
    reference["value"] = 90
    assert baseline_value(reference) == pytest.approx(76.5)
    assert reference["value"] == 90  # applying the discount never compounds it
    assert activity_levels("time")[5] == 45


def test_baseline_palette_has_a_white_reference_and_gentler_threshold():
    reference = reference_from_day((1, 100, 100 * 60000), "workload", "selected")
    assert baseline_value(reference) == 85
    assert baseline_color_level(100, reference, reference_day=True) == 5
    assert baseline_color_level(85, reference) == 6
    assert baseline_color_level(85 - 1e-8, reference) == 4
    assert baseline_color_level(85 + 1e-8, reference) == 6
    assert baseline_color_level(100, reference) == 6  # same effort on another day is green
    assert [baseline_color_level(value, reference) for value in (0, 25, 50, 84)] == [1, 2, 3, 4]
    assert [baseline_color_level(value, reference) for value in (86, 110, 150, 200, 300)] == [6, 7, 8, 9, 10]


def test_baseline_gradient_interpolates_without_white_at_the_target():
    reference = {"value": 100}
    assert baseline_color(0, reference) == "rgba(251, 140, 0, 0.3000)"
    assert baseline_color(85 / 3, reference) == "rgba(255, 241, 118, 0.6500)"
    assert baseline_color(170 / 3, reference) == "#1e6823"
    assert baseline_color(85 - 1e-8, reference) == "#378f36"
    assert baseline_color(85, reference) == "#74ba58"
    assert baseline_color(170, reference) == "#d6e685"
    assert baseline_color(255, reference) == "#81d4fa"
    assert baseline_color(500, reference) == "#81d4fa"
    assert baseline_color(100, reference, reference_day=True) == "#ffffff"
    # Nearby workloads within the former buckets now produce different colors.
    assert baseline_color(10, reference) != baseline_color(15, reference)
    assert baseline_color(90, reference) != baseline_color(100, reference)


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
