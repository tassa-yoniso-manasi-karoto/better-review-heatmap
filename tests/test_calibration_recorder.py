import importlib
import json
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("anki")
pytest.importorskip("aqt")

from anki.cards import Card
from anki.collection import Collection
from anki.scheduler.v3 import CardAnswer
from anki.sound import SoundOrVideoTag, TTSTag
from aqt import gui_hooks
from aqt.qt import QApplication, Qt

from test_activity import setup  # noqa: F401
from test_options import options_module  # noqa: F401

CALIBRATION = "review_heatmap.calibration"
GOOD = CardAnswer.GOOD


@pytest.fixture
def recorder_module(addon_modules):
    module = importlib.import_module(CALIBRATION + ".recorder")
    yield module
    module.stop_recording()


@pytest.fixture
def store(tmp_path):
    from review_heatmap.calibration.store import Store

    store = Store(tmp_path / "data")
    yield store
    store.close()


@pytest.fixture
def col(tmp_path):
    col = Collection(str(tmp_path / "collection.anki2"))
    col.set_config("fsrs", True)
    yield col
    col.close()


@pytest.fixture
def recorder(recorder_module, col, store):
    return recorder_module.start_recording(SimpleNamespace(col=col), store)


def add_note(col, front="front", back="back"):
    note = col.new_note(col.models.by_name("Basic"))
    note["Front"] = front
    note["Back"] = back
    col.add_note(note, 1)
    return note


def present(col):
    """Fetch the next card as Reviewer does, rendering both sides."""
    queued = col.sched.get_queued_cards().cards[0]
    card = Card(col, backend_card=queued.card)
    card.start_timer()
    card.question()
    card.answer()
    return card, queued.states


def show(col):
    card, states = present(col)
    gui_hooks.reviewer_did_show_question(card)
    gui_hooks.reviewer_did_show_answer(card)
    return card, states


def answer(col, card, states, rating=GOOD):
    """Answer in Reviewer._answerCard's order. Anki's eases are ratings + 1."""
    ease = rating + 1
    gui_hooks.reviewer_will_answer_card((True, ease), None, card)
    col.sched.answer_card(
        col.sched.build_answer(card=card, states=states, rating=rating)
    )
    card.load()
    gui_hooks.reviewer_did_answer_card(None, card, ease)


def events(store, kind=None):
    return [
        event for event in store.read_events() if kind is None or event["t"] == kind
    ]


def review_once(col):
    """Turn a new card into a review card that is due again."""
    card, states = present(col)
    col.sched.answer_card(
        col.sched.build_answer(card=card, states=states, rating=CardAnswer.EASY)
    )
    col.sched.set_due_date([card.id], "0")


# Field names, against the real Anki
###########################################################################


def test_question_event_of_a_new_card(col, recorder, store):
    note = add_note(col)
    card, _ = show(col)
    event = events(store, "q")[0]
    assert event["v"] == 1
    assert event["run"] == recorder.run
    assert event["seq"] == 1
    assert (event["cid"], event["nid"]) == (card.id, note.id)
    assert (event["did"], event["odid"]) == (1, 0)
    assert event["mid"] == note.mid
    assert (event["ord"], event["ctype"], event["queue"]) == (0, 0, 0)
    assert (event["ivl"], event["reps"], event["lapses"]) == (0, 0, 0)
    assert event["left"] == card.left
    assert event["memory"] is None
    assert event["last_review_time"] is None
    assert event["max_taken_ms"] == card.time_limit() == 60000
    assert event["preset"] == 1
    assert event["note_mod"] == col.get_note(note.id).mod
    assert (event["q_chars"], event["a_chars"]) == (5, 9)
    assert (event["q_media"], event["a_media"]) == (0, 0)


def test_question_event_reads_the_memory_state_before_the_answer(col, recorder, store):
    add_note(col)
    review_once(col)
    card, states = show(col)
    answer(col, card, states, CardAnswer.AGAIN)
    before, after = events(store, "q")[0], events(store, "ans")[0]
    shown = Card(col, card.id)
    assert before["ctype"] == 2
    assert before["memory"]["s"] > 0 and before["memory"]["d"] > 0
    assert 0 < before["desired_retention"] < 1
    assert 0 < before["decay"] < 1
    assert before["last_review_time"] > 0
    assert after["memory_after"] == {
        "s": shown.memory_state.stability, "d": shown.memory_state.difficulty,
    }
    assert after["memory_after"] != before["memory"]
    assert after["ctype_after"] == shown.type
    assert after["ivl_after"] == shown.ivl


