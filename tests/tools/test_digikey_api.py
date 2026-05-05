"""Tests for tools/digikey_api.py.

Network is stubbed via mock_open on urllib.request.urlopen — we never
hit the real DigiKey API.
"""
from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from tools import digikey_api
from tools.digikey_api import PartInfo, is_configured, lookup, reset_cache, reset_rate_limit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_cache()
    reset_rate_limit()
    yield
    reset_cache()
    reset_rate_limit()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "test-client")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("DIGIKEY_API_URL", "https://api.digikey.com/v3")


def _mock_urlopen(*side_effects):
    """Patch urlopen to return a sequence of `bytes | Exception` values.
    Each call pops the next value — bytes become HTTP-200 bodies.
    """
    call_iter = iter(side_effects)

    class _Ctx:
        def __init__(self, payload):
            self._payload = payload
        def read(self):
            return self._payload
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _open(*_a, **_k):
        nxt = next(call_iter)
        if isinstance(nxt, Exception):
            raise nxt
        return _Ctx(nxt if isinstance(nxt, bytes) else nxt.encode("utf-8"))

    return patch("tools.digikey_api.urllib.request.urlopen", side_effect=_open)


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------

def test_not_configured_when_env_missing(monkeypatch):
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    assert is_configured() is False


def test_configured_when_both_keys_set(configured):
    assert is_configured() is True


def test_lookup_returns_none_when_not_configured(monkeypatch):
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    assert lookup("ADL8107") is None


# ---------------------------------------------------------------------------
# OAuth token fetch
# ---------------------------------------------------------------------------

def test_token_fetch_caches_and_reuses(configured):
    token_resp = json.dumps({"access_token": "tok-123", "expires_in": 3600})
    product_resp = json.dumps({
        "ProductDetails": {
            "ManufacturerPartNumber": "ADL8107",
            "Manufacturer": {"Value": "Analog Devices"},
            "ProductDescription": "Wideband LNA 2-18 GHz",
            "ProductStatus": {"Status": "Active"},
            "PrimaryDatasheet": "https://www.analog.com/en/products/adl8107.html",
            "ProductUrl": "https://www.digikey.com/en/products/detail/ADL8107",
            "UnitPrice": 24.0,
            "QuantityAvailable": 125,
        }
    })
    # One token call + two lookups → still only one token request
    # because of the in-process cache.
    with _mock_urlopen(token_resp, product_resp, product_resp) as mock_open_fn:
        info1 = lookup("ADL8107")
        info2 = lookup("ADL8107")
    assert mock_open_fn.call_count == 3  # 1 token + 2 lookup calls
    assert info1 is not None and info2 is not None
    assert info1.part_number == "ADL8107"
    assert info1.manufacturer == "Analog Devices"
    assert info1.lifecycle_status == "active"
    assert info1.source == "digikey"


def test_token_fetch_failure_returns_none(configured):
    with _mock_urlopen(urllib.error.URLError("connection refused")):
        assert lookup("ADL8107") is None


def test_token_fetch_missing_access_token_returns_none(configured):
    bad = json.dumps({"token_type": "Bearer"})  # no access_token
    with _mock_urlopen(bad):
        assert lookup("ADL8107") is None


# ---------------------------------------------------------------------------
# Lookup outcomes
# ---------------------------------------------------------------------------

def test_lookup_404_returns_none(configured):
    token_resp = json.dumps({"access_token": "tok", "expires_in": 3600})
    http_404 = urllib.error.HTTPError(
        "url", 404, "not found", {}, io.BytesIO(b"")
    )
    with _mock_urlopen(token_resp, http_404):
        assert lookup("HALLUCINATED-MPN") is None


def test_lookup_401_resets_token_cache(configured):
    token_resp = json.dumps({"access_token": "tok-old", "expires_in": 3600})
    http_401 = urllib.error.HTTPError(
        "url", 401, "unauthorised", {}, io.BytesIO(b"")
    )
    with _mock_urlopen(token_resp, http_401):
        assert lookup("ADL8107") is None
    # Cache cleared → next call tries to fetch a new token.
    assert digikey_api._cached_token["access_token"] is None


