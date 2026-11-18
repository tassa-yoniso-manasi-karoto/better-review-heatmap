import json
import math
import random
import subprocess
import sys
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
from review_heatmap.calibration.report import (
    FAIR_TRIAL_RATED_DAYS, FIT_EFFORT_RATINGS, FIT_RATED_DAYS, GATE_EFFORT_RATINGS,
    GATE_RATED_DAYS, JUDGE_BLOCKS, build_report, format_report, gate_text, latest_report,
    next_model_version, read_model, run_fit, ship_gate, write_model, write_report,
)
from review_heatmap.calibration.store import DAY_MS, Store, now_ms
from review_heatmap.metrics import (
    MODEL_INPUTS, active_effort_model, effort_model_available, effort_model_credit,
    use_effort_model,
)

START = 1_790_000_000_000
MINUTE = 60_000
HOUR = 60 * MINUTE
RUN = "run"
TRUE_B = 0.8
TRUE_K = 0.6
BASIC, KANJI = "Basic/Card 1", "Kanji/Card"
CARDS = {BASIC: (100, 0), KANJI: (200, 0)}
EFFORT_CUTS = (-0.5, 0.4, 1.2, 2.0, 2.8)


def logistic_draw(rng):
    draw = rng.random()
    return math.log(draw / (1 - draw))


def levels(values, shares):
    order = sorted(values)
    cuts = [order[min(len(order) - 1, int(len(order) * share))] for share in shares]
    return [1 + sum(value > cut for cut in cuts) for value in values]


