# Debug

Open Anki debug console on Linux: `Ctrl+Shift+;`.

Run code: `Ctrl+Enter`.

## Recorded-time notice

Flag lives in local Anki profile preferences:

```python
mw.pm.profile["heatmap"]["time_notice_seen"]
```

Show notice after restart:

```python
mw.pm.profile["heatmap"]["time_notice_seen"] = False
```

Delete flag and let add-on restore default:

```python
mw.pm.profile.get("heatmap", {}).pop("time_notice_seen", None)
```

Deleting add-on directory does not delete profile preferences.

## Color scale

Value lives in synced collection configuration:

```python
mw.col.get_config("heatmap")["activity_scale"]
```

Valid values:

```text
adaptive
baseline
```

Default: `adaptive` (shown as Classic). Old `fixed` migrates to `adaptive`.

Collection configuration survives reinstall and syncs through Anki.

Never assign through `mw.col.conf`: it wipes Anki's undo and redo history.
See `docs/anki-undo-redo.md`.

## Gradient colors

Active palette lives in synced collection configuration since 2.3.0:

```python
mw.col.get_config("heatmap")["baseline_gradient"]
```

Palette dialog previews edits live and records each session as one Edit >
Undo step.

Shipped defaults live in `src/review_heatmap/config.json`:

```text
baseline_gradient
baseline_gradient_default
```

`baseline_gradient_default` supplies Restore Defaults palette.

`baseline_gradient` only seeds profiles: each profile copies add-on
`meta.json` `config.baseline_gradient` (or shipped value) once, when its
collection has no palette. `meta.json` keeps its copy for other profiles;
editing it afterwards has no effect.

Never add `baseline_gradient` to synced defaults: libaddon merges defaults
into stored settings after updates, which would skip the copy and drop
customized palettes.

## Snanki

Start hidden game:

```python
import importlib
import aqt

extra = importlib.import_module("1868371602.gui.extra")
extra.invoke_snanki(parent=aqt.mw)
```

Old launcher: Shift-click Contributions button. Button no longer exists.

## Devmode

Flag lives in local Anki profile preferences, outside `pm.profile["heatmap"]`:

```python
mw.pm.profile["heatmap_devmode"] = True
mw.pm.save()
```

Reveals Calibration tab next time settings open. No restart.

Turn off: press Stop Recording, then:

```python
mw.pm.profile["heatmap_devmode"] = False
mw.pm.save()
```

Skipped Stop Recording: recorder runs until profile closes, resumes if flag
returns.

Data stays in `<profile>/better_review_heatmap_calibration`. Anki backups do
not cover it. See `docs/calibration.md`.

## Build

Build Qt 5 and Qt 6 package:

```sh
python scripts/build_worktree.py
```

Output name comes from `addon.json` `repo_name` plus `package.json` version.

Run focused checks:

```sh
pytest -q tests/test_package.py tests/test_options.py tests/test_finder.py
```