@pytest.mark.parametrize("status,expected", [
    ("Active", "active"),
    ("Active / Preferred", "active"),
    ("Last Time Buy", "nrnd"),
    ("Not Recommended for New Designs", "nrnd"),
    ("Obsolete Available", "nrnd"),
    ("Discontinued", "obsolete"),
    ("", "unknown"),
])
def test_lifecycle_status_mapping(configured, status, expected):
    token_resp = json.dumps({"access_token": "tok", "expires_in": 3600})
    product_resp = json.dumps({
        "ProductDetails": {
            "ManufacturerPartNumber": "X",
            "Manufacturer": {"Value": "Y"},
            "ProductStatus": {"Status": status},
        }
    })
    with _mock_urlopen(token_resp, product_resp):
        info = lookup("X")
    assert info is not None
    assert info.lifecycle_status == expected


def test_unexpected_response_shape_returns_none(configured):
    token_resp = json.dumps({"access_token": "tok", "expires_in": 3600})
    garbage = json.dumps(["not", "a", "dict"])
    with _mock_urlopen(token_resp, garbage):
        assert lookup("X") is None


def test_empty_part_number_returns_none(configured):
    assert lookup("") is None
    assert lookup(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# URL shape — regression for the v3/v1/v4 endpoint confusion
# ---------------------------------------------------------------------------

def _capture_urls(*side_effects):
    """Variant of _mock_urlopen that records the URL of every request."""
    call_iter = iter(side_effects)
    captured: list[str] = []

    class _Ctx:
        def __init__(self, payload):
            self._payload = payload
        def read(self):
            return self._payload
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _open(req, *_a, **_k):
        # req may be a Request object or a raw URL string
        url = req.full_url if hasattr(req, "full_url") else str(req)
        captured.append(url)
        nxt = next(call_iter)
        if isinstance(nxt, Exception):
            raise nxt
        return _Ctx(nxt if isinstance(nxt, bytes) else nxt.encode("utf-8"))

    return patch("tools.digikey_api.urllib.request.urlopen", side_effect=_open), captured


@pytest.mark.parametrize("api_url_env", [
    "https://api.digikey.com",
    "https://api.digikey.com/v3",       # legacy value users may still have in .env
    "https://api.digikey.com/v1",
    "https://api.digikey.com/products/v4",
])
def test_oauth_token_url_is_always_v1(monkeypatch, api_url_env):
    """OAuth lives at /v1/oauth2/token regardless of what DIGIKEY_API_URL
    holds. This is the regression lock for the silent-404 bug where a
    stale `/v3` env var made every token fetch fail."""
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "cid")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "cs")
    monkeypatch.setenv("DIGIKEY_API_URL", api_url_env)
    token_resp = json.dumps({"access_token": "tok", "expires_in": 3600})
    product_resp = json.dumps({
        "ProductDetails": {
            "ManufacturerPartNumber": "ADL8107",
            "Manufacturer": {"Value": "Analog Devices"},
            "ProductStatus": {"Status": "Active"},
        }
    })
    patcher, urls = _capture_urls(token_resp, product_resp)
    with patcher:
        lookup("ADL8107")
    assert urls[0] == "https://api.digikey.com/v1/oauth2/token", (
        f"OAuth URL must anchor at /v1 — got {urls[0]!r}"
    )


