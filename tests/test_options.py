import copy
import importlib
import io
import sys
from pathlib import Path
from types import ModuleType

import pytest

from test_activity import Config, TODAY, add_review, setup


@pytest.fixture
def options_module(addon_modules, monkeypatch):
    qt = pytest.importorskip("aqt.qt")
    uic = pytest.importorskip("PyQt6.uic")
    from anki.lang import set_lang
    set_lang("en_US")  # translation backend only; no collection is opened
    app = qt.QApplication.instance() or qt.QApplication([])
    qt.QDir.addSearchPath("review_heatmap", str(Path(__file__).resolve().parents[1] / "resources"))
    # Compile the existing designer form in memory. No build outputs or Anki
    # installation are needed, and no collection is opened.
    package = ModuleType("review_heatmap.gui.forms")
    for name in ("options", "contrib"):
        generated = io.StringIO()
        uic.compileUi(str(Path(__file__).resolve().parents[1] / f"designer/{name}.ui"), generated)
        form = ModuleType(f"review_heatmap.gui.forms.{name}")
        exec(generated.getvalue().replace("import icons_rc", ""), form.__dict__)
        setattr(package, name, form)
        monkeypatch.setitem(sys.modules, form.__name__, form)
    monkeypatch.setitem(sys.modules, "review_heatmap.gui.forms", package)
    module = importlib.import_module("review_heatmap.gui.options")
    yield module, app


def test_settings_cancel_and_accept_keep_reference_changes_local(setup, options_module):
    module, app = options_module
    from aqt.qt import QWidget
    from review_heatmap.metrics import baseline_key, reference_from_day

    parent = QWidget()
    parent.col = setup.col
    conf = setup.conf
    original = copy.deepcopy(dict(conf))
    dialog = module.RevHmOptions(conf, parent)
    dialog.selActivityMetric.setCurrentIndex(dialog.selActivityMetric.findData("workload"))
    dialog.selActivityScale.setCurrentIndex(dialog.selActivityScale.findData("baseline"))
    assert dialog.form.groupBox.title() == "Appearance"
    assert dialog.form.groupBox.isAncestorOf(dialog.form.cbTodayProgress)
    assert dialog.form.tab.isAncestorOf(dialog.form.cbTodayProgress)
    assert dialog.form.cbTodayProgress.isEnabled()
    assert dialog.form.cbTodayProgress.isChecked()
    dialog.form.cbTodayProgress.setChecked(False)
    pending = dialog.getData()["synced"]
    reference = reference_from_day((TODAY - 86400, 120, 2700000), "workload", "selected")
    dialog._setReference(pending, reference)
    assert dict(conf) == original
    assert dialog.selActivityScale.isEnabled()
    dialog.reject()
    assert dict(conf) == original
    assert conf.saves == []

    dialog = module.RevHmOptions(conf, parent)
    dialog.selActivityMetric.setCurrentIndex(dialog.selActivityMetric.findData("workload"))
    dialog.selActivityScale.setCurrentIndex(dialog.selActivityScale.findData("baseline"))
    dialog.form.cbTodayProgress.setChecked(False)
    dialog._setReference(dialog.getData()["synced"], reference)
    dialog.accept()
    assert conf["synced"]["activity_metric"] == "workload"
    assert conf["profile"]["show_today_progress"] is False
    assert conf["synced"]["activity_baselines"][baseline_key(conf["synced"])] == reference
    assert len(conf.saves) == 1


