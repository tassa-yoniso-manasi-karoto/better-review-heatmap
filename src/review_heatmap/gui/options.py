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
    QAction, QApplication, QComboBox, QDate, QDateEdit, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QSize, QTimer, QToolButton, QVBoxLayout, QWidget,
)

from anki.lang import _
from aqt import mw
from aqt.studydeck import StudyDeck
from aqt.theme import theme_manager
from aqt.utils import showInfo

from ..activity import ActivityReporter
from ..config import config, ensure_activity_defaults, heatmap_colors, heatmap_modes
from ..metrics import (
    METRICS, SCALES, baseline_key, metric_name,
    legacy_reference, metric_weights, migrate_activity_references,
    reference_from_day, saved_reference, set_reference, automatic_reference,
    AUTO_REFERENCE_PERCENTILE, AUTO_REFERENCE_REFRESH_DAYS,
)
from ..libaddon.gui.dialog_options import OptionsDialog
from ..libaddon.platform import PLATFORM
from ..times import daystart_epoch
from .forms import options as qtform_options
from .gradient import GradientDialog


class CustomWeightsDialog(QDialog):
    def __init__(self, conf, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Custom workload weights")
        layout = QVBoxLayout(self)
        presets = ["Preset weights (reviews / time):"]
        for metric in ("time", "workload"):
            review, time_weight = metric_weights(metric)
            presets.append(f"{METRICS[metric]['label']}: {review:g} / {time_weight:g}")
        layout.addWidget(QLabel("\n".join(presets), self))
        form = QFormLayout()
        self.reviewWeight = QDoubleSpinBox(self)
        self.timeWeight = QDoubleSpinBox(self)
        for spin, value in zip(
            (self.reviewWeight, self.timeWeight), metric_weights("custom", conf),
        ):
            spin.setRange(0, 1)
            spin.setDecimals(2)
            spin.setSingleStep(0.05)
            spin.setKeyboardTracking(False)
            spin.setValue(value)
        form.addRow("Review exponent", self.reviewWeight)
        form.addRow("Time exponent", self.timeWeight)
        layout.addLayout(form)
        layout.addWidget(QLabel(
            "Changing either exponent adjusts the other; their total is 1.", self,
        ))
        self.reviewWeight.valueChanged.connect(
            lambda value: self._adjustOther(self.timeWeight, value),
        )
        self.timeWeight.valueChanged.connect(
            lambda value: self._adjustOther(self.reviewWeight, value),
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _adjustOther(spin, value):
        blocked = spin.blockSignals(True)
        spin.setValue(round(1 - value, 2))
        spin.blockSignals(blocked)


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
        self._setupChangelogTab()

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
        metric_row = QHBoxLayout()
        metric_row.addWidget(self.selActivityMetric, 1)
        self.btnCustomWeights = QPushButton("Edit weights…", tab)
        metric_row.addWidget(self.btnCustomWeights)
        choices.addRow("Color by", metric_row)
        choices.addRow("Color scale", self.selActivityScale)
        self.form.gridLayout.removeWidget(self.form.label)
        self.form.gridLayout.removeWidget(self.form.selHmColor)
        choices.addRow(self.form.label, self.form.selHmColor)
        layout.addLayout(choices)

        self.referenceGroup = QGroupBox(SCALES["baseline"]["label"], tab)
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
        self.btnAutoReference = QToolButton(self.referenceGroup)
        self.btnAutoReference.setAutoRaise(True)
        self.btnAutoReference.setIcon(theme_manager.icon_from_resources(
            "review_heatmap:icons/auto-reference.svg",
        ))
        self.btnAutoReference.setIconSize(QSize(20, 20))
        self.btnAutoReference.setFixedSize(28, 28)
        self.btnAutoReference.setAccessibleName("Recalculate automatic reference")
        date_row = QHBoxLayout()
        date_row.setSpacing(8)
        date_row.addWidget(self.dateReference, 1)
        date_row.addWidget(self.btnAutoReference)
        day_layout.addRow("Reference day", date_row)
        reference_layout.addLayout(day_layout)
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
        self.form.tabWidget.insertTab(1, tab, "Display Mode")


    def _setupChangelogTab(self):
        from aqt.qt import QVBoxLayout, QTextBrowser
        import os
        
        layout = QVBoxLayout(self.form.tabChangelog)
        layout.setContentsMargins(0, 0, 0, 0)
        self.changelogBrowser = QTextBrowser(self.form.tabChangelog)
        self.changelogBrowser.setOpenExternalLinks(True)
        
        # Apply custom styling to remove li padding and add section padding
        self.changelogBrowser.document().setDefaultStyleSheet(
            "ul, ol { margin-top: 4px; margin-bottom: 4px; padding-top: 0px; padding-bottom: 0px; } "
            "li { margin-top: 0px; margin-bottom: 0px; padding-top: 0px; padding-bottom: 0px; } "
            "h2 { margin-top: 16px; margin-bottom: 8px; } "
            "h3 { margin-top: 12px; margin-bottom: 4px; }"
        )
        
        layout.addWidget(self.changelogBrowser)
        
        changelog_path = os.path.join(os.path.dirname(__file__), "..", "CHANGELOG.md")
        if os.path.exists(changelog_path):
            with open(changelog_path, "r", encoding="utf-8") as f:
                md_text = f.read()
            
            # Trim header comments
            if "-->" in md_text:
                md_text = md_text.split("-->", 1)[-1].strip()
            
            if hasattr(self.changelogBrowser, "setMarkdown"):
                self.changelogBrowser.setMarkdown(md_text)
            else:
                try:
                    import markdown
                    html = markdown.markdown(md_text)
                    self.changelogBrowser.setHtml(html)
                except ImportError:
                    self.changelogBrowser.setPlainText(md_text)

    def _refreshActivitySettings(self, *args):
        if not self._activity_ready:
            return
        conf = self.getData()["synced"]
        deck_id = self.selReferenceScope.currentData()
        metric = metric_name(conf)
        classic = metric == "reviews"
        use_baseline = not classic and conf.get("activity_scale") == "baseline"
        show_color_scheme = not use_baseline
        self.form.label.setVisible(show_color_scheme)
        self.form.selHmColor.setVisible(show_color_scheme)
        self.selActivityScale.setEnabled(not classic)
        self.referenceGroup.setVisible(use_baseline)
        self.form.cbTodayProgress.setEnabled(use_baseline)
        self.btnEditGradient.setVisible(use_baseline)
        self.btnCustomWeights.setVisible(metric == "custom")
        migrate_activity_references(
            conf, lambda day: ActivityReporter(self.mw.col, self.getData()).reference_history(
                day, with_durations=True, deck_id=deck_id,
            ), deck_id,
        )
        reference = saved_reference(conf, deck_id)
        self.labReference.setVisible(not reference)
        tooltip = (
            f"Recalculate automatic reference (P{AUTO_REFERENCE_PERCENTILE}).\n"
            f"Automatic references refresh every {AUTO_REFERENCE_REFRESH_DAYS} days."
        )
        if reference and reference.get("source") == "automatic" and reference.get("selected_on"):
            selected_on = datetime.fromtimestamp(reference["selected_on"], timezone.utc).date()
            tooltip += f"\nLast calculated: {selected_on}."
        self.btnAutoReference.setToolTip(tooltip)
        if reference:
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
                "Classic is used until then."
            )
        else:
            self.labReference.setText(
                f"No saved reference. Automatic P{AUTO_REFERENCE_PERCENTILE} selection "
                "needs at least 7 completed "
                "study days with recorded time in the last 60 days. Until then, "
                "Classic is used."
            )
        self._last_reference_date = self.dateReference.date()
        if deck_id is None:
            conf["activity_reference_date"] = self._getReferenceDate(None)

    def _onReferenceScopeChanged(self, *args):
        if self._activity_ready:
            self._displayReferenceDate(QDate.currentDate().addDays(-1))
            self._refreshActivitySettings()

    def _onAutomaticReference(self):
        data = self.getData()
        conf = data["synced"]
        reporter = ActivityReporter(self.mw.col, data)
        rows = reporter.reference_history(
            with_durations=True, deck_id=self.selReferenceScope.currentData(),
        )
        reference = automatic_reference(rows, metric_name(conf), conf, today=reporter._today)
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
        previous = saved_reference(conf, self.selReferenceScope.currentData())
        if previous and previous.get("source") == "selected" and previous["day"] == day:
            return
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

    def _onCalendarDateSelected(self, date):
        # dateChanged may already have rejected an empty day and restored the
        # picker; do not process that same invalid click a second time.
        if date == self.dateReference.date():
            self._onReferenceDateChanged(date)

    def _setReference(self, conf, reference):
        set_reference(conf, reference, self.selReferenceScope.currentData())
        self._refreshActivitySettings()

    def _onEditCustomWeights(self):
        conf = self.getData()["synced"]
        dialog = CustomWeightsDialog(conf, self)
        result = dialog.exec()
        time_weight = round(dialog.timeWeight.value(), 2)
        dialog.deleteLater()
        if result != QDialog.DialogCode.Accepted:
            return
        old_conf = dict(conf)
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
        # Clicking the already displayed automatic date also makes it manual.
        self.dateReference.calendarWidget().clicked.connect(self._onCalendarDateSelected)
        self.dateReference.calendarWidget().activated.connect(self._onCalendarDateSelected)
        self.selReferenceScope.currentIndexChanged.connect(self._onReferenceScopeChanged)
        self.btnAutoReference.clicked.connect(self._onAutomaticReference)
        self.btnEditGradient.clicked.connect(self._onEditGradient)
        self.btnCustomWeights.clicked.connect(self._onEditCustomWeights)

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
