"""Rating dialogs, and when to show them. Devmode only.

Timing, verified in Anki 26.09.3: CollectionOp's wrapped_done runs the success
callback of Reviewer._answerCard, whose _after_answering fires
reviewer_did_answer_card and only then calls nextCard(), and that starts the
next card's timer. So a modal exec() inside the hook can't add to the time of
any answer. Never defer one with QTimer: the next card would be on the clock.
"""

import time
from dataclasses import dataclass
from datetime import datetime
from random import Random
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from anki.consts import CARD_TYPE_NEW, CARD_TYPE_REV
from aqt.qt import (
    QDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPainter,
    QPalette,
    QPushButton,
    QRectF,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    Qt,
    QVBoxLayout,
    QWidget,
)

from .sampling import (
    DAY_RATING_MIN_ANSWERS,
    ROTATION_P,
    AnswerSample,
    evening_begins_ms,
    is_block_start,
    prompt_counts,
    prompts_open,
    prompts_since,
    sample_answer,
)
from .store import DAY_MS, Store, now_ms

# Raise this whenever a question or its answers change: it is logged with every
# prompt, so answers to different wordings can be told apart.
WORDING_VERSION = 4

# Letters on purpose: a digit beside a number reads as that number. Each row is
# longer than most scales need, so no key belongs to two sliders in one dialog.
_BOTTOM_ROW = "ZXCVBN"
_LOWER_ROW = "QWERTYU"
_THIRD_ROW = "ASDFG"
_DIGITS = "1234567"
# Keys hold whole doublings; the steps between are reached with the arrow keys.
_EVERY_OTHER = (0, 2, 4, 6, 8, 10)

_NOT_SET = "Not set yet"
_TICK_SPACE = 6  # above a slider's key letters, in pixels


class Step(NamedTuple):
    value: str  # shown beside the slider
    anchor: str  # what this position feels like, shown under it


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    steps: Tuple[Step, ...]
    keys: str  # each jumps to a position
    positions: Tuple[int, ...] = ()  # of the keys, by default one for each step
    start: int = 3  # where the handle waits: nothing is answered until it moves
    first_value: int = 0  # logged for the first step
    hint: str = ""

    @property
    def labels(self) -> Tuple[str, ...]:
        return tuple(step.value for step in self.steps)

    @property
    def key_positions(self) -> Tuple[Tuple[str, int], ...]:
        return tuple(zip(self.keys, self.positions or range(len(self.steps))))


# See docs/calibration.md for what each question means. Keep the steps of COST
# on half doublings, and in step with ANCHORS in fit.py and COST_LABELS in report.py.
COST = Question(
    "cost",
    "That review took as much effort as how many routine ones?",
    (
        Step("¼ of one", "Obvious at a glance, I barely read it"),
        Step("⅓ of one", "Almost no thought"),
        Step("½ of one", "Quick and sure, easier than routine"),
        Step("¾ of one", "A little easier than routine"),
        Step("One", "Routine: a familiar card, little thought"),
        Step("One and a half", "A bit more thought than routine"),
        Step("Two", "Real thought, or a second look"),
        Step("Three", "Hard recall, or careful reading"),
        Step("Four", "A minute or two of reading and thinking"),
        Step("Six", "A long, careful read"),
        Step("Eight or more", "Several minutes working through a dense card"),
    ),
    _BOTTOM_ROW,
    _EVERY_OTHER,
    start=4,
    hint="A routine review is a familiar card answered without much thought. "
         "Count recalling, reading, understanding and the time it took.",
)
ROTATING = {
    "gain": Question(
        "gain",
        "What did you get out of that review?",
        (
            Step("Nothing", "Autopilot: skipping it would have cost nothing"),
            Step("Very little", "I went through the motions"),
            Step("Kept it fresh", "I recalled it, nothing more"),
            Step("Reinforced it", "It feels a little more solid"),
            Step("A small insight", "I noticed a detail or a link"),
            Step("Learned something", "I understood something I hadn't"),
            Step("Learned a lot", "It changed how I see this card"),
        ),
        _LOWER_ROW,
    ),
    "grasp": Question(
        "grasp",
        "How much of this card do you really know now?",
        (
            Step("0%", "Nothing yet"),
            Step("10%", "I recognise it, no more"),
            Step("20%", "The rough idea"),
            Step("30%", "The rough idea and a little more"),
            Step("40%", "The main point, shakily"),
            Step("50%", "The main point, surely"),
            Step("60%", "The main point and some details"),
            Step("70%", "Most of it, with a few gaps"),
            Step("80%", "Nearly all, a nuance or two missing"),
            Step("90%", "All but the finest detail"),
            Step("100%", "All of it, nuances included"),
        ),
        _LOWER_ROW[:5],
        (2, 4, 6, 8, 10),
        start=5,
    ),
}
WHY = Question(
    "why",
    "Why did you press Again?",
    (
        Step("I blanked", "Nothing came"),
        Step("A hint", "Something came, not enough to pass"),
        Step("Partly", "I got part of it and missed the rest"),
        Step("Nearly", "I knew most of it and missed a detail"),
        Step("On purpose", "I knew it well enough, and failed it to see it again"),
    ),
    _THIRD_ROW,
    start=2,
)
SHARPNESS = Question(
    "sharpness",
    "How well can you concentrate right now?",
    (
        Step("Barely", "I can't focus, my mind keeps slipping"),
        Step("Poorly", "Foggy, staying on task takes real effort"),
        Step("Below usual", "A bit tired or distracted"),
        Step("As usual", "My normal study state"),
        Step("Well", "Clear and settled"),
        Step("Very well", "Sharp and engaged"),
        Step("Fully", "Completely absorbed, nothing distracts me"),
    ),
    _DIGITS,
    first_value=1,
)
TIRING = Question(
    "tiring",
    "How mentally tiring was today's study?",
    (
        Step("Not at all", "I could go on for hours"),
        Step("Barely", "Light, I hardly noticed it"),
        Step("A little", "Easier than a normal day"),
        Step("As usual", "A normal study day"),
        Step("Fairly", "I felt it, but had energy left"),
        Step("Very", "Drained, glad to stop"),
        Step("Exhausting", "I couldn't have done more"),
    ),
    _DIGITS,
    first_value=1,
)
LEARNED = Question(
    "learned",
    "How much did you learn or improve today?",
    (
        Step("Almost nothing", "Just grinding"),
        Step("Very little", "Mostly keeping things alive"),
        Step("A little", "A few small things stuck"),
        Step("As usual", "A normal day's progress"),
        Step("Quite a bit", "Several things became clearer"),
        Step("A lot", "Clearly better than yesterday"),
        Step("Far more than usual", "A real step forward"),
    ),
    _LOWER_ROW,
    first_value=1,
)