def test_reference_picker_rejects_empty_days_and_tracks_each_metric(
    setup, options_module, monkeypatch,
):
    module, app = options_module
    from aqt.qt import QDate, QWidget
    from review_heatmap.metrics import saved_reference

    notices = []
    monkeypatch.setattr(module, "showInfo", lambda *args, **kwargs: notices.append(args[0]))
    rows = {
        TODAY - 86400: [(TODAY - 86400, 120, 2700000, [(22500, 120)])],
        TODAY - 2 * 86400: [(TODAY - 2 * 86400, 60, 1800000, [(30000, 60)])],
    }
    monkeypatch.setattr(
        module.ActivityReporter, "reference_history",
        lambda self, day, with_durations=False, deck_id=None: rows.get(day, []),
    )
    parent = QWidget()
    parent.col = setup.col
    dialog = module.RevHmOptions(setup.conf, parent)
    dialog.selActivityMetric.setCurrentIndex(dialog.selActivityMetric.findData("workload"))
    initial_date = dialog.dateReference.date()
    dialog.dateReference.setDate(QDate(2026, 3, 1))
    assert dialog.dateReference.date() == initial_date
    assert saved_reference(dialog.getData()["synced"]) is None
    assert "No saved reference" in dialog.labReference.text()

    dialog.dateReference.setDate(QDate(2026, 3, 9))
    reference = copy.deepcopy(saved_reference(dialog.getData()["synced"]))
    assert reference["day"] == TODAY - 86400
    assert dialog.labReference.isHidden()
    dialog.dateReference.setDate(QDate(2026, 3, 1))
    assert dialog.dateReference.date() == QDate(2026, 3, 9)
    assert saved_reference(dialog.getData()["synced"]) == reference
    assert dialog.getData()["synced"]["activity_reference_date"] == reference["day"]
    assert len(notices) == 2

    dialog.selActivityMetric.setCurrentIndex(dialog.selActivityMetric.findData("time"))
    assert "No saved reference" in dialog.labReference.text()
    assert not dialog.labReference.isHidden()
    dialog.dateReference.setDate(QDate(2026, 3, 8))
    dialog.selActivityMetric.setCurrentIndex(dialog.selActivityMetric.findData("workload"))
    assert dialog.dateReference.date() == QDate(2026, 3, 9)
    assert saved_reference(dialog.getData()["synced"]) == reference
    dialog.accept()
    dialog = module.RevHmOptions(setup.conf, parent)
    assert dialog.dateReference.date() == QDate(2026, 3, 9)
    assert dialog.labReference.isHidden()
    dialog.reject()


def test_classic_disables_reference_controls(setup, options_module):
    module, app = options_module
    from aqt.qt import QWidget
    parent = QWidget()
    parent.col = setup.col
    dialog = module.RevHmOptions(setup.conf, parent)
    assert dialog.selActivityScale.currentData() == "adaptive"
    assert dialog.form.selHmColor.findData("ice") == -1
    assert dialog.selActivityScale.currentText() == "Classic"
    assert dialog.selActivityScale.findData("fixed") == -1
    assert "median score" in dialog.labActivityDescription.text()
    assert not dialog.form.cbTodayProgress.isEnabled()
    assert dialog.btnEditGradient.isHidden()
    assert not dialog.form.selHmColor.isHidden()
    for metric in ("time", "workload", "custom", "recorded_time"):
        dialog.selActivityMetric.setCurrentIndex(dialog.selActivityMetric.findData(metric))
        for scale in ("baseline", "adaptive"):
            dialog.selActivityScale.setCurrentIndex(dialog.selActivityScale.findData(scale))
            assert dialog.form.selHmColor.isHidden() == (scale == "baseline")
            assert dialog.form.label.isHidden() == (scale == "baseline")
            assert dialog.btnEditGradient.isHidden() == (scale == "adaptive")
            assert dialog.form.cbTodayProgress.isEnabled() == (scale == "baseline")
            assert dialog.form.cbTodayProgress.isChecked()  # keep the saved preference
    dialog.selActivityMetric.setCurrentIndex(dialog.selActivityMetric.findData("reviews"))
    assert not dialog.selActivityScale.isEnabled()
    assert not dialog.form.cbTodayProgress.isEnabled()
    assert dialog.referenceGroup.isHidden()
    assert dialog.btnEditGradient.isHidden()
    dialog.reject()


