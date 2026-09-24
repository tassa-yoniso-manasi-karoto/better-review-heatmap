"""Integration tests use only a synthetic, in-memory SQL fixture."""

import copy
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


def day(year, month, date):
    return int(datetime(year, month, date, tzinfo=timezone.utc).timestamp())


TODAY = day(2026, 3, 10)


class MemoryDB:
    def __init__(self):
        # Never open a real Anki collection in these tests.
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(
            "CREATE TABLE revlog (id INTEGER, cid INTEGER, ease INTEGER, time INTEGER);"
            "CREATE TABLE cards (id INTEGER, did INTEGER, due INTEGER, queue INTEGER);"
        )

    def all(self, sql, *args):
        return self.connection.execute(sql, args).fetchall()

    def scalar(self, sql, *args):
        return self.all(sql, *args)[0][0]


class Config(dict):
    def __init__(self, defaults):
        defaults = copy.deepcopy(defaults)
        if "local" in defaults:
            palette_defaults = json.loads(
                (Path(__file__).resolve().parents[1] / "src/review_heatmap/config.json").read_text()
            )
            defaults["local"] = dict(palette_defaults, **defaults["local"])
        super().__init__(defaults)
        self.saves = []
        self.defaults = copy.deepcopy(defaults)

    @property
    def all(self):
        return self

    def load(self):
        pass

    def save(self, storage=None, **kwargs):
        self.saves.append((storage, kwargs))


@pytest.fixture
def setup(addon_modules):
    db = MemoryDB()
    conf = Config(addon_modules.config.config_defaults)
    decks = SimpleNamespace(
        all=lambda: [{"id": 1}, {"id": 2}],
        children=lambda did: [],
        current=lambda: {"id": 1},
        get_current_id=lambda: 1,
        deck_and_child_ids=lambda did: [did],
    )
    col = SimpleNamespace(
        db=db, decks=decks, crt=TODAY - 100 * 86400, conf={"rollover": 4},
        mod=1, v3_scheduler=lambda: True, sched=SimpleNamespace(today=100),
    )
    class Reporter(addon_modules.activity.ActivityReporter):
        @property
        def _today(self):
            return TODAY

    reporter = Reporter(col, conf)
    yield SimpleNamespace(db=db, conf=conf, col=col, reporter=reporter, modules=addon_modules)
    db.connection.close()


def add_review(setup, date, cid=1, ease=3, milliseconds=30000, sequence=0):
    setup.db.connection.execute(
        "INSERT INTO revlog VALUES (?, ?, ?, ?)",
        ((date + 16 * 3600) * 1000 + sequence, cid, ease, milliseconds),
    )


def test_count_and_time_share_filters_including_deleted_cards(setup):
    setup.db.connection.executemany("INSERT INTO cards VALUES (?, ?, 0, 2)", [(1, 1), (2, 2)])
    yesterday = TODAY - 86400
    add_review(setup, yesterday, 1, milliseconds=30000)
    add_review(setup, yesterday, 2, milliseconds=180000, sequence=1)
    add_review(setup, yesterday, 99, milliseconds=90000, sequence=2)
    add_review(setup, yesterday, 1, ease=0, milliseconds=999999, sequence=3)
    assert setup.reporter._cards_done() == [(yesterday, 3, 300000)]
    assert setup.reporter._cards_done(with_durations=True) == [
        (yesterday, 3, 300000, [(30000, 1), (90000, 1), (180000, 1)]),
    ]
    assert setup.reporter._cards_done(current_deck_only=True) == [(yesterday, 1, 30000)]
    setup.conf["synced"]["limcdel"] = True
    assert setup.reporter._cards_done() == [(yesterday, 2, 210000)]
    setup.conf["synced"]["limdecks"] = [2]
    assert setup.reporter._cards_done() == [(yesterday, 1, 30000)]
    assert setup.reporter.reference_history(yesterday, with_durations=True) == [
        (yesterday, 1, 30000, [(30000, 1)]),
    ]
    setup.conf["synced"]["limresched"] = False
    assert setup.reporter._cards_done() == [(yesterday, 2, 1029999)]


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="requires timezone switching")
def test_dst_grouping_and_streaks_retain_original_calendar_days(setup, monkeypatch):
    original_tz = __import__("os").environ.get("TZ")
    try:
        monkeypatch.setenv("TZ", "America/New_York")
        time.tzset()
        for date in (7, 8, 9):
            add_review(setup, day(2026, 3, date))
        rows = setup.reporter._cards_done()
        assert [row[0] for row in rows] == [day(2026, 3, date) for date in (7, 8, 9)]
        report = setup.reporter.get_report(limfcst=0)
        assert report.stats.streak_cur.value == 3
        assert report.stats.streak_max.value == 3
        assert report.review_time == {row[0]: 30000 for row in rows}
        assert report.stop == TODAY * 1000  # usable even without future cards
    finally:
        if original_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original_tz)
        time.tzset()


