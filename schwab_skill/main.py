"""
System orchestration for the trading bot.

Schedules Morning Brief (9:25 AM ET), signal scan (9:30 AM ET),
hold reminders (3:30 PM ET), self-study (4:00 PM ET), and
weekly digest (Sunday 6:00 PM ET). Global try/except fires
critical error alert on crash.
"""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import schedule

from logger_setup import get_logger, setup_logging
from notifier import send_alert
from schwab_auth import DualSchwabAuth

SKILL_DIR = Path(__file__).resolve().parent
TZ_NY = ZoneInfo("America/New_York")


def daily_heartbeat(skill_dir: Path | str | None = None) -> None:
    """
    Check both Schwab API connections, get account value, send status via notifier.
    Now called internally by build_morning_brief as a sub-check.
    """
    skill_dir = Path(skill_dir or SKILL_DIR)
    env_path = skill_dir / ".env"
    log = get_logger(__name__)

    auth = DualSchwabAuth(skill_dir=skill_dir)
    status_parts = []

    try:
        auth.get_market_token()
        status_parts.append("Market Session: OK")
    except Exception as e:
        status_parts.append(f"Market Session: FAILED ({e})")
        log.warning("Market session check failed: %s", e)

    try:
        auth.get_account_token()
        status_parts.append("Account Session: OK")
    except Exception as e:
        status_parts.append(f"Account Session: FAILED ({e})")
        log.warning("Account session check failed: %s", e)

    account_value = None
    try:
        from execution import get_account_status
        result = get_account_status(auth=auth, skill_dir=skill_dir)
        if isinstance(result, dict):
            accounts = result.get("accounts", [])
            for acc in accounts:
                sec = acc.get("securitiesAccount", acc)
                equity = sec.get("currentBalances", {}).get("equity")
                cash = sec.get("currentBalances", {}).get("cashBalance")
                if equity is not None:
                    account_value = float(equity)
                    break
                if cash is not None and account_value is None:
                    account_value = float(cash)
            if account_value is not None:
                status_parts.append(f"Account Value: ${account_value:,.2f}")
        else:
            status_parts.append(f"Account Fetch: {result}")
    except Exception as e:
        status_parts.append(f"Account Fetch: FAILED ({e})")
        log.warning("Account fetch failed: %s", e)

    msg = "Trading Bot Daily Heartbeat\n" + "\n".join(status_parts)
    send_alert(msg, kind="heartbeat", env_path=env_path)
    log.info("Heartbeat sent: %s", msg)


