Let $R$ be daily answer count, including repeated attempts, $d_i$ each answer's
Anki-recorded minutes, and $T=\sum_i d_i$. Apply the power-product formula to
each answer, then sum:

$$
W_{a,b}=\sum_{i=1}^{R}1^a d_i^b=\sum_{i=1}^{R}d_i^b,\qquad a+b=1.
$$

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
| FSRS-based (experimental) | $\sum_i e_i^a d_i^b$ | Effort $e_i$ from card state replaces the count credit of 1; every constant is a calibration setting. |

“Linear” means equal input influence in the UI, not mathematical linearity.
Exact score doubling is not a design requirement but keeping scores as factors of 1 in log space is.

Durations use minutes; additional pace-anchor calibration remains undecided.

## Scope

- Preserve review history, settings, selected reference dates, and history filters.
- Preserve the intentional below/above-target color jump.
- Defer individual-deck changes and special zero-time handling.
- FSRS-based is the first experimental mode. A second one adding session or
  concentration adjustments on top of it is planned; its name is undecided.
- Reuse the shared calculations in the existing CLI.

## FSRS-based effort

Each answer contributes $e_i^a d_i^b$ with $a+b=1$; the time exponent $b$ is a
calibration setting. The effort

$$
e_i=\operatorname{clamp}\big(s_i\,(D_i/D_p)^{\lambda},\ 1/L,\ L\big)
$$

uses the card state Anki records per answer:

- $s_i$: $w_{new}$ for a card's earliest recorded answer, $w_{step}$ for
  same-day learning and relearning answers (`lastIvl` below one day), and
  $(P/I_i)^{\mu}$ for interday reviews with previous interval $I_i$ days.
- $D_i$: FSRS difficulty after the answer, decoded from `revlog.factor`
  (100–1100 maps to 1–10). Answers recorded without FSRS use $D_i=D_p$.

Defaults: $b=0.4$, $w_{new}=1.5$, $w_{step}=1$, $P=21$ d, $\mu=0.25$, $D_p$ at
50% (5.5), $\lambda=0.25$, $L=2$. All are synced settings (`fsrs_calibration`)
edited from `Calibrate…` beside `Color by`, each with a hover hint. A routine
review of a pivot card has $e_i=1$, so days of routine reviews score as in
Workload (review-weighted). The manual reference day is shared with the other
workload modes and recomputed when the calibration changes. Only this mode reads
per-answer card state.

## Recorded time

Use Anki's recorded durations as-is as scoring inputs. Unusual recorded times,
interruptions, and timer-cap choices are the user's responsibility. Do not
smooth, replace, or otherwise correct durations based on presumed interruptions
or historical timing norms.