def test_reference_history_excludes_today_and_obeys_history_limits(setup):
    for age in (0, 1, 2, 59, 60, 61):
        add_review(setup, TODAY - age * 86400)
    assert len(setup.reporter.reference_history()) == 4
    assert setup.reporter.reference_history(TODAY - 86400) == [(TODAY - 86400, 1, 30000)]
    setup.conf["synced"]["limhist"] = 1
    assert setup.reporter.reference_history() == [(TODAY - 86400, 1, 30000)]


def make_renderer(setup):
    return setup.modules.renderer.HeatmapRenderer(
        SimpleNamespace(col=setup.col), setup.reporter, setup.conf,
    )


def test_render_preserves_raw_totals_streaks_and_forecast_scale(setup):
    reporter = setup.reporter
    report = reporter._get_activity(
        [(TODAY - 86400, 15), (TODAY, 120)], [(TODAY + 86400, -200)],
        {TODAY - 86400: 2700000, TODAY: 2700000},
    )
    reporter.get_report = lambda **kwargs: report
    renderer = make_renderer(setup)
    view = setup.modules.renderer.HeatmapView.deckbrowser
    snapshots = {}
    for metric in ("reviews", "time", "workload"):
        setup.conf["synced"]["activity_metric"] = metric
        html = renderer.render(view)
        options = json.loads(re.search(r"new ReviewHeatmap\((.+)\);", html)[1])
        values = json.loads(re.search(r"reviewHeatmap.create\((.+)\);", html)[1])
        snapshots[metric] = options
        assert options["history"][str(TODAY)] == [120, 2700000]
        assert values[str(TODAY + 86400)] == -200
        assert "68 cards" in html  # original count-based active-day average
        if metric == "time":
            assert renderer._activity_scores(report)[TODAY] == pytest.approx(5400 ** 0.5)
            assert options["dayColors"][str(TODAY)] != "#ffffff"
        elif metric == "workload":
            assert values[str(TODAY)] > values[str(TODAY - 86400)]
    assert snapshots["reviews"]["legend"][:10] == snapshots["workload"]["legend"][:10]
    assert snapshots["reviews"]["legend"][-9:] != snapshots["workload"]["legend"][-9:]


def test_zero_time_days_remain_visible_and_clickable(setup):
    report = setup.reporter._get_activity([(TODAY, 5)], [], {TODAY: 0})
    renderer = make_renderer(setup)
    setup.conf["synced"]["activity_metric"] = "workload"
    html = renderer._generate_heatmap_elm(report, [], False)
    values = json.loads(re.search(r"reviewHeatmap.create\((.+)\);", html)[1])
    assert values[str(TODAY)] == 1
    assert report.stats.streak_cur.value == 1


def test_baseline_stays_stable_between_refreshes_and_adaptive_never_uses_it(setup):
    for age in range(1, 9):
        add_review(setup, TODAY - age * 86400, milliseconds=age * 60000)
    renderer = make_renderer(setup)
    setup.conf["synced"].update(activity_metric="time", activity_scale="baseline")
    levels = renderer._activity_legend([])
    assert len(setup.conf.saves) == 1
    add_review(setup, TODAY - 86400, milliseconds=99999999, sequence=1)
    assert renderer._activity_legend([]) == levels
    assert len(setup.conf.saves) == 1
    setup.conf["synced"]["activity_scale"] = "adaptive"
    assert renderer._baseline_reference() is None
    assert renderer._activity_legend([]) == list(range(1, 10))
    assert len(setup.conf.saves) == 1


