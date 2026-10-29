"""Settings changes on Anki's undo stack, without opening a real collection.

FakeCollection follows the rules of Anki's undo stack that the add-on relies
on (see docs/anki-undo-redo.md); the add-on's real ConfigManager runs on top.
"""

import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_options import options_module  # noqa: F401  (compiles the Qt forms)


SOURCE = Path(__file__).resolve().parents[1] / "src" / "review_heatmap"


class UndoEmpty(Exception):
    pass


class FakeCollection:
    """Collection config with Anki's undo rules and nothing else.

    - set_config(undoable=True) records the previous value; any write, even
      a quiet or unchanged one, starts a new action and so clears redo.
    - Writes through conf[...] clear undo and redo (transact_no_undo).
    - A custom step is kept even when empty; merging folds newer steps in.
    """

    def __init__(self, config=None):
        self.store = {key: self._dump(value) for key, value in (config or {}).items()}
        self.undo_steps = []
        self.redo_steps = []
        self.counter = 0
        self.conf = ConfView(self)

    @staticmethod
    def _dump(value):
        return json.dumps(value, sort_keys=True)

    def _begin(self):
        self.counter += 1
        self.redo_steps.clear()
        return self.counter

    def get_config(self, key, default=None):
        return json.loads(self.store[key]) if key in self.store else default

    def set_config(self, key, value, undoable=False):
        counter = self._begin()
        old, new = self.store.get(key), self._dump(value)
        if old == new:
            return
        self.store[key] = new
        if undoable:
            self.undo_steps.append(
                {"name": "Update Config", "changes": [(key, old)], "counter": counter}
            )

    def add_custom_undo_entry(self, name):
        counter = self._begin()
        self.undo_steps.append({"name": name, "changes": [], "counter": counter})
        return counter

    def merge_undo_entries(self, target):
        index = [step["counter"] for step in self.undo_steps].index(target)
        for step in self.undo_steps[index + 1:]:
            self.undo_steps[index]["changes"].extend(step["changes"])
        del self.undo_steps[index + 1:]
        self.counter = target
        return SimpleNamespace(config=True)

    def write_without_undo(self, key, value):
        self.store[key] = self._dump(value)
        self.undo_steps.clear()
        self.redo_steps.clear()

    def _apply(self, step):
        inverse = []
        for key, value in reversed(step["changes"]):
            inverse.append((key, self.store.get(key)))
            if value is None:
                self.store.pop(key, None)
            else:
                self.store[key] = value
        return dict(step, changes=inverse)

    def undo(self):
        if not self.undo_steps:
            raise UndoEmpty()
        self.redo_steps.append(self._apply(self.undo_steps.pop()))

    def redo(self):
        if not self.redo_steps:
            raise UndoEmpty()
        self.undo_steps.append(self._apply(self.redo_steps.pop()))

    def undo_status(self):
        return SimpleNamespace(
            undo=self.undo_steps[-1]["name"] if self.undo_steps else "",
            redo=self.redo_steps[-1]["name"] if self.redo_steps else "",
            last_step=self.counter,
        )

    def setMod(self):
        pass


class ConfView:
    """col.conf: writes, including those of the dicts it returns, clear both stacks."""

    def __init__(self, col):
        self.col = col

    def __contains__(self, key):
        return key in self.col.store

    def __getitem__(self, key):
        if key not in self.col.store:
            raise KeyError(key)
        value = self.col.get_config(key)
        return WrappedDict(self, key, value) if isinstance(value, dict) else value

    def __setitem__(self, key, value):
        self.col.write_without_undo(key, value)


class WrappedDict(dict):
    def __init__(self, conf, key, value):
        super().__init__(value)
        self._conf, self._key = conf, key

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._conf[self._key] = dict(self)