@pytest.mark.parametrize("api_url_env", [
    "https://api.digikey.com",
    "https://api.digikey.com/v3",
    "https://api.digikey.com/products/v4",
])
def test_product_details_url_uses_v4(monkeypatch, api_url_env):
    """Product Information API is v4: the path is
    /products/v4/search/{mpn}/productdetails, regardless of the env var."""
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "cid")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "cs")
    monkeypatch.setenv("DIGIKEY_API_URL", api_url_env)
    token_resp = json.dumps({"access_token": "tok", "expires_in": 3600})
    product_resp = json.dumps({
        "ProductDetails": {
            "ManufacturerPartNumber": "ADL8107",
            "Manufacturer": {"Value": "Analog Devices"},
            "ProductStatus": {"Status": "Active"},
        }
    })
    patcher, urls = _capture_urls(token_resp, product_resp)
    with patcher:
        lookup("ADL8107")
    assert urls[1] == (
        "https://api.digikey.com/products/v4/search/ADL8107/productdetails"
    ), f"Product URL must use /products/v4 — got {urls[1]!r}"


def test_v4_nested_description_is_extracted(configured):
    """DigiKey v4 nests Description as an object —
    {'ProductDescription': ..., 'DetailedDescription': ...}. The parser
    must unwrap it, otherwise PartInfo.description stays blank for every
    v4 response. Regression for CL05B104KP5NNNC lookup."""
    token_resp = json.dumps({"access_token": "tok", "expires_in": 3600})
    product_resp = json.dumps({
        "Product": {
            "ManufacturerProductNumber": "CL05B104KP5NNNC",
            "Manufacturer": {"Name": "Samsung Electro-Mechanics"},
            "Description": {
                "ProductDescription": "CAP CER 0.1UF 10V X7R 0402",
                "DetailedDescription": "0.1 µF ±10% 10V Ceramic Capacitor X7R 0402",
            },
            "DatasheetUrl": "//mm.digikey.com/foo/CL05B104KP5NNNC_Spec.pdf",
            "ProductStatus": {"Status": "Active"},
            "UnitPrice": 0.1,
            "QuantityAvailable": 27841252,
        }
    })
    with _mock_urlopen(token_resp, product_resp):
        info = lookup("CL05B104KP5NNNC")
    assert info is not None
    assert info.description == "CAP CER 0.1UF 10V X7R 0402"
    # Protocol-relative `//mm.digikey.com/...` must be normalised to https.
    assert info.datasheet_url == "https://mm.digikey.com/foo/CL05B104KP5NNNC_Spec.pdf"
    assert info.manufacturer == "Samsung Electro-Mechanics"
    assert info.lifecycle_status == "active"
    assert info.unit_price_usd == 0.1
    assert info.stock_quantity == 27841252


def test_v3_flat_description_still_works(configured):
    """Old v3 shape where description was a flat string must still parse."""
    token_resp = json.dumps({"access_token": "tok", "expires_in": 3600})
    product_resp = json.dumps({
        "ProductDetails": {
            "ManufacturerPartNumber": "ADL8107",
            "Manufacturer": {"Value": "Analog Devices"},
            "ProductDescription": "Wideband LNA 2-18 GHz",
            "ProductStatus": {"Status": "Active"},
        }
    })
    with _mock_urlopen(token_resp, product_resp):
        info = lookup("ADL8107")
    assert info is not None
    assert info.description == "Wideband LNA 2-18 GHz"


def test_mpn_is_url_encoded(monkeypatch):
    """Part numbers containing reserved chars (`+`, `/`, `,`) must be
    percent-encoded so the URL is well-formed at DigiKey's edge."""
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "cid")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "cs")
    monkeypatch.setenv("DIGIKEY_API_URL", "https://api.digikey.com")
    token_resp = json.dumps({"access_token": "tok", "expires_in": 3600})
    product_resp = json.dumps({
        "ProductDetails": {
            "ManufacturerPartNumber": "ZFSC-2-1+",
            "Manufacturer": {"Value": "Mini-Circuits"},
            "ProductStatus": {"Status": "Active"},
        }
    })
    patcher, urls = _capture_urls(token_resp, product_resp)
    with patcher:
        lookup("ZFSC-2-1+")
    # '+' must be encoded as %2B — otherwise DigiKey sees it as a space.
    assert "%2B" in urls[1], f"'+' must be percent-encoded — got {urls[1]!r}"


