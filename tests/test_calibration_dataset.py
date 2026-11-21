import math

import pytest

from test_activity import TODAY, add_review, setup  # noqa: F401
from test_activity import add_cards, fitted  # noqa: F401

from review_heatmap.calibration.dataset import (
    DRIFT_DAYS, MIN_TEMPLATE_RATINGS, PACE_DAYS, SITTING_SLACK_MS, curve_at, design,
    prompt_counts, read_dataset, revlog_sql,
)
from review_heatmap.calibration.store import DAY_MS, Store
from review_heatmap.metrics import MODEL_INPUTS, effort_model_credit

MINUTE = 60_000
HOUR = 60 * MINUTE
START = 1_790_000_000_000
RUN = "run"
WORD, KANJI = "Vocabulary/Word", "Kanji/Card"
TEMPLATES = {WORD: (100, 0), KANJI: (200, 0)}


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


def sheet(rev_id, seq=None, run=RUN, wording=5, **answers):
    """Questions asked once a card was answered; rev_id names that answer."""
    return {
        "t": "prompt", "kind": "answer", "ts": rev_id, "run": run, "seq": seq,
        "rev_id": rev_id, "wording_v": wording, "answers": answers, "skipped": [],
    }


def session(start, end, wording=5, **answers):
    return {
        "t": "prompt", "kind": "session", "ts": end + 20 * MINUTE, "from_ms": start,
        "to_ms": end, "wording_v": wording, "answers": answers, "skipped": [],
    }


def day_rating(end, wording=5, pair_cards=None, versus_day=None, **answers):
    event = {
        "t": "prompt", "kind": "day", "ts": end, "day_end_ms": end, "wording_v": wording,
        "answers": answers, "skipped": [],
    }
    if pair_cards is not None:
        event["pair_cards"] = pair_cards
    if versus_day is not None:
        event["versus_day"] = versus_day
    return event


def answered(dataset, span):
    return [answer.rev_id for answer in dataset.answers[slice(*span)]]


# What happened
###########################################################################


def test_an_undone_answer_goes_with_its_rating():
    kept, undone = START, START + MINUTE
    events = [
        ans(kept), sheet(kept, effort=2),
        ans(undone), sheet(undone, effort=5),
    ]
    dataset = read_dataset(events, [row(kept)])
    assert [tap.answer.rev_id for tap in dataset.taps] == [kept]
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
    store.append(sheet(START, effort=3))
    store.close()
    path = next(tmp_path.glob("events-*.jsonl"))
    path.write_bytes(path.read_bytes() + b'{"t":"prompt","kind":"answer","rev_i')
    dataset = read_dataset(store.read_events(), [row(START)])
    assert [tap.effort for tap in dataset.taps] == [3]


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
        ans(ids[0], seq=1), sheet(ids[0], seq=1, effort=2),
        ans(ids[1], seq=2),
        ans(ids[2], seq=3),
    ]
    logged = read_dataset(events, [row(rev_id) for rev_id in ids]).logged
    assert [answer.after_prompt for answer in logged] == [False, True, False]


def test_answers_are_kept_from_a_month_before_the_first_question():
    old, kept, asked = START, START + 40 * DAY_MS, START + (PACE_DAYS + 40) * DAY_MS
    rows = [row(old), row(kept), row(asked)]
    dataset = read_dataset([ans(asked), sheet(asked, effort=2)], rows)
    assert [answer.rev_id for answer in dataset.answers] == [kept, asked]
    assert dataset.surveys_began == asked
    assert read_dataset([], rows).answers == []


# Cards
###########################################################################


def test_only_effort_in_the_fitted_wording_makes_a_tap():
    first, second, third, fourth = (START + index * MINUTE for index in range(4))
    events = [
        ans(first), sheet(first, effort=4, gained=2),
        ans(second), sheet(second, gained=3),  # no effort
        ans(third), sheet(third, wording=4, cost=6),  # a cost in routine reviews
        ans(fourth), dict(sheet(fourth, effort=2), wording_v=None),
    ]
    dataset = read_dataset(events, [row(rev_id) for rev_id in (first, second, third, fourth)])
    tap, = dataset.taps
    assert (tap.answer.rev_id, tap.effort, tap.answers) == (first, 4, {"effort": 4, "gained": 2})
    assert len(dataset.prompts) == 4  # kept for the counts, not fitted


