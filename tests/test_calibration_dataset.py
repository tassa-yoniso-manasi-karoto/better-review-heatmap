import math

import pytest

from test_activity import TODAY, add_review, setup  # noqa: F401
from test_activity import add_cards, fitted  # noqa: F401

from review_heatmap.calibration.dataset import (
    MIN_TEMPLATE_RATINGS, design, read_dataset, revlog_sql,
)
from review_heatmap.calibration.store import DAY_MS, Store
from review_heatmap.metrics import MODEL_INPUTS, effort_model_credit

MINUTE = 60_000
HOUR = 60 * MINUTE
START = 1_790_000_000_000
RUN = "run"


def study_day(rev_id):
    return (rev_id - START) // DAY_MS


def row(rev_id, cid=1, ease=3, ms=10_000, last_ivl=0, mid=100, card_ord=0, factor=0):
    day = study_day(rev_id)
    return (cid, rev_id, ease, ms, last_ivl, mid, card_ord, day, factor)


def ans(rev_id, seq=None, taken=10_000, run=RUN, ts=None):
    return {
        "t": "ans", "ts": ts or rev_id, "run": run, "seq": seq, "rev_id": rev_id,
        "taken_raw_ms": taken,
    }


def shown(seq, ts, run=RUN):
    return {"t": "q", "ts": ts, "run": run, "seq": seq}


def flipped(seq, ts, run=RUN):
    return {"t": "a", "ts": ts, "run": run, "seq": seq}


def rating(rev_id, seq=None, run=RUN, **answers):
    return {
        "t": "prompt", "kind": "answer", "ts": rev_id, "run": run, "seq": seq,
        "rev_id": rev_id, "answers": answers, "skipped": [],
    }


def sharpness(ts, value, late=False):
    return {
        "t": "prompt", "kind": "sharpness", "ts": ts, "late": late,
        "answers": {"sharpness": value}, "skipped": [],
    }


def day_rating(end, **answers):
    return {
        "t": "prompt", "kind": "day", "ts": end, "day_end_ms": end, "answers": answers,
        "skipped": [],
    }


def test_an_undone_answer_goes_with_its_rating():
    kept, undone = START, START + MINUTE
    events = [
        ans(kept), rating(kept, cost=2),
        ans(undone), rating(undone, cost=5),
    ]
    dataset = read_dataset(events, [row(kept)])
    assert [answer.rev_id for answer in dataset.rated] == [kept]
    assert [answer.rev_id for answer in dataset.logged] == [kept]
    assert dataset.undone == 1


def test_answers_without_an_ans_event_are_unlogged_once_recording_began():
    before, first, phone, later = START, START + HOUR, START + 2 * HOUR, START + 3 * HOUR
    rows = [row(before), row(first), row(phone), row(later)]
    dataset = read_dataset([ans(first), ans(later)], rows)
    assert dataset.unlogged == 1
    assert [answer.rev_id for answer in dataset.logged] == [first, later]


def test_a_crash_truncated_last_line_loses_only_that_line(tmp_path):
    store = Store(tmp_path)
    store.append(ans(START))
    store.append(rating(START, cost=3))
    store.close()
    path = next(tmp_path.glob("events-*.jsonl"))
    path.write_bytes(path.read_bytes() + b'{"t":"prompt","kind":"answer","rev_i')
    dataset = read_dataset(store.read_events(), [row(START)])
    assert [answer.cost for answer in dataset.rated] == [3]


def test_a_null_seq_keeps_the_answer_but_not_its_split():
    dataset = read_dataset([ans(START, seq=None, taken=None)], [row(START)])
    answer = dataset.logged[0]
    assert (answer.taken_ms, answer.front_ms, answer.back_ms) == (None, None, None)
    assert answer.after_prompt is False


def test_the_first_q_and_a_of_a_presentation_set_its_split():
    events = [
        shown(1, START - 9000),
        shown(1, START - 8000),  # a redraw of the same card
        flipped(1, START - 3000),
        flipped(1, START - 1000),
        ans(START, seq=1),
    ]
    answer = read_dataset(events, [row(START)]).logged[0]
    assert (answer.front_ms, answer.back_ms) == (6000, 3000)


def test_runs_restart_seq_without_mixing_their_answers():
    first, second = START, START + MINUTE
    events = [
        shown(1, first - 5000, "a"), flipped(1, first - 1000, "a"), ans(first, 1, run="a"),
        shown(1, second - 2000, "b"), flipped(1, second - 1000, "b"), ans(second, 1, run="b"),
    ]
    logged = read_dataset(events, [row(first), row(second)]).logged
    assert [answer.front_ms for answer in logged] == [4000, 1000]


def test_only_the_card_right_after_an_answer_prompt_is_marked():
    ids = [START, START + MINUTE, START + 2 * MINUTE]
    events = [
        ans(ids[0], seq=1), rating(ids[0], seq=1, cost=2),
        ans(ids[1], seq=2),
        ans(ids[2], seq=3),
    ]
    logged = read_dataset(events, [row(rev_id) for rev_id in ids]).logged
    assert [answer.after_prompt for answer in logged] == [False, True, False]


