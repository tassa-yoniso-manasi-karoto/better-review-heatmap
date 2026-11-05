import importlib
import itertools
import sqlite3
import time
from collections import deque
from datetime import datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("anki")
pytest.importorskip("aqt")

from anki.scheduler.v3 import CardAnswer
from aqt import gui_hooks
from aqt.qt import (
    QApplication, QEvent, QKeyEvent, QPoint, QStyle, QStyleOptionSlider, Qt, QTimer, QWidget,
)
from aqt.reviewer import Reviewer
from aqt.stylesheets import custom_styles
from aqt.theme import theme_manager
from PyQt6.QtTest import QTest

from test_activity import setup  # noqa: F401
from test_calibration_recorder import (  # noqa: F401
    add_note, answer, col, dormant, events, fake_mw, loaded_calibration_modules,
    recorder_module, review_once, show, store,
)

Key = Qt.Key
ESC = Key.Key_Escape
HOUR = 3600
DAY = 86400
# A fixed "now", 12 hours into a study day ending at CUTOFF.
NOW = 1793880000
CUTOFF = NOW + 12 * HOUR


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def prompts(addon_modules):
    return importlib.import_module("review_heatmap.calibration.prompts")


class Owner:
    """Sits at the screen: answers each dialog that opens with its keys."""

    def __init__(self):
        self.keys = deque()
        self.titles = []
        self._timer = QTimer()
        self._timer.setInterval(1)
        self._timer.timeout.connect(self._look)
        self._timer.start()

    def _look(self):
        dialog = QApplication.activeModalWidget()
        if not dialog or not dialog.isVisible():
            return
        self.titles.append(dialog.windowTitle())
        for key in self.keys.popleft() if self.keys else (ESC,):
            QTest.keyClick(dialog, key)
            if not dialog.isVisible():
                break

    def stop(self):
        self._timer.stop()


@pytest.fixture
def owner(app):
    owner = Owner()
    yield owner
    owner.stop()


class FakeMw(QWidget):
    def __init__(self, col, state="deckBrowser"):
        super().__init__()
        self.col = col
        self.state = state
        self.refreshes = []
        self.invalidated = []
        self.deckBrowser = SimpleNamespace(
            refresh=lambda: self.refreshes.append("deckBrowser"),
        )
        self.overview = SimpleNamespace(
            refresh=lambda: self.refreshes.append("overview"),
        )
        self._review_heatmap = SimpleNamespace(
            invalidate_cache=lambda: self.invalidated.append(True),
        )


class Rigged:
    """Forces the cost and why draws, in the order sample_answer takes them."""

    def __init__(self, cost=False, why=False, rotation="gain"):
        self._draws = itertools.cycle((0.0 if cost else 1.0, 0.0 if why else 1.0))
        self._rotation = rotation

    def random(self):
        return next(self._draws)

    def choice(self, options):
        return self._rotation


@pytest.fixture
def recording(app, recorder_module, col, store, prompts):
    """The real recorder on a real collection, past its baseline."""

    def start(rng, **state):
        store.update_state(**{"recording": True, "baseline_days": 0, **state})
        mw = FakeMw(col, "review")
        recorder = recorder_module.start_recording(mw, store)
        recorder.prompter._rng = rng
        recorder.prompter._last_activity_ms = int(time.time() * 1000)
        recorder.mw = mw
        return recorder

    return start


def prompt_events(store):
    return events(store, "prompt")


# Keys
###########################################################################


def open_dialog(prompts, questions, **kwargs):
    dialog = prompts.RatingDialog("Title", questions, **kwargs)
    dialog.show()
    return dialog


def confirm(dialog):
    QTest.keyClick(dialog, Key.Key_Return)


@pytest.mark.parametrize("key, value", [
    (Key.Key_Z, 0), (Key.Key_X, 2), (Key.Key_C, 4),
    (Key.Key_V, 6), (Key.Key_B, 8), (Key.Key_N, 10),
])
def test_the_bottom_letter_row_sets_the_doublings(app, prompts, key, value):
    dialog = open_dialog(prompts, (prompts.COST,))
    QTest.keyClick(dialog, key)
    assert dialog.answers == {"cost": value}
    assert dialog._sliders["cost"].value() == value


def test_a_key_sets_the_slider_and_only_enter_confirms(app, prompts):
    dialog = open_dialog(prompts, (prompts.COST,))
    QTest.keyClick(dialog, Key.Key_V)
    assert dialog.isVisible()
    QTest.keyClick(dialog, Key.Key_B)  # a change of mind costs nothing
    assert dialog.isVisible() and dialog.answers == {"cost": 8}
    confirm(dialog)
    assert dialog.result() == dialog.DialogCode.Accepted


def test_arrows_reach_the_steps_between_the_keys(app, prompts):
    dialog = open_dialog(prompts, (prompts.COST,))
    QTest.keyClick(dialog, Key.Key_V)
    QTest.keyClick(dialog._sliders["cost"], Key.Key_Right)
    assert dialog.answers == {"cost": 7}
    assert dialog._readouts["cost"][0].text() == "Three"