def build_morning_brief(skill_dir: Path | str | None = None) -> None:
    """
    Comprehensive morning briefing at 9:25 AM ET.
    Sends a structured embed with fields for market, sectors, and portfolio.
    """
    from datetime import timezone

    from notifier import send_embed_alert

    skill_dir = Path(skill_dir or SKILL_DIR)
    env_path = skill_dir / ".env"
    log = get_logger(__name__)
    now = datetime.now(TZ_NY)

    embed: dict = {
        "title": f"Morning Brief - {now.strftime('%b %d, %Y')}",
        "color": 0x3498DB,
        "fields": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Scan runs at 9:30 ET | Use /check <ticker> for a quick look"},
    }

    auth = None
    try:
        auth = DualSchwabAuth(skill_dir=skill_dir)
    except Exception as e:
        log.warning("Morning brief auth failed: %s", e)

    try:
        from market_data import get_daily_history
        spy_df = get_daily_history("SPY", days=5, auth=auth, skill_dir=skill_dir)
        if not spy_df.empty and len(spy_df) >= 2:
            spy_price = float(spy_df["close"].iloc[-1])
            spy_prev = float(spy_df["close"].iloc[-2])
            spy_chg = (spy_price - spy_prev) / spy_prev * 100
            embed["fields"].append({
                "name": "Market",
                "value": f"SPY: **${spy_price:,.2f}** ({spy_chg:+.1f}% 1d)",
                "inline": True,
            })
    except Exception as e:
        log.warning("Morning brief SPY: %s", e)

    try:
        from sector_strength import get_sector_heatmap
        heatmap = get_sector_heatmap(auth, skill_dir)
        winning_rows = [r for r in heatmap.get("rows", []) if r["winning"]]
        if winning_rows:
            names = ", ".join(f"**{r['etf']}**" for r in winning_rows[:5])
            embed["fields"].append({
                "name": "Winning Sectors",
                "value": names,
                "inline": True,
            })
    except Exception as e:
        log.warning("Morning brief sectors: %s", e)

    try:
        from execution import get_account_status
        if auth:
            acct = get_account_status(auth=auth, skill_dir=skill_dir)
            if isinstance(acct, dict):
                pos_count = 0
                total_val = 0.0
                day_pl = 0.0
                for acc in acct.get("accounts", []):
                    sec = acc.get("securitiesAccount", acc)
                    for pos in sec.get("positions", []):
                        q = pos.get("longQuantity", 0) or pos.get("shortQuantity", 0)
                        if q > 0:
                            pos_count += 1
                            total_val += pos.get("marketValue", 0)
                            day_pl += pos.get("currentDayProfitLoss", 0)
                    eq = sec.get("currentBalances", {}).get("equity")
                    if eq and total_val == 0:
                        total_val = float(eq)
                if pos_count > 0:
                    embed["fields"].append({
                        "name": "Portfolio Snapshot",
                        "value": f"{pos_count} positions | ${total_val:,.0f} | Day P/L: ${day_pl:+,.0f}",
                        "inline": False,
                    })
    except Exception as e:
        log.warning("Morning brief portfolio: %s", e)

    try:
        from execution import get_execution_safety_summary
        safety = get_execution_safety_summary(skill_dir=skill_dir, days=1)
        ev = safety.get("events", {})
        guardrail_blocks = ev.get("guardrail_blocked_order", 0)
        exits_allowed = ev.get("guardrail_exit_allowed", 0)
        stop_ok = ev.get("stop_protection_attached", 0)
        stop_fail = ev.get("stop_protection_failed", 0)
        shadow_count = ev.get("action_shadow", 0)
        live_count = ev.get("action_live", 0)
        embed["fields"].append({
            "name": "Execution Safety - 24h",
            "value": (
                f"Blocks: {guardrail_blocks} | Exit bypass: {exits_allowed}\n"
                f"Stops ok/fail: {stop_ok}/{stop_fail}\n"
                f"Shadow/Live: {shadow_count}/{live_count}"
            ),
            "inline": False,
        })
    except Exception as e:
        log.warning("Morning brief execution safety: %s", e)

    send_embed_alert(embed, env_path=env_path)
    log.info("Morning brief sent")