def test_a_tap_reads_the_answers_nobody_rated_and_nothing_logged():
    rated = START + 40 * DAY_MS
    rows = [
        row(START, ease=3),
        row(START + 10 * DAY_MS, last_ivl=10),
        row(START + 20 * DAY_MS, ease=0, last_ivl=5),  # a reschedule, no answer
        row(START + 30 * DAY_MS, last_ivl=9),
        row(rated, last_ivl=5, ease=1),
    ]
    tap, = read_dataset([sheet(rated, effort=5, wrong=["blank"])], rows).taps
    inputs = dict(zip(MODEL_INPUTS, tap.answer.inputs))
    assert inputs["reps"] == pytest.approx(math.log(3))
    assert inputs["overdue"] == pytest.approx(math.log(2))
    assert inputs["again"] == 1
    assert tap.answer.ease == 1 and tap.answers["wrong"] == ["blank"]


def test_tap_inputs_read_only_earlier_template_days():
    rated = START + 2 * DAY_MS
    rows = [row(START + index, ms=(index + 1) * 1000) for index in range(40)]
    rows += [row(START + DAY_MS + index, ms=4000, cid=2) for index in range(40)]
    rows.append(row(rated, cid=3, ms=999_000))
    rows.append(row(rated + 1, cid=3, ms=999_000))  # same day: must not count
    tap, = read_dataset([sheet(rated, effort=2)], rows).taps
    assert dict(zip(MODEL_INPUTS, tap.answer.inputs))["t_med"] == pytest.approx(math.log(0.4))
    assert tap.answer.norm_ms == 26_000  # time-weighted: where half the time went


def test_a_deleted_card_is_rated_without_a_template():
    rows = [row(START, mid=None, card_ord=None)]
    tap, = read_dataset([sheet(START, effort=2)], rows).taps
    inputs = dict(zip(MODEL_INPUTS, tap.answer.inputs))
    assert tap.answer.template is None
    assert (inputs["rich"], inputs["t_med"]) == (0.0, 0.0)


def test_drift_counts_periods_from_the_first_tap():
    ids = [START, START + 45 * DAY_MS, START + 90 * DAY_MS]
    taps = read_dataset([sheet(rev_id, effort=2) for rev_id in ids], [row(i) for i in ids]).taps
    assert [tap.drift for tap in taps] == pytest.approx([0, 0.5, 1])
    assert DRIFT_DAYS == 90


# Sittings
###########################################################################


def sitting_world(count=5, gap=2 * MINUTE, first=START + 10 * HOUR):
    """Cards shown a minute before their answer, and one answered elsewhere."""
    ids = [first + index * gap for index in range(count)]
    events = []
    for seq, rev_id in enumerate(ids, 1):
        events += [shown(seq, rev_id - MINUTE), ans(rev_id, seq)]
    elsewhere = ids[2] + 30_000
    rows = sorted([row(rev_id) for rev_id in ids] + [row(elsewhere, cid=9)], key=lambda r: r[1])
    return ids, elsewhere, events, rows


def test_a_sitting_holds_everything_answered_between_its_cards():
    ids, elsewhere, events, rows = sitting_world()
    # Its bounds reach the recording tool a little after the cards were shown.
    events.append(session(ids[0] + MINUTE, ids[-1], work=3, focus=4))
    dataset = read_dataset(events, rows)
    sitting, = dataset.sittings
    assert answered(dataset, sitting.span) == sorted([*ids, elsewhere])
    assert sitting.ratings == {"work": 3, "focus": 4} and sitting.day == study_day(ids[-1])