def test_automatic_reference_refreshes_after_30_days_without_using_today(setup, monkeypatch):
    from review_heatmap.metrics import baseline_key, saved_reference

    now = [TODAY]
    monkeypatch.setattr(type(setup.reporter), "_today", property(lambda self: now[0]))
    conf = setup.conf["synced"]
    conf.update(activity_metric="recorded_time", activity_scale="baseline")
    for age in range(1, 11):
        add_review(setup, TODAY - age * 86400, milliseconds=60000)
    renderer = make_renderer(setup)
    renderer._activity_legend([])
    original = copy.deepcopy(saved_reference(conf))
    assert original["selected_on"] == TODAY

    for age in range(1, 11):
        add_review(setup, TODAY + (30 - age) * 86400, milliseconds=600000)
    now[0] = TODAY + 29 * 86400
    renderer._activity_legend([])
    assert saved_reference(conf) == original

    now[0] += 86400
    add_review(setup, now[0], milliseconds=99999999)
    renderer._activity_legend([])
    refreshed = copy.deepcopy(saved_reference(conf))
    assert refreshed["value"] == 10
    assert refreshed["day"] < now[0]
    assert refreshed["selected_on"] == now[0]
    assert len(setup.conf.saves) == 2
    renderer._activity_legend([])
    assert saved_reference(conf) == refreshed
    assert len(setup.conf.saves) == 2

    # A manual reference stays fixed even after the refresh interval expires.
    manual = dict(refreshed, source="selected")
    conf["activity_baselines"][baseline_key(conf)] = manual
    now[0] += 30 * 86400
    renderer._activity_legend([])
    assert saved_reference(conf) == manual
    assert len(setup.conf.saves) == 2


def test_automatic_refresh_keeps_old_goal_with_sparse_history_and_retries_next_day(setup):
    from review_heatmap.metrics import baseline_key, saved_reference

    conf = setup.conf["synced"]
    conf.update(activity_metric="recorded_time", activity_scale="baseline")
    key = baseline_key(conf, 1)
    old = {"day": TODAY - 50 * 86400, "value": 3, "source": "automatic",
           "percentile": 90, "selected_on": TODAY - 30 * 86400}
    conf["activity_baselines"][key] = old
    setup.db.connection.execute("INSERT INTO cards VALUES (1, 1, 0, 2)")
    for age in range(1, 7):
        add_review(setup, TODAY - age * 86400, milliseconds=60000)
    renderer = make_renderer(setup)
    renderer._activity_legend([], 1)
    assert saved_reference(conf, 1) == dict(old, last_checked_on=TODAY)
    renderer._activity_legend([], 1)
    assert len(setup.conf.saves) == 1
    assert saved_reference(conf) is None

    # A failed attempt yesterday must not postpone recalibration a full month.
    conf["activity_baselines"][key]["last_checked_on"] = TODAY - 86400
    add_review(setup, TODAY - 7 * 86400, milliseconds=60000)
    renderer._activity_legend([], 1)
    assert saved_reference(conf, 1)["selected_on"] == TODAY
    assert saved_reference(conf, 1)["value"] == 1
    assert saved_reference(conf) is None


def test_global_and_deck_heatmaps_choose_their_own_days_and_show_reminders(setup, monkeypatch):
    from review_heatmap.metrics import baseline_key, reference_from_day, saved_reference

    setup.db.connection.executemany("INSERT INTO cards VALUES (?, ?, 0, 2)", [(1, 1), (2, 2)])
    conf = setup.conf["synced"]
    conf.update(activity_scale="baseline", activity_metric="recorded_time")
    for age in range(1, 11):
        add_review(setup, TODAY - age * 86400, cid=1, milliseconds=age * 60000)
        add_review(setup, TODAY - age * 86400, cid=2,
                   milliseconds=(11 - age) * 120000, sequence=1)
    renderer = make_renderer(setup)
    view = setup.modules.renderer.HeatmapView
    global_html = renderer.render(view.deckbrowser, limfcst=0)
    global_ref = copy.deepcopy(saved_reference(conf))
    assert global_ref["day"] == TODAY - 2 * 86400
    assert global_ref["percentile"] == 90
    assert 'class="rh-reference-reminder"' in global_html
    assert '"referenceScope": "global"' in global_html

    deck_html = renderer.render(view.overview, current_deck_only=True, limfcst=0)
    deck_ref = saved_reference(conf, 1)
    assert deck_ref["day"] == TODAY - 9 * 86400
    assert deck_ref["value"] == 9
    assert '"referenceScope": "deck:1"' in deck_html
    assert 'class="rh-reference-reminder"' in deck_html
    assert saved_reference(conf) == global_ref

    # Statistics for the same deck use the same reference and dismissal state.
    conf["activity_reference_reminders_dismissed"]["deck:1"] = True
    assert 'class="rh-reference-reminder"' not in renderer.render(
        view.stats, current_deck_only=True, limfcst=0,
    )
    assert 'class="rh-reference-reminder"' in renderer.render(view.deckbrowser, limfcst=0)
    monkeypatch.setattr(setup.col.decks, "current", lambda: {"id": 2})
    monkeypatch.setattr(setup.col.decks, "get_current_id", lambda: 2)
    assert 'class="rh-reference-reminder"' in renderer.render(
        view.overview, current_deck_only=True, limfcst=0,
    )
    assert saved_reference(conf, 2)["day"] == TODAY - 2 * 86400

    manual = reference_from_day((TODAY - 5 * 86400, 1, 360000, [(360000, 1)]),
                                "recorded_time", "selected")
    conf["activity_baselines"][baseline_key(conf, 2)] = manual
    assert 'class="rh-reference-reminder"' not in renderer.render(
        view.overview, current_deck_only=True, limfcst=0,
    )
    assert saved_reference(conf, 2) == manual