def test_reference_picker_and_automatic_icon_keep_each_deck_independent(
    setup, options_module, monkeypatch,
):
    module, app = options_module
    from aqt.qt import QWidget
    from review_heatmap.metrics import baseline_key, reference_from_day, saved_reference

    monkeypatch.setattr(module.ActivityReporter, "_today", property(lambda self: TODAY))
    setup.db.connection.executemany("INSERT INTO cards VALUES (?, ?, 0, 2)", [(1, 1), (2, 2)])
    for age in range(1, 11):
        add_review(setup, TODAY - age * 86400, cid=1, milliseconds=60000)
        add_review(setup, TODAY - age * 86400, cid=2, milliseconds=age * 60000, sequence=1)
    conf = setup.conf["synced"]
    conf["activity_scale"] = "baseline"
    global_ref = reference_from_day((TODAY - 2 * 86400, 2, 180000), "workload", "selected")
    auto = reference_from_day((TODAY - 86400, 1, 60000), "workload", "automatic")
    conf["activity_baselines"] = {baseline_key(conf): global_ref, baseline_key(conf, 1): auto}
    original = copy.deepcopy(dict(setup.conf))
    parent = QWidget()
    parent.col = setup.col
    dialog = module.RevHmOptions(setup.conf, parent, reference_deck_id=1, focus_reference=True)
    pending = dialog.getData()["synced"]
    assert dialog.selReferenceScope.currentData() == 1
    assert dialog.form.tabWidget.currentIndex() == 1
    assert not dialog.btnAutoReference.icon().isNull()
    assert not dialog.btnAutoReference.text()
    # Approve the already displayed automatic day using the calendar itself.
    dialog.dateReference.calendarWidget().clicked.emit(dialog.dateReference.date())
    assert saved_reference(pending, 1)["source"] == "selected"
    assert saved_reference(pending) == global_ref
    dialog.selReferenceScope.setCurrentIndex(dialog.selReferenceScope.findData(2))
    dialog.btnAutoReference.click()
    assert saved_reference(pending, 2)["percentile"] == 90
    assert saved_reference(pending, 2)["day"] == TODAY - 9 * 86400
    assert saved_reference(pending, 2)["selected_on"] == TODAY
    assert "Last calculated: 2026-03-10" in dialog.btnAutoReference.toolTip()
    dialog.reject()
    assert dict(setup.conf) == original

    dialog = module.RevHmOptions(setup.conf, parent, reference_deck_id=1)
    dialog.dateReference.calendarWidget().clicked.emit(dialog.dateReference.date())
    dialog.accept()
    dialog = module.RevHmOptions(setup.conf, parent, reference_deck_id=1)
    assert saved_reference(dialog.getData()["synced"], 1)["source"] == "selected"
    dialog.selReferenceScope.setCurrentIndex(0)
    assert saved_reference(dialog.getData()["synced"]) == global_ref
    dialog.reject()


def test_reference_bridge_uses_explicit_scope_and_saves_dismissal(
    setup, options_module, monkeypatch,
):
    from aqt.qt import QWidget
    from types import SimpleNamespace

    bridge = importlib.import_module("review_heatmap.web_bridge")
    calls = []
    monkeypatch.setattr(bridge, "invoke_options_dialog", lambda **kwargs: calls.append(kwargs))
    parent = QWidget()
    handler = bridge._CommandHandler(SimpleNamespace(col=setup.col), setup.conf)
    for metric in ("reviews", "time", "workload", "custom", "recorded_time"):
        for scale in ("adaptive", "baseline"):
            setup.conf["synced"].update(activity_metric=metric, activity_scale=scale)
            assert handler("palettevisible", None, parent) == (
                metric != "reviews" and scale == "baseline"
            )
    handler("choosereference", "deck:2", parent)
    assert calls[-1] == {"parent": parent, "reference_deck_id": 2, "focus_reference": True}
    handler("opts", "global", parent)
    assert calls[-1]["reference_deck_id"] is None
    assert handler("dismissreference", "deck:2", parent) is True
    assert setup.conf["synced"]["activity_reference_reminders_dismissed"] == {"deck:2": True}
    assert setup.conf.saves == [("synced", {"profile_unload": True})]
    assert handler("dismissreference", "deck:999", parent) is False
    assert handler("dismissreference", "deck:invalid", parent) is False
    assert handler("dismissreference", None, parent) is False
    assert len(setup.conf.saves) == 1


def test_first_review_browser_selects_only_new_cards_in_the_requested_scope(setup, options_module, monkeypatch):
    from aqt.qt import QWidget
    from types import SimpleNamespace

    bridge = importlib.import_module("review_heatmap.web_bridge")
    setup.db.connection.executemany("INSERT INTO cards VALUES (?, ?, 0, 2)",
                                   [(1, 1), (2, 2), (3, 1)])
    yesterday = TODAY - 86400
    add_review(setup, TODAY - 2 * 86400, cid=1)
    for cid in (1, 2, 3):
        add_review(setup, yesterday, cid=cid, sequence=cid)
    handler = bridge._CommandHandler(SimpleNamespace(col=setup.col), setup.conf)
    searches = []
    monkeypatch.setattr(handler, "browse", lambda query, context: searches.append(query))
    parent = QWidget()
    handler("firstreviews", f"global,{yesterday}", parent)
    assert searches == ["cid:2,3"]
    handler("firstreviews", f"deck:1,{yesterday}", parent)
    assert searches[-1] == "cid:3"
    for payload in ("deck:999,0", "global,invalid", "global,-1", None):
        handler("firstreviews", payload, parent)
    assert len(searches) == 2


