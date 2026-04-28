from __future__ import annotationsimport jsonimport sysfrom collections import defaultdictfrom pathlib import Pathfrom statistics import meanfrom typing import AnySKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from webapp.db import SessionLocalfrom webapp.models import BacktestRunVALIDATION_DIR = SKILL_DIR / "validation_artifacts"
OUTPUT_PATH = SKILL_DIR / "guardrail_analysis_summary.json"


def _bucket_score(score: Any) -> str:
    try:
        x = float(score)
    except Exception:
        return "unknown"
    if x < 50:
        return "<50"
    if x < 60:
        return "50-59.99"
    if x < 70:
        return "60-69.99"
    return "70+"


def _bucket_vcp(vcp: Any) -> str:
    try:
        x = float(vcp)
    except Exception:
        return "unknown"
    if x < 0.7:
        return "<0.70"
    if x < 0.8:
        return "0.70-0.79"
    if x < 0.9:
        return "0.80-0.89"
    return "0.90+"


def _aggregate_bucket(rows: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        pct = float(row.get("net_return_pct") or 0.0)
        grouped[key_fn(row)].append(pct / 100.0)
    out: list[dict[str, Any]] = []
    for bucket, vals in grouped.items():
        n = len(vals)
        wins = sum(1 for v in vals if v > 0)
        out.append(
            {
                "bucket": bucket,
                "count": n,
                "win_rate_pct": round((wins / n) * 100.0, 2) if n else 0.0,
                "avg_net_return_pct": round(mean(vals) * 100.0, 3) if n else 0.0,
            }
        )
    out.sort(key=lambda x: x["avg_net_return_pct"], reverse=True)
    return out


def _load_all_trades_payloads() -> list[dict[str, Any]]:
    if not VALIDATION_DIR.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(VALIDATION_DIR.glob("all_trades_*_analysis.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def _summarize_backtest_runs() -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = db.query(BacktestRun).filter(BacktestRun.status == "success").all()
    finally:
        db.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = row.result_json
        result = json.loads(payload) if isinstance(payload, str) else (payload or {})
        out.append(
            {
                "run_id": row.id,
                "start_date": result.get("start_date"),
                "end_date": result.get("end_date"),
                "universe_size": result.get("universe_size"),
                "total_trades": result.get("total_trades"),
                "total_return_net_pct": result.get("total_return_net_pct"),
                "profit_factor_net": result.get("profit_factor_net"),
                "max_drawdown_net_pct": result.get("max_drawdown_net_pct"),
                "portfolio_summary": result.get("portfolio_summary"),
            }
        )
    return out


def main() -> None:
    runs = _summarize_backtest_runs()
    all_trades_analysis = _load_all_trades_payloads()
    merged_trades: list[dict[str, Any]] = []
    for a in all_trades_analysis:
        merged_trades.extend(a.get("top_decile_signals") or [])

    score_stats = _aggregate_bucket(merged_trades, lambda r: _bucket_score(r.get("signal_score")))
    vcp_stats = _aggregate_bucket(
        merged_trades,
        lambda r: _bucket_vcp(((r.get("telemetry") or {}).get("vcp_volume_ratio"))),
    )
    output = {
        "run_count": len(runs),
        "runs": runs,
        "all_trades_analysis_count": len(all_trades_analysis),
        "top_decile_signal_score_stats": score_stats,
        "top_decile_vcp_ratio_stats": vcp_stats,
        "recommended_policy_path": "backtest_guardrail_policy.json",
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"WROTE {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