def test_old_automatic_selection_upgrades_to_p90_but_manual_dates_do_not(setup):
    from review_heatmap.metrics import baseline_key, saved_reference

    conf = setup.conf["synced"]
    conf.update(activity_scale="baseline", activity_metric="recorded_time")
    for age in range(1, 11):
        add_review(setup, TODAY - age * 86400, milliseconds=age * 60000)
    key = baseline_key(conf)
    conf["activity_baselines"][key] = {
        "day": TODAY - 8 * 86400, "value": 8, "source": "automatic",
    }
    renderer = make_renderer(setup)
    renderer._activity_legend([])
    assert saved_reference(conf)["day"] == TODAY - 9 * 86400
    assert saved_reference(conf)["percentile"] == 90
    conf["activity_baselines"][key] = {
        "day": TODAY - 3 * 86400, "value": 3, "source": "selected",
    }
    renderer._activity_legend([])
    assert saved_reference(conf)["day"] == TODAY - 3 * 86400


def test_mixed_durations_reach_heatmap_progress_and_reference_migration(setup):
    from review_heatmap.metrics import baseline_key, saved_reference

    yesterday = TODAY - 86400
    for date in (yesterday, TODAY):
        for sequence, duration in enumerate((15000, 15000, 240000)):
            add_review(setup, date, milliseconds=duration, sequence=sequence)
    conf = setup.conf["synced"]
    conf.update(activity_metric="time", activity_scale="baseline")
    old_parts = json.loads(baseline_key(conf))
    old_parts[0] = 2
    old_key = json.dumps(old_parts, separators=(",", ":"))
    old_reference = {"day": yesterday, "reviews": 3, "time_ms": 270000,
                     "value": 13.5, "source": "selected"}
    conf["activity_baselines"][old_key] = copy.deepcopy(old_reference)
    report = setup.reporter.get_report(limfcst=0)
    assert report.review_durations[TODAY] == [(15000, 2), (240000, 1)]
    renderer = make_renderer(setup)
    renderer._activity_legend([])
    assert saved_reference(conf)["value"] == 3
    assert saved_reference(conf)["day"] == yesterday
    assert conf["activity_baselines"][old_key] == old_reference
    progress = renderer._today_progress(report)
    assert progress["percent"] == pytest.approx(100 / 0.85)
    html = renderer._generate_heatmap_elm(report, [], False)
    options = json.loads(re.search(r"new ReviewHeatmap\((.+)\);", html)[1])
    assert options["dayColors"][str(TODAY)] == progress["color"]
    conf["activity_scale"] = "adaptive"
    html = renderer._generate_heatmap_elm(report, [], False)
    values = json.loads(re.search(r"reviewHeatmap.create\((.+)\);", html)[1])
    assert values[str(TODAY)] == 6  # exactly the median, without the 85% discount
    assert renderer._today_progress(report) is None


