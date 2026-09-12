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
Overarching control of heatmap rendering and state
"""

from typing import TYPE_CHECKING, Optional

from aqt.main import AnkiQt

from .activity import ActivityReporter
from .renderer import HeatmapRenderer, HeatmapView
from .web_bridge import HeatmapBridge
from .errors import CollectionError
from .config import ensure_activity_defaults

if TYPE_CHECKING:
    from .libaddon.anki.configmanager import ConfigManager


class HeatmapController:
    def __init__(self, mw: AnkiQt, config: "ConfigManager"):
        self._mw = mw
        self._config: ConfigManager = config

        self._bridge: Optional[HeatmapBridge] = HeatmapBridge(self._mw, self._config)
        self._bridge.register()

        self._renderer: Optional[HeatmapRenderer] = None
        self._collection = None

        from anki.hooks import addHook
        from aqt.gui_hooks import profile_will_close, reviewer_did_answer_card, sync_did_finish

        addHook("reset", self.invalidate_cache)
        reviewer_did_answer_card.append(self.invalidate_cache)
        sync_did_finish.append(self.invalidate_cache)
        profile_will_close.append(self.clear_collection)

    def invalidate_cache(self, *args):
        if self._renderer:
            self._renderer.invalidate_cache()

    def clear_collection(self):
        self._renderer = None
        self._collection = None

    def render_for_view(
        self,
        view: HeatmapView,
        limhist: Optional[int] = None,
        limfcst: Optional[int] = None,
        current_deck_only: bool = False,
    ) -> str:
        col = self._mw.col
        if not col:
            raise CollectionError("Anki collection and/or database is not ready")

        if col is not self._collection:
            # Profile switches and collection replacement must not reuse another
            # collection's reporter, settings, or rendered heatmap.
            self.clear_collection()
            self._config.load()
            ensure_activity_defaults(self._config)
            self._collection = col

        if not self._renderer:
            reporter = ActivityReporter(col, self._config)
            self._renderer = HeatmapRenderer(self._mw, reporter, self._config)

        return self._renderer.render(view, limhist, limfcst, current_deck_only)


def initialize_controller(mw: "AnkiQt", config: "ConfigManager") -> HeatmapController:
    controller = HeatmapController(mw, config)
    mw._review_heatmap = controller  # type: ignore
    return controller
