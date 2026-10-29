# -*- coding: utf-8 -*-

# Review Heatmap Add-on for Anki
#
# Copyright (C) 2016-2022  Aristotelis P. <https//glutanimate.com/>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version, with the additions
# listed at the end of the accompanied license file.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# NOTE: This program is subject to certain additional terms pursuant to
# Section 7 of the GNU Affero General Public License.  You should have
# received a copy of these additional terms immediately following the
# terms and conditions of the GNU Affero General Public License which
# accompanied this program.
#
# If not, please request a copy through one of the means of contact
# listed here: <https://glutanimate.com/contact/>.
#
# Any modifications to this file must keep this entire header intact.

"""
Handles add-on configuration
"""

from copy import deepcopy
from typing import Dict, Optional, Set

from aqt import mw

from .consts import ADDON
from .libaddon.anki.configmanager import ConfigManager
from .metrics import DEFAULT_BASELINE_GRADIENT, DEFAULT_METRIC

__all__ = ["heatmap_colors", "heatmap_modes", "config_defaults", "config"]

# NOTE: Order is important for a predictable selection dropdown.
# NOTE: Preserving the (key, dict) pair even though the dict only contains one
# key in case we need to provide additional info on each theme in the future.

heatmap_colors: Dict[str, Dict[str, str]] = {
    "lime": {"label": "Lime"},
    "olive": {"label": "Olive"},
    "magenta": {"label": "Magenta"},
    "flame": {"label": "Flame"},
}

heatmap_modes: Dict[str, Dict] = {
    "year": {
        "label": "Yearly Overview",
        "domain": "year",
        "subDomain": "day",
        "range": 1,
        "domLabForm": "%Y",
    },
    "months": {
        "label": "Continuous Timeline",
        "domain": "month",
        "subDomain": "day",
        "range": 9,
        "domLabForm": "%b '%y",
    },
}


config_defaults: Dict[str, Dict] = {
    "local": {},  # Defaults and HSL gradient points come from config.json.
    "synced": {
        "colors": "lime",
        "mode": "year",
        "limdate": 0,
        "limhist": 0,
        "limfcst": 0,
        "limcdel": False,
        "limresched": True,
        "limdecks": [],
        "activity_metric": DEFAULT_METRIC,
        "activity_scale": "adaptive",
        "activity_reference_date": 0,
        "activity_baselines": {},
        "activity_reference_reminders_dismissed": {},
        "custom_time_weight": 0.5,
        # Only manual calibration changes are stored; see metrics.calibration.
        "fsrs_calibration": {},
        "concentration_calibration": {},
        "version": ADDON.VERSION,
    },
    "profile": {
        "display": {"deckbrowser": True, "overview": True, "stats": True},
        "statsvis": True,
        "show_today_progress": True,
        "hotkeys": {},
        "time_notice_seen": False,
        # Days on which the heatmap tooltip explained the mouse buttons.
        "mouse_hint_days": [],
        "version": ADDON.VERSION,
    },
}

# The tooltip note about the mouse buttons appears on this many distinct days.
MOUSE_HINT_DAYS = 10

# Collection config key holding the synced settings; Anki can undo changes to
# it (see docs/anki-undo-redo.md).
CONF_KEY = "heatmap"

config: ConfigManager = ConfigManager(
    mw, config_dict=config_defaults, conf_key=CONF_KEY, reset_req=True
)


def ensure_activity_defaults(manager: ConfigManager) -> Set[str]:
    """Add new settings without replacing existing configuration or history.

    Older installations may carry the same version string, so this additive
    migration intentionally does not depend on the add-on version. Returns
    the names of the storages it changed.
    """
    changed_storages = set()
    for storage, keys in (
        ("synced", ("activity_metric", "activity_scale", "activity_reference_date",
                    "activity_baselines", "custom_time_weight", "fsrs_calibration",
                    "concentration_calibration",
                    "activity_reference_reminders_dismissed")),
        ("profile", ("time_notice_seen", "show_today_progress", "mouse_hint_days")),
    ):
        values = manager[storage]
        changed = False
        for key in keys:
            if key not in values:
                values[key] = deepcopy(config_defaults[storage][key])
                changed = True
        if changed:
            manager[storage] = values
            changed_storages.add(storage)

    synced = manager["synced"]
    if synced.get("colors") == "ice":
        synced["colors"] = "lime"
        manager["synced"] = synced
        changed_storages.add("synced")
    if synced.get("activity_scale") == "fixed":
        synced["activity_scale"] = "adaptive"
        manager["synced"] = synced
        changed_storages.add("synced")

    local = manager["local"]
    if local.get("baseline_gradient_version", 1) < 2:
        shipped = manager.defaults["local"]["baseline_gradient_default"]
        # baseline_gradient is the palette saved before 2.3.0, if any.
        for key in ("baseline_gradient_default", "baseline_gradient"):
            if key not in local:
                continue
            gradient = deepcopy(local[key])
            above = gradient.get("above", [])
            if len(above) == 2 and float(above[1].get("workload_ratio", 0)) == 3:
                above[1]["workload_ratio"] = 2
                above.append(deepcopy(shipped["above"][2]))
            local[key] = gradient
        local["baseline_gradient_version"] = 2
        manager["local"] = local
        changed_storages.add("local")

    # The palette moved from meta.json into the collection so that Anki can
    # undo its edits; save_activity_defaults then removes the old copy.
    # WARNING: never give baseline_gradient a synced default. libaddon merges
    # the defaults into the stored settings after every add-on update, which
    # would skip this copy and silently drop the user's palette.
    if "baseline_gradient" not in synced:
        synced["baseline_gradient"] = deepcopy(
            local.get("baseline_gradient", DEFAULT_BASELINE_GRADIENT)
        )
        manager["synced"] = synced
        changed_storages.add("synced")
    return changed_storages


def save_activity_defaults(manager: ConfigManager) -> None:
    """Add missing settings and save them at once, quietly.

    The collection must match the cache before any undo step exists: the
    reload after an undo then finds nothing to write, and a write there would
    delete the redo step the undo just created.
    """
    for storage in ensure_activity_defaults(manager):
        manager.save(storage, profile_unload=True)
    # Only once the palette is saved in the collection, drop the copy from
    # before 2.3.0, so the Config window shows no palette without effect.
    # meta.json is shared by the computer's profiles: profiles opened later
    # get the default palette (one profile per computer is assumed).
    local = manager["local"]
    if "baseline_gradient" in local and "baseline_gradient" in manager["synced"]:
        local = dict(local)
        del local["baseline_gradient"]
        manager["local"] = local
        manager.save("local", profile_unload=True)


def load_config(manager: ConfigManager, storage_name: Optional[str] = None) -> None:
    """(Re)load settings from their storages, then save missing defaults."""
    manager.load(storage_name=storage_name)
    save_activity_defaults(manager)
