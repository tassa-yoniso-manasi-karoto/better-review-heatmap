"""Phone timestamps must never manufacture a button or match a different card."""
import json

import pytest

from test_calibration_recorder import col  # noqa: F401

from review_heatmap.calibration.dataset import read_dataset
from review_heatmap.calibration.phone import PhoneFiles, merge_phone, read_files

CARD = 'Vocabulary/Word'
INDEX = {CARD: {'extra::คำ': {1}, 'x&amp;y': {2}, '123': {3}, 'same': {4, 5}}}


def event(type_, ts, key='extra::คำ', **extra):
    return dict(v=1, id=f'{type_}-{ts}', t=type_, ts=ts, card=CARD, key=key, **extra)


def row(ts=100_000, cid=1, ease=3, duration=10_000, ivl=3):
    return [cid, ts, ease, duration, ivl, 10, 0, 1, 2500]


def merge(events, rows=None):
    return merge_phone(PhoneFiles(events, 1), rows or [row()], INDEX)


def test_no_files_changes_nothing(tmp_path):
    data = merge_phone(read_files(tmp_path), [row()], {})
    assert data.events == []
    assert data.rows == [row()]
    assert data.earliest_ms is None
    assert data.diagnostics == dict(files=0, unresolved=0, ambiguous=0, unshown=0,
                                    credit_gap_ms=None, credit_answers=0)


def test_files_are_deduped_and_damaged_lines_ignored(tmp_path):
    valid = json.dumps(event('q', 90_000))
    for name in ('_brh_cal_a.txt', '_brh_cal_b_123.txt'):
        (tmp_path / name).write_text(valid + '\n{"v":99}\nnull\n{"truncated"', encoding='utf-8')
    (tmp_path / 'unrelated.txt').write_text(valid)
    assert read_files(tmp_path) == PhoneFiles([event('q', 90_000)], 2)


@pytest.mark.parametrize('key,cid', [('extra::คำ', 1), ('x&amp;y', 2), ('123', 3)])
def test_exact_raw_keys(key, cid):
    data = merge([event('q', 90_000, key), event('a', 95_000, key)], [row(cid=cid)])
    logged = read_dataset(data.events, data.rows).logged
    assert len(logged) == 1
    assert logged[0].front_ms == logged[0].back_ms == 5000


def test_ambiguous_and_unresolved_keys_are_dropped_and_counted():
    data = merge([event('q', 80_000, 'same'), event('a', 90_000, 'same'), event('q', 95_000, 'x&y')])
    assert not data.events
    assert data.diagnostics == dict(files=1, ambiguous=1, unresolved=1, unshown=0,
                                    credit_gap_ms=None, credit_answers=0)


def test_first_question_and_answer_survive_redraws():
    data = merge([event('q', 90_000), event('a', 94_000), event('q', 95_000), event('a', 98_000)])
    answer = read_dataset(data.events, data.rows).logged[0]
    assert answer.front_ms == 4000
    assert answer.back_ms == 6000


def test_abandoned_and_undone_presentations_do_not_create_answers():
    data = merge([event('q', 90_000), event('a', 95_000), event('q', 110_000), event('a', 115_000)], [row(120_000, cid=2)])
    assert not data.events
    assert not merge([event('q', 1), event('a', 2)], [row(4_000_000)]).events


def test_previous_answer_bounds_the_window():
    data = merge([event('q', 90_000), event('a', 95_000)], [row(), row(110_000)])
    assert len(read_dataset(data.events, data.rows).logged) == 1


@pytest.mark.parametrize('ease,ivl,kept', [(1, 3, True), (3, 3, False), (1, 0, False)])
def test_why_joins_cost_only_on_review_again(ease, ivl, kept):
    data = merge([event('q', 90_000), event('a', 92_000),
                  event('prompt', 96_000, kind='answer', answers={'cost': 6, 'gain': 2}, panel_ms=2000),
                  event('prompt', 98_000, kind='answer', answers={'why': 4}, panel_ms=1000)],
                 [row(ease=ease, ivl=ivl)])
    dataset = read_dataset(data.events, data.rows)
    prompt, = (e for e in dataset.prompts if e['kind'] == 'answer')
    assert prompt['answers'].get('why') == (4 if kept else None)
    assert data.rows[0][3] == 7000
    assert dataset.logged[0].taken_ms == 7000
    assert dataset.logged[0].back_ms == 5000


