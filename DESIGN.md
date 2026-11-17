Let $R$ be daily answer count, including repeated attempts, $d_i$ each answer's
Anki-recorded minutes, and $T=\sum_i d_i$. Apply the power-product formula to
each answer, then sum:

$$
W_{a,b}=\sum_{i=1}^{R}1^a d_i^b=\sum_{i=1}^{R}d_i^b,\qquad a+b=1.
$$

> [!TIP]
> In code this is `sum(d ** b for d in minutes_per_answer)`. The `1^a` term is
> literally 1, so the review weight never changes a single term; it matters only
> through `a + b = 1`, which ties the two weights together: more weight on time
> means less on count. Raising minutes to a power below 1 compresses the spread
> between fast and slow answers: a 4-minute answer is worth `4 ** 0.4 ≈ 1.74`
> times a 1-minute one, not 4 times, and a 15-second answer `0.57`, not `0.25`.
> At `b = 0` every answer is worth 1 and the score is the review count; at
> `b = 1` it is total minutes. Summing per answer, rather than combining daily
> totals, is what keeps two days with the same answers at the same score.

One answer of $d_i$ minutes is worth, at the two edges of the family and at
the review-weighted exponent,

$$
\text{credit}_i=
\begin{cases}
1 & \text{if } b=0\\
d_i^{\,0.4} & \text{if } b=0.4\\
d_i & \text{if } b=1
\end{cases}
$$

At $b=0.4$ a 15-second answer is worth 0.57, a 1-minute answer 1 and a
4-minute answer 1.74.

Each answer contributes a count of 1; review volume enters through the number
of terms. This equals $R^aT^b$ only when answer durations are equal (or a weight
is 0).

| Mode | Formula | Decision |
| --- | --- | --- |
| Review count (classic) | $R$ | Retain existing classic behavior. |
| Workload (linear) | $\sum_i\sqrt{d_i}$ | Equal ½/½ review/time weights. |
| Workload (review-weighted) | $\sum_i d_i^{2/5}$ | ⅗/⅖ review/time weights; more time influence than the previous ⅓. |
| Workload (custom) | $\sum_i d_i^b$ | Users set both exponents; require $a,b\geq0$ and $a+b=1$. |
| Recorded time | $T$ | Dedicated time-only mode. |
| ⭐ FSRS-based (exp.) | $\sum_i e_i^a d_i^b$ | Effort $e_i$ from card state replaces the count credit of 1; every constant is a calibration setting. Default. |
| 🧪 FSRS-based + sustained concentration (exp.) | $\sum_i c_i e_i^a d_i^b$ | Session coefficient $c_i$ scales the whole credit; see below. |
| 🧪 Calibrated effort (exp.) | $\sum_i e^{\eta_i}$ | Credit fitted to one person's ratings; hidden until it passes the ship gate; see below. |

“Linear” means equal input influence in the UI, not mathematical linearity.
Exact score doubling is not a design requirement but keeping scores as factors of 1 in log space is.

Durations use minutes; additional pace-anchor calibration remains undecided.

## Scope

- Preserve review history, settings, selected reference dates, and history filters.
- Preserve the intentional below/above-target color jump.
- Defer individual-deck changes and special zero-time handling.
- Experimental modes are shown to everyone and suffixed `(exp.)`; the default,
  FSRS-based, carries ⭐ and the other 🧪. Their constants are calibration settings
  with hover hints; only values a user changed are stored (`fsrs_calibration`,
  `concentration_calibration`), so updated defaults reach everyone else.
  Calibrated effort is the exception: nobody sees it until a model passes its gate.
- Reuse the shared calculations in the existing CLI.

## FSRS-based effort

Each answer contributes $e_i^a d_i^b$ with the review-weighted exponents
$a=0.6$, $b=0.4$. The effort

$$
e_i=\mathrm{clamp}\big(s_i\,e^{\rho g_i+\sigma h_i},\ 1/L,\ L\big),
\qquad g,\ h=\mathrm{logit}(\text{desired retention})-\mathrm{logit}(R)
$$