class FakeAddonManager:
    def __init__(self, defaults, meta=None):
        self.defaults = defaults
        self.meta = copy.deepcopy(meta or {})

    def addonConfigDefaults(self, module):
        return copy.deepcopy(self.defaults)

    def getConfig(self, module):
        return dict(copy.deepcopy(self.defaults), **copy.deepcopy(self.meta))

    def writeConfig(self, module, config):
        self.meta = copy.deepcopy(config)

    def setConfigAction(self, module, action):
        pass

    def setConfigUpdatedAction(self, module, action):
        pass


class FakeMW:
    def __init__(self, col, addon_manager):
        self.col = col
        self.addonManager = addon_manager
        self.pm = SimpleNamespace(profile={})
        self.state = "deckBrowser"
        self.resets = 0
        self.menu_updates = 0
        self.refreshed = []
        self.deckBrowser = SimpleNamespace(refresh=lambda: self.refreshed.append("deckBrowser"))
        self.overview = SimpleNamespace(refresh=lambda: self.refreshed.append("overview"))

    def reset(self):
        # Like Anki: redraw, then refresh the Edit menu.
        self.resets += 1
        self.update_undo_actions()

    def update_undo_actions(self):
        self.menu_updates += 1


@pytest.fixture
def env(addon_modules):
    config_module = addon_modules.config
    manager_class = importlib.import_module(
        "review_heatmap.libaddon.anki.configmanager"
    ).ConfigManager
    local_defaults = json.loads((SOURCE / "config.json").read_text())

    def make(stored=None, meta=None, version=None):
        defaults = copy.deepcopy(config_module.config_defaults)
        if version:
            defaults["synced"]["version"] = version
        col = FakeCollection({} if stored is None else {"heatmap": stored})
        mw = FakeMW(col, FakeAddonManager(local_defaults, meta))
        manager = manager_class(mw, config_dict=defaults, conf_key="heatmap", reset_req=True)
        config_module.load_config(manager)
        return SimpleNamespace(col=col, mw=mw, config=manager)

    return SimpleNamespace(
        make=make, undo=importlib.import_module("review_heatmap.undo"),
        config_module=config_module, local_defaults=local_defaults,
    )


def test_a_change_is_one_labelled_step_that_undo_and_redo_reverse(env):
    undo = env.undo
    setup = env.make()
    col, config = setup.col, setup.config
    col.add_custom_undo_entry("Delete Deck")  # an earlier, unrelated step

    assert undo.commit_synced_change(config, undo.CALENDAR_MODE, lambda conf: dict(conf, mode="months"))
    assert col.undo_status().undo == "Change Heatmap Calendar Mode"
    assert col.get_config("heatmap")["mode"] == config["synced"]["mode"] == "months"
    assert (setup.mw.resets, setup.mw.menu_updates) == (1, 1)

    col.undo()
    assert col.get_config("heatmap")["mode"] == "year"
    assert col.undo_status().undo == "Delete Deck"
    assert col.undo_status().redo == "Change Heatmap Calendar Mode"
    col.redo()
    assert col.get_config("heatmap")["mode"] == "months"

    # Nothing to record: no empty step, no redraw.
    assert not undo.commit_synced_change(config, undo.CALENDAR_MODE, lambda conf: conf)
    assert [step["name"] for step in col.undo_steps] == [
        "Delete Deck", "Change Heatmap Calendar Mode",
    ]
    assert setup.mw.resets == 1


def test_a_quiet_change_only_refreshes_the_edit_menu(env):
    undo = env.undo
    setup = env.make()

    def dismiss(conf):
        conf["activity_reference_reminders_dismissed"]["global"] = True
        return conf

    assert undo.commit_synced_change(setup.config, undo.DISMISS_REMINDER, dismiss, refresh=False)
    assert (setup.mw.resets, setup.mw.menu_updates) == (0, 1)
    assert setup.col.undo_status().undo == "Dismiss Heatmap Reference Reminder"


