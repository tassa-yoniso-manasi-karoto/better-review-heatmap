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
e_i=\mathrm{clamp}\big(s_i\,(D_i/D_p)^{\lambda},\ 1/L,\ L\big)
$$

> [!TIP]
> The count credit of 1 is replaced by `e_i`, the product of two factors,
> clamped to `[1/L, L]`. The first factor is a lookup on the card's state: a
> fixed weight for a card's first answer, a fixed weight for same-day steps,
> and for ordinary reviews `(P / I) ** mu`, which falls as the previous
> interval `I` grows past the pivot `P` and rises below it. `mu = 0.25` is a
> fourth root, so the effect is deliberately gentle: a 1-day card gets 2.14
> and a 1-year card 0.49. The second factor `(D / D_p) ** lambda` reads the
> FSRS difficulty, with `D_p` the difficulty that counts as neutral; another
> fourth root, so even extreme difficulties move the weight by about 20%. The
> clamp caps how far the two factors can stack. `e_i` then enters the credit as
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

and for a review of a card at the neutral difficulty the clamp gives

$$
e_i=
\begin{cases}
2 & \text{if } I_i\le1.3\text{ days}\\
(21/I_i)^{0.25} & \text{if } 1.3\lt I_i\lt 336\text{ days}\\
0.5 & \text{if } I_i\ge336\text{ days}
\end{cases}
$$

so a 7-day review counts 1.32, a 21-day one 1 and a 90-day one 0.69. The
difficulty factor $(D_i/5.5)^{0.25}$ multiplies this by 0.65 at difficulty 1
and 1.16 at 10 before the clamp. The formula uses the card state Anki records
per answer:

- $s_i$: $w_{new}$ for a card's earliest recorded answer, $w_{step}$ for
  same-day learning and relearning answers (`lastIvl` below one day), and
  $(P/I_i)^{\mu}$ for interday reviews with previous interval $I_i$ days.
- $D_i$: FSRS difficulty after the answer, decoded from `revlog.factor`
  (100–1100 maps to 1–10). Answers recorded without FSRS use $D_i=D_p$.

Defaults: $w_{new}=1.5$, $w_{step}=1$, $P=21$ d, $\mu=0.25$, $D_p$ at 50%
(5.5), $\lambda=0.25$, $L=2$, edited from `Calibrate…` beside `Color by`. A
routine review of a pivot card has $e_i=1$, so days of routine reviews score as
in Workload (review-weighted). The manual reference day is shared with the
other workload modes and recomputed when a calibration changes. Only the
experimental modes read per-answer card state.

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
  at every interval length.
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
  apart from cost: grinding is high cost and low gain.
- **The day is the judge.** The heatmap shows days, so day ratings test every formula
  whatever it becomes. A day of 427 answers in 103 minutes that didn't feel tiring
  ranked 57th by time but 5th on review-weighted: the count weight overscored it,
  and hand-tuned effort factors can't fix that.
- **Ask about a day only after studying it.** The day rating comes on the way back to
  the deck list after reviewing, from 19:00, once a day, with 20+ answers today. It
  rates today, and appears before the deck list draws. Asking the next morning meant
  rating yesterday before any study, and needed the heatmap hidden until then. A day
  with no evening exit stays unrated. Most study days end between 19:15 and 21:30,
  so 19:00 leaves up to 79% of days rateable with about 88% of their answers done.
- **Rate on sliders.** Buttons were too discrete, `gain` most of all with three levels,
  so ratings take finer steps, each with a description to match a feeling against. A
  slider is no answer until touched, so its resting place can't become the commonest
  rating. Keys only set a position: Enter confirms, so a slip can be corrected.

| Answer | Asked | Role |
| --- | --- | --- |
| `cost`: effort in routine reviews | Sampled answers | Trains the model |
| `gain`, `grasp` | Rotating, with `cost` | Recorded, nothing reads them yet |
| `why`: reason for Again | Sampled lapses | Report only: scoring never has it |
| `sharpness`: concentration | Start of a study block | Control in the fit, never scores |
| `tiring` | Evening, on leaving the reviewer | Judges every formula, decides the gate |
| `learned` | Evening, on leaving the reviewer | Reported beside `tiring`, outside the gate |