> [!TIP]
> The count credit of 1 is replaced by `e_i`, the product of two factors,
> clamped to `[1/L, L]`. The first factor is a lookup on the card's state: a
> fixed weight for a card's first answer, a fixed weight for same-day steps,
> and for ordinary reviews `(P / I) ** mu`, which falls as the previous
> interval `I` grows past the pivot `P` and rises below it. `mu = 0.25` is a
> fourth root, so the effect is deliberately gentle: a 1-day card gets 2.14
> and a 1-year card 0.49. The second factor reads how close the card was to
> being forgotten. `R` is its chance of recall just before the answer, and the
> gap compares it with the desired retention of the card's deck in log-odds: 0
> when the card was answered at that retention, positive when late, negative
> when early. `g` is the gap of an answer a day or more after the card's
> previous one, `h` that of an answer on the same study day; the other is 0.
> `exp(rho * g)` is the ratio of the two odds to the power `rho`: with
> `rho = 0.25`, a card at 80% where the deck asks for 90% counts
> `(9 / 4) ** 0.25 = 1.22` times, and one at 95% counts 0.83 times. The clamp
> caps how far the factors can stack. `e_i` then enters the credit as
> `e_i ** 0.6`, so a weight of 2 adds about 52% to an answer's credit, not 100%.

With the defaults, the state factor is

$$
s_i=
\begin{cases}
1.5 & \text{first answer of a new card}\\
1 & \text{same-day learning or relearning step}\\
(21/I_i)^{0.25} & \text{review after } I_i \text{ days}
\end{cases}
$$

and for a review answered at its deck's desired retention the clamp gives

$$
e_i=
\begin{cases}
2 & \text{if } I_i\le1.3\text{ days}\\
(21/I_i)^{0.25} & \text{if } 1.3\lt I_i\lt 336\text{ days}\\
0.5 & \text{if } I_i\ge336\text{ days}
\end{cases}
$$

so a 7-day review counts 1.32, a 21-day one 1 and a 90-day one 0.69. The
formula uses the card state Anki records per answer, and the chance of recall
replayed from it (see Chance of recall):

- $s_i$: $w_{new}$ for a card's earliest recorded answer, $w_{step}$ for
  same-day learning and relearning answers (`lastIvl` below one day), and
  $(P/I_i)^{\mu}$ for interday reviews with previous interval $I_i$ days.
- $g_i$, $h_i$: the answer's recall gaps. A card's first answer, deleted cards
  and collections without FSRS hold no state before the answer and read 0.

Defaults: $w_{new}=1.5$, $w_{step}=1$, $P=21$ d, $\mu=0.25$, $\rho=\sigma=0.25$,
$L=2$, edited from `Calibrate…` beside `Color by`. A routine review of a pivot
card answered on time has $e_i=1$, so days of routine reviews score as in
Workload (review-weighted). The manual reference day is shared with the other
workload modes and recomputed when a calibration changes. Only the
experimental modes read per-answer card state.

Up to formula 3, a fourth root of the FSRS difficulty, decoded from
`revlog.factor`, stood where the recall factor is. It explained nothing: on one
person's reviews, 0.01% of answer time once template, month and button were
known, and nothing beyond recall and lateness. Its scale also differs between
presets.

### Chance of recall

Anki keeps no chance of recall per answer, so `memory.py` replays each card's
review log in plain Python. It follows Anki's rules for which entries make up a
card's history (rslib `scheduler/fsrs/params.rs`, `reviews_for_fsrs`): the
card's last run of learning steps starts it; without one, its first answer that
set an interval of a day or more does, from the state SM-2's ease and interval
imply; a reset discards what came before; cramming in a filtered deck never
counts; a preset's "ignore reviews before" date is honoured. Whole days count
from the next day cutoff, as Anki counts them.

- An answer a day or more after the card's previous one reads FSRS-6 with the
  parameters of the card's deck preset: the model Anki schedules with, fitted
  to the user's own reviews. A filtered card reads its home deck's preset, and
  a deck's own desired retention replaces its preset's.
- An answer on the same study day reads FSRS-7 with its published default
  parameters. FSRS-6 counts whole days and gives every same-day answer
  $R = 1$; FSRS-7 counts fractions of a day. Anki fits no FSRS-7 parameters,
  so everyone gets the defaults.

The formulas agree with the official libraries, py-fsrs 6.3.3 for FSRS-6 and
ts-fsrs 6.0.0 for FSRS-7, to rounding, on 20,000 random states each.

On one person's 2026 reviews:

| | FSRS-6, fitted | FSRS-7, defaults |
| --- | --- | --- |
| Log loss of $R$ on pass or fail, a day or more apart | 0.461 | 0.495 |
| The same within a day | 3.41 (always 1) | 0.532 |
| Answer time at 80% instead of 90%, a day or more apart | +4.6% | +3.4% |
| The same within a day | | +0.2% |

