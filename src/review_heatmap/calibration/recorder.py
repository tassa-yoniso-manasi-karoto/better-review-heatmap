"""Silent recorder: turns reviewer hooks into raw events.

Devmode only. Events are raw on purpose, never computed features, so that any
later formula can re-derive what it needs. No card text is stored, only sizes.

See docs/calibration.md for the event schema, and prompts.py for the Anki timing.
"""

import platform
import re
import secrets
import time
import traceback
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from anki.buildinfo import version as anki_version
from aqt import gui_hooks
from aqt.qt import QApplication

from ..consts import ADDON
from .prompts import Prompter
from .store import Store, now_ms

EVENT_VERSION = 1

# The templates embed large JS and CSS. This unrolled form takes a sixth of
# the time of a lazy .*? on such cards, and every answer pays for it twice.
_RE_EMBEDDED = re.compile(
    r"<(script|style)\b[^>]*>[^<]*(?:<(?!/\1)[^<]*)*(?:</\1\s*>|\Z)", re.IGNORECASE
)
_RE_TAG = re.compile(r"<[^>]*>")
# Sounds are already [anki:play:...] placeholders in rendered cards.
_RE_MEDIA = re.compile(r"<(?:img|video|audio)\b|\[anki:play:", re.IGNORECASE)
_RE_SOUND = re.compile(r"\[anki:play:[^\]]*\]")


def _sizes(html: str) -> Tuple[int, int]:
    """Visible characters and media count of one rendered card side."""
    html = _RE_EMBEDDED.sub("", html)
    media = len(_RE_MEDIA.findall(html))
    text = _RE_SOUND.sub("", _RE_TAG.sub("", html))
    return len("".join(text.split())), media


def _memory(card) -> Optional[dict]:
    state = card.memory_state
    if state is None:
        return None
    return {"s": state.stability, "d": state.difficulty}


def question_fields(card) -> dict:
    """Describe a card before its answer. The reviewer has already rendered
    both sides and loaded the note, so this renders nothing."""
    conf = card.col.decks.config_dict_for_deck_id(card.current_deck_id())
    note = card.note()
    q_chars, q_media = _sizes(card.question())
    a_chars, a_media = _sizes(card.answer())
    return {
        "cid": card.id,
        "nid": card.nid,
        "did": card.did,
        "odid": card.odid,
        "mid": note.mid,
        "ord": card.ord,
        "ctype": card.type,
        "queue": card.queue,
        "ivl": card.ivl,
        "reps": card.reps,
        "lapses": card.lapses,
        "left": card.left,
        "memory": _memory(card),
        "desired_retention": card.desired_retention,
        "decay": card.decay,
        "last_review_time": card.last_review_time,
        "max_taken_ms": conf["maxTaken"] * 1000,
        "preset": conf["id"],
        "q_chars": q_chars,
        "a_chars": a_chars,
        "q_media": q_media,
        "a_media": a_media,
        "note_mod": note.mod,
    }


def answer_fields(card) -> dict:
    """Describe a card reloaded after its answer."""
    db = card.col.db
    return {
        "cid": card.id,
        # WARNING: keep the leading SELECT (see ActivityReporter.set_collection).
        "rev_id": db.scalar("SELECT max(id) FROM revlog WHERE cid=?", card.id),
        "ctype_after": card.type,
        "ivl_after": card.ivl,
        "memory_after": _memory(card),
        # Not card.note(): it would load the whole note just for this.
        "note_mod_after": db.scalar("SELECT mod FROM notes WHERE id=?", card.nid),
    }