def build_weekly_digest(skill_dir: Path | str | None = None) -> None:
    """
    Weekly performance digest -- scheduled for Sundays at 6 PM ET.
    Sends a structured embed with fields for signals, fills, and self-study.
    """
    import json
    from datetime import timezone

    from notifier import send_embed_alert

    skill_dir = Path(skill_dir or SKILL_DIR)
    env_path = skill_dir / ".env"
    log = get_logger(__name__)
    now = datetime.now(TZ_NY)
    week_start = (now - timedelta(days=7)).strftime("%b %d")
    week_end = now.strftime("%b %d, %Y")

    embed: dict = {
        "title": f"Weekly Digest - {week_start} to {week_end}",
        "color": 0x9B59B6,
        "fields": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Self-study runs daily at 4 PM ET"},
    }

    try:
        history_path = skill_dir / ".signal_alert_history.json"
        signals_this_week = 0
        if history_path.exists():
            data = json.loads(history_path.read_text())
            if isinstance(data, dict):
                cutoff = (now - timedelta(days=7)).isoformat()
                for ticker, entries in data.items():
                    if isinstance(entries, list):
                        signals_this_week += sum(
                            1 for e in entries
                            if isinstance(e, dict) and (e.get("timestamp", "") or e.get("date", "")) >= cutoff
                        )
                    elif isinstance(entries, dict) and (entries.get("timestamp", "") or entries.get("date", "")) >= cutoff:
                        signals_this_week += 1
        embed["fields"].append({
            "name": "Signals",
            "value": f"**{signals_this_week}** generated this week",
            "inline": True,
        })
    except Exception as e:
        log.warning("Weekly digest signals: %s", e)

    try:
        outcomes_path = skill_dir / ".trade_outcomes.json"
        if outcomes_path.exists():
            outcomes = json.loads(outcomes_path.read_text())
            if isinstance(outcomes, list):
                cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
                week_trades = [o for o in outcomes if (o.get("date", "") or "") >= cutoff]
                buys = sum(1 for o in week_trades if (o.get("side", "").upper()) == "BUY")
                sells = sum(1 for o in week_trades if (o.get("side", "").upper()) == "SELL")
                embed["fields"].append({
                    "name": "Fills",
                    "value": f"**{buys}** buy(s), **{sells}** sell(s)",
                    "inline": True,
                })
    except Exception as e:
        log.warning("Weekly digest outcomes: %s", e)

    try:
        study_path = skill_dir / ".self_study.json"
        if study_path.exists():
            study = json.loads(study_path.read_text())
            win_rate = study.get("win_rate")
            rt_count = study.get("round_trips_count", 0)
            suggested = study.get("suggested_min_conviction")
            by_conv = study.get("by_conviction", {})

            insights = []
            if rt_count > 0 and win_rate is not None:
                insights.append(f"{rt_count} round trips, {win_rate:.0f}% win rate")

            best_band = None
            best_wr = 0
            for band, info in by_conv.items():
                wr = info.get("win_rate", 0)
                if wr > best_wr and info.get("count", 0) >= 2:
                    best_wr = wr
                    best_band = band
            if best_band:
                insights.append(f"Best band: {best_band} ({best_wr:.0f}%)")

            by_sector = study.get("by_sector", {})
            best_sec = None
            best_sec_ret = -999
            for sec, info in by_sector.items():
                avg_ret = info.get("avg_return_pct", -999)
                if avg_ret > best_sec_ret and info.get("count", 0) >= 2:
                    best_sec_ret = avg_ret
                    best_sec = sec
            if best_sec:
                insights.append(f"Best sector: {best_sec} ({best_sec_ret:+.1f}%)")

            if suggested:
                insights.append(f"Min conviction: {suggested}")

            if insights:
                embed["fields"].append({
                    "name": "Self-Study",
                    "value": "\n".join(insights),
                    "inline": False,
                })
    except Exception as e:
        log.warning("Weekly digest self-study: %s", e)

    try:
        from execution import get_execution_safety_summary
        safety = get_execution_safety_summary(skill_dir=skill_dir, days=7)
        ev = safety.get("events", {})
        lines = [
            f"Guardrail blocks: {ev.get('guardrail_blocked_order', 0)}",
            f"Exit bypass allowed: {ev.get('guardrail_exit_allowed', 0)}",
            f"Stop attached/failed: {ev.get('stop_protection_attached', 0)}/{ev.get('stop_protection_failed', 0)}",
            f"Shadow/Live actions: {ev.get('action_shadow', 0)}/{ev.get('action_live', 0)}",
        ]
        top_reasons = safety.get("top_reasons") or []
        if top_reasons:
            first = top_reasons[0]
            lines.append(f"Top failure reason: {first.get('reason')} ({first.get('count')})")
        embed["fields"].append({
            "name": "Execution Safety - 7d",
            "value": "\n".join(lines),
            "inline": False,
        })
    except Exception as e:
        log.warning("Weekly digest execution safety: %s", e)

    try:
        from signal_scanner import get_signal_quality_summary
        quality = get_signal_quality_summary(skill_dir=skill_dir, days=7)
        d = quality.get("diagnostics", {})
        lines = [
            f"Scans: {quality.get('scan_count', 0)} | Signals: {quality.get('signals_total', 0)}",
            f"Avg score: {quality.get('avg_signal_score', 0):.1f} | Avg conviction: {quality.get('avg_conviction', 0):.1f}",
            f"Would-filter/filtered: {d.get('quality_gates_would_filter', 0)}/{d.get('quality_gates_filtered', 0)}",
            f"Weak breakout vol: {d.get('low_breakout_volume', 0)} | Weak MiroFish: {d.get('weak_mirofish_alignment', 0)}",
        ]
        embed["fields"].append({
            "name": "Signal Quality - 7d",
            "value": "\n".join(lines),
            "inline": False,
        })
    except Exception as e:
        log.warning("Weekly digest signal quality: %s", e)

    try:
        from signal_scanner import get_signal_quality_summary

        quality = get_signal_quality_summary(skill_dir=skill_dir, days=7)
        d = quality.get("diagnostics", {})
        sec_lines = [
            f"Tagged signals: {d.get('sec_tagged_signals', 0)} | Recent 8-K: {d.get('sec_recent_8k_count', 0)}",
            f"High-risk tags: {d.get('sec_high_risk_tag_count', 0)} | Data failures: {d.get('sec_data_failures', 0)}",
            f"Hint shadow/live: {d.get('sec_score_hint_shadow_adjustments', 0)}/{d.get('sec_score_hint_applied_count', 0)}",
        ]
        embed["fields"].append({
            "name": "SEC Enrichment - 7d",
            "value": "\n".join(sec_lines),
            "inline": False,
        })
    except Exception as e:
        log.warning("Weekly digest SEC diagnostics: %s", e)

    send_embed_alert(embed, env_path=env_path)
    log.info("Weekly digest sent")


