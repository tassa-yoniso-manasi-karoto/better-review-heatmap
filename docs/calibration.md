# Effort calibration: operator guide

Everything here runs from Anki, with no coding assistant and no external library. Ratings come from recordings made where you review, which reach this device through media sync. Fit learns from them how hard answers are and how answers add up to a day's work, and tests the result against your own evening ratings. Normal users see none of it.

The reasoning and research findings are in `DESIGN.md`, under Calibration.

## Devmode

Open the debug console with `Ctrl+Shift+;`, run code with `Ctrl+Enter`.

Turn on:

```python
mw.pm.profile["heatmap_devmode"] = True
mw.pm.save()
```

The Calibration tab appears the next time you open Tools > Better Review Heatmap Options, with no restart. The flag is per profile and per device, and stays local. It is a top-level key on purpose: anything written inside `pm.profile["heatmap"]` is erased at the next settings save.

Turn off: press Stop Recording first, then run

```python
mw.pm.profile["heatmap_devmode"] = False
mw.pm.save()
```

Without Stop Recording, the recorder keeps running until the profile closes, and recording resumes by itself if devmode ever comes back on. A local fit also stays active until the profile closes.

With devmode off there is no tab, no recorder and no hidden heatmap. Calibrated effort leaves the measures unless a validated model has shipped. If it was selected, scoring falls back to the default measure and the saved choice stays. The data folder is left alone.

## Runbook

1. Turn devmode on. Ratings come from recording files made where you review: see Recordings from other devices. To time the answers given here as well, open settings, Calibration tab, Start Recording. Recording then restarts by itself at every profile open.
2. Review as usual, and answer the questions as they come: about cards once they are answered, about sittings, and about the day in the evening. Rate every evening you can: those ratings are the judge, and the scarcest.
3. Press Check Phone now and then. It shows how many recorded answers matched the review log, and whether any key failed to resolve.
4. Copy the data folder monthly.
5. Fit unlocks at 150 effort ratings and 20 rated days. Press it to see how answers add up for you. Use This Fit on This Profile colours your own calendar with the result.
6. From 300 effort ratings and 60 rated days, the report tries the ship gate. If it passes, follow Shipping. If it does not, nothing ships, and that is a valid result. Before about 90 rated days the test is weak, so NOT PASSED there isn't final: keep rating days and press Fit again. See Ship gate.

Run the smoke test on a scratch profile before step 1.

The error count in the tab should stay at 0. A failing handler never interrupts a review: it writes to `errors.log` instead, so a rising count means data may be missing.

## Questions

The recording tool asks; this add-on only reads the answers. Every answer is one tap on a row of choices, none preselected, and every question can be skipped.

| Asked | Questions |
| --- | --- |
| About a sampled card once it is answered, on a sheet over whatever follows | `effort`, then `familiar` on a new card, or `gained` or `know` on another. `wrong` after a failure |
| About a sitting of 15+ cards, once it has ended, or any sitting the rater ends | `work`, `focus`, `deep`; from wording 6, `work` and a drawn `curve` |
| In the evening, once a day | `amount`, `strain`, `rest`, `versus` and `pair` when there is something to name, `learned`, a note |

A card is asked about after its answer, never on it: a control on the card went unseen on quick answers, and a question during the card would land on its clock.

## What each question means

**`effort`, 1 to 6.** How hard did you have to think on this card? Front and back together: None, Slight, Some, Solid, Hard, Maximal. Leave out how long it took and which button you pressed: both are recorded. Each step describes what happened in your head, so it needs no comparison with another review. *Used for:* stage 1 of the fit, which answers are harder.

**`familiar`, 0 to 3.** Before this new card, did you know this word or idea? *Used for:* a control in stage 1. It never scores.

**`gained`, 0 to 4, or `know`, 0 to 100,** taking turns. What the review left behind, from Nothing to Something new; and how much of the card you know, asked about its main point on a young card and about its details on a mature one, on one scale. *Used for:* nothing scores them. The report tests whether the inputs predict them.

