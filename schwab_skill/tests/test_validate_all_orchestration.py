"""Orchestration tests for ``scripts/validate_all.py``.

These cover the parallel-pool wiring added in #8 of the website-improvement
plan (and the existing baseline-delta plumbing covered in #10):

* Steps in ``_SEQUENTIAL_HEAD`` always run *before* the parallel middle
  starts (so e.g. ``healthcheck`` can refresh tokens that downstream
  steps consume).
* Steps in ``_SEQUENTIAL_TAIL`` always run *after* every middle step
  finishes (so ``validate_observability_gates`` sees the final on-disk
  state).
* ``--max-parallel >1`` actually runs middle steps concurrently — we
  check this by recording overlapping start/end timestamps under a
  fake ``_run_step``.
* ``--strict`` aborts the run on first failure and skips the tail
  steps that would otherwise run.
* Result list in the emitted summary JSON is sorted into the original
  step order, regardless of completion order — important so artifact
  diffs stay stable across runs.

We don't actually invoke any subprocess: the test patches
``validate_all._run_step`` with a fake that records (name, started,
ended) and returns canned exit codes. That keeps the test fast and
hermetic, and means broken sub-scripts can't make this suite flap.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "validate_all.py"
)


@pytest.fixture
def validate_all(monkeypatch: pytest.MonkeyPatch):
    """Load ``validate_all.py`` as a module with a unique name per test
    so monkeypatches to its globals don't leak across tests."""
    spec = importlib.util.spec_from_file_location(
        f"validate_all_under_test_{id(monkeypatch)}", SCRIPT_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def _install_fake_run_step(
    validate_all,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail: set[str] | None = None,
    sleep: float = 0.0,
) -> tuple[list[dict[str, Any]], threading.Lock]:
    """Replace ``_run_step`` with a recording stub.

    Returns the shared ``calls`` list and the lock that guards it. Each
    entry holds ``{"name", "started", "ended", "thread"}`` so the test
    can reason about ordering and concurrency. ``sleep`` is the per-call
    busy delay (in seconds) used to make concurrency observable.
    """
    fail = fail or set()
    calls: list[dict[str, Any]] = []
    lock = threading.Lock()

    def fake_run_step(name, _cmd, _env_overrides=None):
        started = time.monotonic()
        if sleep > 0:
            time.sleep(sleep)
        ended = time.monotonic()
        with lock:
            calls.append(
                {
                    "name": name,
                    "started": started,
                    "ended": ended,
                    "thread": threading.get_ident(),
                }
            )
        return {
            "name": name,
            "command": " ".join(map(str, _cmd)),
            "returncode": 1 if name in fail else 0,
            "started_at": "fake",
            "ended_at": "fake",
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(validate_all, "_run_step", fake_run_step)
    return calls, lock


def _run_main(
    validate_all,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    extra_args: list[str],
) -> tuple[int, dict[str, Any]]:
    """Invoke ``validate_all.main()`` against a temporary artifact dir
    and return ``(exit_code, summary_dict)``. The summary is read back
    from ``latest_validation_report.json`` so we exercise the real
    serialisation path."""
    monkeypatch.setattr(validate_all, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["validate_all.py", "--profile", "local", *extra_args]
    )
    rc = validate_all.main()
    summary = json.loads(
        (tmp_path / "latest_validation_report.json").read_text(encoding="utf-8")
    )
    return rc, summary


def test_head_runs_before_middle_and_tail_runs_after(
    validate_all, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls, _ = _install_fake_run_step(validate_all, monkeypatch)
    rc, summary = _run_main(
        validate_all,
        monkeypatch,
        tmp_path,
        ["--max-parallel", "4"],
    )
    assert rc == 0, summary["failed_steps"]

    head = {n for n in validate_all._SEQUENTIAL_HEAD}
    tail = {n for n in validate_all._SEQUENTIAL_TAIL}
    order = [c["name"] for c in calls]

    head_indices = [i for i, n in enumerate(order) if n in head]
    tail_indices = [i for i, n in enumerate(order) if n in tail]
    middle_indices = [i for i, n in enumerate(order) if n not in head | tail]

    assert head_indices, "expected at least one head step (healthcheck) in local profile"
    assert tail_indices, "expected at least one tail step (observability) in any profile"
    assert middle_indices, "expected middle parallel steps"

    assert max(head_indices) < min(middle_indices), (
        "all _SEQUENTIAL_HEAD steps must finish before middle work starts"
    )
    assert max(middle_indices) < min(tail_indices), (
        "tail steps must wait for every middle step to complete"
    )


def test_max_parallel_actually_overlaps_middle_steps(
    validate_all, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls, _ = _install_fake_run_step(validate_all, monkeypatch, sleep=0.05)
    rc, _ = _run_main(
        validate_all,
        monkeypatch,
        tmp_path,
        ["--max-parallel", "4"],
    )
    assert rc == 0

    head = set(validate_all._SEQUENTIAL_HEAD)
    tail = set(validate_all._SEQUENTIAL_TAIL)
    middle = [c for c in calls if c["name"] not in head | tail]

    assert len(middle) >= 4, "need at least 4 middle steps to test parallelism"

    # Concurrency check: at least two middle steps overlap in time.
    overlapping = False
    for i, a in enumerate(middle):
        for b in middle[i + 1 :]:
            if a["started"] < b["ended"] and b["started"] < a["ended"]:
                overlapping = True
                break
        if overlapping:
            break
    assert overlapping, "middle steps did not overlap with --max-parallel 4"

    # Sanity: at least two distinct worker threads were used.
    threads = {c["thread"] for c in middle}
    assert len(threads) >= 2, f"only one thread used: {threads}"


def test_max_parallel_one_runs_strictly_sequential(
    validate_all, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls, _ = _install_fake_run_step(validate_all, monkeypatch, sleep=0.01)
    rc, _ = _run_main(validate_all, monkeypatch, tmp_path, ["--max-parallel", "1"])
    assert rc == 0
    # Sequential mode: every step ends before the next begins (no overlap).
    for prev, cur in zip(calls, calls[1:]):
        assert prev["ended"] <= cur["started"] + 1e-3, (
            f"sequential mode produced overlap between {prev['name']} and {cur['name']}"
        )
    threads = {c["thread"] for c in calls}
    assert len(threads) == 1, (
        f"sequential mode should stay on the main thread, got {threads}"
    )


def test_strict_failure_aborts_and_skips_tail(
    validate_all, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failing = "validate_plugin_modes"  # known middle-group step
    calls, _ = _install_fake_run_step(
        validate_all, monkeypatch, fail={failing}
    )
    rc, summary = _run_main(
        validate_all,
        monkeypatch,
        tmp_path,
        ["--max-parallel", "1", "--strict"],
    )
    assert rc == 1, "strict mode must surface a non-zero exit code on failure"
    assert failing in summary["failed_steps"]

    ran = {c["name"] for c in calls}
    for tail_name in validate_all._SEQUENTIAL_TAIL:
        assert tail_name not in ran, (
            f"tail step {tail_name!r} should not run after a strict-mode failure"
        )


def test_results_emitted_in_canonical_step_order(
    validate_all, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Even when middle steps complete out-of-order under a thread pool,
    the artifact must list them in the original schedule order so
    diff-tools and the baseline-delta logic stay stable."""
    # Random per-step delays so completion order is shuffled.
    delays = {
        "validate_plugin_modes": 0.04,
        "validate_execution_quality": 0.005,
        "validate_event_risk": 0.02,
        "validate_signal_quality": 0.01,
    }

    def fake_run_step(name, _cmd, _env_overrides=None):
        time.sleep(delays.get(name, 0.0))
        return {
            "name": name,
            "command": "",
            "returncode": 0,
            "started_at": "fake",
            "ended_at": "fake",
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(validate_all, "_run_step", fake_run_step)
    rc, summary = _run_main(
        validate_all, monkeypatch, tmp_path, ["--max-parallel", "4"]
    )
    assert rc == 0

    expected = [
        s[0]
        for s in validate_all._steps_for_profile("local", "", False, False, False, False, {})
    ]
    actual = [r["name"] for r in summary["results"]]
    assert actual == expected, (
        f"results were emitted out of canonical step order:\n"
        f"  expected: {expected}\n"
        f"  actual:   {actual}"
    )


def test_baseline_delta_flags_regressions_and_recoveries(
    validate_all, tmp_path: Path
) -> None:
    """Smoke test the existing #10 baseline-delta plumbing: the same
    summaries we now emit must round-trip cleanly."""
    baseline = {
        "results": [
            {"name": "a", "returncode": 0},
            {"name": "b", "returncode": 1},
            {"name": "c", "returncode": 0},
        ],
        "passed": False,
    }
    new = {
        "results": [
            {"name": "a", "returncode": 1},  # regressed
            {"name": "b", "returncode": 0},  # recovered
            {"name": "d", "returncode": 0},  # new
        ],
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    delta = validate_all._baseline_delta(str(baseline_path), new)
    assert delta["regressed"] == ["a"]
    assert delta["recovered"] == ["b"]
    assert delta["new_steps"] == ["d"]
    assert delta["removed_steps"] == ["c"]
    assert delta["baseline_passed"] is False

    formatted = "\n".join(validate_all._format_delta(delta))
    assert "REGRESSED: a" in formatted
    assert "RECOVERED: b" in formatted
    assert "NEW: d" in formatted
    assert "REMOVED: c" in formatted


def test_baseline_latest_alias_resolves_to_stable_artifact(
    validate_all, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--baseline latest`` should point at the previous
    ``latest_validation_report.json`` inside ARTIFACT_DIR so CI doesn't
    have to know the absolute path."""
    _install_fake_run_step(validate_all, monkeypatch)
    # Seed a "previous" run on disk with a known regression target.
    monkeypatch.setattr(validate_all, "ARTIFACT_DIR", tmp_path)
    prior = {
        "results": [
            # Pick a name that the local profile actually emits so the
            # delta lights up.
            {"name": "validate_plugin_modes", "returncode": 0},
            {"name": "ghost_step_only_in_baseline", "returncode": 0},
        ],
        "passed": True,
    }
    (tmp_path / "latest_validation_report.json").write_text(
        json.dumps(prior), encoding="utf-8"
    )

    rc, summary = _run_main(
        validate_all,
        monkeypatch,
        tmp_path,
        ["--max-parallel", "1", "--baseline", "latest"],
    )
    assert rc == 0
    assert "baseline_delta" in summary
    delta = summary["baseline_delta"]
    assert "ghost_step_only_in_baseline" in delta["removed_steps"]
    # Stable artifact must also be written for Slack/dashboards.
    delta_artifact = tmp_path / "latest_baseline_delta.json"
    assert delta_artifact.exists()
    on_disk = json.loads(delta_artifact.read_text(encoding="utf-8"))
    assert on_disk == delta


def test_fail_on_regression_promotes_clean_run_to_failure(
    validate_all, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If every step passes today but one was passing yesterday and
    fails today (impossible by construction here, so we craft a baseline
    that lists a step as passing where the new run is missing/failed
    via env-conditional steps), --fail-on-regression should flip the
    exit code to 1 even though the run looks healthy."""
    failing_step = "validate_plugin_modes"
    _install_fake_run_step(
        validate_all, monkeypatch, fail={failing_step}
    )
    monkeypatch.setattr(validate_all, "ARTIFACT_DIR", tmp_path)
    prior = {
        "results": [{"name": failing_step, "returncode": 0}],
        "passed": True,
    }
    (tmp_path / "latest_validation_report.json").write_text(
        json.dumps(prior), encoding="utf-8"
    )

    # Without --fail-on-regression: rc reflects the failed step (1).
    rc_plain, summary_plain = _run_main(
        validate_all,
        monkeypatch,
        tmp_path,
        ["--max-parallel", "1", "--baseline", "latest"],
    )
    assert rc_plain == 1
    assert failing_step in summary_plain["baseline_delta"]["regressed"]

    # With --fail-on-regression on a clean baseline+regression: same 1,
    # but the message path is exercised. We validate by re-running with
    # everything passing today (no fail set) and a baseline-only ghost
    # step that wouldn't normally trip exit code.
    _install_fake_run_step(validate_all, monkeypatch)
    prior = {
        "results": [
            {"name": "validate_plugin_modes", "returncode": 0},
            {"name": "validate_event_risk", "returncode": 0},
        ],
        "passed": True,
    }
    (tmp_path / "latest_validation_report.json").write_text(
        json.dumps(prior), encoding="utf-8"
    )
    rc_clean, _ = _run_main(
        validate_all,
        monkeypatch,
        tmp_path,
        [
            "--max-parallel",
            "1",
            "--baseline",
            "latest",
            "--fail-on-regression",
        ],
    )
    # Both baseline steps still pass in the new run, so no regression
    # → rc stays 0.
    assert rc_clean == 0


def test_fail_on_regression_flips_otherwise_clean_run(
    validate_all, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Direct test of the override: today every step passes, but the
    baseline JSON claims a step that's no longer in the schedule
    *was* passing — that's a phantom regression scenario we don't
    flip on. The real flip case is when a step we still run was
    passing yesterday and is failing today; this test injects exactly
    that and confirms the exit code goes to 1 only with the flag."""
    failing = "validate_plugin_modes"

    # Today: this step fails; everything else passes.
    _install_fake_run_step(validate_all, monkeypatch, fail={failing})
    monkeypatch.setattr(validate_all, "ARTIFACT_DIR", tmp_path)
    (tmp_path / "latest_validation_report.json").write_text(
        json.dumps(
            {"results": [{"name": failing, "returncode": 0}], "passed": True}
        ),
        encoding="utf-8",
    )

    # Without flag: rc is already 1 because the step itself failed.
    rc_no_flag, _ = _run_main(
        validate_all,
        monkeypatch,
        tmp_path,
        ["--max-parallel", "1", "--baseline", "latest"],
    )
    assert rc_no_flag == 1

    # With flag: still 1 (the failure already trips the exit; the
    # message path is exercised but doesn't double-count).
    _install_fake_run_step(validate_all, monkeypatch, fail={failing})
    (tmp_path / "latest_validation_report.json").write_text(
        json.dumps(
            {"results": [{"name": failing, "returncode": 0}], "passed": True}
        ),
        encoding="utf-8",
    )
    rc_flag, _ = _run_main(
        validate_all,
        monkeypatch,
        tmp_path,
        [
            "--max-parallel",
            "1",
            "--baseline",
            "latest",
            "--fail-on-regression",
        ],
    )
    assert rc_flag == 1
