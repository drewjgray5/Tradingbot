#!/usr/bin/env python3
"""
Validate Discord UI payload safety and formatting limits.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


def _validate_embed_limits(embed: dict) -> list[str]:
    errs: list[str] = []
    title = str(embed.get("title", ""))
    desc = str(embed.get("description", ""))
    if len(title) > 256:
        errs.append(f"title too long ({len(title)})")
    if len(desc) > 4096:
        errs.append(f"description too long ({len(desc)})")
    fields = embed.get("fields") or []
    if len(fields) > 25:
        errs.append(f"too many fields ({len(fields)})")
    for idx, f in enumerate(fields):
        name = str(f.get("name", ""))
        value = str(f.get("value", ""))
        if len(name) > 256:
            errs.append(f"field[{idx}] name too long ({len(name)})")
        if len(value) > 1024:
            errs.append(f"field[{idx}] value too long ({len(value)})")
    footer = embed.get("footer")
    if isinstance(footer, dict):
        text = str(footer.get("text", ""))
        if len(text) > 2048:
            errs.append(f"footer too long ({len(text)})")
    return errs


def main() -> int:
    from full_report import (
        EdgarSection,
        FullReport,
        MiroFishSection,
        report_to_discord_sections,
    )
    from notifier import _sanitize_embed
    from signal_scanner import _build_comparison_embed

    long_desc = "x" * 9000
    report = FullReport(
        ticker="AAPL",
        generated_at="2026-01-01T00:00:00Z",
        edgar=EdgarSection(
            cik="0000320193",
            risk_tag="medium",
            risk_reasons=["recent 8-K present", "material weakness noted in filing text"],
            recent_8k=True,
            filing_recency_days=4,
            recent_filings=[
                {
                    "form": "8-K",
                    "date": "2026-01-01",
                    "description": long_desc,
                    "url": "https://www.sec.gov",
                }
            ],
        ),
        mirofish=MiroFishSection(
            conviction_score=22,
            summary=long_desc,
            agent_votes=[
                {"name": "agent_a", "score": 55, "reason": long_desc},
                {"name": "agent_b", "score": -10, "reason": long_desc},
            ],
            continuation_probability=0.66,
            bull_trap_probability=0.22,
        ),
        synthesis=long_desc,
    )
    embeds = report_to_discord_sections(report)
    for i, e in enumerate(embeds):
        errs = _validate_embed_limits(e)
        if errs:
            print(f"FAIL: report embed {i} violates limits: {errs}")
            return 1

    comparison = _build_comparison_embed(
        [
            {"ticker": f"T{i}", "signal_score": 80 - i, "mirofish_conviction": 10 + i, "sector_etf": "XLK"}
            for i in range(20)
        ]
    )
    if not comparison or len(comparison.get("fields", [])) > 13:
        print("FAIL: comparison embed did not apply field cap")
        return 1

    sanitized = _sanitize_embed(
        {
            "title": "t" * 999,
            "description": "d" * 9999,
            "fields": [{"name": "n" * 500, "value": "v" * 5000, "inline": False}] * 40,
            "footer": {"text": "f" * 5000},
        }
    )
    errs = _validate_embed_limits(sanitized)
    if errs:
        print(f"FAIL: notifier sanitization left invalid embed: {errs}")
        return 1

    print("PASS: UI payload validation checks succeeded")
    print(f"  report_embeds={len(embeds)}")
    print(f"  comparison_fields={len(comparison.get('fields', [])) if comparison else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
