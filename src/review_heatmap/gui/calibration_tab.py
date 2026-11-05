"""Hidden settings tab for the developer-only effort calibration.

Everything here acts at once on the calibration data folder: the tab has no
mapped widgets and stays out of OK/Cancel, so it never touches synced settings
or Anki's undo history.
"""

import traceback
from datetime import datetime

from aqt.operations import QueryOp
from aqt.qt import (
    QApplication,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    sip,
)
from aqt.utils import openFolder, showWarning, tooltip

from ..activity import ActivityReporter
from ..calibration import activate_model
from ..calibration.dataset import revlog_sql
from ..calibration.recorder import active_recorder, start_recording, stop_recording
from ..calibration.report import (
    format_report,
    gate_text,
    latest_report,
    pretty_json,
    read_model,
    run_fit,
    write_model,
)
from ..calibration.sampling import prompt_counts
from ..calibration.store import Store, baseline_days_left, now_ms
from ..config import config

# The fit is worth reading from here on.
_COST_RATINGS_GOAL = 800
_FIT_MIN_COST_RATINGS = 300

# key -> (label, decimals, step, maximum)
_SETTINGS = {
    "baseline_days": ("Silent baseline (days)", 0, 1, 365),
    "answer_rate": ("Answer rate", 3, 0.01, 1),
    "lapse_reason_rate": ("Again reason rate", 2, 0.05, 1),
    "block_gap_hours": ("Study block gap (hours)", 1, 0.5, 24),
    "day_rating_hour": ("Day rating from (hour)", 0, 1, 23),
    "daily_cap": ("Daily prompt cap", 0, 1, 500),
    "stop_after_cost_ratings": ("Stop after cost ratings", 0, 50, 100000),
}


