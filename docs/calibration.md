# Effort calibration: operator guide

Everything here runs from Anki, with no coding assistant and no external library. It records every answer on this device, asks for a few ratings, fits a model of felt effort, and tests that model against your own day ratings. Normal users see none of it.

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

Without Stop Recording, the recorder and its prompts keep running until the profile closes, and recording resumes by itself if devmode ever comes back on. A local fit also stays active until the profile closes.

With devmode off there is no tab, no recorder and no hidden heatmap. Calibrated effort leaves the measures unless a validated model has shipped. If it was selected, scoring falls back to the default measure and the saved choice stays. The data folder is left alone.

## Runbook

1. Turn devmode on. Open settings, Calibration tab, Start Recording. Recording then restarts by itself at every profile open.
2. For 14 days, just review. Rate the day when asked, in the evening as you leave reviewing: day ratings start at once, with no baseline.
3. Answer and sharpness prompts then start by themselves, about a minute a day. See What each question means.
4. Copy the data folder monthly.
5. Press Fit at about 300 cost ratings to check that it works, and again at 800 or more.
6. Check the ship gate. If it passes, follow Shipping. If it does not, nothing ships, and that is a valid result. At 60 rated days the test is weak, though, so NOT PASSED there isn't final: keep rating days and press Fit again. See Ship gate.

Run the smoke test on a scratch profile before step 1.

Stop Recording does not pause the baseline: it counts days from the very first start. The error count in the tab should stay at 0. A failing handler never interrupts a review: it writes to `errors.log` instead, so a rising count means data may be missing.

## Prompts

Every rating is a slider. Set each one, then press Enter or Done: a key only moves its slider, so no dialog closes by itself. Esc skips whatever isn't set yet, keeps what is, and logs the skip. Nothing is dismissed by a timer.

| Prompt | When | Keys |
| --- | --- | --- |
| Effort of that review, `cost` | Sampled answers | `Z X C V B N`, for ¼, ½, One, Two, Four and Eight or more |
| One of two rotating questions, `gain` or `grasp` | With every cost question | `gain`: `Q W E R T Y U`. `grasp`: `Q W E R T`, for 20 to 100% |
| Why you pressed Again, `why` | Sampled Again on a card in review state | `A S D F G` |
| Your concentration, `sharpness` | Start of a study block, or after an answer if the gap passed inside the reviewer | `1` to `7` |
| Day rating, `tiring` and `learned`, optional note | Leaving the reviewer in the evening, once a day, if today has 20+ answers | `1` to `7`, `Q W E R T Y U`; click the note field to type |

Cost uses letters on purpose. With digits, pressing 2 for "2" would set ½, a fourfold error nothing can detect once confirmed, and 1 to 4 also answer cards, so a habit would move a slider unnoticed.

Answer prompts appear before the next card's timer starts, so they never add to any recorded time.

The day rating is only ever asked on the way back to the deck list after reviewing: press D, click Decks, or finish the queue and then go back. It rates today, once, from 19:00 by default, and needs 20+ answers today, so you're never asked about study you haven't done. Opening Anki, regaining focus and rollover never ask. It appears before the deck list draws, so the day's final score isn't on screen. Esc settles the day. If you study more afterwards, that's fine.

A day with no evening exit stays unrated, on purpose: a question asked later would blur into the next day. On your 2026 study pattern, a 19:00 start leaves up to 79% of study days rateable, with about 88% of the day's answers done at the first prompt. An earlier hour rates more days less completely: 18:00 gives 85% and 74%, 20:00 gives 61% and 99%.

## What each question means

Each question asks about one thing, so that "effort" doesn't have to carry everything. A slider shows its value beside it and a short description under it, so you can match a step to how it felt.

A key sets a slider to the tick above its letter. The letter or digit is only a shortcut, not part of the answer. The arrows then fine-tune that slider, and Enter confirms. Clicking and dragging work too. An untouched slider is no answer: it shows a dash and Not set yet, wherever its handle rests, and Done waits until all are set. To choose the step where a handle rests, click it.

