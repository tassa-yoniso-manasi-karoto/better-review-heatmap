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
    assert data.diagnostics == dict(files=0, unresolved=0, ambiguous=0)


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
    assert data.diagnostics == dict(files=1, ambiguous=1, unresolved=1)


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
    assert dataset.rated[0].why == (4 if kept else None)
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


def test_day_and_sharpness_have_dataset_fields():
    data = merge([event('q', 90_000), event('a', 95_000),
                  event('prompt', 91_000, kind='sharpness', answers={'sharpness': 7}),
                  dict(v=1, id='day', t='prompt', ts=101_000, kind='day', answers={'tiring': 7, 'learned': 1})])
    dataset = read_dataset(data.events, data.rows)
    assert dataset.days[0].tiring == 7
    assert dataset.days[0].learned == 1


def test_front_panel_correction_preserves_split_and_does_not_mutate_rows():
    original = row()
    data = merge([event('q', 90_000), event('prompt', 93_000, kind='sharpness', answers={'sharpness': 4}, panel_ms=2000), event('a', 95_000)], [original])
    logged = read_dataset(data.events, data.rows).logged[0]
    assert logged.front_ms == 3000
    assert logged.back_ms == 5000
    assert original[3] == 10_000


def test_run_fit_uses_phone_baseline_and_corrected_rows(tmp_path, monkeypatch):
    from review_heatmap.calibration import report
    from review_heatmap.calibration.store import Store
    media = tmp_path / 'media'
    media.mkdir()
    events = [event('q', 90_000), event('a', 95_000),
              event('prompt', 98_000, kind='answer', answers={'cost': 6}, panel_ms=2000)]
    (media / '_brh_cal_a.txt').write_text('\n'.join(map(json.dumps, events)))
    store = Store(tmp_path / 'calibration')
    def build(dataset, state, *args):
        assert state['recording_since'] == 90_000
        assert len(dataset.rated) == 1
        assert dataset.logged[0].taken_ms == 8000
        return {'recording_since': state['recording_since']}
    monkeypatch.setattr(report, 'build_report', build)
    result = report.run_fit(store, [row()], None, {}, lambda text: None, media, INDEX)
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
              event('prompt', 118_000, kind='answer', answers={'cost': 4})]
    before = merge(events)
    assert before.events == []
    after = merge(events, [row(), row(120_000)])
    assert len(read_dataset(after.events, after.rows).rated) == 1


def test_raw_phone_cost_ratings_enable_fit_before_matching(
    tmp_path, addon_modules, monkeypatch,
):
    from types import SimpleNamespace
    from aqt.qt import QApplication
    from review_heatmap.gui import calibration_tab
    app = QApplication.instance() or QApplication([])
    media = tmp_path / 'media'
    media.mkdir()
    events = [event('prompt', index, kind='answer', answers={'cost': 4})
              for index in range(300)]
    (media / '_brh_cal_a.txt').write_text('\n'.join(map(json.dumps, events)))
    monkeypatch.setattr(calibration_tab, 'active_recorder', lambda: None)
    window = SimpleNamespace(
        pm=SimpleNamespace(profileFolder=lambda: str(tmp_path)),
        col=SimpleNamespace(media=SimpleNamespace(dir=lambda: str(media))),
    )
    tab = calibration_tab.CalibrationTab(window)
    assert tab.btnFit.isEnabled()
    assert 'Cost ratings: 300/' in tab.labPrompts.text()
    assert 'Phone: 1 files' in tab.labRecording.text()
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
               event('offer', 92_000, cost='broken')]
    (tmp_path / '_brh_cal_bad.txt').write_text('\n'.join(map(json.dumps, [valid, *damaged])))
    assert read_files(tmp_path).events == [valid]
