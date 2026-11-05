"""Sealed phone recordings joined to the synced revlog. Stdlib only.

The caller resolves raw note keys through the collection API. Nothing here
opens a collection or infers an answer button from template events.
"""

import json
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

from .dataset import CID, ID, EASE, DURATION, LAST_IVL


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
                                or event.get('kind') not in ('answer', 'sharpness', 'day')
                                or (event['kind'] != 'day' and 'card' not in event)):
                            continue
                        if event['t'] == 'offer' and event.get('cost') is not None and not isinstance(event['cost'], dict):
                            continue
                        events.setdefault(event['id'], event)
                    except (ValueError, TypeError):
                        continue
        except OSError:
            continue
    return PhoneFiles(sorted(events.values(), key=lambda event: event['ts']), files)


def _prompt(event, **fields):
    answers = event.get('answers', {})
    scales = {'cost': 10, 'gain': 6, 'grasp': 10, 'why': 4,
              'sharpness': 7, 'tiring': 7, 'learned': 7}
    answers = {key: value for key, value in answers.items()
               if key in scales and isinstance(value, int) and not isinstance(value, bool)
               and (1 if key in ('sharpness', 'tiring', 'learned') else 0) <= value <= scales[key]} if isinstance(answers, dict) else {}
    return {**event, 'run': 'phone', 'answers': answers,
            'skipped': [key for key in event.get('skipped', []) if isinstance(key, str) and key in scales]
            if isinstance(event.get('skipped', []), list) else [], **fields}


def merge_phone(phone: PhoneFiles, rows, index) -> PhoneData:
    """Index is {card: {raw key: iterable of card IDs}}; collisions stay visible.

    Only intervals ending in a real answer can produce answer events. A lone
    question, a future survey, or a row removed by undo cannot create an answer.
    """
    if not phone.events:
        return PhoneData([], rows, None, {'files': phone.files, 'unresolved': 0, 'ambiguous': 0})
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
    for event in phone.events:
        if 'card' not in event:
            if event['t'] == 'prompt' and event.get('kind') == 'day':
                output.append(_prompt(event, day_end_ms=event['ts'] + 1, p=1))
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
        panel = min(panel, rev_id - q['ts'])
        raw = rev_id - q['ts'] - panel
        seq += 1
        common = {'v': 1, 'run': 'phone', 'seq': seq, 'cid': cid, 'rev_id': rev_id}
        output.append({**q, **common})
        if a:
            # Front sheets consume time too; subtract only panels completed before the flip.
            front_panel = sum(e.get('panel_ms', 0) for e in prompts
                              if e['ts'] <= a['ts'] and isinstance(e.get('panel_ms'), int)
                              and e['panel_ms'] > 0)
            output.append({**a, **common, 'ts': max(q['ts'], a['ts'] - front_panel)})
        output.append({**common, 't': 'ans', 'ts': rev_id - panel,
                       'ease': row[EASE], 'taken_capped_ms': row[DURATION],
                       'taken_raw_ms': raw})
        if panel:
            revised[DURATION] = min(row[DURATION], raw)
        corrected.append(revised)
        answer_prompts = [e for e in prompts if e.get('kind') == 'answer']
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
                     {'files': phone.files, 'unresolved': len(unresolved), 'ambiguous': len(ambiguous)})
