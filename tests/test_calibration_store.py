import json
import subprocess
import sys
from pathlib import Path

import pytest

from review_heatmap.calibration.store import (
    DEFAULT_STATE, FOLDER_NAME, Store, baseline_days_left,
)

# 2026-11-05 and 2026-12-01, both 00:00:00 UTC.
NOVEMBER = 1793836800000
DECEMBER = 1796083200000
DAY_MS = 86400000


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "data")
    yield store
    store.close()


def event(ts=NOVEMBER, kind="ans", **fields):
    return {"v": 1, "t": kind, "ts": ts, **fields}


def test_data_folder_sits_inside_the_profile(tmp_path):
    assert Store.for_profile(tmp_path).folder == tmp_path / FOLDER_NAME


def test_events_round_trip(store):
    events = [
        event(seq=1, memory={"s": 8.2956, "d": 1.0}, ratio=1 / 3),
        event(memory=None, note="ไทย 日本語"),
    ]
    for item in events:
        store.append(item)
    store.close()
    assert list(store.read_events()) == events


def test_no_data_yet_reads_as_empty(store):
    assert list(store.read_events()) == []
    assert store.count_events("ans") == 0
    assert store.error_count() == 0


def test_events_survive_without_close(store):
    store.append(event(seq=1))
    # Each event must reach the file at once: a crash runs no close().
    assert list(Store(store.folder).read_events()) == [event(seq=1)]


def test_crash_truncated_line_is_skipped_and_does_not_eat_the_next(store):
    store.append(event(seq=1))
    store.close()
    path = store.folder / "events-2026-11.jsonl"
    path.write_bytes(path.read_bytes() + b'{"v":1,"t":"ans","ts":17')
    assert list(store.read_events()) == [event(seq=1)]

    store.append(event(seq=2))
    store.close()
    assert list(store.read_events()) == [event(seq=1), event(seq=2)]


def test_binary_garbage_and_non_objects_are_skipped(store):
    store.append(event(seq=1))
    store.close()
    path = store.folder / "events-2026-11.jsonl"
    path.write_bytes(path.read_bytes() + b"\xff\xfe\n12\n")
    store.append(event(seq=2))
    store.close()
    assert [item["seq"] for item in store.read_events()] == [1, 2]


def test_months_rotate_and_read_back_in_order(store):
    store.append(event(DECEMBER - 1, seq=1))
    store.append(event(DECEMBER, seq=2))
    store.close()
    store.append(event(DECEMBER + 1, seq=3))
    store.close()
    assert sorted(path.name for path in store.folder.iterdir()) == [
        "events-2026-11.jsonl", "events-2026-12.jsonl",
    ]
    assert [item["seq"] for item in store.read_events()] == [1, 2, 3]


def test_failed_write_reopens_and_repairs_the_line(store, monkeypatch):
    store.append(event(seq=1))

    class FullDisk:
        def write(self, line):
            path = store.folder / "events-2026-11.jsonl"
            path.write_bytes(path.read_bytes() + line[:10])
            raise OSError("No space left on device")

        def close(self):
            pass

    real = store._events
    store._events = FullDisk()
    with pytest.raises(OSError):
        store.append(event(seq=2))
    real.close()
    store.append(event(seq=3))
    store.close()
    assert [item["seq"] for item in store.read_events()] == [1, 3]


def test_count_events_counts_one_kind_across_months(store):
    store.append(event(NOVEMBER, "ans"))
    store.append(event(NOVEMBER, "q"))
    store.append(event(DECEMBER, "ans"))
    store.close()
    assert store.count_events("ans") == 2
    assert store.count_events("q") == 1


def test_state_defaults_and_atomic_update(store):
    assert store.load_state() == DEFAULT_STATE
    assert store.update_state(recording=True)["recording"] is True
    assert Store(store.folder).load_state()["recording"] is True
    assert [path.name for path in store.folder.iterdir()] == ["state.json"]


def test_defaults_reach_a_state_file_from_an_older_version(store):
    store.save_state({"recording": True})
    state = store.load_state()
    assert state["recording"] is True
    assert state["baseline_days"] == DEFAULT_STATE["baseline_days"]


def test_loaded_state_shares_nothing_with_the_defaults(store):
    store.load_state()["recording"] = True
    assert DEFAULT_STATE["recording"] is False


@pytest.mark.parametrize("content", ["{broken", "[1, 2]", "12"])
def test_corrupt_state_is_kept_aside_and_logged(store, content):
    store.folder.mkdir()
    (store.folder / "state.json").write_text(content)
    assert store.load_state() == DEFAULT_STATE
    assert (store.folder / "state.json.corrupt").read_text() == content
    assert store.error_count() == 1


def test_errors_take_one_line_each(store):
    store.log_error("q", "Traceback\n  line\nValueError: x")
    store.log_error("a", "Traceback\n  line\nKeyError: y")
    assert store.error_count() == 2
    lines = (store.folder / "errors.log").read_text().splitlines()
    assert json.loads(lines[0])["where"] == "q"
    assert "ValueError" in json.loads(lines[0])["details"]


def test_log_error_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    Store(blocker / "data").log_error("q", "details")


def test_baseline_counts_down_from_the_first_start():
    state = {"recording_since": NOVEMBER, "baseline_days": 14}
    assert baseline_days_left(state, NOVEMBER) == 14
    assert baseline_days_left(state, NOVEMBER + DAY_MS - 1) == 14
    assert baseline_days_left(state, NOVEMBER + DAY_MS) == 13
    assert baseline_days_left(state, NOVEMBER + 40 * DAY_MS) == 0
    assert baseline_days_left({"recording_since": None, "baseline_days": 14}, 0) == 14


def test_store_imports_without_anki_or_qt():
    source = Path(__file__).resolve().parents[1] / "src"
    code = f"""
import importlib.util, sys, types
for name in ("aqt", "anki", "PyQt6"):
    sys.modules[name] = None
package = types.ModuleType("review_heatmap")
package.__path__ = [{str(source / "review_heatmap")!r}]
sys.modules["review_heatmap"] = package
import review_heatmap.calibration.store
"""
    subprocess.run([sys.executable, "-S", "-c", code], check=True)