def test_missing_legacy_reference_is_not_replaced_by_automatic_selection(setup):
    from review_heatmap.metrics import baseline_key, saved_reference

    for age in range(1, 9):
        add_review(setup, TODAY - age * 86400)
    conf = setup.conf["synced"]
    conf.update(activity_metric="workload", activity_scale="baseline")
    old_parts = json.loads(baseline_key(conf))
    old_parts[0] = 2
    old_key = json.dumps(old_parts, separators=(",", ":"))
    conf["activity_baselines"][old_key] = {
        "day": TODAY - 20 * 86400, "value": 30, "source": "selected",
    }
    before = copy.deepcopy(conf)
    assert make_renderer(setup)._activity_legend([]) == list(range(1, 10))
    assert saved_reference(conf) is None
    assert conf == before
    assert not setup.conf.saves


def test_baseline_colors_preserve_real_totals_and_leave_empty_days_alone(setup):
    from review_heatmap.metrics import baseline_key, reference_from_day

    conf = setup.conf["synced"]
    conf.update(activity_metric="workload", activity_scale="baseline", colors="ice")
    reference_day = TODAY - 4 * 86400
    reference = reference_from_day((reference_day, 100, 6000000), "workload", "selected")
    conf["activity_baselines"] = {baseline_key(conf): reference}
    report = setup.reporter._get_activity(
        [(reference_day, 100), (TODAY - 3 * 86400, 10), (TODAY - 86400, 100), (TODAY, 0)],
        [(TODAY + 86400, -200)],
        {reference_day: 6000000, TODAY - 3 * 86400: 600000, TODAY - 86400: 6000000},
    )
    renderer = make_renderer(setup)
    html = renderer._generate_heatmap_elm(report, renderer._activity_legend([]), False)
    values = json.loads(re.search(r"reviewHeatmap.create\((.+)\);", html)[1])
    options = json.loads(re.search(r"new ReviewHeatmap\((.+)\);", html)[1])
    assert values[str(reference_day)] == 5
    assert values[str(TODAY - 3 * 86400)] == 1
    assert values[str(TODAY - 86400)] == 6
    assert str(TODAY - 2 * 86400) not in values
    assert values[str(TODAY)] == 0
    assert values[str(TODAY + 86400)] == -200
    assert options["history"][str(reference_day)] == [100, 6000000]
    assert "rh-baseline" in renderer._get_css_classes(setup.modules.renderer.HeatmapView.deckbrowser)
    assert conf["activity_baselines"][baseline_key(conf)] == reference
    assert 'class="streak rh-theme-lime"' in renderer._generate_stats_elm(report, list(range(10)))
    assert conf["colors"] == "ice"
    conf["activity_scale"] = "adaptive"
    assert renderer._baseline_reference() is None
    assert 'class="streak"' in renderer._generate_stats_elm(report, list(range(10)))
    assert conf["colors"] == "ice"
    conf["activity_metric"] = "reviews"
    assert "rh-baseline" not in renderer._get_css_classes(setup.modules.renderer.HeatmapView.deckbrowser)


def test_adaptive_uses_included_active_history_and_obeys_date_limits(setup):
    from review_heatmap.metrics import adaptive_anchor, adaptive_color

    for age, count in ((400, 1), (10, 2), (2, 3), (0, 100)):
        for sequence in range(count):
            add_review(setup, TODAY - age * 86400, milliseconds=60000, sequence=sequence)
    # Forecast cards and days without reviews must not affect the median.
    setup.db.connection.execute("INSERT INTO cards VALUES (1, 1, 101, 2)")
    report = setup.reporter.get_report(limfcst=2)
    renderer = make_renderer(setup)
    conf = setup.conf["synced"]
    assert conf["activity_scale"] == "adaptive"
    assert renderer._today_progress(report) is None
    assert adaptive_anchor(renderer._activity_scores(report).values()) == 2.5
    html = renderer._generate_heatmap_elm(report, renderer._activity_legend([]), False)
    options = json.loads(re.search(r"new ReviewHeatmap\((.+)\);", html)[1])
    assert options["dayColors"][str(TODAY - 10 * 86400)] == adaptive_color(2, 2.5)
    assert options["dayColors"][str(TODAY)] == adaptive_color(100, 2.5)
    assert "#ffffff" not in options["dayColors"].values()
    assert str(TODAY + 86400) not in options["dayColors"]
    assert conf["activity_baselines"] == {}
    assert not setup.conf.saves
    assert not options["showPaletteButton"]
    assert "rh-baseline" not in renderer._get_css_classes(setup.modules.renderer.HeatmapView.deckbrowser)
    # Adaptive ignores the goal gradient and follows the chosen original theme.
    before = copy.deepcopy(options["dayColors"])
    setup.conf["local"]["baseline_gradient"]["above"][0]["hsl"] = [0, 100, 50]
    html = renderer._generate_heatmap_elm(report, renderer._activity_legend([]), False)
    assert json.loads(re.search(r"new ReviewHeatmap\((.+)\);", html)[1])["dayColors"] == before
    conf["colors"] = "ice"
    html = renderer._generate_heatmap_elm(report, renderer._activity_legend([]), False)
    options = json.loads(re.search(r"new ReviewHeatmap\((.+)\);", html)[1])
    assert options["dayColors"][str(TODAY)] == adaptive_color(100, 2.5, "ice")
    conf["limhist"] = 3
    filtered = setup.reporter.get_report(limfcst=2)
    assert adaptive_anchor(renderer._activity_scores(filtered).values()) == 51.5


