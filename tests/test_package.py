"""Check the built archive and start it with Anki APIs but no collection."""

from pathlib import Path
import json
import subprocess
import sys
from zipfile import ZipFile

import pytest


ROOT = Path(__file__).resolve().parents[1]
METADATA = json.loads((ROOT / "addon.json").read_text(encoding="utf-8"))
VERSION = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
ARTIFACT = ROOT / "build" / f"{METADATA['repo_name']}-{VERSION}.ankiaddon"
pytestmark = pytest.mark.skipif(not ARTIFACT.exists(), reason="build the package first")


def test_archive_contains_current_sources_and_qt6_forms():
    with ZipFile(ARTIFACT) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert METADATA["ankiweb_id"] not in manifest["conflicts"]
        assert "1771074083" in manifest["conflicts"]
        for filename in (
            "activity.py", "config.py", "metrics.py", "controller.py", "renderer.py", "gui/options.py",
        ):
            assert archive.read(filename) == (ROOT / "src" / "review_heatmap" / filename).read_bytes()
        for filename in (
            "__init__.py", "manifest.json", "LICENSE.txt", "LICENSES_ICONS.txt",
            "LICENSES_WEB.txt",
            "gui/forms/qt5/options.py", "gui/forms/qt5/contrib.py",
            "gui/forms/qt6/options.py", "gui/forms/qt6/contrib.py",
            "gui/resources/review_heatmap/icons/help.svg", "web/anki-review-heatmap.js",
            "gui/resources/review_heatmap/icons/restore.svg", "web/assets/palette.svg",
            "gui/resources/review_heatmap/icons/auto-reference.svg",
        ):
            assert filename in names
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
        assert not any(name.startswith("review_heatmap/") for name in names)
        assert archive.read("LICENSES_WEB.txt") == (
            ROOT / "src/web/_vendor/LICENSES.txt"
        ).read_bytes()


def test_qt5_forms_initialize(tmp_path):
    pytest.importorskip("PyQt5")
    package = tmp_path / "better_review_heatmap"
    with ZipFile(ARTIFACT) as archive:
        archive.extractall(package)
    script = r'''
import sys
from pathlib import Path
from types import ModuleType
from PyQt5 import QtCore, QtGui, QtWidgets

aqt = ModuleType("aqt")
qt = ModuleType("aqt.qt")
for module in (QtCore, QtGui, QtWidgets):
    for name in dir(module):
        if not name.startswith("_"):
            setattr(qt, name, getattr(module, name))
qt.qtmajor = 5
aqt.qt = qt
sys.modules["aqt"] = aqt
sys.modules["aqt.qt"] = qt

sys.path.insert(0, str(Path(sys.argv[1]) / "gui"))
from forms import options, contrib

app = QtWidgets.QApplication([])
for module in (options, contrib):
    dialog = QtWidgets.QDialog()
    module.Ui_Dialog().setupUi(dialog)
print("Qt 5 forms initialized successfully")
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(package)], capture_output=True, text=True,
        env={**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_packaged_addon_initializes_without_opening_a_collection(tmp_path):
    pytest.importorskip("aqt")
    package = tmp_path / "review_heatmap"
    with ZipFile(ARTIFACT) as archive:
        archive.extractall(package)
    script = r'''
import sys
from pathlib import Path
from types import SimpleNamespace
import aqt
from anki.lang import set_lang
from aqt.qt import QApplication, QMenu, QWidget

set_lang("en_US")  # translation backend only; no collection is opened
app = QApplication([])
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.col = None
        self.pm = SimpleNamespace(addonFolder=lambda: sys.argv[1], profile={})
        self.addonManager = SimpleNamespace(
            setWebExports=lambda *args: None,
            setConfigAction=lambda *args: None,
            setConfigUpdatedAction=lambda *args: None,
            addonConfigDefaults=lambda *args: __import__("json").loads(
                (Path(sys.argv[1]) / "review_heatmap" / "config.json").read_text()
            ),
        )
        self.form = SimpleNamespace(menuTools=QMenu(self))
    def reset(self):
        pass

aqt.mw = main = MainWindow()
sys.path.insert(0, sys.argv[1])
import review_heatmap
assert main._review_heatmap is not None
assert main.col is None
from review_heatmap.gui.forms import options, contrib
from aqt.qt import QDialog, QIcon
for module in (options, contrib):
    dialog = QDialog()
    module.Ui_Dialog().setupUi(dialog)
assert not QIcon("review_heatmap:icons/help.svg").isNull()
print("Packaged add-on initialized successfully with no collection")
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
