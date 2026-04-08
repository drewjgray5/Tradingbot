"""
Durable hypothesis records for scanner signals, advisory outputs, and report conclusions.

Stored under .hypothesis_ledger.json (list of records). Outcome metrics are written by
scripts/score_hypothesis_outcomes.py into each record's "outcomes" map keyed by horizon days.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
_LEDGER_NAME = ".hypothesis_ledger.json"
_LOCK = threading.Lock()


def _ledger_path(skill_dir: Path) -> Path:
    return skill_dir / _LEDGER_NAME


def _load_ledger(skill_dir: Path) -> dict[str, Any]:
    path = _ledger_path(skill_dir)
    if not path.exists():
        return {"schema": 1, "records": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            return data
    except Exception as e:
        LOG.warning("hypothesis_ledger load failed: %s", e)
    return {"schema": 1, "records": []}


def _save_ledger(skill_dir: Path, data: dict[str, Any]) -> None:
    path = _ledger_path(skill_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def fingerprint_from_mapping(payload: dict[str, Any], *, exclude_keys: frozenset[str] | None = None) -> str:
    ex = exclude_keys or frozenset()
    normalized = {k: payload[k] for k in sorted(payload.keys()) if k not in ex}
    raw = json.dumps(normalized, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def append_hypothesis(record: dict[str, Any], skill_dir: Path | str | None = None) -> str:
    """
    Append a hypothesis record. Returns record id.
    Expected keys on record: ticker (or scope), source, strategy_or_model_id, prediction (dict).
    Sets id and created_at when missing.
    """
    skill_dir = Path(skill_dir or SKILL_DIR)
    rid = str(record.get("id") or uuid.uuid4())
    rec = dict(record)
    rec["id"] = rid
    rec.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    rec.setdefault("outcomes", {})

    with _LOCK:
        data = _load_ledger(skill_dir)
        records: list[Any] = list(data.get("records") or [])
        records.append(rec)
        data["records"] = records
        _save_ledger(skill_dir, data)
    LOG.debug("hypothesis_ledger appended %s source=%s ticker=%s", rid, rec.get("source"), rec.get("ticker"))
    return rid


def record_from_signal(
    signal: dict[str, Any],
    skill_dir: Path | str | None = None,
    strategy_or_model_id: str | None = None,
) -> dict[str, Any]:
    """Build a hypothesis dict from a scanner signal row."""
    skill_dir = Path(skill_dir or SKILL_DIR)
    tkr = str(signal.get("ticker") or "").upper()
    price = signal.get("price")
    sma50 = signal.get("sma_50")
    sma200 = signal.get("sma_200")
    advisory = signal.get("advisory") if isinstance(signal.get("advisory"), dict) else {}
    model_id = strategy_or_model_id
    if not model_id:
        try:
            from config import get_advisory_model_path

            model_id = Path(get_advisory_model_path(skill_dir)).name
        except Exception:
            model_id = "advisory_default"

    fp_src = {
        "ticker": tkr,
        "signal_score": signal.get("signal_score"),
        "mirofish_conviction": signal.get("mirofish_conviction"),
        "sector_etf": signal.get("sector_etf"),
        "advisory_p_up": advisory.get("p_up_10d"),
    }
    fp = fingerprint_from_mapping(fp_src, exclude_keys=frozenset())

    prediction: dict[str, Any] = {
        "direction": "long",
        "entry_reference_px": float(price) if price is not None else None,
        "levels": {
            "sma_50": float(sma50) if sma50 is not None else None,
            "sma_200": float(sma200) if sma200 is not None else None,
        },
        "horizons_trading_days": [],  # filled from config at score time
    }
    if advisory:
        prediction["advisory"] = {
            "p_up_10d": advisory.get("p_up_10d"),
            "confidence_bucket": advisory.get("confidence_bucket"),
        }

    return {
        "ticker": tkr,
        "source": "signal_scanner",
        "strategy_or_model_id": model_id,
        "input_fingerprint": fp,
        "prediction": prediction,
        "raw_context": {
            "signal_score": signal.get("signal_score"),
            "mirofish_conviction": signal.get("mirofish_conviction"),
        },
    }


def record_from_advisory_row(
    ticker: str,
    advisory: dict[str, Any],
    skill_dir: Path | str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    skill_dir = Path(skill_dir or SKILL_DIR)
    try:
        from config import get_advisory_model_path

        model_id = Path(get_advisory_model_path(skill_dir)).name
    except Exception:
        model_id = "advisory_default"
    fp = fingerprint_from_mapping(
        {"ticker": ticker.upper(), **(advisory or {})},
        exclude_keys=frozenset(),
    )
    p_up = advisory.get("p_up_10d")
    direction = "long" if (p_up is None or float(p_up) >= 0.5) else "short"
    rec = {
        "ticker": ticker.upper(),
        "source": "advisory",
        "strategy_or_model_id": model_id,
        "input_fingerprint": fp,
        "prediction": {
            "direction": direction,
            "entry_reference_px": None,
            "advisory": dict(advisory),
            "horizons_trading_days": [],
        },
        "raw_context": extra or {},
    }
    return rec


def record_from_report_conclusion(
    ticker: str,
    conclusion: dict[str, Any],
    *,
    skill_dir: Path | str | None = None,
    report_kind: str = "full_report",
) -> dict[str, Any]:
    fp = fingerprint_from_mapping({"ticker": ticker.upper(), **conclusion})
    return {
        "ticker": ticker.upper(),
        "source": report_kind,
        "strategy_or_model_id": conclusion.get("model_id", "full_report_v1"),
        "input_fingerprint": fp,
        "prediction": {
            "direction": conclusion.get("direction", "neutral"),
            "entry_reference_px": conclusion.get("reference_px"),
            "notes": conclusion.get("summary"),
            "horizons_trading_days": [],
        },
        "raw_context": {"sections": conclusion.get("sections_touched")},
    }


def summarize_scored_hypotheses(skill_dir: Path | str | None = None) -> dict[str, Any]:
    """Aggregate hit rates / mean returns by source for self-study merge."""
    skill_dir = Path(skill_dir or SKILL_DIR)
    data = _load_ledger(skill_dir)
    records = [r for r in (data.get("records") or []) if isinstance(r, dict)]
    by_source: dict[str, dict[str, Any]] = {}
    for r in records:
        src = str(r.get("source") or "unknown")
        out = r.get("outcomes") if isinstance(r.get("outcomes"), dict) else {}
        if not out:
            continue
        bucket = by_source.setdefault(src, {"n": 0, "hits": 0, "returns": []})
        for _h, metrics in out.items():
            if not isinstance(metrics, dict):
                continue
            if "thesis_hit" not in metrics and "return_pct" not in metrics:
                continue
            bucket["n"] += 1
            if metrics.get("thesis_hit") is True:
                bucket["hits"] += 1
            rp = metrics.get("return_pct")
            if rp is not None:
                try:
                    bucket["returns"].append(float(rp))
                except (TypeError, ValueError):
                    pass
    summary: dict[str, Any] = {}
    for src, b in by_source.items():
        n = int(b["n"])
        hits = int(b["hits"])
        rets = b["returns"]
        summary[src] = {
            "scored_samples": n,
            "hit_rate": round(hits / n, 4) if n else None,
            "mean_return_pct": round(sum(rets) / len(rets), 4) if rets else None,
        }
    return {"by_source": summary, "ledger_records": len(records)}


def promotion_guard_reasons(skill_dir: Path | str | None = None) -> list[str]:
    """
    Optional advisory promotion blocker: too few samples or hit rate below floor
    for advisory-sourced scored hypotheses.
    """
    skill_dir = Path(skill_dir or SKILL_DIR)
    try:
        from config import (
            get_hypothesis_promotion_guard_enabled,
            get_hypothesis_promotion_min_hit_rate,
            get_hypothesis_promotion_min_n,
        )

        if not get_hypothesis_promotion_guard_enabled(skill_dir):
            return []
        min_n = int(get_hypothesis_promotion_min_n(skill_dir))
        min_hr = float(get_hypothesis_promotion_min_hit_rate(skill_dir))
    except Exception:
        return []

    summ = summarize_scored_hypotheses(skill_dir)
    by = summ.get("by_source") or {}
    combined_n = 0
    weighted = 0.0
    for src in ("advisory", "signal_scanner"):
        b = by.get(src) or {}
        sn = int(b.get("scored_samples") or 0)
        hr = b.get("hit_rate")
        if sn and hr is not None:
            combined_n += sn
            weighted += float(hr) * sn
    if combined_n < min_n:
        return []
    avg_hr = weighted / combined_n if combined_n else None
    if avg_hr is None:
        return ["hypothesis_promotion_guard_missing_hit_rate"]
    if float(avg_hr) < min_hr:
        return [
            f"hypothesis_promotion_guard_low_hit_rate:{float(avg_hr):.4f}<{min_hr:.4f}_n={combined_n}"
        ]
    return []