def _faint(text: str, parent: QWidget) -> QLabel:
    """Secondary text, grey in light and dark themes alike."""
    label = QLabel(text, parent)
    label.setEnabled(False)
    return label


class RatingSlider(QSlider):
    """Clicking moves the handle there at once, where a QSlider would only step."""

    def __init__(self, question: Question, parent: QWidget):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(0, len(question.steps) - 1)
        self.setPageStep(1)
        self.setValue(question.start)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _handle_length(self) -> int:
        # Not PM_SliderLength: under Anki's stylesheet that reads -1.
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        return self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self,
        ).width()

    def center_of(self, position: int) -> float:
        length = self._handle_length()
        return length / 2 + QStyle.sliderPositionFromValue(
            self.minimum(), self.maximum(), position, self.width() - length,
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            length = self._handle_length()
            self.setValue(QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(),
                int(event.position().x() - length / 2), self.width() - length,
            ))
            # Qt only says so for a press on the handle, and this one may have
            # landed beside a handle that had nowhere to go.
            self.sliderPressed.emit()
        super().mousePressEvent(event)  # starts the drag


class SliderKeys(QWidget):
    """A slider's ticks and shortcuts, each key under the tick it jumps to.

    The ticks are drawn here because Anki's stylesheet hides a QSlider's own.
    """

    def __init__(self, slider: RatingSlider, question: Question, parent: QWidget):
        super().__init__(parent)
        self.slider = slider
        self.key_positions = question.key_positions
        self.setFixedHeight(_TICK_SPACE + self.fontMetrics().height())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText,
        ))
        for position in range(self.slider.maximum() + 1):
            center = round(self.slider.center_of(position))
            painter.drawLine(center, 0, center, _TICK_SPACE - 3)
        height = self.height() - _TICK_SPACE
        for key, position in self.key_positions:
            center = self.slider.center_of(position)
            painter.drawText(
                QRectF(center - 12, _TICK_SPACE, 24, height), Qt.AlignmentFlag.AlignCenter, key,
            )