def test_unanswered_offer_is_skip_and_long_probability_uses_uncapped_time():
    data = merge([event('q', 10_000), event('a', 20_000),
                  event('offer', 21_000, cost={'rotation': 'gain', 'p_base': .06, 'p_long': .42}, why_p=.3)])
    prompt = next(e for e in data.events if e['t'] == 'prompt')
    assert prompt['skipped'] == ['cost', 'gain']
    assert prompt['answer_p'] == .42


def test_delayed_offer_that_never_appeared_is_not_a_skip():
    cost = {'rotation': 'gain', 'show_at_ms': 60_000, 'p_base': .06, 'p_long': .42}
    short = merge([event('q', 50_000), event('a', 60_000), event('offer', 61_000, cost=cost, why_p=0)])
    assert not [e for e in short.events if e['t'] == 'prompt']
    long = merge([event('q', 10_000), event('a', 20_000), event('offer', 21_000, cost=cost, why_p=0)])
    assert next(e for e in long.events if e['t'] == 'prompt')['skipped'] == ['cost', 'gain']


def test_why_offer_left_alone_only_counts_on_a_real_lapse():
    offer = event('offer', 96_000, cost=None, why=True, why_p=.3)
    shown = [event('q', 90_000), event('a', 95_000), offer]
    assert not [e for e in merge(shown).events if e['t'] == 'prompt']
    prompt = next(e for e in merge(shown, [row(ease=1)]).events if e['t'] == 'prompt')
    assert prompt['skipped'] == ['why'] and prompt['answer_p'] == 0


def test_day_ratings_reach_the_dataset_and_earlier_wordings_stay_unread():
    data = merge([event('q', 90_000), event('a', 95_000),
                  event('prompt', 91_000, kind='sharpness', answers={'sharpness': 7}),
                  dict(v=1, id='old', t='prompt', ts=100_500, kind='day',
                       answers={'tiring': 7, 'learned': 1}),
                  dict(v=1, id='day', t='prompt', ts=101_000, kind='day', wording_v=5,
                       answers={'amount': 6, 'learned': 1})])
    dataset = read_dataset(data.events, data.rows)
    assert [day.ratings for day in dataset.days] == [{'amount': 6, 'learned': 1}]
    assert {e['kind'] for e in dataset.prompts} == {'sharpness', 'day'}


def test_front_panel_correction_preserves_split_and_does_not_mutate_rows():
    original = row()
    data = merge([event('q', 90_000), event('prompt', 93_000, kind='sharpness', answers={'sharpness': 4}, panel_ms=2000), event('a', 95_000)], [original])
    logged = read_dataset(data.events, data.rows).logged[0]
    assert logged.front_ms == 3000
    assert logged.back_ms == 5000
    assert original[3] == 10_000


def test_run_fit_reads_phone_recordings_on_corrected_rows(tmp_path, monkeypatch):
    from review_heatmap.calibration import report
    from review_heatmap.calibration.store import Store
    media = tmp_path / 'media'
    media.mkdir()
    events = [event('q', 90_000), event('a', 95_000),
              event('prompt', 98_000, kind='answer', answers={'cost': 6}, panel_ms=2000),
              dict(v=1, id='day', t='prompt', ts=101_000, kind='day', wording_v=5,
                   answers={'amount': 4, 'pair': 2}, pair_cards=[CARD, 'Other/Card'])]
    (media / '_brh_cal_a.txt').write_text('\n'.join(map(json.dumps, events)))
    store = Store(tmp_path / 'calibration')
    def build(dataset, conf, names, version, progress):
        assert dataset.logged[0].taken_ms == 8000
        assert [day.ratings for day in dataset.days] == [{'amount': 4, 'pair': 2}]
        # The second card was never answered: no pair to fit on.
        assert dataset.days[0].pair is None
        return {}
    monkeypatch.setattr(report, 'build_report', build)
    result = report.run_fit(store, [row()], None, {}, lambda text: None, media, INDEX,
                            card_templates={CARD: (10, 0), 'Other/Card': (11, 0)})
    assert result['phone']['unresolved'] == 0
    assert result['phone']['files'] == 1
    store.close()