Reviews done elsewhere can join the same data through sealed recording files in the
media folder, which sync like any media. Fit resolves their note keys and matches
them to the synced revlog, which alone supplies the answer button. Time spent in a
survey that sits inside a card's timer is removed for fitting only: heatmap scores
keep it.

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

The inputs come from revlog rows alone, through one function shared by fitting and
scoring, so a day scores the same on any view and historic days can be scored:

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

Template inputs read the 60 days before the answer, or all earlier days under 30
answers, and never the day itself. `rich` is meant to tell rich cards from quick
ones on any collection, with no note-type names. That rests on one note type so far.

The fit is a regularized ordered logistic regression on the `cost` ratings
$y_i$, which step through ¼, ⅓, ½, ¾, 1, 1½, 2, 3, 4, 6 and 8+ routine reviews, half
doublings rounded to plain numbers:

$$
P(y_i=k)=F\Big(\frac{c_k-\eta_i}{\sigma}\Big)-F\Big(\frac{c_{k-1}-\eta_i}{\sigma}\Big),
\qquad c=\ln 2\cdot(-1.75,\,-1.25,\,\dots,\,2.75)
$$

> [!TIP]
> This is the machine learning step, in pure Python in `calibration/fit.py`, run
> from a tab button with no library. `F` is the logistic curve and `sigma` the noise
> in the ratings. Each rating means the true cost fell in a range of log cost, and
> the fit finds the weights that make the given ratings most likely. Ranges sit
> halfway between steps, and they are **fixed**: plain ordered logit learns
> them freely and so can only order answers, while fixed ranges give the weights an
> absolute size. The price is trust in the rater's sense of "double": misjudged by
> ×0.7 or ×1.4, the time exponent moves 30 to 45%, while day ordering mostly
> survives. An L2 penalty of 2 on standardized inputs keeps few ratings from
> overfitting.

Logistic regression beat gradient-boosted trees up to about 800 labels, and trees
only caught up around 3,000 to 6,000. 15 inputs did as well as 33. Reps and lapses
correlate at 0.81, so card history stops at reps. Simulated at 800 noisy ratings,
the fit recovers the time exponent to about ±0.05.

There are two fits. The **shared fit** uses these inputs, with sharpness and drift
as centred controls scored at 0. It is what ships. The **profile fit** adds one
offset per template with 30+ ratings, kept in that profile's `model.json`: a note
type can cost more than its timing shows, and only ratings can tell. Offsets never
ship, because they mean nothing on another collection. Never fit them and then
drop them: that scored worse than never fitting them. Typical day error in
simulation, on 145 real days:

| Note types carry | Shared fit | Profile fit | Fitted, then dropped | Review-weighted |
| --- | --- | --- | --- | --- |
| Effort their timing shows | 3.1% | 4.3% | 3.8% | 21.2% |
| Hidden effort | 7.9% | 4.1% | 12.1% | 15.4% |
| Both | 5.8% | 4.7% | 8.8% | 23.1% |
| Nothing | 3.2% | 4.5% | 4.7% | 7.6% |

The shipped `effort_model.json` holds `"status": "none"` until a model passes the
gate. Until then the measure is hidden, `FSRS_CALIBRATION_DEFAULTS` and the default
measure stay as they are, and failing is a valid result.

The gate needs 800 cost ratings and 60 rated days. On the later 30% of rated days,
with the fit trained only on earlier ratings, fitted must be no worse than
review-weighted on `tiring`, and must explain `tiring` ratings review-weighted leaves
unexplained: the rank correlation with review-weighted removed from both, one-sided
$p<0.05$. A fixed Spearman margin can't work: day scores agree so closely that even
a perfect fit gains only 0.02 to 0.05, so a margin of 0.05 turned away real gains
more surely with more days. This test gets stronger with data, passing a real gain
23 to 41% of the time at 60 rated days and 51 to 80% at 180, with about 7% false
passes. So 60 days is a minimum, not a fair trial.

## Recorded time

Use Anki's recorded durations as-is as scoring inputs. Unusual recorded times,
interruptions, and timer-cap choices are the user's responsibility. Do not
smooth, replace, or otherwise correct durations based on presumed interruptions
or historical timing norms.