Answer time was compared within a template and month, with the button,
lateness and previous interval known; recall explained 0.24% of what varied.
Where FSRS-7 had fitted parameters too, it edged out FSRS-6 a day or more apart
(0.544 against 0.546), as its author reports. A first look at 39 felt-effort
ratings found no link with the gap (rank correlation 0.04), so both exponents
stay gentle until more ratings settle it.

The replay takes about 0.3 s for 133,000 answers. It is kept between redraws
and takes in new answers as they come; anything else (older answers arriving
by sync, an undo, a changed preset, deck or card) replays everything. FSRS-based
days are kept until an answer, a card's deck, the calibration or the view
changes: recomputing a full history costs about 0.2 s, redrawing it 13 ms.

## Sustained concentration

The second experimental mode scales each answer's credit by a session
coefficient: $\sum_i c_i\,e_i^a d_i^b$. Sessions are read from every answer,
including excluded decks, and an idle gap longer than the break $G$ starts a
new one. Within a session, with $d_j$ the recorded duration in seconds, $m_T$
the median recorded duration of the card's template over the included history,
$k$ the template cap and $\theta$ the minimal sensitivity, heavy study
accumulates as

$$
A_i=\sum_{j\le i}\frac{\ell_j}{60}\,\mathrm{clamp}\!\Big(\frac{\ell_j-\theta}{\theta},\,0,\,1\Big),
\qquad \ell_j=\min\big(d_j,\ k\,m_{T(j)}\big).
$$

> [!TIP]
> `A_i` is a running total of heavy minutes within the current session, reset
> to zero after every break. Each answer adds its duration `l`, in minutes,
> multiplied by a gate between 0 and 1: the gate is 0 up to the sensitivity
> `theta` (45 s), 1 from `2 * theta` (90 s) on, and linear in between, so there
> is no hard cutoff. `l` is the recorded duration capped at `k` times the card
> template's median duration `m_T`. A 2-minute answer on a template that
> normally takes 20 s is capped at 30 s, which the gate then zeroes; this is how
> the clock tells heavy material from a slow day. The recorded duration still
> enters the time term of the credit unchanged; only the clock sees the cap.

With the defaults ($\theta=45$ s, $k=1.5$), one answer adds to the clock

$$
\Delta A_j=
\begin{cases}
0 & \text{if } \ell_j\le45\text{ s}\\
\dfrac{\ell_j}{60}\cdot\dfrac{\ell_j-45}{45} & \text{if } 45\lt\ell_j\lt 90\text{ s}\\
\ell_j/60 & \text{if } \ell_j\ge90\text{ s}
\end{cases}
\qquad
\ell_j=\min(d_j,\ 1.5\,m_{T(j)})
$$

A 60-second answer adds 0.33 min and a 2-minute answer 2 min. A 2-minute
answer on a template whose median is 20 s is capped at 30 s and adds nothing.

A slow day on quick cards therefore builds nothing, while recorded durations
themselves stay untouched. With the smoothstep $S(x)=3x^2-2x^3$ on $[0,1]$, the
warm-up $w$, full concentration $f$, the fatigue cap $T$ and the fade $\kappa$,
the coefficient after $t$ minutes of the session is

$$
c_i=1+\beta\,S\!\Big(\frac{A_i-w}{f-w}\Big)\,g(t_i),\qquad
g(t)=\begin{cases}1 & t\le T\\ e^{-(t-T)/\kappa} & t\gt T\end{cases}
$$

> [!TIP]
> `c_i` multiplies an answer's whole credit and is never below 1. The bonus has
> three parts. `beta` is the ceiling: 1 means an answer at full concentration is
> worth double. `S(x)` is the smoothstep function `3x² - 2x³` applied to how far
> `A_i` sits between the warm-up `w` and full concentration `f`: 0 below `w`, 1
> above `f`, and an S-shaped ramp in between that is flat at both ends, which is
> why the first heavy minutes earn nothing and the rise is gradual rather than
> abrupt. `g(t)` is the fatigue term, with `t` the minutes since the session
> began: 1 up to the cap `T`, then an exponential decay with time constant
> `kappa`, which halves the remaining bonus about every `0.7 * kappa` minutes,
> roughly every 21 minutes with the defaults. Because the fade multiplies only
> the bonus, no answer scores below its unbonused credit, though a long session
> can score below the same answers split across sittings.

With the defaults ($\beta=1$, $w=10$, $f=30$, $T=75$, $\kappa=30$ min),
$c_i=1+g(t_i)\,b(A_i)$ with