def test_fixed_setting_migrates_without_changing_saved_references(setup):
    conf = setup.conf
    conf["synced"].update(activity_scale="fixed", activity_baselines={"legacy": {"day": TODAY}})
    before = copy.deepcopy(dict(conf))
    setup.modules.config.ensure_activity_defaults(conf)
    expected = copy.deepcopy(before)
    expected["synced"]["activity_scale"] = "adaptive"
    assert dict(conf) == expected
    conf["synced"]["activity_scale"] = "baseline"
    setup.modules.config.ensure_activity_defaults(conf)
    assert conf["synced"]["activity_scale"] == "baseline"


def test_cache_expires_on_day_rollover_or_configuration_change(setup, monkeypatch):
    add_review(setup, TODAY)
    renderer = make_renderer(setup)
    view = setup.modules.renderer.HeatmapView.deckbrowser
    renderer.render(view, limfcst=0)
    assert renderer._cache_still_valid(view, None, 0, False)
    setup.conf["synced"]["activity_metric"] = "custom"
    assert not renderer._cache_still_valid(view, None, 0, False)
    renderer.render(view, limfcst=0)
    setup.conf["synced"]["custom_time_weight"] = 0.7
    assert not renderer._cache_still_valid(view, None, 0, False)
    renderer.render(view, limfcst=0)
    monkeypatch.setattr(type(setup.reporter), "_today", property(lambda self: TODAY + 86400))
    assert not renderer._cache_still_valid(view, None, 0, False)


def test_additive_settings_migration_preserves_existing_data(addon_modules):
    conf = Config(addon_modules.config.config_defaults)
    # Palette defaults are loaded separately from config.json in Anki.
    conf["local"]["baseline_gradient_version"] = 2
    for key in ("activity_metric", "activity_scale", "activity_baselines"):
        del conf["synced"][key]
    del conf["profile"]["time_notice_seen"]
    del conf["profile"]["show_today_progress"]
    conf["synced"].update(colors="ice", limdate=1234, limdecks=[17], custom={"keep": True})
    previous = copy.deepcopy(conf)
    addon_modules.config.ensure_activity_defaults(conf)
    for storage, values in previous.items():
        for key, value in values.items():
            assert conf[storage][key] == value
    assert conf["synced"]["activity_metric"] == "workload"
    assert conf["profile"]["show_today_progress"] is True
    assert conf["profile"]["time_notice_seen"] is False
    conf["synced"]["activity_metric"] = "workload"
    addon_modules.config.ensure_activity_defaults(conf)
    assert conf["synced"]["activity_metric"] == "workload"

    from review_heatmap.metrics import baseline_key, saved_reference

    key_parts = json.loads(baseline_key(conf["synced"]))
    key_parts[0] = 1
    old_key = json.dumps(key_parts, separators=(",", ":"))
    conf["synced"]["activity_baselines"][old_key] = {
        "day": TODAY, "reviews": 120, "time_ms": 2700000,
        "value": (120 * 45) ** 0.5, "source": "selected",
    }
    palette = copy.deepcopy(conf["local"])
    addon_modules.config.ensure_activity_defaults(conf)
    # Loading settings has no durations; migration waits for the reporter.
    assert saved_reference(conf["synced"]) is None
    assert conf["synced"]["activity_baselines"][old_key]["day"] == TODAY
    assert conf["local"] == palette