def world(days=70, seed=7, kanji_effort=0.0):
    """Days of a simulated owner whose work follows a known rule.

    Credit is exp(TRUE_B * log_d + TRUE_K * intensity): an Again, a Hard or a new
    card asks for more thought. Answers per day and pace vary apart, so count and
    time disagree. A third of the answers are rated on effort; each day's two
    sittings, the day and its two templates against each other are rated on how
    much work they were. kanji_effort is thought Kanji cards take that no input shows.
    """
    rng = random.Random(seed)
    rows, events, seen = [], [], set()
    sittings, days_info = [], []
    for day in range(days):
        moment = START + day * DAY_MS + 3 * HOUR
        pace = math.exp(rng.gauss(0, 0.5))
        count = rng.randint(30, 80)
        by_template = {100: 0.0, 200: 0.0}
        bounds = [[None, None, 0.0], [None, None, 0.0]]
        for index in range(count):
            half = int(index >= count // 2)
            rev_id = moment + index * 2 * MINUTE + half * 5 * HOUR
            mid = 100 if rng.random() < 0.7 else 200
            seconds = (6 if mid == 100 else 30) * pace * math.exp(rng.gauss(0, 0.6))
            ms = int(seconds * 1000)
            ease = rng.choices((1, 2, 3, 4), (2, 1, 6, 1))[0]
            cid = rng.randrange(1, 600) + (0 if mid == 100 else 10_000)
            new = cid not in seen
            seen.add(cid)
            last_ivl = 0 if new else rng.choice((0, 1, 3, 9, 30))
            intensity = (1.0 * (ease == 1) + 0.8 * (ease == 2) - 0.4 * (ease == 4)
                         + 0.5 * new + kanji_effort * (mid == 200) + rng.gauss(0, 0.5))
            credit = math.exp(TRUE_B * math.log(seconds / 10) + TRUE_K * intensity)
            by_template[mid] += credit
            seq = day * 1000 + index + 1
            shown = rev_id - ms
            rows.append((cid, rev_id, ease, ms, last_ivl, mid, 0, day, 500))
            events += [
                {"t": "q", "ts": shown, "run": RUN, "seq": seq},
                {"t": "a", "ts": rev_id - ms // 4, "run": RUN, "seq": seq},
                {"t": "ans", "ts": rev_id, "run": RUN, "seq": seq, "rev_id": rev_id,
                 "taken_raw_ms": ms},
            ]
            if index % 3 == 0:
                effort = 1 + sum(
                    intensity + 0.3 * logistic_draw(rng) > cut for cut in EFFORT_CUTS
                )
                answers = {"effort": effort}
                if new:
                    answers["familiar"] = rng.randrange(4)
                if ease == 1:
                    answers["wrong"] = ["blank"] if effort > 3 else ["form"]
                events.append({
                    "t": "prompt", "kind": "answer", "ts": rev_id, "run": RUN, "seq": seq,
                    "rev_id": rev_id, "wording_v": 5, "answers": answers, "skipped": [],
                })
            sitting = bounds[half]
            sitting[0] = shown if sitting[0] is None else sitting[0]
            sitting[1] = shown
            sitting[2] += credit
        for first, last, credit in bounds:
            sittings.append((first, last, math.log(credit) + rng.gauss(0, 0.25)))
        total = sum(by_template.values())
        days_info.append((day, rows[-1][1], math.log(total) + rng.gauss(0, 0.25),
                          math.log(by_template[200] / by_template[100]) + rng.gauss(0, 0.25)))
    work = levels([value for _, _, value in sittings], (0.15, 0.4, 0.7, 0.9))
    for (first, last, _), rating in zip(sittings, work):
        events.append({
            "t": "prompt", "kind": "session", "ts": last + 25 * MINUTE, "from_ms": first,
            "to_ms": last, "wording_v": 5, "answers": {"work": rating, "deep": 2},
            "skipped": [],
        })
    amount = levels([info[2] for info in days_info], (0.05, 0.15, 0.35, 0.65, 0.85, 0.95))
    pair = levels([info[3] for info in days_info], (0.15, 0.35, 0.65, 0.85))
    for (day, last, _, _), rated, compared in zip(days_info, amount, pair):
        end = last + 10 * MINUTE
        event = {
            "t": "prompt", "kind": "day", "ts": end, "day_end_ms": end, "wording_v": 5,
            "answers": {"amount": rated, "pair": compared, "learned": 8 - rated},
            "skipped": [], "pair_cards": [BASIC, KANJI],
        }
        if day:
            event["answers"]["versus"] = 3
            event["versus_day"] = START + (day - 1) * DAY_MS
        events.append(event)
    events.sort(key=lambda event: event["ts"])
    return events, rows


def report_of(events, rows, names=None, version=1):
    dataset = read_dataset(events, rows, templates=CARDS)
    return build_report(dataset, None, names or {}, version, lambda label: None)


@pytest.fixture(scope="module")
def simulated():
    return world()


@pytest.fixture(scope="module")
def report(simulated):
    return report_of(*simulated, names={(100, 0): "Basic / Card 1"})


def weight(report, name):
    return next(row for row in report["cards"]["weights"] if row["name"] == name)


def test_the_two_stages_find_the_owner_behind_the_ratings(report):
    work = report["work"]
    assert work["converged"] and not work["fell_back"]
    assert work["b"] == pytest.approx(TRUE_B, abs=0.15)
    low, high = work["interval"]
    assert low < TRUE_B < high
    assert work["k_used"] > 0 and work["without_difficulty"] > 3.84
    assert work["units"] == {"amount": 70, "pair": 70, "work": 140}
    # Harder answers, as the ratings tell them apart: Again over Hard over Good over Easy.
    again, hard, easy, new = (weight(report, name)["weight"] for name in ("again", "hard", "easy", "new"))
    assert again > hard > 0 > easy and new > 0
    assert weight(report, "focus")["control"] and not weight(report, "again")["control"]


def test_the_model_body_is_ready_for_effort_model_json(report):
    model, work = report["model"], report["work"]
    assert set(model) == {"status", "version", "intercept", "weights"}
    assert model["status"] == "local"  # shipping it takes a person
    assert list(model["weights"]) == list(MODEL_INPUTS)
    # Time counts once, in b: the difficulty keeps none of it.
    assert model["weights"]["log_d"] == work["b_used"]
    assert model["weights"]["log_d·rich"] == 0
    assert model["weights"]["again"] == pytest.approx(work["k_used"] * weight(report, "again")["weight"])
    inputs = dict.fromkeys(MODEL_INPUTS, 0.0)
    inputs["log_d"] = 1.0
    credit = effort_model_credit(inputs, model)
    assert credit == pytest.approx(math.exp(model["intercept"] + model["weights"]["log_d"]))


def test_counts_and_category_use(report, simulated):
    events, rows = simulated
    taps = [event for event in events if event.get("kind") == "answer"]
    counts = report["counts"]
    assert counts["effort"] == counts["effort_ratings"] == len(taps)
    assert (counts["amount"], counts["rated_days"]) == (70, 70)
    assert (counts["work"], counts["rated_sittings"]) == (140, 140)
    assert (counts["pair"], counts["pair_days"]) == (70, 70)
    assert counts["logged"] == len(rows)
    assert (counts["unlogged"], counts["undone"], counts["earlier"]) == (0, 0, 0)
    assert report["skip_rate"] == 0
    assert sum(report["category_use"]["effort"].values()) == len(taps)


def test_the_judge_scores_each_block_by_a_fit_on_the_days_before(report):
    judge = report["judge"]
    blocks = judge["blocks"]
    assert len(blocks) == JUDGE_BLOCKS - 1 and judge["rated_days"] == 70
    assert judge["amount"]["days"] == sum(block["days"] for block in blocks) == 70 - 18
    trained = [block["train_ratings"] for block in blocks]
    assert trained == sorted(trained) and trained[0] > 0
    assert judge["z"] == pytest.approx(sum(block["t"] for block in blocks) / math.sqrt(len(blocks)))
    for measure in ("count", "recorded_time", "review_weighted", "fsrs", "fitted"):
        assert judge["learned"][measure] == pytest.approx(-judge["amount"][measure])
    assert judge["amount"]["fitted"] > judge["amount"]["review_weighted"]


def test_the_judge_never_sees_a_rating_from_the_days_it_scores(simulated):
    events, rows = simulated
    last_block = round(70 * (JUDGE_BLOCKS - 1) / JUDGE_BLOCKS)

    def with_amounts(changed):
        return [
            dict(event, answers={**event["answers"], "amount": 8 - event["answers"]["amount"]})
            if event.get("kind") == "day" and changed(rows_day(event["day_end_ms"]))
            else event
            for event in events
        ]

    def rows_day(end):
        return max(row[7] for row in rows if row[1] < end)

    plain = report_of(events, rows)["judge"]["blocks"]
    judged = report_of(with_amounts(lambda day: day >= last_block), rows)["judge"]["blocks"]
    trained = report_of(with_amounts(lambda day: day < last_block), rows)["judge"]["blocks"]
    assert [(b["b"], b["k"]) for b in judged] == [(b["b"], b["k"]) for b in plain]
    assert judged[-1]["t"] != plain[-1]["t"]  # the scored days' own ratings judge them
    assert trained[-1]["b"] != plain[-1]["b"]


def test_a_real_gain_passes_the_gate(report):
    gate = report["gate"]
    assert gate["passed"], gate
    assert gate["z"] == report["judge"]["z"] >= gate["needed"]
    assert gate["margin"] == pytest.approx(
        report["judge"]["amount"]["fitted"] - report["judge"]["amount"]["review_weighted"],
    )


def test_a_report_survives_json_and_reads_as_text(report, tmp_path):
    path = write_report(tmp_path, report)
    assert path.name.startswith("report-") and path.suffix == ".json"
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(json.dumps(report))
    assert latest_report(tmp_path) == json.loads(json.dumps(report))
    text = format_report(json.loads(json.dumps(report)))
    for line in ("Effort ratings: ", "Cards, which answers are harder", "(time, scored through b)",
                 "Work, how answers add up", "Time exponent b:", "Difficulty weight k:",
                 "Judge, 52 of 70 rated days in 3 blocks", "amount (52 days)",
                 "Beyond review-weighted on amount: z", "Questions:", "b from evenings alone",
                 "Front share", "Basic / Card 1: 75%", "Pace"):
        assert line in text, line


def test_the_questions_say_what_each_one_shows(report):
    questions = report["questions"]
    effort = questions["effort"]["log_loss"]
    assert effort["inputs"] < effort["buttons"] < effort["levels"]
    assert questions["familiar"]["ratings"] > 0 and questions["focus"]["ratings"] == 0
    reasons = {row["reason"]: row for row in questions["wrong"]["reasons"]}
    assert reasons["blank"]["effort"] > reasons["form"]["effort"]
    assert [row["label"] for row in questions["sources"]] == [
        "from evenings alone", "with pairs", "with sittings too",
    ]
    widths = [row["interval"][1] - row["interval"][0] for row in questions["sources"]]
    assert widths[-1] < widths[0]  # sittings and pairs narrow it down


def test_front_and_back_share_per_template(report):
    named = next(row for row in report["templates"] if row["template"] == "Basic / Card 1")
    assert named["front_share"] == pytest.approx(0.75, abs=0.01)
    assert {row["template"] for row in report["templates"]} == {"Basic / Card 1", "200:0"}


def paced_events(slow_after_prompt=1.0, slow_since=1.0):
    """Twenty answers a day on two days a fortnight apart, effort asked on every
    other card of the second. Answers after a question, or since questions began,
    take longer."""
    rows, events = [], []
    for day in (0, 14):
        pace = slow_since if day else 1.0
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
                    "rev_id": rev_id, "wording_v": 5, "answers": {"effort": 2}, "skipped": [],
                })
    return events, rows