def run_scheduler() -> None:
    """Run main loop with Morning Brief (9:25 AM ET), scan (9:30), hold reminders, self-study, and weekly digest."""
    setup_logging()
    log = get_logger(__name__)
    log.info("Trading bot starting. Morning Brief at 9:25 AM ET, Weekly Digest Sun 6 PM ET.")

    try:
        from discord_confirm import start_confirm_bot
        start_confirm_bot(SKILL_DIR / ".env")
    except Exception as e:
        log.warning("Discord confirm bot failed to start: %s (signals will use webhook)", e)

    _last_brief_minute: int | None = None

    def _run_morning_brief_if_scheduled() -> None:
        nonlocal _last_brief_minute
        now = datetime.now(TZ_NY)
        key = now.hour * 60 + now.minute
        if now.hour == 9 and now.minute == 25 and key != _last_brief_minute:
            _last_brief_minute = key
            try:
                build_morning_brief()
            except Exception as e:
                log.warning("Morning brief failed: %s", e)
                daily_heartbeat()

    _last_signal_minute: int | None = None

    def _run_signal_scan_if_scheduled() -> None:
        nonlocal _last_signal_minute
        now = datetime.now(TZ_NY)
        key = now.hour * 60 + now.minute
        if now.hour == 9 and now.minute == 30 and key != _last_signal_minute:
            _last_signal_minute = key
            try:
                from signal_scanner import run_scan_and_notify
                n = run_scan_and_notify(skill_dir=SKILL_DIR)
                log.info("Signal scan: %d signals found, Discord notifications sent.", n)
            except Exception as e:
                log.warning("Signal scan failed: %s", e)

    _last_hold_reminder_minute: int | None = None

    def _run_hold_reminder_if_scheduled() -> None:
        nonlocal _last_hold_reminder_minute
        now = datetime.now(TZ_NY)
        key = now.hour * 60 + now.minute
        if now.hour == 15 and now.minute == 30 and key != _last_hold_reminder_minute:
            _last_hold_reminder_minute = key
            try:
                from hold_reminder import check_hold_period_and_alert
                n = check_hold_period_and_alert(skill_dir=SKILL_DIR)
                if n > 0:
                    log.info("Hold reminder: %d alerts sent.", n)
            except Exception as e:
                log.warning("Hold reminder failed: %s", e)

    _last_self_study_minute: int | None = None

    def _run_self_study_if_scheduled() -> None:
        nonlocal _last_self_study_minute
        now = datetime.now(TZ_NY)
        key = now.hour * 60 + now.minute
        if now.hour == 16 and now.minute == 0 and key != _last_self_study_minute:
            _last_self_study_minute = key
            try:
                from self_study import run_self_study
                result = run_self_study(skill_dir=SKILL_DIR)
                if result.get("round_trips_count", 0) > 0:
                    log.info("Self-study: %d round trips, win_rate=%.1f%%",
                             result["round_trips_count"], result.get("win_rate") or 0)
            except Exception as e:
                log.warning("Self-study failed: %s", e)

    _last_weekly_minute: int | None = None

    def _run_weekly_digest_if_scheduled() -> None:
        nonlocal _last_weekly_minute
        now = datetime.now(TZ_NY)
        key = now.day * 10000 + now.hour * 60 + now.minute
        if now.weekday() == 6 and now.hour == 18 and now.minute == 0 and key != _last_weekly_minute:
            _last_weekly_minute = key
            try:
                build_weekly_digest()
            except Exception as e:
                log.warning("Weekly digest failed: %s", e)

    schedule.every().minute.do(_run_morning_brief_if_scheduled)
    schedule.every().minute.do(_run_signal_scan_if_scheduled)
    schedule.every().minute.do(_run_hold_reminder_if_scheduled)
    schedule.every().minute.do(_run_self_study_if_scheduled)
    schedule.every().minute.do(_run_weekly_digest_if_scheduled)
    build_morning_brief()

    try:
        while True:
            schedule.run_pending()
            import time
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Shutdown requested")
    except Exception as e:
        log.exception("Critical error")
        send_alert(
            f"Trading bot CRASH: {e}. Check logs immediately.",
            kind="crash",
            env_path=SKILL_DIR / ".env",
        )
        raise


if __name__ == "__main__":
    run_scheduler()
