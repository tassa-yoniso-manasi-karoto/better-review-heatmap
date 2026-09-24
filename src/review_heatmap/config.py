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
from typing import Dict

from aqt import mw

from .consts import ADDON
from .libaddon.anki.configmanager import ConfigManager

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
        "activity_metric": "workload",
        "activity_scale": "adaptive",
        "activity_reference_date": 0,
        "activity_baselines": {},
        "activity_reference_reminders_dismissed": {},
        "custom_time_weight": 0.5,
        "version": ADDON.VERSION,
    },
    "profile": {
        "display": {"deckbrowser": True, "overview": True, "stats": True},
        "statsvis": True,
        "show_today_progress": True,
        "hotkeys": {},
        "time_notice_seen": False,
        "version": ADDON.VERSION,
    },
}

config: ConfigManager = ConfigManager(
    mw, config_dict=config_defaults, conf_key="heatmap", reset_req=True
)


def ensure_activity_defaults(manager: ConfigManager) -> None:
    """Add new settings without replacing existing configuration or history.

    Older installations may carry the same version string, so this additive
    migration intentionally does not depend on the add-on version.
    """
    for storage, keys in (
        ("synced", ("activity_metric", "activity_scale", "activity_reference_date",
                    "activity_baselines", "custom_time_weight",
                    "activity_reference_reminders_dismissed")),
        ("profile", ("time_notice_seen", "show_today_progress")),
    ):
        values = manager[storage]
        changed = False
        for key in keys:
            if key not in values:
                values[key] = deepcopy(config_defaults[storage][key])
                changed = True
        if changed:
            manager[storage] = values

    synced = manager["synced"]
    if synced.get("colors") == "ice":
        synced["colors"] = "lime"
        manager["synced"] = synced
    if synced.get("activity_scale") == "fixed":
        synced["activity_scale"] = "adaptive"
        manager["synced"] = synced

    local = manager["local"]
    if local.get("baseline_gradient_version", 1) < 2:
        defaults = manager.defaults["local"]
        for key in ("baseline_gradient_default", "baseline_gradient"):
            gradient = deepcopy(local.get(key, defaults[key]))
            above = gradient.get("above", [])
            if len(above) == 2 and float(above[1].get("workload_ratio", 0)) == 3:
                above[1]["workload_ratio"] = 2
                above.append(deepcopy(defaults[key]["above"][2]))
            local[key] = gradient
        local["baseline_gradient_version"] = 2
        manager["local"] = local