def test_collection_index_uses_raw_first_field_and_numeric_note_id(addon_modules, col):
    from review_heatmap.gui.calibration_tab import _phone_index
    model = col.models.by_name('Basic')
    note = col.new_note(model)
    note['Front'], note['Back'] = 'x&amp;y', 'answer'
    col.add_note(note, 1)
    name = model['name'] + '/' + model['tmpls'][0]['name']
    index = _phone_index(col, {name})[name]
    assert index['x&amp;y'] == {note.cards()[0].id}
    assert index[str(note.id)] == index['x&amp;y']
    assert 'x&y' not in index


def test_pending_surveys_are_joined_after_revlog_sync():
    events = [event('q', 110_000), event('a', 115_000),
              sheet(115_001, 123_000, answers={'effort': 4})]
    before = merge(events)
    assert before.events == []
    after = merge(events, [row(), row(120_000)])
    assert [tap.effort for tap in read_dataset(after.events, after.rows).taps] == [4]


def test_raw_phone_ratings_enable_fit_before_matching(
    tmp_path, addon_modules, monkeypatch,
):
    from types import SimpleNamespace
    from aqt.qt import QApplication
    from review_heatmap.gui import calibration_tab
    from review_heatmap.calibration.report import FIT_EFFORT_RATINGS, FIT_RATED_DAYS
    app = QApplication.instance() or QApplication([])
    media = tmp_path / 'media'
    media.mkdir()
    events = [sheet(index, index + 1000, answers={'effort': 2})
              for index in range(FIT_EFFORT_RATINGS)]
    events += [dict(v=1, id=f'day-{index}', t='prompt', ts=10_000 + index, kind='day',
                    wording_v=5, answers={'amount': 4})
               for index in range(FIT_RATED_DAYS - 1)]
    (media / '_brh_cal_a.txt').write_text('\n'.join(map(json.dumps, events)))
    monkeypatch.setattr(calibration_tab, 'active_recorder', lambda: None)
    window = SimpleNamespace(
        pm=SimpleNamespace(profileFolder=lambda: str(tmp_path)),
        col=SimpleNamespace(media=SimpleNamespace(dir=lambda: str(media))),
    )
    tab = calibration_tab.CalibrationTab(window)
    assert not tab.btnFit.isEnabled()  # one rated day short
    events.append(dict(v=1, id='day-last', t='prompt', ts=20_000, kind='day', wording_v=5,
                       answers={'amount': 5}))
    (media / '_brh_cal_b.txt').write_text(json.dumps(events[-1]))
    tab.deleteLater()
    tab = calibration_tab.CalibrationTab(window)
    assert tab.btnFit.isEnabled()
    assert f'Effort ratings: {FIT_EFFORT_RATINGS}, {FIT_EFFORT_RATINGS} to fit' in tab.labRatings.text()
    assert f'Rated days: {FIT_RATED_DAYS}, {FIT_RATED_DAYS} to fit' in tab.labRatings.text()
    assert 'Phone: 2 files' in tab.labRecording.text()
    tab.deleteLater()


def test_abandoned_card_does_not_supply_time_when_it_returns_later():
    events = [event('q', 1000), event('a', 2000),
              event('q', 3000, 'x&amp;y'), event('q', 90_000), event('a', 95_000)]
    data = merge(events)
    answer = read_dataset(data.events, data.rows).logged[0]
    assert answer.taken_ms == 10_000
    assert answer.front_ms == answer.back_ms == 5000


def test_reopened_reviewer_starts_new_presentation_but_background_alone_does_not():
    events = [event('q', 1000), event('a', 2000),
              event('focus', 3000, state='hidden'), event('q', 90_000), event('a', 95_000)]
    data = merge(events)
    assert read_dataset(data.events, data.rows).logged[0].taken_ms == 10_000
    events = [event('q', 90_000), event('focus', 92_000, state='hidden'),
              event('focus', 94_000, state='visible'), event('a', 95_000)]
    data = merge(events)
    assert read_dataset(data.events, data.rows).logged[0].taken_ms == 10_000


def test_sharpness_offer_is_not_an_answer_offer_and_unanswered_offer_is_skip():
    data = merge([event('q', 90_000), event('offer', 90_001, sharpness=True),
                  event('a', 95_000),
                  event('offer', 95_001, cost={'rotation': 'gain', 'p_base': .06, 'p_long': .42}, why=False, why_p=.3)],
                 [row(ease=1)])
    prompts = [e for e in data.events if e['t'] == 'prompt']
    sharp = next(e for e in prompts if e['kind'] == 'sharpness')
    answer = next(e for e in prompts if e['kind'] == 'answer')
    assert sharp['skipped'] == ['sharpness']
    assert answer['answer_p'] == .06
    assert answer['skipped'] == ['cost', 'gain']


