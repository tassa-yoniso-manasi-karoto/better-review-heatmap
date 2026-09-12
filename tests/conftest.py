"""Load add-on modules without starting Anki or opening a collection."""

import importlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "src" / "review_heatmap"
package = ModuleType("review_heatmap")
package.__path__ = [str(SOURCE)]
sys.modules["review_heatmap"] = package
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def addon_modules(tmp_path_factory):
    aqt = pytest.importorskip("aqt")
    original_mw = aqt.mw
    folder = tmp_path_factory.mktemp("addons")
    aqt.mw = SimpleNamespace(
        pm=SimpleNamespace(addonFolder=lambda: str(folder), profile={}),
        addonManager=SimpleNamespace(
            setConfigAction=lambda *args: None,
            setConfigUpdatedAction=lambda *args: None,
            addonConfigDefaults=lambda *args: json.loads(
                (SOURCE / "config.json").read_text()
            ),
        ),
        col=None,
        reset=lambda: None,
    )
    consts = importlib.import_module("review_heatmap.consts")
    libconsts = importlib.import_module("review_heatmap.libaddon.consts")
    libconsts.set_addon_properties(consts.ADDON)
    modules = SimpleNamespace(**{
        name: importlib.import_module("review_heatmap." + name)
        for name in ("config", "activity", "renderer")
    })
    yield modules
    aqt.mw = original_mw