def test_reactivity_compares_pace_on_comparable_answers():
    reactivity = report_of(*paced_events(slow_since=1.5))["reactivity"]
    assert reactivity["since_vs_before"]["ratio"] == pytest.approx(1.5, abs=0.01)
    assert reactivity["after_prompt_vs_others"]["ratio"] == pytest.approx(1, abs=0.01)
    reactivity = report_of(*paced_events(slow_after_prompt=2))["reactivity"]
    assert reactivity["after_prompt_vs_others"]["ratio"] == pytest.approx(2, abs=0.01)


def test_reactivity_says_so_when_nothing_is_comparable():
    events, rows = paced_events()
    later = [event for event in events if event["ts"] >= START + 14 * DAY_MS]
    report = report_of(later, rows[20:])
    assert report["reactivity"]["since_vs_before"] is None
    assert "too few comparable answers" in format_report(report)


def test_too_little_data_still_reports_and_fails_the_gate():
    rows = [(1, START, 3, 10_000, 0, 100, 0, 0, 0)]
    events = [{
        "t": "prompt", "kind": "answer", "ts": START, "run": RUN, "seq": 1,
        "rev_id": START, "wording_v": 5, "answers": {"effort": 2}, "skipped": [],
    }]
    report = report_of(events, rows)
    assert (report["cards"], report["work"], report["model"], report["judge"]) == (None,) * 4
    assert report["gate"]["passed"] is False
    assert not any("converge" in reason for reason in report["gate"]["reasons"])
    text = format_report(report)
    for line in ("Cards: no difficulty yet", "Work: no fit yet", "Judge: it takes",
                 "effort ratings", "rated days"):
        assert line in text + gate_text(report["gate"]), line
    json.dumps(report)