def test_a_prompt_without_a_cost_is_no_rated_answer():
    events = [ans(START), rating(START, why=2)]
    assert read_dataset(events, [row(START)]).rated == []


def worded(event, version):
    return {**event, "wording_v": version}  # rating() would take it for an answer


def test_cost_and_why_from_before_the_sliders_double_onto_the_new_scales():
    event = worded(rating(START, cost=5, why=2, gain=2, grasp=3), 3)
    dataset = read_dataset([ans(START), event], [row(START)])
    assert (dataset.rated[0].cost, dataset.rated[0].why) == (10, 4)
    assert dataset.prompts[0]["answers"] == {"cost": 10, "why": 4, "gain": 2, "grasp": 3}
    assert event["answers"]["cost"] == 5  # a second read must not double it again


def test_sharpness_and_day_ratings_from_before_the_sliders_stay_as_logged():
    events = [
        ans(START), rating(START, cost=2),
        worded(sharpness(START - 1, 4), 3),
        worded(day_rating(START + DAY_MS, tiring=2, learned=5), 3),
    ]
    dataset = read_dataset(events, [row(START)])
    assert dataset.rated[0].sharpness == 4
    assert (dataset.days[0].tiring, dataset.days[0].learned) == (2, 5)


@pytest.mark.parametrize("version", [{"wording_v": 4}, {}], ids=["sliders", "unversioned"])
def test_answers_on_the_slider_scales_stay_as_logged(version):
    event = {**rating(START, cost=3, why=1), **version}
    answer = read_dataset([ans(START), event], [row(START)]).rated[0]
    assert (answer.cost, answer.why) == (3, 1)


def test_a_rating_reads_the_answers_nobody_rated_and_nothing_logged():
    rated = START + 40 * DAY_MS
    rows = [
        row(START, ease=3),
        row(START + 10 * DAY_MS, last_ivl=10),
        row(START + 20 * DAY_MS, ease=0, last_ivl=5),  # a reschedule, no answer
        row(START + 30 * DAY_MS, last_ivl=9),
        row(rated, last_ivl=5, ease=1),
    ]
    answer = read_dataset([ans(rated), rating(rated, cost=1, why=2)], rows).rated[0]
    assert answer.inputs["reps"] == pytest.approx(math.log(3))
    assert answer.inputs["overdue"] == pytest.approx(math.log(2))
    assert answer.inputs["again"] == 1
    assert answer.why == 2


def test_rated_inputs_read_only_earlier_template_days():
    rated = START + 2 * DAY_MS
    rows = [row(START + index, ms=(index + 1) * 1000) for index in range(40)]
    rows += [row(START + DAY_MS + index, ms=4000, cid=2) for index in range(40)]
    rows.append(row(rated, cid=3, ms=999_000))
    rows.append(row(rated + 1, cid=3, ms=999_000))  # same day: must not count
    inputs = read_dataset([ans(rated), rating(rated, cost=2)], rows).rated[0].inputs
    assert inputs["t_med"] == pytest.approx(math.log(0.4))


def test_a_deleted_card_is_rated_without_a_template():
    rows = [row(START, mid=None, card_ord=None)]
    answer = read_dataset([ans(START), rating(START, cost=2)], rows).rated[0]
    assert answer.template is None
    assert (answer.inputs["rich"], answer.inputs["t_med"]) == (0.0, 0.0)


def test_inputs_match_what_scoring_computes_from_the_same_collection(setup, fitted):
    """Fit and scoring share the input function: same rows, same credit."""
    add_cards(setup, (1, 1, 100), (2, 1, 100), (3, 2, 200))
    yesterday = TODAY - 86400
    add_review(setup, TODAY - 90 * 86400, cid=1)
    add_review(setup, TODAY - 60 * 86400, cid=1, last_interval=30, milliseconds=5000)
    add_review(setup, TODAY - 40 * 86400, cid=2, milliseconds=70000)
    add_review(setup, TODAY - 30 * 86400, cid=3, sequence=1, ease=0)
    add_review(setup, TODAY - 30 * 86400, cid=3, sequence=2, milliseconds=9000)
    add_review(setup, yesterday, cid=1, last_interval=30, ease=1, milliseconds=90000)
    add_review(setup, yesterday, cid=2, sequence=1, last_interval=-600, ease=2)
    add_review(setup, yesterday, cid=3, sequence=2, last_interval=29, ease=4)
    add_review(setup, yesterday, cid=9, sequence=3, last_interval=5)  # deleted card
    weights = dict(zip(MODEL_INPUTS, (0.7, 0.3, -0.2, 0.1, 0.5, 0.2, 0.4, 0.1, -0.3, 0.2, 0.1, 0.05)))
    fitted(intercept=0.1, weights=weights)
    model = {"intercept": 0.1, "weights": weights}
    rows = setup.db.all(revlog_sql(setup.reporter._offset))
    day = rows[-1][7]
    end = max(row[1] for row in rows) + 1
    dataset = read_dataset([day_rating(end, tiring=3)], rows)
    assert dataset.days[0].day == day
    credits = [effort_model_credit(answer.inputs, model) for answer in dataset.days[0].answers]
    scored = setup.reporter._cards_done(with_durations=True)[-1]
    assert scored[0] == day
    assert sorted(bucket[2] for bucket in scored[3]) == pytest.approx(sorted(credits))


