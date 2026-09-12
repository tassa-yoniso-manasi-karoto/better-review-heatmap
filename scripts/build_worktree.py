"""Build the current files, including uncommitted additions, without changing Git.

Install requirements.txt and run npm ci, then:
    python scripts/build_worktree.py

Only Qt's UI compilers, the existing JavaScript bundler, and Python's standard
library are used. Both Qt 5 and Qt 6 forms are included in the resulting ZIP.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile


major = 6
    
def compile_forms(root, package):
    forms = package / "gui" / "forms"
    forms.mkdir(parents=True, exist_ok=True)
    names = []
    target = forms / f"qt{major}"
    target.mkdir(exist_ok=True)
    names = []
    for source in sorted((root / "designer").glob("*.ui")):
        generated = subprocess.check_output(
            [sys.executable, "-m", f"PyQt{major}.uic.pyuic", str(source)],
            text=True,
        )
        # Resources are ordinary files registered through QDir below.
        generated = re.sub(r"^import \w+_rc\s*$", "", generated, flags=re.MULTILINE)
        generated = generated.replace(":/review_heatmap/", "review_heatmap:")
        (target / f"{source.stem}.py").write_text(generated, encoding="utf-8")
        names.append(source.stem)
    (target / "__init__.py").write_text(
        "from . import " + ", ".join(names) + "\n", encoding="utf-8",
    )
    (forms / "__init__.py").write_text(
        "from aqt.qt import qtmajor\n\n"
        "if qtmajor >= 6:\n    from .qt6 import *\n"
        "else:\n    from .qt5 import *\n",
        encoding="utf-8",
    )


def copy_resources(root, package):
    target = package / "gui" / "resources"
    target.mkdir(parents=True, exist_ok=True)
    registrations = []
    for resource_file in sorted((root / "resources").glob("*.qrc")):
        for group in ET.parse(resource_file).getroot():
            prefix = group.attrib["prefix"].strip("/")
            for entry in group:
                relative = entry.text or ""
                source = resource_file.parent / relative
                # These optional branding files are absent from the checkout.
                if not source.exists() and relative.startswith("icons/optional/"):
                    continue
                destination = target / prefix / entry.attrib.get("alias", relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            registrations.append(
                f"QDir.addSearchPath({prefix!r}, str(Path(__file__).parent / {prefix!r}))"
            )
    (target / "__init__.py").write_text(
        "from pathlib import Path\nfrom aqt.qt import QDir\n\n"
        + "\n".join(registrations) + "\n", encoding="utf-8",
    )


def main():
    root = Path(__file__).resolve().parents[1]
    # Check both compilers before touching any previous build.
    subprocess.run(
        [sys.executable, "-m", f"PyQt{major}.uic.pyuic", "--version"], check=True,
    )
    metadata = json.loads((root / "addon.json").read_text(encoding="utf-8"))
    version = json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
    dist = root / "build" / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)

    package = dist / "src" / metadata["module_name"]
    shutil.copytree(
        root / "src" / metadata["module_name"], package,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "meta.json", "user_files", "debug", "log.txt"),
    )
    # Discard any generated UI left in the source tree by earlier builds.
    for name in ("forms", "resources"):
        generated = package / "gui" / name
        if generated.exists():
            shutil.rmtree(generated)
    copy_resources(root, package)
    compile_forms(root, package)
    shutil.copy2(root / "LICENSE", package / "LICENSE.txt")
    shutil.copy2(root / "resources" / "LICENSES_ICONS.md", package / "LICENSES_ICONS.txt")
    version_file = package / "_version.py"
    version_file.write_text(
        re.sub(r'^__version__ = .+$', f'__version__ = "{version}+workload"',
               version_file.read_text(encoding="utf-8"), flags=re.MULTILINE),
        encoding="utf-8",
    )
    manifest = {
        "package": metadata["module_name"],
        "name": metadata["display_name"] + " (workload preview)",
        "mod": int(time.time()),
        "conflicts": list(dict.fromkeys(metadata["conflicts"] + [metadata["ankiweb_id"]])),
    }
    (package / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    environment = dict(os.environ, ANKI_REVIEW_HEATMAP_OUTFILE=str(package / "web" / "anki-review-heatmap.js"))
    subprocess.run(["npm", "run", "build"], cwd=root, env=environment, check=True)
    destination = root / "build" / "review-heatmap-workload-preview.ankiaddon"
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for source in sorted(package.rglob("*")):
            if source.is_file():
                archive.write(source, source.relative_to(package).as_posix())
    print(f"Built {destination}")


if __name__ == "__main__":
    main()