def judged(margin=0.02, z=2.0, converged=True, blocks=3):
    return {"blocks": [{}] * blocks, "margin": margin, "z": z, "converged": converged}


def test_the_gate_passes_only_with_enough_data_and_a_real_gain():
    # A small plain margin is all a real gain can show: measures agree closely.
    passed = ship_gate(GATE_EFFORT_RATINGS, GATE_RATED_DAYS, judged(), True)
    assert passed["passed"] is True and passed["reasons"] == []
    assert "PASSED" in gate_text(passed) and "NOT" not in gate_text(passed)


@pytest.mark.parametrize("gate,reason", [
    (ship_gate(GATE_EFFORT_RATINGS - 1, GATE_RATED_DAYS, judged(), True), "effort ratings"),
    (ship_gate(GATE_EFFORT_RATINGS, GATE_RATED_DAYS - 1, judged(), True), "rated days"),
    (ship_gate(GATE_EFFORT_RATINGS, GATE_RATED_DAYS, judged(z=1.0), True), "needs 1.64"),
    (ship_gate(GATE_EFFORT_RATINGS, GATE_RATED_DAYS, judged(margin=-0.1), True), "by -0.100"),
    (ship_gate(GATE_EFFORT_RATINGS, GATE_RATED_DAYS, judged(), False), "converge"),
    (ship_gate(GATE_EFFORT_RATINGS, GATE_RATED_DAYS, judged(converged=False), True), "converge"),
    (ship_gate(GATE_EFFORT_RATINGS, GATE_RATED_DAYS, judged(blocks=0), True), "judged"),
    (ship_gate(GATE_EFFORT_RATINGS, GATE_RATED_DAYS, None, True), "judged"),
])
def test_the_gate_says_why_it_failed(gate, reason):
    assert gate["passed"] is False
    assert reason in gate_text(gate)
    assert "NOT PASSED" in gate_text(gate)