class Recorder:
    def __init__(self, mw, store: Store):
        self._mw = mw
        self._store = store
        self.run = secrets.token_hex(6)  # seq restarts with every run
        self._seq = 0
        # (cid, timer start) of the card on screen: a redraw of the same card
        # fires the show hooks again, and must not look like a new presentation.
        self._shown: Optional[Tuple[int, Optional[float]]] = None
        # (cid, capped ms, raw ms, card type) from the moment of the answer.
        self._taken: Optional[Tuple[int, int, int, int]] = None
        self._day_cutoff = 0
        self._installed: List[Tuple[object, Callable]] = []
        self._focus_slot: Optional[Callable] = None
        self.prompter = Prompter(mw, store, self.emit)

    # Lifecycle
    #########################################################################

    def start(self) -> None:
        self._guarded(self._emit_meta)()
        self._guarded(self.prompter.start)()
        self._installed = [
            (hook, self._guarded(handler)) for hook, handler in self._hook_table()
        ]
        self._installed.append((
            gui_hooks.reviewer_will_answer_card,
            self._guarded_filter(self._on_will_answer),
        ))
        for hook, handler in self._installed:
            hook.append(handler)
        app = QApplication.instance()
        if app:
            self._focus_slot = self._guarded(self._on_focus)
            app.applicationStateChanged.connect(self._focus_slot)
            self._focus_slot(app.applicationState())

    def stop(self) -> None:
        self._guarded(self._uninstall)()

    def _uninstall(self) -> None:
        for hook, handler in self._installed:
            hook.remove(handler)
        self._installed = []
        app = QApplication.instance()
        if app and self._focus_slot:
            app.applicationStateChanged.disconnect(self._focus_slot)
        self._focus_slot = None
        self._store.close()

    def emit(self, kind: str, /, **fields) -> None:
        self._store.append({
            "v": EVENT_VERSION, "t": kind, "ts": now_ms(), "run": self.run, **fields,
        })

    def _hook_table(self) -> List[Tuple[object, Callable]]:
        hooks = [
            (gui_hooks.reviewer_did_show_question, self._on_show_question),
            (gui_hooks.reviewer_did_show_answer, self._on_show_answer),
            (gui_hooks.reviewer_did_answer_card, self._on_did_answer),
            (gui_hooks.av_player_did_begin_playing, self._on_audio_begin),
            (gui_hooks.av_player_did_end_playing, self._on_audio_end),
            (gui_hooks.audio_will_replay, self._on_audio_replay),
            (gui_hooks.state_did_undo, self._on_undo),
            (gui_hooks.state_will_change, self._on_state_will_change),
        ]
        day_did_change = getattr(gui_hooks, "day_did_change", None)
        if day_did_change:
            hooks.append((day_did_change, self._check_rollover))
        return hooks

    # A raising hook is removed and the error re-raised (see _aqt/hooks.py),
    # which would stop recording and interrupt the review.
    def _guarded(self, handler: Callable) -> Callable:
        def guarded(*args) -> None:
            try:
                handler(*args)
            except Exception:
                self._store.log_error(handler.__name__, traceback.format_exc())

        return guarded

    def _guarded_filter(self, handler: Callable) -> Callable:
        guarded = self._guarded(handler)

        def passthrough(value, *args):
            guarded(value, *args)
            return value

        return passthrough

    # Reviewer
    #########################################################################

    def _on_show_question(self, card) -> None:
        self._check_rollover()
        shown = (card.id, card.timer_started)
        if shown != self._shown:
            self._shown = shown
            self._seq += 1
        self.emit("q", seq=self._seq, **question_fields(card))

    def _on_show_answer(self, card) -> None:
        self.emit("a", seq=self._seq_of(card))

    def _on_will_answer(self, ease_tuple, reviewer, card) -> None:
        self._taken = (
            card.id, card.time_taken(), card.time_taken(capped=False), card.type,
        )

    def _on_did_answer(self, reviewer, card, ease) -> None:
        self._check_rollover()
        known = self._taken and self._taken[0] == card.id
        capped, raw, ctype = self._taken[1:] if known else (None, None, None)
        seq = self._seq_of(card)
        fields = answer_fields(card)
        self.emit(
            "ans",
            seq=seq,
            ease=ease,
            taken_capped_ms=capped,
            taken_raw_ms=raw,
            **fields,
        )
        if known:
            self.prompter.after_answer(card.id, fields["rev_id"], seq, ease, raw, ctype)

    def _seq_of(self, card) -> Optional[int]:
        """None if recording started while this card was already on screen."""
        return self._seq if self._shown and self._shown[0] == card.id else None

    # Rollover
    #########################################################################

    def _check_rollover(self) -> None:
        # day_did_change alone is not enough: Anki 26.09.3 skips it when the
        # rollover happens in the reviewer, repeats it after one elsewhere, and
        # its timer can fire hours late once the machine has slept. So everything
        # that reads today's counts comes through here first.
        if time.time() >= self._day_cutoff:
            self._emit_meta()
            self.prompter.on_new_day()

    def _emit_meta(self) -> None:
        col = self._mw.col
        self._day_cutoff = col.sched.day_cutoff
        offset = datetime.now().astimezone().utcoffset()
        self.emit(
            "meta",
            addon_version=ADDON.VERSION,
            anki_version=anki_version,
            platform=platform.platform(),
            utc_offset_min=int(offset.total_seconds() // 60),
            rollover=col.get_preferences().scheduling.rollover,
            day_cutoff=self._day_cutoff,
        )

    # Everything else
    #########################################################################

    def _on_audio_begin(self, player, tag) -> None:
        self.emit("audio", kind="begin", tag=type(tag).__name__)

    def _on_audio_end(self, player) -> None:
        self.emit("audio", kind="end")

    def _on_audio_replay(self, webview, card, is_front_side) -> None:
        self.emit("audio", kind="replay", cid=card.id, front=is_front_side)

    def _on_undo(self, changes) -> None:
        self.emit(
            "undo", op=changes.operation, reverted_to=changes.reverted_to_timestamp,
        )

    def _on_focus(self, state) -> None:
        self.emit("focus", state=state.name)

    def _on_state_will_change(self, new: str, old: str) -> None:
        if self._mw.col:
            self._check_rollover()
        self.prompter.on_state_will_change(new, old)


_active: Optional[Recorder] = None


def active_recorder() -> Optional[Recorder]:
    return _active


def start_recording(mw, store: Store) -> Recorder:
    global _active
    stop_recording()
    # Stays installed when recording stops: removing a hook from inside its
    # own run makes Anki skip the next callback, another add-on's perhaps.
    gui_hooks.profile_will_close.remove(stop_recording)
    gui_hooks.profile_will_close.append(stop_recording)
    _active = Recorder(mw, store)
    _active.start()
    return _active


def stop_recording() -> None:
    global _active
    if _active:
        recorder, _active = _active, None
        recorder.stop()