def test_workload_defaults_do_not_replace_existing_mode_choices(addon_modules):
    conf = Config(addon_modules.config.config_defaults)
    assert conf["synced"]["activity_metric"] == "workload"
    assert conf["profile"]["show_today_progress"] is True
    conf["profile"]["show_today_progress"] = False
    for metric in ("reviews", "time", "workload", "custom", "recorded_time"):
        conf["synced"]["activity_metric"] = metric
        addon_modules.config.ensure_activity_defaults(conf)
        assert conf["synced"]["activity_metric"] == metric
        assert conf["profile"]["show_today_progress"] is False


@pytest.mark.parametrize("reviews, expected", [(0, 0), (34, 40), (85, 100), (170, 200)])
def test_today_progress_matches_workload_baseline_and_calendar_color(setup, reviews, expected):
    from review_heatmap.metrics import baseline_key, reference_from_day

    conf = setup.conf["synced"]
    conf.update(activity_metric="workload", activity_scale="baseline")
    setup.conf["profile"]["show_today_progress"] = True
    reference_day = TODAY - 86400
    reference = reference_from_day((reference_day, 100, 6000000), "workload", "selected")
    conf["activity_baselines"] = {baseline_key(conf): reference}
    history = [(reference_day, 100)]
    times = {reference_day: 6000000}
    if reviews:
        history.append((TODAY, reviews))
        times[TODAY] = reviews * 60000
    report = setup.reporter._get_activity(history, [(TODAY, -400)], times)
    renderer = make_renderer(setup)
    progress = renderer._today_progress(report)
    assert progress["percent"] == pytest.approx(expected)
    if reviews:
        html = renderer._generate_heatmap_elm(report, [], False)
        options = json.loads(re.search(r"new ReviewHeatmap\((.+)\);", html)[1])
        assert progress["color"] == options["dayColors"][str(TODAY)]
    else:
        # Pending cards do not become completed work.
        assert report.activity[TODAY] == -400
    reference["day"] = TODAY
    assert renderer._today_progress(report)["color"] == "#ffffff"


def test_today_progress_visibility_and_missing_baseline(setup):
    renderer = make_renderer(setup)
    assert renderer._today_progress(None) is None  # Classic never shows goal progress
    setup.conf["synced"]["activity_scale"] = "baseline"
    assert renderer._today_progress(None) == {"percent": None, "color": "", "context": ""}
    assert renderer._today_progress_script(setup.modules.renderer.HeatmapView.overview, None) == ""
    setup.conf["profile"]["show_today_progress"] = False
    assert renderer._today_progress(None) is None
    setup.conf["profile"]["show_today_progress"] = True
    setup.conf["synced"]["activity_metric"] = "reviews"
    assert renderer._today_progress(None) is None
    for metric in ("time", "custom", "recorded_time"):
        setup.conf["synced"]["activity_metric"] = metric
        assert renderer._today_progress(None)["percent"] is None


def test_today_progress_is_rendered_when_calendar_is_hidden(setup):
    add_review(setup, TODAY)
    setup.conf["profile"].update(show_today_progress=True, statsvis=False)
    setup.conf["profile"]["display"]["deckbrowser"] = False
    setup.conf["synced"]["activity_scale"] = "baseline"
    html = make_renderer(setup).render(setup.modules.renderer.HeatmapView.deckbrowser)
    assert "ReviewHeatmap.updateTodayProgress(" in html
    assert "new ReviewHeatmap(" not in html


def test_progress_animation_context_survives_reviews_but_resets_for_new_targets(setup):
    from review_heatmap.metrics import baseline_key, reference_from_day

    conf = setup.conf["synced"]
    conf["activity_scale"] = "baseline"
    reference = reference_from_day((TODAY - 86400, 100, 6000000), "workload", "selected")
    conf["activity_baselines"][baseline_key(conf)] = reference
    renderer = make_renderer(setup)
    before = renderer._today_progress(None)["context"]
    report = setup.reporter._get_activity([(TODAY, 10)], [], {TODAY: 600000})
    assert renderer._today_progress(report)["context"] == before
    assert make_renderer(setup)._today_progress(report)["context"] != before
    tomorrow = report._replace(today=report.today + 86400000)
    assert renderer._today_progress(tomorrow)["context"] != before
    reference["value"] *= 2
    assert renderer._today_progress(report)["context"] != before
