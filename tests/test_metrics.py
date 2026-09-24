import json
import math
from copy import deepcopy

import pytest

from review_heatmap.metrics import (
    activity_levels, activity_value, adaptive_anchor, activity_color, adaptive_color,
    COLOR_THEMES,
    automatic_reference, baseline_key,
    baseline_color, baseline_color_level, baseline_value,
    legacy_reference, metric_weights, migrate_activity_references,
    reference_from_day, saved_reference, show_reference_reminder,
)


def test_review_weighted_workload_recognizes_dense_recall():
    reading = activity_value(15, 45 * 60000, "workload")
    recall = activity_value(120, 45 * 60000, "workload")
    assert recall / reading == pytest.approx(8 ** 0.6)
    assert activity_value(30, 90 * 60000, "workload") == pytest.approx(2 * reading)
    assert activity_value(15, 90 * 60000, "workload") == pytest.approx(2 ** 0.4 * reading)
    assert activity_value(30, 45 * 60000, "workload") == pytest.approx(2 ** 0.6 * reading)


@pytest.mark.parametrize("metric", ["time", "workload", "custom"])
def test_mixed_answers_sum_without_bonus_from_combining_them(metric):
    conf = {"custom_time_weight": 0.7}
    fast = activity_value(100, 1500000, metric, [(15000, 100)], conf)
    slow = activity_value(10, 2400000, metric, [(240000, 10)], conf)
    combined = activity_value(110, 3900000, metric,
                              [(15000, 100), (240000, 10)], conf)
    assert combined == pytest.approx(fast + slow)
    if metric == "time":
        assert combined == 70  # 100 * sqrt(1/4) + 10 * sqrt(4)
    assert combined < activity_value(110, 3900000, metric, conf=conf)
    assert activity_value(220, 7800000, metric,
                          [(15000, 200), (240000, 20)], conf) == pytest.approx(2 * combined)


def test_custom_weights_match_presets_and_endpoints():
    durations = [(15000, 100), (240000, 10)]
    for weight, metric in ((0, "reviews"), (0.4, "workload"),
                           (0.5, "time"), (1, "recorded_time")):
        conf = {"custom_time_weight": weight}
        assert sum(metric_weights("custom", conf)) == 1
        assert activity_value(110, 3900000, "custom", durations, conf) == (
            activity_value(110, 3900000, metric, durations)
        )
    assert baseline_key(dict(conf, activity_metric="custom")) != baseline_key(
        dict(conf, activity_metric="custom", custom_time_weight=0.4)
    )


def test_measures_keep_counts_and_zero_time():
    assert activity_value(15, 2700000, "reviews") == 15
    assert activity_value(15, 2700000, "recorded_time") == 45
    assert activity_value(15, 2700000, "time") == pytest.approx(math.sqrt(675))
    assert activity_value(15, 0, "workload") == 0
    assert activity_value(0, 2700000, "workload") == 0


@pytest.mark.parametrize("metric", ["time", "workload"])
def test_old_references_keep_dates_and_raw_totals_across_formula_upgrade(metric):
    conf = {"activity_metric": metric, "activity_reference_date": 1}
    references = conf["activity_baselines"] = {}
    old_references = {}
    for exclusions, source in (([], "selected"), ([17], "automatic")):
        key_parts = json.loads(baseline_key(dict(conf, limdecks=exclusions)))
        key_parts[0] = 2
        key = json.dumps(key_parts, separators=(",", ":"))
        old_references[key] = {
            "day": 1, "reviews": 120, "time_ms": 2700000,
            "value": 45 if metric == "time" else math.sqrt(120 * 45), "source": source,
        }
    references.update(deepcopy(old_references))
    other_metric = "workload" if metric == "time" else "time"
    other_key = baseline_key(dict(conf, activity_metric=other_metric))
    references[other_key] = reference_from_day((2, 15, 2700000), other_metric, "selected")
    old_other = deepcopy(references[other_key])

    durations = [(15000, 100), (60000, 20)]
    read_day = lambda day: [(day, 120, 2700000, durations)]
    for exclusions in ([], [17]):
        active_conf = dict(conf, limdecks=exclusions)
        assert migrate_activity_references(active_conf, read_day)
        reference = saved_reference(active_conf)
        expected = 70 if metric == "time" else 100 * 0.25 ** 0.4 + 20
        assert reference["value"] == pytest.approx(expected)
        assert reference["day"] == 1
        assert reference["reviews"] == 120
        assert reference["time_ms"] == 2700000
        assert reference["source"] == ("selected" if not exclusions else "automatic")
    assert conf["activity_reference_date"] == 1
    assert references[other_key] == old_other
    for key, reference in old_references.items():
        assert references[key] == reference
    migrated = deepcopy(conf)
    assert not migrate_activity_references(conf, read_day)
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
    assert not migrate_activity_references(conf, lambda day: pytest.fail("newer reference wins"))
    assert conf == original
    del conf["activity_baselines"][key]
    original = deepcopy(conf)
    assert not migrate_activity_references(conf)
    assert not migrate_activity_references(conf, lambda day: [])
    # Summaries alone must never silently replace the old score with an estimate.
    assert not migrate_activity_references(conf, lambda day: [(day, 15, 2700000)])
    assert legacy_reference(conf)["day"] == 1
    assert saved_reference(conf) is None
    assert conf == original


