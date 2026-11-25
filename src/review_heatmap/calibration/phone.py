"""Sealed phone recordings joined to the synced revlog. Stdlib only.

The caller resolves raw note keys through the collection API. Nothing here
opens a collection or infers an answer button from template events.
"""

import json
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import NamedTuple

from .dataset import CID, ID, EASE, DURATION, LAST_IVL

# From this wording on, a card is asked about on a sheet shown once it is answered,
# over the card that follows. Earlier wordings asked on the card itself.
SHEET_WORDING = 5
# From this wording on, an answer may lie half a step between two neighbouring levels
# (effort 2.5, know 27.5), and a sheet says how sure the owner was of each answer.
HALF_STEP_WORDING = 6
# Lowest and highest value of each answer. No id has meant two scales, except gained: read
# through GAINED_WORDING, whatever its wording.
SCALES = {
    'cost': (0, 10), 'gain': (0, 6), 'grasp': (0, 10), 'why': (0, 4),
    'sharpness': (1, 7), 'tiring': (1, 7), 'learned': (1, 7),
    'effort': (1, 6), 'familiar': (0, 3), 'know': (0, 100), 'gained': (0, 5), 'fixed': (0, 3),
    'amount': (1, 7), 'strain': (1, 5), 'rest': (1, 5), 'versus': (1, 5), 'pair': (1, 5),
    'work': (1, 5), 'focus': (1, 5), 'deep': (1, 5), 'richness': (1, 5), 'fresh': (1, 5),
    'interfered': (0, 1), 'effort_recall': (1, 5), 'effort_reading': (1, 5),
}
# gained says how much firmer a review left the card: Nothing 0, Kept it alive 1, Firmer 2, and
# from wording 9 Much firmer 3; fixed, asked with it, whether the review fixed something. Up to
# wording 7, gained's 3 and 4 were Fixed something and Something new: never answered, dropped.
# Wording 8 alone mixed both on eleven levels of its own: its answers about firmness alone are read
# on this scale, the others dropped.
GAINED_WORDING = 9
GAINED_EARLIER_MAX = 2
GAINED_WORDING_8 = {0: 0, 0.5: 0.5, 1: 1, 1.5: 1.5, 4: 2, 4.5: 3}
# What went wrong on a failed card: any number of these, kept in this order.
REASONS = ('blank', 'other', 'form', 'sense', 'misread', 'purpose', 'else')
# From wording 10, on a card not failed: whether another word got in the way (interfered, 1 with
# the word in interfered_word, 0 for No). After a word named there or as the one a failed card was
# taken for, how strong its pull was, 0 to 100 (strength, by question).
NAMED = ('confused', 'interfered')
# From wording 11, effort is asked in two parts on one view: the search for the word on the front
# (effort_recall), the reading of the back (effort_reading), five levels each. The fit, which takes
# one rating, reads the larger as effort: the hardest part of the card. The parts are kept as given.
EFFORT_SPLIT_WORDING = 11
# Taken for another word ('other'): how the two are alike, as related-word lists class them.
CONFUSIONS = ('homophone', 'similar-sound', 'tone-pair', 'similar-spelling', 'similar-kanji',
              'similar-meaning', 'pair', 'false-friend')


class PhoneFiles(NamedTuple):
    events: list
    files: int


class PhoneData(NamedTuple):
    events: list
    rows: list
    earliest_ms: object
    diagnostics: dict


