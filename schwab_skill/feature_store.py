"""
Feature Store: middleware that captures raw technical features alongside
pass/fail decisions for every ticker evaluated during a scan.

Logs into a ``feature_store`` table in the configured database (Supabase
Postgres or local SQLite). Designed to be called from within the scanner
pipeline so that every scan event builds a labelled training dataset
for downstream ML analysis (see evolve_logic.py).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import Session, declarative_base, sessionmaker

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent

Base = declarative_base()


class FeatureRecord(Base):  # type: ignore[valid-type,misc]
    """
    Schema for the feature_store table.

    Each row is one ticker evaluated during a scan, with raw technical
    features and the scanner's pass/fail decision.
    """

    __tablename__ = "feature_store"

    id = Column(String(40), primary_key=True, default=lambda: uuid.uuid4().hex[:16])
    scan_id = Column(String(40), nullable=False, index=True)
    scan_ts = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    ticker = Column(String(16), nullable=False, index=True)

    # Stage 2 features
    stage2_pass = Column(Integer, nullable=True)
    price = Column(Float, nullable=True)
    sma_50 = Column(Float, nullable=True)
    sma_150 = Column(Float, nullable=True)
    sma_200 = Column(Float, nullable=True)
    pct_from_52w_high = Column(Float, nullable=True)
    sma_200_upward_days = Column(Integer, nullable=True)

    # VCP features
    vcp_pass = Column(Integer, nullable=True)
    vcp_contraction_days = Column(Integer, nullable=True)
    latest_volume = Column(Float, nullable=True)
    avg_vol_50 = Column(Float, nullable=True)
    volume_ratio = Column(Float, nullable=True)

    # Sector features
    sector_etf = Column(String(16), nullable=True)
    sector_winning = Column(Integer, nullable=True)

    # Composite scores
    signal_score = Column(Float, nullable=True)
    stage_a_score = Column(Float, nullable=True)

    # Advisory / MiroFish
    advisory_prob = Column(Float, nullable=True)
    advisory_confidence = Column(String(16), nullable=True)
    mirofish_conviction = Column(Float, nullable=True)

    # Risk features
    sec_risk_tag = Column(String(16), nullable=True)
    forensic_sloan = Column(Float, nullable=True)
    forensic_beneish = Column(Float, nullable=True)
    forensic_altman = Column(Float, nullable=True)
    pead_surprise_pct = Column(Float, nullable=True)

    # Quality gate
    quality_reasons_json = Column(Text, nullable=True)
    quality_filtered = Column(Integer, nullable=True)

    # Event risk
    event_risk_flagged = Column(Integer, nullable=True)
    earnings_distance_days = Column(Integer, nullable=True)

    # Decision
    decision = Column(String(16), nullable=False, index=True)  # pass / stage2_fail / vcp_fail / sector_fail / quality_filtered / ...
    regime_bucket = Column(String(16), nullable=True)

    # Full payload for ad-hoc analysis
    raw_features_json = Column(Text, nullable=True)


def _get_session(skill_dir: Path | None = None) -> Session:
    """Reuse the webapp DB engine if available, else build a standalone one."""
    try:
        from webapp.db import SessionLocal
        return SessionLocal()
    except Exception:
        pass
    import os
    db_url = os.getenv("DATABASE_URL", f"sqlite:///{(skill_dir or SKILL_DIR) / 'webapp' / 'webapp.db'}")
    if db_url.startswith("postgres://"):
        db_url = "postgresql+psycopg2://" + db_url[len("postgres://"):]
    kwargs: dict[str, Any] = {}
    if db_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    eng = create_engine(db_url, **kwargs)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng)()


def ensure_table(skill_dir: Path | None = None) -> None:
    """Create the feature_store table if it doesn't exist."""
    try:
        from webapp.db import engine
        Base.metadata.create_all(bind=engine)
    except Exception:
        _get_session(skill_dir).close()


def log_stage_a_result(
    *,
    scan_id: str,
    ticker: str,
    result: dict[str, Any],
    regime_bucket: str | None = None,
    skill_dir: Path | None = None,
) -> None:
    """
    Log a Stage A evaluation result (pass or fail with reason).
    Called from within _scan_stage_a_one or after its return.
    """
    db = _get_session(skill_dir)
    try:
        ok = result.get("ok", False)
        candidate = result.get("candidate", {}) if ok else {}
        reason = result.get("reason", "unknown") if not ok else "pass"

        record = FeatureRecord(
            scan_id=scan_id,
            ticker=ticker.upper(),
            decision="pass" if ok else reason,
            regime_bucket=regime_bucket,
            # From candidate (only if passed Stage A)
            price=candidate.get("price"),
            sma_50=candidate.get("sma_50"),
            sma_200=candidate.get("sma_200"),
            sector_etf=candidate.get("sector_etf"),
            stage_a_score=candidate.get("stage_a_score"),
            latest_volume=candidate.get("latest_volume"),
            avg_vol_50=candidate.get("avg_vol_50"),
            stage2_pass=1 if ok else (0 if reason == "stage2_fail" else None),
            vcp_pass=1 if ok else (0 if reason == "vcp_fail" else None),
            sector_winning=1 if ok else (0 if reason == "sector_not_winning" else None),
        )
        if candidate.get("latest_volume") and candidate.get("avg_vol_50"):
            try:
                record.volume_ratio = float(candidate["latest_volume"]) / float(candidate["avg_vol_50"])
            except (TypeError, ValueError, ZeroDivisionError):
                pass

        db.add(record)
        db.commit()
    except Exception as e:
        db.rollback()
        LOG.debug("Feature store Stage A log failed for %s: %s", ticker, e)
    finally:
        db.close()