def test_a_card_shown_before_the_slack_is_no_part_of_the_sitting():
    ids, _, events, rows = sitting_world()
    events.append(session(ids[1] - MINUTE + SITTING_SLACK_MS + 1, ids[-1], work=3))
    dataset = read_dataset(events, rows)
    assert answered(dataset, dataset.sittings[0].span)[0] == ids[2]


def test_a_sitting_rated_twice_keeps_the_later_rating_and_one_unsynced_waits():
    ids, _, events, rows = sitting_world()
    events += [
        session(ids[0], ids[-1], work=2),
        dict(session(ids[0], ids[-1], work=4), ts=ids[-1] + HOUR),
        session(ids[-1] + 2 * HOUR, ids[-1] + 3 * HOUR, work=5),  # its answers haven't synced
        session(ids[0], ids[-1], wording=4, work=1),
    ]
    dataset = read_dataset(events, rows)
    assert [sitting.ratings for sitting in dataset.sittings] == [{"work": 4}]


def test_a_curve_reads_straight_between_its_points_and_flat_beyond_them():
    curve = ((100, 2), (200, 4), (200, 1), (400, 1.5))
    assert curve_at(curve, 50) == 2 and curve_at(curve, 150) == 3
    assert curve_at(curve, 300) == 1.25 and curve_at(curve, 900) == 1.5


def test_a_tap_reads_its_sittings_curve_at_its_answer():
    ids, _, events, rows = sitting_world()
    events += [sheet(ids[1], 2, effort=3), sheet(ids[3], 4, effort=2), sheet(ids[-1] + HOUR, effort=1)]
    # Wording 6 draws concentration instead of asking it: a sitting with a curve and no answer.
    rated = dict(session(ids[0], ids[-1], wording=6), curve=((ids[0], 2), (ids[-1], 4)))
    events.append(rated)
    rows.append(row(ids[-1] + HOUR, cid=3))
    dataset = read_dataset(events, rows)
    assert [sitting.curve for sitting in dataset.sittings] == [((ids[0], 2), (ids[-1], 4))]
    assert [tap.focus for tap in dataset.taps] == [2.5, 3.5, None]


def test_a_tap_takes_the_focus_of_its_sitting():
    ids, _, events, rows = sitting_world()
    events += [sheet(ids[1], 2, effort=3), sheet(ids[-1] + HOUR, effort=1)]
    events.append(session(ids[0], ids[-1], focus=2))
    rows.append(row(ids[-1] + HOUR, cid=3))
    taps = read_dataset(events, rows).taps
    assert [tap.focus for tap in taps] == [2, None]


# Days
###########################################################################


def test_a_day_rating_finds_the_study_day_and_only_what_came_before_it():
    rows = [
        row(START + DAY_MS + HOUR),
        row(START + DAY_MS + 2 * HOUR),
        row(START + DAY_MS + 3 * HOUR),  # after the rating
        row(START + 2 * DAY_MS + HOUR),
    ]
    events = [
        ans(rows[0][1]),
        day_rating(START + DAY_MS + 2 * HOUR + 1, amount=4, learned=2),
        day_rating(START + 3 * DAY_MS, learned=5),
    ]
    dataset = read_dataset(events, rows)
    first, second = dataset.days
    assert (first.day, first.ratings) == (1, {"amount": 4, "learned": 2})
    assert answered(dataset, first.span) == [rows[0][1], rows[1][1]]
    assert [answer.logged for answer in dataset.answers[slice(*first.span)]] == [True, False]
    assert (second.day, second.ratings) == (2, {"learned": 5})


def test_a_day_rated_twice_keeps_the_later_rating():
    rows = [row(START + HOUR)]
    events = [day_rating(START + 2 * HOUR, amount=3), day_rating(START + 3 * HOUR, amount=5)]
    assert [day.ratings for day in read_dataset(events, rows).days] == [{"amount": 5}]