def test_semantically_damaged_lines_are_ignored(tmp_path):
    valid = event('q', 90_000)
    damaged = [dict(valid, id='missing-key', key=None),
               dict(v=1, id='no-card', t='q', ts=90_000),
               event('prompt', 91_000, kind='answer', answers=None),
               dict(v=1, id='cardless', t='prompt', ts=91_500, kind='answer', answers={'effort': 2}),
               event('offer', 92_000, cost='broken')]
    (tmp_path / '_brh_cal_bad.txt').write_text('\n'.join(map(json.dumps, [valid, *damaged])))
    assert read_files(tmp_path).events == [valid]


# Wording 5: a card is asked about once it is answered, on a sheet over the next card.


def sheet(ts, at, key='extra::คำ', wording_v=5, **fields):
    """Sent after its card's answer, and stamped back inside that card's review."""
    return event('prompt', ts, key, kind='answer', wording_v=wording_v, after=True, at=at, **fields)


def two_cards(*extra):
    """One card answered at 100 s, the next shown half a second later and answered at 110 s."""
    return [event('q', 90_000), event('a', 95_000), event('q', 100_500, 'x&amp;y'),
            event('a', 106_000, 'x&amp;y'), *extra]


def test_sheet_rates_the_card_before_it_and_takes_its_time_off_the_card_under_it():
    rows = [row(), row(110_000, cid=2, duration=9500)]
    data = merge(two_cards(
        event('offer', 95_001, ask=['effort', 'gained'], p=.33, situation='review'),
        sheet(95_001, 103_500, answers={'effort': 3, 'gained': 2}, rt_ms={'effort': 900},
              sheet_ms=3000, via='leave'),
    ), rows)
    dataset = read_dataset(data.events, data.rows)
    prompt, = (e for e in dataset.prompts if e['kind'] == 'answer')
    assert prompt['rev_id'] == 100_000 and prompt['ease'] == 3
    assert prompt['answers'] == {'effort': 3, 'gained': 2} and prompt['skipped'] == []
    assert prompt['wording_v'] == 5 and prompt['answer_p'] == .33 and prompt['via'] == 'leave'
    assert prompt['sheet_ms'] == 3000 and prompt['panel_ms'] == 0
    tap, = dataset.taps
    assert (tap.answer.rev_id, tap.effort, tap.answers) == (100_000, 3, {'effort': 3, 'gained': 2})
    rated, covered = dataset.logged
    assert data.rows[0][3] == 10_000 and rated.taken_ms == 10_000
    assert data.rows[1][3] == 6500 and covered.taken_ms == 6500
    assert covered.front_ms == 2500 and covered.back_ms == 4000
    assert rows[1][3] == 9500


def test_sheet_already_off_the_clock_is_not_removed_twice():
    # The recording tool gave the sheet's 3 s back: the revlog holds 6.5 s, not 9.5 s.
    rows = [row(), row(110_000, cid=2, duration=6500)]
    data = merge(two_cards(
        sheet(95_001, 103_500, answers={'effort': 3}, sheet_ms=3000, timer_credit_ms=3000),
    ), rows)
    rated, covered = read_dataset(data.events, data.rows).logged
    assert data.rows[1][3] == 6500 and covered.taken_ms == 6500
    assert covered.front_ms == 2500 and covered.back_ms == 4000
    prompt, = (e for e in data.events if e['t'] == 'prompt')
    assert prompt['timer_credit_ms'] == 3000 and prompt['sheet_ms'] == 3000
    assert data.diagnostics['credit_gap_ms'] == 0 and data.diagnostics['credit_answers'] == 1


@pytest.mark.parametrize('credit', [None, -5, True, '3000'])
def test_sheet_without_a_valid_credit_is_still_taken_off_for_fitting(credit):
    fields = {} if credit is None else {'timer_credit_ms': credit}
    data = merge(two_cards(sheet(95_001, 103_500, answers={'effort': 3}, sheet_ms=3000, **fields)),
                 [row(), row(110_000, cid=2, duration=9500)])
    assert data.rows[1][3] == 6500
    prompt, = (e for e in data.events if e['t'] == 'prompt')
    assert prompt['timer_credit_ms'] is None
    assert data.diagnostics['credit_gap_ms'] is None and data.diagnostics['credit_answers'] == 0