# ---------------------------------------------------------------------------
# 429 circuit-breaker
# ---------------------------------------------------------------------------

def _http_429(retry_after: str = "24000") -> urllib.error.HTTPError:
    """Build a minimal HTTP 429 error with a Retry-After header."""
    headers = MagicMock()
    headers.get = lambda k, d="": retry_after if k == "Retry-After" else d
    err = urllib.error.HTTPError(
        url="https://api.digikey.com/token", code=429,
        msg="Too Many Requests", hdrs=headers, fp=None,
    )
    return err


def test_circuit_opens_after_first_429(configured, monkeypatch):
    """First 429 from DigiKey opens the circuit; subsequent keyword_search
    calls return [] without hitting the network again."""
    token_resp = json.dumps({"access_token": "tok", "expires_in": 3600})
    call_count = 0

    def _open(*_a, **_k):
        nonlocal call_count
        call_count += 1
        if call_count == 1:  # token fetch
            class _Ctx:
                def read(self): return token_resp.encode()
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return _Ctx()
        raise _http_429()

    with patch("tools.digikey_api.urllib.request.urlopen", side_effect=_open):
        from tools.digikey_api import keyword_search
        r1 = keyword_search("LNA 2-18 GHz")

    assert r1 == [], "first 429 should return empty list"
    assert digikey_api._is_rate_limited(), "circuit must be open after 429"

    # Second call must short-circuit without touching urlopen
    calls_before = call_count
    with patch("tools.digikey_api.urllib.request.urlopen") as mock_open:
        keyword_search("mixer 6 GHz")
        assert not mock_open.called, "circuit-open call must not hit urlopen"
    assert call_count == calls_before, "no extra urlopen calls while circuit is open"


def test_circuit_resets_after_window(configured, monkeypatch):
    """After `reset_rate_limit()` the circuit closes and the next call
    is allowed through.

    Originally tested via `_mark_rate_limited(0.001)` + `time.sleep(0.01)`,
    but that was flaky on Windows CI — sleep granularity is ~15 ms so
    a 1 ms window sometimes didn't elapse within the 10 ms sleep. Now
    we test the explicit `reset_rate_limit()` API, which is the code
    path callers actually depend on."""
    # Open the circuit for a long window so no natural expiry can help.
    digikey_api._mark_rate_limited(300)
    assert digikey_api._is_rate_limited()

    # Explicit reset → circuit closes.
    digikey_api.reset_rate_limit()
    assert not digikey_api._is_rate_limited()


def test_backoff_cap_is_5_seconds(configured, monkeypatch):
    """Per-attempt sleep must be capped at 5 s (not 30 s).  A 7-hour
    Retry-After must not make the test (or pipeline) sleep more than 5 s."""
    slept: list[float] = []
    monkeypatch.setattr("tools.digikey_api.time.sleep", lambda s: slept.append(s))

    token_resp = json.dumps({"access_token": "tok", "expires_in": 3600})
    call_count = 0

    def _open(*_a, **_k):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            class _Ctx:
                def read(self): return token_resp.encode()
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return _Ctx()
        raise _http_429("24000")  # DigiKey says wait 24000 s

    with patch("tools.digikey_api.urllib.request.urlopen", side_effect=_open):
        from tools.digikey_api import keyword_search
        keyword_search("LNA 2-18 GHz")

    assert slept, "should have slept at least once (one retry)"
    assert max(slept) <= 5.0, f"largest sleep must be <=5 s; got {max(slept)}"


def test_lookup_skips_when_circuit_open(configured):
    """lookup() returns None immediately when the circuit-breaker is open."""
    digikey_api._mark_rate_limited(300)   # open for 5 min
    with patch("tools.digikey_api.urllib.request.urlopen") as mock_open:
        result = lookup("ADL8107")
    assert result is None
    assert not mock_open.called, "lookup must not hit urlopen while circuit is open"
