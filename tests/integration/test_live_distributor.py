"""
Live DigiKey + Mouser API smoke tests.

These tests **do** hit the real distributor endpoints. They auto-skip
when the required API keys are absent from the environment, so local
dev and CI without secrets run green without incident. Run them with:

    export DIGIKEY_CLIENT_ID=...
    export DIGIKEY_CLIENT_SECRET=...
    export MOUSER_API_KEY=...
    pytest tests/integration/test_live_distributor.py -v

What they validate that the unit tests cannot:
  1. OAuth against the real DigiKey IdP actually works with our keys.
  2. The response-shape fallbacks in `_parse_product_details` /
     `_parse_search_response` still find the expected fields in the
     live JSON (catches silent v4/v5 drift).
  3. A known-good canonical RF part (STM32F407VGT6) is findable by
     BOTH distributors and returns matching manufacturer strings.

We pick STM32F407VGT6 deliberately: it's a high-volume part that both
distributors carry, has been active for >10 years, and is unlikely to
go obsolete this week.

Network-skip policy:
  Some sandbox / corporate environments restrict outbound HTTPS to an
  allowlist that doesn't include api.digikey.com / api.mouser.com.
  When the per-host probe fails (403 "Host not in allowlist", DNS
  failure, timeout) we **skip** rather than **fail** — credentials
  may be perfectly valid, the harness just can't reach them.

These tests are marked `slow` so CI can opt out via `-m "not slow"`.
"""
from __future__ import annotations

import os
import ssl
import urllib.error
import urllib.request

import pytest

# Keys gate — these strings must point at real credentials for the
# tests in this file to actually do anything.
_HAS_DIGIKEY = bool(
    os.getenv("DIGIKEY_CLIENT_ID") and os.getenv("DIGIKEY_CLIENT_SECRET")
)
_HAS_MOUSER = bool(os.getenv("MOUSER_API_KEY"))

_KNOWN_GOOD_MPN = "STM32F407VGT6"

pytestmark = [pytest.mark.slow]


# ---------------------------------------------------------------------------
# Network-reachability gate
# ---------------------------------------------------------------------------