def test_answer_event_joins_the_revlog_and_keeps_both_times(col, recorder, store):
    note = add_note(col)
    card, states = show(col)
    card.timer_started -= 100  # past the preset's 60 s cap
    answer(col, card, states, CardAnswer.AGAIN)
    event = events(store, "ans")[0]
    assert event["run"] == recorder.run
    assert (event["seq"], event["cid"], event["ease"]) == (1, card.id, 1)
    assert event["rev_id"] == col.db.scalar(
        "SELECT id FROM revlog WHERE cid=?", card.id,
    )
    assert event["taken_capped_ms"] == 60000
    assert 100000 <= event["taken_raw_ms"] < 110000
    assert event["note_mod_after"] == col.get_note(note.id).mod
    # Anki records the same capped time.
    assert event["taken_capped_ms"] == col.db.scalar(
        "SELECT time FROM revlog WHERE id=?", event["rev_id"],
    )


def test_note_mod_after_sees_an_edit_made_during_the_answer(col, recorder, store):
    note = add_note(col)
    card, states = show(col)
    time.sleep(1.1)  # note.mod has a resolution of one second
    note["Back"] = "changed"
    col.update_note(note)
    answer(col, card, states)
    assert events(store, "ans")[0]["note_mod_after"] > events(store, "q")[0]["note_mod"]


def test_undone_answer_loses_its_join_key_and_redo_restores_it(col, recorder, store):
    add_note(col)
    card, states = show(col)
    answer(col, card, states)
    rev_id = events(store, "ans")[0]["rev_id"]
    # Reading with the recorder left the undo history intact.
    assert col.undo_status().undo
    gui_hooks.state_did_undo(col.undo())
    assert col.db.scalar("SELECT count() FROM revlog WHERE id=?", rev_id) == 0
    assert events(store, "undo")[0]["op"] == "Answer Card"
    col.redo()
    assert col.db.scalar("SELECT count() FROM revlog WHERE id=?", rev_id) == 1


def test_filtered_preview_still_gets_its_own_join_key(col, recorder, store):
    note = add_note(col)
    deck = col.sched.get_or_create_filtered_deck(0)
    deck.name = "Preview"
    deck.config.reschedule = False
    term = deck.config.search_terms.add()
    term.search = f"nid:{note.id}"
    term.limit = 10
    col.decks.set_current(col.sched.add_or_update_filtered_deck(deck).id)
    for rating in (CardAnswer.AGAIN, CardAnswer.AGAIN):
        card, states = show(col)
        answer(col, card, states, rating)
    first, second = events(store, "ans")
    assert first["rev_id"] and second["rev_id"] > first["rev_id"]
    assert events(store, "q")[0]["preset"] == 1  # the home deck's, by odid


def test_no_card_text_reaches_the_log(col, recorder, store):
    add_note(col, "secretfront", "secretback")
    card, states = show(col)
    answer(col, card, states)
    gui_hooks.audio_will_replay(None, card, True)
    gui_hooks.av_player_did_begin_playing(None, SoundOrVideoTag("secret.mp3"))
    store.close()
    text = "".join(
        path.read_text() for path in store.folder.glob("events-*.jsonl")
    )
    assert '"t":"audio"' in text
    assert "secret" not in text


def test_sizes_ignore_embedded_code_and_count_media(col, recorder, store):
    add_note(
        col,
        "<script>var hidden = '<b>' + 1 < 2;</script><STYLE>b {}</STYLE>"
        "<b>a b</b><img src=x.png>[sound:x.mp3]",
        "<script>never closed",
    )
    show(col)
    event = events(store, "q")[0]
    assert event["q_chars"] == 2
    assert event["q_media"] == 2
    # The back holds the front through {{FrontSide}}.
    assert event["a_chars"] == 2
    assert event["a_media"] == 2


# Sequence numbers
###########################################################################


