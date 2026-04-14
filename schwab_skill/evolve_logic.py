"""
Post-mortem analysis engine: correlates realized P/L with scanner features
to identify which parameters predict success vs failure in the current
market regime, and generates automatic threshold adjustments.

Workflow:
1. Pull realized P/L from .trade_outcomes.json
2. Match outcomes to features logged in the feature_store
3. Train a Random Forest to identify feature importance
4. If a parameter is a high predictor of failure, generate strategy_update.json
   with adjusted thresholds

Usage:
    python evolve_logic.py                    # analyze + generate update
    python evolve_logic.py --apply            # analyze + apply to .env
    python evolve_logic.py --dry-run          # analyze only, no file writes
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent

TRADE_OUTCOMES_FILE = ".trade_outcomes.json"
STRATEGY_UPDATE_FILE = "strategy_update.json"

TUNABLE_FEATURE_MAP: dict[str, dict[str, Any]] = {
    "volume_ratio": {
        "env_key": "QUALITY_BREAKOUT_VOLUME_MIN_RATIO",
        "direction": "higher_is_better",
        "current_default": 0.90,
        "adjust_step": 0.05,
        "min_bound": 0.50,
        "max_bound": 2.00,
    },
    "signal_score": {
        "env_key": "QUALITY_MIN_SIGNAL_SCORE",
        "direction": "higher_is_better",
        "current_default": 50,
        "adjust_step": 5,
        "min_bound": 20,
        "max_bound": 80,
    },
    "forensic_sloan": {
        "env_key": "FORENSIC_SLOAN_MAX",
        "direction": "lower_is_better",
        "current_default": 0.10,
        "adjust_step": 0.02,
        "min_bound": 0.02,
        "max_bound": 0.25,
    },
    "forensic_beneish": {
        "env_key": "FORENSIC_BENEISH_MAX",
        "direction": "lower_is_better",
        "current_default": -1.78,
        "adjust_step": 0.10,
        "min_bound": -2.50,
        "max_bound": -1.00,
    },
    "advisory_prob": {
        "env_key": "ADVISORY_CONFIDENCE_LOW",
        "direction": "higher_is_better",
        "current_default": 0.52,
        "adjust_step": 0.02,
        "min_bound": 0.40,
        "max_bound": 0.70,
    },
}


class LearningEngine:
    """
    Post-mortem analysis engine.

    Attributes:
        skill_dir: Path to schwab_skill directory
        feature_df: Feature store DataFrame
        outcomes_df: Trade outcomes DataFrame
        merged_df: Joined features + outcomes for ML
        model: Trained RandomForestRegressor
        feature_importance: Sorted (feature, importance) pairs
        strategy_updates: Generated threshold adjustments
    """

    def __init__(
        self,
        skill_dir: Path | str | None = None,
        lookback_days: int = 90,
        outcomes_records: list[dict[str, Any]] | None = None,
        write_strategy_file: bool = True,
    ):
        self.skill_dir = Path(skill_dir or SKILL_DIR)
        self.lookback_days = lookback_days
        self._outcomes_records = outcomes_records
        self.write_strategy_file = bool(write_strategy_file)
        self.feature_df: pd.DataFrame = pd.DataFrame()
        self.outcomes_df: pd.DataFrame = pd.DataFrame()
        self.merged_df: pd.DataFrame = pd.DataFrame()
        self.model: Any = None
        self.feature_importance: list[tuple[str, float]] = []
        self.strategy_updates: list[dict[str, Any]] = []
        self.strategy_update_payload: dict[str, Any] | None = None

    def load_outcomes(self) -> pd.DataFrame:
        try:
            if self._outcomes_records is not None:
                data: Any = self._outcomes_records
            else:
                path = self.skill_dir / TRADE_OUTCOMES_FILE
                if not path.exists():
                    LOG.warning("No trade outcomes file found at %s", path)
                    return pd.DataFrame()
                data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list) or not data:
                return pd.DataFrame()
            df = pd.DataFrame(data)
            if "ticker" not in df.columns:
                return pd.DataFrame()
            if "return_pct" not in df.columns and "pnl_pct" in df.columns:
                df["return_pct"] = df["pnl_pct"]
            self.outcomes_df = df
            return df
        except Exception as e:
            LOG.error("Failed to load trade outcomes: %s", e)
            return pd.DataFrame()

    def load_features(self) -> pd.DataFrame:
        try:
            from feature_store import get_feature_dataframe
            df = get_feature_dataframe(days=self.lookback_days, skill_dir=self.skill_dir)
            self.feature_df = df
            return df
        except Exception as e:
            LOG.error("Failed to load feature store: %s", e)
            return pd.DataFrame()

    def merge_data(self) -> pd.DataFrame:
        """Join feature records to trade outcomes with strict temporal alignment.

        Only features recorded BEFORE the outcome entry date are used,
        preventing future data leakage. Each outcome is matched to the
        most recent feature snapshot for that ticker preceding the trade.
        """
        if self.outcomes_df.empty or self.feature_df.empty:
            LOG.warning("Cannot merge: outcomes=%d, features=%d", len(self.outcomes_df), len(self.feature_df))
            return pd.DataFrame()

        outcomes = self.outcomes_df.copy()
        features = self.feature_df.copy()

        outcomes["ticker"] = outcomes["ticker"].str.upper().str.strip()
        features["ticker"] = features["ticker"].str.upper().str.strip()

        features_passed = features[features["decision"] == "pass"].copy()
        if features_passed.empty:
            LOG.warning("No passing signals in feature store to match against outcomes")
            return pd.DataFrame()

        has_temporal = "scan_ts" in features_passed.columns and (
            "entry_date" in outcomes.columns or "date" in outcomes.columns
        )

        if has_temporal:
            outcome_date_col = "entry_date" if "entry_date" in outcomes.columns else "date"
            features_passed["_feat_ts"] = pd.to_datetime(features_passed["scan_ts"], errors="coerce", utc=True)
            outcomes["_outcome_ts"] = pd.to_datetime(outcomes[outcome_date_col], errors="coerce", utc=True)

            valid_feat = features_passed.dropna(subset=["_feat_ts"])
            valid_out = outcomes.dropna(subset=["_outcome_ts"])

            if not valid_feat.empty and not valid_out.empty:
                merged_rows = []
                agg_cols = [
                    "signal_score", "volume_ratio", "forensic_sloan",
                    "forensic_beneish", "forensic_altman", "advisory_prob",
                    "pead_surprise_pct", "mirofish_conviction",
                    "sma_50", "sma_200", "price",
                ]
                for _, outcome_row in valid_out.iterrows():
                    tkr = outcome_row["ticker"]
                    ts = outcome_row["_outcome_ts"]
                    prior = valid_feat[
                        (valid_feat["ticker"] == tkr) & (valid_feat["_feat_ts"] < ts)
                    ]
                    if prior.empty:
                        continue
                    latest = prior.sort_values("_feat_ts").iloc[-1]
                    row = {}
                    for c in agg_cols:
                        row[c] = latest.get(c) if c in latest.index else np.nan
                    row["ticker"] = tkr
                    row["return_pct"] = outcome_row.get("return_pct", np.nan)
                    merged_rows.append(row)

                if merged_rows:
                    self.merged_df = pd.DataFrame(merged_rows)
                    LOG.info(
                        "Temporally aligned merge: %d rows from %d outcomes x %d features",
                        len(self.merged_df), len(valid_out), len(valid_feat),
                    )
                    return self.merged_df

            LOG.info("Temporal columns present but alignment produced no matches; falling back to ticker-level merge")

        features_agg = features_passed.groupby("ticker").agg({
            c: "mean" for c in [
                "signal_score", "volume_ratio", "forensic_sloan",
                "forensic_beneish", "forensic_altman", "advisory_prob",
                "pead_surprise_pct", "mirofish_conviction",
            ] if c in features_passed.columns
        })
        for c in ["sma_50", "sma_200", "price"]:
            if c in features_passed.columns:
                features_agg[c] = features_passed.groupby("ticker")[c].last()
        features_agg = features_agg.reset_index()

        merged = outcomes.merge(features_agg, on="ticker", how="inner", suffixes=("_outcome", "_feature"))
        self.merged_df = merged
        LOG.info("Ticker-level merge (no temporal data): %d rows from %d outcomes x %d features",
                 len(merged), len(outcomes), len(features_agg))
        return merged

    def train_model(self) -> dict[str, Any]:
        """Train Random Forest with held-out validation split.

        Uses 75/25 train/test split when data is sufficient (>= 20 rows).
        Validation R² must be non-negative to accept the model; otherwise
        updates are suppressed to avoid acting on noise.
        """
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.model_selection import train_test_split

        if self.merged_df.empty:
            return {"error": "No merged data available"}

        feature_cols = [
            "signal_score", "volume_ratio", "forensic_sloan", "forensic_beneish",
            "forensic_altman", "advisory_prob", "pead_surprise_pct",
            "mirofish_conviction",
        ]
        available = [c for c in feature_cols if c in self.merged_df.columns]

        target_col = "return_pct"
        if target_col not in self.merged_df.columns:
            return {"error": f"Target column '{target_col}' not found in merged data"}

        df = self.merged_df[available + [target_col]].dropna(subset=[target_col])
        df = df.fillna(0)

        if len(df) < 10:
            return {"error": f"Insufficient data: {len(df)} rows (need >= 10)"}

        X = df[available].values
        y = df[target_col].values

        use_split = len(df) >= 20
        val_r2: float | None = None

        if use_split:
            X_train, X_val, y_train, y_val = train_test_split(
                X, y, test_size=0.25, random_state=42,
            )
        else:
            X_train, y_train = X, y
            X_val, y_val = None, None

        self.model = RandomForestRegressor(
            n_estimators=100,
            max_depth=6,
            min_samples_leaf=3,
            random_state=42,
            n_jobs=-1,
        )
        self.model.fit(X_train, y_train)

        importances = self.model.feature_importances_
        self.feature_importance = sorted(
            zip(available, importances),
            key=lambda x: x[1],
            reverse=True,
        )

        train_r2 = round(self.model.score(X_train, y_train), 4)

        if X_val is not None:
            val_r2 = round(self.model.score(X_val, y_val), 4)
            if val_r2 < 0:
                LOG.warning(
                    "Validation R²=%.4f is negative — model does not generalize. "
                    "Suppressing parameter updates.", val_r2,
                )
                self._validation_passed = False
            else:
                self._validation_passed = True
        else:
            self._validation_passed = True

        return {
            "r2_train": train_r2,
            "r2_validation": val_r2,
            "validation_passed": self._validation_passed,
            "n_samples": len(df),
            "n_train": len(X_train),
            "n_validation": len(X_val) if X_val is not None else 0,
            "feature_importance": [
                {"feature": f, "importance": round(imp, 4)}
                for f, imp in self.feature_importance
            ],
        }

    def generate_updates(self, importance_threshold: float = 0.15) -> list[dict[str, Any]]:
        """
        For each high-importance tunable feature, check if its current
        threshold is contributing to losses and suggest an adjustment.

        Skipped entirely when validation R² is negative (model doesn't generalize).
        """
        if not self.feature_importance or self.merged_df.empty:
            return []

        if not getattr(self, "_validation_passed", True):
            LOG.info("Skipping update generation: validation did not pass")
            return []

        updates: list[dict[str, Any]] = []

        for feat_name, importance in self.feature_importance:
            if importance < importance_threshold:
                continue
            if feat_name not in TUNABLE_FEATURE_MAP:
                continue

            config = TUNABLE_FEATURE_MAP[feat_name]
            col = self.merged_df[feat_name].dropna()
            if col.empty:
                continue

            losing_mask = self.merged_df["return_pct"] < 0
            winning_mask = self.merged_df["return_pct"] > 0

            losing_mean = float(self.merged_df.loc[losing_mask, feat_name].mean()) if losing_mask.any() else None
            winning_mean = float(self.merged_df.loc[winning_mask, feat_name].mean()) if winning_mask.any() else None

            if losing_mean is None or winning_mean is None:
                continue

            current = config["current_default"]
            step = config["adjust_step"]
            direction = config["direction"]
            suggestion = current

            if direction == "higher_is_better":
                if losing_mean < winning_mean:
                    midpoint = (losing_mean + winning_mean) / 2
                    suggestion = max(current, round(midpoint, 4))
                    suggestion = min(suggestion, current + step * 2)
            elif direction == "lower_is_better":
                if losing_mean > winning_mean:
                    midpoint = (losing_mean + winning_mean) / 2
                    suggestion = min(current, round(midpoint, 4))
                    suggestion = max(suggestion, current - step * 2)

            suggestion = max(config["min_bound"], min(config["max_bound"], suggestion))

            if abs(suggestion - current) < step * 0.5:
                continue

            update = {
                "feature": feat_name,
                "env_key": config["env_key"],
                "current_value": current,
                "suggested_value": round(suggestion, 4),
                "importance": round(importance, 4),
                "losing_mean": round(losing_mean, 4) if losing_mean is not None else None,
                "winning_mean": round(winning_mean, 4) if winning_mean is not None else None,
                "direction": direction,
                "rationale": (
                    f"{feat_name} has importance {importance:.2f}. "
                    f"Losing trades avg {losing_mean:.4f} vs winning {winning_mean:.4f}. "
                    f"Adjusting {config['env_key']} from {current} to {suggestion}."
                ),
            }
            updates.append(update)

        self.strategy_updates = updates
        return updates

    def save_strategy_update(self) -> Path | None:
        if not self.strategy_updates:
            LOG.info("No strategy updates to save")
            self.strategy_update_payload = None
            return None

        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": self.lookback_days,
            "n_outcomes": len(self.outcomes_df),
            "n_features": len(self.feature_df),
            "n_merged": len(self.merged_df),
            "feature_importance": [
                {"feature": f, "importance": round(imp, 4)}
                for f, imp in self.feature_importance
            ],
            "updates": self.strategy_updates,
            "env_overrides": {
                u["env_key"]: str(u["suggested_value"])
                for u in self.strategy_updates
            },
        }
        self.strategy_update_payload = output

        if not self.write_strategy_file:
            return None
        path = self.skill_dir / STRATEGY_UPDATE_FILE
        path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        LOG.info("Saved strategy update to %s with %d adjustments", path, len(self.strategy_updates))
        return path

    def run(self, apply: bool = False) -> dict[str, Any]:
        """
        Full pipeline: load -> merge -> train -> generate -> save.

        Returns a summary dict suitable for logging or API response.
        """
        self.load_outcomes()
        self.load_features()

        if self.outcomes_df.empty:
            return {"status": "no_outcomes", "message": "No trade outcomes found"}
        if self.feature_df.empty:
            return {"status": "no_features", "message": "No feature store data found"}

        self.merge_data()
        if self.merged_df.empty:
            return {"status": "no_matches", "message": "No ticker matches between outcomes and features"}

        train_result = self.train_model()
        if "error" in train_result:
            return {"status": "train_failed", **train_result}

        updates = self.generate_updates()
        saved_path = self.save_strategy_update()

        if apply and saved_path:
            self._apply_to_env()

        return {
            "status": "ok",
            "training": train_result,
            "updates_count": len(updates),
            "updates": updates,
            "strategy_update_path": str(saved_path) if saved_path else None,
            "strategy_update": self.strategy_update_payload,
            "applied": apply and bool(saved_path),
        }

    def _apply_to_env(self) -> None:
        """Apply suggested overrides to .env (preserving existing lines)."""
        env_path = self.skill_dir / ".env"
        overrides = {u["env_key"]: str(u["suggested_value"]) for u in self.strategy_updates}
        if not overrides:
            return

        lines: list[str] = []
        seen: set[str] = set()
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    if key in overrides:
                        lines.append(f"{key}={overrides[key]}")
                        seen.add(key)
                        continue
                lines.append(line)

        for key, val in overrides.items():
            if key not in seen:
                lines.append(f"{key}={val}")

        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        LOG.info("Applied %d overrides to %s", len(overrides), env_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    apply = "--apply" in sys.argv
    dry_run = "--dry-run" in sys.argv

    engine = LearningEngine(skill_dir=SKILL_DIR)
    result = engine.run(apply=apply and not dry_run)

    print(json.dumps(result, indent=2, default=str))

    if result.get("status") == "ok":
        print(f"\nFeature importance ({len(engine.feature_importance)} features):")
        for feat, imp in engine.feature_importance:
            bar = "#" * int(imp * 50)
            print(f"  {feat:25s} {imp:.4f}  {bar}")

        if engine.strategy_updates:
            print(f"\nSuggested updates ({len(engine.strategy_updates)}):")
            for u in engine.strategy_updates:
                print(f"  {u['env_key']}: {u['current_value']} -> {u['suggested_value']}  (importance={u['importance']:.3f})")
        else:
            print("\nNo threshold adjustments suggested.")


if __name__ == "__main__":
    main()