def test_legacy_statistics_pass_the_correct_global_or_deck_scope(setup, options_module):
    from types import SimpleNamespace

    views = importlib.import_module("review_heatmap.views")
    calls = []
    injector = object.__new__(views.DeckStatsInjector)
    injector._controller = SimpleNamespace(
        render_for_view=lambda *args, **kwargs: calls.append(kwargs) or "heatmap",
    )
    for whole_collection in (True, False):
        result = injector.on_collection_stats_due_graph(
            SimpleNamespace(type=2, wholeCollection=whole_collection), lambda _: "statistics",
        )
        assert result == "statisticsheatmap"
        assert calls[-1]["current_deck_only"] is (not whole_collection)


def test_custom_exponents_recalculate_the_same_reference_and_respect_cancel(setup, options_module):
    module, app = options_module
    from aqt.qt import QDate, QWidget
    from review_heatmap.metrics import saved_reference

    yesterday = TODAY - 86400
    for sequence, duration in enumerate((15000, 15000, 240000)):
        add_review(setup, yesterday, milliseconds=duration, sequence=sequence)
    parent = QWidget()
    parent.col = setup.col
    original = copy.deepcopy(dict(setup.conf))
    dialog = module.RevHmOptions(setup.conf, parent)
    dialog.selActivityMetric.setCurrentIndex(dialog.selActivityMetric.findData("custom"))
    dialog.selActivityScale.setCurrentIndex(dialog.selActivityScale.findData("baseline"))
    assert not dialog.customGroup.isHidden()
    dialog.dateReference.setDate(QDate(2026, 3, 9))
    conf = dialog.getData()["synced"]
    assert saved_reference(conf)["value"] == 3
    dialog.spinCustomReviewWeight.setValue(0.6)
    assert dialog.spinCustomTimeWeight.value() == 0.4
    assert conf["custom_time_weight"] == 0.4
    reference = saved_reference(conf)
    assert reference["day"] == yesterday
    assert reference["value"] == pytest.approx(2 * 0.25 ** 0.4 + 4 ** 0.4)
    dialog.spinCustomTimeWeight.setValue(1)
    assert dialog.spinCustomReviewWeight.value() == 0
    assert saved_reference(conf)["value"] == 4.5
    dialog.reject()
    assert dict(setup.conf) == original

    dialog = module.RevHmOptions(setup.conf, parent)
    dialog.selActivityMetric.setCurrentIndex(dialog.selActivityMetric.findData("custom"))
    dialog.spinCustomTimeWeight.setValue(0.7)
    dialog.accept()
    assert setup.conf["synced"]["custom_time_weight"] == 0.7
    dialog = module.RevHmOptions(setup.conf, parent)
    assert dialog.spinCustomReviewWeight.value() == 0.3
    assert dialog.spinCustomTimeWeight.value() == 0.7
    dialog.reject()


def test_timer_notice_is_once_per_profile_and_never_changes_deck_settings(
    setup, options_module, monkeypatch,
):
    module, app = options_module
    from types import SimpleNamespace
    notices = []
    callbacks = []
    monkeypatch.setattr(module, "config", setup.conf)
    monkeypatch.setattr(module, "mw", SimpleNamespace(col=setup.col))
    monkeypatch.setattr(module, "showInfo", lambda *args, **kwargs: notices.append(args[0]))
    monkeypatch.setattr(module.QTimer, "singleShot", lambda delay, callback: callbacks.append(callback))
    original = copy.deepcopy(setup.col.conf)
    module._on_profile_open()
    callbacks.pop()()
    module._on_profile_open()
    assert len(notices) == 1
    assert "Maximum answer seconds" in notices[0]
    assert not callbacks
    assert setup.col.conf == original
    assert setup.conf["profile"]["time_notice_seen"]


def test_delayed_notice_does_not_apply_to_a_different_profile(setup, options_module, monkeypatch):
    module, app = options_module
    from types import SimpleNamespace
    callbacks = []
    monkeypatch.setattr(module, "config", setup.conf)
    monkeypatch.setattr(module, "mw", SimpleNamespace(col=setup.col))
    monkeypatch.setattr(module.QTimer, "singleShot", lambda delay, callback: callbacks.append(callback))
    monkeypatch.setattr(module, "showInfo", lambda *args, **kwargs: pytest.fail("unexpected notice"))
    module._on_profile_open()
    module.mw.col = object()
    callbacks.pop()()
    assert not setup.conf["profile"]["time_notice_seen"]
