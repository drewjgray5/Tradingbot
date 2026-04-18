"""Static-asset checks for ``webapp/static/modules/router.js``.

We don't have a JS test runner wired into the project, so these are
lightweight contract checks against the source file itself:

* The exported alias map matches the friendly-name -> DOM-id contract
  used by docs / marketing emails (``?section=backtest`` etc.). If
  someone renames a section without updating the alias map, the test
  flags it.
* ``app.js`` no longer carries its own copy of the helpers — that's
  the whole point of #7 in the website improvement plan; if the
  helpers leak back inline we want to know about it.
* The router module is referenced by ``app.js`` (catches accidental
  delete of the import block).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
ROUTER_JS = STATIC / "modules" / "router.js"
APP_JS = STATIC / "app.js"


@pytest.fixture(scope="module")
def router_source() -> str:
    return ROUTER_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_source() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _parse_alias_map(source: str) -> dict[str, str]:
    """Pull the literal SECTION_ALIASES object out of router.js.

    Tiny regex-based extractor: the object body is a flat key/value
    map of plain string literals, so a full JS parser would be
    overkill. If a future edit introduces nested objects or computed
    keys, this helper will deliberately fail so the test gets updated
    alongside the schema.
    """
    match = re.search(
        r"export const SECTION_ALIASES = Object\.freeze\(\{(.*?)\}\)",
        source,
        re.DOTALL,
    )
    assert match, "SECTION_ALIASES literal block not found in router.js"
    body = match.group(1)
    pairs = re.findall(r'(\w+)\s*:\s*"([^"]+)"', body)
    assert pairs, "SECTION_ALIASES body parsed to zero entries"
    return {k: v for k, v in pairs}


# Friendly-name -> DOM-id contract. These are the URL-safe shortcuts we
# hand out in onboarding emails / docs / Stripe success pages and must
# stay backwards compatible. Updating the dashboard sections also means
# updating this map AND this test.
EXPECTED_ALIASES = {
    "backtest": "backtestSection",
    "backtests": "backtestSection",
    "pending": "pendingSection",
    "trades": "pendingSection",
    "scan": "workflowPrimary",
    "workflow": "workflowPrimary",
    "connect": "onboardingSection",
    "onboarding": "onboardingSection",
    "setup": "onboardingSection",
}


def test_router_alias_map_matches_contract(router_source: str) -> None:
    assert _parse_alias_map(router_source) == EXPECTED_ALIASES


def test_router_exposes_public_surface(router_source: str) -> None:
    """The four pieces app.js depends on must be exported."""
    for name in (
        "export function handleRouteHash",
        "export function applyQuerySectionDeepLink",
        "export function clearOAuthQueryParams",
        "export function installRouter",
        "export function resolveSectionAlias",
        "export const SECTION_ALIASES",
    ):
        assert name in router_source, f"router.js missing public export: {name!r}"


def test_app_js_uses_router_module(app_source: str) -> None:
    assert "./modules/router.js" in app_source, (
        "app.js no longer imports the router module; the routing helpers "
        "are supposed to live in modules/router.js after #7 of the "
        "website improvement plan."
    )
    assert "installRouter()" in app_source, (
        "app.js boot sequence should call installRouter() from router.js"
    )


def test_app_js_does_not_redefine_router_helpers(app_source: str) -> None:
    """Guardrail against the helpers drifting back into app.js."""
    forbidden = [
        # The whole point of the extraction: these definitions must not
        # come back inline in app.js.
        "function handleRouteHash(",
        "function applyQuerySectionDeepLink(",
        "function openAncestorDetails(",
    ]
    for needle in forbidden:
        assert needle not in app_source, (
            f"{needle!r} reappeared in app.js — should live in "
            "modules/router.js instead."
        )


def test_app_js_no_longer_inlines_oauth_query_cleanup(app_source: str) -> None:
    """The OAuth callback cleanup was the third inline bit of router-ish
    logic. After #7 it should go through ``clearOAuthQueryParams`` so
    every history.replaceState call lives in one module."""
    assert "clearOAuthQueryParams(" in app_source
    # The old multi-line replaceState block had this exact tail; a
    # regression would re-introduce it.
    assert (
        'window.history.replaceState({}, "", u.pathname + (u.search ? u.search : ""))'
        not in app_source
    ), "old inline OAuth replaceState block re-introduced in app.js"