def test_a_skipped_emptied_or_earlier_day_is_no_rated_day():
    rows = [row(START + HOUR)]
    events = [
        day_rating(START + DAY_MS),
        day_rating(START + 5 * DAY_MS, amount=3),  # its answers are gone
        day_rating(START + 2 * HOUR, wording=4, tiring=3),
    ]
    assert read_dataset(events, rows).days == []


def pair_day(*names, mids=(100, 200)):
    rows = [row(START + HOUR, cid=1, mid=mids[0]), row(START + 2 * HOUR, cid=2, mid=mids[1])]
    events = [day_rating(START + 3 * HOUR, amount=4, pair=2, pair_cards=list(names))]
    return read_dataset(events, rows, templates=TEMPLATES).days[0].pair


def test_pair_names_two_templates_answered_that_day():
    assert pair_day(WORD, KANJI) == ((100, 0), (200, 0))
    assert pair_day(KANJI, WORD) == ((200, 0), (100, 0))  # the rating grows with the second
    assert pair_day(WORD, "Other/Card") is None
    assert pair_day(WORD, WORD) is None
    assert pair_day(WORD, KANJI, mids=(100, 100)) is None  # no Kanji answered
    assert pair_day(WORD) is None


def test_versus_spans_the_whole_day_it_names():
    rows = [
        row(START + DAY_MS + HOUR), row(START + DAY_MS + 5 * HOUR),
        row(START + 3 * DAY_MS + HOUR),
    ]
    end = START + 3 * DAY_MS + 2 * HOUR
    dataset = read_dataset([day_rating(end, amount=5, versus=4, versus_day=START + DAY_MS)], rows)
    assert answered(dataset, dataset.days[0].versus) == [rows[0][1], rows[1][1]]
    later = read_dataset([day_rating(end, amount=5, versus=4, versus_day=START + 3 * DAY_MS)], rows)
    assert later.days[0].versus is None
    unanswered = read_dataset([day_rating(end, amount=5, versus_day=START + DAY_MS)], rows)
    assert unanswered.days[0].versus is None


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
    dataset = read_dataset([day_rating(end, amount=3)], rows)
    assert dataset.days[0].day == day
    credits = [
        effort_model_credit(dict(zip(MODEL_INPUTS, answer.inputs)), model)
        for answer in dataset.answers[slice(*dataset.days[0].span)]
    ]
    scored = setup.reporter._cards_done(with_durations=True)[-1]
    assert scored[0] == day
    assert sorted(bucket[2] for bucket in scored[3]) == pytest.approx(sorted(credits))


def test_recall_gaps_reach_the_fit_and_the_day_scores_as_the_calendar_reads_them():
    from review_heatmap.calibration.report import _day_scores
    from review_heatmap.memory import RecallIndex
    from review_heatmap.metrics import activity_value, answer_effort, fsrs_calibration

    late, early = START + HOUR, START + 2 * HOUR
    index = RecallIndex()
    index.add(late, 0.8, 0.0)
    index.add(early, 0.0, -0.3)
    rows = [row(START), row(late, cid=2, last_ivl=10), row(early, cid=2, last_ivl=0)]
    events = [sheet(late, effort=4), day_rating(START + DAY_MS - 1, amount=4)]
    dataset = read_dataset(events, rows, recall=index)
    tap, = dataset.taps
    inputs = dict(zip(MODEL_INPUTS, tap.answer.inputs))
    assert (inputs["recall"], inputs["same_day_recall"]) == (0.8, 0.0)
    assert inputs["day_load"] == pytest.approx(math.log(2))  # second answer of the day
    day, = dataset.days
    answers = dataset.answers[slice(*day.span)]
    assert [answer.recall for answer in answers] == [(0.0, 0.0), (0.8, 0.0), (0.0, -0.3)]
    calibration = fsrs_calibration({})
    assert _day_scores(answers, {}, {})["fsrs"] == pytest.approx(activity_value(3, 30_000, "fsrs", [
        (10_000, 1, answer_effort(True, 0, 0.0, 0.0, calibration)),
        (10_000, 1, answer_effort(True, 10, 0.8, 0.0, calibration)),
        (10_000, 1, answer_effort(False, 0, 0.0, -0.3, calibration)),
    ]))
    without = read_dataset(events, rows)
    assert dict(zip(MODEL_INPUTS, without.taps[0].answer.inputs))["recall"] == 0