### Effort of that review (`cost`)

> That review took as much effort as how many routine ones?
>
> ¼ of one, ⅓ of one, ½ of one, ¾ of one, One, One and a half, Two, Three, Four, Six, Eight or more

A routine review is a familiar card answered without much thought. Picture a few of those in a row: "Two" means doing two of them would take as much effort as the review you just did. "¼ of one" means it took a quarter of a routine one.

Count the whole review: recalling, reading, understanding, looking things up, and how long it lasted.

- ¼ of one: obvious at a glance, you barely read it.
- ½ of one: quick and sure, easier than routine.
- One: a familiar card, little thought, about ten seconds.
- Two: real thought, or a second look.
- Four: a minute or two of reading and thinking.
- Eight or more: several minutes working through a dense card.

The scale moves in half doublings, rounded to plain numbers, so every second step doubles. That's because the model works on ratios: One to Two counts the same as Four to Eight. The keys hold these doublings, and the arrows reach the steps between.

**Used for:** the only rating the model learns from.

### What you got from it (`gain`)

> What did you get out of that review?
>
> Nothing, Very little, Kept it fresh, Reinforced it, A small insight, Learned something, Learned a lot

This asks what the review gave you, whatever it took. Nothing means autopilot: your hands answered and you'd have lost nothing by skipping it. Kept it fresh means you recalled or reread what you already knew. Learned something means you now understand something you didn't before, such as a nuance. A hard review can teach nothing and an easy one can teach something, so answer this apart from effort.

**Used for:** nothing yet. It's recorded so that grinding can be measured later.

### How well you know the card (`grasp`)

> How much of this card do you really know now?
>
> 0% to 100%, in steps of 10%

Of everything on the card worth knowing, how much could you produce or explain right now without looking: the meaning, the pitch, the nuances. Answer for the card, not for this review. Pressing Good doesn't mean 100%.

**Used for:** nothing yet. It's recorded to test how far maturity reflects real knowledge.

### Why you failed (`why`)

> Why did you press Again?
>
> I blanked, A hint, Partly, Nearly, On purpose

Read this slider as how close you were to passing. I blanked means nothing came. A hint means something came, but not enough to pass. Partly means you got some of it and missed the rest. Nearly means you knew most of it and missed a detail. On purpose means you knew it well enough to pass, but want the card back soon, for instance to clear up a nuance. The review log can't tell these apart.

**Used for:** the report's Cost by Again reason. It can't feed the model, because public scoring never has this answer.

### Your concentration (`sharpness`)

> How well can you concentrate right now?
>
> Barely, Poorly, Below usual, As usual, Well, Very well, Fully

This is about you, not the cards, and As usual means your normal study state. It's asked before you start, so the session can't colour it.

**Used for:** a control during the fit, which separates "this card is hard" from "I was tired". It never scores.

### How tiring the day was (`tiring`)

> How mentally tiring was today's study?
>
> Not at all, Barely, A little, As usual, Fairly, Very, Exhausting

This is about today's study so far, compared with your usual day. Rate how drained it left you, not how many cards you did or how useful it was. A long, easy day can be A little.

**Used for:** judging every formula, and deciding the ship gate.

### What the day taught you (`learned`)

> How much did you learn or improve today?
>
> Almost nothing, Very little, A little, As usual, Quite a bit, A lot, Far more than usual

This is progress, not effort. A day of grinding vocabulary can be Very tiring and Almost nothing, and a short session on rich cards can be the reverse.

**Used for:** shown beside tiring in the judge, outside the gate. It's recorded so that the heatmap could one day credit what you gained instead of what it cost.

The optional note is for anything unusual, such as being ill or changing how you study. Nothing reads it automatically.

## Settings

All of these are in the tab, take effect at once and are saved in `state.json`.