def test_a_day_rating_finds_the_study_day_and_all_of_its_answers():
    rows = [
        row(START + DAY_MS + HOUR),
        row(START + DAY_MS + 2 * HOUR),
        row(START + 2 * DAY_MS + HOUR),
    ]
    events = [
        ans(rows[0][1]),
        day_rating(START + 2 * DAY_MS, tiring=4, learned=2),
        day_rating(START + 3 * DAY_MS, learned=5),
    ]
    first, second = read_dataset(events, rows).days
    assert (first.day, first.tiring, first.learned) == (1, 4, 2)
    assert [answer.logged for answer in first.answers] == [True, False]
    assert (second.day, second.tiring, second.learned) == (2, None, 5)


def test_a_skipped_or_emptied_day_is_no_rated_day():
    rows = [row(START + HOUR)]
    events = [
        day_rating(START + DAY_MS),
        day_rating(START + 5 * DAY_MS, tiring=3),  # its answers are gone
    ]
    assert read_dataset(events, rows).days == []


def test_sharpness_belongs_to_its_study_block():
    block = [START + 10 * HOUR, START + 10 * HOUR + MINUTE]
    next_block = START + 14 * HOUR
    late_block = [START + 20 * HOUR, START + 20 * HOUR + MINUTE]
    unrated = START + 24 * HOUR
    rows = [row(rev_id) for rev_id in (*block, next_block, *late_block, unrated)]
    events = [ans(row_[1]) for row_ in rows]
    events += [rating(row_[1], cost=2) for row_ in rows]
    events += [
        sharpness(block[0] - MINUTE, 4),
        sharpness(next_block - 3 * HOUR, 1),  # the owner left without answering
        sharpness(late_block[0] + 1, 2, late=True),
    ]
    rated = read_dataset(events, rows, gap_hours=2).rated
    assert [answer.sharpness for answer in rated] == [4, 4, None, 2, 2, None]


def test_a_skipped_sharpness_prompt_is_ignored():
    event = dict(sharpness(START - 1, 3), answers={}, skipped=["sharpness"])
    events = [ans(START), rating(START, cost=2), event]
    assert read_dataset(events, [row(START)]).rated[0].sharpness is None


def rated_answers(*groups):
    """groups are (mid, count, sharpness) with one rating per answer, a day apart."""
    rows, events, rev_id = [], [], START
    for mid, count, value in groups:
        for _ in range(count):
            rows.append(row(rev_id, cid=rev_id, mid=mid))
            events += [ans(rev_id), rating(rev_id, cost=2)]
            if value is not None:
                events.append(sharpness(rev_id - 1, value))
            rev_id += DAY_MS
    return read_dataset(events, rows).rated


def test_the_shared_design_has_no_note_type_offsets():
    rated = rated_answers((100, MIN_TEMPLATE_RATINGS, 4), (300, MIN_TEMPLATE_RATINGS, None))
    assert design(rated)[3] == ["sharpness", "drift"]


def test_design_centres_sharpness_and_offsets_only_well_rated_templates():
    rated = rated_answers(
        (100, MIN_TEMPLATE_RATINGS, 4),
        (200, MIN_TEMPLATE_RATINGS - 1, 2),
        (300, MIN_TEMPLATE_RATINGS, None),
    )
    rows, ratings, names, controls = design(rated, note_type_offsets=True)
    assert controls == ["sharpness", "drift", "template:100:0", "template:300:0"]
    assert names == [*MODEL_INPUTS, *controls]
    assert ratings == [2] * len(rated)
    values = [row[names.index("sharpness")] for row in rows]
    given = values[:2 * MIN_TEMPLATE_RATINGS - 1]
    assert sum(given) == pytest.approx(0)
    assert values[0] > 0 > values[MIN_TEMPLATE_RATINGS]
    assert values[-1] == 0
    drift = names.index("drift")
    assert sum(row[drift] for row in rows) == pytest.approx(0)  # scored at an average moment
    assert rows[60][drift] - rows[0][drift] == pytest.approx(60 / 90)
    offset = names.index("template:100:0")
    assert [row[offset] for row in (rows[0], rows[MIN_TEMPLATE_RATINGS])] == [1, 0]
    assert rows[-1][names.index("template:300:0")] == 1


def test_design_offsets_ignore_a_deleted_card():
    rated = rated_answers((None, MIN_TEMPLATE_RATINGS, None))
    assert design(rated, note_type_offsets=True)[3] == ["sharpness", "drift"]
