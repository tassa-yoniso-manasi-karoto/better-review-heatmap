"""Integration tests use only a synthetic, in-memory SQL fixture."""

import copy
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
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
        super().__init__(copy.deepcopy(defaults))
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
    assert setup.reporter._cards_done(current_deck_only=True) == [(yesterday, 1, 30000)]
    setup.conf["synced"]["limcdel"] = True
    assert setup.reporter._cards_done() == [(yesterday, 2, 210000)]
    setup.conf["synced"]["limdecks"] = [2]
    assert setup.reporter._cards_done() == [(yesterday, 1, 30000)]
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
            assert values[str(TODAY)] == 45
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
    assert 0 < values[str(TODAY)] < 0.001
    assert report.stats.streak_cur.value == 1


def test_baseline_is_saved_once_and_fixed_scale_never_uses_it(setup):
    for age in range(1, 9):
        add_review(setup, TODAY - age * 86400, milliseconds=age * 60000)
    renderer = make_renderer(setup)
    setup.conf["synced"].update(activity_metric="time", activity_scale="baseline")
    levels = renderer._activity_legend([])
    assert len(setup.conf.saves) == 1
    add_review(setup, TODAY - 86400, milliseconds=99999999, sequence=1)
    assert renderer._activity_legend([]) == levels
    assert len(setup.conf.saves) == 1
    setup.conf["synced"]["activity_scale"] = "fixed"
    assert renderer._activity_legend([]) == [1, 5, 10, 20, 30, 45, 60, 90, 120]


def test_baseline_colors_preserve_real_totals_and_leave_empty_days_alone(setup):
    from review_heatmap.metrics import baseline_key, reference_from_day

    conf = setup.conf["synced"]
    conf.update(activity_metric="workload", activity_scale="baseline")
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
    conf["activity_scale"] = "fixed"
    assert "rh-baseline" not in renderer._get_css_classes(setup.modules.renderer.HeatmapView.deckbrowser)


def test_cache_expires_on_day_rollover_or_configuration_change(setup, monkeypatch):
    add_review(setup, TODAY)
    renderer = make_renderer(setup)
    view = setup.modules.renderer.HeatmapView.deckbrowser
    renderer.render(view, limfcst=0)
    assert renderer._cache_still_valid(view, None, 0, False)
    setup.conf["synced"]["activity_metric"] = "workload"
    assert not renderer._cache_still_valid(view, None, 0, False)
    renderer.render(view, limfcst=0)
    monkeypatch.setattr(type(setup.reporter), "_today", property(lambda self: TODAY + 86400))
    assert not renderer._cache_still_valid(view, None, 0, False)


def test_additive_settings_migration_preserves_existing_data(addon_modules):
    conf = Config(addon_modules.config.config_defaults)
    for key in ("activity_metric", "activity_scale", "activity_baselines"):
        del conf["synced"][key]
    del conf["profile"]["time_notice_seen"]
    conf["synced"].update(colors="ice", limdate=1234, limdecks=[17], custom={"keep": True})
    previous = copy.deepcopy(conf)
    addon_modules.config.ensure_activity_defaults(conf)
    for storage, values in previous.items():
        for key, value in values.items():
            assert conf[storage][key] == value
    assert conf["synced"]["activity_metric"] == "reviews"
    assert conf["profile"]["time_notice_seen"] is False
    conf["synced"]["activity_metric"] = "workload"
    addon_modules.config.ensure_activity_defaults(conf)
    assert conf["synced"]["activity_metric"] == "workload"