| Setting | Key | Default | Meaning |
| --- | --- | --- | --- |
| Silent baseline (days) | `baseline_days` | 14 | No answer or sharpness prompts before this |
| Answer rate | `answer_rate` | 0.06 | Weighted `1 + 6·(over 60 s) + 2·new`, about 13 prompts a day |
| Again reason rate | `lapse_reason_rate` | 0.3 | About 3 prompts a day |
| Study block gap (hours) | `block_gap_hours` | 2 | Idle time that starts a new block |
| Day rating from (hour) | `day_rating_hour` | 19 | Local hour from which leaving the reviewer asks. A study day runs past midnight, so 1 a.m. still counts as that evening |
| Daily prompt cap | `daily_cap` | 30 | Skipped prompts count, day ratings do not |
| Stop after cost ratings | `stop_after_cost_ratings` | 1200 | Prompts stop, the silent log and day ratings continue |

If prompts get tiresome, set Again reason rate to 0 first, and raise the study block gap to make sharpness rarer. Lower the answer rate only as a last resort. Keep cost and the day rating. The rotating question has no switch of its own.

## Data folder

```text
<Anki base>/<profile>/better_review_heatmap_calibration/
```

On Linux the base is usually `~/.local/share/Anki2`. Open Data Folder in the tab goes straight there.

| File | Content |
| --- | --- |
| `events-YYYY-MM.jsonl` | All events, one JSON object per line, one file per UTC month |
| `state.json` | Settings and progress, replaced atomically |
| `errors.log` | One JSON object per failure: `ts`, `where`, `details` |
| `report-YYYYMMDD.json` | Full report of a fit. A second fit on the same day replaces it |
| `model.json` | The local fit in use, with its note-type offsets, written by Use This Fit on This Profile |
| `state.json.corrupt` | A damaged `state.json`, set aside if that ever happens |

Besides the settings, `state.json` holds `recording`, `recording_since` (ms, first start) and `day_rated_through_ms`, the end of the last study day rated or skipped.

**Copy the whole folder once a month.** Anki backups don't cover it and sync doesn't touch it. A reinstall or an add-on update leaves it in place, but losing the disk loses it, and these events can't be recovered from the revlog. A copy is safe while Anki runs: at worst the last line is truncated, and truncated lines are skipped on reading. To restore, close Anki and put the folder back.

The folder holds no card text, only sizes. The one exception is what you type in a day rating's note.

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

**`prompt`**: one per dialog, skipped or not.

| Field | Meaning |
| --- | --- |
| `kind` | `answer`, `sharpness` or `day` |
| `wording_v` | Version of the question wording, 4. Earlier versions offered every other position of `cost` and `why`, and `dataset.py` doubles those on reading. Their other answers stay as logged, on the shorter scales of the time |
| `answers` | Question id to value |
| `rt_ms` | Question id to reaction time in ms, up to the last change of that slider |
| `skipped` | Ids of the questions left unanswered |
| `cid`, `rev_id`, `seq`, `ease`, `answer_p`, `why_p` | `answer` only. The two probabilities the answer was sampled with |
| `rotation_p` | `answer` with a cost question only |
| `p`, `late` | `sharpness`. `p` is 1, `late` is true if asked after an answer |
| `p`, `day_end_ms`, `day_answers`, `note` | `day`. `note` only if you wrote one |

Values in `answers`:

| Question id | Values |
| --- | --- |
| `cost` | 0 to 10, in the order the slider shows, so 4 is One and 10 is Eight or more |
| `gain` | 0 to 6, in the order the slider shows |
| `grasp` | 0 to 10, in tens of percent |
| `why` | 0 to 4, in the order the slider shows |
| `sharpness`, `tiring`, `learned` | 1 to 7, in the order the slider shows |

## Fit and report

