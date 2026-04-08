"""
Discord bot for trade confirmation: sends Approve/Reject buttons before executing.
Requires DISCORD_BOT_TOKEN and DISCORD_CONFIRM_CHANNEL_ID in .env.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
import uuid
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
_pending: dict[str, dict] = {}
_lock = threading.Lock()
_bot_start_time: float | None = None
_last_scan_info: dict[str, Any] = {}

EMBED_COLORS = {
    "green": 0x2ECC71,
    "yellow": 0xF39C12,
    "red": 0xE74C3C,
    "blue": 0x3498DB,
    "purple": 0x9B59B6,
    "teal": 0x1ABC9C,
    "orange": 0xE67E22,
}
MAX_PORTFOLIO_ROWS = 10
MAX_SECTOR_ROWS = 12


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _stamp_embed(embed_dict: dict, footer_text: str = "") -> dict:
    """Add a UTC timestamp and optional footer to an embed dict."""
    embed_dict["timestamp"] = _utc_now_iso()
    if footer_text:
        embed_dict["footer"] = {"text": footer_text}
    return embed_dict


def _friendly_error(prefix: str) -> str:
    return f"{prefix} Please try again in a moment."


# MiroFish agent display names
AGENT_DISPLAY_NAMES = {
    "institutional_trend": "Institutional Trend-Follower",
    "mean_reversion": "Mean-Reversion Bot",
    "retail_fomo": "Retail FOMO Trader",
}


def _build_conviction_meter(score: int, width: int = 21) -> str:
    """Build a visual bar from -100 to +100 with cursor. score in [-100, 100]."""
    score = max(-100, min(100, int(score)))
    # Map -100..100 to position 0..width
    pos = round((score + 100) / 200 * width) if width > 0 else 0
    pos = max(0, min(width, pos))
    bar = "░" * pos + "●" + "░" * (width - pos)
    return f"`{bar}`\nBearish `-100` ← **{score:+d}** → `+100` Bullish"


def _get_simulation_viewer_url() -> str:
    """Base URL for simulation viewer (configurable via SIMULATION_VIEWER_URL)."""
    env = _load_env(SKILL_DIR / ".env")
    return (env.get("SIMULATION_VIEWER_URL") or "http://127.0.0.1:3000").strip().rstrip("/")


def _build_mirofish_embed(mirofish_result: dict | None):
    """
    Build a Discord Embed from MiroFish result: Conviction Meter + influential agent summary.
    Includes a local link to open the interactive social graph when simulation_id is present.
    Returns None if no usable data.
    """
    import discord

    if not mirofish_result:
        return None
    conv = mirofish_result.get("conviction_score")
    summary = mirofish_result.get("summary") or "—"
    votes = mirofish_result.get("agent_votes") or []
    sim_id = mirofish_result.get("simulation_id")
    cont_prob = mirofish_result.get("continuation_probability")
    bull_prob = mirofish_result.get("bull_trap_probability")

    def _fmt_prob(p: object) -> str | None:
        try:
            f = float(p)  # type: ignore[arg-type]
            if f != f:
                return None
            f = max(0.0, min(1.0, f))
            return f"{f * 100.0:.0f}%"
        except Exception:
            return None

    embed = discord.Embed(
        title="MiroFish Market Sentiment",
        description=f"**{summary}**",
        color=0x9B59B6,
    )
    if conv is not None:
        embed.add_field(
            name="Conviction Meter",
            value=_build_conviction_meter(conv),
            inline=False,
        )

    # Optional: show scenario probabilities if produced by the simulation.
    cont_s = _fmt_prob(cont_prob)
    bull_s = _fmt_prob(bull_prob)
    if cont_s and bull_s:
        embed.add_field(
            name="Scenario Probabilities",
            value=f"Continuation next 1-2w: {cont_s}\nBull Trap next 1-2w: {bull_s}",
            inline=False,
        )
    # Sort by |score| descending to show most influential first
    sorted_votes = sorted(votes, key=lambda a: abs(a.get("score", 0)), reverse=True)
    if sorted_votes:
        lines = []
        for a in sorted_votes[:3]:
            name = AGENT_DISPLAY_NAMES.get(a.get("name", ""), a.get("name", "Agent"))
            s = a.get("score", 0)
            reason = (a.get("reason") or "").strip()[:120]
            # Optional: include per-agent scenario probabilities when available.
            cont_agent_s = _fmt_prob(a.get("continuation_probability"))
            bull_agent_s = _fmt_prob(a.get("bull_trap_probability"))
            probs_suffix = ""
            if cont_agent_s and bull_agent_s:
                probs_suffix = f" (A:{cont_agent_s} / B:{bull_agent_s})"
            if reason:
                lines.append(f"**{name}** ({s:+d})\n*{reason}{probs_suffix}*")
            else:
                lines.append(f"**{name}** ({s:+d}){probs_suffix}")
        embed.add_field(
            name="Agent Sentiment",
            value="\n\n".join(lines) if lines else "—",
            inline=False,
        )
    if sim_id:
        base = _get_simulation_viewer_url()
        link = f"{base}/simulation/{sim_id}"
        embed.add_field(
            name="Live View",
            value=f"[Open interactive social graph]({link})",
            inline=False,
        )
    return embed


def _load_env(env_path: Path | None = None) -> dict:
    path = env_path or SKILL_DIR / ".env"
    if not path.exists():
        return {}
    vals = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip().strip('"\'')
    return vals


_confirmation_queue: queue.Queue = queue.Queue()
_bot_ready = threading.Event()
_bot_started = False


def request_trade_confirmation(signal: dict, skill_dir: Path, mock: bool = False) -> bool:
    """
    Queue a trade for Discord confirmation. Returns True if queued (bot running).
    mock=True: no real order on Approve, just "Mock executed" message.
    """
    if not _bot_ready.is_set():
        return False
    if mock:
        signal = dict(signal)
        signal["_mock"] = True
    _confirmation_queue.put((signal, skill_dir))
    return True


def request_mock_confirmation(
    ticker: str = "AAPL",
    price: float = 225.0,
    skill_dir: Path | None = None,
    sma_50: float | None = None,
    sma_200: float | None = None,
    signal_score: float | None = None,
    mirofish_summary: str | None = None,
    mirofish_conviction: int | float | None = None,
    mirofish_result: dict | None = None,
) -> bool:
    """Queue a mock trade for testing the confirmation flow."""
    skill_dir = skill_dir or SKILL_DIR
    signal = {"ticker": ticker, "price": price, "sector_etf": "XLK"}
    if sma_50 is not None:
        signal["sma_50"] = sma_50
    if sma_200 is not None:
        signal["sma_200"] = sma_200
    if signal_score is not None:
        signal["signal_score"] = signal_score
    if mirofish_summary is not None:
        signal["mirofish_summary"] = mirofish_summary
    if mirofish_conviction is not None:
        signal["mirofish_conviction"] = mirofish_conviction
    if mirofish_result is not None:
        signal["mirofish_result"] = mirofish_result
    elif mirofish_summary is not None or mirofish_conviction is not None:
        import uuid
        mock_sim_id = f"sim_{uuid.uuid4().hex[:12]}"
        mock_result = {
            "simulation_id": mock_sim_id,
            "ticker": ticker,
            "conviction_score": int(mirofish_conviction) if mirofish_conviction is not None else 0,
            "summary": mirofish_summary or "—",
            "agent_votes": [
                {"name": "institutional_trend", "score": 60, "reason": "Momentum favors continuation"},
                {"name": "mean_reversion", "score": 25, "reason": "Moderate extension, some caution"},
                {"name": "retail_fomo", "score": 50, "reason": "Positive news sentiment"},
            ],
            "seed_preview": "(mock trade)",
        }
        signal["mirofish_result"] = mock_result
        try:
            from engine_analysis import _persist_simulation
            _persist_simulation(mock_sim_id, mock_result, skill_dir)
        except Exception as e:
            LOG.debug("Could not persist mock simulation: %s", e)
    return request_trade_confirmation(signal, skill_dir, mock=True)


def _build_portfolio_embed(skill_dir: Path) -> dict:
    """Build a portfolio embed using embed fields (mobile-friendly, no code blocks)."""
    import json
    from datetime import date

    from execution import get_account_status
    from schwab_auth import DualSchwabAuth

    auth = DualSchwabAuth(skill_dir=skill_dir)
    result = get_account_status(auth=auth, skill_dir=skill_dir)

    if isinstance(result, str):
        return _stamp_embed(
            {"title": "Portfolio Snapshot", "description": result, "color": EMBED_COLORS["red"]},
            "Use /check TICKER for a quick verdict",
        )

    accounts = result.get("accounts", [])
    positions = []
    total_value = 0.0
    total_day_pl = 0.0

    for acc in accounts:
        sec = acc.get("securitiesAccount", acc)
        for pos in sec.get("positions", []):
            inst = pos.get("instrument", {})
            sym = inst.get("symbol", "?")
            qty = pos.get("longQuantity", 0) or pos.get("shortQuantity", 0)
            if qty == 0:
                continue
            avg_cost = pos.get("averagePrice", 0)
            mkt_val = pos.get("marketValue", 0)
            current = pos.get("currentDayProfitLoss", 0)
            current_price = (mkt_val / qty) if qty else 0
            pl_pct = ((current_price - avg_cost) / avg_cost * 100) if avg_cost else 0

            positions.append({
                "sym": sym, "qty": int(qty), "avg_cost": avg_cost,
                "current": current_price, "pl_pct": pl_pct,
                "day_pl": current, "mkt_val": mkt_val,
            })
            total_value += mkt_val
            total_day_pl += current

    tracker_path = skill_dir / ".positions_tracker.json"
    entry_dates: dict[str, str] = {}
    if tracker_path.exists():
        try:
            data = json.loads(tracker_path.read_text())
            if isinstance(data, dict):
                for sym, info in data.items():
                    if isinstance(info, dict) and info.get("entry_date"):
                        entry_dates[sym.upper()] = info["entry_date"]
        except Exception:
            pass

    if not positions:
        return _stamp_embed(
            {"title": "Portfolio Snapshot", "description": "No open positions.", "color": EMBED_COLORS["blue"]},
            "Use /check TICKER for a quick verdict",
        )

    today = date.today()
    positions.sort(key=lambda p: abs(float(p.get("mkt_val", 0) or 0)), reverse=True)
    color = EMBED_COLORS["green"] if total_day_pl >= 0 else EMBED_COLORS["red"]
    embed: dict[str, Any] = {
        "title": f"Portfolio Snapshot - {len(positions)} position(s)",
        "description": f"Total: **${total_value:,.0f}** | Day P/L: **${total_day_pl:+,.0f}**",
        "color": color,
        "fields": [],
    }

    for p in positions[:MAX_PORTFOLIO_ROWS]:
        ed = entry_dates.get(p["sym"].upper())
        days_held = None
        if ed:
            try:
                days_held = (today - date.fromisoformat(ed)).days
            except Exception:
                pass
        days_str = f"{days_held}d" if days_held is not None else "?"
        pl_sign = "+" if p["pl_pct"] >= 0 else ""
        embed["fields"].append({
            "name": f"{p['sym']}  {pl_sign}{p['pl_pct']:.1f}%",
            "value": (
                f"{p['qty']} shares @ ${p['avg_cost']:.2f}\n"
                f"Now ${p['current']:.2f} | Held {days_str}"
            ),
            "inline": True,
        })
    if len(positions) > MAX_PORTFOLIO_ROWS:
        embed["fields"].append(
            {
                "name": "Additional Positions",
                "value": f"+{len(positions) - MAX_PORTFOLIO_ROWS} more position(s) not shown.",
                "inline": False,
            }
        )

    return _stamp_embed(embed, "Use /check TICKER for a quick verdict")


def _build_sectors_embed(skill_dir: Path) -> dict:
    """Build a sector heatmap using inline embed fields (mobile-friendly)."""
    from schwab_auth import DualSchwabAuth
    from sector_strength import get_sector_heatmap

    auth = DualSchwabAuth(skill_dir=skill_dir)
    heatmap = get_sector_heatmap(auth, skill_dir)

    rows = heatmap.get("rows", [])
    spy_ret = heatmap.get("spy_return", 0)
    winning = heatmap.get("winning_count", 0)
    total = heatmap.get("total", 0)

    if not rows:
        return _stamp_embed(
            {"title": "Sector Heatmap", "description": "No sector data available.", "color": EMBED_COLORS["yellow"]},
            "Use /sectors to refresh",
        )

    embed: dict[str, Any] = {
        "title": "Sector Heatmap (21d vs SPY)",
        "description": f"**{winning}/{total}** sectors beating SPY ({spy_ret:+.1f}%)",
        "color": EMBED_COLORS["blue"],
        "fields": [],
    }

    rows = sorted(rows, key=lambda r: float(r.get("return_pct", 0) or 0), reverse=True)
    for r in rows[:MAX_SECTOR_ROWS]:
        pct = r["return_pct"]
        status = "WINNING" if r["winning"] else "lagging"
        bar_len = min(8, max(0, int(abs(pct) * 2)))
        bar = chr(0x2588) * bar_len + chr(0x2591) * (8 - bar_len)
        embed["fields"].append({
            "name": f"{r['etf']} {r['name']}",
            "value": f"`{bar}` {pct:+.1f}%  **{status}**",
            "inline": True,
        })
    if len(rows) > MAX_SECTOR_ROWS:
        embed["fields"].append(
            {
                "name": "Additional Sectors",
                "value": f"+{len(rows) - MAX_SECTOR_ROWS} more sector(s) not shown.",
                "inline": False,
            }
        )

    return _stamp_embed(embed, "Scan filters for winning sectors only")


def _build_status_embed(skill_dir: Path) -> dict:
    """Build a bot status embed showing uptime, API health, and last scan info."""
    import time as _time

    fields = []

    if _bot_start_time:
        uptime_s = _time.time() - _bot_start_time
        hours = int(uptime_s // 3600)
        mins = int((uptime_s % 3600) // 60)
        fields.append({"name": "Bot", "value": f"Online ({hours}h {mins}m)", "inline": True})
    else:
        fields.append({"name": "Bot", "value": "Starting...", "inline": True})

    market_ok = False
    account_ok = False
    try:
        from schwab_auth import DualSchwabAuth
        auth = DualSchwabAuth(skill_dir=skill_dir)
        try:
            auth.get_market_token()
            market_ok = True
        except Exception:
            pass
        try:
            auth.get_account_token()
            account_ok = True
        except Exception:
            pass
    except Exception:
        pass

    fields.append({"name": "Market API", "value": "**OK**" if market_ok else "**FAIL**", "inline": True})
    fields.append({"name": "Account API", "value": "**OK**" if account_ok else "**FAIL**", "inline": True})

    scan_info = _last_scan_info
    if scan_info.get("time"):
        fields.append({
            "name": "Last Scan",
            "value": f"{scan_info['time']} ({scan_info.get('signals', 0)} signals)",
            "inline": True,
        })
    else:
        fields.append({"name": "Last Scan", "value": "Not yet", "inline": True})

    fields.append({"name": "Next Scan", "value": "9:30 AM ET (daily)", "inline": True})

    color = EMBED_COLORS["green"] if (market_ok and account_ok) else EMBED_COLORS["yellow"]
    return _stamp_embed(
        {"title": "System Status", "color": color, "fields": fields},
        "All systems operational" if (market_ok and account_ok) else "Some APIs unavailable",
    )


def _run_bot_async(env: dict) -> None:
    import discord
    from discord import app_commands

    intents = discord.Intents.default()
    intents.message_content = True

    class TradeConfirmView(discord.ui.View):
        def __init__(self, trade_id: str):
            # Keep buttons valid longer; 10 minutes was easy to miss in real usage.
            super().__init__(timeout=3600)
            self.trade_id = trade_id

        async def _mark_message_done(self, interaction: discord.Interaction, status: str) -> None:
            """Disable buttons and annotate message so users see final state."""
            try:
                for item in self.children:
                    item.disabled = True
                if interaction.message and interaction.message.embeds:
                    embed = interaction.message.embeds[0].copy()
                    embed.set_footer(text=status)
                    await interaction.message.edit(embed=embed, view=self)
                elif interaction.message:
                    await interaction.message.edit(view=self)
            except Exception as e:
                LOG.debug("TradeConfirmView: failed to update message state: %s", e)

        async def _send_ephemeral_or_channel(
            self,
            interaction: discord.Interaction,
            message: str,
        ) -> None:
            """Prefer ephemeral followup; fallback to channel message when token is stale."""
            try:
                await interaction.followup.send(message, ephemeral=True)
                return
            except Exception:
                pass
            try:
                if interaction.channel:
                    await interaction.channel.send(message)
            except Exception:
                pass

        @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
        async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            try:
                # CRITICAL: Defer FIRST - Discord requires response within 3 seconds
                await interaction.response.defer(ephemeral=True)
            except Exception as e:
                LOG.exception("Approve: failed to defer interaction: %s", e)
                return
            with _lock:
                trade = _pending.get(self.trade_id)
            if not trade:
                await self._send_ephemeral_or_channel(
                    interaction,
                    "Trade expired, already handled, or bot was restarted. Please run a fresh scan.",
                )
                await self._mark_message_done(interaction, "Trade expired or already handled")
                return
            if trade.get("_mock"):
                msg = f"**Mock executed:** Would have placed BUY {trade['qty']} {trade['ticker']} @ MARKET (~${trade['qty'] * trade.get('price', 0):,.2f})"
                await self._send_ephemeral_or_channel(interaction, msg)
                with _lock:
                    _pending.pop(self.trade_id, None)
                await self._mark_message_done(interaction, "Approved (mock)")
                self.stop()
                return
            try:
                from execution import place_order
                sig = trade.get("signal") or {}
                result = await asyncio.to_thread(
                    place_order,
                    trade["ticker"], trade["qty"], "BUY", "MARKET",
                    auth=None, skill_dir=trade["skill_dir"],
                    price_hint=trade.get("price"),
                    mirofish_conviction=sig.get("mirofish_conviction"),
                    sector_etf=sig.get("sector_etf"),
                )
                if isinstance(result, dict) and result.get("orderId"):
                    msg = f"Order placed: {trade['qty']} {trade['ticker']} @ MARKET. Order ID: {result.get('orderId', 'N/A')}"
                elif isinstance(result, str) and "Error" not in result:
                    msg = result
                else:
                    msg = f"Failed: {result}"
                await self._send_ephemeral_or_channel(interaction, f"**Executed:** {msg}")
                with _lock:
                    _pending.pop(self.trade_id, None)
                await self._mark_message_done(interaction, "Approved and processed")
                self.stop()
            except Exception as e:
                LOG.exception("Approve: error executing trade: %s", e)
                await self._send_ephemeral_or_channel(interaction, f"Error: {e}")

        @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
        async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception as e:
                LOG.exception("Reject: failed to defer: %s", e)
                return
            with _lock:
                existed = _pending.pop(self.trade_id, None)
            self.stop()
            if existed:
                await self._send_ephemeral_or_channel(interaction, "Trade rejected.")
                await self._mark_message_done(interaction, "Rejected")
            else:
                await self._send_ephemeral_or_channel(
                    interaction,
                    "Trade was already handled or expired. Run a fresh scan if needed.",
                )
                await self._mark_message_done(interaction, "Trade expired or already handled")

        async def on_timeout(self):
            with _lock:
                _pending.pop(self.trade_id, None)
            for item in self.children:
                item.disabled = True
            try:
                if self.message and self.message.embeds:
                    embed = self.message.embeds[0].copy()
                    embed.set_footer(text="Trade confirmation expired - run a fresh scan")
                    await self.message.edit(embed=embed, view=self)
                elif self.message:
                    await self.message.edit(view=self)
            except Exception:
                pass

    class ConfirmBot(discord.Client):
        def __init__(self):
            super().__init__(intents=intents)
            self.tree = app_commands.CommandTree(self)

        async def setup_hook(self):
            @self.tree.command(name="ping", description="Check that the bot responds (instant)")
            async def ping(interaction: discord.Interaction):
                await interaction.response.send_message("Pong - bot is online.", ephemeral=True)

            @self.tree.command(name="scan", description="Run a new signal scan (Stage 2 + VCP)")
            async def scan(interaction: discord.Interaction):
                # Acknowledge within 3s — must run before any blocking work
                try:
                    await interaction.response.defer(ephemeral=False, thinking=True)
                except discord.InteractionResponded:
                    return
                except Exception as e:
                    LOG.exception("Scan: defer failed: %s", e)
                    return

                channel = interaction.channel

                async def _finish(msg: str) -> None:
                    try:
                        await interaction.followup.send(msg, ephemeral=False)
                    except Exception as e:
                        LOG.warning("Scan followup failed (%s), posting to channel", e)
                        if channel:
                            try:
                                await channel.send(msg)
                            except Exception as e2:
                                LOG.warning("Scan channel send failed: %s", e2)

                try:
                    from signal_scanner import run_scan_and_notify

                    def do_scan():
                        return run_scan_and_notify(skill_dir=SKILL_DIR, send_summary=False)

                    n = await asyncio.to_thread(do_scan)
                    from datetime import datetime
                    from zoneinfo import ZoneInfo
                    _now = datetime.now(ZoneInfo("America/New_York"))
                    _last_scan_info["time"] = _now.strftime("%b %d %I:%M %p ET")
                    _last_scan_info["signals"] = n
                    await _finish(
                        f"**Scan complete.** Found **{n}** signal(s). Check the channel for alerts.",
                    )
                except Exception as e:
                    LOG.exception("Scan command failed: %s", e)
                    await _finish(_friendly_error("Scan failed."))

            @self.tree.command(name="check", description="Quick 3-line verdict for a ticker (fast)")
            @app_commands.describe(ticker="Stock ticker symbol (e.g. AAPL)")
            async def check(interaction: discord.Interaction, ticker: str):
                try:
                    await interaction.response.defer(ephemeral=False, thinking=True)
                except discord.InteractionResponded:
                    return
                except Exception as e:
                    LOG.exception("Check: defer failed: %s", e)
                    return
                try:
                    from full_report import quick_check
                    embed_data = await asyncio.to_thread(quick_check, ticker.upper().strip())
                    embed = discord.Embed.from_dict(embed_data)
                    await interaction.followup.send(embed=embed, ephemeral=False)
                except Exception as e:
                    LOG.exception("Check command failed: %s", e)
                    try:
                        await interaction.followup.send(_friendly_error(f"Check failed for **{ticker.upper()}**."), ephemeral=False)
                    except Exception:
                        pass

            @self.tree.command(name="portfolio", description="Show current positions with P/L")
            async def portfolio(interaction: discord.Interaction):
                try:
                    await interaction.response.defer(ephemeral=False, thinking=True)
                except discord.InteractionResponded:
                    return
                except Exception as e:
                    LOG.exception("Portfolio: defer failed: %s", e)
                    return
                try:
                    embed_data = await asyncio.to_thread(_build_portfolio_embed, SKILL_DIR)
                    embed = discord.Embed.from_dict(embed_data)
                    await interaction.followup.send(embed=embed, ephemeral=False)
                except Exception as e:
                    LOG.exception("Portfolio command failed: %s", e)
                    try:
                        await interaction.followup.send(_friendly_error("Portfolio lookup failed."), ephemeral=False)
                    except Exception:
                        pass

            @self.tree.command(name="sectors", description="Show sector heatmap vs SPY")
            async def sectors(interaction: discord.Interaction):
                try:
                    await interaction.response.defer(ephemeral=False, thinking=True)
                except discord.InteractionResponded:
                    return
                except Exception as e:
                    LOG.exception("Sectors: defer failed: %s", e)
                    return
                try:
                    embed_data = await asyncio.to_thread(_build_sectors_embed, SKILL_DIR)
                    embed = discord.Embed.from_dict(embed_data)
                    await interaction.followup.send(embed=embed, ephemeral=False)
                except Exception as e:
                    LOG.exception("Sectors command failed: %s", e)
                    try:
                        await interaction.followup.send(_friendly_error("Sector lookup failed."), ephemeral=False)
                    except Exception:
                        pass

            @self.tree.command(name="status", description="Show bot health, API status, and last scan info")
            async def status(interaction: discord.Interaction):
                try:
                    await interaction.response.defer(ephemeral=False, thinking=True)
                except discord.InteractionResponded:
                    return
                except Exception as e:
                    LOG.exception("Status: defer failed: %s", e)
                    return
                try:
                    embed_data = await asyncio.to_thread(_build_status_embed, SKILL_DIR)
                    embed = discord.Embed.from_dict(embed_data)
                    await interaction.followup.send(embed=embed, ephemeral=False)
                except Exception as e:
                    LOG.exception("Status command failed: %s", e)
                    try:
                        await interaction.followup.send(_friendly_error("Status check failed."), ephemeral=False)
                    except Exception:
                        pass

            @self.tree.command(name="report", description="Generate a full financial report for a ticker")
            @app_commands.describe(
                ticker="Stock ticker symbol (e.g. AAPL)",
                section="Optional: tech, dcf, comps, health, edgar, mirofish",
                skip_mirofish="Skip MiroFish LLM simulation (faster)",
                skip_edgar="Skip SEC EDGAR filing lookup",
            )
            async def report(
                interaction: discord.Interaction,
                ticker: str,
                section: str | None = None,
                skip_mirofish: bool = False,
                skip_edgar: bool = False,
            ):
                try:
                    await interaction.response.defer(ephemeral=False, thinking=True)
                except discord.InteractionResponded:
                    return
                except Exception as e:
                    LOG.exception("Report: defer failed: %s", e)
                    return

                channel = interaction.channel

                try:
                    from full_report import generate_full_report, report_to_discord_sections

                    def do_report():
                        return generate_full_report(
                            ticker,
                            skip_mirofish=skip_mirofish,
                            skip_edgar=skip_edgar,
                        )

                    result = await asyncio.to_thread(do_report)
                    embeds_raw = report_to_discord_sections(result, section_filter=section)
                    embeds = [discord.Embed.from_dict(e) for e in embeds_raw]

                    if not embeds:
                        await interaction.followup.send(
                            f"Report for **{ticker.upper()}** produced no sections.",
                            ephemeral=False,
                        )
                        return

                    for i in range(0, len(embeds), 10):
                        batch = embeds[i:i + 10]
                        await interaction.followup.send(embeds=batch, ephemeral=False)

                except Exception as e:
                    LOG.exception("Report command failed: %s", e)
                    try:
                        await interaction.followup.send(
                            _friendly_error(f"Report failed for **{ticker.upper()}**."),
                            ephemeral=False,
                        )
                    except Exception as e2:
                        LOG.warning("Report error followup failed: %s", e2)
                        if channel:
                            try:
                                await channel.send(f"Report failed for **{ticker.upper()}**: {e}")
                            except Exception:
                                pass

        async def on_message(self, message):
            if message.author.bot:
                return
            channel_id = int(env.get("DISCORD_CONFIRM_CHANNEL_ID", "0"))
            if not channel_id or message.channel.id != channel_id:
                return
            text = (message.content or "").strip()
            if not text.startswith("!"):
                return

            parts = text[1:].split()
            cmd = parts[0].lower() if parts else ""
            args = parts[1:]

            if cmd == "ping":
                await message.reply("Pong - bot is online.")

            elif cmd == "help":
                help_text = (
                    "**Available commands:**\n"
                    "`!ping` -- Check the bot is online\n"
                    "`!status` -- Bot health, API status, last scan\n"
                    "`!check TICKER` -- Quick 3-line verdict (fast)\n"
                    "`!report TICKER [section]` -- Full report (or just one section: tech, dcf, comps, health, edgar, mirofish)\n"
                    "`!scan` -- Run signal scanner (Stage 2 + VCP)\n"
                    "`!portfolio` -- Show positions with P/L\n"
                    "`!sectors` -- Sector heatmap vs SPY\n"
                    "`!help` -- Show this message"
                )
                await message.reply(help_text)

            elif cmd == "status":
                try:
                    embed_data = await asyncio.to_thread(_build_status_embed, SKILL_DIR)
                    embed = discord.Embed.from_dict(embed_data)
                    await message.reply(embed=embed)
                except Exception as e:
                    LOG.exception("!status command failed: %s", e)
                    await message.reply(f"Status check failed: {e}")

            elif cmd == "check":
                if not args:
                    await message.reply("Usage: `!check TICKER`")
                    return
                ticker = args[0].upper()
                try:
                    from full_report import quick_check
                    embed_data = await asyncio.to_thread(quick_check, ticker)
                    embed = discord.Embed.from_dict(embed_data)
                    await message.reply(embed=embed)
                except Exception as e:
                    LOG.exception("!check command failed: %s", e)
                    await message.reply(f"Check failed for **{ticker}**: {e}")

            elif cmd == "portfolio":
                status_msg = await message.reply("Fetching portfolio...")
                try:
                    embed_data = await asyncio.to_thread(_build_portfolio_embed, SKILL_DIR)
                    embed = discord.Embed.from_dict(embed_data)
                    await status_msg.edit(content=None, embed=embed)
                except Exception as e:
                    LOG.exception("!portfolio command failed: %s", e)
                    await status_msg.edit(content=f"Portfolio failed: {e}")

            elif cmd == "sectors":
                status_msg = await message.reply("Building sector heatmap...")
                try:
                    embed_data = await asyncio.to_thread(_build_sectors_embed, SKILL_DIR)
                    embed = discord.Embed.from_dict(embed_data)
                    await status_msg.edit(content=None, embed=embed)
                except Exception as e:
                    LOG.exception("!sectors command failed: %s", e)
                    await status_msg.edit(content=f"Sectors failed: {e}")

            elif cmd == "report":
                if not args:
                    await message.reply("Usage: `!report TICKER [section]`\nSections: tech, dcf, comps, health, edgar, mirofish")
                    return
                ticker = args[0].upper()
                section_filter = args[1].lower() if len(args) > 1 else None
                label = f"**{ticker}** ({section_filter})" if section_filter else f"**{ticker}**"
                status_msg = await message.reply(f"Generating report for {label}...")
                try:
                    from full_report import generate_full_report, report_to_discord_sections

                    def do_report():
                        return generate_full_report(ticker)

                    result = await asyncio.to_thread(do_report)
                    embeds_raw = report_to_discord_sections(result, section_filter=section_filter)
                    embeds = [discord.Embed.from_dict(e) for e in embeds_raw]

                    if not embeds:
                        await status_msg.edit(content=f"Report for {label} produced no sections.")
                        return

                    await status_msg.edit(content=f"**Report -- {ticker}**")
                    for i in range(0, len(embeds), 10):
                        batch = embeds[i:i + 10]
                        await message.channel.send(embeds=batch)

                except Exception as e:
                    LOG.exception("!report command failed: %s", e)
                    await status_msg.edit(content=f"Report failed for {label}: {e}")

            elif cmd == "scan":
                status_msg = await message.reply("Running signal scan... this may take a minute.")
                try:
                    from signal_scanner import run_scan_and_notify

                    def do_scan():
                        return run_scan_and_notify(skill_dir=SKILL_DIR, send_summary=False)

                    n = await asyncio.to_thread(do_scan)
                    from datetime import datetime
                    from zoneinfo import ZoneInfo
                    _now = datetime.now(ZoneInfo("America/New_York"))
                    _last_scan_info["time"] = _now.strftime("%b %d %I:%M %p ET")
                    _last_scan_info["signals"] = n
                    await status_msg.edit(
                        content=f"**Scan complete.** Found **{n}** signal(s). Check the channel for alerts.",
                    )
                except Exception as e:
                    LOG.exception("!scan command failed: %s", e)
                    await status_msg.edit(content=_friendly_error("Scan failed."))

            else:
                await message.reply("Unknown command. Type `!help` for available commands.")

        async def on_ready(self):
            global _bot_start_time
            import time as _time
            _bot_start_time = _time.time()
            channel_id = int(env.get("DISCORD_CONFIRM_CHANNEL_ID", "0"))
            if channel_id:
                LOG.info("Discord confirm bot ready: %s", self.user)
                _bot_ready.set()
            else:
                LOG.warning("DISCORD_CONFIRM_CHANNEL_ID not set, confirmations disabled")
            asyncio.create_task(self._process_queue())
            asyncio.create_task(self._sync_slash_commands())

        async def _sync_slash_commands(self) -> None:
            try:
                guild_id = env.get("DISCORD_GUILD_ID", "").strip()
                if guild_id:
                    guild = discord.Object(id=int(guild_id))
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                    LOG.info("Slash commands synced to guild %s", guild_id)
                else:
                    await self.tree.sync()
                    LOG.info("Slash commands synced globally")
            except Exception as e:
                LOG.warning("Slash command sync failed: %s", e)

        async def _process_queue(self):
            channel_id = int(env.get("DISCORD_CONFIRM_CHANNEL_ID", "0"))
            if not channel_id:
                LOG.warning("DISCORD_CONFIRM_CHANNEL_ID not set, confirmations disabled")
                return
            channel = self.get_channel(channel_id)
            if not channel:
                channel = await self.fetch_channel(channel_id)

            def get_from_queue():
                return _confirmation_queue.get(timeout=2)

            while not self.is_closed():
                try:
                    signal, skill_dir = await asyncio.to_thread(get_from_queue)
                except queue.Empty:
                    await asyncio.sleep(0.5)
                    continue
                except RuntimeError as e:
                    # During shutdown, asyncio may refuse to schedule new work on the
                    # executor/event loop, causing: "cannot schedule new futures after shutdown".
                    # Treat this as a normal exit condition.
                    if "cannot schedule new futures after shutdown" in str(e):
                        LOG.info("Confirm queue worker exiting due to shutdown: %s", e)
                        return
                    raise
                except asyncio.CancelledError:
                    return
                t = signal["ticker"]
                p = signal["price"]
                sector = signal["sector_etf"]
                label = signal.get("_alert_label", "")
                # Never block the event loop: sizing can hit Schwab (seconds) and causes
                # "The application did not respond" on /scan and Approve/Reject.
                from execution import get_position_size_usd

                def _qty_for_signal() -> int:
                    pos_usd = get_position_size_usd(ticker=t, price=p, skill_dir=skill_dir)
                    return max(1, int(pos_usd / p)) if p > 0 else 10

                qty = await asyncio.to_thread(_qty_for_signal)
                trade_id = str(uuid.uuid4())[:8]
                with _lock:
                    _pending[trade_id] = {
                        "ticker": t, "qty": qty, "skill_dir": skill_dir,
                        "signal": signal, "price": p, "_mock": signal.get("_mock", False),
                    }
                title = "[Mock] Signal Confirmation" if signal.get("_mock") else "Signal Confirmation"
                embed = discord.Embed(
                    title=title,
                    description=f"**Buy {t}** {label}\nStage 2 + VCP breakout in a winning sector. Review details below.",
                    color=0x3498DB,
                )
                order_value = qty * p
                embed.add_field(name="Order", value=f"**{qty}** shares @ **${p:,.2f}**\nEst. value: **${order_value:,.2f}**", inline=True)
                sma50 = signal.get("sma_50")
                sma200 = signal.get("sma_200")
                sma50_str = f"${sma50:,.2f}" if isinstance(sma50, (int, float)) else "—"
                sma200_str = f"${sma200:,.2f}" if isinstance(sma200, (int, float)) else "—"
                embed.add_field(name="Technical", value=(
                    f"SMA 50: {sma50_str}\n"
                    f"SMA 200: {sma200_str}\n"
                    f"Sector: **{sector}**"
                ), inline=True)
                setup_score = signal.get("signal_score")
                if setup_score is not None:
                    embed.add_field(name="Setup Score", value=f"**{setup_score:.0f}/100**", inline=True)
                embed.set_footer(text="Approve to execute market order • Reject to cancel")

                # MiroFish embed: Conviction Meter + influential agent sentiment
                mirofish_result = signal.get("mirofish_result")
                mirofish_embed = _build_mirofish_embed(mirofish_result)
                embeds_to_send = [embed]
                if mirofish_embed:
                    embeds_to_send.append(mirofish_embed)

                view = TradeConfirmView(trade_id)
                try:
                    sent = await channel.send(embeds=embeds_to_send, view=view)
                    view.message = sent
                    from alert_history import record_alert_sent
                    record_alert_sent(t, skill_dir)
                    LOG.info("Sent confirm prompt to channel %s for %s", channel_id, t)
                except Exception as e:
                    LOG.warning(
                        "Failed to send confirm message (channel %s, ticker %s). "
                        "Check: bot has Send Messages + Embed Links in that channel. Error: %s",
                        channel_id, t, e,
                    )
                    with _lock:
                        _pending.pop(trade_id, None)

    token = env.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        LOG.warning("DISCORD_BOT_TOKEN not set, trade confirmation bot disabled")
        return
    client = ConfirmBot()
    try:
        client.run(token)
    except Exception as e:
        LOG.warning("Discord confirm bot failed (invalid token?): %s", e)


def start_confirm_bot(env_path: Path | None = None) -> threading.Thread | None:
    """Start the Discord confirmation bot in a background thread. Idempotent - safe to call multiple times."""
    global _bot_started
    if _bot_started:
        return None
    env = _load_env(env_path or SKILL_DIR / ".env")
    if not env.get("DISCORD_BOT_TOKEN", "").strip() or not env.get("DISCORD_CONFIRM_CHANNEL_ID", "").strip():
        return None
    _bot_started = True
    for k, v in env.items():
        if k not in os.environ:
            os.environ[k] = v
    thread = threading.Thread(
        target=lambda: _run_bot_async(env),
        daemon=True,
        name="DiscordConfirmBot",
    )
    thread.start()
    return thread


def ensure_bot_ready(timeout: float = 15.0) -> bool:
    """Ensure confirm bot is running. Starts it if needed. Returns True when ready."""
    env = _load_env(SKILL_DIR / ".env")
    if not env.get("DISCORD_BOT_TOKEN", "").strip():
        return False
    if _bot_ready.is_set():
        return True
    start_confirm_bot(SKILL_DIR / ".env")
    return _bot_ready.wait(timeout=timeout)