**`wrong`, any number of reasons.** After a failure: blank, another word or meaning, a wrong form, a missed sense, misheard or misread, on purpose, something else. *Used for:* the report's effort by reason. It can't feed the model: public scoring never has it.

**`work`, 1 to 5.** How much Anki work was that sitting, all in all? Tiny to Huge. *Used for:* stage 2, how answers add up.

**`focus`, 1 to 5.** How well could you concentrate during it? *Used for:* a control in stage 1. It never scores.

**`deep`, 1 to 5.** How much of the sitting were you deep in it, with no break in your attention? Count stretches of several minutes on demanding cards. *Used for:* a check of sustained concentration, in the report.

**`curve`, from wording 6, in place of `focus` and `deep`.** Concentration has a depth and a length, and one answer folded both. The rater draws the sitting instead: time from its first card to its last across, depth up from Scattered (1) to Absorbed (5), with half steps, and lines where the kind of card changed. *Used for:* the control `focus` in stage 1, read off the curve at each answer, so that it rises and falls within a sitting. The report's check of sustained concentration still reads `deep`, from the sittings rated before the curve.

**`amount`, 1 to 7.** How much Anki work did you do today, all in all? From Almost none to About the most I ever do. Only Anki: a short day is a small day, even when it was all you could manage. *Used for:* stage 2, and the judge of every formula.

**`strain` and `rest`, 1 to 5.** How hard it was to get the day's study done, and how much the rest of the day took out of you. A tired or busy day shows here, not in `amount`. *Used for:* the report checks that they stay out of `amount`.

**`versus`, 1 to 5.** Next to a named earlier study day, today's work was much less, about the same, or much more. It needs no steady sense of a usual day. *Used for:* a second judge.

**`pair`, 1 to 5.** Which was more work today, of the two note types with the most time, each with ten minutes or more? It compares two piles of work done by the same person on the same day, so tiredness and chores cancel out. *Used for:* stage 2. It says most directly how a long answer weighs against a quick one.

**`learned`, 1 to 7.** How much did you learn or improve today? *Used for:* shown beside `amount` in the judge, outside the gate.

The optional note is for anything unusual, such as being ill or changing how you study. Nothing reads it automatically.

## Data folder

```text
<Anki base>/<profile>/better_review_heatmap_calibration/
```

On Linux the base is usually `~/.local/share/Anki2`. Open Data Folder in the tab goes straight there.

| File | Content |
| --- | --- |
| `events-YYYY-MM.jsonl` | All events, one JSON object per line, one file per UTC month |
| `state.json` | Whether recording is on, and since when, replaced atomically |
| `errors.log` | One JSON object per failure: `ts`, `where`, `details` |
| `report-YYYYMMDD.json` | Full report of a fit. A second fit on the same day replaces it |
| `model.json` | The local fit in use, with its note-type offsets, written by Use This Fit on This Profile |
| `state.json.corrupt` | A damaged `state.json`, set aside if that ever happens |

`state.json` holds `recording` and `recording_since` (ms, the first start). One written before the rating dialogs were retired holds their settings too: they are kept and unused.

**Copy the whole folder once a month.** Anki backups don't cover it and sync doesn't touch it. A reinstall or an add-on update leaves it in place, but losing the disk loses it, and these events can't be recovered from the revlog. A copy is safe while Anki runs: at worst the last line is truncated, and truncated lines are skipped on reading. To restore, close Anki and put the folder back.

The folder holds no card text, only sizes.

## Event schema

Version 1. Every line has `v` (1), `t` (the kind), `ts` (ms) and `run` (a random id for each recorder start). Events are raw on purpose, never computed features, so any later formula can re-derive what it needs.

`seq` numbers the presentations of cards inside one run. A redraw of the same card repeats `q` with the same `seq`, and `dataset.py` keeps the first. `seq` is null on `a` and `ans` if recording started while that card was already on screen.