$$
b(A)=
\begin{cases}
0 & \text{if } A\le10\\
S\!\Big(\dfrac{A-10}{20}\Big) & \text{if } 10\lt A\lt 30\\
1 & \text{if } A\ge30
\end{cases}
\qquad
g(t)=
\begin{cases}
1 & \text{if } t\le75\\
e^{-(t-75)/30} & \text{if } t\gt 75
\end{cases}
$$

Midway through the ramp, at 20 heavy minutes, $c_i=1.5$. At full
concentration a session that has run 90 minutes gives 1.61, 105 minutes 1.37
and 120 minutes 1.22.

The base credit never drops below 1. Defaults: $\beta=1$,
$\theta=45$ s, $k=1.5$, $w=10$ min, $f=30$ min, $T=75$ min, $\kappa=30$ min,
$G=5$ min, shown in the same popup as the effort settings. Rationale: effort
rises with time on task and uninterrupted blocks avoid switching costs, which
bounds the ceiling near 2×, while demanding work degrades after roughly an
hour, hence the fade.

## Calibration

The constants above were picked by hand and never tested against felt effort.
Calibrated effort replaces them with weights fitted to one person's ratings, and
ships only if it tracks that person's day ratings better than review-weighted.
`docs/calibration.md` covers operating it and what each question means.

The numbers below describe one person's 117,632 answers. They show behaviour, not
felt effort, which is why ratings are collected at all.

- **Record first.** Anki discards front and back time, uncapped time, the memory
  state before an answer, card size and interruptions once an answer ends, so
  each day without recording loses them for good. Events stay raw, never computed
  features, so any later formula can re-derive what it needs. They hold sizes,
  never card text.
- **Stay hidden.** Everything lives in this add-on behind the `heatmap_devmode`
  profile key. Other users see nothing, are never prompted and pay no runtime cost.
- **Use a variable only if it earns its place.** FSRS is not a goal. FSRS difficulty
  predicted almost nothing within a template, and its scale differs by preset: in
  one, a new card rated Good starts at 1 and loses up to 23% credit. Failures
  followed lateness, not interval: about 10% on time and 30% when over 2.5× late,
  at every interval length. Difficulty has since left FSRS-based; the chance of
  recall, which reads lateness and the card's memory together, took its place.
- **Split "effort".** It mixes how demanding an answer was, how sharp the rater
  was, how much they invested and what they got from it, so one number can't
  carry it all.
- **Maturity is not knowledge.** A known card may be failed on purpose to clear up
  a nuance, which then shrinks its interval. Again took about twice as long as a
  pass on quick templates and a quarter as long on a rich one, and slow and normal
  Agains had the same repeat-fail rate, so time can't tell them apart. The reason
  is asked.
- **Note types differ, and change.** Rich cards behave unlike quick ones, every user
  has other note types, and one template's median review went from 10 s to 177 s
  in 2026. So note-type inputs come from timing over the last 60 days, never from
  names or all-time medians.
- **Readiness can't be inferred.** On the same kinds of answers, whole days differ
  by about 17% in speed and 7 points in failures, those two are uncorrelated
  (0.02), and time of day explains about ±3%. So it is asked, and only enters the
  fit as a control: public users will never have it.
- **Learning isn't visible in the log.** Session pace didn't change whether a card
  passed its next review (odds ratio 1.01, n = 1,932), so gain is asked, and kept
  apart from effort: grinding is much effort for little gain.
- **Ask each level what can be felt there.** A first survey asked sampled answers for
  their cost in routine reviews. The answers followed the button, not the time, so a
  fit on them could only restate review-weighted. What is felt on a card is how hard
  it made you think; how much work there was is felt over a sitting or a day. So
  cards are asked the first, and sittings and days the second, where time is felt.
- **The day is the judge.** The heatmap shows days, so the evening's rating of how
  much Anki work a day was tests every formula whatever it becomes. A day of 427
  answers in 103 minutes that didn't feel like much ranked 57th by time but 5th on
  review-weighted: the count weight overscored it, and hand-tuned effort factors
  can't fix that. How hard the day felt and what the rest of it took are asked apart,
  so that a tiring day isn't rated as a big one.
- **Ask about a day only after studying it.** The evening rating comes once the day's
  study is mostly done, once a day, and covers what was answered before it. Asking
  the next morning meant rating yesterday before any study.