def test_a_live_previewed_session_undoes_to_its_starting_point(env):
    undo = env.undo
    setup = env.make()
    col, config = setup.col, setup.config
    start = copy.deepcopy(config["synced"]["baseline_gradient"])
    for lightness in (10, 20, 30):  # live previews are quiet saves
        synced = copy.deepcopy(config["synced"])
        synced["baseline_gradient"]["below"][0]["hsl"][2] = lightness
        config["synced"] = synced
        config.save("synced")
    final = copy.deepcopy(config["synced"]["baseline_gradient"])

    assert undo.commit_synced_change(
        config, undo.GRADIENT, lambda conf: dict(conf, baseline_gradient=final),
        before={"baseline_gradient": start},
    )
    assert [step["name"] for step in col.undo_steps] == ["Change Heatmap Gradient"]
    col.undo()
    assert col.get_config("heatmap")["baseline_gradient"] == start
    col.redo()
    assert col.get_config("heatmap")["baseline_gradient"] == final


def test_the_change_builds_on_the_stored_value_not_a_stale_cache(env):
    undo = env.undo
    setup = env.make()
    col, config = setup.col, setup.config
    undo.commit_synced_change(config, undo.CALENDAR_MODE, lambda conf: dict(conf, mode="months"))
    col.undo()  # the cache still says "months"
    assert undo.commit_synced_change(config, undo.COLOR_SCHEME, lambda conf: dict(conf, colors="olive"))
    stored = col.get_config("heatmap")
    assert (stored["mode"], stored["colors"]) == ("year", "olive")


def test_an_update_moves_the_palette_out_of_meta_json(env):
    shipped = env.local_defaults["baseline_gradient_default"]
    assert "baseline_gradient" not in env.local_defaults
    custom = copy.deepcopy(shipped)
    custom["below"][0]["hsl"] = [200, 50, 50]
    # Stored settings older than the add-on: libaddon merges the defaults in
    # first, and must not insert a default palette over the user's own.
    setup = env.make(
        stored={"mode": "months", "version": "0.1.0"},
        meta={"baseline_gradient": custom}, version="99.0.0",
    )
    stored = setup.col.get_config("heatmap")
    assert stored["baseline_gradient"] == custom
    assert stored["mode"] == "months"
    assert stored["version"] == "99.0.0"
    # Once in the collection, the old copy leaves the Config window.
    addons = setup.mw.addonManager
    assert "baseline_gradient" not in addons.getConfig("review_heatmap")
    assert addons.getConfig("review_heatmap")["baseline_gradient_default"] == shipped
    # A copy put back by hand has no effect and goes away again.
    addons.meta["baseline_gradient"] = shipped
    env.config_module.load_config(setup.config)
    assert setup.col.get_config("heatmap")["baseline_gradient"] == custom
    assert "baseline_gradient" not in addons.getConfig("review_heatmap")


def test_the_old_palette_stays_until_the_collection_holds_it(env, monkeypatch):
    custom = copy.deepcopy(env.local_defaults["baseline_gradient_default"])
    custom["below"][0]["hsl"] = [200, 50, 50]

    def fail(*args, **kwargs):
        raise RuntimeError("collection unavailable")

    monkeypatch.setattr(FakeCollection, "set_config", fail)
    with pytest.raises(RuntimeError):
        env.make(meta={"baseline_gradient": custom})
    monkeypatch.undo()
    setup = env.make(meta={"baseline_gradient": custom})
    assert setup.col.get_config("heatmap")["baseline_gradient"] == custom


def test_three_way_patch_applies_only_the_users_edits(env):
    patch = env.undo.three_way_patch
    original = {"mode": "year", "colors": "lime", "activity_baselines": {"a": 1, "b": 2}, "old": 1}
    edited = {"mode": "months", "colors": "lime", "activity_baselines": {"a": 1, "c": 3},
              "limhist": 30}
    stored = {"mode": "year", "colors": "olive", "old": 1, "version": "x",
              "activity_baselines": {"a": 1, "b": 2, "automatic": 9}}
    assert patch(stored, original, edited) == {
        "mode": "months", "colors": "olive", "limhist": 30, "version": "x",
        "activity_baselines": {"a": 1, "c": 3, "automatic": 9},
    }
    assert patch({"limhist": 30}, {"limhist": 30}, {"limhist": 30.0}) == {"limhist": 30}


