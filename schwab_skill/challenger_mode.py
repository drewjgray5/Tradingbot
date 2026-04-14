"""
Challenger Mode: runs a parallel scan with auto-suggested parameter
overrides from strategy_update.json and compares results against the
champion (current production) configuration.

Results are persisted to .challenger_results.json for the Performance
tab to display a Champion vs Challenger comparison.

Usage:
    python challenger_mode.py                    # run challenger scan
    python challenger_mode.py --compare-only     # show latest comparison
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent

STRATEGY_UPDATE_FILE = "strategy_update.json"
CHALLENGER_RESULTS_FILE = ".challenger_results.json"
MAX_HISTORY = 60


class ChallengerRunner:
    """
    Runs a parallel scan using auto-suggested parameter overrides from
    the LearningEngine and compares against the champion's latest results.
    """

    def __init__(
        self,
        skill_dir: Path | str | None = None,
        strategy_update_data: dict[str, Any] | None = None,
        history_loader: Any | None = None,
        history_saver: Any | None = None,
    ):
        self.skill_dir = Path(skill_dir or SKILL_DIR)
        self._history_path = self.skill_dir / CHALLENGER_RESULTS_FILE
        self._strategy_update_data = strategy_update_data
        self._history_loader = history_loader
        self._history_saver = history_saver

    def load_strategy_update(self) -> dict[str, Any] | None:
        if isinstance(self._strategy_update_data, dict) and self._strategy_update_data.get("env_overrides"):
            return self._strategy_update_data
        path = self.skill_dir / STRATEGY_UPDATE_FILE
        if not path.exists():
            LOG.warning("No strategy_update.json found at %s", path)
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("env_overrides"):
                return data
        except Exception as e:
            LOG.error("Failed to load strategy update: %s", e)
        return None

    def run_champion_scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Run scan with current (champion) configuration."""
        from signal_scanner import scan_for_signals_detailed
        return scan_for_signals_detailed(skill_dir=self.skill_dir)

    def run_challenger_scan(
        self, env_overrides: dict[str, str],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Run scan with challenger configuration (env overrides)."""
        from signal_scanner import scan_for_signals_detailed
        return scan_for_signals_detailed(
            skill_dir=self.skill_dir,
            env_overrides=env_overrides,
        )

    def compare(
        self,
        champion_signals: list[dict[str, Any]],
        champion_diagnostics: dict[str, Any],
        challenger_signals: list[dict[str, Any]],
        challenger_diagnostics: dict[str, Any],
        env_overrides: dict[str, str],
    ) -> dict[str, Any]:
        """Build comparison summary between champion and challenger."""
        def _signal_summary(signals: list[dict[str, Any]]) -> dict[str, Any]:
            if not signals:
                return {
                    "count": 0,
                    "avg_score": 0.0,
                    "avg_conviction": 0.0,
                    "tickers": [],
                    "top_ticker": None,
                    "top_score": 0.0,
                }
            scores = [float(s.get("signal_score", 0) or 0) for s in signals]
            convictions = [float(s.get("mirofish_conviction", 0) or 0) for s in signals]
            tickers = [str(s.get("ticker", "")) for s in signals]
            sorted_signals = sorted(signals, key=lambda s: float(s.get("signal_score", 0) or 0), reverse=True)
            return {
                "count": len(signals),
                "avg_score": round(sum(scores) / len(scores), 2),
                "avg_conviction": round(sum(convictions) / len(convictions), 2),
                "tickers": tickers,
                "top_ticker": sorted_signals[0].get("ticker") if sorted_signals else None,
                "top_score": round(float(sorted_signals[0].get("signal_score", 0) or 0), 2) if sorted_signals else 0.0,
            }

        champ_tickers = {str(s.get("ticker", "")).upper() for s in champion_signals}
        chall_tickers = {str(s.get("ticker", "")).upper() for s in challenger_signals}
        overlap = champ_tickers & chall_tickers
        champ_only = champ_tickers - chall_tickers
        chall_only = chall_tickers - champ_tickers

        champ_summary = _signal_summary(champion_signals)
        chall_summary = _signal_summary(challenger_signals)

        score_delta = chall_summary["avg_score"] - champ_summary["avg_score"]
        verdict = "tie"
        if score_delta > 3.0:
            verdict = "challenger_better"
        elif score_delta < -3.0:
            verdict = "champion_better"

        return {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "verdict": verdict,
            "score_delta": round(score_delta, 2),
            "champion": champ_summary,
            "challenger": chall_summary,
            "overlap_tickers": sorted(overlap),
            "champion_only_tickers": sorted(champ_only),
            "challenger_only_tickers": sorted(chall_only),
            "env_overrides": env_overrides,
            "champion_diagnostics_summary": {
                "watchlist_size": champion_diagnostics.get("watchlist_size"),
                "stage2_fail": champion_diagnostics.get("stage2_fail"),
                "vcp_fail": champion_diagnostics.get("vcp_fail"),
                "quality_gates_filtered": champion_diagnostics.get("quality_gates_filtered"),
                "exceptions": champion_diagnostics.get("exceptions"),
            },
            "challenger_diagnostics_summary": {
                "watchlist_size": challenger_diagnostics.get("watchlist_size"),
                "stage2_fail": challenger_diagnostics.get("stage2_fail"),
                "vcp_fail": challenger_diagnostics.get("vcp_fail"),
                "quality_gates_filtered": challenger_diagnostics.get("quality_gates_filtered"),
                "exceptions": challenger_diagnostics.get("exceptions"),
            },
        }

    def save_result(self, comparison: dict[str, Any]) -> None:
        if callable(self._history_saver):
            self._history_saver(comparison)
            return
        history = self._load_history()
        history.append(comparison)
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        self._history_path.write_text(json.dumps(history, indent=2, default=str), encoding="utf-8")

    def _load_history(self) -> list[dict[str, Any]]:
        if callable(self._history_loader):
            loaded = self._history_loader()
            return loaded if isinstance(loaded, list) else []
        if not self._history_path.exists():
            return []
        try:
            data = json.loads(self._history_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def get_latest_comparison(self) -> dict[str, Any] | None:
        history = self._load_history()
        return history[-1] if history else None

    def get_comparison_history(self, n: int = 10) -> list[dict[str, Any]]:
        history = self._load_history()
        return history[-n:]

    def get_win_rate_summary(self) -> dict[str, Any]:
        """Aggregate challenger vs champion win rate over all runs."""
        history = self._load_history()
        if not history:
            return {"total_runs": 0}
        verdicts = [h.get("verdict") for h in history]
        return {
            "total_runs": len(history),
            "challenger_wins": verdicts.count("challenger_better"),
            "champion_wins": verdicts.count("champion_better"),
            "ties": verdicts.count("tie"),
            "challenger_win_rate_pct": round(
                (verdicts.count("challenger_better") / len(verdicts)) * 100, 1
            ),
            "avg_score_delta": round(
                sum(float(h.get("score_delta", 0) or 0) for h in history) / len(history), 2
            ),
        }

    def run(self) -> dict[str, Any]:
        """Full pipeline: load update -> run both scans -> compare -> save."""
        update = self.load_strategy_update()
        if update is None:
            return {
                "status": "no_update",
                "message": "No strategy_update.json found. Run evolve_logic.py first.",
            }

        env_overrides = update.get("env_overrides", {})
        if not env_overrides:
            return {
                "status": "no_overrides",
                "message": "strategy_update.json has no env_overrides.",
            }

        LOG.info("Running champion scan (current config)...")
        champ_signals, champ_diag = self.run_champion_scan()

        LOG.info("Running challenger scan with overrides: %s", env_overrides)
        chall_signals, chall_diag = self.run_challenger_scan(env_overrides)

        comparison = self.compare(
            champ_signals, champ_diag,
            chall_signals, chall_diag,
            env_overrides,
        )
        self.save_result(comparison)

        return {
            "status": "ok",
            "comparison": comparison,
            "win_rate": self.get_win_rate_summary(),
        }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    compare_only = "--compare-only" in sys.argv

    runner = ChallengerRunner(skill_dir=SKILL_DIR)

    if compare_only:
        latest = runner.get_latest_comparison()
        if latest:
            print(json.dumps(latest, indent=2, default=str))
            print(f"\nVerdict: {latest.get('verdict')}")
            print(f"Score delta: {latest.get('score_delta'):+.2f}")
        else:
            print("No challenger results found. Run without --compare-only first.")
        summary = runner.get_win_rate_summary()
        if summary.get("total_runs", 0) > 0:
            print(f"\nOverall: {summary}")
        return

    result = runner.run()
    print(json.dumps(result, indent=2, default=str))

    if result.get("status") == "ok":
        comp = result["comparison"]
        print(f"\n{'='*60}")
        print(f"  Champion:   {comp['champion']['count']} signals, avg score {comp['champion']['avg_score']}")
        print(f"  Challenger: {comp['challenger']['count']} signals, avg score {comp['challenger']['avg_score']}")
        print(f"  Verdict:    {comp['verdict']} (delta: {comp['score_delta']:+.2f})")
        print(f"  Overlap:    {comp['overlap_tickers']}")
        print(f"  Champ only: {comp['champion_only_tickers']}")
        print(f"  Chall only: {comp['challenger_only_tickers']}")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
