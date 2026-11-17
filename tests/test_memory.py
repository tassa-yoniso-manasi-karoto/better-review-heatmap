"""Recall replayed from the review log: Anki's rules for a card's history, FSRS-6 and FSRS-7."""

import math

import pytest

from review_heatmap.memory import (
    FSRS6_DEFAULT_PARAMETERS, Preset, RecallIndex, Replay, fsrs6_parameters, logit,
)

DAY = 86400
BASE = 20000 * DAY  # a day cutoff; the replay counts days from a later one
CUTOFF = BASE + 100 * DAY

# Reference values from independent ports of Anki's and FSRS's code, checked against
# states AnkiDroid recorded (FSRS-6) and predictions an FSRS-7 build stored. Entries
# are (id, type, ease, ivl, factor); expected is R just before each answer, from
# FSRS-6 a day or more after the previous one, from FSRS-7 on the same day.
STEPS_LAPSE_CRAM_RESCHEDULE = [
        (1728036000000, 0, 1, -60, 500),
        (1728036600000, 0, 3, -600, 500),
        (1728037800000, 0, 3, 1, 500),
        (1728118800000, 1, 3, 3, 500),
        (1728417600000, 1, 2, 5, 500),
        (1729065600000, 1, 1, -600, 500),
        (1729066320000, 2, 3, 1, 500),
        (1729066380000, 3, 3, 0, 0),
        (1729332000000, 1, 4, 20, 500),
        (1729332300000, 4, 0, 30, 2500),
        (1731492000000, 1, 3, 50, 500),
    ]
STEPS_EXPECTED = [None, ('same', 0.7094976937002552), ('same', 0.9356510750871474), ('later', 0.7943861201504139), ('later', 0.8756082071194708), ('later', 0.875270965832491), ('same', 0.8947923824692702), None, ('later', 0.8178062948252925), None, ('later', 0.7572429365738291)]
IMPORTED_THEN_RESET = [
        (1728043200000, 1, 3, 10, 2500),
        (1728907200000, 1, 3, 25, 2500),
        (1730635200000, 4, 0, 0, 0),
        (1730721600000, 1, 3, 4, 2500),
        (1731067200000, 1, 2, 6, 2500),
        (1731088800000, 2, 3, 6, 2500),
    ]
IMPORTED_EXPECTED = [None, ('later', 0.9), None, None, ('later', 0.9), ('same', 0.9710780332531131)]


def recall(gap, target=0.5):
    """R from a gap measured against a desired retention of `target`."""
    return 1 / (1 + math.exp(gap - logit(target)))


def replay_card(entries, preset=None):
    replay = Replay({1: preset or Preset([], 0.5)}, CUTOFF)
    return [replay.feed(1, *entry) for entry in entries]


@pytest.mark.parametrize("entries,expected", [
    (STEPS_LAPSE_CRAM_RESCHEDULE, STEPS_EXPECTED),
    (IMPORTED_THEN_RESET, IMPORTED_EXPECTED),
])
def test_replay_matches_the_reference_ports(entries, expected):
    for (later, same), want in zip(replay_card(entries), expected):
        if want is None:
            assert (later, same) == (0, 0)
        elif want[0] == "later":
            assert same == 0 and recall(later) == pytest.approx(want[1], abs=1e-9)
        else:
            assert later == 0 and recall(same) == pytest.approx(want[1], abs=1e-6)


def test_parameters_are_filled_and_clipped_as_fsrs_does():
    assert fsrs6_parameters([]) == FSRS6_DEFAULT_PARAMETERS
    five = list(FSRS6_DEFAULT_PARAMETERS[:19])
    # FSRS runs with short-term memory on, which floors w19 at 0.01.
    assert fsrs6_parameters(five) == pytest.approx(five + [0.01, 0.5])
    four = [0.4, 0.6, 2.4, 5.8, 4.93, 0.94, 0.86, 0.01, 1.49, 0.14, 0.94, 2.18, 0.05, 0.34,
            1.26, 0.29, 2.61]
    filled = fsrs6_parameters(four)
    assert filled[4] == pytest.approx(4.93 + 2 * 0.94)
    assert filled[5] == pytest.approx(math.log(1 + 3 * 0.94) / 3)
    assert filled[6] == pytest.approx(0.86 + 0.5) and filled[17:] == (0.0, 0.0, 0.01, 0.5)
    assert fsrs6_parameters([1000.0] * 21)[0] == 100  # clipped
    assert fsrs6_parameters([1.0] * 20) is None
    assert fsrs6_parameters([float("nan")] * 21) is None
    assert Preset([1.0] * 5, 0.9).w == FSRS6_DEFAULT_PARAMETERS  # unusable: the defaults


