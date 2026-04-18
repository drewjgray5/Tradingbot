#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
PROGRESS_PATH = ARTIFACT_DIR / "multi_era_backtest_schwab_only_progress.json"
sys.path.insert(0, str(SKILL_DIR))


ERAS: list[tuple[str, str | None, str]] = [
    ("2024-01-01", None, "recent_current"),
    ("2022-01-01", "2023-12-31", "bear_rates"),
    ("2020-01-01", "2021-12-31", "crash_recovery"),
    ("2018-01-01", "2019-12-31", "volatility_chop"),
    ("2015-01-01", "2017-12-31", "late_bull"),
]


def _chunk(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), max(1, size))]


def _load_universe_tickers() -> list[str]:
    # Avoid network/watchlist refresh stalls by using cached full-universe list first.
    from watchlist_loader import _fallback_watchlist, _load_cached, load_full_watchlist

    cached = _load_cached()
    if cached and cached[0]:
        return [str(t).strip().upper() for t in cached[0] if str(t).strip()]
    try:
        wl = load_full_watchlist(force_refresh=False)
        if wl:
            return [str(t).strip().upper() for t in wl if str(t).strip()]
    except Exception:
        pass
    return _fallback_watchlist()


def _max_drawdown_pct(returns: list[float]) -> float:
    if not returns:
        return 0.0
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for r in returns:
        equity *= (1.0 + float(r))
        if equity > peak:
            peak = equity
        dd = (equity / peak) - 1.0
        if dd < worst:
            worst = dd
    return round(100.0 * worst, 2)


def _aggregate_era(
    *,
    name: str,
    start_date: str,
    end_date: str | None,
    chunk_payloads: list[dict[str, Any]],
    universe_size: int,
) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    excluded_total = 0
    for p in chunk_payloads:
        excluded_total += int(p.get("excluded_count", 0) or 0)
        for t in p.get("trades", []) or []:
            trades.append(
                {
                    "return": float(t.get("return", 0.0) or 0.0),
                    "net_return": float(t.get("net_return", 0.0) or 0.0),
                    "exit_date": str(t.get("exit_date") or ""),
                }
            )
    trades.sort(key=lambda t: t["exit_date"])
    ret_net = [float(t["net_return"]) for t in trades]
    total = len(trades)
    if total == 0:
        return {
            "era": name,
            "start": start_date,
            "end": end_date,
            "profit_factor_net": 0.0,
            "total_return_net_pct": 0.0,
            "max_drawdown_net_pct": 0.0,
            "win_rate_net": 0.0,
            "total_trades": 0,
            "universe_size": universe_size,
            "excluded_count": excluded_total,
        }
    wins_net = sum(1 for r in ret_net if r > 0)
    gp = sum(r for r in ret_net if r > 0)
    gl = abs(sum(r for r in ret_net if r <= 0))
    pf_net = (gp / gl) if gl > 0 else float("inf")
    total_ret_net = 1.0
    for r in ret_net:
        total_ret_net *= (1.0 + r)
    total_ret_net -= 1.0
    return {
        "era": name,
        "start": start_date,
        "end": end_date,
        "profit_factor_net": round(float(pf_net), 3) if pf_net != float("inf") else "inf",
        "total_return_net_pct": round(100.0 * total_ret_net, 2),
        "max_drawdown_net_pct": _max_drawdown_pct(ret_net),
        "win_rate_net": round(100.0 * wins_net / total, 2),
        "total_trades": total,
        "universe_size": universe_size,
        "excluded_count": excluded_total,
    }


def _run_single_chunk(
    *,
    start_date: str,
    end_date: str | None,
    era_name: str,
    tickers_file: Path,
    out_file: Path,
) -> int:
    os.environ["SCHWAB_ONLY_DATA"] = "true"
    os.environ["BACKTEST_SKIP_MIROFISH"] = "true"
    os.environ["SEC_FILING_LLM_SUMMARY_ENABLED"] = "false"
    from backtest import run_backtest

    tickers = json.loads(tickers_file.read_text(encoding="utf-8"))
    metrics = run_backtest(
        tickers=[str(t).strip().upper() for t in tickers if str(t).strip()],
        start_date=start_date,
        end_date=end_date,
        include_all_trades=True,
    )
    trades_in = list(metrics.get("trades") or [])
    compact_trades = [
        {
            "return": float(t.get("return", 0.0) or 0.0),
            "net_return": float(t.get("net_return", 0.0) or 0.0),
            "exit_date": str(t.get("exit_date") or ""),
        }
        for t in trades_in
    ]
    payload = {
        "era": era_name,
        "start": start_date,
        "end": end_date,
        "chunk_size": len(tickers),
        "excluded_count": int(metrics.get("excluded_count", 0) or 0),
        "trades": compact_trades,
    }
    out_file.write_text(json.dumps(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "era": era_name,
                "chunk_size": len(tickers),
                "trades": len(compact_trades),
                "excluded_count": int(metrics.get("excluded_count", 0) or 0),
            },
            separators=(",", ":"),
        )
    )
    return 0