def test_a_failed_gate_before_a_fair_trial_asks_for_more_days():
    early = ship_gate(GATE_EFFORT_RATINGS, GATE_RATED_DAYS, judged(z=1.0), True)
    assert "keep rating days" in gate_text(early)
    fair = ship_gate(GATE_EFFORT_RATINGS, FAIR_TRIAL_RATED_DAYS, judged(z=1.0), True)
    assert "keep rating days" not in gate_text(fair)


def test_the_thresholds_go_up_from_fitting_to_shipping():
    assert FIT_EFFORT_RATINGS < GATE_EFFORT_RATINGS
    assert FIT_RATED_DAYS < GATE_RATED_DAYS < FAIR_TRIAL_RATED_DAYS


@pytest.fixture(scope="module")
def heavy_kanji():
    """Kanji cards take thought no input shows, and only the effort ratings can tell."""
    return report_of(*world(kanji_effort=1.0), names={(200, 0): "Kanji / Card"})


def test_only_the_profile_model_holds_note_type_offsets(heavy_kanji):
    report = heavy_kanji
    assert "offsets" not in report["model"]  # what Copy Weights gives
    offsets = report["local_model"]["offsets"]
    # Only their difference counts: the cut points take any common level.
    assert offsets["200:0"] > offsets["100:0"]
    assert not any(row["name"].startswith("template:") for row in report["cards"]["weights"])
    assert [row["label"] for row in report["note_type_offsets"]] == [
        "template:100:0", "Kanji / Card",
    ]
    assert "Kanji / Card: +" in format_report(report)


def test_a_profile_model_scores_with_its_offsets(heavy_kanji):
    model = heavy_kanji["local_model"]
    inputs = dict.fromkeys(MODEL_INPUTS, 0.0)
    gap = math.exp(model["offsets"]["200:0"] - model["offsets"]["100:0"])
    assert effort_model_credit(inputs, model, (200, 0)) == pytest.approx(
        gap * effort_model_credit(inputs, model, (100, 0)),
    )


def test_the_judge_shows_offsets_beside_the_shared_fit(heavy_kanji):
    amount = heavy_kanji["judge"]["amount"]
    assert amount["fitted_offsets"] is not None and amount["fitted"] is not None
    assert amount["fitted_offsets"] != amount["fitted"]
    assert "Fitted with note-type offsets" in format_report(heavy_kanji)


# Files
###########################################################################


def filled_store(folder, simulated):
    store = Store(folder)
    events, rows = simulated
    for event in events:
        store.append(event)
    store.close()
    return store, rows