def test_redraw_keeps_the_presentation_and_a_new_timer_starts_one(col, recorder, store):
    add_note(col)
    card, states = present(col)
    gui_hooks.reviewer_did_show_question(card)
    gui_hooks.reviewer_did_show_question(card)  # e.g. after an edit
    gui_hooks.reviewer_did_show_answer(card)
    answer(col, card, states)
    gui_hooks.state_did_undo(col.undo())
    show(col)  # the same card again, after the undo
    assert [(event["t"], event["seq"]) for event in events(store) if "seq" in event] == [
        ("q", 1), ("q", 1), ("a", 1), ("ans", 1), ("q", 2), ("a", 2),
    ]


def test_seq_restarts_under_a_new_run_id(recorder_module, col, store):
    add_note(col)
    first = recorder_module.start_recording(SimpleNamespace(col=col), store)
    show(col)
    second = recorder_module.start_recording(SimpleNamespace(col=col), store)
    show(col)
    assert first.run != second.run
    assert [(event["run"], event["seq"]) for event in events(store, "q")] == [
        (first.run, 1), (second.run, 1),
    ]


def test_card_already_on_screen_at_start_gets_no_seq(recorder_module, col, store):
    add_note(col)
    card, states = present(col)
    recorder_module.start_recording(SimpleNamespace(col=col), store)
    gui_hooks.reviewer_did_show_answer(card)
    answer(col, card, states)
    assert events(store, "a")[0]["seq"] is None
    event = events(store, "ans")[0]
    assert event["seq"] is None
    assert event["rev_id"]


def test_answer_without_the_will_answer_capture_has_no_times(col, recorder, store):
    add_note(col)
    card, _ = show(col)
    gui_hooks.reviewer_will_answer_card((True, 3), None, card)
    other = add_note(col)
    stale = Card(col, other.card_ids()[0])
    gui_hooks.reviewer_did_answer_card(None, stale, 3)
    event = events(store, "ans")[0]
    assert event["taken_raw_ms"] is None and event["taken_capped_ms"] is None


# Handlers never raise into Anki
###########################################################################


def test_raising_handler_logs_and_stays_installed(
    recorder_module, col, recorder, store, monkeypatch,
):
    add_note(col)
    card, _ = present(col)
    hooks = gui_hooks.reviewer_did_show_question.count()

    def explode(card):
        raise ValueError("boom")

    monkeypatch.setattr(recorder_module, "question_fields", explode)
    gui_hooks.reviewer_did_show_question(card)
    assert gui_hooks.reviewer_did_show_question.count() == hooks
    assert store.error_count() == 1
    entry = json.loads((store.folder / "errors.log").read_text())
    assert entry["where"] == "_on_show_question"
    assert "ValueError: boom" in entry["details"]
    # The rest of the card's events still arrive.
    gui_hooks.reviewer_did_show_answer(card)
    monkeypatch.undo()
    gui_hooks.reviewer_did_show_question(card)
    assert len(events(store, "a")) == 1 and len(events(store, "q")) == 1


def test_unwritable_log_does_not_raise_either(col, recorder, store, monkeypatch):
    add_note(col)
    card, _ = present(col)

    def full_disk(line):
        raise OSError("No space left on device")

    monkeypatch.setattr(store, "append", full_disk)
    monkeypatch.setattr(store.__class__, "_errors_path", Path("/nonexistent/x/y"))
    gui_hooks.reviewer_did_show_question(card)


def test_will_answer_returns_its_input_unchanged(col, recorder, store):
    add_note(col)
    card, _ = present(col)
    for value in ((True, 3), (False, 1)):
        assert gui_hooks.reviewer_will_answer_card(value, None, card) is value


def test_will_answer_returns_its_input_when_it_fails(col, recorder, store):
    add_note(col)
    card, _ = present(col)
    card.timer_started = None  # time_taken() raises TypeError
    value = (True, 3)
    hooks = gui_hooks.reviewer_will_answer_card.count()
    assert gui_hooks.reviewer_will_answer_card(value, None, card) is value
    assert gui_hooks.reviewer_will_answer_card.count() == hooks
    assert store.error_count() == 1


# Rollover
###########################################################################


def test_start_writes_meta(col, recorder, store):
    event = events(store, "meta")[0]
    assert event["run"] == recorder.run
    assert event["anki_version"] == "26.09.3"
    assert event["addon_version"]
    assert event["platform"]
    assert event["rollover"] == 4
    assert event["day_cutoff"] == col.sched.day_cutoff
    assert isinstance(event["utc_offset_min"], int)


