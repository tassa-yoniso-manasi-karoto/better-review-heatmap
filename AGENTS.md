# Heatmap Metrics CLI Guide

Test formula changes without Anki or add-on build.

## Files

- Tool: `scripts/metrics_cli.py`
- Formulas: `src/review_heatmap/metrics.py`
- Tests: `tests/test_metrics_cli.py`, `tests/test_metrics.py`

Stdlib only. Works with `python -S`.

## Commands

```sh
# Basic table check
python scripts/metrics_cli.py --reviews 100 --minutes 10

# JSON output
python scripts/metrics_cli.py --reviews 100 --time-ms 600000 --json

# Baseline check
python scripts/metrics_cli.py --reviews 100 --minutes 10 --reference-reviews 200 --reference-minutes 40 --json

# Single metric
python scripts/metrics_cli.py --reviews 100 --minutes 10 --metric workload --json
```

## CLI Flags

- `--reviews N`: Nonnegative integer. Required.
- `--minutes T` | `--time-ms M`: Duration. One required. Fractional minutes OK; milliseconds integer.
- `--metric KEY`: Optional. Default `all`. Choices: `all`, `reviews`, `time`, `workload`.
- `--reference-reviews N` + `--reference-minutes T`: Optional baseline pair. Both required together. Both > 0.
- `--json`: Output single raw JSON object.

Exit code: 0 success, 2 invalid input (negative, nonfinite, missing pair, bad key).

## JSON Output

Keys:
- `schema_version`: 1
- `metric_source_path`: `metrics.py` path
- `inputs`: `{"reviews": N, "minutes": T, "milliseconds": M}`
- `reference`: `null` or input totals for reference day
- `results.<metric>`:
  - `score`: `activity_value(reviews, milliseconds, metric)`
  - `fixed_thresholds`: Levels from `activity_levels(metric)`. Null for classic.
  - `reference_score`: Reference day score. Null for classic or no reference.
  - `target_score`: 85% of reference score (`baseline_value(reference)`).
  - `ratio` / `percent`: `score / target_score` and `100 * ratio`.
  - `color`: CSS RGBA from `baseline_color(score, reference)`. Null if reviews 0.

## Formula Revision Workflow

1. Edit `src/review_heatmap/metrics.py` (`activity_value`, `FIXED_LEVELS`, `activity_levels`, `baseline_value`).
2. Run CLI with edge cases:
   - Zero: `--reviews 0 --minutes 0`
   - Dense fast: `--reviews 300 --minutes 15`
   - Slow reading: `--reviews 20 --minutes 40`
3. Run tests:
   ```sh
   pytest tests/test_metrics_cli.py tests/test_metrics.py
   ```