# Wording 6: half steps between levels, a confidence per answer, richness and freshness.


def test_wording_6_reads_half_steps_and_confidence_for_answered_questions():
    rows = [row(), row(110_000, cid=2, duration=9500)]
    data = merge(two_cards(sheet(
        95_001, 103_500, wording_v=6, sheet_ms=3000,
        answers={'effort': 2.5, 'know': 27.5, 'fresh': 4, 'richness': 5, 'wrong': ['blank']},
        confidence={'effort': 80, 'know': 40, 'fresh': 101, 'gained': 70, 'wrong': 30, 'richness': True},
        skipped=['gained'])), rows)
    dataset = read_dataset(data.events, data.rows)
    prompt, = (e for e in dataset.prompts if e['kind'] == 'answer')
    # Reasons only count after a real Again, and so does how sure the owner was of them.
    assert prompt['answers'] == {'effort': 2.5, 'know': 27.5, 'fresh': 4, 'richness': 5}
    assert prompt['confidence'] == {'effort': 80, 'know': 40}
    tap, = dataset.taps
    assert tap.effort == 2.5 and tap.confidence == {'effort': 80, 'know': 40}


@pytest.mark.parametrize('value', [2.5, 2.25, 3.0])
def test_half_steps_are_read_from_wording_6_only(value):
    rows = [row(), row(110_000, cid=2, duration=9500)]
    for wording, kept in ((5, False), (6, value == 2.5)):
        data = merge(two_cards(sheet(95_001, 103_500, wording_v=wording, sheet_ms=3000,
                                     answers={'effort': value})), rows)
        prompt, = (e for e in data.events if e['t'] == 'prompt')
        assert prompt['answers'] == ({'effort': value} if kept else {})


def test_revision_brings_its_own_confidence(tmp_path):
    rated = sheet(95_001, 103_500, wording_v=6, answers={'effort': 3}, confidence={'effort': 60},
                  skipped=[], sheet_ms=3000)
    lines = [rated, revision(rated['id'], 200_000, {'effort': 2.5}, confidence={'effort': 90},
                             skipped=[])]
    (tmp_path / '_brh_cal_a.txt').write_text('\n'.join(map(json.dumps, lines)), encoding='utf-8')
    data = merge(two_cards(*read_files(tmp_path).events), [row(), row(110_000, cid=2, duration=9500)])
    merged, = (e for e in data.events if e['t'] == 'prompt')
    assert merged['answers'] == {'effort': 2.5} and merged['confidence'] == {'effort': 90}


def revision(of, ts, answers, **fields):
    return dict(v=1, id=f'revision-{ts}', t='revision', ts=ts, of=of, answers=answers, **fields)


def test_newest_revision_replaces_the_sheets_answers_and_drops_out(tmp_path):
    rated = sheet(95_001, 103_500, answers={'effort': 3, 'gained': 2}, skipped=[], sheet_ms=3000)
    lines = [rated, revision(rated['id'], 200_000, {'effort': 5}, skipped=['gained']),
             revision(rated['id'], 150_000, {'effort': 1}, skipped=[]),
             revision('never-read', 210_000, {'effort': 6})]
    (tmp_path / '_brh_cal_a.txt').write_text(
        '\n'.join(json.dumps(line, ensure_ascii=False) for line in lines), encoding='utf-8')
    files = read_files(tmp_path)
    assert [e['t'] for e in files.events] == ['prompt']
    prompt, = files.events
    assert prompt['answers'] == {'effort': 5} and prompt['skipped'] == ['gained']
    assert prompt['revised'] is True and prompt['withdrawn'] is False
    data = merge(two_cards(*files.events), [row(), row(110_000, cid=2, duration=9500)])
    merged, = (e for e in data.events if e['t'] == 'prompt')
    assert merged['answers'] == {'effort': 5} and merged['skipped'] == ['gained']
    assert merged['revised'] is True and merged['sheet_ms'] == 3000
    assert data.rows[1][3] == 6500
    assert json.loads((tmp_path / '_brh_cal_a.txt').read_text(encoding='utf-8').splitlines()[0]) == rated