def test_keys_get_through_a_focused_slider(app, prompts):
    # A real key press reaches the slider first, not the dialog.
    dialog = open_dialog(prompts, (prompts.COST,))
    slider = dialog._sliders["cost"]
    QTest.keyClick(slider, Key.Key_V)
    QTest.keyClick(slider, Key.Key_Left)
    assert dialog.answers == {"cost": 5} and dialog.isVisible()
    QTest.keyClick(slider, Key.Key_Return)
    assert dialog.result() == dialog.DialogCode.Accepted


@pytest.mark.parametrize("key", [Key.Key_1, Key.Key_2, Key.Key_3, Key.Key_4])
def test_digits_never_rate_a_cost(app, prompts, key):
    # 2 must not record 1/2 to someone meaning "2", nor Good a routine cost.
    dialog = open_dialog(prompts, (prompts.COST,))
    QTest.keyClick(dialog, key)
    assert dialog.answers == {} and dialog.isVisible()
    dialog.reject()


def test_an_untouched_slider_is_no_answer(app, prompts):
    dialog = open_dialog(prompts, (prompts.COST, prompts.ROTATING["gain"]))
    assert dialog.answers == {} and dialog.skipped() == ["cost", "gain"]
    assert dialog._readouts["cost"][0].text() == "—"
    assert not dialog.btnDone.isEnabled()
    confirm(dialog)
    assert dialog.isVisible()
    QTest.keyClick(dialog, Key.Key_V)
    assert not dialog.btnDone.isEnabled()  # one slider is still at rest
    QTest.keyClick(dialog, Key.Key_Q)
    assert dialog.btnDone.isEnabled()
    dialog.reject()


def test_the_value_and_its_anchor_follow_the_slider(app, prompts):
    dialog = open_dialog(prompts, (prompts.COST,))
    QTest.keyClick(dialog, Key.Key_V)
    value, anchor = dialog._readouts["cost"]
    assert value.text() == "Two"
    assert anchor.text() == "Real thought, or a second look"


def test_each_key_is_shown_under_the_tick_it_jumps_to(app, prompts):
    dialog = open_dialog(prompts, (prompts.COST,))
    shortcuts = dialog.findChild(prompts.SliderKeys)
    assert ("V", 6) in shortcuts.key_positions
    assert shortcuts.slider is dialog._sliders["cost"]


def test_no_value_can_pass_for_a_bare_number(prompts):
    questions = (prompts.COST, *prompts.ROTATING.values(), prompts.WHY,
                 prompts.SHARPNESS, prompts.TIRING, prompts.LEARNED)
    assert not [
        label for question in questions for label in question.labels
        if label.isnumeric() or "[" in label
    ]


def test_every_step_has_its_own_description(prompts):
    questions = (prompts.COST, *prompts.ROTATING.values(), prompts.WHY,
                 prompts.SHARPNESS, prompts.TIRING, prompts.LEARNED)
    for question in questions:
        anchors = [step.anchor for step in question.steps]
        assert all(anchors) and len(set(anchors)) == len(anchors)
        assert len(question.steps) >= 5


def test_every_key_in_a_prompt_belongs_to_one_slider(prompts):
    rows = [prompts.COST, prompts.ROTATING["gain"], prompts.WHY]
    keys = [key for question in rows for key, _ in question.key_positions]
    assert len(keys) == len(set(keys))
    rows = [prompts.COST, prompts.ROTATING["grasp"]]
    keys = [key for question in rows for key, _ in question.key_positions]
    assert len(keys) == len(set(keys))
    rows = [prompts.TIRING, prompts.LEARNED]
    keys = [key for question in rows for key, _ in question.key_positions]
    assert len(keys) == len(set(keys))


def test_keys_land_where_the_scale_says(prompts):
    for question in (prompts.COST, prompts.ROTATING["grasp"]):
        assert all(position < len(question.steps) for _, position in question.key_positions)
    assert prompts.COST.labels[6] == "Two"
    assert prompts.ROTATING["grasp"].key_positions[3] == ("R", 8)  # 80%


def test_two_sliders_answer_independently(app, prompts):
    dialog = open_dialog(prompts, (prompts.COST, prompts.ROTATING["grasp"]))
    QTest.keyClick(dialog, Key.Key_X)
    assert dialog.isVisible() and dialog.skipped() == ["grasp"]
    QTest.keyClick(dialog, Key.Key_R)
    assert dialog.answers == {"cost": 2, "grasp": 8}
    confirm(dialog)
    assert not dialog.isVisible()
    assert set(dialog.reaction_ms) == {"cost", "grasp"}


def test_keys_for_another_slider_do_nothing(app, prompts):
    dialog = open_dialog(prompts, (prompts.ROTATING["gain"],))
    QTest.keyClick(dialog, Key.Key_1)
    QTest.keyClick(dialog, Key.Key_I)
    assert dialog.answers == {} and dialog.isVisible()
    dialog.reject()


def test_all_three_sliders_can_share_one_dialog(app, prompts):
    dialog = open_dialog(
        prompts, (prompts.COST, prompts.ROTATING["gain"], prompts.WHY),
    )
    for key in (Key.Key_N, Key.Key_E, Key.Key_G):
        QTest.keyClick(dialog, key)
    assert dialog.answers == {"cost": 10, "gain": 2, "why": 4}