def read_files(folder) -> PhoneFiles:
    events = {}
    paths = sorted(Path(folder).glob('_brh_cal_*')) if folder else []
    files = 0
    for path in paths:
        if not path.is_file():
            continue
        try:
            with path.open(encoding='utf-8', errors='replace') as stream:
                files += 1
                for line in stream:
                    try:
                        event = json.loads(line)
                        if (not isinstance(event, dict) or event.get('v') != 1
                                or not isinstance(event.get('id'), str)
                                or not isinstance(event.get('t'), str)
                                or not isinstance(event.get('ts'), int)
                                or isinstance(event['ts'], bool) or event['ts'] < 0):
                            continue
                        if 'card' in event and (not isinstance(event['card'], str)
                                                or not isinstance(event.get('key'), str)):
                            continue
                        if event['t'] in ('q', 'a', 'state', 'offer', 'size', 'audio') and 'card' not in event:
                            continue
                        if event['t'] == 'prompt' and (
                                not isinstance(event.get('answers'), dict)
                                or event.get('kind') not in ('answer', 'sharpness', 'day', 'session')
                                or (event['kind'] in ('answer', 'sharpness') and 'card' not in event)):
                            continue
                        if event['t'] == 'offer' and event.get('cost') is not None and not isinstance(event['cost'], dict):
                            continue
                        if event['t'] == 'revision' and (not isinstance(event.get('of'), str)
                                                         or not isinstance(event.get('answers'), dict)):
                            continue
                        events.setdefault(event['id'], event)
                    except (ValueError, TypeError):
                        continue
        except OSError:
            continue
    return PhoneFiles(sorted(_revised(events), key=lambda event: event['ts']), files)


def _revised(events: dict) -> list:
    """Prompts with the answers of their newest revision; the revisions themselves drop out.

    A recording is never rewritten: a later answer to the same card arrives as a revision
    naming the prompt it replaces. Withdrawn answers leave the prompt shown, all skipped.
    A revision of a prompt not read yet waits for it.
    """
    newest = {}
    for event in events.values():
        if event['t'] == 'revision' and (event['of'] not in newest
                                         or event['ts'] >= newest[event['of']]['ts']):
            newest[event['of']] = event
    out = []
    for event in events.values():
        if event['t'] == 'revision':
            continue
        revision = newest.get(event['id']) if event['t'] == 'prompt' else None
        if revision:
            event = {**event, 'answers': revision['answers'],
                     'confidence': revision.get('confidence', event.get('confidence')),
                     'skipped': revision.get('skipped', []), 'revised': True,
                     'withdrawn': revision.get('withdrawn') is True}
        out.append(event)
    return out


def _number(value, halves: bool):
    """An integer, or with halves a value on a half step; None for anything else. A whole
    number written as a float (3.0) never came from a sheet, so it stays dropped."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int):
        return value
    return value if halves and value != int(value) and value * 2 == int(value * 2) else None


def _answers(raw, halves: bool = False) -> dict:
    """Answers on a known scale; anything else a file holds is dropped. Before wording 6 a
    value between two levels meant nothing, so only then are halves read."""
    answers = {}
    for key, value in raw.items() if isinstance(raw, dict) else ():
        if key == 'wrong':
            reasons = [reason for reason in REASONS if reason in value] if isinstance(value, list) else []
            if reasons:
                answers[key] = reasons
        elif key == 'confused':
            if isinstance(value, str) and value in CONFUSIONS:
                answers[key] = value
        elif key in SCALES:
            number = _number(value, halves)
            if number is not None and SCALES[key][0] <= number <= SCALES[key][1]:
                answers[key] = number
    return answers


def _confidence(raw, answers: dict) -> dict:
    """How sure, 0 to 100, for answers that were given: a confidence alone says nothing."""
    return {key: value for key, value in raw.items()
            if key in answers and not isinstance(value, bool) and isinstance(value, (int, float))
            and 0 <= value <= 100} if isinstance(raw, dict) else {}


def _strength(raw, answers: dict) -> dict:
    """How strong a named word's pull was, 0 to 100: only beside the answer that named it."""
    return {key: value for key, value in raw.items()
            if key in NAMED and answers.get(key) not in (None, 0) and not isinstance(value, bool)
            and isinstance(value, (int, float)) and 0 <= value <= 100} if isinstance(raw, dict) else {}


def _skipped(raw) -> list:
    return [key for key in raw if isinstance(key, str)
            and (key in SCALES or key in ('wrong', 'confused', 'curve'))] if isinstance(raw, list) else []


# The most points a sitting's curve keeps.
CURVE_POINTS = 200