def test_each_fit_takes_a_new_version_and_saves_a_report(tmp_path, simulated):
    store, rows = filled_store(tmp_path, simulated)
    labels = []
    first = run_fit(store, rows, None, {}, labels.append, card_templates=CARDS)
    assert labels[0] == "Reading the calibration events" and len(labels) > 3
    second = run_fit(store, rows, None, {}, labels.append, card_templates=CARDS)
    assert (first["model"]["version"], second["model"]["version"]) == (1, 2)
    assert first["counts"]["pair_days"] == 70
    assert len(list(tmp_path.glob("report-*.json"))) == 1  # one per day
    assert latest_report(tmp_path) == json.loads(json.dumps(second))
    # Without knowing which template a recording's card is, no pair can be fitted.
    assert run_fit(store, rows, None, {}, labels.append)["counts"]["pair_days"] == 0


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


def test_the_latest_report_is_the_newest_of_this_design(tmp_path):
    assert latest_report(tmp_path) is None
    (tmp_path / "report-20261101.json").write_text('{"format": 2, "a": 1}')
    (tmp_path / "report-20261103.json").write_text('{"format": 2, "a": 2}')
    assert latest_report(tmp_path) == {"format": 2, "a": 2}
    (tmp_path / "report-20261104.json").write_text('{"a": 3}')  # fitted to cost ratings
    assert latest_report(tmp_path) is None
    (tmp_path / "report-20261105.json").write_text('{"a"')
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


def seed_ratings(store, days, taps_per_day=8, setup=None):
    """Days of ten answers each, effort asked on the first ones, each day rated.

    With setup, the answers reach the revlog too; without, ratings wait for a sync."""
    for day in range(days):
        base = START + day * DAY_MS
        for index in range(10):
            rev_id = base + index * MINUTE
            if setup:
                setup.db.connection.execute(
                    "INSERT INTO revlog (id, cid, ease, time) VALUES (?, ?, ?, ?)",
                    (rev_id, day * 100 + index + 1, 1 + index % 4, 3000 + 4000 * index),
                )
            if index < taps_per_day:
                store.append({
                    "t": "prompt", "kind": "answer", "ts": rev_id, "run": RUN, "seq": index,
                    "rev_id": rev_id, "wording_v": 5, "answers": {"effort": 1 + index % 4},
                    "skipped": [],
                })
        end = base + 11 * MINUTE
        store.append({
            "t": "prompt", "kind": "day", "ts": end, "day_end_ms": end, "wording_v": 5,
            "answers": {"amount": 1 + day % 7}, "skipped": [],
        })
    store.close()


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
    # Recordings name a card "Note type/Template".
    cards = tab_module._card_templates(col)
    assert cards["Basic (and reversed card)/Card 2"] == (note.mid, 1)
    assert col.undo_status().undo == can_undo and can_undo


def test_fit_waits_for_the_effort_ratings_and_the_rated_days(app, window, tab_module):
    days = FIT_RATED_DAYS - 1
    taps = FIT_EFFORT_RATINGS // days + 1
    store = Store.for_profile(window.pm.profileFolder())
    seed_ratings(store, days, taps)
    tab = tab_module.CalibrationTab(window)
    assert not tab.btnFit.isEnabled()
    assert f"Rated days: {days}, {FIT_RATED_DAYS} to fit" in tab.labRatings.text()
    assert f"{FIT_RATED_DAYS} rated days" in tab.labFit.text()
    store.append({
        "t": "prompt", "kind": "day", "ts": now_ms(), "day_end_ms": now_ms(), "wording_v": 5,
        "answers": {"amount": 3}, "skipped": [],
    })
    store.close()
    assert tab_module.CalibrationTab(window).btnFit.isEnabled()