# Design
###########################################################################


def taps_of(*groups):
    """groups are (mid, count, focus): effort taps a day apart, each in a sitting rated focus."""
    rows, events, rev_id, seq = [], [], START, 0
    for mid, count, focus in groups:
        for _ in range(count):
            seq += 1
            rows.append(row(rev_id, cid=rev_id, mid=mid))
            events += [shown(seq, rev_id - MINUTE), ans(rev_id, seq), sheet(rev_id, seq, effort=2)]
            if focus is not None:
                events.append(session(rev_id - MINUTE, rev_id, focus=focus))
            rev_id += DAY_MS
    return read_dataset(events, rows).taps


def test_the_shared_design_has_no_note_type_offsets():
    taps = taps_of((100, MIN_TEMPLATE_RATINGS, 4), (300, MIN_TEMPLATE_RATINGS, None))
    assert design(taps)[3] == ["focus", "familiar", "drift"]


def test_design_centres_the_controls_and_offsets_only_well_rated_templates():
    taps = taps_of(
        (100, MIN_TEMPLATE_RATINGS, 4),
        (200, MIN_TEMPLATE_RATINGS - 1, 2),
        (300, MIN_TEMPLATE_RATINGS, None),
    )
    rows, ratings, names, controls = design(taps, note_type_offsets=True)
    assert controls == ["focus", "familiar", "drift", "template:100:0", "template:300:0"]
    assert names == [*MODEL_INPUTS, *controls]
    assert ratings == [2] * len(taps)
    values = [row[names.index("focus")] for row in rows]
    given = values[:2 * MIN_TEMPLATE_RATINGS - 1]
    assert sum(given) == pytest.approx(0)
    assert values[0] > 0 > values[MIN_TEMPLATE_RATINGS]
    assert values[-1] == 0  # a tap outside any rated sitting says nothing either way
    drift = names.index("drift")
    assert sum(row[drift] for row in rows) == pytest.approx(0)  # scored at an average moment
    assert rows[60][drift] - rows[0][drift] == pytest.approx(60 / DRIFT_DAYS)
    offset = names.index("template:100:0")
    assert [row[offset] for row in (rows[0], rows[MIN_TEMPLATE_RATINGS])] == [1, 0]
    assert rows[-1][names.index("template:300:0")] == 1


def test_design_centres_familiar_on_the_new_cards_that_were_asked():
    ids = [START + index * MINUTE for index in range(3)]
    events = [
        sheet(ids[0], effort=3, familiar=0), sheet(ids[1], effort=2, familiar=2),
        sheet(ids[2], effort=1),
    ]
    rows, _, names, _ = design(read_dataset(events, [row(rev_id) for rev_id in ids]).taps)
    assert [row[names.index("familiar")] for row in rows] == [-1, 1, 0]


def test_design_offsets_ignore_a_deleted_card():
    taps = taps_of((None, MIN_TEMPLATE_RATINGS, None))
    assert design(taps, note_type_offsets=True)[3] == ["focus", "familiar", "drift"]


# Counts
###########################################################################


def test_prompt_counts_count_the_counted_wording_and_set_the_rest_apart():
    events = [
        sheet(START, wording=6, effort=2, gained=1),
        dict(sheet(START + 1, wording=6, effort=1), skipped=["know"]),
        sheet(START + 2, wording=4, cost=3),
        # Card answers count from wording 6 on; the fit still reads this one.
        sheet(START + 3, effort=3),
        session(START, START + HOUR, work=3, focus=2),
        day_rating(START + 2 * HOUR, amount=4, pair=2),
    ]
    assert prompt_counts(events) == {
        "effort": 2, "amount": 1, "work": 1, "pair": 1,
        "prompts": 4, "skipped": 1, "earlier": 2,
    }
