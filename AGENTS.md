# Git

- When writing commits focus on the WHY, not on the WHAT (the what is self obvious in the diff of the commit)
- IMPORTANT: when writing the commit message each line shouldn't go beyond 80 characters
- Do NOT write commit messages in the "conventional commit" style i.e. do not prefix with "feat: ", "fix: " or whatnot
- When writing detailed list of changed in the git descriptions, use one line per change and preceed it by a bullet point "∙"

Do not run git status.

# Heatmap Metrics CLI Guide

## Files

- Tool: `scripts/metrics_cli.py`
- Formulas: `src/review_heatmap/metrics.py`
- Tests: `tests/test_metrics_cli.py`, `tests/test_metrics.py`

Stdlib only. Works with `python -S`.

Exact workload scores require individual answer durations. Totals-only inputs
assume every answer took the same time and are labeled accordingly. Collection
callers must pass actual duration buckets to `activity_value`; never use the
totals-only approximation to migrate a saved reference. Experimental scores
also need each answer's card state, and sustained concentration its timestamp
and card template; durations alone score every answer at effort 1 and
concentration 1.

## Commands

```sh
# Exact mixed-duration scores (milliseconds per answer)
python -S scripts/metrics_cli.py --durations-ms 15000 15000 240000

# Exact baseline comparison, JSON output
python scripts/metrics_cli.py --durations-ms 15000 15000 240000 \
  --reference-durations-ms 15000 240000 --json

# Custom review/time weights: either flag implies the other
python scripts/metrics_cli.py --durations-ms 15000 240000 \
  --metric custom --review-exponent 0.3 --time-exponent 0.7 --json

# Synthetic equal-duration scenario (100 answers of 6 seconds each)
python scripts/metrics_cli.py --reviews 100 --minutes 10 --json

# FSRS-based: answers as [duration_ms, lastIvl, factor, first], with overrides
python scripts/metrics_cli.py --answers-json answers.json --metric fsrs \
  --fsrs-calibration new_card_weight=2 --json

# Sustained concentration: add end_ms and the template's typical_ms per answer;
# without end_ms the answers form one back-to-back session
python scripts/metrics_cli.py --answers-json answers.json --metric concentration \
  --concentration-calibration warm_up_minutes=5 --json
```

## CLI Flags

- Supply `--durations-ms D ...`, `--answers-json FILE`, or `--reviews N` with
  `--minutes T` / `--time-ms M`. Durations and counts must be nonnegative;
  milliseconds are integers. With individual durations or answers, count is
  inferred; an explicit count must match their number.
- `--answers-json FILE`: JSON array of `[duration_ms, last_interval, factor,
  first, end_ms, typical_ms]` rows (revlog `lastIvl` and `factor`; `first`
  marks the card's earliest answer; `end_ms` the answer's timestamp;
  `typical_ms` the card template's median duration). Trailing fields default
  to 0/false/null; give `end_ms` for every answer or for none.
- `--metric KEY`: defaults to `all`; keys are `reviews`, `time`, `workload`,
  `custom`, `recorded_time`, `fsrs`, `concentration`. Legacy key `time` means
  **Workload (linear)**; `workload` is the recommended **⭐ Workload
  (review-weighted)**; `recorded_time` means time alone; `fsrs` is **🧪
  FSRS-based (exp.)** and `concentration` **🧪 FSRS-based + sustained
  concentration (exp.)**.
- Reference: `--reference-durations-ms D ...`, `--reference-answers-json FILE`,
  or the positive pair `--reference-reviews N --reference-minutes T`. Reference
  total time must be positive. An explicit count must match any supplied
  individual durations or answers.
- `--review-exponent A` / `--time-exponent B`: custom mode only, each in [0, 1].
  One implies its complement; if both are supplied, they must total 1.
- `--fsrs-calibration KEY=VALUE ...` / `--concentration-calibration KEY=VALUE
  ...`: experimental overrides. Keys and ranges are the `*_DEFAULTS` /
  `*_RANGES` pairs in `CALIBRATIONS` (`metrics.py`). The add-on stores only
  values a user changed (`calibration_overrides`), so defaults can be updated.
- `--json`: Output single raw JSON object.

Exit code: 0 success, 2 invalid input (negative, nonfinite, missing pair, bad key).

## JSON Output

Keys:

- `schema_version`: 2
- `metric_source_path`: `metrics.py` path
- `duration_model`: `individual` or `equal-duration assumption`.
- `reference_duration_model`: the same labels, or null without a reference.
- `inputs`: `{"reviews": N, "minutes": T, "milliseconds": M}`
- `reference`: `null` or input totals for reference day
- `results.<metric>`:
  - `score`: shared `activity_value` result with supplied durations and weights.
  - `exponents`: `reviews` and `time` weights.
  - `calibration`: experimental measures only; the calibrations in effect,
    keyed `fsrs_calibration` and, for concentration, `concentration_calibration`.
  - `fixed_thresholds`: Levels from `activity_levels(metric)`. Null for classic.
  - `reference_score`: Reference day score. Null for classic or no reference.
  - `target_score`: 85% of reference score (`baseline_value(reference)`).
  - `ratio` / `percent`: `score / target_score` and `100 * ratio`.
  - `color`: CSS hex/RGBA from `baseline_color`; null without reviews/reference.

Classic has no fixed thresholds or reference-derived results. Colors use the
bundled default gradient; the CLI does not load a profile's custom palette.

## Formula Revision Workflow

1. Change the shared functions in `src/review_heatmap/metrics.py`; do not copy
   formulas into the CLI. Preserve classic behavior and reference migrations.
   FSRS-based effort lives in `answer_effort` and sustained concentration in
   `concentration_coefficients`; their per-answer inputs come from
   `ActivityReporter._cards_done(with_effort=True)` and
   `_cards_done(with_concentration=True)` with `_template_norms`.
2. Compare mixed and uniform durations through the CLI. Use totals only for
   explicitly synthetic equal-duration scenarios.
3. Run focused tests; include activity/options tests when those paths change:

   ```sh
   pytest tests/test_metrics_cli.py tests/test_metrics.py
   ```
