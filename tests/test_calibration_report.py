import json
import math
import random
import subprocess
import sys
from bisect import bisect_left
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("aqt")

from aqt import gui_hooks
from aqt.qt import QApplication

from test_activity import setup  # noqa: F401
from test_calibration_recorder import col, fake_mw, recorder_module  # noqa: F401

from review_heatmap.calibration.dataset import read_dataset, revlog_sql
from review_heatmap.calibration.fit import ANCHORS
from review_heatmap.calibration.report import (
    COST_LABELS, GATE_COST_RATINGS, GATE_RATED_DAYS, HOLDOUT_SHARE,
    JUDGED_SHARE, WHY_LABELS, build_report, format_report, gate_text, latest_report,
    next_model_version, read_model, run_fit, ship_gate, write_model, write_report,
)
from review_heatmap.calibration.store import DAY_MS, Store, now_ms
from review_heatmap.metrics import (
    MODEL_INPUTS, active_effort_model, effort_model_available, effort_model_credit,
    use_effort_model,
)

START = 1_790_000_000_000
MINUTE = 60_000
RUN = "run"
TRUE_TIME_EXPONENT = 0.8
TRUE_AGAIN = 0.6
TRUE_SIGMA = 0.3


def logistic_draw(rng):
    draw = rng.random()
    return math.log(draw / (1 - draw))


def day_of(rev_id):
    return (rev_id - START) // DAY_MS