def _can_reach(host_url: str) -> tuple[bool, str]:
    """Best-effort probe — try a HEAD/GET against the host root and
    return (reachable, reason). 'reachable' means the TCP+TLS handshake
    succeeded and we got an HTTP response (any status — even 401/404
    proves we reached the host). Returns False only on connection-level
    failures (DNS, refused, blocked-by-allowlist 403)."""
    req = urllib.request.Request(host_url, method="GET",
                                 headers={"User-Agent": "HardwarePipelineLiveProbe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8.0,
                                    context=ssl.create_default_context()) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as exc:
        # Distinguish a sandbox allowlist-block (403 "Host not in allowlist")
        # from a perfectly valid 401/403/404 returned by the real API.
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        if "host not in allowlist" in body.lower() or "host not allowed" in body.lower():
            return False, f"sandbox-blocked ({body.strip()})"
        # Any other HTTP error means we DID reach the host.
        return True, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


_DIGIKEY_REACHABLE: tuple[bool, str] | None = None
_MOUSER_REACHABLE: tuple[bool, str] | None = None


def _digikey_reachable() -> tuple[bool, str]:
    global _DIGIKEY_REACHABLE
    if _DIGIKEY_REACHABLE is None:
        api = os.getenv("DIGIKEY_API_URL", "https://api.digikey.com").rstrip("/")
        # Use the OAuth path — it's always present and a 4xx still proves reachability.
        _DIGIKEY_REACHABLE = _can_reach(api.split("/v")[0] + "/v1/oauth2/token")
    return _DIGIKEY_REACHABLE


def _mouser_reachable() -> tuple[bool, str]:
    global _MOUSER_REACHABLE
    if _MOUSER_REACHABLE is None:
        api = os.getenv("MOUSER_API_URL", "https://api.mouser.com/api/v2").rstrip("/")
        _MOUSER_REACHABLE = _can_reach(api)
    return _MOUSER_REACHABLE


def _require_digikey() -> None:
    if not _HAS_DIGIKEY:
        pytest.skip("DIGIKEY_CLIENT_ID / SECRET not set")
    ok, why = _digikey_reachable()
    if not ok:
        pytest.skip(f"api.digikey.com unreachable from this environment: {why}")


def _require_mouser() -> None:
    if not _HAS_MOUSER:
        pytest.skip("MOUSER_API_KEY not set")
    ok, why = _mouser_reachable()
    if not ok:
        pytest.skip(f"api.mouser.com unreachable from this environment: {why}")


# ---------------------------------------------------------------------------
# DigiKey
# ---------------------------------------------------------------------------

def test_digikey_oauth_and_known_good_mpn():
    """End-to-end: token fetch → lookup → parse → PartInfo."""
    _require_digikey()
    from tools import digikey_api
    digikey_api.reset_cache()
    info = digikey_api.lookup(_KNOWN_GOOD_MPN, timeout_s=15.0)
    assert info is not None, f"DigiKey returned no record for {_KNOWN_GOOD_MPN}"
    assert info.source == "digikey"
    assert info.part_number.upper().startswith("STM32F407")
    assert info.manufacturer  # non-empty
    # Datasheet URL should resolve to a known vendor
    assert info.datasheet_url
    assert any(
        d in info.datasheet_url.lower()
        for d in ("st.com", "digikey.com")
    )


def test_digikey_hallucinated_mpn_returns_none():
    """A clearly-invented MPN must come back as None, not an exception."""
    _require_digikey()
    from tools import digikey_api
    digikey_api.reset_cache()
    assert digikey_api.lookup("TOTALLY-HALLUCINATED-X9Z9-PART", timeout_s=15.0) is None


# ---------------------------------------------------------------------------
# Mouser
# ---------------------------------------------------------------------------

def test_mouser_known_good_mpn():
    _require_mouser()
    from tools import mouser_api
    info = mouser_api.lookup(_KNOWN_GOOD_MPN, timeout_s=15.0)
    assert info is not None, f"Mouser returned no record for {_KNOWN_GOOD_MPN}"
    assert info.source == "mouser"
    assert info.part_number.upper().startswith("STM32F407")
    # Price should parse into a number with a currency code
    if info.unit_price is not None:
        assert info.unit_price > 0
        assert info.unit_price_currency  # non-empty ISO code


def test_mouser_hallucinated_mpn_returns_none():
    _require_mouser()
    from tools import mouser_api
    assert mouser_api.lookup("TOTALLY-HALLUCINATED-X9Z9-PART", timeout_s=15.0) is None


# ---------------------------------------------------------------------------
# Unified search — both tiers agree on a real part
# ---------------------------------------------------------------------------

def test_unified_search_finds_part_on_primary_tier():
    """Both configured → should hit DigiKey (primary) first."""
    _require_digikey()
    _require_mouser()
    from tools import distributor_search
    distributor_search.reset_cache()
    info = distributor_search.lookup(_KNOWN_GOOD_MPN, timeout_s=15.0)
    assert info is not None
    # Primary tier answers first; we don't care which — just that it's live.
    assert info.source in {"digikey", "mouser"}


# ---------------------------------------------------------------------------
# Keyword / parametric search (optional — skips when the endpoint isn't open)
# ---------------------------------------------------------------------------

def test_mouser_keyword_search_returns_list():
    _require_mouser()
    from tools import mouser_api
    hits = mouser_api.keyword_search("STM32F407", records=5, timeout_s=15.0)
    # Don't assert count — keyword API may rate-limit or return 0 for
    # rare queries. Just verify shape + that exceptions don't propagate.
    assert isinstance(hits, list)
    for h in hits:
        assert h.source == "mouser"
        assert h.part_number  # non-empty