**`meta`**: at each recorder start and at each rollover, including while Anki stays open.

`addon_version`, `anki_version`, `platform`, `utc_offset_min`, `rollover`, `day_cutoff`

**`q`**: question shown.

| Field | Meaning |
| --- | --- |
| `seq` | See above |
| `cid`, `nid`, `did`, `odid`, `mid`, `ord` | Card, note, deck, original deck, note type, template |
| `ctype`, `queue`, `ivl`, `reps`, `lapses`, `left` | Card fields before the answer |
| `memory` | `{"s", "d"}` FSRS stability and difficulty, or null |
| `desired_retention`, `decay`, `last_review_time` | From the card, or null. `last_review_time` is in seconds |
| `max_taken_ms` | The preset's answer time cap |
| `preset` | Deck preset id |
| `q_chars`, `a_chars` | Visible characters per side, without script, style or tags. The answer side includes the front through `{{FrontSide}}` |
| `q_media`, `a_media` | Images, video, audio and sound placeholders per side |
| `note_mod` | Note modification time in seconds |

**`a`**: answer shown. `seq`

**`ans`**: answer given.

| Field | Meaning |
| --- | --- |
| `seq`, `cid`, `ease` | As above, `ease` is the button, 1 to 4 |
| `rev_id` | `max(id)` in the revlog for this card: the exact join key |
| `taken_capped_ms`, `taken_raw_ms` | Time taken with and without the preset's cap. Null if the moment of the answer was missed |
| `ctype_after`, `ivl_after`, `memory_after`, `note_mod_after` | The card after the answer |

