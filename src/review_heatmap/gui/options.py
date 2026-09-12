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
Options dialog and associated components
"""

import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Optional

from aqt.qt import (
    QAction, QApplication, QComboBox, QDate, QDateEdit, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QTimer, QVBoxLayout, QWidget,
)

from anki.lang import _
from aqt import mw
from aqt.studydeck import StudyDeck
from aqt.utils import showInfo

from ..activity import ActivityReporter
from ..config import config, ensure_activity_defaults, heatmap_colors, heatmap_modes
from ..metrics import (
    METRICS, SCALES, automatic_reference, baseline_key, metric_name,
    reference_from_day, saved_reference,
)
from ..libaddon.gui.dialog_options import OptionsDialog
from ..libaddon.platform import PLATFORM
from ..times import daystart_epoch
from .forms import options as qtform_options


class RevHmOptions(OptionsDialog):

    """
    Add-on-specific options dialog implementation
    """

    _mapped_widgets = (
        (
            "selActivityMetric",
            (
                ("items", {"setter": "_setActivityMetricItems"}),
                ("value", {"dataPath": "synced/activity_metric"}),
            ),
        ),
        (
            "selActivityScale",
            (
                ("items", {"setter": "_setActivityScaleItems"}),
                ("value", {"dataPath": "synced/activity_scale"}),
            ),
        ),
        (
            "form.selHmColor",
            (
                # order is important (e.g. to set-up items before current item)
                ("items", {"setter": "_setSelHmColorItems"}),
                ("value", {"dataPath": "synced/colors"}),
            ),
        ),
        (
            "form.selHmCalMode",
            (
                ("items", {"setter": "_setSelHmCalModeItems"}),
                ("value", {"dataPath": "synced/mode"}),
            ),
        ),
        ("form.cbHmMain", (("value", {"dataPath": "profile/display/deckbrowser"}),)),
        ("form.cbHmDeck", (("value", {"dataPath": "profile/display/overview"}),)),
        ("form.cbHmStats", (("value", {"dataPath": "profile/display/stats"}),)),
        ("form.cbStreakAll", (("value", {"dataPath": "profile/statsvis"}),)),
        ("form.spinLimHist", (("value", {"dataPath": "synced/limhist"}),)),
        ("form.spinLimFcst", (("value", {"dataPath": "synced/limfcst"}),)),
        (
            "form.dateLimData",
            (
                ("value", {"dataPath": "synced/limdate", "getter": "_getDateLimData"}),
                ("min", {"setter": "_setDateLimDataMin"}),
                ("max", {"setter": "_setDateLimDataMax"}),
            ),
        ),
        ("form.cbLimDel", (("value", {"dataPath": "synced/limcdel"}),)),
        ("form.cbLimResched", (("value", {"dataPath": "synced/limresched"}),)),
        (
            "form.listDecks",
            (
                (
                    "value",
                    {"dataPath": "synced/limdecks", "setter": "_setListDecksValue"},
                ),
            ),
        ),
    )

    def __init__(self, config, mw, parent=None, **kwargs):
        # Mediator methods defined in mapped_widgets might need access to
        # certain instance attributes. As super().__init__ calls these
        # mediator methods it is important that we set the attributes
        # beforehand:
        self.parent = parent or mw
        self.mw = mw
        self._activity_ready = False
        ensure_activity_defaults(config)
        super(RevHmOptions, self).__init__(
            self._mapped_widgets,
            config,
            form_module=qtform_options,
            parent=self.parent,
            **kwargs
        )
        # Reference selection and recalibration are tentative until OK is used.
        self._data = deepcopy(self._data)
        self._activity_ready = True
        self._refreshActivitySettings()
        # Instance methods that modify the initialized UI should either be
        # called from self._setupUI or from here

    # UI adjustments

    def _setupUI(self):
        super(RevHmOptions, self)._setupUI()
        self._setupActivityTab()

        # manually adjust title label font sizes on Windows
        # gap between default windows font sizes and sizes that work well
        # on Linux and macOS is simply too big
        # TODO: find a better solution
        if PLATFORM == "win":
            default_size = QApplication.font().pointSize()
            for label in [self.form.fmtLabContrib, self.form.labHeading]:
                font = label.font()
                font.setPointSize(int(default_size * 1.5))
                label.setFont(font)

    def _setupActivityTab(self):
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        choices = QFormLayout()
        self.selActivityMetric = QComboBox(tab)
        self.selActivityScale = QComboBox(tab)
        choices.addRow("Color by", self.selActivityMetric)
        choices.addRow("Color scale", self.selActivityScale)
        layout.addLayout(choices)

        self.labActivityDescription = QLabel(tab)
        self.labActivityDescription.setWordWrap(True)
        layout.addWidget(self.labActivityDescription)

        self.referenceGroup = QGroupBox("Automatic baseline", tab)
        reference_layout = QVBoxLayout(self.referenceGroup)
        explanation = QLabel(
            "Choose a strong day automatically from the last 60 days, or select "
            "a reference day below. The reference stays fixed until you "
            "recalculate it. It uses all included decks, so colors are "
            "comparable across views.",
            self.referenceGroup,
        )
        explanation.setWordWrap(True)
        reference_layout.addWidget(explanation)
        self.labReference = QLabel(self.referenceGroup)
        self.labReference.setWordWrap(True)
        reference_layout.addWidget(self.labReference)
        self.btnRecalculate = QPushButton("Recalculate from recent activity", self.referenceGroup)
        reference_layout.addWidget(self.btnRecalculate)

        day_layout = QHBoxLayout()
        self.dateReference = QDateEdit(self.referenceGroup)
        self.dateReference.setCalendarPopup(True)
        self.dateReference.setDisplayFormat("yyyy-MM-dd")
        self.dateReference.setMaximumDate(QDate.currentDate())
        self.dateReference.setDate(QDate.currentDate().addDays(-1))
        self.btnUseReferenceDay = QPushButton("Use this day as reference", self.referenceGroup)
        day_layout.addWidget(self.dateReference)
        day_layout.addWidget(self.btnUseReferenceDay)
        reference_layout.addLayout(day_layout)
        layout.addWidget(self.referenceGroup)

        timing = QLabel(
            "Hover over a past day to see recorded study time and reviews. "
            "Anki limits recorded time using Deck Options → Timers → Maximum "
            "answer seconds. Choose a limit appropriate for your study habits. "
            "Streaks count any day with reviews. Future days show cards due.", tab,
        )
        timing.setWordWrap(True)
        layout.addWidget(timing)
        layout.addStretch()
        self.form.tabWidget.insertTab(1, tab, "Activity")

    def _refreshActivitySettings(self, *args):
        if not self._activity_ready:
            return
        conf = self.getData()["synced"]
        metric = metric_name(conf)
        classic = metric == "reviews"
        self.selActivityScale.setEnabled(not classic)
        use_baseline = not classic and conf.get("activity_scale") == "baseline"
        self.referenceGroup.setVisible(use_baseline)
        descriptions = {
            "reviews": "Classic uses the original review counts and average-based color scale.",
            "time": "Study time colors days by Anki's recorded review duration.",
            "workload": (
                "Workload balances review count and recorded time. At the same "
                "duration, more reviews produce a stronger shade. Long reading "
                "sessions and interruptions have less influence than in Study time."
            ),
        }
        description = descriptions[metric]
        if not classic and not use_baseline:
            description += " Fixed scale uses stable thresholds, independent of your history."
        self.labActivityDescription.setText(description)
        reference = saved_reference(conf)
        if reference:
            day = datetime.fromtimestamp(int(reference["day"]), timezone.utc).date()
            source = "Selected" if reference.get("source") == "selected" else "Automatic"
            self.labReference.setText(f"{source} reference: {day}. Saved when you press OK.")
        else:
            self.labReference.setText(
                "No saved reference. Automatic selection needs at least 7 completed "
                "study days with recorded time in the last 60 days. Until then, "
                "the fixed scale is used."
            )

    def _onRecalculateReference(self):
        data = self.getData()
        conf = data["synced"]
        rows = ActivityReporter(self.mw.col, data).reference_history()
        reference = automatic_reference(rows, metric_name(conf))
        if reference is None:
            showInfo("Not enough recent study days with recorded time to choose a reference.", parent=self)
            return
        self._setReference(conf, reference)

    def _onUseReferenceDay(self):
        data = self.getData()
        conf = data["synced"]
        date = self.dateReference.date()
        day = int(datetime(date.year(), date.month(), date.day(), tzinfo=timezone.utc).timestamp())
        rows = ActivityReporter(self.mw.col, data).reference_history(day)
        reference = reference_from_day(rows[0], metric_name(conf), "selected") if rows else None
        if reference is None:
            showInfo("No included reviews with recorded time for that day. Check the date and history filters.", parent=self)
            return
        self._setReference(conf, reference)

    def _setReference(self, conf, reference):
        if not isinstance(conf.get("activity_baselines"), dict):
            conf["activity_baselines"] = {}
        conf["activity_baselines"][baseline_key(conf)] = reference
        self._refreshActivitySettings()

    def _onAccept(self):
        for storage, values in self.getData().items():
            self.config[storage] = values
        self.config.save()

    def restoreData(self):
        super().restoreData()
        self._refreshActivitySettings()

    # Events:

    def _setupEvents(self):
        super(RevHmOptions, self)._setupEvents()
        self.form.btnDeckAdd.clicked.connect(self._onAddIgnoredDeck)
        self.form.btnDeckDel.clicked.connect(self._onDeleteIgnoredDeck)
        self.selActivityMetric.currentIndexChanged.connect(self._refreshActivitySettings)
        self.selActivityScale.currentIndexChanged.connect(self._refreshActivitySettings)
        self.form.tabWidget.currentChanged.connect(self._refreshActivitySettings)
        self.btnRecalculate.clicked.connect(self._onRecalculateReference)
        self.btnUseReferenceDay.clicked.connect(self._onUseReferenceDay)

    # Actions:

    # Deck list buttons
    # TODO: Migrate to custom widget

    def _onAddIgnoredDeck(self):
        list_widget = self.form.listDecks
        ret = StudyDeck(
            self.mw,
            accept=_("Choose"),
            title=_("Choose Deck"),
            help="",
            parent=self,
            geomKey="selectDeck",
        )
        deck_name = ret.name
        if not deck_name:
            return False
        deck_id = self.mw.col.decks.id(deck_name)

        item_tuple = (deck_name, deck_id)

        if not self.interface.setCurrentByData(list_widget, deck_id):
            self.interface.addValueAndMakeCurrent(list_widget, item_tuple)

    def _onDeleteIgnoredDeck(self):
        list_widget = self.form.listDecks
        self.interface.removeSelected(list_widget)

    # Helpers:

    def _getComboItems(self, dct):
        return list((val["label"], key) for key, val in dct.items())

    # Config setters:

    def _setDateLimDataMin(self, data_val):
        return self.mw.col.crt

    def _setDateLimDataMax(self, data_val):
        return int(round(time.time()))

    def _setSelHmColorItems(self, data_val):
        return self._getComboItems(heatmap_colors)

    def _setSelHmCalModeItems(self, data_val):
        return self._getComboItems(heatmap_modes)

    def _setActivityMetricItems(self, data_val):
        return self._getComboItems(METRICS)

    def _setActivityScaleItems(self, data_val):
        return self._getComboItems(SCALES)

    def _setListDecksValue(self, dids):
        item_tuples = []
        for did in dids:
            try:
                name = self.mw.col.decks.name_if_exists(did)
            except AttributeError:
                name = self.mw.col.decks.nameOrNone(did)
            if not name:
                continue
            item_tuples.append((name, did))
        return item_tuples

    # Config getters:

    def _getDateLimData(self, widget_val):
        db = self.mw.col.db
        val = daystart_epoch(db, widget_val)
        default = daystart_epoch(db, self._setDateLimDataMin(None))
        if val == default:
            return 0
        return widget_val


def invoke_options_dialog(parent: Optional[QWidget] = None) -> int:
    """Call settings dialog"""
    dialog = RevHmOptions(config, mw, parent=parent)
    return dialog.exec()


def initialize_options():
    config.setConfigAction(invoke_options_dialog)
    # Set up menu entry:
    options_action = QAction("Review &Heatmap Options...", mw)
    options_action.triggered.connect(lambda _: invoke_options_dialog())
    mw.form.menuTools.addAction(options_action)

    from aqt.gui_hooks import profile_did_open

    profile_did_open.append(_on_profile_open)


def _on_profile_open():
    config.load()
    ensure_activity_defaults(config)
    collection = mw.col
    if config["profile"]["time_notice_seen"]:
        return

    def remind_about_timing():
        if not collection or mw.col is not collection:
            return
        profile = config["profile"]
        if profile["time_notice_seen"]:
            return
        profile["time_notice_seen"] = True
        config["profile"] = profile
        config.save("profile", profile_unload=True)
        showInfo(
            "Review Heatmap offers Study time and Workload color modes in "
            "Review Heatmap Options → Activity.\n\n"
            "Both use Anki's recorded review time. Please check Deck Options → "
            "Timers → Maximum answer seconds and choose a limit that suits "
            "your cards. Time beyond that limit is not recorded. Changing it "
            "affects future reviews.\n\n"
            "Review Heatmap leaves this setting under your control.",
            parent=mw,
            title="Review Heatmap — recorded study time",
        )

    QTimer.singleShot(0, remind_about_timing)