def test_withdrawn_answers_leave_the_sheet_shown_and_skipped(tmp_path):
    rated = sheet(95_001, 103_500, answers={'effort': 3}, asked=['effort'], sheet_ms=3000)
    lines = [rated, revision(rated['id'], 200_000, {}, skipped=['effort'], withdrawn=True),
             dict(v=1, id='bad', t='revision', ts=1, answers={'effort': 1})]
    (tmp_path / '_brh_cal_a.txt').write_text('\n'.join(map(json.dumps, lines)), encoding='utf-8')
    data = merge(two_cards(*read_files(tmp_path).events), [row(), row(110_000, cid=2)])
    merged, = (e for e in data.events if e['t'] == 'prompt')
    assert merged['answers'] == {} and merged['skipped'] == ['effort'] and merged['withdrawn'] is True


def test_check_phone_reports_changed_answers(addon_modules):
    from review_heatmap.gui.calibration_tab import _sheet_lines
    data = merge(two_cards(sheet(95_001, 103_500, answers={}, skipped=['effort'], sheet_ms=3000,
                                 revised=True, withdrawn=True)),
                 [row(), row(110_000, cid=2)])
    assert 'Answers changed afterwards: 1, 1 of them withdrawn' in _sheet_lines(data)


def test_sheet_closed_after_the_flip_leaves_the_front_time_alone():
    data = merge(two_cards(sheet(95_001, 108_000, answers={'effort': 1}, sheet_ms=2000)),
                 [row(), row(110_000, cid=2)])
    covered = read_dataset(data.events, data.rows).logged[1]
    assert covered.front_ms == 5500 and covered.back_ms == 2000


@pytest.mark.parametrize('ease,kept', [(1, ['blank', 'form']), (3, None)])
def test_reasons_count_only_after_a_real_again(ease, kept):
    data = merge([event('q', 90_000), event('a', 95_000),
                  sheet(95_001, 101_000, sheet_ms=4000, skipped=['gained', 'bogus'],
                        answers={'effort': 5, 'wrong': ['form', 'made up', 'blank'], 'know': 101})],
                 [row(ease=ease)])
    prompt = next(e for e in data.events if e['t'] == 'prompt')
    assert prompt['answers'].get('wrong') == kept
    assert prompt['answers']['effort'] == 5 and 'know' not in prompt['answers']
    assert prompt['skipped'] == ['gained']
    # Its own card ended before the sheet opened.
    assert data.rows[0][3] == 10_000


def test_sheet_closed_unanswered_is_a_skip_and_a_question_never_shown_is_not():
    closed = merge([event('q', 90_000), event('a', 95_000),
                    event('offer', 95_001, ask=['effort'], p=.05),
                    sheet(95_001, 101_000, answers={}, skipped=['effort'], sheet_ms=800)])
    prompt = next(e for e in closed.events if e['t'] == 'prompt')
    assert prompt['answers'] == {} and prompt['skipped'] == ['effort']
    assert closed.diagnostics['unshown'] == 0
    unshown = merge([event('q', 90_000), event('a', 95_000),
                     event('offer', 95_001, ask=['effort'], p=.05)])
    assert not [e for e in unshown.events if e['t'] == 'prompt']
    assert unshown.diagnostics['unshown'] == 1
    silent = merge([event('q', 90_000), event('a', 95_000), event('offer', 95_001, ask=[])])
    assert silent.diagnostics['unshown'] == 0


def test_earlier_wording_keeps_its_own_rules_beside_the_sheet():
    data = merge([event('q', 90_000), event('a', 92_000),
                  event('prompt', 96_000, kind='answer', wording_v=4, answers={'cost': 6}, panel_ms=2000)])
    dataset = read_dataset(data.events, data.rows)
    prompt, = dataset.prompts
    assert (prompt['wording_v'], prompt['answers']) == (4, {'cost': 6})
    assert dataset.taps == []  # kept as recorded, never fitted
    assert dataset.logged[0].taken_ms == 8000