def test_reference_is_an_actual_day_and_ignores_zero_duration():
    rows = [(day, day, day * 60000) for day in range(1, 9)] + [(9, 500, 0)]
    reference = automatic_reference(rows, "workload")
    assert reference["day"] == 8
    assert reference["reviews"] == 8
    assert reference["source"] == "automatic"
    assert reference["percentile"] == 90
    assert automatic_reference(rows[:6], "workload") is None
    assert automatic_reference([(day, day, day * 60000) for day in range(1, 11)],
                               "workload")["day"] == 9


def test_reference_and_reminder_scopes_are_independent():
    conf = {"activity_metric": "workload", "activity_scale": "baseline"}
    auto = reference_from_day((1, 20, 1200000), "workload", "automatic")
    manual = dict(auto, source="selected")
    conf["activity_baselines"] = {baseline_key(conf): manual, baseline_key(conf, 1): auto}
    assert saved_reference(conf) == manual
    assert saved_reference(conf, 1) == auto
    assert saved_reference(conf, 2) is None  # never inherit the global selection
    assert not show_reference_reminder(conf)
    assert show_reference_reminder(conf, 1)
    conf["activity_baselines"][baseline_key(conf, 2)] = auto
    conf["activity_reference_reminders_dismissed"] = {"deck:1": True}
    assert not show_reference_reminder(conf, 1)
    assert show_reference_reminder(conf, 2)
    assert not show_reference_reminder(dict(conf, activity_scale="adaptive"), 2)
    conf["activity_baselines"][baseline_key(conf, 2)] = manual
    assert not show_reference_reminder(conf, 2)


def test_reference_requires_positive_time_and_discount_never_compounds():
    assert reference_from_day((1, 100, 0), "time", "selected") is None
    reference = reference_from_day((1, 120, 2700000), "time", "selected")
    assert activity_levels() == list(range(1, 10))
    assert baseline_value(reference) == pytest.approx(0.85 * math.sqrt(5400))
    reference["value"] = 90
    assert baseline_value(reference) == pytest.approx(76.5)
    assert reference["value"] == 90  # applying the discount never compounds it


def test_adaptive_uses_median_without_discount_or_absolute_pace_anchor():
    scores = [1, 2, 3, 100]
    anchor = adaptive_anchor(scores)
    assert anchor == 2.5
    assert adaptive_anchor([]) is None
    assert adaptive_anchor([0]) is None
    assert adaptive_anchor([0.01]) == 0.01  # no absolute floor for light users
    for theme, colors in COLOR_THEMES.items():
        for night in (False, True):
            palette = colors[::-1] if night else colors
            assert adaptive_color(anchor, anchor, theme, night) == palette[5]
            # The median is an ordinary shade, not a goal-achievement jump.
            assert adaptive_color(anchor - 1e-8, anchor, theme, night) == palette[5]
            assert adaptive_color(anchor + 1e-8, anchor, theme, night) == palette[5]
            assert adaptive_color(4 * anchor, anchor, theme, night) == palette[-1]
            assert adaptive_color(0, anchor, theme, night) == palette[0]
    for factor in (0.01, 60):
        scaled_anchor = adaptive_anchor([score * factor for score in scores])
        assert adaptive_color(2 * factor, scaled_anchor) == adaptive_color(2, anchor)


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
