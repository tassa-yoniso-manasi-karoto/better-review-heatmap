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
    app = qt.QApplication.instance() or qt.QApplication([])
    # Compile the existing designer form in memory. No build outputs or Anki
    # installation are needed, and no collection is opened.
    generated = io.StringIO()
    uic.compileUi(str(Path(__file__).resolve().parents[1] / "designer/options.ui"), generated)
    form = ModuleType("review_heatmap.gui.forms.options")
    exec(generated.getvalue().replace("import icons_rc", ""), form.__dict__)
    package = ModuleType("review_heatmap.gui.forms")
    package.options = form
    monkeypatch.setitem(sys.modules, "review_heatmap.gui.forms", package)
    monkeypatch.setitem(sys.modules, "review_heatmap.gui.forms.options", form)
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
        lambda self, day, with_durations=False: rows.get(day, []),
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
    assert dialog.selActivityScale.currentText() == "Adaptive"
    assert dialog.selActivityScale.findData("fixed") == -1
    assert "median score" in dialog.labActivityDescription.text()
    assert dialog.form.cbTodayProgress.isEnabled()
    assert not dialog.btnEditGradient.isHidden()
    dialog.selActivityMetric.setCurrentIndex(dialog.selActivityMetric.findData("reviews"))
    assert not dialog.selActivityScale.isEnabled()
    assert not dialog.form.cbTodayProgress.isEnabled()
    assert dialog.referenceGroup.isHidden()
    assert dialog.btnEditGradient.isHidden()
    dialog.reject()


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