class CalibrationTab(QWidget):
    def __init__(self, mw, parent=None):
        super().__init__(parent)
        self._mw = mw
        self._store = Store.for_profile(mw.pm.profileFolder())
        # With the report, this outgrows a small screen.
        content = QWidget()
        self._layout = QVBoxLayout(content)
        scroll = QScrollArea(self)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._setupRecordingSection()
        self._setupPromptSection()
        self._setupFitSection()
        self._layout.addStretch(1)
        self._refreshRecordingSection()
        self._refreshFitSection()

    def _setupRecordingSection(self):
        group = QGroupBox("Recording", self)
        layout = QVBoxLayout(group)
        self.labRecording = QLabel(group)
        self.labRecording.setWordWrap(True)
        layout.addWidget(self.labRecording)
        buttons = QHBoxLayout()
        self.btnRecording = QPushButton(group)
        self.btnRecording.clicked.connect(self._onToggleRecording)
        buttons.addWidget(self.btnRecording)
        self.btnDataFolder = QPushButton("Open Data Folder", group)
        self.btnDataFolder.clicked.connect(self._onOpenDataFolder)
        buttons.addWidget(self.btnDataFolder)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self._layout.addWidget(group)

    def _setupPromptSection(self):
        group = QGroupBox("Prompts", self)
        layout = QVBoxLayout(group)
        form = QFormLayout()
        state = self._store.load_state()
        self.settingBoxes = {}
        for key, (label, decimals, step, maximum) in _SETTINGS.items():
            box = QDoubleSpinBox(group) if decimals else QSpinBox(group)
            if decimals:
                box.setDecimals(decimals)
            box.setRange(0, maximum)
            box.setSingleStep(step)
            box.setValue(state[key])
            box.setKeyboardTracking(False)
            box.valueChanged.connect(
                lambda value, setting=key: self._onSettingChanged(setting, value)
            )
            form.addRow(label, box)
            self.settingBoxes[key] = box
        layout.addLayout(form)
        self.labPrompts = QLabel(group)
        layout.addWidget(self.labPrompts)
        self._layout.addWidget(group)

    def _setupFitSection(self):
        self._report = latest_report(self._store.folder)
        group = QGroupBox("Fit", self)
        layout = QVBoxLayout(group)
        self.labFit = QLabel(group)
        self.labFit.setWordWrap(True)
        layout.addWidget(self.labFit)
        buttons = QHBoxLayout()
        self.btnFit = QPushButton("Fit", group)
        self.btnFit.clicked.connect(self._onFit)
        buttons.addWidget(self.btnFit)
        self.btnUseFit = QPushButton("Use This Fit on This Profile", group)
        self.btnUseFit.clicked.connect(self._onUseFit)
        buttons.addWidget(self.btnUseFit)
        self.btnCopyWeights = QPushButton("Copy Weights as JSON", group)
        self.btnCopyWeights.clicked.connect(self._onCopyWeights)
        buttons.addWidget(self.btnCopyWeights)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.txtReport = QPlainTextEdit(group)
        self.txtReport.setReadOnly(True)
        self.txtReport.setMinimumHeight(120)
        layout.addWidget(self.txtReport)
        self._layout.addWidget(group, 1)

    def _refreshRecordingSection(self):
        recording = active_recorder() is not None
        state = self._store.load_state()
        since = state["recording_since"]
        now = now_ms()
        lines = [
            "Recording since " + _date(since) if recording else "Not recording",
            f"Answers logged: {self._store.count_events('ans')}",
            f"Baseline days left: {baseline_days_left(state, now)}",
            f"Errors: {self._store.error_count()}",
        ]
        self.labRecording.setText("\n".join(lines))
        self.btnRecording.setText("Stop Recording" if recording else "Start Recording")
        counts = prompt_counts(self._store.read_events("prompt"))
        skip_rate = counts["skipped"] / counts["prompts"] if counts["prompts"] else 0
        self.labPrompts.setText("\n".join((
            f"Cost ratings: {counts['cost']}/{_COST_RATINGS_GOAL}",
            f"Gain: {counts['gain']}",
            f"Grasp: {counts['grasp']}",
            f"Again reasons: {counts['why']}",
            f"Sharpness: {counts['sharpness']}",
            f"Day ratings: {counts['tiring']}",
            f"Skip rate: {skip_rate:.0%}",
        )))
        self.btnFit.setEnabled(counts["cost"] >= _FIT_MIN_COST_RATINGS)

    def _refreshFitSection(self):
        report = self._report
        local = read_model(self._store.folder)
        gate = gate_text(report["gate"]) if report else (
            f"No fit yet: it needs {_FIT_MIN_COST_RATINGS} cost ratings."
        )
        weights = f"fit version {local['version']}" if local else "the shipped weights"
        self.labFit.setText(f"{gate}\nIn use on this profile: {weights}")
        self.txtReport.setPlainText(format_report(report) if report else "")
        self.btnUseFit.setEnabled(bool(report))
        self.btnCopyWeights.setEnabled(bool(report))

    def _onSettingChanged(self, key, value):
        self._store.update_state(**{key: value})
        recorder = active_recorder()
        if recorder:
            recorder.prompter.reload()
        self._refreshRecordingSection()

    def _onToggleRecording(self):
        if active_recorder():
            self._store.update_state(recording=False)
            stop_recording()
        else:
            since = self._store.load_state()["recording_since"]
            self._store.update_state(
                recording=True,
                recording_since=since or now_ms(),
            )
            start_recording(self._mw, self._store)
        self._refreshRecordingSection()

    def _onOpenDataFolder(self):
        self._store.folder.mkdir(parents=True, exist_ok=True)
        openFolder(str(self._store.folder))

    def _onFit(self):
        mw = self._mw
        conf = config["synced"]
        offset = ActivityReporter(mw.col, config)._offset

        def progress(label):
            mw.taskman.run_on_main(lambda: mw.progress.update(label=label))

        def fit(col):
            rows = col.db.all(revlog_sql(offset))
            return run_fit(self._store, rows, conf, _template_names(col), progress)

        QueryOp(parent=self, op=fit, success=self._onFitDone).failure(
            self._onFitFailed
        ).with_progress("Reading the revlog").run_in_background()

    def _onFitDone(self, report):
        # The progress window only blocks this dialog once it shows up.
        if sip.isdeleted(self):
            return
        self._report = report
        self._refreshFitSection()

    def _onFitFailed(self, error):
        self._store.log_error("fit", "".join(traceback.format_exception(error)))
        if sip.isdeleted(self):
            return
        self._refreshRecordingSection()
        showWarning(f"The fit failed: {error}", parent=self)

    def _onUseFit(self):
        # With this profile's note-type offsets, unlike Copy Weights below.
        model = self._report["local_model"]
        write_model(self._store.folder, model)
        activate_model(self._mw, model)
        self._refreshFitSection()
        tooltip("Calibrated effort is in the measures next time settings open.", parent=self)

    def _onCopyWeights(self):
        QApplication.clipboard().setText(pretty_json(self._report["model"]))
        tooltip("Weights copied", parent=self)


def _template_names(col) -> dict:
    return {
        (notetype["id"], card_ord): f"{notetype['name']} / {template['name']}"
        for notetype in col.models.all()
        for card_ord, template in enumerate(notetype["tmpls"])
    }


def _date(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000).strftime("%Y-%m-%d")
