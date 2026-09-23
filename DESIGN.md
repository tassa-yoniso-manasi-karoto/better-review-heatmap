# P03 — Workload design decisions

**Status:** implemented using per-answer sums; the bonus constraint was withdrawn.

Let $R$ be daily answer count, including repeated attempts, $d_i$ each answer's
Anki-recorded minutes, and $T=\sum_i d_i$. Apply Fable's power-product family to
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

“Linear” means equal input influence in the UI, not mathematical linearity.
Exact score doubling is no longer a design requirement.

Durations use minutes; additional pace-anchor calibration remains undecided.

## Scope

- Preserve review history, settings, selected reference dates, and history filters.
- Use Anki's recorded time; users remain responsible for their timer cap.
- Preserve the intentional below/above-target color jump.
- Defer individual-deck changes and special zero-time handling.
- Removing automatic reference selection in favor of mandatory manual day
  selection is decided, but implementation is deferred.
- Keep room for a future Experimental mode; concentration or burstiness
  adjustments are deferred.
- Reuse the shared calculations in the existing CLI.

## Mixing credit

Use the per-answer sum directly. Pooling fast and slow answers adds no extra
credit, so no bonus cap or cram-day threshold is applied. The earlier 25% bonus
condition and 250-answer/150-minute thresholds are superseded.