def test_rollover_inside_the_reviewer_is_noticed_on_the_next_card(col, recorder, store):
    # Anki 26.09.3 fires no day_did_change in this case.
    add_note(col)
    recorder._day_cutoff = time.time() - 1
    show(col)
    show(col)
    assert len(events(store, "meta")) == 2


def test_repeated_day_did_change_logs_one_rollover(col, recorder, store):
    gui_hooks.day_did_change()
    assert len(events(store, "meta")) == 1
    recorder._day_cutoff = time.time() - 1
    gui_hooks.day_did_change()
    gui_hooks.day_did_change()
    assert len(events(store, "meta")) == 2


# Audio, focus
###########################################################################


def test_audio_logs_the_tag_type_only(col, recorder, store):
    add_note(col)
    card, _ = present(col)
    gui_hooks.av_player_did_begin_playing(None, SoundOrVideoTag("word.mp3"))
    gui_hooks.av_player_did_begin_playing(
        None, TTSTag("text", ["th_TH"], [], 1.0, None),
    )
    gui_hooks.av_player_did_end_playing(None)
    gui_hooks.audio_will_replay(None, card, False)
    logged = [
        {key: value for key, value in event.items() if key not in ("v", "t", "ts", "run")}
        for event in events(store, "audio")
    ]
    assert logged == [
        {"kind": "begin", "tag": "SoundOrVideoTag"},
        {"kind": "begin", "tag": "TTSTag"},
        {"kind": "end"},
        {"kind": "replay", "cid": card.id, "front": False},
    ]


def test_focus_changes_are_logged_until_stop(recorder_module, col, store):
    app = QApplication.instance() or QApplication([])
    recorder_module.start_recording(SimpleNamespace(col=col), store)
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationInactive)
    recorder_module.stop_recording()
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    states = [event["state"] for event in events(store, "focus")]
    # The first one records the state at start.
    assert len(states) == 2
    assert states[1] == "ApplicationInactive"


# Lifecycle and devmode
###########################################################################


HOOKS = (
    "reviewer_did_show_question", "reviewer_did_show_answer",
    "reviewer_will_answer_card", "reviewer_did_answer_card",
    "av_player_did_begin_playing", "av_player_did_end_playing",
    "audio_will_replay", "state_did_undo", "day_did_change",
    "state_will_change", "profile_will_close",
)


def hook_counts():
    return {name: getattr(gui_hooks, name).count() for name in HOOKS}


def test_stop_removes_every_hook_it_added(recorder_module, col, store):
    before = hook_counts()
    recorder_module.start_recording(SimpleNamespace(col=col), store)
    recorder_module.stop_recording()
    after = hook_counts()
    # profile_will_close stays, see start_recording.
    before["profile_will_close"] = after["profile_will_close"]
    assert after == before
    assert recorder_module.active_recorder() is None


def test_closing_the_profile_stops_without_skipping_other_callbacks(
    recorder_module, col, store,
):
    called = []
    recorder_module.start_recording(SimpleNamespace(col=col), store)
    # Run right after ours: the first one a removal during the run would skip.
    def after_ours():
        called.append(True)

    gui_hooks.profile_will_close.append(after_ours)
    try:
        gui_hooks.profile_will_close()
    finally:
        gui_hooks.profile_will_close.remove(after_ours)
    assert called == [True]
    assert recorder_module.active_recorder() is None


def test_closing_the_profile_never_raises(
    recorder_module, col, store, monkeypatch,
):
    recorder_module.start_recording(SimpleNamespace(col=col), store)

    def fail():
        raise OSError("gone")

    monkeypatch.setattr(store, "close", fail)
    gui_hooks.profile_will_close()
    assert store.error_count() == 1


@pytest.fixture
def fake_mw(addon_modules, monkeypatch, tmp_path):
    import aqt

    profile = {}
    monkeypatch.setattr(aqt.mw.pm, "profile", profile, raising=False)
    monkeypatch.setattr(
        aqt.mw.pm, "profileFolder", lambda: str(tmp_path / "profile"), raising=False,
    )
    return aqt.mw


@pytest.fixture
def dormant(addon_modules, monkeypatch):
    """Calibration as a fresh Anki start sees it: only __init__ is imported."""
    for name in list(sys.modules):
        if name.startswith(CALIBRATION + ".") or name.endswith(".calibration_tab"):
            monkeypatch.delitem(sys.modules, name)
    package = importlib.import_module(CALIBRATION)
    for name in ("store", "recorder"):
        monkeypatch.delattr(package, name, raising=False)
    return package


