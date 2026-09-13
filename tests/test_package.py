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
        for filename in (
            "activity.py", "config.py", "metrics.py", "controller.py", "renderer.py", "gui/options.py",
        ):
            assert archive.read(filename) == (ROOT / "src" / "review_heatmap" / filename).read_bytes()
        for filename in (
            "__init__.py", "manifest.json", "LICENSE.txt", "LICENSES_ICONS.txt",
            "LICENSES_WEB.txt",
            "gui/forms/qt6/options.py", "gui/forms/qt6/contrib.py",
            "gui/resources/review_heatmap/icons/help.svg", "web/anki-review-heatmap.js",
            "gui/resources/review_heatmap/icons/restore.svg", "web/assets/palette.svg",
        ):
            assert filename in names
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
        assert not any(name.startswith("review_heatmap/") for name in names)
        assert archive.read("LICENSES_WEB.txt") == (
            ROOT / "src/web/_vendor/LICENSES.txt"
        ).read_bytes()


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