Fit is the machine learning step. It runs a regularized ordered logistic regression, in pure Python in `calibration/fit.py`, with no library. From your cost ratings it learns one weight for each input, then those weights give every answer a credit, rated or not. Only cost ratings train it: `gain`, `grasp` and `why` are not read by it, and `tiring` and `learned` only test it. DESIGN.md has the formulas. Nothing is learned until you press Fit, and nothing changes your heatmap until you press Use This Fit on This Profile.

It runs in the background and is enabled from 300 cost ratings. It fits twice:

- The **shared fit** has no note-type offsets. It is what the report's weights, the holdout and the gate describe, and what Copy Weights as JSON gives you, as it's the only one that can work on someone else's collection.
- The **profile fit** adds one offset for each template with 30+ ratings. It's what Use This Fit on This Profile uses, because your own heatmap can know your own note types.

Use This Fit on This Profile writes `model.json` and scores with it at once, and at each profile open while devmode is on. Calibrated effort then appears under Color by the next time settings open. Each fit gets a new version, so saved references recompute. To go back to the shipped weights, delete `model.json` and reopen the profile.

Copy Weights as JSON copies the model body for shipping. It says `"status": "local"`, so pasting it alone releases nothing. It never holds offsets.

The report appears in the tab, top to bottom:

**Counts.** Cost ratings here exclude undone answers, so this can read lower than the tab's live count. `unlogged` counts revlog answers since recording began with no `ans` event: phone reviews without recording files, or answers while recording was off. `undone` counts `ans` events whose revlog row is gone. Check Cost use: if nearly everything is on one or two labels, the scale isn't being used.

**Fit.** `sigma` is the noise in your ratings, in log units: smaller means steadier. `DID NOT CONVERGE` means the rest of the report can't be trusted, and the gate fails.

**Time exponent.** How credit grows with an answer's duration, on plain templates (`rich` 0) and rich ones (`rich` 1.5). Review-weighted uses 0.4. This number depends on how well you judge "double": anchors misjudged by ×0.7 or ×1.4 move it by 30 to 45%, while day ordering mostly survives. So trust the judge over this line.

**Weights.** Each line is `name: weight ± standard error`, per raw input unit. A weight within about two standard errors of 0 hasn't earned its place. Lines marked `(control)` explain the ratings and never score: sharpness and drift, both centred, so scoring at 0 means an average block at an average moment.

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

**Note-type offsets.** From the profile fit: how much each template costs beyond what its timing explains, in log units, against templates with too few ratings to have an offset. An offset of +0.69 means about double. Only this profile scores with them.

**Holdout.** Trained on earlier rating days, tested on the later 20%. Log loss must be below intercept-only, or the inputs add nothing. Spearman is the rank match between predicted credit and your ratings of single answers.

**Judge.** The result that matters. For the later 30% of rated days, using a fit trained only on ratings from before them, it gives the Spearman of tiring and of learned against count, recorded time, review-weighted, FSRS-based, fitted, and fitted with note-type offsets. Higher is better. `n/a` means no fit could be trained. If the version with offsets clearly beats fitted, your note types carry effort the inputs can't see: your own heatmap gets that gain, and public users can't. The last line is the gate's test, explained below.

**Front share.** Median front and back time per template, from logged answers.

**Cost by Again reason.** The median cost of each reason. It shows whether deliberate fails feel different from blanks.

**Pace.** Reactivity checks on comparable answers (same template, button and phase). ×1.00 means no change, above 1 means slower. The first line compares after the baseline with during it, the second compares answers right after a prompt with the others. A ratio far from 1 means prompts are changing how you review.

## Ship gate

The label above the report says PASSED or NOT PASSED with every reason. All of these must hold:

- At least 800 cost ratings, counted without undone answers.
- At least 60 rated days, each with a tiring rating.
- The fit on every rating and the fit used by the judge both converged.
- On the later 30% of rated days, fitted is no worse than review-weighted on tiring, and it explains tiring ratings that review-weighted leaves unexplained. That is the report's `Beyond review-weighted` line: `t` must reach the value printed there, which is one-sided p < 0.05 on the partial rank correlation.