def world(days=75, seed=7, baseline_days=0):
    """Days of a simulated owner whose felt cost follows a known model.

    Answers per day and pace vary apart, so count and time disagree, and the
    day ratings follow the true total cost.
    """
    rng = random.Random(seed)
    rows, events = [], []
    day_costs = []
    for day in range(days):
        moment = START + day * DAY_MS + 3_600_000
        answers = rng.randint(25, 90)
        pace = math.exp(rng.gauss(0, 0.7))
        total = 0.0
        for index in range(answers):
            seconds = 10 * pace * math.exp(rng.gauss(0, 0.6))
            ms = int(seconds * 1000)
            ease = rng.choices((1, 2, 3, 4), (2, 1, 6, 1))[0]
            mid = rng.choice((100, 200))
            cid = rng.randrange(1, 400)
            last_ivl = rng.choice((0, 1, 3, 9, 30))
            eta = TRUE_TIME_EXPONENT * math.log(seconds / 10) + TRUE_AGAIN * (ease == 1)
            total += math.exp(eta)
            seq = day * 1000 + index + 1
            rev_id = moment + index * 3 * MINUTE
            rows.append((cid, rev_id, ease, ms, last_ivl, mid, 0, day, 500))
            events += [
                {"t": "q", "ts": rev_id - ms, "run": RUN, "seq": seq},
                {"t": "a", "ts": rev_id - ms // 4, "run": RUN, "seq": seq},
                {"t": "ans", "ts": rev_id, "run": RUN, "seq": seq, "rev_id": rev_id,
                 "taken_raw_ms": ms},
            ]
            if index % 4 == 0:
                cost = bisect_left(ANCHORS, eta + TRUE_SIGMA * logistic_draw(rng))
                answers_given = {"cost": cost}
                if ease == 1:
                    answers_given["why"] = rng.randrange(5)
                events.append({
                    "t": "prompt", "kind": "answer", "ts": rev_id, "run": RUN, "seq": seq,
                    "rev_id": rev_id, "answers": answers_given, "skipped": [],
                })
        day_costs.append(total)
    ranks = sorted(range(days), key=lambda day: day_costs[day] * math.exp(rng.gauss(0, 0.1)))
    for rank, day in enumerate(ranks):
        tiring = 1 + rank * 5 // days
        end = START + (day + 1) * DAY_MS
        events.append({
            "t": "prompt", "kind": "day", "ts": end, "day_end_ms": end,
            "answers": {"tiring": tiring, "learned": 6 - tiring}, "skipped": [],
        })
    events.sort(key=lambda event: event["ts"])
    state = {"recording_since": START, "baseline_days": baseline_days, "block_gap_hours": 2}
    return events, rows, state


def report_of(events, rows, state, names=None, version=1):
    dataset = read_dataset(events, rows, state["block_gap_hours"])
    return build_report(dataset, state, None, names or {}, version, lambda label: None)


@pytest.fixture(scope="module")
def simulated():
    return world()


@pytest.fixture(scope="module")
def report(simulated):
    return report_of(*simulated, names={(100, 0): "Basic / Card 1"})


def weight(report, name):
    return next(row for row in report["fit"]["weights"] if row["name"] == name)


def test_the_fit_recovers_the_model_behind_the_ratings(report):
    fit = report["fit"]
    assert fit["converged"]
    assert fit["sigma"] == pytest.approx(TRUE_SIGMA, abs=0.05)
    row = weight(report, "log_d")
    assert abs(row["weight"] - TRUE_TIME_EXPONENT) < 3 * row["se"] + 0.05
    assert weight(report, "again")["weight"] == pytest.approx(TRUE_AGAIN, abs=0.15)
    assert weight(report, "sharpness")["control"] is True
    assert weight(report, "log_d")["control"] is False


def test_the_time_exponent_reads_the_interaction_at_each_richness(report):
    exponents = report["fit"]["time_exponent"]
    base, interaction = (weight(report, name)["weight"] for name in ("log_d", "log_d·rich"))
    assert exponents == {"0.0": pytest.approx(base), "1.5": pytest.approx(base + 1.5 * interaction)}


def test_the_model_body_is_ready_for_effort_model_json(report):
    model = report["model"]
    assert set(model) == {"status", "version", "intercept", "weights"}
    assert model["status"] == "local"  # shipping it takes a person
    assert list(model["weights"]) == list(MODEL_INPUTS)
    inputs = dict.fromkeys(MODEL_INPUTS, 0.0)
    inputs["log_d"] = 1.0
    credit = effort_model_credit(inputs, model)
    assert credit == pytest.approx(math.exp(model["intercept"] + model["weights"]["log_d"]))


def test_counts_and_category_use(report, simulated):
    events, rows, _ = simulated
    prompts = [event for event in events if event["t"] == "prompt" and event["kind"] == "answer"]
    counts = report["counts"]
    assert counts["cost"] == counts["rated_answers"] == len(prompts)
    assert counts["tiring"] == counts["rated_days"] == 75
    assert counts["logged"] == len(rows)
    assert (counts["unlogged"], counts["undone"]) == (0, 0)
    assert report["skip_rate"] == 0
    assert sum(report["category_use"]["cost"].values()) == len(prompts)


def test_holdout_scores_the_later_days_against_the_intercept(report, simulated):
    holdout = report["holdout"]
    assert holdout["days"] == round(75 * HOLDOUT_SHARE)
    assert holdout["ratings"] + holdout["train_ratings"] == report["counts"]["rated_answers"]
    assert holdout["log_loss"] < holdout["intercept_only_log_loss"]
    assert holdout["spearman"] > 0.5


def test_the_judge_ranks_every_measure_on_the_later_days(report):
    judge = report["judge"]
    assert judge["tiring"]["days"] == round(75 * JUDGED_SHARE)
    assert judge["learned"]["days"] == judge["tiring"]["days"]
    for measure in ("count", "recorded_time", "review_weighted", "fsrs", "fitted"):
        assert judge["tiring"][measure] == pytest.approx(-judge["learned"][measure])
    assert judge["tiring"]["fitted"] > judge["tiring"]["review_weighted"]


def test_the_judge_never_sees_a_rating_from_the_judged_days(simulated):
    events, rows, state = simulated
    first_judged = 75 - round(75 * JUDGED_SHARE)

    def with_costs(changed):
        return [
            dict(event, answers={"cost": 10 - event["answers"]["cost"]})
            if event["t"] == "prompt" and event["kind"] == "answer" and changed(event)
            else event
            for event in events
        ]

    plain = report_of(events, rows, state)
    judged_changed = report_of(
        with_costs(lambda event: day_of(event["rev_id"]) >= first_judged), rows, state,
    )
    trained_changed = report_of(
        with_costs(lambda event: day_of(event["rev_id"]) < first_judged), rows, state,
    )
    assert judged_changed["judge"] == plain["judge"]
    assert judged_changed["model"] != plain["model"]  # the shipped fit reads them all
    assert trained_changed["judge"] != plain["judge"]


def test_a_report_survives_json_and_reads_as_text(report, tmp_path):
    path = write_report(tmp_path, report)
    assert path.name.startswith("report-") and path.suffix == ".json"
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(json.dumps(report))
    text = format_report(report)
    for line in ("Fit on", "Time exponent", "log_d·rich", "Holdout", "Judge", "tiring (",
                 "learned (", "Front share", "Basic / Card 1: 75%", "Cost by Again reason",
                 "Pace"):
        assert line in text


def test_front_and_back_share_per_template(report):
    named = next(row for row in report["templates"] if row["template"] == "Basic / Card 1")
    assert named["front_share"] == pytest.approx(0.75, abs=0.01)
    assert {row["template"] for row in report["templates"]} == {"Basic / Card 1", "200:0"}


def test_cost_by_again_reason_uses_the_prompt_labels(report):
    reasons = report["cost_by_reason"]
    assert [row["reason"] for row in reasons] == list(WHY_LABELS)
    assert sum(row["ratings"] for row in reasons) == sum(report["category_use"]["why"].values())
    assert {row["median_cost"] for row in reasons} <= set(COST_LABELS)


def test_labels_stay_in_step_with_the_prompts(addon_modules):
    from review_heatmap.calibration.prompts import COST, WHY

    assert COST_LABELS == COST.labels
    assert WHY_LABELS == WHY.labels


def paced_events(slow_after_prompt=1.0, slow_after_baseline=1.0):
    """Ten answers a day on two days a fortnight apart, an answer prompt on every
    other card. The answer after a prompt, and later days, take longer."""
    rows, events = [], []
    for day in (0, 14):
        pace = slow_after_baseline if day else 1.0
        previous_prompted = False
        for index in range(20):
            rev_id = START + day * DAY_MS + index * MINUTE
            seq = day * 100 + index + 1
            ms = 10_000 * pace * (slow_after_prompt if previous_prompted else 1)
            rows.append((rev_id, rev_id, 3, int(ms), 0, 100, 0, day, 0))
            events.append({
                "t": "ans", "ts": rev_id, "run": RUN, "seq": seq, "rev_id": rev_id,
                "taken_raw_ms": int(ms),
            })
            previous_prompted = day > 0 and index % 2 == 0
            if previous_prompted:
                events.append({
                    "t": "prompt", "kind": "answer", "ts": rev_id, "run": RUN, "seq": seq,
                    "rev_id": rev_id, "answers": {"cost": 2}, "skipped": [],
                })
    return events, rows, {"recording_since": START, "baseline_days": 14, "block_gap_hours": 2}


def test_reactivity_compares_pace_on_comparable_answers():
    reactivity = report_of(*paced_events(slow_after_baseline=1.5))["reactivity"]
    assert reactivity["afterwards_vs_baseline"]["ratio"] == pytest.approx(1.5, abs=0.01)
    assert reactivity["after_prompt_vs_others"]["ratio"] == pytest.approx(1, abs=0.01)
    reactivity = report_of(*paced_events(slow_after_prompt=2))["reactivity"]
    assert reactivity["after_prompt_vs_others"]["ratio"] == pytest.approx(2, abs=0.01)


def test_reactivity_says_so_when_nothing_is_comparable():
    events, rows, state = paced_events()
    state["baseline_days"] = 0
    reactivity = report_of(events, rows, state)["reactivity"]
    assert reactivity["afterwards_vs_baseline"] is None
    assert "too few comparable answers" in format_report(report_of(events, rows, state))


def test_too_little_data_still_reports_and_fails_the_gate():
    rev_id = START
    rows = [(1, rev_id, 3, 10_000, 0, 100, 0, 0, 0)]
    events = [{
        "t": "prompt", "kind": "answer", "ts": rev_id, "run": RUN, "seq": 1,
        "rev_id": rev_id, "answers": {"cost": 2}, "skipped": [],
    }]
    report = report_of(events, rows, {
        "recording_since": None, "baseline_days": 14, "block_gap_hours": 2,
    })
    assert (report["holdout"], report["judge"]) == (None, None)
    assert report["gate"]["passed"] is False
    assert "too few" in format_report(report)
    json.dumps(report)


def judged(fitted, weighted, converged=True, t=2.0):
    return {
        "converged": converged,
        "tiring": {"fitted": fitted, "review_weighted": weighted},
        "beyond": None if fitted is None else {"days": 18, "r": 0.5, "t": t, "needed": 1.75},
    }


def test_the_gate_passes_only_with_enough_data_and_a_real_gain():
    # A small plain margin is all a real gain can show: measures agree closely.
    passed = ship_gate(GATE_COST_RATINGS, GATE_RATED_DAYS, judged(0.60, 0.58), True)
    assert passed["passed"] is True and passed["reasons"] == []
    assert passed["margin"] == pytest.approx(0.02)
    assert "PASSED" in gate_text(passed) and "NOT" not in gate_text(passed)


@pytest.mark.parametrize("gate,reason", [
    (ship_gate(GATE_COST_RATINGS - 1, GATE_RATED_DAYS, judged(0.9, 0.1), True), "cost ratings"),
    (ship_gate(GATE_COST_RATINGS, GATE_RATED_DAYS - 1, judged(0.9, 0.1), True), "rated days"),
    (ship_gate(GATE_COST_RATINGS, GATE_RATED_DAYS, judged(0.6, 0.56, t=1.0), True), "needs 1.75"),
    (ship_gate(GATE_COST_RATINGS, GATE_RATED_DAYS, judged(0.5, 0.6), True), "by -0.100"),
    (ship_gate(GATE_COST_RATINGS, GATE_RATED_DAYS, judged(0.9, 0.1), False), "converge"),
    (ship_gate(GATE_COST_RATINGS, GATE_RATED_DAYS, judged(0.9, 0.1, False), True), "converge"),
    (ship_gate(GATE_COST_RATINGS, GATE_RATED_DAYS, judged(None, 0.5, None), True), "judged"),
    (ship_gate(GATE_COST_RATINGS, GATE_RATED_DAYS, None, True), "judged"),
])
def test_the_gate_says_why_it_failed(gate, reason):
    assert gate["passed"] is False
    assert reason in gate_text(gate)
    assert "NOT PASSED" in gate_text(gate)


def test_the_gate_runs_on_real_measures(report):
    assert report["gate"]["passed"], report["gate"]
    assert report["gate"]["margin"] == pytest.approx(
        report["judge"]["tiring"]["fitted"] - report["judge"]["tiring"]["review_weighted"],
    )
    beyond = report["gate"]["beyond"]
    assert beyond == report["judge"]["beyond"]
    assert beyond["t"] >= beyond["needed"] and beyond["days"] >= 8


def test_too_few_judged_days_cannot_pass_the_gate(simulated):
    events, rows, state = simulated
    last = rows[-1][1]
    few = [
        event for event in events
        if event.get("kind") != "day" or event["day_end_ms"] > last - 12 * DAY_MS
    ]
    report = report_of(few, rows, state)
    assert report["judge"]["beyond"] is None
    assert report["gate"]["passed"] is False


def world_with_note_type_level(level=1.0, seed=3):
    """Template 200 costs more than its answer times show, and only ratings can tell."""
    events, rows, state = world(seed=seed)
    template = {row[1]: row[5] for row in rows}
    rng = random.Random(seed)
    shifted = []
    for event in events:
        if event.get("kind") == "answer" and template[event["rev_id"]] == 200:
            cost = min(10, event["answers"]["cost"] + (rng.random() < level) * 4)
            event = {**event, "answers": {**event["answers"], "cost": cost}}
        shifted.append(event)
    return shifted, rows, state


def test_only_the_profile_model_holds_note_type_offsets():
    report = report_of(*world_with_note_type_level(), names={(200, 0): "Heavy / Card 1"})
    assert "offsets" not in report["model"]  # what Copy Weights gives
    offsets = report["local_model"]["offsets"]
    assert offsets["200:0"] - offsets["100:0"] == pytest.approx(math.log(4), abs=0.35)
    assert not any(row["name"].startswith("template:") for row in report["fit"]["weights"])
    assert [row["label"] for row in report["note_type_offsets"]] == [
        "template:100:0", "Heavy / Card 1",
    ]
    assert "Heavy / Card 1: +" in format_report(report)


def test_a_profile_model_scores_with_its_offsets():
    report = report_of(*world_with_note_type_level())
    model = report["local_model"]
    inputs = dict.fromkeys(MODEL_INPUTS, 0.0)
    gap = math.exp(model["offsets"]["200:0"] - model["offsets"]["100:0"])
    assert effort_model_credit(inputs, model, (200, 0)) == pytest.approx(
        gap * effort_model_credit(inputs, model, (100, 0)),
    )


def test_the_judge_shows_offsets_beside_the_shared_fit():
    report = report_of(*world_with_note_type_level())
    tiring = report["judge"]["tiring"]
    assert tiring["fitted_offsets"] is not None and tiring["fitted"] is not None
    assert tiring["fitted_offsets"] != tiring["fitted"]
    assert "Fitted with note-type offsets" in format_report(report)


def test_a_missing_offset_level_does_not_inflate_the_holdout():
    report = report_of(*world_with_note_type_level(level=0.5))
    holdout = report["holdout"]
    assert holdout["log_loss"] < holdout["intercept_only_log_loss"]


# Files
###########################################################################


def filled_store(folder, simulated):
    store = Store(folder)
    events, rows, state = simulated
    for event in events:
        store.append(event)
    store.close()
    store.update_state(recording_since=state["recording_since"], baseline_days=0)
    return store, rows


def test_each_fit_takes_a_new_version_and_saves_a_report(tmp_path, simulated):
    store, rows = filled_store(tmp_path, simulated)
    labels = []
    first = run_fit(store, rows, None, {}, labels.append)
    assert labels[0] == "Reading the calibration events" and len(labels) > 3
    second = run_fit(store, rows, None, {}, labels.append)
    assert (first["model"]["version"], second["model"]["version"]) == (1, 2)
    assert len(list(tmp_path.glob("report-*.json"))) == 1  # one per day
    assert latest_report(tmp_path) == json.loads(json.dumps(second))


def test_the_version_stays_above_a_local_and_a_shipped_model(tmp_path, monkeypatch):
    assert next_model_version(tmp_path) == 1
    write_model(tmp_path, {"status": "local", "version": 4})
    assert next_model_version(tmp_path) == 5
    (tmp_path / "report-20261101.json").write_text('{"model": {"version": 6}}')
    assert next_model_version(tmp_path) == 7
    monkeypatch.setitem(
        next_model_version.__globals__, "load_effort_model", lambda: {"version": 9},
    )
    assert next_model_version(tmp_path) == 10


def test_the_latest_report_is_the_newest_and_a_damaged_one_is_no_report(tmp_path):
    assert latest_report(tmp_path) is None
    (tmp_path / "report-20261101.json").write_text('{"a": 1}')
    (tmp_path / "report-20261103.json").write_text('{"a": 2}')
    assert latest_report(tmp_path) == {"a": 2}
    (tmp_path / "report-20261104.json").write_text('{"a"')
    assert latest_report(tmp_path) is None


def test_a_model_round_trips_with_the_non_ascii_input_name(tmp_path):
    assert read_model(tmp_path) is None
    model = {"status": "local", "version": 3, "weights": {"log_d·rich": 0.25}}
    write_model(tmp_path, model)
    assert "log_d·rich" in (tmp_path / "model.json").read_text(encoding="utf-8")
    assert read_model(tmp_path) == model


def test_dataset_and_report_import_without_anki_or_qt():
    source = Path(__file__).resolve().parents[1] / "src" / "review_heatmap"
    code = f"""
import sys, types
package = types.ModuleType("review_heatmap")
package.__path__ = [{str(source)!r}]
sys.modules["review_heatmap"] = package
import review_heatmap.calibration.report
assert not {{"aqt", "anki", "PyQt6"}} & set(sys.modules), sorted(sys.modules)
"""
    subprocess.run([sys.executable, "-S", "-c", code], check=True, timeout=30)


# Local model
###########################################################################


class FakeTaskman:
    def __init__(self):
        self.labels = []

    def with_progress(self, task, on_done, label=None, parent=None):
        self.labels.append(label)
        future = Future()
        try:
            future.set_result(task())
        except Exception as error:
            future.set_exception(error)
        on_done(future)

    def run_on_main(self, closure):
        closure()


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(fake_mw, setup, monkeypatch):
    """The main window as the tab and the startup hook use it."""
    calls = []
    taskman = FakeTaskman()
    refresh = SimpleNamespace(refresh=lambda: calls.append("refresh"))
    for name, value in (
        ("col", setup.col),
        ("taskman", taskman),
        ("progress", SimpleNamespace(update=lambda label: taskman.labels.append(label))),
        ("state", "deckBrowser"),
        ("deckBrowser", refresh),
        ("overview", SimpleNamespace(refresh=lambda: calls.append("overview"))),
        ("_review_heatmap", SimpleNamespace(invalidate_cache=lambda: calls.append("invalidate"))),
        ("_increase_background_ops", lambda: None),
        ("_decrease_background_ops", lambda: None),
    ):
        monkeypatch.setattr(fake_mw, name, value, raising=False)
    setup.col.models = SimpleNamespace(all=lambda: [
        {"id": 100, "name": "Basic", "tmpls": [{"name": "Card 1"}, {"name": "Card 2"}]},
    ])
    fake_mw.calls = calls
    yield fake_mw
    gui_hooks.profile_will_close.remove(use_effort_model)
    use_effort_model()


@pytest.fixture
def tab_module(addon_modules, recorder_module, setup, monkeypatch):
    from review_heatmap.gui import calibration_tab

    monkeypatch.setattr(calibration_tab, "config", setup.conf)
    return calibration_tab


def seed_cost_ratings(store, count):
    for index in range(count):
        store.append({
            "t": "prompt", "kind": "answer", "ts": now_ms(), "run": RUN, "seq": index,
            "rev_id": START + index, "answers": {"cost": index % 6}, "skipped": [],
        })
    store.close()


def seed_revlog(setup, count):
    for index in range(count):
        setup.db.connection.execute(
            "INSERT INTO revlog (id, cid, ease, time) VALUES (?, ?, 3, ?)",
            (START + index, index + 1, 5000 + 1000 * index),
        )


def test_the_revlog_query_and_template_names_work_on_a_real_collection(
    addon_modules, tab_module, col,
):
    """A real schema, where several tables share column names, and real undo."""
    note = col.new_note(col.models.by_name("Basic (and reversed card)"))
    note["Front"], note["Back"] = "front", "back"
    col.add_note(note, 1)
    card = note.cards()[1]
    card.start_timer()
    col.sched.answerCard(card, 3)
    can_undo = col.undo_status().undo

    rows = col.db.all(revlog_sql(4))
    assert rows[0][:3] == [card.id, rows[0][1], 3]
    assert rows[0][5:7] == [note.mid, 1]
    dataset = read_dataset([], rows)
    assert dataset.logged == []
    names = tab_module._template_names(col)
    assert names[(note.mid, 1)] == "Basic (and reversed card) / Card 2"
    assert col.undo_status().undo == can_undo and can_undo


def test_fit_waits_for_300_cost_ratings(app, window, tab_module):
    store = Store.for_profile(window.pm.profileFolder())
    seed_cost_ratings(store, 299)
    assert not tab_module.CalibrationTab(window).btnFit.isEnabled()
    seed_cost_ratings(store, 1)
    assert tab_module.CalibrationTab(window).btnFit.isEnabled()


def fitted_tab(tab_module, window, setup):
    store = Store.for_profile(window.pm.profileFolder())
    seed_cost_ratings(store, 300)
    seed_revlog(setup, 300)
    tab = tab_module.CalibrationTab(window)
    assert not tab.btnUseFit.isEnabled() and not tab.btnCopyWeights.isEnabled()
    assert "No fit yet" in tab.labFit.text()
    tab.btnFit.click()
    return tab


def test_fit_runs_with_visible_progress_and_shows_the_gate(app, window, tab_module, setup):
    tab = fitted_tab(tab_module, window, setup)
    assert window.taskman.labels[:2] == ["Reading the revlog", "Reading the calibration events"]
    assert "Judging against the day ratings" in window.taskman.labels
    assert "Ship gate: NOT PASSED" in tab.labFit.text()
    assert "300 of 800 cost ratings" in tab.labFit.text()
    assert "shipped weights" in tab.labFit.text()
    assert "Fit on 300 ratings" in tab.txtReport.toPlainText()
    assert tab.btnUseFit.isEnabled() and tab.btnCopyWeights.isEnabled()
    # A reopened dialog finds it again.
    reopened = tab_module.CalibrationTab(window)
    assert reopened.labFit.text() == tab.labFit.text()
    assert reopened.btnUseFit.isEnabled()


def test_a_failed_fit_is_logged_and_told(app, window, tab_module, setup, monkeypatch):
    shown = []
    store = Store.for_profile(window.pm.profileFolder())
    seed_cost_ratings(store, 300)
    tab = tab_module.CalibrationTab(window)  # the revlog has none of these answers
    monkeypatch.setattr(tab_module, "showWarning", lambda text, parent: shown.append(text))
    tab.btnFit.click()
    assert len(shown) == 1 and "fit failed" in shown[0]
    assert store.error_count() == 1
    assert "Errors: 1" in tab.labRecording.text()


def test_use_this_fit_writes_activates_and_redraws(app, window, tab_module, setup, monkeypatch):
    tab = fitted_tab(tab_module, window, setup)
    monkeypatch.setattr(tab_module, "tooltip", lambda *args, **kwargs: None)
    tab.btnUseFit.click()
    model = read_model(tab._store.folder)
    assert model == tab._report["local_model"] and "offsets" in model
    assert active_effort_model() == model and effort_model_available()
    assert window.calls == ["invalidate", "refresh"]
    assert f"fit version {model['version']}" in tab.labFit.text()


def test_copy_weights_gives_a_body_scoring_can_read(app, window, tab_module, setup, monkeypatch):
    tab = fitted_tab(tab_module, window, setup)
    monkeypatch.setattr(tab_module, "tooltip", lambda *args, **kwargs: None)
    tab.btnCopyWeights.click()
    body = json.loads(QApplication.clipboard().text())
    assert body == json.loads(json.dumps(tab._report["model"]))
    assert "offsets" not in body  # they mean nothing on another collection
    assert not (tab._store.folder / "model.json").exists()


def test_a_saved_fit_is_active_from_profile_open_in_devmode_only(window, recorder_module):
    from review_heatmap import calibration

    store = Store.for_profile(window.pm.profileFolder())
    write_model(store.folder, {"status": "local", "version": 3, "weights": {}})
    calibration._on_profile_did_open()
    assert not effort_model_available() and window.calls == []

    window.pm.profile[calibration.DEVMODE_KEY] = True
    calibration._on_profile_did_open()
    assert effort_model_available()
    assert active_effort_model()["version"] == 3
    # The first render came before this hook.
    assert window.calls == ["invalidate", "refresh"]


def test_the_shipped_weights_return_when_the_profile_closes(window):
    from review_heatmap import calibration

    calibration.activate_model(window, {"status": "local", "version": 3})
    calibration.activate_model(window, {"status": "local", "version": 4})
    assert active_effort_model()["version"] == 4
    gui_hooks.profile_will_close()
    assert not effort_model_available()


def test_an_overview_on_screen_is_redrawn_too(window, monkeypatch):
    from review_heatmap import calibration

    monkeypatch.setattr(window, "state", "overview")
    calibration.activate_model(window, {"status": "local", "version": 3})
    assert window.calls == ["invalidate", "overview"]


def test_a_failed_activation_still_starts_the_recorder(
    window, recorder_module, monkeypatch,
):
    from review_heatmap import calibration

    def fail(*args):
        raise RuntimeError("no")

    store = Store.for_profile(window.pm.profileFolder())
    write_model(store.folder, {"status": "local", "version": 3})
    store.update_state(recording=True)
    window.pm.profile[calibration.DEVMODE_KEY] = True
    started = []
    monkeypatch.setattr(calibration, "activate_model", fail)
    monkeypatch.setattr(recorder_module, "start_recording", lambda *args: started.append(args))
    calibration._on_profile_did_open()
    assert len(started) == 1
    assert store.error_count() == 1