def loaded_calibration_modules():
    return sorted(
        name for name in sys.modules
        if name.startswith(CALIBRATION + ".") or name.endswith(".calibration_tab")
    )


def test_devmode_off_installs_nothing_and_imports_nothing(dormant, fake_mw):
    before = hook_counts()
    opened = gui_hooks.profile_did_open.count()
    dormant.initialize_calibration()
    try:
        assert gui_hooks.profile_did_open.count() == opened + 1
        gui_hooks.profile_did_open()
    finally:
        gui_hooks.profile_did_open.remove(dormant._on_profile_did_open)
    assert hook_counts() == before
    assert loaded_calibration_modules() == []
    assert not (Path(fake_mw.pm.profileFolder())).exists()


def test_devmode_on_but_not_recording_starts_nothing(dormant, fake_mw):
    fake_mw.pm.profile[dormant.DEVMODE_KEY] = True
    before = hook_counts()
    dormant._on_profile_did_open()
    assert hook_counts() == before
    assert "review_heatmap.calibration.recorder" not in sys.modules


def test_devmode_and_saved_recording_state_start_the_recorder(
    recorder_module, fake_mw, col, monkeypatch,
):
    from review_heatmap import calibration
    from review_heatmap.calibration.store import Store

    monkeypatch.setattr(fake_mw, "col", col)
    fake_mw.pm.profile[calibration.DEVMODE_KEY] = True
    Store.for_profile(fake_mw.pm.profileFolder()).update_state(recording=True)
    calibration._on_profile_did_open()
    assert recorder_module.active_recorder() is not None
    assert gui_hooks.reviewer_did_show_question.count() > 0


def test_failed_start_logs_instead_of_breaking_the_profile_open(
    recorder_module, fake_mw, monkeypatch,
):
    from review_heatmap import calibration
    from review_heatmap.calibration.store import Store

    def fail(*args):
        raise RuntimeError("no")

    monkeypatch.setattr(recorder_module, "start_recording", fail)
    fake_mw.pm.profile[calibration.DEVMODE_KEY] = True
    store = Store.for_profile(fake_mw.pm.profileFolder())
    store.update_state(recording=True)
    calibration._on_profile_did_open()
    assert store.error_count() == 1


def test_console_key_survives_saving_the_profile_settings(
    addon_modules, fake_mw, monkeypatch,
):
    from review_heatmap import calibration

    monkeypatch.setattr(
        fake_mw, "col", SimpleNamespace(setMod=lambda: None), raising=False,
    )
    manager = addon_modules.config.config
    fake_mw.pm.profile["heatmap"] = {"version": "x"}
    fake_mw.pm.profile[calibration.DEVMODE_KEY] = True
    manager.load(storage_name="profile")
    manager["profile"]["show_today_progress"] = False
    manager.save("profile")
    assert calibration.is_devmode() is True
    assert fake_mw.pm.profile["heatmap"]["show_today_progress"] is False


def test_devmode_needs_a_profile(addon_modules, monkeypatch):
    import aqt

    from review_heatmap import calibration

    monkeypatch.setattr(aqt.mw.pm, "profile", None, raising=False)
    assert calibration.is_devmode() is False


# Tab
###########################################################################


def test_options_show_no_tab_without_devmode(dormant, fake_mw, setup, options_module):
    from aqt.qt import QWidget

    module, app = options_module
    parent = QWidget()
    parent.col = setup.col
    dialog = module.RevHmOptions(setup.conf, parent)
    tabs = dialog.form.tabWidget
    labels = [tabs.tabText(index) for index in range(tabs.count())]
    assert "Calibration" not in labels
    assert loaded_calibration_modules() == []
    dialog.reject()