Failing is a valid result, and nothing ships.

There is no fixed margin on purpose. Day scores agree so closely that even a perfect fit gains only about 0.02 to 0.05 in plain Spearman, so a margin of 0.05 would turn away real gains, more surely with more days. The partial test compares the measures on the part where they differ, so it gets stronger with data.

Simulated odds of passing, with ratings that correlate about 0.7 with true effort, for a true time exponent of 0.8 and for a score that is mostly recorded time:

| Rated days | Judged days | Exponent 0.8 | Mostly recorded time | No real gain |
| --- | --- | --- | --- | --- |
| 60 | 18 | 23% | 41% | 7% |
| 90 | 27 | 30% | 53% | 8% |
| 120 | 36 | 38% | 66% | 8% |
| 180 | 54 | 51% | 80% | 6% |

So 60 days is the minimum, not a fair trial. NOT PASSED at 60 days may only mean too little data, and about 1 fit in 14 passes with no real gain. Day ratings keep coming after cost ratings stop, so keep rating and press Fit again.

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

- A cold read of the measure takes about 680 ms on 117k answers, against FSRS-based 348 ms and sustained concentration 712 ms; cached, about 10 ms. Appending only new revlog rows would be faster but risks stale scores after card deletions or note type changes, so it was left out on purpose.
- `scripts/research/` matches the `research` rule in `.gitignore`, so those scripts stay local unless you re-include them.

## Smoke test

Do this on a scratch profile before the real one. Build first: `python scripts/build_worktree.py`.

```sh
ANKI_SINGLE_INSTANCE_KEY=rh-smoke anki -b <empty folder>
```

Without that key a second Anki only raises the running window and exits.

1. In the scratch Anki, install `build/better-review-heatmap-<version>.ankiaddon` through Tools > Add-ons > Install from file, restart it the same way, and add some cards.
2. Turn devmode on, Start Recording, then set Silent baseline to 0. Set Answer rate to 1 so that a prompt comes at once.
3. Review at least 21 cards, as the undo below must leave 20. In the data folder, confirm `q`, `a` and `ans` lines, each `ans` with a `rev_id`.
4. Check that a prompt appears before the next card. In the file, the next `q` comes after the `prompt`, and that card's `taken_raw_ms` matches its own `ans` `ts` minus `q` `ts`, without the time the prompt stayed open.
5. Undo an answer: an `undo` line appears.
6. Close Anki mid-session and reopen it: recording resumes under a new `run`.
7. Leave Anki open past rollover: a new `meta` line appears at the next card, and no prompt appears.
8. Set Day rating from to the current hour. With 20+ answers today, press D in the reviewer: the day rating appears before the deck list, and doing it again doesn't ask twice.
9. Confirm the Errors count in the tab is still 0, and that reviewing feels no slower.

Then turn devmode on in the real profile and follow the Runbook.

## Known limits

- The recorder sees this desktop only. Reviews from elsewhere join through recording files, see below. Without them they count as unlogged, though they still score through the revlog.
- Split timing and sharpness can't score historic days or public users. They explain the fit; only revlog-derived inputs score.
- The weights describe one person. Ordering transferred to an unseen note type in the research, level did not.
- Ratings measure felt cost, not truth.

## Recordings from other devices

The recorder only runs on desktop. Reviews done elsewhere can reach Fit as recording files, which a separate tool has to write: none ships with the add-on. Fit reads every file named `_brh_cal_*` in the media folder, so media sync carries them. Check Phone in the tab shows matched answers, front and back medians, and key failures, without waiting for 300 cost ratings. Their raw cost ratings count towards that threshold.

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
- The earliest recorded event starts the baseline if it predates desktop's.

Split timing and sharpness remain diagnostics, never scoring inputs. Files are never deleted or rewritten here.
