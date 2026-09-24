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
    QAction, QApplication, QCheckBox, QComboBox, QDate, QDateEdit, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QTimer, QVBoxLayout, QWidget,
)

from anki.lang import _
from aqt import mw
from aqt.studydeck import StudyDeck
from aqt.utils import showInfo

from ..activity import ActivityReporter
from ..config import config, ensure_activity_defaults, heatmap_colors, heatmap_modes
from ..metrics import (
    METRICS, SCALES, baseline_key, metric_name,
    legacy_reference, metric_weights, migrate_activity_references,
    reference_from_day, saved_reference, reference_scope, automatic_reference,
    AUTO_REFERENCE_PERCENTILE,
)
from ..libaddon.gui.dialog_options import OptionsDialog
from ..libaddon.platform import PLATFORM
from ..times import daystart_epoch
from .forms import options as qtform_options
from .gradient import GradientDialog


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
        ("form.cbTodayProgress", (("value", {"dataPath": "profile/show_today_progress"}),)),
        ("spinCustomTimeWeight", (("value", {"dataPath": "synced/custom_time_weight"}),)),
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

    def __init__(self, config, mw, parent=None, reference_deck_id=None,
                 focus_reference=False, **kwargs):
        # Mediator methods defined in mapped_widgets might need access to
        # certain instance attributes. As super().__init__ calls these
        # mediator methods it is important that we set the attributes
        # beforehand:
        self.parent = parent or mw
        self.mw = mw
        self._activity_ready = False
        self._initial_reference_deck = reference_deck_id
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
        if focus_reference:
            self.form.tabWidget.setCurrentIndex(1)
            self.dateReference.setFocus()
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
            for label in [self.form.labHeading]:
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

        self.customGroup = QGroupBox("Custom workload", tab)
        custom_layout = QFormLayout(self.customGroup)
        self.spinCustomReviewWeight = QDoubleSpinBox(self.customGroup)
        self.spinCustomTimeWeight = QDoubleSpinBox(self.customGroup)
        for spin in (self.spinCustomReviewWeight, self.spinCustomTimeWeight):
            spin.setRange(0, 1)
            spin.setDecimals(2)
            spin.setSingleStep(0.05)
            spin.setKeyboardTracking(False)
        custom_layout.addRow("Review exponent", self.spinCustomReviewWeight)
        custom_layout.addRow("Time exponent", self.spinCustomTimeWeight)
        custom_layout.addRow(QLabel("Changing either exponent adjusts the other; their total is 1."))
        layout.addWidget(self.customGroup)

        self.referenceGroup = QGroupBox("Automatic baseline", tab)
        reference_layout = QVBoxLayout(self.referenceGroup)
        explanation = QLabel(
            "<b>Use a solid day of studying as your reference.</b><br> "
            "A white cell on the heatmap marks the reference day."
        )
        explanation.setWordWrap(True)
        reference_layout.addWidget(explanation)
        self.selReferenceScope = QComboBox(self.referenceGroup)
        self.selReferenceScope.addItem("Global heatmap (all included decks)", None)
        for deck in sorted(self.mw.col.decks.all(), key=lambda d: d.get("name", "").casefold()):
            self.selReferenceScope.addItem(deck.get("name", f"Deck {deck['id']}"), int(deck["id"]))
        self.selReferenceScope.setCurrentIndex(max(
            0, self.selReferenceScope.findData(self._initial_reference_deck),
        ))
        scope_layout = QFormLayout()
        scope_layout.addRow("Reference for", self.selReferenceScope)
        reference_layout.addLayout(scope_layout)
        self.labReferenceSource = QLabel(self.referenceGroup)
        self.labReferenceSource.setWordWrap(True)
        reference_layout.addWidget(self.labReferenceSource)
        self.labReference = QLabel(self.referenceGroup)
        self.labReference.setWordWrap(True)
        reference_layout.addWidget(self.labReference)
        day_layout = QFormLayout()
        self.dateReference = QDateEdit(self.referenceGroup)
        self.dateReference.setCalendarPopup(True)
        self.dateReference.setKeyboardTracking(False)
        self.dateReference.setDisplayFormat("yyyy-MM-dd")
        self.dateReference.setMaximumDate(QDate.currentDate())
        self.dateReference.setDate(QDate.currentDate().addDays(-1))
        day_layout.addRow("Reference day", self.dateReference)
        reference_layout.addLayout(day_layout)
        reference_actions = QHBoxLayout()
        self.btnUseReference = QPushButton("Use this day", self.referenceGroup)
        self.btnAutoReference = QPushButton(
            f"Choose automatically (P{AUTO_REFERENCE_PERCENTILE})", self.referenceGroup,
        )
        reference_actions.addWidget(self.btnUseReference)
        reference_actions.addWidget(self.btnAutoReference)
        reference_layout.addLayout(reference_actions)
        self.cbReferenceReminder = QCheckBox(
            "Remind me when this heatmap uses an automatic reference", self.referenceGroup,
        )
        reference_layout.addWidget(self.cbReferenceReminder)
        layout.addWidget(self.referenceGroup)
        self.btnEditGradient = QPushButton("Edit gradient colors…", tab)
        layout.addWidget(self.btnEditGradient)

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
        deck_id = self.selReferenceScope.currentData()
        metric = metric_name(conf)
        classic = metric == "reviews"
        self.selActivityScale.setEnabled(not classic)
        use_baseline = not classic and conf.get("activity_scale") == "baseline"
        self.referenceGroup.setVisible(use_baseline)
        self.form.cbTodayProgress.setEnabled(not classic)
        self.btnEditGradient.setVisible(not classic)
        self.customGroup.setVisible(metric == "custom")
        custom_review, custom_time = metric_weights("custom", conf)
        for spin, value in ((self.spinCustomReviewWeight, custom_review),
                            (self.spinCustomTimeWeight, custom_time)):
            blocked = spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(blocked)
        self._last_custom_weight = custom_time
        descriptions = {
            "reviews": "Classic uses the original review counts and average-based color scale.",
            "time": (
                "Equal review and time influence (0.5 / 0.5). Each answer earns "
                "the square root of its recorded minutes; credits are added for the day."
            ),
            "workload": (
                "Review and time exponents are 0.6 / 0.4. Each answer earns its "
                "recorded minutes raised to 0.4; credits are added for the day."
            ),
            "custom": (
                f"Review and time exponents are {custom_review:.2f} / {custom_time:.2f}. "
                "Each answer earns its recorded minutes raised to the time exponent; "
                "credits are added for the day."
            ),
            "recorded_time": "Colors use total Anki-recorded minutes, without a review-count adjustment.",
        }
        description = descriptions[metric]
        if not classic and not use_baseline:
            description += (
                " Adaptive compares each day with the median score of active days "
                "in the included history. Date and history limits apply; calendar "
                "navigation does not change the benchmark."
            )
        self.labActivityDescription.setText(description)
        migrate_activity_references(
            conf, lambda day: ActivityReporter(self.mw.col, self.getData()).reference_history(
                day, with_durations=True, deck_id=deck_id,
            ), deck_id,
        )
        reference = saved_reference(conf, deck_id)
        self.labReference.setVisible(not reference)
        self.labReferenceSource.setVisible(bool(reference))
        blocked = self.cbReferenceReminder.blockSignals(True)
        self.cbReferenceReminder.setChecked(not conf.get(
            "activity_reference_reminders_dismissed", {},
        ).get(reference_scope(deck_id), False))
        self.cbReferenceReminder.blockSignals(blocked)
        if reference:
            source = reference.get("source")
            self.labReferenceSource.setText(
                f"Automatically selected (P{reference.get('percentile', 75)}). "
                "Choose a day yourself to match your study goal."
                if source == "automatic" else
                "Selected by you." if source == "selected" else "Saved reference day."
            )
            day = datetime.fromtimestamp(int(reference["day"]), timezone.utc).date()
            self._displayReferenceDate(QDate(day.year, day.month, day.day))
        elif legacy_reference(conf, deck_id) is not None:
            previous = legacy_reference(conf, deck_id)
            try:
                day = datetime.fromtimestamp(int(previous["day"]), timezone.utc).date()
                self._displayReferenceDate(QDate(day.year, day.month, day.day))
            except (KeyError, ValueError, TypeError, OverflowError):
                pass
            self.labReference.setText(
                "The saved reference's review durations are unavailable. "
                "Choose another reference day. The old snapshot is preserved; "
                "Adaptive is used until then."
            )
        else:
            self.labReference.setText(
                f"No saved reference. Automatic P{AUTO_REFERENCE_PERCENTILE} selection "
                "needs at least 7 completed "
                "study days with recorded time in the last 60 days. Until then, "
                "Adaptive is used."
            )
        self._last_reference_date = self.dateReference.date()
        if deck_id is None:
            conf["activity_reference_date"] = self._getReferenceDate(None)

    def _onReferenceScopeChanged(self, *args):
        if self._activity_ready:
            self._displayReferenceDate(QDate.currentDate().addDays(-1))
            self._refreshActivitySettings()

    def _onReminderChanged(self, checked):
        if self._activity_ready:
            conf = self.getData()["synced"]
            conf.setdefault("activity_reference_reminders_dismissed", {})[
                reference_scope(self.selReferenceScope.currentData())
            ] = not checked

    def _onAutomaticReference(self):
        data = self.getData()
        conf = data["synced"]
        rows = ActivityReporter(self.mw.col, data).reference_history(
            with_durations=True, deck_id=self.selReferenceScope.currentData(),
        )
        reference = automatic_reference(rows, metric_name(conf), conf)
        if reference is None:
            showInfo("Automatic selection needs at least 7 completed study days "
                     "with recorded time in the last 60 days for this heatmap.", parent=self)
            return
        self._setReference(conf, reference)

    def _displayReferenceDate(self, date):
        blocked = self.dateReference.blockSignals(True)
        try:
            self.dateReference.setDate(date)
        finally:
            self.dateReference.blockSignals(blocked)

    def _onReferenceDateChanged(self, date):
        if not self._activity_ready:
            return
        data = self.getData()
        conf = data["synced"]
        day = int(datetime(date.year(), date.month(), date.day(), tzinfo=timezone.utc).timestamp())
        rows = ActivityReporter(self.mw.col, data).reference_history(
            day, with_durations=True, deck_id=self.selReferenceScope.currentData(),
        )
        reference = reference_from_day(rows[0], metric_name(conf), "selected", conf) if rows else None
        if reference is None:
            self._displayReferenceDate(self._last_reference_date)
            self._refreshActivitySettings()
            showInfo("No included reviews with recorded time for that day.", parent=self)
            return
        self._setReference(conf, reference)

    def _setReference(self, conf, reference):
        if not isinstance(conf.get("activity_baselines"), dict):
            conf["activity_baselines"] = {}
        conf["activity_baselines"][baseline_key(conf, self.selReferenceScope.currentData())] = reference
        self._refreshActivitySettings()

    def _onCustomWeightChanged(self, value, is_time):
        if not self._activity_ready:
            return
        data = self.getData()
        conf = data["synced"]
        old_conf = dict(conf, custom_time_weight=self._last_custom_weight)
        time_weight = round(value if is_time else 1 - value, 2)
        for spin, weight in ((self.spinCustomTimeWeight, time_weight),
                             (self.spinCustomReviewWeight, 1 - time_weight)):
            blocked = spin.blockSignals(True)
            spin.setValue(weight)
            spin.blockSignals(blocked)
        conf["custom_time_weight"] = time_weight
        if metric_name(conf) == "custom":
            # Preserve every scope's chosen day when the shared weights change.
            # Each snapshot is recalculated lazily when its heatmap is used.
            for index in range(self.selReferenceScope.count()):
                deck_id = self.selReferenceScope.itemData(index)
                previous = saved_reference(old_conf, deck_id) or legacy_reference(old_conf, deck_id)
                if previous and saved_reference(conf, deck_id) is None:
                    conf.setdefault("activity_baselines", {})[baseline_key(conf, deck_id)] = dict(
                        previous, needs_durations=True,
                    )
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
        self.dateReference.dateChanged.connect(self._onReferenceDateChanged)
        self.selReferenceScope.currentIndexChanged.connect(self._onReferenceScopeChanged)
        self.btnUseReference.clicked.connect(
            lambda: self._onReferenceDateChanged(self.dateReference.date()),
        )
        self.btnAutoReference.clicked.connect(self._onAutomaticReference)
        self.cbReferenceReminder.toggled.connect(self._onReminderChanged)
        self.btnEditGradient.clicked.connect(self._onEditGradient)
        self.spinCustomTimeWeight.valueChanged.connect(
            lambda value: self._onCustomWeightChanged(value, True),
        )
        self.spinCustomReviewWeight.valueChanged.connect(
            lambda value: self._onCustomWeightChanged(value, False),
        )

    # Actions:

    def _onEditGradient(self):
        GradientDialog(self.config, self).exec()
        # The options dialog keeps a tentative copy of all settings. Refresh
        # its local copy so pressing OK does not undo palette changes.
        self._data["local"] = deepcopy(self.config["local"])

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

    def _setReferenceDate(self, data_val):
        if data_val:
            return data_val
        date = QDate.currentDate().addDays(-1)
        return int(datetime(
            date.year(), date.month(), date.day(), tzinfo=timezone.utc
        ).timestamp())

    def _getReferenceDate(self, widget_val):
        date = self.dateReference.date()
        return int(datetime(
            date.year(), date.month(), date.day(), tzinfo=timezone.utc
        ).timestamp())

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


def invoke_options_dialog(parent: Optional[QWidget] = None, reference_deck_id=None,
                          focus_reference=False) -> int:
    """Call settings dialog"""
    dialog = RevHmOptions(config, mw, parent=parent, reference_deck_id=reference_deck_id,
                         focus_reference=focus_reference)
    return dialog.exec()


def initialize_options():
    # Keep Anki's Config button available for the editable gradient JSON.
    config.setConfigAction(None)
    # Set up menu entry:
    options_action = QAction("🟩 Better Review &Heatmap Options...", mw)
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
            "Review Heatmap offers Workload (linear) and Workload "
            "(review-weighted) color modes in Review Heatmap Options → Activity.\n"
            "Both use Anki's recorded review time. \n\n"
            "Please ensure that Deck Options → Timers → Maximum answer seconds has "
            "a time limit that is large enough for every card type you review.\n\n"
            "Time now affects Review Heatmap's workload estimations and time beyond that limit will "
            "not be recorded and changing it only affects future reviews' recorded time.\n\n"
            "Review Heatmap leaves this setting for you to decide.",
            parent=mw,
            title="Review Heatmap — recorded study time",
        )

    QTimer.singleShot(0, remind_about_timing)