def log_stage_b_signal(
    *,
    scan_id: str,
    signal: dict[str, Any],
    quality_reasons: list[str] | None = None,
    quality_filtered: bool = False,
    regime_bucket: str | None = None,
    skill_dir: Path | None = None,
) -> None:
    """
    Log a fully enriched Stage B signal with all features and the final decision.
    """
    db = _get_session(skill_dir)
    try:
        advisory = signal.get("advisory") or {}
        event_risk = signal.get("event_risk") or {}

        record = FeatureRecord(
            scan_id=scan_id,
            ticker=str(signal.get("ticker", "")).upper(),
            decision="quality_filtered" if quality_filtered else "pass",
            regime_bucket=regime_bucket,
            # Technical
            price=signal.get("price"),
            sma_50=signal.get("sma_50"),
            sma_200=signal.get("sma_200"),
            stage2_pass=1,
            vcp_pass=1,
            sector_etf=signal.get("sector_etf"),
            sector_winning=1,
            latest_volume=signal.get("latest_volume"),
            avg_vol_50=signal.get("avg_vol_50"),
            signal_score=signal.get("signal_score"),
            # Advisory
            advisory_prob=advisory.get("p_up_10d"),
            advisory_confidence=advisory.get("confidence_bucket"),
            mirofish_conviction=signal.get("mirofish_conviction"),
            # Risk
            sec_risk_tag=signal.get("sec_risk_tag"),
            forensic_sloan=signal.get("forensic_sloan"),
            forensic_beneish=signal.get("forensic_beneish"),
            forensic_altman=signal.get("forensic_altman"),
            pead_surprise_pct=signal.get("pead_surprise_pct"),
            # Event risk
            event_risk_flagged=1 if event_risk.get("flagged") else 0,
            earnings_distance_days=event_risk.get("earnings_distance_days"),
            # Quality
            quality_reasons_json=json.dumps(quality_reasons) if quality_reasons else None,
            quality_filtered=1 if quality_filtered else 0,
        )
        if signal.get("latest_volume") and signal.get("avg_vol_50"):
            try:
                record.volume_ratio = float(signal["latest_volume"]) / float(signal["avg_vol_50"])
            except (TypeError, ValueError, ZeroDivisionError):
                pass

        safe_keys = {
            "ticker", "price", "sma_50", "sma_200", "signal_score",
            "sector_etf", "latest_volume", "avg_vol_50",
            "sec_risk_tag", "forensic_sloan", "forensic_beneish", "forensic_altman",
            "pead_surprise_pct", "pead_beat", "guidance_signal",
            "mirofish_conviction", "breakout_confirmed",
            "mirofish_disagreement", "agent_weighting", "meta_policy",
            "meta_policy_size_multiplier", "prediction_market_size_multiplier",
        }
        raw = {k: v for k, v in signal.items() if k in safe_keys}
        record.raw_features_json = json.dumps(raw, default=str)

        db.add(record)
        db.commit()
    except Exception as e:
        db.rollback()
        LOG.debug("Feature store Stage B log failed for %s: %s", signal.get("ticker"), e)
    finally:
        db.close()


def get_feature_dataframe(
    days: int = 90,
    skill_dir: Path | None = None,
) -> "pd.DataFrame":
    """Load feature_store rows from the last N days as a pandas DataFrame."""
    import pandas as pd
    db = _get_session(skill_dir)
    try:
        cutoff = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        from datetime import timedelta
        cutoff -= timedelta(days=days)
        rows = db.query(FeatureRecord).filter(FeatureRecord.scan_ts >= cutoff).all()
        if not rows:
            return pd.DataFrame()
        data = []
        for r in rows:
            data.append({
                "scan_id": r.scan_id,
                "scan_ts": r.scan_ts,
                "ticker": r.ticker,
                "decision": r.decision,
                "stage2_pass": r.stage2_pass,
                "vcp_pass": r.vcp_pass,
                "sector_winning": r.sector_winning,
                "price": r.price,
                "sma_50": r.sma_50,
                "sma_200": r.sma_200,
                "signal_score": r.signal_score,
                "stage_a_score": r.stage_a_score,
                "latest_volume": r.latest_volume,
                "avg_vol_50": r.avg_vol_50,
                "volume_ratio": r.volume_ratio,
                "sector_etf": r.sector_etf,
                "advisory_prob": r.advisory_prob,
                "advisory_confidence": r.advisory_confidence,
                "mirofish_conviction": r.mirofish_conviction,
                "sec_risk_tag": r.sec_risk_tag,
                "forensic_sloan": r.forensic_sloan,
                "forensic_beneish": r.forensic_beneish,
                "forensic_altman": r.forensic_altman,
                "pead_surprise_pct": r.pead_surprise_pct,
                "quality_filtered": r.quality_filtered,
                "event_risk_flagged": r.event_risk_flagged,
                "regime_bucket": r.regime_bucket,
            })
        return pd.DataFrame(data)
    finally:
        db.close()