def _run_chunk_subprocess(
    *,
    idx: int,
    chunk_tickers: list[str],
    era_dir: Path,
    start_date: str,
    end_date: str | None,
    era_name: str,
    timeout_seconds: int,
    retry_on_fail: int,
) -> tuple[bool, dict[str, Any] | None, dict[str, Any] | None]:
    chunk_out = era_dir / f"chunk_{idx:04d}.json"
    if chunk_out.exists():
        try:
            return True, json.loads(chunk_out.read_text(encoding="utf-8")), None
        except Exception:
            pass
    tickers_file = era_dir / f"chunk_{idx:04d}_tickers.json"
    tickers_file.write_text(json.dumps(chunk_tickers), encoding="utf-8")
    attempts = 0
    last_error: dict[str, Any] | None = None
    while attempts <= max(0, retry_on_fail):
        attempts += 1
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--single-chunk",
            "--start-date",
            start_date,
            "--era-name",
            era_name,
            "--tickers-file",
            str(tickers_file),
            "--out-file",
            str(chunk_out),
        ]
        if end_date:
            cmd += ["--end-date", end_date]
        env = os.environ.copy()
        env["SCHWAB_ONLY_DATA"] = "true"
        env["BACKTEST_SKIP_MIROFISH"] = "true"
        env["SEC_FILING_LLM_SUMMARY_ENABLED"] = "false"
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(SKILL_DIR),
                capture_output=True,
                text=True,
                timeout=max(120, int(timeout_seconds)),
                env=env,
            )
        except subprocess.TimeoutExpired:
            last_error = {"era": era_name, "chunk": idx, "reason": "timeout", "attempts": attempts}
            continue
        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "").strip()[-500:]
            last_error = {
                "era": era_name,
                "chunk": idx,
                "reason": "nonzero_exit",
                "attempts": attempts,
                "stderr_tail": stderr_tail,
            }
            continue
        if not chunk_out.exists():
            last_error = {
                "era": era_name,
                "chunk": idx,
                "reason": "missing_chunk_output",
                "attempts": attempts,
            }
            continue
        try:
            payload = json.loads(chunk_out.read_text(encoding="utf-8"))
        except Exception as e:
            last_error = {
                "era": era_name,
                "chunk": idx,
                "reason": "invalid_chunk_json",
                "attempts": attempts,
                "error": f"{type(e).__name__}: {e}",
            }
            continue
        return True, payload, None
    return False, None, last_error or {"era": era_name, "chunk": idx, "reason": "unknown_failure"}


