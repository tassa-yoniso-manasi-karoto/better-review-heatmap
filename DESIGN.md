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
> the bonus, a long session never scores below the same answers spread out.

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

## Recorded time

Use Anki's recorded durations as-is as scoring inputs. Unusual recorded times,
interruptions, and timer-cap choices are the user's responsibility. Do not
smooth, replace, or otherwise correct durations based on presumed interruptions
or historical timing norms.