def test_sitting_and_day_answers_reach_the_dataset_without_a_card(tmp_path):
    from review_heatmap.calibration.dataset import prompt_counts
    from review_heatmap.calibration.report import _category_use
    extra = [dict(v=1, id='sitting', t='prompt', ts=99_000, kind='session', wording_v=5,
                  answers={'work': 4, 'focus': 9, 'deep': 3}, from_ms=80_000, to_ms=99_000),
             dict(v=1, id='day', t='prompt', ts=101_000, kind='day', wording_v=5,
                  answers={'amount': 5, 'strain': 2, 'pair': 1, 'tiring': 0}, pair_cards=['a', 'b'])]
    events = [event('q', 90_000), event('a', 95_000),
              sheet(95_001, 100_800, answers={'effort': 2, 'wrong': ['sense']}), *extra]
    (tmp_path / '_brh_cal_a.txt').write_text('\n'.join(map(json.dumps, events)))
    assert read_files(tmp_path).events == sorted(events, key=lambda e: e['ts'])
    data = merge(events, [row(ease=1)])
    dataset = read_dataset(data.events, data.rows)
    sitting = next(e for e in dataset.prompts if e['kind'] == 'session')
    assert sitting['answers'] == {'work': 4, 'deep': 3} and sitting['from_ms'] == 80_000
    day = next(e for e in dataset.prompts if e['kind'] == 'day')
    assert day['answers'] == {'amount': 5, 'strain': 2, 'pair': 1}
    assert day['pair_cards'] == ['a', 'b']
    rated, = dataset.days
    assert rated.ratings == {'amount': 5, 'strain': 2, 'pair': 1}
    assert rated.pair is None  # its cards' templates aren't known here
    assert [s.ratings for s in dataset.sittings] == [{'work': 4, 'deep': 3}]
    assert [(tap.effort, tap.focus) for tap in dataset.taps] == [(2, None)]
    counts = prompt_counts(dataset.prompts)
    assert (counts['effort'], counts['work'], counts['amount'], counts['earlier']) == (1, 1, 1, 0)
    assert _category_use(dataset.prompts)['wrong'] == {'sense': 1}


@pytest.mark.parametrize('value', [1, 5, 0, 6, True, 3.0, '3', None])
def test_sitting_deep_scale_survives_import(value):
    raw = dict(v=1, id='sitting', t='prompt', ts=99_000, kind='session', wording_v=5,
               answers={'work': 4, 'focus': 2, 'deep': value}, skipped=[],
               from_ms=80_000, to_ms=99_000)
    data = merge([raw])
    sitting = read_dataset(data.events, data.rows).prompts[0]
    expected = {'work': 4, 'focus': 2}
    if type(value) is int and 1 <= value <= 5:
        expected['deep'] = value
    assert sitting['answers'] == expected
    assert sitting['skipped'] == []


@pytest.mark.parametrize('skipped', [[], ['deep']])
def test_sitting_without_deep_stays_valid_before_or_after_the_question_was_added(skipped):
    raw = dict(v=1, id='sitting', t='prompt', ts=99_000, kind='session', wording_v=5,
               answers={'work': 4, 'focus': 2}, skipped=skipped,
               from_ms=80_000, to_ms=99_000)
    data = merge([raw])
    sitting = read_dataset(data.events, data.rows).prompts[0]
    assert sitting['answers'] == {'work': 4, 'focus': 2}
    assert sitting['skipped'] == skipped


def test_check_phone_reports_the_sheets(addon_modules):
    from review_heatmap.gui.calibration_tab import _sheet_lines
    data = merge([event('q', 90_000), event('a', 95_000),
                  sheet(95_001, 101_000, answers={'effort': 2}, via='leave'),
                  event('q', 105_000, 'x&amp;y'), event('a', 106_000, 'x&amp;y'),
                  event('offer', 106_001, 'x&amp;y', ask=['effort'], p=.05)],
                 [row(), row(110_000, cid=2)])
    assert _sheet_lines(data) == [
        'Cards asked about: 1 shown, 1 rated on effort, 1 never shown', 'Shown by: leave 1',
    ]
    assert _sheet_lines(merge([event('q', 90_000), event('a', 95_000)])) == []


def test_check_phone_reports_sheets_already_off_the_clock(addon_modules):
    from review_heatmap.gui.calibration_tab import _sheet_lines
    data = merge(two_cards(
        sheet(95_001, 103_500, answers={'effort': 3}, sheet_ms=3000, timer_credit_ms=3000, via='leave'),
    ), [row(), row(110_000, cid=2, duration=6800)])
    assert _sheet_lines(data)[2:] == [
        'Sheet time already off the clock: 1 of 1 sheets',
        'Answers under them: recorded time is +0.3 s from first sight to answer without the sheet'
        ' (median of 1)',
    ]
