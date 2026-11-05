"""Calibration data folder: append-only JSONL events and state.json.

Stdlib only, so the pure calibration modules can read the data without Anki.

Each event is one line written with a single unbuffered write, so a crash
leaves at most a truncated last line. Nothing here touches the collection,
and the folder lives outside it: no effect on sync or undo.
"""

import json
import os
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Iterator, Optional, Union

FOLDER_NAME = "better_review_heatmap_calibration"

# Written on user actions only, never from the answer path, whose budget is
# 5 ms: the counts shown in the tab come from the event files instead.
DEFAULT_STATE = {
    "recording": False,
    "recording_since": None,  # ms, first start: a Stop keeps the baseline going
    "baseline_days": 14,
    "answer_rate": 0.06,
    "lapse_reason_rate": 0.3,
    "block_gap_hours": 2,
    "daily_cap": 30,
    "stop_after_cost_ratings": 1200,
    "day_rating_hour": 19,  # local hour from which leaving the reviewer asks
    "day_rated_through_ms": 0,  # end of the last study day rated or skipped
}

DAY_MS = 86_400_000


def now_ms() -> int:
    return int(time.time() * 1000)


class Store:
    def __init__(self, folder: Union[str, Path]):
        self.folder = Path(folder)
        # Some audio players report playback from a background thread.
        self._lock = threading.Lock()
        self._month: Optional[str] = None
        self._events = None

    @classmethod
    def for_profile(cls, profile_folder: Union[str, Path]) -> "Store":
        return cls(Path(profile_folder) / FOLDER_NAME)

    # Events
    #########################################################################

    def append(self, event: dict) -> None:
        """Append one event, whose "ts" (ms) picks the month's file."""
        line = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
        # UTC so that file order stays time order across a timezone change.
        month = time.strftime("%Y-%m", time.gmtime(event["ts"] / 1000))
        with self._lock:
            if month != self._month:
                self._open(month)
            try:
                self._events.write(line)
            except OSError:
                # Reopening repairs the partial line a full disk may leave.
                self._close()
                raise

    def close(self) -> None:
        with self._lock:
            self._close()

    def read_events(self, kind: Optional[str] = None) -> Iterator[dict]:
        """Yield every readable event in time order, skipping damaged lines.
        With a kind, the other lines are skipped unparsed, which is far cheaper."""
        needle = _kind_needle(kind) if kind else b""
        for path in sorted(self.folder.glob("events-*.jsonl")):
            with path.open("rb") as lines:
                for line in lines:
                    if needle not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        yield event

    def count_events(self, kind: str) -> int:
        """Count events of one kind, cheaply enough for the settings dialog."""
        needle = _kind_needle(kind)
        return sum(
            path.read_bytes().count(needle)
            for path in self.folder.glob("events-*.jsonl")
        )

    def _open(self, month: str) -> None:
        self._close()
        self.folder.mkdir(parents=True, exist_ok=True)
        events = (self.folder / f"events-{month}.jsonl").open("a+b", buffering=0)
        # End a line truncated by a crash, or the next event would join it.
        if events.seek(0, os.SEEK_END):
            events.seek(-1, os.SEEK_END)
            if events.read(1) != b"\n":
                events.write(b"\n")
        self._events = events
        self._month = month

    def _close(self) -> None:
        if self._events is not None:
            self._events.close()
        self._events = None
        self._month = None

    # Errors
    #########################################################################

    @property
    def _errors_path(self) -> Path:
        return self.folder / "errors.log"

    def log_error(self, where: str, details: str) -> None:
        """Log one failure per line. Runs inside handlers, so it never raises."""
        entry = {"ts": now_ms(), "where": where, "details": details}
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with self._errors_path.open("a", encoding="utf-8") as errors:
                errors.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def error_count(self) -> int:
        try:
            return self._errors_path.read_bytes().count(b"\n")
        except FileNotFoundError:
            return 0

    # State
    #########################################################################

    @property
    def _state_path(self) -> Path:
        return self.folder / "state.json"

    def load_state(self) -> dict:
        state = deepcopy(DEFAULT_STATE)
        try:
            state.update(json.loads(self._state_path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            pass
        except (TypeError, ValueError) as error:
            # Keep the damaged file: saving the defaults would overwrite it.
            self.log_error("load_state", repr(error))
            os.replace(self._state_path, self.folder / "state.json.corrupt")
        return state

    def save_state(self, state: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.folder / "state.json.tmp"
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(state, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, self._state_path)

    def update_state(self, **changes) -> dict:
        state = self.load_state()
        state.update(changes)
        self.save_state(state)
        return state


def _kind_needle(kind: str) -> bytes:
    return b'"t":"%s"' % kind.encode("ascii")


def baseline_days_left(state: dict, now: int) -> int:
    """Silent days left before the prompts may start."""
    since = state["recording_since"]
    if since is None:
        return state["baseline_days"]
    return max(0, state["baseline_days"] - (now - since) // DAY_MS)