def test_tab_appears_before_about_and_stays_out_of_ok_cancel(
    recorder_module, fake_mw, setup, options_module, monkeypatch,
):
    from aqt.qt import QWidget

    from review_heatmap import calibration
    from review_heatmap.gui import calibration_tab

    module, app = options_module
    monkeypatch.setattr(fake_mw, "col", setup.col)
    setup.col.sched.day_cutoff = int(time.time()) + 3600
    setup.col.get_preferences = lambda: SimpleNamespace(
        scheduling=SimpleNamespace(rollover=4),
    )
    fake_mw.pm.profile[calibration.DEVMODE_KEY] = True
    parent = QWidget()
    parent.col = setup.col
    parent.pm = fake_mw.pm
    original = json.dumps(dict(setup.conf), sort_keys=True, default=str)
    dialog = module.RevHmOptions(setup.conf, parent)
    tabs = dialog.form.tabWidget
    labels = [tabs.tabText(index).replace("&", "") for index in range(tabs.count())]
    assert labels[-2:] == ["Calibration", "About"]
    tab = tabs.widget(labels.index("Calibration"))
    assert isinstance(tab, calibration_tab.CalibrationTab)
    assert "Not recording" in tab.labRecording.text()
    assert "Baseline days left: 14" in tab.labRecording.text()

    tab.btnRecording.click()
    assert recorder_module.active_recorder() is not None
    state = tab._store.load_state()
    assert state["recording"] is True and state["recording_since"]
    assert "Recording since" in tab.labRecording.text()
    assert tab.btnRecording.text() == "Stop Recording"
    # Cancel leaves recording running and writes no settings.
    dialog.reject()
    assert recorder_module.active_recorder() is not None
    assert json.dumps(dict(setup.conf), sort_keys=True, default=str) == original
    assert setup.conf.saves == []

    since = state["recording_since"]
    tab.btnRecording.click()
    assert recorder_module.active_recorder() is None
    state = tab._store.load_state()
    assert state["recording"] is False and state["recording_since"] == since
    assert tab.btnRecording.text() == "Start Recording"
    # A later start keeps the baseline going.
    tab.btnRecording.click()
    assert tab._store.load_state()["recording_since"] == since


def test_tab_counts_answers_and_errors(recorder_module, fake_mw, monkeypatch, tmp_path):
    from review_heatmap.calibration.store import Store
    from review_heatmap.gui import calibration_tab

    app = QApplication.instance() or QApplication([])
    store = Store.for_profile(fake_mw.pm.profileFolder())
    for _ in range(3):
        store.append({"v": 1, "t": "ans", "ts": 1793836800000})
    store.close()
    store.log_error("q", "details")
    opened = []
    monkeypatch.setattr(calibration_tab, "openFolder", opened.append)
    tab = calibration_tab.CalibrationTab(fake_mw)
    assert "Answers logged: 3" in tab.labRecording.text()
    assert "Errors: 1" in tab.labRecording.text()
    tab.btnDataFolder.click()
    assert opened == [str(store.folder)]


# Budget
###########################################################################


def test_answer_path_stays_under_five_ms_on_a_heavy_card(recorder_module, col, store):
    """200 fields and 600 KB of embedded CSS and JS, like the TH note type."""
    notetype = col.models.new("Heavy")
    for index in range(200):
        col.models.add_field(notetype, col.models.new_field(f"F{index}"))
    template = col.models.new_template("Card")
    code = "<script>" + "if (a < b) { foo('<div>'); }\n" * 10000 + "</script>"
    template["qfmt"] = "{{F0}}" + code
    template["afmt"] = "{{FrontSide}}{{F1}}" + code
    col.models.add_template(notetype, template)
    notetype["css"] = ".card { color: red; font-size: 12px; }\n" * 8000
    col.models.add_dict(notetype)
    note = col.new_note(col.models.by_name("Heavy"))
    for index in range(200):
        note[f"F{index}"] = "<ruby>漢<rt>かん</rt></ruby> " * 40
    col.add_note(note, 1)
    card_id = note.card_ids()[0]

    recorder_module.start_recording(SimpleNamespace(col=col), store)
    timings = []
    for _ in range(100):
        card = Card(col, card_id)
        card.start_timer()
        card.question()
        card.answer()
        started = time.perf_counter()
        gui_hooks.reviewer_did_show_question(card)
        gui_hooks.reviewer_did_show_answer(card)
        gui_hooks.reviewer_will_answer_card((True, 3), None, card)
        card.load()  # only runs because reviewer_did_answer_card has a listener
        gui_hooks.reviewer_did_answer_card(None, card, 3)
        timings.append(time.perf_counter() - started)
    assert store.error_count() == 0
    assert statistics.median(timings) < 0.005
