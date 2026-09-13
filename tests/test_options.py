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


def test_classic_disables_reference_controls(setup, options_module):
    module, app = options_module
    from aqt.qt import QWidget
    parent = QWidget()
    parent.col = setup.col
    dialog = module.RevHmOptions(setup.conf, parent)
    dialog.selActivityMetric.setCurrentIndex(dialog.selActivityMetric.findData("reviews"))
    assert not dialog.selActivityScale.isEnabled()
    assert not dialog.form.cbTodayProgress.isEnabled()
    assert dialog.referenceGroup.isHidden()
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