# Just enough to fit: every seeded day is rated, a few answers of each on effort.
TAPS_PER_DAY = -(-FIT_EFFORT_RATINGS // FIT_RATED_DAYS)
SEEDED_TAPS = TAPS_PER_DAY * FIT_RATED_DAYS


def fitted_tab(tab_module, window, setup):
    store = Store.for_profile(window.pm.profileFolder())
    seed_ratings(store, FIT_RATED_DAYS, TAPS_PER_DAY, setup)
    tab = tab_module.CalibrationTab(window)
    assert not tab.btnUseFit.isEnabled() and not tab.btnCopyWeights.isEnabled()
    assert "No fit yet" in tab.labFit.text()
    tab.btnFit.click()
    return tab


def test_fit_runs_with_visible_progress_and_shows_the_gate(app, window, tab_module, setup):
    tab = fitted_tab(tab_module, window, setup)
    assert window.taskman.labels[:2] == ["Reading the revlog", "Reading the calibration events"]
    assert "Judging on days no fit has seen" in window.taskman.labels
    assert "Ship gate: NOT PASSED" in tab.labFit.text()
    assert f"{SEEDED_TAPS} of {GATE_EFFORT_RATINGS} effort ratings" in tab.labFit.text()
    assert f"{FIT_RATED_DAYS} of {GATE_RATED_DAYS} rated days" in tab.labFit.text()
    assert "shipped weights" in tab.labFit.text()
    text = tab.txtReport.toPlainText()
    assert f"Cards, which answers are harder, on {SEEDED_TAPS} effort ratings" in text
    assert f"Work, how answers add up, on {FIT_RATED_DAYS} days" in text
    assert tab.btnUseFit.isEnabled() and tab.btnCopyWeights.isEnabled()
    # A reopened dialog finds it again.
    reopened = tab_module.CalibrationTab(window)
    assert reopened.labFit.text() == tab.labFit.text()
    assert reopened.btnUseFit.isEnabled()


def test_a_failed_fit_is_logged_and_told(app, window, tab_module, setup, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("no")

    shown = []
    store = Store.for_profile(window.pm.profileFolder())
    seed_ratings(store, FIT_RATED_DAYS, TAPS_PER_DAY)
    tab = tab_module.CalibrationTab(window)
    monkeypatch.setattr(tab_module, "showWarning", lambda text, parent: shown.append(text))
    monkeypatch.setattr(tab_module, "run_fit", fail)
    tab.btnFit.click()
    assert len(shown) == 1 and "fit failed" in shown[0]
    assert store.error_count() == 1
    assert "Errors: 1" in tab.labRecording.text()


def test_a_report_with_nothing_to_score_with_offers_no_weights(app, window, tab_module):
    store = Store.for_profile(window.pm.profileFolder())
    write_report(store.folder, {**report_of([], [(1, START, 3, 10_000, 0, 100, 0, 0, 0)]),
                                "phone": {}})
    tab = tab_module.CalibrationTab(window)
    assert "Ship gate: NOT PASSED" in tab.labFit.text()
    assert not tab.btnUseFit.isEnabled() and not tab.btnCopyWeights.isEnabled()


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


def test_half_steps_and_confidence_are_counted_per_question():
    from review_heatmap.calibration import report
    from review_heatmap.calibration.dataset import Tap
    def tap(answers, confidence):
        return Tap(None, answers["effort"], answers, None, None, 0.0, confidence)
    taps = [tap({"effort": 2.5, "fresh": 4}, {"effort": 80}),
            tap({"effort": 3, "fresh": 3.5}, {"effort": 60, "fresh": 90}),
            tap({"effort": 2}, {})]
    detail = report._answer_detail(taps)
    assert detail["effort"] == {"answers": 3, "half_steps": 1, "rated": 2, "median": 70}
    assert detail["fresh"] == {"answers": 2, "half_steps": 1, "rated": 1, "median": 90}
    questions = {"effort": None, "gained": None, "know": None, "fresh": None,
                 "richness": {"taps": 4, "notes": 2, "t": None}, "detail": detail,
                 "familiar": None, "focus": None, "wrong": {"ratings": 0},
                 "state": {"rest_strain": {"spearman": None}, "rest_amount": {"t": None}},
                 "versus": {"days": 0}, "sources": [], "deep": {"spearman": None}}
    text = "\n".join(report._question_lines(questions))
    assert "effort 1/3 half steps, 2 sure at median 70%" in text
    assert "richness: 2 notes rated, 4 effort ratings on their cards, too few to test." in text