- **Rate on a row of choices, none preselected.** A slider's resting place was
  avoided by one question and chosen by another, and its finer steps added nothing a
  fit could use. Each choice describes what happened, so there is nothing to compare
  with.

| Answer | Asked | Role |
| --- | --- | --- |
| `effort`: how hard you had to think | Sampled cards, once answered | Stage 1: which answers are harder |
| `familiar`: known before | New cards | Control in stage 1, never scores |
| `gained`, `know` | Taking turns, with `effort` | Recorded; the report tests what predicts them |
| `wrong`: what went wrong | Failed cards | Report only: scoring never has it |
| `work`: how much work | Sittings | Stage 2: how answers add up |
| `focus`, `deep` | Sittings | A control in stage 1; a test of sustained concentration |
| `amount`: how much Anki work | Evenings | Stage 2, and judges every formula |
| `strain`, `rest` | Evenings | Keep how the day felt out of `amount` |
| `versus`, `pair` | Evenings, when there is a day or two note types to name | A second judge; stage 2 |
| `learned` | Evenings | Reported beside `amount`, outside the gate |

The questions are asked where the reviews happen, by a recording tool whose sealed
files reach the add-on in the media folder, which syncs like any media. Fit resolves
their note keys and matches them to the synced revlog, which alone supplies the
answer button. Time spent on a question that sits inside a card's timer is removed
for fitting only: heatmap scores keep it. The rating dialogs on desktop asked the
first survey's questions, and are gone.

## Calibrated effort

Each answer's credit is fitted, and the score sums it like any other measure:

$$
\text{credit}_i=e^{\eta_i},\qquad \eta_i=w_0+\sum_j w_j x_{ij},\qquad
\tfrac1{16}\le e^{\eta_i}\le 32
$$

> [!TIP]
> In code this is `exp(intercept + sum(weight * input))`, in
> `effort_model_credit`. Adding in the exponent multiplies factors, so scores stay
> factors of 1 in log space. An input of 0 changes nothing, and a weight of 0.69
> doubles the credit per unit of its input. Since `log_d` is `ln(seconds / 10)`,
> `exp(w * log_d)` is `(seconds / 10) ** w`: its weight **is** the time exponent,
> learned from ratings instead of set by hand. The clamp only stops a wild input.

The inputs come from revlog rows and deck presets alone, through one function shared
by fitting and scoring, so a day scores the same on any view and historic days can be
scored:

| Input | Meaning |
| --- | --- |
| `log_d` | $\ln(\text{seconds}/10)$ |
| `rich` | $\ln$(time-weighted median / median) of the template, 0 to 3 |
| `t_med` | $\ln(\text{median seconds}/10)$ of the template |
| `log_d·rich` | Lets the time exponent differ on rich templates |
| `new`, `step` | First answer, same-day step |
| `again`, `hard`, `easy` | This answer's button |
| `overdue` | $\ln(\text{elapsed}/\text{last interval})$, 0.2 to 50, 0 unless interday |
| `reps` | $\ln(1+\text{interday answers so far})$ |
| `reps·rich` | Lets repetition matter differently on rich templates |
| `recall` | Recall gap a day or more after the card's previous answer (FSRS-6), −4 to 4 |
| `same_day_recall` | Recall gap on the same study day (FSRS-7 defaults), −4 to 4 |
| `day_load` | $\ln(1+\text{answers given earlier that study day})$ |

Template inputs read the 60 days before the answer, or all earlier days under 30
answers, and never the day itself. `rich` is meant to tell rich cards from quick
ones on any collection, with no note-type names. That rests on one note type so far.

The weights are fitted in two stages, in pure Python in `calibration/fit.py` and
`calibration/credit.py`, run from a tab button with no library.

**Stage 1, cards.** An ordered logistic regression of the `effort` ratings $y_i$, how
hard a card made its owner think, on the inputs:

$$
P(y_i\le j)=F\Big(c_j-\sum_m v_m x_{im}\Big),\qquad
\delta_i=\sum_{m\notin T} v_m x_{im}
$$

> [!TIP]
> `F` is the logistic curve. The cut points `c_j` between the six levels are fitted
> too: a rating says which of two answers was harder, not by how much, so the
> weights `v` only order answers. An L2 penalty of 2 on standardized inputs keeps few
> ratings from overfitting. The difficulty `δ` leaves out `T`, the two time inputs
> `log_d` and `log_d·rich`: the fit reads them, so that its other weights compare
> answers of equal length, and time is then counted once, in stage 2. Controls
> explain the ratings and never score: the sitting's focus, what was known of a new
> card, and drift over the months.

