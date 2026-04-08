"""
Schwab API client - single entry point for OpenClaw.

All trade requests go through execution.place_order (consolidated).
Uses DualSchwabAuth (Market + Account sessions).
"""

from pathlib import Path

from execution import GuardrailWrapper, get_account_status
from schwab_auth import DualSchwabAuth

# Default skill directory (where .env, tokens.enc, etc. live)
SKILL_DIR = Path(__file__).resolve().parent


def get_client() -> "ClientFacade":
    """Return client facade. Use place_order via execution.place_order for trades."""
    auth = DualSchwabAuth(skill_dir=SKILL_DIR)
    return ClientFacade(auth, SKILL_DIR)


class ClientFacade:
    """Unified client: get_accounts, place_order delegates to execution."""

    def __init__(self, auth: DualSchwabAuth, skill_dir: Path):
        self.auth = auth
        self.skill_dir = Path(skill_dir)
        self._wrapper = GuardrailWrapper(auth, skill_dir)

    def get_accounts(self) -> dict | str:
        """Fetch accounts with positions."""
        result = get_account_status(auth=self.auth, skill_dir=self.skill_dir)
        if isinstance(result, str):
            return result
        return {"accounts": result["accounts"], "account_ids": result["account_ids"]}

    def place_order(
        self,
        ticker: str,
        qty: int,
        side: str = "BUY",
        order_type: str = "MARKET",
        limit_price: float | None = None,
    ) -> dict | str:
        """Place order via execution (guardrails, sector filter, fill monitor)."""
        from execution import place_order
        return place_order(ticker, qty, side, order_type, limit_price, auth=self.auth, skill_dir=self.skill_dir)