class RatingDialog(QDialog):
    """Sliders, each showing its value and what that value feels like.

    A slider counts once it is moved, clicked or set by a key: it starts with no
    answer, so that its resting place can't become the most common rating. Done
    or Enter confirms once all are set. Esc leaves the rest unanswered, and no
    timer ever dismisses it.
    """

    def __init__(
        self, title: str, questions: Sequence[Question], parent=None,
        with_note: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.answers: Dict[str, int] = {}
        self.reaction_ms: Dict[str, int] = {}
        self._questions = questions
        self._choices: Dict[int, Tuple[Question, int]] = {}
        self._sliders: Dict[str, RatingSlider] = {}
        self._readouts: Dict[str, Tuple[QLabel, QLabel]] = {}
        self._shown_at = 0.0
        self.noteEdit: Optional[QLineEdit] = None
        self.setMinimumWidth(460)
        self._layout = QVBoxLayout(self)
        # One width for all, so that every slider in the dialog has one length.
        metrics = self.fontMetrics()
        self._value_width = 16 + max(
            metrics.horizontalAdvance(label) for question in questions for label in question.labels
        )
        for index, question in enumerate(questions):
            if index:
                self._layout.addSpacing(12)
            self._addQuestion(question)
        if with_note:
            self._addNote()
        self.btnDone = QPushButton("Done", self)
        self.btnDone.setDefault(True)
        self.btnDone.setEnabled(False)
        self.btnDone.clicked.connect(self.accept)
        self._layout.addWidget(self.btnDone)
        self._layout.addWidget(_faint("Keys and arrows move a slider. Enter confirms, Esc skips.", self))

    def _addQuestion(self, question: Question) -> None:
        self._layout.addWidget(QLabel(question.text, self))
        if question.hint:
            hint = _faint(question.hint, self)
            hint.setWordWrap(True)
            self._layout.addWidget(hint)
        slider = RatingSlider(question, self)
        value = QLabel("—", self)
        value.setStyleSheet("font-weight: bold;")
        value.setMinimumWidth(self._value_width)
        anchor = QLabel(_NOT_SET, self)
        grid = QGridLayout()
        grid.addWidget(slider, 0, 0)
        grid.addWidget(value, 0, 1)
        grid.addWidget(SliderKeys(slider, question, self), 1, 0)
        grid.setColumnStretch(0, 1)
        grid.setVerticalSpacing(2)
        self._layout.addLayout(grid)
        self._layout.addWidget(anchor)
        self._sliders[question.id] = slider
        self._readouts[question.id] = (value, anchor)
        for key, position in question.key_positions:
            self._choices[getattr(Qt.Key, "Key_" + key)] = (question, position)
        slider.valueChanged.connect(lambda _position, chosen=question: self._answer(chosen))
        # A click on the resting place changes no value, yet it is an answer.
        slider.sliderPressed.connect(lambda chosen=question: self._answer(chosen))

    def _addNote(self) -> None:
        self.noteEdit = QLineEdit(self)
        self.noteEdit.setPlaceholderText("Optional note")
        # Tab must not move into it: keys belong to the sliders until it is clicked.
        self.noteEdit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._layout.addWidget(self.noteEdit)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._shown_at = time.monotonic()

    def keyPressEvent(self, event) -> None:
        choice = self._choices.get(event.key())
        if choice is None:
            super().keyPressEvent(event)
        # A held answer key must not set the prompt it just opened.
        elif not event.isAutoRepeat():
            question, position = choice
            slider = self._sliders[question.id]
            slider.setFocus()  # the arrow keys then fine-tune it
            slider.setValue(position)
            self._answer(question)

    def _answer(self, question: Question) -> None:
        position = self._sliders[question.id].value()
        self.answers[question.id] = question.first_value + position
        self.reaction_ms[question.id] = int((time.monotonic() - self._shown_at) * 1000)
        value, anchor = self._readouts[question.id]
        value.setText(question.steps[position].value)
        anchor.setText(question.steps[position].anchor)
        self.btnDone.setEnabled(not self.skipped())

    def skipped(self) -> List[str]:
        return [q.id for q in self._questions if q.id not in self.answers]

    def note(self) -> str:
        return self.noteEdit.text().strip() if self.noteEdit else ""


class Prompter:
    """Decides on prompts from the recorder's hooks, and logs what they got.
    Every entry point is called through the recorder's guards."""

    def __init__(
        self, mw, store: Store, emit: Callable[..., None], rng: Optional[Random] = None,
    ):
        self._mw = mw
        self._store = store
        self._emit = emit
        self._rng = rng or Random()
        self._state = store.load_state()
        self._shown_today = 0
        self._cost_ratings = 0
        self._last_activity_ms = 0
        self._left_reviewer = False

    def start(self) -> None:
        events = list(self._store.read_events("prompt"))
        self._cost_ratings = prompt_counts(events)["cost"]
        self._shown_today = prompts_since(events, self._today()[0])
        self._last_activity_ms = self._last_answer_ms(now_ms() + 1) or 0

    def reload(self) -> None:
        """Adopt settings changed in the tab."""
        self._state = self._store.load_state()

    def on_new_day(self) -> None:
        self._shown_today = 0

    def after_answer(
        self, cid: int, rev_id: int, seq: Optional[int], ease: int, taken_ms: int,
        ctype_before: int,
    ) -> None:
        """Runs in reviewer_did_answer_card, before the next card's timer."""
        now = now_ms()
        late_block = self._block_starts(now)
        self._last_activity_ms = now
        if not self._prompts_open(now):
            return
        sample = sample_answer(
            self._state, self._rng, taken_ms, ctype_before == CARD_TYPE_NEW, ease,
            ctype_before == CARD_TYPE_REV,
        )
        if sample:
            self._ask_answer(sample, cid=cid, rev_id=rev_id, seq=seq, ease=ease)
        if late_block and self._prompts_open(now):
            self._ask_sharpness(late=True)

    def on_state_will_change(self, new: str, old: str) -> None:
        """Runs before the new view draws, so no card is on the clock."""
        if old == "review":
            self._left_reviewer = True
        if new == "deckBrowser":
            # The reviewer may have gone through the congratulations screen.
            if self._left_reviewer:
                self._left_reviewer = False
                self._offer_day_rating()
        elif new == "review":
            self._on_entering_reviewer()

    def _on_entering_reviewer(self) -> None:
        # The revlog also knows about answers synced in since the last one here.
        now = now_ms()
        last = self._last_answer_ms(now + 1) or 0
        self._last_activity_ms = max(self._last_activity_ms, last)
        if self._block_starts(now):
            self._last_activity_ms = now
            if self._prompts_open(now):
                self._ask_sharpness(late=False)

    # Day rating
    #########################################################################

    def _offer_day_rating(self) -> None:
        """Rate today once, on the way back to the deck list in the evening.

        Only leaving the reviewer can ask, so nobody is asked about study they
        haven't done, and the final score isn't on screen yet. A day that never
        gets here goes unrated: any later question would blur into the next day.
        """
        start, end = self._today()
        evening = evening_begins_ms(
            start, datetime.fromtimestamp(start / 1000).hour, self._state["day_rating_hour"],
        )
        if end <= self._state["day_rated_through_ms"] or now_ms() < evening:
            return
        answers = self._count_answers(start, end)
        if answers < DAY_RATING_MIN_ANSWERS:
            return
        date = datetime.fromtimestamp(start / 1000)
        self._ask(
            "day",
            f"Today's study, {date:%Y-%m-%d}",
            (TIRING, LEARNED),
            with_note=True,
            p=1.0,
            day_end_ms=end,
            day_answers=answers,
        )
        self._save(day_rated_through_ms=end)

    # Answer and sharpness prompts
    #########################################################################

    def _ask_answer(self, sample: AnswerSample, **fields) -> None:
        questions = []
        if sample.rotation:
            questions += [COST, ROTATING[sample.rotation]]
            fields["rotation_p"] = ROTATION_P
        if sample.why:
            questions.append(WHY)
        self._ask(
            "answer",
            "That review",
            questions,
            answer_p=sample.answer_p,
            why_p=sample.why_p,
            **fields,
        )

    def _ask_sharpness(self, late: bool) -> None:
        self._ask("sharpness", "New study block", (SHARPNESS,), p=1.0, late=late)

    def _ask(
        self, kind: str, title: str, questions: Sequence[Question],
        with_note: bool = False, **fields,
    ) -> None:
        dialog = RatingDialog(title, questions, self._mw, with_note)
        dialog.exec()
        if dialog.note():
            fields["note"] = dialog.note()
        self._emit(
            "prompt",
            kind=kind,
            wording_v=WORDING_VERSION,
            answers=dialog.answers,
            rt_ms=dialog.reaction_ms,
            skipped=dialog.skipped(),
            **fields,
        )
        self._shown_today += kind != "day"
        self._cost_ratings += COST.id in dialog.answers
        dialog.deleteLater()

    def _prompts_open(self, now: int) -> bool:
        return prompts_open(self._state, now, self._shown_today, self._cost_ratings)

    def _block_starts(self, now: int) -> bool:
        return is_block_start(
            self._last_activity_ms, now, self._state["block_gap_hours"],
        )

    # Collection and state
    #########################################################################

    def _today(self) -> Tuple[int, int]:
        end = self._mw.col.sched.day_cutoff * 1000
        return end - DAY_MS, end

    def _last_answer_ms(self, before_ms: int) -> Optional[int]:
        # WARNING: keep the leading SELECT (see ActivityReporter.set_collection).
        return self._mw.col.db.scalar(
            "SELECT id FROM revlog WHERE id < ? AND ease >= 1 ORDER BY id DESC LIMIT 1",
            before_ms,
        )

    def _count_answers(self, start_ms: int, end_ms: int) -> int:
        return self._mw.col.db.scalar(
            "SELECT count() FROM revlog WHERE id >= ? AND id < ? AND ease >= 1",
            start_ms,
            end_ms,
        )

    def _save(self, **changes) -> None:
        self._state = self._store.update_state(**changes)