**`audio`**: `kind` is `begin` with `tag` (the tag's type name, never the filename), `end`, or `replay` with `cid` and `front`.

**`focus`**: `state`, the Qt application state name.

**`undo`**: `op`, `reverted_to`. Nothing reads these to pair undos: an undone answer has no revlog row any more, so `dataset.py` drops it by `rev_id`.

**`prompt`**: one per rating dialog, in folders from before the dialogs were retired. They are read for the counts and never fitted. Fields: `kind` (`answer`, `sharpness` or `day`), `wording_v`, `answers`, `rt_ms` and `skipped`; an answer adds `cid`, `rev_id`, `seq`, `ease` and the chances it was sampled with, a day `day_end_ms`, `day_answers` and its `note`. Their answers: `cost` 0 to 10 on ¼ to 8+ routine reviews, `gain` 0 to 6, `grasp` 0 to 10 in tens of percent, `why` 0 to 4, and `sharpness`, `tiring` and `learned` 1 to 7. Wordings before 4 offered every other position of `cost` and `why`.

## Fit and report

Fit is the machine learning step, in pure Python with no library. It runs in the background, and is enabled from 150 effort ratings and 20 rated days. Nothing is learned until you press Fit, and nothing changes your heatmap until you press Use This Fit on This Profile. DESIGN.md has the formulas. There are two stages:

1. **Cards.** An ordered logistic regression of the effort ratings on the inputs below. Its cut points are fitted too, so it learns which answers are harder, not by how much: every answer gets a difficulty. The two time inputs inform it and stay out of the difficulty. Three controls explain the ratings and never score: the sitting's `focus`, a new card's `familiar`, and drift over the months.
2. **Work.** An answer's credit is its duration to the power b, times exp(k × difficulty). b and k are found so that summed credit explains how sittings, days and pairs were rated. b is the whole time exponent: 0 counts answers, 0.4 is review-weighted, 1 is recorded time.

If the ratings can't place b, its 90% range holds both 0.2 and 0.8. b then stays at 0.4, review-weighted's exponent, and only k is fitted. The report says so.

There are two fits:

- The **shared fit** has no note-type offsets. It is what the report's weights, the judge and the gate describe, and what Copy Weights as JSON gives you, as it's the only one that can work on someone else's collection.
- The **profile fit** adds, in stage 1, one offset for each template with 30+ effort ratings. It's what Use This Fit on This Profile uses, because your own heatmap can know your own note types.

Use This Fit on This Profile writes `model.json` and scores with it at once, and at each profile open while devmode is on. Calibrated effort then appears under Color by the next time settings open. Each fit gets a new version, so saved references recompute. To go back to the shipped weights, delete `model.json` and reopen the profile.

Copy Weights as JSON copies the model body for shipping. It says `"status": "local"`, so pasting it alone releases nothing. It never holds offsets. Both buttons wait for a report with a fit in it.

The report appears in the tab, top to bottom:

**Counts.** Effort ratings, rated days, rated sittings, days with a pair, and the skip rate. `unlogged` counts revlog answers since recording began with no recorded answer: reviews of templates nothing records, or while recording was off. `undone` counts recorded answers whose revlog row is gone. Then how often each choice of each question was taken: if nearly everything sits on one or two choices, the scale isn't being used.

**Cards.** Each line is `name: weight ± standard error`, per raw input unit, on the effort ratings' own scale. A weight within about two standard errors of 0 hasn't earned its place. `(time, scored through b)` marks the time inputs, `(control)` the controls.

| Input | Meaning |
| --- | --- |
| `log_d` | ln(seconds / 10) |
| `rich` | ln(time-weighted median / median) of the template, 0 to 3 |
| `t_med` | ln(template median seconds / 10) |
| `log_d·rich`, `reps·rich` | Interactions |
| `new`, `step` | First answer, learning step |
| `again`, `hard`, `easy` | The button pressed |
| `overdue` | ln(elapsed / last interval), 0 unless interday |
| `reps` | ln(1 + interday answers so far on the card) |
| `recall` | Recall gap a day or more after the card's previous answer: logit(desired retention) − logit(R), FSRS-6 with the preset's parameters, −4 to 4 |
| `same_day_recall` | The same on the same study day, from FSRS-7 with its default parameters |
| `day_load` | ln(1 + answers given earlier that study day) |

**Note-type offsets.** From the profile fit: how much harder each template's answers felt than their inputs show. Only their differences mean anything, since the cut points take any common level. Only this profile scores with them.

**Work.** b and its 90% range, and whether the rule kept 0.4. Then k, as what an answer one standard deviation harder counts for, and what the fit loses without difficulty, in deviance: 3.8 is p 0.05. Last, the share of answers at a limit of credit.

**Judge.** The result that matters, explained under Ship gate. For each measure, its rank correlation with `amount` and with `learned` on the judged days, then what fitted adds to review-weighted, block by block, and the same on `versus`.

**Questions.** What each question shows so far, so that one showing nothing can stop being asked: how well every input, or the button alone, predicts `effort`, `gained` and `know` on later days; the weights of `familiar` and `focus`; effort by `wrong` reason; whether `rest` goes with `strain` and stays out of `amount`; how often `versus` agrees with its two days' `amount`; b from evenings alone, with pairs, and with sittings too; and `deep` against the share of a sitting that sustained concentration rewards.

**Front share.** Median front and back time per template, from recorded answers.

**Pace.** Reactivity checks on comparable answers (same template, button and phase): answers since the first question against the 30 days before it, on the revlog's own times, and the answer right after a question against the others. ×1.00 means no change, above 1 means slower. A ratio far from 1 means questions change how you review.

## Ship gate

The label above the report says PASSED or NOT PASSED with every reason. All of these must hold:

- At least 300 effort ratings and 60 rated days.
- Every fit converged.
- On days no fit of it had seen, fitted explains `amount` beyond review-weighted: the judge's z reaches 1.645, one-sided p < 0.05. And it is no worse than review-weighted in plain rank correlation with `amount`.

The judge cuts the rated days into four blocks in time order. Every block but the first is scored by a fit on everything rated before it, so no day is scored by a fit that saw it, and three quarters of the days are judged. In each block, an ordered logistic regression of `amount` on log review-weighted and log(fitted / review-weighted) gives the t of the second, and the blocks' t add up to z.

There is no fixed margin: day scores agree so closely that even a perfect fit gains only about 0.02 to 0.05 in plain rank correlation. Nor is the test a rank correlation with review-weighted removed from both sides, as it was before. Ranking bends the tails of the ratings and of the candidate alike, and a straight line on ranks leaves that bend behind, so review-weighted plus pure noise passed in 8 to 14% of simulated cases. The ordered logit works on the scale the fit itself assumes, and passes such candidates about 5% of the time.

Simulated odds of passing, on one person's real days, with a fitted measure that really is better (a time exponent of 0.7 and a difficulty effect), and with none:

| Rated days (study days) | Mixing a quick and a slow note type | One quick note type only | No real gain |
| --- | --- | --- | --- |
| about 68 (90) | 73% | 25% | 5% |
| about 90 (120) | 86% | 32% | 5% |
| about 135 (180) | 95% | 53% | 4% |

So 60 rated days is a minimum, not a fair trial: from about 90, a real gain passes most of the time. On days of one quick note type every measure ranks days almost alike, so NOT PASSED there says little. Day ratings keep coming: keep rating, and press Fit again.

## Shipping

Do this only after PASSED.

1. Press Copy Weights as JSON and replace the whole of `src/review_heatmap/effort_model.json` with it.
2. Set `status` to `validated`.
3. Check `version`. The copied value is already above every earlier fit and the shipped file, so keep it unless it fails to beat the value you just replaced.
4. Eight tests pin the unshipped `{"status": "none"}` file and fail after this edit: one each in `tests/test_activity.py`, `tests/test_metrics.py`, `tests/test_metrics_cli.py` and `tests/test_options.py`, and four in `tests/test_calibration_report.py`. Change them to patch in the model they need. The rest of the suite passes with a validated model.
5. Add a user-facing line under Added in `CHANGELOG.md`, following the rules at the top of that file.
6. Run `python -m pytest -q tests` and `npm test`, then `python scripts/build_worktree.py`.

No other code changes are needed. The measure then appears for every user as 🧪 Calibrated effort (exp.), while the default measure and `FSRS_CALIBRATION_DEFAULTS` stay as they are. Changing the default is a separate decision.

To score a model outside Anki, with `answers.json` as described in `AGENTS.md`:

```sh
python scripts/metrics_cli.py --answers-json answers.json --metric effort_model \
  --effort-model model.json --json
```

Note-type offsets never ship: public users have no ratings to fit them, and other collections have other note types. Only Use This Fit on This Profile scores with them.

Known items:

- A cold read of the measure takes about 680 ms on 117k answers, against sustained concentration 712 ms; cached, about 10 ms. Appending only new revlog rows would be faster but risks stale scores after card deletions or note type changes, so it was left out on purpose.
- The chance of recall behind `recall` and `same_day_recall` is replayed once per session, about 0.3 s on 133k answers, then extended answer by answer; DESIGN.md, Chance of recall, has the rules. Without FSRS both inputs are 0.
- `scripts/research/` matches the `research` rule in `.gitignore`, so those scripts stay local unless you re-include them.

## Smoke test

Do this on a scratch profile before the real one. Build first: `python scripts/build_worktree.py`.

```sh
ANKI_SINGLE_INSTANCE_KEY=rh-smoke anki -b <empty folder>
```

Without that key a second Anki only raises the running window and exits.

1. In the scratch Anki, install `build/better-review-heatmap-<version>.ankiaddon` through Tools > Add-ons > Install from file, restart it the same way, and add some cards.
2. Turn devmode on and Start Recording.
3. Review a few cards. In the data folder, confirm `q`, `a` and `ans` lines, each `ans` with a `rev_id`, and that a card's `taken_raw_ms` matches its own `ans` `ts` minus `q` `ts`.
4. Undo an answer: an `undo` line appears.
5. Close Anki mid-session and reopen it: recording resumes under a new `run`.
6. Leave Anki open past rollover: a new `meta` line appears at the next card.
7. Confirm the Errors count in the tab is still 0, and that reviewing feels no slower.

Then turn devmode on in the real profile and follow the Runbook.

## Known limits

- The recorder sees this desktop only, and asks nothing. Reviews from elsewhere join through recording files, see below. Without them they count as unlogged, though they still score through the revlog.
- Split timing and the ratings themselves can't score historic days or public users. They explain the fit; only revlog-derived inputs score.
- The weights describe one person. Ordering transferred to an unseen note type in the research, level did not.
- Ratings measure felt effort and felt work, not truth.

## Recordings from other devices

The recorder only runs on desktop. Reviews done elsewhere can reach Fit as recording files, which a separate tool has to write: none ships with the add-on. Fit reads every file named `_brh_cal_*` in the media folder, so media sync carries them. Check Phone in the tab shows matched answers, front and back medians, how many cards were asked about and how, and key failures, without waiting for a fit. Their effort ratings and rated days count towards Fit's thresholds before they are matched.

Write each file once and never touch it again. Media sync doesn't notice a file growing in place, and re-uploads a whole file on any change.

Each file is JSON lines, beginning with a `meta` event. Every event has `v` (1), a unique `id` for deduplication, `t` and `ts` (ms). Card events also carry `card`, as `Note type/Template`, and `key`, which is the note id or the raw text of the note's first field. A key that doesn't name exactly one card is dropped and counted.

| Event | Fields |
| --- | --- |
| `q`, `a` | Question or answer shown |
| `size` | `side`, `chars`, `media` |
| `focus` | `state`, `hidden` or `visible` |
| `audio` | `kind` `replay`, `front` |
| `state` | `phase`, `before` or `after`, with card fields and FSRS memory |
| `offer` | `cost` with `rotation`, `show_at_ms`, `p_base`, `p_long`, then `why_p` and `why`. Or `sharpness` |
| `due` | A delayed cost offer reached the card |
| `prompt` | `kind` `answer`, `sharpness` or `day`, with `wording_v`, `answers`, `rt_ms`, `skipped`, `panel_ms`. `day` adds `day_answers` and `note` |

Files never hold the answer button. Fit matches each interval ending in a real revlog answer to the card's events, within one hour: a redraw keeps the first question and flip, and another card or a reopened app starts a new presentation. Events waiting for a revlog sync are picked up by a later Fit.

- An offer with no matching answer counts as skipped, once it could have been seen: a delayed offer needs the answer to have run that long.
- `why` is kept only for a real Again on a review card, since the survey comes before the button.
- `panel_ms` is removed from that answer's duration for fitting only. Heatmap scores keep it.

Wordings before 5 asked on the card itself, with the `offer`, `due` and `prompt` events above: their answers are counted and never fitted. Split timing remains a diagnostic, never a scoring input. Files are never deleted or rewritten here.

### Wording 5

From `wording_v` 5 on, files are read as follows. These are the answers Fit reads.

A card is asked about once it is answered, on a sheet shown over whatever follows it. That keeps the question off the card being rated, and off its clock.

| Event | Fields |
| --- | --- |
| `offer` | `ask`, the question ids planned for this answer, and `p`, the chance it had of being asked about |
| `prompt`, `kind` `answer` | `wording_v` 5, `answers`, `rt_ms`, `skipped`, `after` true, `at` (ms, when the sheet closed) and `sheet_ms`. Its `ts` lies inside its own card's review, after the answer side was shown. `timer_credit_ms` when the recording tool already took the sheet's time off the clock of the card under it |
| `prompt`, `kind` `session` | A sitting: `answers`, `from_ms`, `to_ms`. No `card` |
| `prompt`, `kind` `day` | As before, with the answers below, and `versus_day` and `pair_cards` naming what `versus` and `pair` compare |
| `revision` | A later change to a card's answers: `of`, the prompt's id, then `answers`, `skipped` and `withdrawn`. No `card` |

| Answer | Values |
| --- | --- |
| `effort` | 1 to 6: how hard the card made you think, with no reference review and no time in it |
| `familiar` | 0 to 3: what was known of a new card before it |
| `know` | 0 to 100: how much of the card is known |
| `gained` | 0 to 4: what the review left behind |
| `wrong` | A list from `blank`, `other`, `form`, `sense`, `misread`, `purpose`, `else` |
| `work`, `focus` | 1 to 5, about a sitting: how much work it was, and how well you could concentrate |
| `deep` | 1 to 5: how much of the sitting was spent in unbroken concentration on demanding cards |
| `curve` | Session prompts from wording 6: `[[ts_ms, depth], ...]` inside `from_ms`..`to_ms`, depth 1 to 5 on whole or half steps, in time order |
| `amount` | 1 to 7, about the day: how much study was done |
| `strain`, `rest`, `versus`, `pair` | 1 to 5, about the day: how hard it felt, what the rest of the day took, and two comparisons |

`deep` was added within wording 5: sittings rated before it remain valid without it.

- An `offer` with no `prompt` for its answer was never shown. It is counted as such, never as a skip.
- A prompt with revisions is read with the answers and skips of its newest one, and the revisions themselves are dropped. The file keeps the original. A withdrawn revision answers nothing: the sheet still counts as shown, its questions as skipped. Check Phone counts changed and withdrawn answers. A revision whose prompt isn't read yet waits for it.
- `wrong` is kept only after a real Again.
- `sheet_ms` is removed from the duration of the card that was on screen while the sheet was up, for fitting only.
- `timer_credit_ms` says how much of that time the recording tool already gave back to that card's clock, so the revlog never held it. The revlog time then matches the time without the sheet, and nothing is removed twice. Check Phone shows how far recorded times sit from it under such sheets: near 0 when the credit works, about a sheet's length when it doesn't.
- A rated day holds the answers of its study day given before the rating: what came later that day wasn't rated. The last answer before the rating names the day.
- A sitting holds every answer from its first card's to its last's, found by when its cards were shown, with five minutes before `from_ms` allowed for: its bounds say when the tool received them. A day or a sitting rated twice keeps the later rating.
- `pair_cards` names the two templates `pair` compares, as `Note type/Template`, and the rating grows with the second. A day on which either wasn't answered has no pair.

### Wording 6

From `wording_v` 6, a card prompt can say more about each answer, and so can the evening and sitting prompts. Earlier prompts read as before.

- **Half steps.** An answer may lie halfway between two neighbouring levels: `effort` 2.5 lies between Slight and Some, and `know` 27.5 between 20 and 35. Only values on a half step are read, and only from wording 6. The fit treats a half step as a level of its own: its cut points are fitted, so the levels that occur are all it needs.
- **`confidence`.** From question id to 0 to 100: how sure the rater was of that answer. It is kept only for answers given; untouched means no rating. A revision carrying `confidence` replaces the prompt's, as its answers do. Check Phone and the report count it per question. No fit reads it yet. Wording 7 drops the slider, since the half steps give the same granularity: its prompts carry no `confidence`, and wording 6's are kept as recorded.
- **`fresh`**, 1 to 5: how fresh and solid the card felt in memory once its answer was seen. It is asked on cards seen a day or more before. The report tests it like `gained` and `know`.
- **Evening and sitting ratings** take half steps too, on every question: `amount` 4.5 lies between Usual and More than usual. The fits of `amount`, `work` and `pair` treat a half step as a level of its own, as for `effort`.
- **`curve`** replaces `focus` and `deep` on sitting prompts (see above). Only points inside the sitting, on a half step, are read, and at most 200 of them. A sitting with a curve and no answer is still a rated sitting for the control, never for `work`. Between two points the depth runs straight; before the first and after the last it stays at that point's.
- **`richness`**, 1 to 5: the rater's forecast of how much work the whole note will take before it is known well, asked once per note. The report tests whether it explains effort beyond the inputs, counting every rated card of a rated note. It is never a scoring input: no other collection has it.