Logistic regression beat gradient-boosted trees up to about 800 labels, and trees
only caught up around 3,000 to 6,000. 15 inputs did as well as 33. Reps and lapses
correlate at 0.81, so card history stops at reps.

**Stage 2, work.** An answer's credit is $e^{b\ell_i+k\delta_i}$, with $\ell_i$ its
`log_d`. A sitting or a day $u$ has the summed credit $S_u$, and a pair rating
compares a day's two named templates through $\ln S_{u,2}-\ln S_{u,1}$. Each kind of
rating $r$ is an ordered logit on its own log credit, with a slope and cut points of
its own:

$$
P(r_u\le j)=F\big(c^{r}_j-\alpha_r\ln S_u\big)
$$

$b$ and $k$ maximize the sum over the three kinds, every slope and cut refitted at
each $b$ and $k$ tried.

> [!TIP]
> `b` is the whole time exponent: 0 counts answers, 0.4 is review-weighted and 1 is
> recorded time. `k` says how much difficulty adds. Credit is clamped as in scoring,
> and centred so that an average answer counts 1: ratings fix no size. The shipped
> file keeps its shape: the weight of `log_d` is `b`, that of `log_d·rich` is 0, and
> every other input weighs `k` times its stage 1 weight. Letting the difficulty keep
> its time terms would count time twice, and `b` = 0.4 would no longer mean
> review-weighted.

If the 90% profile likelihood interval of $b$ holds both 0.2 and 0.8, the ratings
can't tell a count from time: $b$ stays at 0.4, and $k$ alone is fitted. Two fits,
with $b$ fixed at each end, decide it. A bootstrap of days agreed, except on days of
one quick note type at 60 days, where its wider ranges would keep 0.4 more often.

Simulated on 60 real days of one person's reviews, drawn with replacement, with
planted rules of effort and work, and ratings at that person's survey rates:

- $b$ came back without bias. On days mixing a quick and a slow note type it was
  within about ±0.1 after 30 days and ±0.04 after 120. On days of one quick note
  type, within ±0.2 after 60 days, and only through the sitting ratings: evening
  ratings alone left ±0.46.
- Day scores were typically within 4 to 6% of the planted truth, against 7 to 30%
  for review-weighted on mixed days. With nothing to gain, the fit cost 1 to 3%.
- Effects no input could see, a note type harder than its inputs or failures that
  mean something else on mature cards, left $b$ unbiased and day scores within about
  5 to 6%.

There are two fits. The **shared fit** uses these inputs, with the controls centred
and scored at 0. It is what ships. The **profile fit** adds one offset per template
with 30+ effort ratings to stage 1, kept in that profile's `model.json`: a note type
can feel harder than its inputs show, and only ratings can tell. Offsets never ship,
because they mean nothing on another collection. Never fit them and then drop them:
in earlier research that scored worse than never fitting them.

The shipped `effort_model.json` holds `"status": "none"` until a model passes the
gate. Until then the measure is hidden, `FSRS_CALIBRATION_DEFAULTS` and the default
measure stay as they are, and failing is a valid result.

The gate needs 300 effort ratings and 60 rated days. The rated days are cut into
four blocks in time order, and every block but the first is scored by a fit on
everything rated before it. In each, an ordered logit of the evening's `amount` on
$\ln$ review-weighted and $\ln$(fitted / review-weighted) gives the $t$ of the second,
and the blocks' $t$ add up to $z$. Fitted must reach $z\ge1.645$, one-sided $p<0.05$,
and be no worse than review-weighted in plain rank correlation. A fixed margin can't
work: day scores agree so closely that even a perfect fit gains only 0.02 to 0.05. A
rank correlation with review-weighted removed from both sides, used before, passed
review-weighted plus pure noise in 8 to 14% of simulated cases: ranking bends the
tails of ratings and candidate alike, and a straight line on ranks leaves that bend
behind. In simulation a real gain passed 73% of the time at about 68 rated days and
86% at about 90 on mixed days, far less often on days of one quick note type, where
every measure ranks days alike, and about 5% of the time with no gain. So 60 days is
a minimum, not a fair trial.

## Recorded time

Use Anki's recorded durations as-is as scoring inputs. Unusual recorded times,
interruptions, and timer-cap choices are the user's responsibility. Do not
smooth, replace, or otherwise correct durations based on presumed interruptions
or historical timing norms.