def _write_progress(
    run_id: str,
    completed: list[dict[str, Any]],
    current_era: str | None,
    status: str,
    failed: list[dict[str, Any]] | None = None,
    era_state: dict[str, Any] | None = None,
) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "current_era": current_era,
        "completed_count": len(completed),
        "total_eras": len(ERAS),
        "completed": completed,
        "failed": failed or [],
        "era_state": era_state or {},
    }
    PROGRESS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_progress_if_any() -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    if not PROGRESS_PATH.exists():
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), [], []
    try:
        data = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), [], []
    run_id = str(data.get("run_id") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    completed = list(data.get("completed") or [])
    failed = list(data.get("failed") or [])
    return run_id, completed, failed


def _orchestrate(
    timeout_seconds: int,
    retry_on_fail: int,
    resume: bool,
    chunk_size: int,
    max_workers: int,
) -> int:
    run_id, completed, failed = _load_progress_if_any() if resume else (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        [],
        [],
    )
    completed_by_era = {str(r.get("era")) for r in completed}
    failed = []
    era_state: dict[str, Any] = {}
    _write_progress(run_id, completed, None, "running", failed, era_state)

    for start_date, end_date, name in ERAS:
        if name in completed_by_era:
            print(f"[multi-era] skipping completed era {name}")
            continue
        watchlist = _load_universe_tickers()
        chunks = _chunk(watchlist, chunk_size)
        era_dir = ARTIFACT_DIR / "multi_era_chunks" / run_id / name
        era_dir.mkdir(parents=True, exist_ok=True)
        chunk_payloads_by_idx: dict[int, dict[str, Any]] = {}
        era_state[name] = {"total_chunks": len(chunks), "completed_chunks": 0}
        print(
            f"[multi-era] starting {name} ({start_date} -> {end_date or 'present'}), "
            f"chunks={len(chunks)} size={chunk_size} workers={max(1, int(max_workers))}"
        )
        era_failed = False
        pending: list[tuple[int, list[str]]] = []
        for idx, chunk_tickers in enumerate(chunks, start=1):
            chunk_out = era_dir / f"chunk_{idx:04d}.json"
            if chunk_out.exists():
                try:
                    chunk_payloads_by_idx[idx] = json.loads(chunk_out.read_text(encoding="utf-8"))
                    continue
                except Exception:
                    pass
            pending.append((idx, chunk_tickers))

        if chunk_payloads_by_idx:
            era_state[name]["completed_chunks"] = len(chunk_payloads_by_idx)
            _write_progress(run_id, completed, name, "running", failed, era_state)

        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
            fut_to_idx = {
                pool.submit(
                    _run_chunk_subprocess,
                    idx=idx,
                    chunk_tickers=chunk_tickers,
                    era_dir=era_dir,
                    start_date=start_date,
                    end_date=end_date,
                    era_name=name,
                    timeout_seconds=timeout_seconds,
                    retry_on_fail=retry_on_fail,
                ): idx
                for idx, chunk_tickers in pending
            }
            for fut in as_completed(fut_to_idx):
                idx = fut_to_idx[fut]
                try:
                    ok, payload, err = fut.result()
                except Exception as e:
                    ok, payload, err = False, None, {"era": name, "chunk": idx, "reason": f"future_exception:{type(e).__name__}"}
                if not ok or payload is None:
                    era_failed = True
                    failed.append(err or {"era": name, "chunk": idx, "reason": "unknown_failure"})
                    print(f"[multi-era] failed {name} chunk={idx}: {(err or {}).get('reason')}")
                    continue
                chunk_payloads_by_idx[idx] = payload
                era_state[name]["completed_chunks"] = len(chunk_payloads_by_idx)
                _write_progress(run_id, completed, name, "running", failed, era_state)
                print(f"[multi-era] progress {name}: chunk {era_state[name]['completed_chunks']}/{len(chunks)}")

        if era_failed:
            print(f"[multi-era] era failed after chunk retries: {name}")
            continue

        chunk_payloads = [chunk_payloads_by_idx[i] for i in sorted(chunk_payloads_by_idx.keys())]
        row = _aggregate_era(
            name=name,
            start_date=start_date,
            end_date=end_date,
            chunk_payloads=chunk_payloads,
            universe_size=len(watchlist),
        )
        completed.append(row)
        completed_by_era.add(name)
        _write_progress(run_id, completed, None, "running", failed, era_state)
        print(f"[multi-era] completed {name}: trades={row.get('total_trades')} oos_pf={row.get('profit_factor_net')}")

    out = ARTIFACT_DIR / f"multi_era_backtest_schwab_only_{run_id}.json"
    payload = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schwab_only": True,
        "results": completed,
        "failed_eras": failed,
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    status = "completed" if not failed else "completed_with_failures"
    _write_progress(run_id, completed, None, status, failed, era_state)
    print(f"Multi-era backtest artifact: {out}")
    return 0 if not failed else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Run strict Schwab-only multi-era backtests with progress checkpoints.")
    parser.add_argument("--single-era", action="store_true", help="Internal mode: run one era and emit compact JSON.")
    parser.add_argument("--single-chunk", action="store_true", help="Internal mode: run one ticker chunk and write JSON.")
    parser.add_argument("--start-date", default="", help="Era start date YYYY-MM-DD (single-era mode).")
    parser.add_argument("--end-date", default="", help="Era end date YYYY-MM-DD (single-era mode).")
    parser.add_argument("--era-name", default="", help="Era label (single-era mode).")
    parser.add_argument("--timeout-seconds", type=int, default=7200, help="Per-era timeout for orchestrated runs.")
    parser.add_argument("--retry-on-fail", type=int, default=1, help="Retry count per era after timeout/failure.")
    parser.add_argument("--no-resume", action="store_true", help="Ignore existing progress and start fresh run_id.")
    parser.add_argument("--chunk-size", type=int, default=120, help="Ticker chunk size for each era.")
    parser.add_argument("--max-workers", type=int, default=4, help="Parallel chunk worker subprocesses per era.")
    parser.add_argument("--tickers-file", default="", help="JSON file with chunk tickers (single-chunk mode).")
    parser.add_argument("--out-file", default="", help="Output JSON file path (single-chunk mode).")
    args = parser.parse_args()

    if args.single_chunk:
        if not args.start_date or not args.era_name or not args.tickers_file or not args.out_file:
            raise SystemExit("--single-chunk requires --start-date, --era-name, --tickers-file, --out-file")
        end_date = args.end_date or None
        return _run_single_chunk(
            start_date=args.start_date,
            end_date=end_date,
            era_name=args.era_name,
            tickers_file=Path(args.tickers_file),
            out_file=Path(args.out_file),
        )
    return _orchestrate(
        timeout_seconds=args.timeout_seconds,
        retry_on_fail=args.retry_on_fail,
        resume=not args.no_resume,
        chunk_size=args.chunk_size,
        max_workers=args.max_workers,
    )


if __name__ == "__main__":
    raise SystemExit(main())
