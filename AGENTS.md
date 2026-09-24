# Heatmap Metrics CLI Guide

Test formula changes without Anki or an add-on build. Read [DESIGN.md](DESIGN.md)
for current decisions, their rationale, and deferred work.

## Suggesting commit messages

- When writing commits focus on the WHY, not on the WHAT (the what is self obvious in the diff of the commit)
- IMPORTANT: when writing the commit message each line shouldn't go beyond 80 characters
- Do NOT write commit messages in the "conventional commit" style i.e. do not prefix with "feat: ", "fix: " or whatnot
- When writing detailed list of changed in the git descriptions, use one line per change and preceed it by a bullet point "∙"

## Files

- Tool: `scripts/metrics_cli.py`
- Formulas: `src/review_heatmap/metrics.py`
- Tests: `tests/test_metrics_cli.py`, `tests/test_metrics.py`

Stdlib only. Works with `python -S`.

Exact workload scores require individual answer durations. Totals-only inputs
assume every answer took the same time and are labeled accordingly. Collection
callers must pass actual duration buckets to `activity_value`; never use the
totals-only approximation to migrate a saved reference.

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
```

## CLI Flags

- Supply `--durations-ms D ...`, or `--reviews N` with `--minutes T` /
  `--time-ms M`. Durations and counts must be nonnegative; milliseconds are
  integers. With individual durations, count is inferred; an explicit count
  must match the number of durations.
- `--metric KEY`: defaults to `all`; keys are `reviews`, `time`, `workload`,
  `custom`, `recorded_time`. Legacy key `time` means **Workload (linear)**;
  `recorded_time` means time alone.
- Reference: `--reference-durations-ms D ...`, or the positive pair
  `--reference-reviews N --reference-minutes T`. Reference total time must be
  positive. An explicit count must match any supplied individual durations.
- `--review-exponent A` / `--time-exponent B`: custom mode only, each in [0, 1].
  One implies its complement; if both are supplied, they must total 1.
- `--json`: Output single raw JSON object.

Exit code: 0 success, 2 invalid input (negative, nonfinite, missing pair, bad key).

## JSON Output

Keys:

- `schema_version`: 1
- `metric_source_path`: `metrics.py` path
- `duration_model`: `individual` or `equal-duration assumption`.
- `reference_duration_model`: the same labels, or null without a reference.
- `inputs`: `{"reviews": N, "minutes": T, "milliseconds": M}`
- `reference`: `null` or input totals for reference day
- `results.<metric>`:
  - `score`: shared `activity_value` result with supplied durations and weights.
  - `exponents`: `reviews` and `time` weights.
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
2. Compare mixed and uniform durations through the CLI. Use totals only for
   explicitly synthetic equal-duration scenarios.
3. Run focused tests; include activity/options tests when those paths change:

   ```sh
   pytest tests/test_metrics_cli.py tests/test_metrics.py
   ```
