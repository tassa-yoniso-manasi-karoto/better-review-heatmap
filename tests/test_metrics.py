import json
import math
from copy import deepcopy

import pytest

from review_heatmap.metrics import (
    activity_levels, activity_value, adaptive_anchor, activity_color, adaptive_color,
    answer_effort, fsrs_calibration, fsrs_difficulty, FSRS_CALIBRATION_DEFAULTS,
    CONCENTRATION_DEFAULTS, EXPERIMENTAL_METRICS, METRICS, calibration_overrides,
    concentration_calibration, concentration_coefficients,
    COLOR_THEMES,
    automatic_reference, baseline_key,
    baseline_color, baseline_color_level, baseline_value,
    legacy_reference, metric_weights, migrate_activity_references,
    reference_from_day, saved_reference, set_reference, show_reference_reminder,
    template_norms, template_profiles, time_weighted_median,
    MODEL_INPUTS, MODEL_TEMPLATE_DAYS, CardHistory, active_effort_model, answer_inputs,
    effort_model_available, effort_model_credit, load_effort_model, metric_name,
    model_inputs, use_effort_model, workload_reference_day,
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


@pytest.mark.parametrize("source_metric", ["time", "workload", "custom"])
def test_existing_manual_day_is_recovered_for_other_workload_formulas(source_metric):
    row = (1, 3, 270000, [(15000, 2), (240000, 1)])
    conf = {"activity_metric": source_metric, "custom_time_weight": 0.7}
    original = reference_from_day(row, source_metric, "selected", conf)
    conf["activity_baselines"] = {baseline_key(conf): original}
    for metric in ("time", "workload", "custom"):
        target = dict(conf, activity_metric=metric, custom_time_weight=0.3)
        if baseline_key(target) != baseline_key(conf):
            # A previously automatic target must not hide the manual selection.
            target["activity_baselines"][baseline_key(target)] = dict(
                original, source="automatic", day=2,
            )
            assert migrate_activity_references(target, lambda day: [row])
        reference = saved_reference(target)
        assert reference["day"] == 1 and reference["source"] == "selected"
        assert reference["value"] == pytest.approx(
            activity_value(3, 270000, metric, row[3], target)
        )
    assert conf["activity_baselines"][baseline_key(conf)] == original


def test_shared_manual_day_respects_scope_filters_and_explicit_automatic_choice():
    conf = {"activity_metric": "workload"}
    row = (1, 3, 270000, [(15000, 2), (240000, 1)])
    set_reference(conf, reference_from_day(row, "workload", "selected"), 1)
    custom = dict(conf, activity_metric="custom", custom_time_weight=0.8)
    assert not migrate_activity_references(custom, lambda day: [row])
    assert not migrate_activity_references(custom, lambda day: [row], 2)
    assert not migrate_activity_references(dict(custom, limhist=30), lambda day: [row], 1)
    assert not migrate_activity_references(custom, lambda day: [], 1)
    assert saved_reference(custom, 1) is None
    assert legacy_reference(custom, 1)["day"] == 1  # do not silently pick P90
    assert migrate_activity_references(custom, lambda day: [row], 1)
    automatic = reference_from_day((2, *row[1:]), "custom", "automatic", custom)
    set_reference(custom, automatic, 1)
    assert not migrate_activity_references(custom, lambda day: pytest.fail("keep auto"), 1)
    assert saved_reference(custom, 1) == automatic
    manual = reference_from_day((3, *row[1:]), "custom", "selected", custom)
    set_reference(custom, manual, 1)
    assert migrate_activity_references(conf, lambda day: [(day, *row[1:])], 1)
    assert saved_reference(conf, 1)["day"] == 3


@pytest.mark.parametrize("metric", ["time", "workload", "custom", "recorded_time"])
def test_old_day_grouping_recalculates_saved_day_once_without_losing_selection(metric):
    row = (1, 3, 270000, [(15000, 2), (240000, 1)])
    for source in ("selected", "automatic"):
        conf = {"activity_metric": metric}
        old = {"day": 1, "reviews": 1, "time_ms": 240000, "value": 4,
               "source": source, "selected_on": 10, "percentile": 90}
        conf["activity_baselines"] = {baseline_key(conf, 7): old.copy()}
        assert saved_reference(conf, 7) is None
        assert not migrate_activity_references(conf, lambda day: [], 7)
        assert conf["activity_baselines"][baseline_key(conf, 7)] == old
        assert legacy_reference(conf, 7)["day"] == 1
        assert migrate_activity_references(conf, lambda day: [row], 7)
        current = saved_reference(conf, 7)
        assert current["value"] == pytest.approx(activity_value(3, 270000, metric, row[3]))
        assert current["day"] == 1 and current["source"] == source
        assert current["selected_on"] == 10 and current["percentile"] == 90
        assert not migrate_activity_references(conf, lambda day: pytest.fail("already updated"), 7)


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


def test_fsrs_effort_weights_new_young_and_difficult_answers_more():
    calibration = fsrs_calibration({})
    assert calibration == FSRS_CALIBRATION_DEFAULTS
    assert fsrs_difficulty(550) == pytest.approx(5.05)
    assert (fsrs_difficulty(100), fsrs_difficulty(1100)) == (1, 10)
    assert fsrs_difficulty(2500) is None and fsrs_difficulty(0) is None
    assert answer_effort(False, 21, 550, calibration) == pytest.approx((5.05 / 5.5) ** 0.25)
    assert answer_effort(False, 21, 0) == 1  # SM-2 learning and cramming carry no difficulty
    assert answer_effort(False, 21, 2500) == 1  # SM-2 ease is not FSRS difficulty
    assert answer_effort(True, 0, 0) == 1.5
    assert answer_effort(False, -600, 0) == 1
    assert answer_effort(False, 3, 0) == pytest.approx(7 ** 0.25)
    assert answer_effort(False, 1, 0) == 2  # the weight limit caps both directions
    assert answer_effort(False, 3650, 0) == 0.5
    assert answer_effort(True, 0, 1100) == pytest.approx(min(2, 1.5 * (10 / 5.5) ** 0.25))
    custom = fsrs_calibration({"fsrs_calibration": {
        "maturity_exponent": 0, "difficulty_exponent": 0, "new_card_weight": 3,
        "weight_limit": 2.5,
    }})
    assert answer_effort(True, 0, 1100, custom) == 2.5
    assert answer_effort(False, 1, 100, custom) == 1
    invalid = {"fsrs_calibration": {"step_weight": 20, "weight_limit": "x"}}
    assert fsrs_calibration(invalid) == calibration
    assert fsrs_calibration({"fsrs_calibration": [1]}) == calibration


def test_fsrs_scores_read_effort_only_in_their_own_mode():
    conf = {"activity_metric": "fsrs", "fsrs_calibration": {"time_weight": 0}}
    buckets = [(15000, 2, 1.5), (240000, 1, 0.5)]
    plain = [(15000, 2), (240000, 1)]
    # The exponents are review-weighted's; a stale stored exponent is ignored.
    assert metric_weights("fsrs", conf) == metric_weights("workload")
    assert "time_weight" not in fsrs_calibration(conf)
    assert activity_value(3, 270000, "fsrs", buckets, conf) == pytest.approx(
        2 * 1.5 ** 0.6 * 0.25 ** 0.4 + 0.5 ** 0.6 * 4 ** 0.4
    )
    workload_score = activity_value(3, 270000, "workload", plain)
    assert activity_value(3, 270000, "fsrs", plain, conf) == workload_score
    assert activity_value(3, 270000, "workload", buckets) == workload_score
    assert activity_value(3, 270000, "fsrs", None, conf) == activity_value(3, 270000, "workload")
    # Calibration changes re-key references; the manual day is shared with workload modes.
    assert baseline_key(conf) != baseline_key(dict(conf, fsrs_calibration={"weight_limit": 3}))
    assert baseline_key(conf, 5) != baseline_key(conf)
    workload = {"activity_metric": "workload", "activity_baselines": {}}
    set_reference(workload, reference_from_day((1, 3, 270000, plain), "workload", "selected"))
    fsrs = dict(workload, activity_metric="fsrs")
    assert saved_reference(fsrs) is None
    assert legacy_reference(fsrs)["day"] == 1
    assert migrate_activity_references(fsrs, lambda day: [(day, 3, 270000, buckets)])
    assert saved_reference(fsrs)["value"] == pytest.approx(
        activity_value(3, 270000, "fsrs", buckets, fsrs)
    )
    assert saved_reference(fsrs, 5) is None


def metrics_module_default():
    import review_heatmap.metrics as metrics
    return metrics.metric_name({})


def test_concentration_rises_with_heavy_uninterrupted_study_and_fades():
    minute = 60000
    assert concentration_calibration({}) == CONCENTRATION_DEFAULTS
    heavy = [((i + 1) * 2 * minute, 2 * minute, 2 * minute) for i in range(40)]
    c = concentration_coefficients(heavy)
    assert c[:5] == [1] * 5  # warm-up: the first 10 heavy minutes earn nothing
    assert 1 < c[5] < c[10] < 2
    assert c[14] == pytest.approx(2) and c[36] == pytest.approx(2)  # full at 30, cap at 75
    assert c[37] == pytest.approx(1 + math.exp(-1 / 30))
    assert c[39] == pytest.approx(1 + math.exp(-5 / 30))
    light = [((i + 1) * 20000, 20000, 20000) for i in range(240)]
    assert concentration_coefficients(light) == [1] * 240
    slow = [((i + 1) * 2 * minute, 2 * minute, 20000) for i in range(40)]  # slow on quick cards
    assert concentration_coefficients(slow) == [1] * 40
    unknown = [((i + 1) * 2 * minute, 2 * minute, None) for i in range(20)]
    assert concentration_coefficients(unknown) == c[:20]
    paused = heavy[:15] + [(38 * minute, 2 * minute, 2 * minute)]
    assert concentration_coefficients(paused)[-1] == 1
    step = concentration_calibration({"concentration_calibration": {
        "max_bonus": 0.5, "warm_up_minutes": 10, "full_minutes": 10,
    }})
    assert concentration_coefficients(heavy[:5], step)[3:] == [1, 1.5]


def test_template_norm_follows_recent_recorded_time():
    # Long answers hold most of the time even as a minority; a median says 10 s.
    assert time_weighted_median({10: 30, 200: 10}) == 200
    day = 86400
    rows = [(0, 8, 1000), (100, 10, 30), (100, 200, 10), (101, 200, 1), (160, 8, 40), (161, 8, 1)]
    norms = template_norms([(7, 0, date * day, seconds, count)
                            for date, seconds, count in rows], 60)
    # Day 100 reads nothing recent and never itself, so it keeps earlier answers.
    assert norms[(7, 0, 100 * day)] == 8000
    # By day 101 the old clicks are outside the window.
    assert norms[(7, 0, 101 * day)] == 200000
    # Exactly 60 days back still counts; a day later it has left.
    assert norms[(7, 0, 160 * day)] == 200000
    assert norms[(7, 0, 161 * day)] == 8000
    assert (7, 0, 0) not in norms  # a first day has nothing to read
    assert template_norms([(7, 0, 0, 8, 1), (7, 0, day, 8, 1),
                           (8, 1, 0, 90, 1), (8, 1, day, 90, 1)], 60) == {
        (7, 0, day): 8000, (8, 1, day): 90000,
    }


def test_calibration_stores_only_manual_changes_and_keys_references():
    conf = {"fsrs_calibration": {"new_card_weight": 3, "bogus": 1, "weight_limit": "x"},
            "concentration_calibration": {"max_bonus": 0.5, "fade_minutes": 9999}}
    assert calibration_overrides(conf, "fsrs_calibration") == {"new_card_weight": 3}
    assert calibration_overrides(conf, "concentration_calibration") == {"max_bonus": 0.5}
    assert fsrs_calibration(conf) == dict(FSRS_CALIBRATION_DEFAULTS, new_card_weight=3)
    assert concentration_calibration(conf) == dict(CONCENTRATION_DEFAULTS, max_bonus=0.5)
    assert calibration_overrides({"fsrs_calibration": [1]}, "fsrs_calibration") == {}
    plain = {"activity_metric": "concentration"}
    assert baseline_key(plain) != baseline_key(dict(conf, activity_metric="concentration"))
    assert baseline_key(plain) != baseline_key({"activity_metric": "fsrs"})
    assert json.loads(baseline_key(plain))[7] == {
        "fsrs_calibration": FSRS_CALIBRATION_DEFAULTS,
        "concentration_calibration": CONCENTRATION_DEFAULTS,
    }
    buckets = [(60000, 2, 1.5, 2.0), (120000, 1, 1.0, 1.0)]
    assert activity_value(3, 240000, "concentration", buckets) == pytest.approx(
        2 * 2.0 * 1.5 ** 0.6 + 2 ** 0.4
    )
    assert activity_value(3, 240000, "fsrs", buckets) == pytest.approx(2 * 1.5 ** 0.6 + 2 ** 0.4)
    assert activity_value(3, 240000, "workload", buckets) == pytest.approx(2 + 2 ** 0.4)
    assert METRICS["fsrs"]["label"].startswith("⭐ ")
    assert METRICS["concentration"]["label"].startswith("🧪 ")
    assert all(METRICS[key]["label"].endswith("(exp.)") for key in EXPERIMENTAL_METRICS)
    assert metrics_module_default() == "fsrs"


@pytest.fixture
def fitted_model():
    model = {"status": "local", "version": 3, "intercept": 0.25,
             "weights": {"log_d": 0.5, "again": 0.3, "reps·rich": -0.1}}
    use_effort_model(model)
    yield model
    use_effort_model()


def test_template_profiles_share_the_norms_window():
    rows = [(1, 0, 0, 2, 40), (1, 0, 0, 100, 4), (1, 0, 86400, 3, 1), (1, 0, 200 * 86400, 7, 1),
            (2, 1, 0, 9, 1)]
    profiles = template_profiles(rows, 60)
    norms = template_norms(rows, 60)
    assert set(profiles) == set(norms) == {(1, 0, 86400), (1, 0, 200 * 86400)}
    # Past 60 days only one answer is recent, so all earlier ones are read.
    assert profiles[(1, 0, 86400)] == (2000, 100000)
    assert profiles[(1, 0, 200 * 86400)] == (2000, 100000)
    assert all(profiles[key][1] == norms[key] for key in norms)
    enough = [(1, 0, 0, 100, 40), (1, 0, 150 * 86400, 2, 30), (1, 0, 200 * 86400, 7, 1)]
    assert template_profiles(enough, MODEL_TEMPLATE_DAYS)[(1, 0, 200 * 86400)] == (2000, 2000)


def test_card_history_reads_only_what_came_before():
    history = CardHistory()
    day = 86400000
    assert history.advance(1, day, 0) == (True, None, 0)
    assert history.advance(2, day, 0) == (True, None, 0)
    assert history.advance(1, day + 600000, -600) == (False, 600000 / day, 0)
    assert history.advance(1, 3 * day, 2) == (False, pytest.approx(2 - 600000 / day), 0)
    assert history.advance(1, 9 * day, 6) == (False, 6, 1)
    assert history.advance(1, 10 * day, 0) == (False, 1, 2)


def test_model_inputs_match_a_hand_calculation():
    inputs = model_inputs(40000, 1, False, 10, 15.0, 3, (20000, 80000))
    assert tuple(inputs) == MODEL_INPUTS and len(MODEL_INPUTS) == 12
    rich, log_d, reps = math.log(4), math.log(4), math.log(4)
    assert inputs == pytest.approx({
        "log_d": log_d, "rich": rich, "t_med": math.log(2), "log_d·rich": log_d * rich,
        "new": 0, "step": 0, "again": 1, "hard": 0, "easy": 0,
        "overdue": math.log(1.5), "reps": reps, "reps·rich": reps * rich,
    })
    new = model_inputs(0, 3, True, 0, None, 0, None)
    assert new["log_d"] == pytest.approx(math.log(0.03))  # no logarithm of 0 ms
    assert (new["rich"], new["t_med"], new["new"], new["step"]) == (0, 0, 1, 0)
    step = model_inputs(5000, 4, False, -600, 0.01, 2, (0, 0))
    assert (step["step"], step["easy"], step["overdue"], step["rich"]) == (1, 1, 0, 0)
    assert step["t_med"] == pytest.approx(math.log(0.03))
    assert model_inputs(5000, 2, False, 10, 1000.0, 2, None)["overdue"] == pytest.approx(math.log(50))
    assert model_inputs(5000, 2, False, 10, 0.0, 2, None)["overdue"] == pytest.approx(math.log(0.2))
    assert model_inputs(5000, 2, False, 10, 0.0, 2, None)["hard"] == 1
    assert model_inputs(5000, 3, False, 10, 10.0, 0, (1000, 500))["rich"] == 0
    assert model_inputs(5000, 3, False, 10, 10.0, 0, (1000, 10 ** 8))["rich"] == 3


def test_answer_inputs_stream_each_cards_history_through_the_shared_function():
    profiles = {(7, 1, 864000): (20000, 80000)}
    history = CardHistory()
    day = 86400000
    rows = [(1, day, 3, 10000, 0, 7, 1, 86400), (1, 11 * day, 3, 40000, 10, 7, 1, 864000)]
    assert answer_inputs(rows[0], history, profiles) == model_inputs(
        10000, 3, True, 0, None, 0, None,
    )
    assert answer_inputs(rows[1], history, profiles) == model_inputs(
        40000, 3, False, 10, 10.0, 0, (20000, 80000),
    )


def test_effort_model_credit_is_exp_eta_and_clamped():
    inputs = model_inputs(40000, 1, False, 10, 15.0, 3, (20000, 80000))
    model = {"intercept": 0.25, "weights": {"log_d": 0.5, "again": 0.3, "unknown": 9}}
    assert effort_model_credit(inputs, model) == pytest.approx(
        math.exp(0.25 + 0.5 * math.log(4) + 0.3)
    )
    assert effort_model_credit(inputs, {"status": "none", "version": 0}) == 1
    assert effort_model_credit(inputs, {"intercept": 50}) == pytest.approx(32)
    assert effort_model_credit(inputs, {"intercept": -50}) == pytest.approx(1 / 16)


def test_only_a_profile_model_reads_the_template():
    inputs = model_inputs(10000, 3, False, 0, None, 0, None)
    model = {"intercept": 0.25, "offsets": {"7:1": 0.5}}
    assert effort_model_credit(inputs, model, (7, 1)) == pytest.approx(math.exp(0.75))
    assert effort_model_credit(inputs, model, [7, 1]) == pytest.approx(math.exp(0.75))
    assert effort_model_credit(inputs, model, (7, 2)) == pytest.approx(math.exp(0.25))
    assert effort_model_credit(inputs, model, (None, None)) == pytest.approx(math.exp(0.25))
    assert effort_model_credit(inputs, model) == pytest.approx(math.exp(0.25))
    assert effort_model_credit(inputs, {"intercept": 0.25}, (7, 1)) == pytest.approx(math.exp(0.25))


def test_fitted_measure_is_hidden_until_a_model_is_validated_or_local(fitted_model):
    use_effort_model()
    assert load_effort_model() == {"status": "none", "version": 0}
    assert not effort_model_available()
    assert "effort_model" in METRICS and "effort_model" in EXPERIMENTAL_METRICS
    conf = {"activity_metric": "effort_model"}
    assert metric_name(conf) == "fsrs"
    assert baseline_key(conf) == baseline_key({"activity_metric": "fsrs"})
    use_effort_model({"status": "validated", "version": 1})
    assert effort_model_available() and metric_name(conf) == "effort_model"
    use_effort_model(fitted_model)
    assert effort_model_available() and active_effort_model() is fitted_model
    assert metric_name(conf) == "effort_model"
    use_effort_model()
    assert metric_name(conf) == "fsrs" and metric_name({}) == "fsrs"


def test_fitted_score_sums_credits_and_references_follow_the_model(fitted_model):
    assert metric_weights("effort_model") == (1, 0)
    buckets = [(60000, 2, 1.5), (0, 1, 0.5)]
    assert activity_value(3, 120000, "effort_model", buckets) == pytest.approx(3.5)
    assert activity_value(3, 120000, "workload", [(60000, 2), (0, 1)]) == pytest.approx(2)
    conf = {"activity_metric": "effort_model"}
    first_key = baseline_key(conf)
    assert json.loads(first_key)[7] == 3
    day = 1776000000
    set_reference(conf, {"day": day, "value": 50, "source": "selected",
                         "day_grouping_version": 1})
    assert saved_reference(conf)["value"] == 50
    use_effort_model(dict(fitted_model, version=4))
    assert baseline_key(conf) != first_key
    assert saved_reference(conf) is None
    # The chosen day survives new weights, and is shared with other workloads.
    assert workload_reference_day(conf)["day"] == day
    assert workload_reference_day({"activity_metric": "workload", **conf,
                                   "activity_metric": "fsrs"})["day"] == day
    deck_key = json.loads(baseline_key(conf, 5))
    assert deck_key[7] == 4 and deck_key[8] == ["deck", 5]
