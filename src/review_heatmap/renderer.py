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
Heatmap and stats elements generation
"""

import json
from enum import Enum
from hashlib import sha256
from typing import TYPE_CHECKING, Dict, List, NamedTuple, Optional, Tuple
from uuid import uuid4

from aqt.main import AnkiQt

from .activity import ActivityReport, ActivityReporter, StatsEntry, StatsType
from .config import heatmap_modes
from .libaddon.platform import PLATFORM
from .metrics import (
    activity_levels,
    activity_value,
    legacy_reference,
    migrate_activity_references,
    automatic_reference,
    baseline_color_level,
    baseline_color,
    baseline_key,
    baseline_value,
    metric_name,
    saved_reference,
)
from .web_content import (
    CSS_DISABLE_HEATMAP,
    CSS_DISABLE_STATS,
    CSS_MODE_PREFIX,
    CSS_PLATFORM_PREFIX,
    CSS_THEME_PREFIX,
    CSS_VIEW_PREFIX,
    HTML_HEATMAP,
    HTML_INFO_NODATA,
    HTML_MAIN_ELEMENT,
    HTML_STREAK,
    HTML_TODAY_PROGRESS,
)

if TYPE_CHECKING:
    from .libaddon.anki.configmanager import ConfigManager


# workaround for list comprehensions not working in class-scope
def _compress_levels(colors, indices):
    return [colors[i] for i in indices]  # type: ignore


class HeatmapView(Enum):
    deckbrowser = 0
    overview = 1
    stats = 2


class _StatsVisual(NamedTuple):
    levels: Optional[List[Tuple[int, str]]]
    unit: Optional[str]


class _RenderCache(NamedTuple):
    html: str
    arguments: Tuple[HeatmapView, Optional[int], Optional[int], bool]
    deck: int
    col_mod: int
    settings: str
    today: int


class HeatmapRenderer:

    _css_colors: Tuple[str, str, str, str, str, str, str, str, str, str, str] = (
        "rh-col0",
        "rh-col11",
        "rh-col12",
        "rh-col13",
        "rh-col14",
        "rh-col15",
        "rh-col16",
        "rh-col17",
        "rh-col18",
        "rh-col19",
        "rh-col20",
    )

    _stats_formatting: Dict[StatsType, _StatsVisual] = {
        StatsType.streak: _StatsVisual(
            levels=list(
                zip(
                    (0, 14, 30, 90, 180, 365),
                    _compress_levels(_css_colors, (0, 2, 4, 6, 9, 10)),
                )
            ),
            unit="day",
        ),
        StatsType.percentage: _StatsVisual(
            levels=list(zip((0, 25, 50, 60, 70, 80, 85, 90, 95, 99), _css_colors)),
            unit=None,
        ),
        StatsType.cards: _StatsVisual(levels=None, unit="card"),
    }

    _dynamic_legend_factors: Tuple[float, ...] = (
        0.125,
        0.25,
        0.5,
        0.75,
        1.0,
        1.25,
        1.5,
        2.0,
        4.0,
    )

    def __init__(self, mw: AnkiQt, reporter: ActivityReporter, config: "ConfigManager"):
        self._mw: AnkiQt = mw
        self._config: "ConfigManager" = config
        self._reporter: ActivityReporter = reporter
        self._render_cache: Optional[_RenderCache] = None
        # A renderer lives for one open collection, including page reloads.
        self._progress_session = uuid4().hex

    # TODO: Consider caching on the render-level

    def render(
        self,
        view: HeatmapView,
        limhist: Optional[int] = None,
        limfcst: Optional[int] = None,
        current_deck_only: bool = False,
    ) -> str:
        if self._render_cache and self._cache_still_valid(
            view, limhist, limfcst, current_deck_only
        ):
            return self._render_cache.html

        prefs = self._config["profile"]

        report = self._reporter.get_report(
            limhist=limhist, limfcst=limfcst, current_deck_only=current_deck_only
        )
        if report is None:
            return HTML_MAIN_ELEMENT.format(
                content=HTML_INFO_NODATA + self._today_progress_script(view, report),
                classes="",
            )

        count_legend = self._dynamic_legend(report.stats.activity_daily_avg.value)
        history_legend = self._activity_legend(count_legend)
        stats_legend = self._stats_legend(count_legend)
        heatmap_legend = self._heatmap_legend(history_legend, count_legend)

        classes = self._get_css_classes(view)

        if prefs["display"][view.name]:
            heatmap = self._generate_heatmap_elm(
                report, heatmap_legend, current_deck_only
            )
        else:
            heatmap = ""
            classes.append(CSS_DISABLE_HEATMAP)

        if prefs["display"][view.name] or prefs["statsvis"]:
            stats = self._generate_stats_elm(report, stats_legend)
        else:
            stats = ""
            classes.append(CSS_DISABLE_STATS)

        if not current_deck_only:
            self._save_current_perf(report)

        render = HTML_MAIN_ELEMENT.format(
            content=heatmap + stats + self._today_progress_script(view, report),
            classes=" ".join(classes),
        )

        self._render_cache = _RenderCache(
            html=render,
            arguments=(view, limhist, limfcst, current_deck_only),
            deck=self._mw.col.decks.current()["id"],
            col_mod=self._mw.col.mod,
            settings=self._settings_signature(),
            today=report.today,
        )

        return render

    def set_activity_reporter(self, reporter: ActivityReporter):
        self._reporter = reporter

    def invalidate_cache(self):
        self._render_cache = None

    def _cache_still_valid(self, view, limhist, limfcst, current_deck_only) -> bool:
        # FIXME: for 2.1.28+
        cache = self._render_cache
        if not cache:
            return False
        col_unchanged = self._mw.col.mod == cache.col_mod  # type: ignore
        return (
            col_unchanged
            and cache.settings == self._settings_signature()
            and cache.today == self._reporter._today * 1000
            and (view, limhist, limfcst, current_deck_only) == cache.arguments  # type: ignore
            and (not current_deck_only or cache.deck == self._mw.col.decks.current()["id"])
        )

    def _settings_signature(self) -> str:
        return repr((self._config["synced"], self._config["profile"],
                     self._config["local"]))

    def _activity_legend(self, count_legend: List[float]) -> List[float]:
        conf = self._config["synced"]
        metric = metric_name(conf)
        if metric == "reviews":
            return count_legend
        reference = None
        if conf.get("activity_scale") == "baseline":
            if migrate_activity_references(
                conf, lambda day: self._reporter.reference_history(day, with_durations=True),
            ):
                self._config["synced"] = conf
                self._config.save("synced", profile_unload=True)
            reference = saved_reference(conf)
            if reference is None and legacy_reference(conf) is None:
                reference = automatic_reference(
                    self._reporter.reference_history(with_durations=True), metric, conf,
                )
                if reference is not None:
                    references = conf.get("activity_baselines")
                    if not isinstance(references, dict):
                        references = {}
                    references[baseline_key(conf)] = reference
                    conf["activity_baselines"] = references
                    self._config["synced"] = conf
                    # Persist only the reference/settings. Avoid a recursive UI reset.
                    self._config.save("synced", profile_unload=True)
        return activity_levels(metric, reference)

    def _get_css_classes(self, view: HeatmapView) -> List[str]:
        conf = self._config["synced"]
        classes = [
            f"{CSS_PLATFORM_PREFIX}-{PLATFORM}",
            f"{CSS_THEME_PREFIX}-{conf['colors']}",
            f"{CSS_MODE_PREFIX}-{conf['mode']}",
            f"{CSS_VIEW_PREFIX}-{view.name}",
        ]
        if self._baseline_reference() is not None:
            classes.append("rh-baseline")
        return classes

    def _baseline_reference(self) -> Optional[Dict]:
        conf = self._config["synced"]
        if metric_name(conf) != "reviews" and conf.get("activity_scale") == "baseline":
            return saved_reference(conf)
        return None

    def _today_progress_script(
        self, view: HeatmapView, report: Optional[ActivityReport]
    ) -> str:
        if view != HeatmapView.deckbrowser:
            return ""
        return HTML_TODAY_PROGRESS.format(data=json.dumps(self._today_progress(report)))

    def _today_progress(self, report: Optional[ActivityReport]) -> Optional[Dict]:
        conf = self._config["synced"]
        if (
            not self._config["profile"].get("show_today_progress", True)
            or metric_name(conf) == "reviews"
            or conf.get("activity_scale") != "baseline"
        ):
            return None
        reference = self._baseline_reference()
        if reference is None:
            return {"percent": None, "color": "", "context": ""}
        today = report.today // 1000 if report else self._reporter._today
        # Today's forecast is negative; only completed reviews contribute.
        count = max(0, report.activity.get(today, 0)) if report else 0
        milliseconds = report.review_time.get(today, 0) if report else 0
        durations = (report.review_durations or {}).get(today) if report else None
        value = activity_value(count, milliseconds, metric_name(conf), durations, conf)
        # Do not animate between different profiles, days, filters or targets.
        context = json.dumps([
            self._progress_session, today, baseline_key(conf), reference["day"],
            baseline_value(reference), self._config["local"].get("baseline_gradient"),
        ], sort_keys=True)
        return {
            "context": sha256(context.encode("utf-8")).hexdigest(),
            "percent": 100 * value / baseline_value(reference),
            "color": baseline_color(
                value, reference, reference_day=today == int(reference["day"]),
                gradient=self._config["local"].get("baseline_gradient"),
            ),
        }

    def _generate_heatmap_elm(
        self, report: ActivityReport, dynamic_legend, current_deck_only: bool
    ) -> str:
        mode = heatmap_modes[self._config["synced"]["mode"]]
        metric = metric_name(self._config["synced"])

        # TODO: pass on "whole" to govern browser link "deck:current" addition
        options = {
            "domain": mode["domain"],
            "subdomain": mode["subDomain"],
            "range": mode["range"],
            "domLabForm": mode["domLabForm"],
            "start": report.start,
            "stop": report.stop,
            "today": report.today,
            "offset": report.offset,
            "legend": dynamic_legend,
            "whole": not current_deck_only,
            "showPaletteButton": (
                metric != "reviews"
                and self._config["synced"].get("activity_scale") == "baseline"
            ),
            "dayColors": {},
            "history": {
                day: [report.activity[day], milliseconds]
                for day, milliseconds in report.review_time.items()
            },
        }

        reference = self._baseline_reference()
        activity = {}
        for day, count in report.activity.items():
            if count <= 0 or metric == "reviews":
                activity[day] = count
                continue
            value = activity_value(
                count, report.review_time.get(day, 0), metric,
                (report.review_durations or {}).get(day), self._config["synced"],
            )
            if reference is not None:
                options["dayColors"][day] = baseline_color(
                    value, reference, reference_day=day == int(reference["day"]),
                    gradient=self._config["local"].get("baseline_gradient"),
                )
                activity[day] = baseline_color_level(
                    value, reference, reference_day=day == int(reference["day"])
                )
            else:
                # A reviewed day with zero recorded time remains visible/clickable.
                activity[day] = max(1e-9, value)

        return HTML_HEATMAP.format(
            options=json.dumps(options), data=json.dumps(activity)
        )

    def _generate_stats_elm(self, data: ActivityReport, dynamic_legend) -> str:
        dynamic_levels = self._get_dynamic_levels(dynamic_legend)
        stats_formatting = self._stats_formatting

        format_dict: Dict[str, str] = {}
        stats_entry: StatsEntry

        for name, stats_entry in data.stats._asdict().items():
            stat_format = stats_formatting[stats_entry.type]

            value = stats_entry.value
            levels = stat_format.levels

            if levels is None:
                levels = dynamic_levels

            css_class = self._css_colors[0]
            for threshold, css_class in levels:
                if value <= threshold:
                    break

            unit = stat_format.unit
            label = self._maybe_pluralize(value, unit) if unit else str(value)

            format_dict["class_" + name] = css_class
            format_dict["text_" + name] = label

        return HTML_STREAK.format(**format_dict)

    def _get_dynamic_levels(self, dynamic_legend) -> List[Tuple[int, str]]:
        return list(zip(dynamic_legend, self._css_colors))

    def _heatmap_legend(
        self, legend: List[float], forecast_legend: Optional[List[float]] = None
    ) -> List[float]:
        # Inverted negative legend for future dates. Allows us to
        # implement different color schemes for past and future without
        # having to modify cal-heatmap:
        return [-i for i in (forecast_legend or legend)[::-1]] + [0.0] + legend

    def _stats_legend(self, legend: List[float]) -> List[float]:
        return [0.0] + legend

    def _dynamic_legend(self, average: int) -> List[float]:
        # set default average if average too low for informational levels
        avg = max(20, average)
        return [fct * avg for fct in self._dynamic_legend_factors]

    @staticmethod
    def _maybe_pluralize(count: float, term: str) -> str:
        return "{} {}{}".format(str(count), term, "s" if abs(count) > 1 else "")

    def _save_current_perf(self, activity_report: ActivityReport):
        """
        Store current performance in mw object

        TODO: Make data like this available through a proper API

        Just a quick hack that allows us to assess user performance from
        other distant parts of the code / other add-ons
        """
        self._mw._hmStreakMax = activity_report.stats.streak_max.value  # type: ignore
        self._mw._hmStreakCur = activity_report.stats.streak_cur.value  # type: ignore
        self._mw._hmActivityDailyAvg = activity_report.stats.activity_daily_avg.value  # type: ignore