def _curve(raw, start, end) -> list:
    """A sitting's concentration as the rater drew it, from wording 6: [ts, depth] pairs inside the
    sitting, depth from 1 to 5 in half steps, in time order. Anything else is left out."""
    if (not isinstance(raw, list) or isinstance(start, bool) or isinstance(end, bool)
            or not isinstance(start, int) or not isinstance(end, int)):
        return []
    points = []
    for item in raw[:CURVE_POINTS]:
        if (isinstance(item, list) and len(item) == 2 and isinstance(item[0], int)
                and not isinstance(item[0], bool) and start <= item[0] <= end):
            depth = _number(item[1], True)
            if depth is not None and 1 <= depth <= 5:
                points.append([item[0], depth])
    return sorted(points)


def _prompt(event, **fields):
    version = event.get('wording_v')
    halves = isinstance(version, int) and version >= HALF_STEP_WORDING
    answers = _answers(event.get('answers'), halves)
    # A revision keeps its sheet's wording: both are read as the sheet asked.
    if 'gained' in answers:
        gained = answers.pop('gained')
        if version == 8:
            gained = GAINED_WORDING_8.get(gained)
        elif gained > (3 if isinstance(version, int) and version >= GAINED_WORDING else GAINED_EARLIER_MAX):
            gained = None
        if gained is not None:
            answers['gained'] = gained
    if isinstance(version, int) and version >= EFFORT_SPLIT_WORDING and 'effort' not in answers:
        parts = [answers[key] for key in ('effort_recall', 'effort_reading') if key in answers]
        if parts:
            answers['effort'] = max(parts)
    cleaned = {**event, 'run': 'phone', 'answers': answers,
               'confidence': _confidence(event.get('confidence'), answers),
               'strength': _strength(event.get('strength'), answers),
               'skipped': _skipped(event.get('skipped', [])), **fields}
    if event.get('kind') == 'session':
        cleaned['curve'] = _curve(event.get('curve'), event.get('from_ms'), event.get('to_ms'))
    return cleaned


def _is_sheet(event) -> bool:
    version = event.get('wording_v')
    return isinstance(version, int) and version >= SHEET_WORDING


