"""Record user changes to the synced settings as steps on Anki's undo stack.

Read docs/anki-undo-redo.md before changing this module or making another
setting undoable.

See LICENSE for the add-on's license and additional terms.
"""

import json
from copy import deepcopy
from typing import Callable, Optional

# Undo step labels; Anki's Edit menu shows "Undo <label>" / "Redo <label>".
GRADIENT = "Change Heatmap Gradient"
MEASURE = "Change Heatmap Measure"
COLOR_SCALE = "Change Heatmap Color Scale"
REFERENCE_DAY = "Set Heatmap Reference Day"
AUTOMATIC_REFERENCE = "Use Automatic Heatmap Reference Day"
CALIBRATION = "Calibrate Heatmap Measure"
WORKLOAD_WEIGHTS = "Change Heatmap Workload Weights"
COLOR_SCHEME = "Change Heatmap Color Scheme"
CALENDAR_MODE = "Change Heatmap Calendar Mode"
EXCLUSIONS = "Change Heatmap Exclusions"
DISMISS_REMINDER = "Dismiss Heatmap Reference Reminder"
OPTIONS = "Change Heatmap Options"

# Synced keys the user edits and the step each one names. Keys missing here
# (activity_baselines, the unread legacy activity_reference_date, version)
# only hold data derived from these, so a diff made of them alone is no edit.
_KEY_LABELS = {
    "activity_metric": MEASURE,
    "activity_scale": COLOR_SCALE,
    "fsrs_calibration": CALIBRATION,
    "concentration_calibration": CALIBRATION,
    "custom_time_weight": WORKLOAD_WEIGHTS,
    "colors": COLOR_SCHEME,
    "mode": CALENDAR_MODE,
    "limdate": EXCLUSIONS,
    "limhist": EXCLUSIONS,
    "limfcst": EXCLUSIONS,
    "limcdel": EXCLUSIONS,
    "limresched": EXCLUSIONS,
    "limdecks": EXCLUSIONS,
    "baseline_gradient": GRADIENT,
    "activity_reference_reminders_dismissed": DISMISS_REMINDER,
}

_REFERENCE_LABELS = {"selected": REFERENCE_DAY, "automatic": AUTOMATIC_REFERENCE}

_MISSING = object()


def same(first, second) -> bool:
    """Whether Anki stores both settings values identically."""
    return json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def commit_synced_change(
    config,
    label: str,
    change: Callable[[dict], dict],
    before: Optional[dict] = None,
    refresh: bool = True,
) -> bool:
    """Apply a user's change to the synced settings as one Edit > Undo step.

    WARNING: write user changes to the synced settings only through here. The
    change builds on the collection's current value, so a stale cache cannot
    revert an earlier undo, and nothing is recorded when nothing changes
    (Anki would show and keep an empty step).

    change -- returns the new settings, given a copy of the current ones
    before -- values the step records as its starting point instead of the
              stored ones, e.g. a live-previewed dialog's opening values
    refresh -- end with mw.reset() (redraw and Edit menu); False only
               refreshes the Edit menu, so the caller redraws what it needs

    Returns whether a step was recorded.
    """
    config.load(storage_name="synced")
    current = config["synced"]
    start = dict(current, **deepcopy(before)) if before else current
    new = change(deepcopy(start))
    if same(new, start):
        return False
    if not same(start, current):
        # Live previews already stored newer values; restore the starting
        # point quietly so that undoing the step returns to it.
        config["synced"] = start
        config.save("synced", profile_unload=True)
    config["synced"] = new
    config.save("synced", profile_unload=not refresh, undo=label)
    return True


def three_way_patch(stored: dict, original: dict, edited: dict) -> dict:
    """Apply the user's edits (original -> edited) onto the stored settings.

    Top-level keys and the entries of dict values (references, calibrations,
    dismissed reminders) are patched one by one, so values saved meanwhile
    elsewhere survive; deeper values are replaced whole. Python equality
    decides what changed, so normalization noise such as 30 vs 30.0 is not
    an edit.
    """
    patched = deepcopy(stored)
    for key in set(original) | set(edited):
        if key not in edited:
            patched.pop(key, None)
            continue
        old, new = original.get(key, _MISSING), edited[key]
        if old == new:
            continue
        target = patched.get(key)
        if isinstance(old, dict) and isinstance(new, dict) and isinstance(target, dict):
            for entry in set(old) | set(new):
                if entry not in new:
                    target.pop(entry, None)
                elif old.get(entry, _MISSING) != new[entry]:
                    target[entry] = deepcopy(new[entry])
        else:
            patched[key] = deepcopy(new)
    return patched


def describe_change(
    original: dict, edited: dict, reference_source: Optional[str] = None
) -> Optional[str]:
    """Label for an Options edit of the synced settings; None if it holds none.

    reference_source is the source of the reference day the user picked
    ("selected") or recalculated ("automatic") in the dialog. Reference data
    alone cannot tell: migrations keep a picked day's "selected" source.
    """
    labels = {
        label for key, label in _KEY_LABELS.items()
        if original.get(key, _MISSING) != edited.get(key, _MISSING)
    }
    if reference_source in _REFERENCE_LABELS:
        labels.add(_REFERENCE_LABELS[reference_source])
    if len(labels) > 1:
        return OPTIONS
    return labels.pop() if labels else None
