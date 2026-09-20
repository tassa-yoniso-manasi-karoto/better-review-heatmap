import json
from pathlib import Path
import subprocess
import sys

import pytest

from review_heatmap.metrics import (
    METRICS, activity_levels, activity_value,
    baseline_color, baseline_value, reference_from_day,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = REPO_ROOT / "scripts" / "metrics_cli.py"


def run_cli(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(CLI_PATH)] + list(args),
        cwd=cwd or str(REPO_ROOT),
        capture_output=True,
        text=True,
    )


def test_json_scores_match_direct_metric_calls():
    proc = run_cli(["--reviews", "100", "--minutes", "10", "--json"])
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)

    assert data["schema_version"] == 1
    assert data["inputs"] == {"reviews": 100, "minutes": 10.0, "milliseconds": 600000}
    assert data["reference"] is None

    for metric_key, meta in METRICS.items():
        res = data["results"][metric_key]
        assert res["label"] == meta["label"]
        expected_score = activity_value(100, 600000, metric_key)
        assert res["score"] == pytest.approx(expected_score)
        if metric_key == "reviews":
            assert res["fixed_thresholds"] is None
        else:
            assert res["fixed_thresholds"] == list(activity_levels(metric_key))
        assert res["reference_score"] is None
        assert res["target_score"] is None
        assert res["ratio"] is None
        assert res["percent"] is None
        assert res["color"] is None


def test_reference_and_color_against_direct_calls():
    proc = run_cli([
        "--reviews", "120", "--minutes", "30",
        "--reference-reviews", "150", "--reference-minutes", "45",
        "--json",
    ])
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)

    assert data["reference"] == {"reviews": 150, "minutes": 45.0, "milliseconds": 2700000}
    assert data["palette"] == "bundled_default"

    # Classic metric marks reference calculations as null
    classic = data["results"]["reviews"]
    assert classic["score"] == 120.0
    assert classic["fixed_thresholds"] is None
    assert classic["reference_score"] is None
    assert classic["target_score"] is None
    assert classic["ratio"] is None
    assert classic["percent"] is None
    assert classic["color"] is None

    # Workload metrics match direct calls
    for metric_key in ("time", "workload"):
        res = data["results"][metric_key]
        score = activity_value(120, 1800000, metric_key)
        ref = reference_from_day((0, 150, 2700000), metric_key, "cli")
        assert ref is not None
        ref_score = ref["value"]
        target = baseline_value(ref)
        ratio = score / target
        percent = 100.0 * ratio
        color = baseline_color(score, ref, reference_day=False)

        assert res["score"] == pytest.approx(score)
        assert res["reference_score"] == pytest.approx(ref_score)
        assert res["target_score"] == pytest.approx(target)
        assert res["ratio"] == pytest.approx(ratio)
        assert res["percent"] == pytest.approx(percent)
        assert res["color"] == color
        assert res["palette"] == "bundled_default"


def test_equivalent_minute_and_millisecond_inputs():
    proc_min = run_cli(["--reviews", "80", "--minutes", "15", "--json"])
    proc_ms = run_cli(["--reviews", "80", "--time-ms", "900000", "--json"])

    assert proc_min.returncode == 0
    assert proc_ms.returncode == 0

    data_min = json.loads(proc_min.stdout)
    data_ms = json.loads(proc_ms.stdout)

    assert data_min["inputs"]["milliseconds"] == 900000
    assert data_ms["inputs"]["milliseconds"] == 900000
    assert data_min["results"] == data_ms["results"]


def test_zero_activity_and_empty_color():
    proc = run_cli([
        "--reviews", "0", "--minutes", "0",
        "--reference-reviews", "100", "--reference-minutes", "20",
        "--json",
    ])
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)

    assert data["inputs"]["reviews"] == 0
    assert data["inputs"]["milliseconds"] == 0
    for res in data["results"].values():
        assert res["score"] == 0.0
        assert res["color"] is None
        if res["fixed_thresholds"] is not None:
            assert res["ratio"] == 0.0
            assert res["percent"] == 0.0


@pytest.mark.parametrize("bad_args", [
    ["--reviews", "-1", "--minutes", "10"],
    ["--reviews", "10", "--minutes", "-5"],
    ["--reviews", "10", "--time-ms", "-1"],
    ["--reviews", "10"],  # missing time
    ["--reviews", "10", "--minutes", "5", "--time-ms", "300000"],  # mutually exclusive
    ["--reviews", "10", "--minutes", "5", "--reference-reviews", "50"],  # missing ref-minutes
    ["--reviews", "10", "--minutes", "5", "--reference-minutes", "10"],  # missing ref-reviews
    ["--reviews", "10", "--minutes", "5", "--reference-reviews", "0", "--reference-minutes", "10"],
    ["--reviews", "10", "--minutes", "5", "--reference-reviews", "-5", "--reference-minutes", "10"],
    ["--reviews", "10", "--minutes", "5", "--reference-reviews", "50", "--reference-minutes", "0"],
    ["--reviews", "10", "--minutes", "5", "--reference-reviews", "50", "--reference-minutes", "0.000001"],  # rounds to 0 ms
    ["--reviews", "10", "--minutes", "nan"],
    ["--reviews", "10", "--minutes", "inf"],
    ["--reviews", "10", "--minutes", "-inf"],
    ["--reviews", "10", "--minutes", "5", "--reference-reviews", "50", "--reference-minutes", "nan"],
    ["--reviews", "10", "--minutes", "5", "--reference-reviews", "50", "--reference-minutes", "inf"],
    ["--reviews", "10", "--minutes", "5", "--metric", "invalid_metric"],
])
def test_invalid_inputs_exit_code_2(bad_args):
    proc = run_cli(bad_args)
    assert proc.returncode == 2
    assert "error:" in proc.stderr.lower()


def test_subprocess_from_another_dir_with_python_s(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-S", str(CLI_PATH), "--reviews", "100", "--minutes", "10", "--json"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["schema_version"] == 1
    assert data["inputs"]["reviews"] == 100
    assert data["inputs"]["milliseconds"] == 600000
    assert "workload" in data["results"]


def test_metric_filtering():
    proc = run_cli(["--reviews", "50", "--minutes", "10", "--metric", "workload", "--json"])
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert list(data["results"].keys()) == ["workload"]


def test_human_readable_table_output():
    proc = run_cli(["--reviews", "100", "--minutes", "10"])
    assert proc.returncode == 0, proc.stderr
    assert "100 reviews" in proc.stdout
    assert "Review count (classic)" in proc.stdout
    assert "Workload (linear)" in proc.stdout
    assert "Workload (review-weighted)" in proc.stdout

    # With reference
    proc_ref = run_cli([
        "--reviews", "100", "--minutes", "10",
        "--reference-reviews", "200", "--reference-minutes", "40",
    ])
    assert proc_ref.returncode == 0, proc_ref.stderr
    assert "Reference:" in proc_ref.stdout
    assert "bundled_default" in proc_ref.stdout