def test_a_held_answer_key_does_not_set_the_prompt(app, prompts):
    dialog = open_dialog(prompts, (prompts.SHARPNESS,))  # opens right after an answer
    held = QKeyEvent(
        QEvent.Type.KeyPress, Key.Key_3, Qt.KeyboardModifier.NoModifier, "3",
        True,  # autorepeat
    )
    QApplication.sendEvent(dialog, held)
    assert dialog.answers == {} and dialog.isVisible()
    dialog.reject()


def test_space_and_enter_answer_nothing(app, prompts):
    dialog = open_dialog(prompts, (prompts.COST,))
    QTest.keyClick(dialog, Key.Key_Space)
    QTest.keyClick(dialog, Key.Key_Return)
    assert dialog.answers == {} and dialog.isVisible()
    dialog.reject()


def click_at(slider, position):
    QTest.mouseClick(
        slider, Qt.MouseButton.LeftButton,
        pos=QPoint(int(slider.center_of(position)), slider.height() // 2),
    )


def test_clicking_jumps_to_the_spot(app, prompts):
    dialog = open_dialog(prompts, (prompts.COST,))
    app.processEvents()
    click_at(dialog._sliders["cost"], 7)
    assert dialog.answers == {"cost": 7}


def test_clicking_the_resting_place_is_an_answer(app, prompts):
    dialog = open_dialog(prompts, (prompts.COST,))
    app.processEvents()
    click_at(dialog._sliders["cost"], prompts.COST.start)
    assert dialog.answers == {"cost": prompts.COST.start}
    assert dialog._readouts["cost"][0].text() == "One"


def test_clicking_beside_the_resting_handle_is_an_answer(app, prompts):
    dialog = open_dialog(prompts, (prompts.WHY,))
    app.processEvents()
    slider = dialog._sliders["why"]
    beside = int(slider.center_of(prompts.WHY.start)) + 30  # still nearest that step
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(beside, slider.height() // 2))
    assert dialog.answers == {"why": prompts.WHY.start}


def test_ticks_and_clicks_find_the_handle_under_ankis_stylesheet(app, prompts):
    dialog = prompts.RatingDialog("Title", (prompts.COST,))
    dialog.setStyleSheet(custom_styles.slider(theme_manager))
    dialog.show()
    app.processEvents()
    slider = dialog._sliders["cost"]
    option = QStyleOptionSlider()
    for position in (0, 7, 10):
        slider.setValue(position)
        slider.initStyleOption(option)
        handle = slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, slider,
        )
        assert slider.center_of(position) == pytest.approx(handle.center().x(), abs=2)
        click_at(slider, position)
        assert slider.value() == position


def test_note_dialog_waits_for_done_and_digits_can_be_typed(app, prompts):
    dialog = open_dialog(prompts, (prompts.TIRING, prompts.LEARNED), with_note=True)
    assert not dialog.btnDone.isEnabled()
    QTest.keyClick(dialog, Key.Key_4)
    QTest.keyClick(dialog, Key.Key_W)
    assert dialog.answers == {"tiring": 4, "learned": 2}
    assert dialog.isVisible() and dialog.btnDone.isEnabled()
    QTest.keyClicks(dialog.noteEdit, "2 tired")
    assert dialog.note() == "2 tired" and dialog.answers["tiring"] == 4
    confirm(dialog)
    assert dialog.result() == dialog.DialogCode.Accepted


# Answer prompts on the real recorder
###########################################################################


def test_reading_one_kind_keeps_its_events_only(store):
    from review_heatmap.calibration.store import Store

    store.append({"v": 1, "t": "prompt", "ts": 1793836800000, "note": '"t":"ans"'})
    store.append({"v": 1, "t": "ans", "ts": 1793836800001})
    store.close()
    path = next(store.folder.glob("events-*.jsonl"))
    path.write_bytes(path.read_bytes() + b'{"v":1,"t":"prompt","ts":17')
    assert [event["t"] for event in Store(store.folder).read_events("prompt")] == ["prompt"]
    assert [event["t"] for event in Store(store.folder).read_events("ans")] == ["ans"]


def test_prompt_logs_answers_probabilities_and_the_join_key(
    col, recording, owner, store,
):
    recorder = recording(Rigged(cost=True, rotation="grasp"))
    owner.keys.append((Key.Key_V, Key.Key_W, Key.Key_Return))
    add_note(col)
    card, states = show(col)
    answer(col, card, states)
    event = prompt_events(store)[0]
    answered = events(store, "ans")[0]
    assert event["kind"] == "answer"
    assert event["run"] == recorder.run
    assert event["wording_v"] == 4
    assert event["answers"] == {"cost": 6, "grasp": 4}
    assert event["skipped"] == []
    assert set(event["rt_ms"]) == {"cost", "grasp"}
    assert event["rev_id"] == answered["rev_id"]
    assert (event["seq"], event["cid"], event["ease"]) == (1, card.id, 3)
    assert event["answer_p"] == pytest.approx(0.06 * 3)  # a new card
    assert event["why_p"] == 0
    assert event["rotation_p"] == 0.5


def test_esc_is_logged_as_a_skip(col, recording, owner, store):
    recording(Rigged(cost=True))
    owner.keys.append((ESC,))
    add_note(col)
    card, states = show(col)
    answer(col, card, states)
    event = prompt_events(store)[0]
    assert event["answers"] == {}
    assert event["skipped"] == ["cost", "gain"]


def test_esc_after_the_cost_keeps_the_cost(col, recording, owner, store):
    recording(Rigged(cost=True))
    owner.keys.append((Key.Key_X, ESC))
    add_note(col)
    card, states = show(col)
    answer(col, card, states)
    event = prompt_events(store)[0]
    assert event["answers"] == {"cost": 2}
    assert event["skipped"] == ["gain"]


def test_long_answers_are_judged_on_the_uncapped_time(col, recording, owner, store):
    recording(Rigged(cost=True))
    add_note(col)
    review_once(col)
    card, states = show(col)
    card.timer_started -= 100  # past the preset's 60 s cap
    answer(col, card, states)
    assert prompt_events(store)[0]["answer_p"] == pytest.approx(0.06 * 7)


def test_why_asks_after_again_on_a_card_in_review_state(col, recording, owner, store):
    recording(Rigged(why=True))
    owner.keys.append((Key.Key_D, Key.Key_Return))
    add_note(col)
    review_once(col)
    card, states = show(col)
    answer(col, card, states, CardAnswer.AGAIN)
    event = prompt_events(store)[0]
    assert event["answers"] == {"why": 2}
    assert event["why_p"] == 0.3
    assert "rotation_p" not in event


@pytest.mark.parametrize("rating", [CardAnswer.HARD, CardAnswer.GOOD, CardAnswer.EASY])
def test_why_skips_answers_other_than_again(col, recording, owner, store, rating):
    recording(Rigged(why=True))
    add_note(col)
    review_once(col)
    card, states = show(col)
    answer(col, card, states, rating)
    assert prompt_events(store) == [] and owner.titles == []


def test_why_skips_again_on_a_new_card(col, recording, owner, store):
    recording(Rigged(why=True))
    add_note(col)
    card, states = show(col)
    answer(col, card, states, CardAnswer.AGAIN)
    assert prompt_events(store) == []


def test_why_skips_again_on_a_relearning_card(col, recording, owner, store):
    recorder = recording(Rigged())
    add_note(col)
    review_once(col)
    card, states = show(col)
    answer(col, card, states, CardAnswer.AGAIN)
    assert card.type == 3
    recorder.prompter._rng = Rigged(why=True)
    card, states = show(col)
    answer(col, card, states, CardAnswer.AGAIN)
    assert prompt_events(store) == []


def test_daily_cap_stops_prompts_and_counts_earlier_runs(
    col, recording, owner, store, recorder_module,
):
    recording(Rigged(cost=True), daily_cap=2)
    for _ in range(3):
        add_note(col)
    for _ in range(2):
        card, states = show(col)
        answer(col, card, states, CardAnswer.EASY)
    assert len(prompt_events(store)) == 2
    # A restart on the same day doesn't reset the count.
    recording(Rigged(cost=True), daily_cap=2)
    card, states = show(col)
    answer(col, card, states, CardAnswer.EASY)
    assert len(prompt_events(store)) == 2


def test_cost_ratings_stop_the_prompts_but_not_the_log(col, recording, owner, store):
    recorder = recording(Rigged(cost=True), stop_after_cost_ratings=1)
    owner.keys.extend(((Key.Key_Z, Key.Key_Q, Key.Key_Return), (ESC,)))
    for _ in range(2):
        add_note(col)
    for _ in range(2):
        card, states = show(col)
        answer(col, card, states, CardAnswer.EASY)
    assert len(prompt_events(store)) == 1
    assert len(events(store, "ans")) == 2
    assert recorder.prompter._cost_ratings == 1


def test_skipped_cost_is_not_a_cost_rating(col, recording, owner, store):
    recording(Rigged(cost=True), stop_after_cost_ratings=1)
    for _ in range(2):
        add_note(col)
    for _ in range(2):
        card, states = show(col)
        answer(col, card, states, CardAnswer.EASY)
    assert len(prompt_events(store)) == 2


def test_cost_ratings_survive_a_restart(col, recording, owner, store):
    recording(Rigged(cost=True), stop_after_cost_ratings=1)
    owner.keys.append((Key.Key_Z, Key.Key_Q, Key.Key_Return))
    add_note(col)
    card, states = show(col)
    answer(col, card, states)
    recorder = recording(Rigged(cost=True), stop_after_cost_ratings=1)
    assert recorder.prompter._cost_ratings == 1


def test_a_failing_prompt_is_logged_and_the_answer_stands(
    col, recording, owner, store, prompts, monkeypatch,
):
    def fail(*args):
        raise RuntimeError("no")

    monkeypatch.setattr(prompts, "RatingDialog", fail)
    recording(Rigged(cost=True))
    add_note(col)
    card, states = show(col)
    answer(col, card, states)
    assert len(events(store, "ans")) == 1
    assert store.error_count() == 1


# Timing
###########################################################################


def fake_reviewer(col, card, order):
    """Just enough of a Reviewer to run Anki's own answer tail on it."""
    reviewer = SimpleNamespace(
        mw=SimpleNamespace(col=col),
        card=card,
        _v3=None,
        _answeredIds=[],
        check_timebox=lambda: False,
    )

    def next_card():
        order.append("nextCard")
        assert QApplication.activeModalWidget() is None
        Reviewer._get_next_v3_card(reviewer)

    reviewer.nextCard = next_card
    return reviewer


def test_next_card_timer_starts_after_the_prompt_closes(
    col, recording, owner, store,
):
    recording(Rigged(cost=True))
    add_note(col, "first")
    add_note(col, "second")
    card, states = show(col)
    order = []
    reviewer = fake_reviewer(col, card, order)

    opened_at = []

    def on_tick():
        if not opened_at and QApplication.activeModalWidget():
            order.append("prompt open")
            opened_at.append(time.time())

    watcher = QTimer()
    watcher.setInterval(1)
    watcher.timeout.connect(on_tick)
    watcher.start()
    owner._timer.setInterval(120)  # the owner takes a moment to rate
    owner.keys.append((Key.Key_C, Key.Key_Q, Key.Key_Return))

    gui_hooks.reviewer_will_answer_card((True, 3), reviewer, card)
    col.sched.answer_card(
        col.sched.build_answer(card=card, states=states, rating=CardAnswer.GOOD)
    )
    card.load()
    Reviewer._after_answering(reviewer, 3)
    watcher.stop()

    assert order == ["prompt open", "nextCard"]
    prompt = prompt_events(store)[0]
    assert reviewer.card.id != card.id
    # The timer started after the rating was logged, so it holds none of it.
    assert reviewer.card.timer_started * 1000 >= prompt["ts"]
    assert reviewer.card.timer_started - opened_at[0] >= 0.1
    assert reviewer.card.time_taken() < 1000


# Baseline
###########################################################################


def test_baseline_silences_answer_and_sharpness_prompts(
    col, recorder_module, store, owner, app,
):
    now = int(time.time() * 1000)
    store.update_state(recording=True, recording_since=now - 13 * DAY * 1000)
    mw = FakeMw(col, "overview")
    recorder = recorder_module.start_recording(mw, store)
    recorder.prompter._rng = Rigged(cost=True, why=True)
    add_note(col)
    gui_hooks.state_will_change("review", "overview")
    card, states = show(col)
    answer(col, card, states)
    assert prompt_events(store) == [] and owner.titles == []


def test_prompts_start_by_themselves_when_the_baseline_ends(
    col, recorder_module, store, owner, app,
):
    now = int(time.time() * 1000)
    store.update_state(recording=True, recording_since=now - 14 * DAY * 1000)
    mw = FakeMw(col, "overview")
    recorder = recorder_module.start_recording(mw, store)
    recorder.prompter._rng = Rigged(cost=True)
    add_note(col)
    gui_hooks.state_will_change("review", "overview")
    card, states = show(col)
    answer(col, card, states)
    assert [event["kind"] for event in prompt_events(store)] == ["sharpness", "answer"]


# Sharpness
###########################################################################


def test_sharpness_comes_before_the_first_card_of_a_block(
    col, recording, owner, store,
):
    recorder = recording(Rigged())
    recorder.prompter._last_activity_ms = 0
    owner.keys.append((Key.Key_4, Key.Key_Return))
    gui_hooks.state_will_change("review", "overview")
    event = prompt_events(store)[0]
    assert event["kind"] == "sharpness"
    assert event["answers"] == {"sharpness": 4}
    assert event["late"] is False
    assert event["p"] == 1


def test_sharpness_waits_for_the_gap(col, recording, owner, store):
    recorder = recording(Rigged())
    hours = lambda count: int(time.time() * 1000) - count * HOUR * 1000
    recorder.prompter._last_activity_ms = hours(1.9)
    gui_hooks.state_will_change("review", "overview")
    assert prompt_events(store) == []
    recorder.prompter._last_activity_ms = hours(2)
    gui_hooks.state_will_change("review", "overview")
    assert len(prompt_events(store)) == 1


def test_sharpness_asks_once_per_block(col, recording, owner, store):
    recorder = recording(Rigged())
    recorder.prompter._last_activity_ms = 0
    add_note(col)
    gui_hooks.state_will_change("review", "overview")
    card, states = show(col)
    answer(col, card, states)
    gui_hooks.state_will_change("overview", "review")
    gui_hooks.state_will_change("review", "overview")
    assert len(prompt_events(store)) == 1


def test_sharpness_ignores_other_states(col, recording, owner, store):
    recorder = recording(Rigged())
    recorder.prompter._last_activity_ms = 0
    gui_hooks.state_will_change("overview", "deckBrowser")
    assert prompt_events(store) == []


def test_answers_synced_in_keep_a_block_open(col, recording, owner, store):
    recorder = recording(Rigged())
    add_note(col)
    review_once(col)  # as if from the phone, unseen by the recorder
    recorder.prompter._last_activity_ms = 0
    gui_hooks.state_will_change("review", "overview")
    assert prompt_events(store) == []


def test_late_sharpness_when_a_gap_passes_inside_the_reviewer(
    col, recording, owner, store,
):
    recorder = recording(Rigged(cost=True))
    owner.keys.extend(((Key.Key_X, Key.Key_W, Key.Key_Return), (Key.Key_3, Key.Key_Return)))
    add_note(col)
    recorder.prompter._last_activity_ms = 1
    card, states = show(col)
    answer(col, card, states)
    assert [event["kind"] for event in prompt_events(store)] == ["answer", "sharpness"]
    assert prompt_events(store)[1]["late"] is True
    assert prompt_events(store)[1]["answers"] == {"sharpness": 3}


def test_late_sharpness_needs_no_sampled_answer(col, recording, owner, store):
    recorder = recording(Rigged())
    add_note(col)
    recorder.prompter._last_activity_ms = 1
    card, states = show(col)
    answer(col, card, states)
    assert prompt_events(store)[0]["late"] is True


def test_sharpness_counts_towards_the_daily_cap(col, recording, owner, store):
    recorder = recording(Rigged(), daily_cap=1)
    recorder.prompter._last_activity_ms = 0
    gui_hooks.state_will_change("review", "overview")
    recorder.prompter._last_activity_ms = 0
    gui_hooks.state_will_change("review", "overview")
    assert len(prompt_events(store)) == 1


# Day rating
###########################################################################


class Revlog:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("CREATE TABLE revlog (id INTEGER PRIMARY KEY, ease)")

    def add(self, seconds, count=1, ease=3):
        self.connection.executemany(
            "INSERT INTO revlog VALUES (?, ?)",
            [(int(seconds * 1000) + index, ease) for index in range(count)],
        )

    def scalar(self, sql, *args):
        row = self.connection.execute(sql, args).fetchone()
        return row[0] if row else None


class Clock:
    def __init__(self, monkeypatch):
        self.now = NOW
        monkeypatch.setattr(time, "time", lambda: self.now)


@pytest.fixture
def clock(monkeypatch):
    return Clock(monkeypatch)


@pytest.fixture
def day(app, prompts, store, clock):
    """A prompter on a collection with a movable day, plus what it logs."""
    logged = []
    revlog = Revlog()
    mw = FakeMw(SimpleNamespace(
        db=revlog, sched=SimpleNamespace(day_cutoff=CUTOFF),
    ))
    store.update_state(recording=True)

    def log(event, /, **fields):
        logged.append(fields)

    def make():
        return prompts.Prompter(mw, store, log, Rigged())

    return SimpleNamespace(
        mw=mw, revlog=revlog, logged=logged, make=make, clock=clock, store=store,
    )


YESTERDAY_NOON = NOW - DAY


def evening_at(day, moment):
    """Make the evening begin at that moment of the current study day."""
    start = day.mw.col.sched.day_cutoff - DAY
    hours = (moment - start) // HOUR
    hour = (datetime.fromtimestamp(start).hour + hours) % 24
    day.store.update_state(day_rating_hour=hour)


def leave_the_reviewer(prompter):
    prompter.on_state_will_change("deckBrowser", "review")


@pytest.fixture
def studied(day):
    """A study day of 25 answers, now in its evening."""
    day.revlog.add(NOW - 3600, 25)
    evening_at(day, NOW)
    return day


def test_leaving_the_reviewer_in_the_evening_rates_today(studied, owner):
    owner.keys.append((Key.Key_2, Key.Key_T, Key.Key_Return))
    prompter = studied.make()
    prompter.start()
    leave_the_reviewer(prompter)
    assert owner.titles == ["Today's study, 2026-11-05"]
    event = studied.logged[0]
    assert event["kind"] == "day"
    assert event["answers"] == {"tiring": 2, "learned": 5}
    assert event["skipped"] == []
    assert event["p"] == 1
    assert event["day_end_ms"] == CUTOFF * 1000
    assert event["day_answers"] == 25
    assert studied.store.load_state()["day_rated_through_ms"] == CUTOFF * 1000


def test_no_study_today_means_no_question(day, owner):
    # What prompted this design: asked about a day before studying a card.
    day.revlog.add(YESTERDAY_NOON, 25)
    evening_at(day, NOW)
    prompter = day.make()
    prompter.start()
    leave_the_reviewer(prompter)
    assert day.logged == []


def test_nineteen_answers_earn_no_question(day, owner):
    day.revlog.add(NOW - 60, 19)
    evening_at(day, NOW)
    prompter = day.make()
    prompter.start()
    leave_the_reviewer(prompter)
    assert day.logged == []


def test_manual_revlog_rows_are_not_answers(day, owner):
    day.revlog.add(NOW - 60, 19)
    day.revlog.add(NOW - 100, 5, ease=0)
    evening_at(day, NOW)
    prompter = day.make()
    prompter.start()
    leave_the_reviewer(prompter)
    assert day.logged == []


def test_before_the_evening_it_waits_for_a_later_exit(studied, owner):
    prompter = studied.make()
    prompter.start()
    studied.clock.now = NOW - 3600
    leave_the_reviewer(prompter)
    assert studied.logged == []
    studied.clock.now = NOW
    leave_the_reviewer(prompter)
    assert [event["kind"] for event in studied.logged] == ["day"]


def test_past_midnight_is_still_that_evening(studied, owner):
    prompter = studied.make()
    prompter.start()
    studied.clock.now = CUTOFF - 60
    leave_the_reviewer(prompter)
    assert studied.logged[0]["day_end_ms"] == CUTOFF * 1000


def test_finishing_the_queue_goes_through_the_overview_first(studied, owner):
    prompter = studied.make()
    prompter.start()
    prompter.on_state_will_change("overview", "review")
    assert studied.logged == []
    prompter.on_state_will_change("deckBrowser", "overview")
    assert [event["kind"] for event in studied.logged] == ["day"]


def test_browsing_decks_without_reviewing_never_asks(studied, owner):
    prompter = studied.make()
    prompter.start()
    prompter.on_state_will_change("deckBrowser", "startup")
    prompter.on_state_will_change("overview", "deckBrowser")
    prompter.on_state_will_change("deckBrowser", "overview")
    assert studied.logged == []


def test_a_day_is_asked_once(studied, owner):
    prompter = studied.make()
    prompter.start()
    leave_the_reviewer(prompter)
    leave_the_reviewer(prompter)
    studied.make().start()
    assert len(studied.logged) == 1


def test_the_next_evening_asks_again(studied, owner):
    prompter = studied.make()
    prompter.start()
    leave_the_reviewer(prompter)
    studied.clock.now = NOW + DAY
    studied.mw.col.sched.day_cutoff = CUTOFF + DAY
    studied.revlog.add(NOW + DAY - 3600, 25)
    leave_the_reviewer(prompter)
    assert [event["day_end_ms"] for event in studied.logged] == [
        CUTOFF * 1000, (CUTOFF + DAY) * 1000,
    ]


def test_esc_settles_the_day(studied, owner):
    prompter = studied.make()
    prompter.start()
    leave_the_reviewer(prompter)
    assert studied.logged[0]["skipped"] == ["tiring", "learned"]
    leave_the_reviewer(prompter)
    assert len(studied.logged) == 1


def test_rating_keeps_its_note(studied, owner, prompts, monkeypatch):
    class Noted(prompts.RatingDialog):
        def note(self):
            return "long session"

    monkeypatch.setattr(prompts, "RatingDialog", Noted)
    owner.keys.append((Key.Key_1, Key.Key_Q, Key.Key_Return))
    prompter = studied.make()
    prompter.start()
    leave_the_reviewer(prompter)
    assert studied.logged[0]["note"] == "long session"


def test_rating_leaves_the_drawing_to_the_state_change(studied, owner):
    prompter = studied.make()
    prompter.start()
    leave_the_reviewer(prompter)
    assert studied.mw.refreshes == []


def test_day_rating_starts_on_day_one_inside_the_baseline(studied, owner):
    studied.store.update_state(recording_since=NOW * 1000, baseline_days=14)
    prompter = studied.make()
    prompter.start()
    leave_the_reviewer(prompter)
    assert [event["kind"] for event in studied.logged] == ["day"]


def test_day_rating_outlives_the_answer_prompts(studied, owner):
    studied.store.update_state(stop_after_cost_ratings=0, baseline_days=0)
    prompter = studied.make()
    prompter.start()
    leave_the_reviewer(prompter)
    assert [event["kind"] for event in studied.logged] == ["day"]


def test_phone_answers_count_towards_the_day(day, owner):
    evening_at(day, NOW)
    prompter = day.make()
    prompter.start()
    day.revlog.add(NOW - 60, 25)  # arrives with a sync
    leave_the_reviewer(prompter)
    assert [event["kind"] for event in day.logged] == ["day"]


def test_rollover_resets_the_daily_cap(day):
    prompter = day.make()
    prompter._shown_today = 30
    prompter.on_new_day()
    assert prompter._shown_today == 0


# The machine slept through rollover, so Anki's timer has not fired yet, and
# yesterday's count must not hold back whatever happens first.


@pytest.fixture
def overnight(recording):
    """A day that used its whole cap, and a rollover nothing has noticed yet."""
    recorder = recording(Rigged())
    recorder.prompter._shown_today = 30
    recorder._day_cutoff = time.time() - 1
    return recorder


def test_a_state_change_notices_a_missed_rollover(overnight, store):
    gui_hooks.state_will_change("overview", "deckBrowser")
    assert overnight.prompter._shown_today == 0
    assert len(events(store, "meta")) == 2


def test_an_answer_left_on_screen_overnight_notices_it_too(overnight, col, store):
    add_note(col)
    card, states = show(col)
    overnight._day_cutoff = time.time() - 1  # show() noticed it: it happens after
    overnight.prompter._shown_today = 30
    answer(col, card, states)
    assert overnight.prompter._shown_today == 0


# Day rating through the recorder hooks
###########################################################################


def add_today(col, count=25):
    # Tests own this throwaway collection: the add-on itself only reads.
    start = (col.sched.day_cutoff - DAY + HOUR) * 1000
    for index in range(count):
        col.db.execute(
            "INSERT INTO revlog VALUES (?, 1, -1, 3, 1, 1, 2500, 1000, 1)", start + index,
        )


def every_hour_is_evening(store, col):
    """day_rating_hour at the study day's own start: the evening is all of it."""
    start = col.sched.day_cutoff - DAY
    store.update_state(day_rating_hour=datetime.fromtimestamp(start).hour)


def test_opening_the_profile_never_asks(col, recorder_module, store, owner, app):
    add_today(col)
    every_hour_is_evening(store, col)
    store.update_state(recording=True)
    recorder_module.start_recording(FakeMw(col), store)
    assert prompt_events(store) == []


def test_regaining_focus_never_asks(col, recorder_module, store, owner, app):
    add_today(col)
    every_hour_is_evening(store, col)
    store.update_state(recording=True)
    recorder_module.start_recording(FakeMw(col), store)
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert prompt_events(store) == []


def test_the_d_key_path_asks_through_the_real_hook(col, recording, owner, store):
    add_today(col)
    every_hour_is_evening(store, col)
    recording(Rigged())
    gui_hooks.state_will_change("deckBrowser", "review")
    assert [event["kind"] for event in prompt_events(store)] == ["day"]


def test_rollover_alone_never_asks(col, recording, owner, store):
    add_today(col)
    every_hour_is_evening(store, col)
    recorder = recording(Rigged())
    recorder._day_cutoff = time.time() - 1
    gui_hooks.day_did_change()
    assert owner.titles == []


def test_rollover_is_noticed_without_the_hook(
    col, recorder_module, store, owner, app, monkeypatch,
):
    monkeypatch.delattr(gui_hooks, "day_did_change")
    store.update_state(recording=True)
    recorder = recorder_module.start_recording(FakeMw(col, "review"), store)
    add_note(col)
    recorder._day_cutoff = time.time() - 1
    show(col)
    assert len(events(store, "meta")) == 2


# Normal users
###########################################################################


@pytest.fixture
def renderer(setup, fake_mw):
    module = setup.modules.renderer
    mw = SimpleNamespace(col=setup.col, pm=fake_mw.pm)
    instance = module.HeatmapRenderer(mw, setup.reporter, setup.conf)
    instance.view = module.HeatmapView.deckbrowser
    return instance


def calibration_store(fake_mw):
    from review_heatmap.calibration.store import Store

    return Store.for_profile(fake_mw.pm.profileFolder())


def test_the_heatmap_never_looks_at_calibration(dormant, fake_mw, renderer, monkeypatch):
    def no_folder():
        raise AssertionError("no file access while drawing")

    monkeypatch.setattr(fake_mw.pm, "profileFolder", no_folder)
    renderer._mw = SimpleNamespace(col=renderer._mw.col, pm=fake_mw.pm)
    assert renderer.render(renderer.view)
    assert loaded_calibration_modules() == []


# Tab
###########################################################################


@pytest.fixture
def tab(app, recorder_module, fake_mw):
    from review_heatmap.gui import calibration_tab

    return calibration_tab.CalibrationTab(fake_mw)


def test_tab_settings_save_at_once_and_reach_the_prompter(
    col, tab, recorder_module, fake_mw, monkeypatch,
):
    monkeypatch.setattr(fake_mw, "col", col)
    store = tab._store
    recorder = recorder_module.start_recording(fake_mw, store)
    assert tab.settingBoxes["daily_cap"].value() == 30
    tab.settingBoxes["daily_cap"].setValue(12)
    tab.settingBoxes["answer_rate"].setValue(0.1)
    state = store.load_state()
    assert state["daily_cap"] == 12
    assert state["answer_rate"] == pytest.approx(0.1)
    assert recorder.prompter._state["daily_cap"] == 12


def test_tab_shows_every_default_setting(tab):
    from review_heatmap.calibration.store import DEFAULT_STATE

    for key, box in tab.settingBoxes.items():
        assert box.value() == pytest.approx(DEFAULT_STATE[key])
    assert set(tab.settingBoxes) == {
        "baseline_days", "answer_rate", "lapse_reason_rate", "block_gap_hours",
        "day_rating_hour", "daily_cap", "stop_after_cost_ratings",
    }


def test_tab_counts_ratings_and_skips(app, recorder_module, fake_mw):
    from review_heatmap.gui import calibration_tab

    store = calibration_store(fake_mw)
    rows = [
        ("answer", {"cost": 1, "gain": 0}, []),
        ("answer", {"cost": 2, "grasp": 3}, []),
        ("answer", {"why": 1}, []),
        ("answer", {}, ["cost", "gain"]),
        ("sharpness", {"sharpness": 4}, []),
        ("day", {"tiring": 2, "learned": 3}, []),
    ]
    for kind, answers, skipped in rows:
        store.append({
            "v": 1, "t": "prompt", "ts": 1793836800000, "kind": kind,
            "answers": answers, "skipped": skipped,
        })
    store.close()
    text = calibration_tab.CalibrationTab(fake_mw).labPrompts.text()
    for line in (
        "Cost ratings: 2/800", "Gain: 1", "Grasp: 1", "Again reasons: 1",
        "Sharpness: 1", "Day ratings: 1", "Skip rate: 17%",
    ):
        assert line in text