def _credit(event):
    """Sheet time the recording tool already took off the clock of the card under it."""
    value = event.get('timer_credit_ms')
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def merge_phone(phone: PhoneFiles, rows, index) -> PhoneData:
    """Index is {card: {raw key: iterable of card IDs}}; collisions stay visible.

    Only intervals ending in a real answer can produce answer events. A lone
    question, a future survey, or a row removed by undo cannot create an answer.
    """
    if not phone.events:
        return PhoneData([], rows, None,
                         {'files': phone.files, 'unresolved': 0, 'ambiguous': 0, 'unshown': 0,
                          'credit_gap_ms': None, 'credit_answers': 0})
    # A different card, or reopening after hiding the reviewer, starts a new
    # presentation. Within it keep the first q: order redraws must not reset time.
    presentations = {}
    identity = None
    start = None
    hidden = False
    for event in sorted(phone.events, key=lambda e: e['ts']):
        if event['t'] == 'focus' and event.get('state') == 'hidden':
            hidden = True
        if event['t'] == 'q' and 'card' in event:
            following = (event['card'], event['key'])
            if following != identity or hidden:
                start = event['ts']
            identity, hidden = following, False
            presentations[event['id']] = start
    by_card = defaultdict(list)
    unresolved, ambiguous = set(), set()
    output = []
    earliest = min((e['ts'] for e in phone.events if e['t'] != 'meta'), default=None)
    # A sheet is answered over the next card, on that card's clock: when it closed, how
    # long it had been up, and how much of that the recording tool took off the clock.
    sheets = sorted((
        (event['at'], event['sheet_ms'], _credit(event)) for event in phone.events
        if event['t'] == 'prompt' and _is_sheet(event) and event.get('after') is True
        and all(isinstance(event.get(name), int) and not isinstance(event.get(name), bool)
                and event[name] > 0 for name in ('at', 'sheet_ms'))
    ), key=lambda sheet: sheet[:2])
    closed = [at for at, _, _ in sheets]
    unshown = 0
    # Under sheets already off the clock, recorded time should match the time from first
    # sight to answer without the sheet: that gap shows whether the credit works.
    credit_gaps = []
    for event in phone.events:
        if 'card' not in event:
            if event['t'] == 'prompt' and event.get('kind') == 'day':
                output.append(_prompt(event, day_end_ms=event['ts'] + 1, p=1))
            elif event['t'] == 'prompt' and event.get('kind') == 'session':
                output.append(_prompt(event, p=1))
            continue
        identity = (event['card'], event['key'])
        candidates = set(index.get(identity[0], {}).get(identity[1], ()))
        if len(candidates) != 1:
            (ambiguous if candidates else unresolved).add(identity)
            continue
        cid = next(iter(candidates))
        by_card[cid].append(event)
        if event['t'] == 'prompt' and event.get('kind') == 'sharpness':
            output.append(_prompt(event, cid=cid, late=False, p=1))
    for events in by_card.values():
        events.sort(key=lambda e: e['ts'])
    timestamps = {cid: [e['ts'] for e in events] for cid, events in by_card.items()}
    previous = {}
    corrected = []
    seq = 0
    for row in sorted(rows, key=lambda r: r[ID]):
        revised = list(row)
        cid, rev_id = row[CID], row[ID]
        if row[EASE] < 1:
            corrected.append(revised)
            continue
        lower = max(previous.get(cid, -1), rev_id - 3_600_000)
        previous[cid] = rev_id
        events = by_card.get(cid, [])
        times = timestamps.get(cid, [])
        window = events[bisect_right(times, lower):bisect_right(times, rev_id - 1)]
        questions = [e for e in window if e['t'] == 'q']
        latest = max((presentations[e['id']] for e in questions), default=None)
        q = next((e for e in questions if presentations[e['id']] == latest), None)
        a = next((e for e in window if e['t'] == 'a' and q and e['ts'] >= q['ts']), None)
        if q is None:
            corrected.append(revised)
            continue
        window = [e for e in window if e['ts'] >= q['ts']]
        prompts = [e for e in window if e['t'] == 'prompt']
        panel = sum(e.get('panel_ms', 0) for e in prompts
                    if isinstance(e.get('panel_ms', 0), int) and e.get('panel_ms', 0) > 0)
        # Sheets about the card before this one, closed while this one was on screen.
        over = sheets[bisect_left(closed, q['ts']):bisect_left(closed, rev_id)]
        panel = min(panel + sum(ms for _, ms, _ in over), rev_id - q['ts'])
        raw = rev_id - q['ts'] - panel
        if over and all(credit is not None for _, _, credit in over):
            credit_gaps.append(row[DURATION] - raw)
        seq += 1
        common = {'v': 1, 'run': 'phone', 'seq': seq, 'cid': cid, 'rev_id': rev_id}
        output.append({**q, **common})
        if a:
            # Front sheets consume time too; subtract only panels completed before the flip.
            front_panel = sum(e.get('panel_ms', 0) for e in prompts
                              if e['ts'] <= a['ts'] and isinstance(e.get('panel_ms'), int)
                              and e['panel_ms'] > 0)
            front_panel += sum(ms for at, ms, _ in over if at <= a['ts'])
            output.append({**a, **common, 'ts': max(q['ts'], a['ts'] - front_panel)})
        output.append({**common, 't': 'ans', 'ts': rev_id - panel,
                       'ease': row[EASE], 'taken_capped_ms': row[DURATION],
                       'taken_raw_ms': raw})
        # A sheet already off the clock leaves the two equal, so nothing is removed twice.
        if panel:
            revised[DURATION] = min(row[DURATION], raw)
        corrected.append(revised)
        sheet_prompts = [e for e in prompts if e.get('kind') == 'answer' and _is_sheet(e)]
        asks = [e for e in window if e['t'] == 'offer' and isinstance(e.get('ask'), list) and e['ask']]
        if sheet_prompts:
            answers, skipped, rt, sheet_ms, sure, pull = {}, set(), {}, 0, {}, {}
            credits = [credit for credit in map(_credit, sheet_prompts) if credit is not None]
            for event in sheet_prompts:
                cleaned = _prompt(event)
                answers.update(cleaned['answers'])
                sure.update(cleaned['confidence'])
                pull.update(cleaned['strength'])
                skipped.update(cleaned['skipped'])
                if isinstance(event.get('rt_ms'), dict):
                    rt.update(event['rt_ms'])
                if isinstance(event.get('sheet_ms'), int) and event['sheet_ms'] > 0:
                    sheet_ms += event['sheet_ms']
            # The card can't know its button: reasons only count after a real Again, and the
            # word it was taken for only as one of them.
            if row[EASE] != 1:
                answers.pop('wrong', None)
                skipped.discard('wrong')
            if 'other' not in answers.get('wrong', ()):
                answers.pop('confused', None)
                skipped.discard('confused')
            skipped -= answers.keys()
            asked, last = (asks[-1] if asks else {}), sheet_prompts[-1]
            # panel_ms stays 0: the sheet took its time from the next card, not this one.
            output.append({**common, 't': 'prompt', 'kind': 'answer', 'ts': rev_id,
                           'ease': row[EASE], 'wording_v': last['wording_v'], 'answers': answers,
                           'confidence': {key: value for key, value in sure.items() if key in answers},
                           'strength': {key: value for key, value in pull.items() if key in answers},
                           'skipped': sorted(skipped), 'rt_ms': rt, 'panel_ms': 0,
                           'sheet_ms': sheet_ms,
                           'timer_credit_ms': sum(credits) if credits else None,
                           'revised': any(e.get('revised') is True for e in sheet_prompts),
                           'withdrawn': any(e.get('withdrawn') is True for e in sheet_prompts),
                           'answer_p': asked.get('p'),
                           'situation': asked.get('situation'), 'know_q': last.get('know_q'),
                           'via': last.get('via'), 'why_p': 0, 'rotation_p': 0.5})
        elif asks:
            # Planned and never shown: reviewing stopped before anything followed the card.
            unshown += 1
        answer_prompts = [e for e in prompts if e.get('kind') == 'answer' and not _is_sheet(e)]
        offers = [e for e in window if e['t'] == 'offer' and 'cost' in e]
        for offer in window:
            if (offer['t'] == 'offer' and offer.get('sharpness')
                    and not any(e.get('kind') == 'sharpness' for e in prompts)):
                output.append(_prompt(dict(offer, t='prompt', kind='sharpness',
                                           answers={}, skipped=['sharpness'], wording_v=4),
                                      late=False, p=1))
        if answer_prompts or offers:
            answers, skipped, rt = {}, set(), {}
            for event in answer_prompts:
                cleaned = _prompt(event)
                answers.update(cleaned['answers'])
                skipped.update(cleaned['skipped'])
                if isinstance(event.get('rt_ms'), dict):
                    rt.update(event['rt_ms'])
            if row[EASE] != 1 or row[LAST_IVL] < 1:
                answers.pop('why', None)
                skipped.discard('why')
            offer = next((e for e in offers if 'cost' in e), {})
            cost = offer.get('cost') or {}
            # A delayed offer only appears once the answer runs that long.
            if cost and raw >= cost.get('show_at_ms', 0):
                skipped.update(k for k in ('cost', cost.get('rotation')) if k and k not in answers)
            if offer.get('why', bool(offer.get('why_p'))) and row[EASE] == 1 and row[LAST_IVL] >= 1 and 'why' not in answers:
                skipped.add('why')
            skipped -= answers.keys()
            # An offer left alone on a card that wasn't failed asked nothing.
            if answers or skipped:
                probability = cost.get('p_long' if raw > 60_000 else 'p_base', 1 if cost else 0)
                output.append({**common, 't': 'prompt', 'kind': 'answer',
                               'ts': rev_id, 'ease': row[EASE], 'wording_v': 4, 'answers': answers,
                               'skipped': sorted(skipped), 'rt_ms': rt,
                               'panel_ms': panel, 'answer_p': probability,
                               'why_p': offer.get('why_p', 0), 'rotation_p': 0.5})
    # Dataset consumes question before flip before answer, even after panel correction.
    output.sort(key=lambda e: (e['ts'], {'q': 0, 'a': 1, 'ans': 2}.get(e['t'], 3)))
    return PhoneData(output, corrected, earliest,
                     {'files': phone.files, 'unresolved': len(unresolved),
                      'ambiguous': len(ambiguous), 'unshown': unshown,
                      'credit_gap_ms': round(median(credit_gaps)) if credit_gaps else None,
                      'credit_answers': len(credit_gaps)})