ORIGINAL_OPTIONS = {
    "activity_metric": "workload", "activity_scale": "adaptive", "colors": "lime",
    "mode": "year", "limhist": 0, "limdecks": [], "custom_time_weight": 0.5,
    "fsrs_calibration": {}, "activity_baselines": {}, "activity_reference_date": 0,
}


@pytest.mark.parametrize("edits, source, label", [
    ({"activity_metric": "fsrs"}, None, "Change Heatmap Measure"),
    ({"activity_scale": "baseline"}, None, "Change Heatmap Color Scale"),
    ({}, "selected", "Set Heatmap Reference Day"),
    ({}, "automatic", "Use Automatic Heatmap Reference Day"),
    ({"fsrs_calibration": {"weight": 2}}, None, "Calibrate Heatmap Measure"),
    ({"custom_time_weight": 0.7}, None, "Change Heatmap Workload Weights"),
    ({"colors": "olive"}, None, "Change Heatmap Color Scheme"),
    ({"mode": "months"}, None, "Change Heatmap Calendar Mode"),
    ({"limdecks": [3], "limhist": 30}, None, "Change Heatmap Exclusions"),
    ({"activity_metric": "fsrs"}, "selected", "Change Heatmap Options"),
    ({"activity_baselines": {"key": {"source": "selected"}},
      "activity_reference_date": 5}, None, None),
    ({"limhist": 0.0}, None, None),
])
def test_options_edits_are_labelled_by_what_the_user_changed(env, edits, source, label):
    edited = dict(ORIGINAL_OPTIONS, **edits)
    assert env.undo.describe_change(ORIGINAL_OPTIONS, edited, source) == label


def test_the_heatmap_reloads_and_redraws_after_anki_undoes_a_step(env, options_module):
    controller_module = importlib.import_module("review_heatmap.controller")
    undo = env.undo
    setup = env.make()
    col, config, mw = setup.col, setup.config, setup.mw
    controller = object.__new__(controller_module.HeatmapController)
    controller._mw, controller._config = mw, config
    controller._collection, controller._renderer = col, None
    config_changes = SimpleNamespace(config=True)

    undo.commit_synced_change(config, undo.CALENDAR_MODE, lambda conf: dict(conf, mode="months"))
    # mw.reset()'s synthesized op after our own step finds nothing to reload.
    controller._on_operation_did_execute(config_changes, None)
    assert mw.refreshed == []

    col.undo()
    controller._on_operation_did_execute(config_changes, None)
    assert config["synced"]["mode"] == "year"
    assert mw.refreshed == ["deckBrowser"]
    # The reload wrote nothing, so Redo survives, and later quiet saves no
    # longer write the stale value back.
    assert col.undo_status().redo == "Change Heatmap Calendar Mode"
    config.save("synced", profile_unload=True)
    assert col.get_config("heatmap")["mode"] == "year"

    # Anki fires the hook after every undo and every redo.
    undo.commit_synced_change(config, undo.CALENDAR_MODE, lambda conf: dict(conf, mode="months"))
    col.undo()
    controller._on_operation_did_execute(config_changes, None)
    col.redo()
    mw.state = "overview"
    controller._on_operation_did_execute(config_changes, None)
    assert config["synced"]["mode"] == "months"
    assert mw.refreshed == ["deckBrowser", "deckBrowser", "overview"]

    # Operations without config changes are ignored, syncs are not.
    col.undo()
    controller._on_operation_did_execute(SimpleNamespace(config=False), None)
    assert config["synced"]["mode"] == "months"
    controller._on_sync_did_finish()
    assert config["synced"]["mode"] == "year"