def test_stability_is_the_interval_at_ninety_percent():
    preset = Preset([], 0.9)
    entries = [(BASE * 1000 + 3600000, 0, 3, 1, 500)]
    entries.append((entries[0][0] + int(FSRS6_DEFAULT_PARAMETERS[2]) * DAY * 1000, 1, 3, 5, 500))
    first, second = replay_card(entries, preset)
    assert first == (0, 0)
    days = int(FSRS6_DEFAULT_PARAMETERS[2])
    r = (1 + (0.9 ** (-1 / FSRS6_DEFAULT_PARAMETERS[20]) - 1) * days / FSRS6_DEFAULT_PARAMETERS[2]
         ) ** -FSRS6_DEFAULT_PARAMETERS[20]
    assert second[0] == pytest.approx(logit(0.9) - logit(r))
    assert second[1] == 0


def at(day, hour, minute=0):
    return (BASE + day * DAY + hour * 3600 + minute * 60) * 1000


def test_whole_days_count_from_the_day_cutoff():
    # One minute either side of a cutoff is a day apart; 23 hours within one day are not.
    across = replay_card([(at(0, 23, 59), 0, 3, 1, 500), (at(1, 0, 1), 1, 3, 3, 500)])
    within = replay_card([(at(0, 0, 30), 0, 3, 1, 500), (at(0, 23, 30), 1, 3, 3, 500)])
    assert across[1][0] and not across[1][1]
    assert within[1][1] and not within[1][0]


def test_a_new_learning_run_or_a_reset_starts_the_history_again():
    learned = [(at(0, 10), 0, 3, 1, 500), (at(3, 10), 1, 3, 8, 500)]
    # A manual reschedule breaks the run: the learning answer after it starts afresh.
    rescheduled = learned + [(at(4, 10), 4, 0, 30, 2500), (at(5, 10), 0, 3, 1, 500),
                             (at(6, 10), 1, 3, 4, 500)]
    gaps = replay_card(rescheduled)
    assert gaps[3] == (0, 0)
    assert gaps[4] == replay_card([(at(5, 10), 0, 3, 1, 500), (at(6, 10), 1, 3, 4, 500)])[1]
    # Cramming in a filtered deck never counts, and does not break the run.
    crammed = [(at(0, 10), 0, 1, -60, 500), (at(0, 10, 5), 3, 3, 0, 0),
               (at(0, 10, 10), 0, 3, 1, 500)]
    plain = [crammed[0], crammed[2]]
    assert replay_card(crammed)[2] == replay_card(plain)[1]
    assert replay_card(crammed)[1] == (0, 0)
    # After a reset nothing counts until an answer sets a day or more again.
    reset = learned + [(at(9, 10), 4, 0, 0, 0), (at(9, 11), 2, 1, -600, 2500),
                       (at(9, 12), 1, 3, 10, 2500), (at(19, 12), 1, 3, 25, 2500)]
    gaps = replay_card(reset, Preset([], 0.9))
    assert gaps[3:5] == [(0, 0), (0, 0)]
    assert gaps[5][0] == pytest.approx(0, abs=1e-9)  # SM-2's state: 90% after its interval


def test_answers_before_the_ignore_date_wait_for_an_answer_after_it():
    preset = Preset([], 0.9, ignore_before_ms=at(5, 0))
    entries = [(at(0, 10), 0, 3, 1, 500), (at(2, 10), 1, 3, 6, 500),
               (at(8, 10), 1, 3, 15, 2500), (at(23, 10), 1, 3, 30, 2500)]
    gaps = replay_card(entries, preset)
    assert gaps[:3] == [(0, 0), (0, 0), (0, 0)]
    assert gaps[3][0] == pytest.approx(0, abs=1e-9)  # seeded at the first answer after it


def test_a_card_without_a_preset_reads_nothing():
    replay = Replay({1: Preset([], 0.9)}, CUTOFF)
    assert replay.feed(2, at(0, 10), 0, 3, 1, 500) == (0, 0)
    assert replay.feed(2, at(5, 10), 1, 3, 9, 500) == (0, 0)
    assert 2 not in replay.cards


def test_the_gap_is_measured_against_the_decks_desired_retention():
    entries = [(at(0, 10), 0, 3, 1, 500), (at(9, 10), 1, 3, 20, 500)]
    strict, lenient = replay_card(entries, Preset([], 0.95)), replay_card(entries, Preset([], 0.8))
    assert strict[1][0] - lenient[1][0] == pytest.approx(logit(0.95) - logit(0.8))


def test_recall_index_holds_only_answers_with_a_gap():
    index = RecallIndex()
    index.add(5, 0.0, 0.0)
    index.add(7, 0.4, 0.0)
    index.add(9, 0.0, -0.2)
    assert len(index) == 2
    assert index.get(7) == (0.4, 0.0) and index.get(9) == (0.0, -0.2)
    assert index.get(5) == index.get(8) == index.get(10) == (0.0, 0.0)
