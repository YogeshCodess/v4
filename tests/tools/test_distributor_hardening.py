"""Regression tests for the seven hardening fixes on top of the original
DigiKey / Mouser / distributor-search trio. Network is fully stubbed."""
from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from tools import digikey_api, mouser_api, distributor_search
from tools.distributor_search import (
    _fuzzy_candidates,
    normalize_mpn,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    # Disable the persistent on-disk RAG cache so write-throughs don't
    # leak across tests via the shared SQLite file.
    monkeypatch.setenv("COMPONENT_CACHE_DISABLED", "1")
    # `config.py` calls `load_dotenv()` at import time so real DigiKey /
    # Mouser credentials from .env leak into every test process. That
    # poisons mock-based tests in two ways:
    #   1. `is_configured()` returns True even when the test only mocked
    #      the *other* distributor — so the unintended distributor fires
    #      a real network call (or, with patched urlopen, consumes the
    #      mock iterator before the test's distributor gets to it).
    #   2. The shared `urllib.request.urlopen` symbol means a single
    #      `patch("tools.X.urllib.request.urlopen", ...)` intercepts
    #      DigiKey's token endpoint first, exhausting the mock and
    #      surfacing as `StopIteration` from the *other* distributor.
    # Strip both upfront; per-test fixtures (`dk_configured` /
    # `mouser_configured`) re-add what each test actually needs.
    for key in ("DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET",
                "DIGIKEY_API_URL", "MOUSER_API_KEY", "MOUSER_API_URL"):
        monkeypatch.delenv(key, raising=False)
    distributor_search.reset_cache()
    digikey_api.reset_cache()
    digikey_api.reset_rate_limit()
    yield
    distributor_search.reset_cache()
    digikey_api.reset_cache()
    digikey_api.reset_rate_limit()


@pytest.fixture
def dk_configured(monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
    monkeypatch.setenv("DIGIKEY_API_URL", "https://api.digikey.com")


@pytest.fixture
def mouser_configured(monkeypatch):
    monkeypatch.setenv("MOUSER_API_KEY", "z")
    monkeypatch.setenv("MOUSER_API_URL", "https://api.mouser.com/api/v2")


def _mock_dk_urlopen(*side_effects):
    call_iter = iter(side_effects)

    class _Ctx:
        def __init__(self, p): self._p = p
        def read(self): return self._p if isinstance(self._p, bytes) else self._p.encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _open(*_a, **_k):
        nxt = next(call_iter)
        if isinstance(nxt, Exception):
            raise nxt
        return _Ctx(nxt)

    return patch("tools.digikey_api.urllib.request.urlopen", side_effect=_open)


def _mock_mouser_urlopen(*side_effects):
    call_iter = iter(side_effects)

    class _Ctx:
        def __init__(self, p): self._p = p
        def read(self): return self._p if isinstance(self._p, bytes) else self._p.encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _open(*_a, **_k):
        nxt = next(call_iter)
        if isinstance(nxt, Exception):
            raise nxt
        return _Ctx(nxt)

    return patch("tools.mouser_api.urllib.request.urlopen", side_effect=_open)


def _mk_http_error(code: int, retry_after: str | None = None):
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError("url", code, "err", headers, io.BytesIO(b""))


# ---------------------------------------------------------------------------
# #2 — 429 + Retry-After backoff
# ---------------------------------------------------------------------------

class Test429Retry:

    def test_digikey_retries_on_429_then_succeeds(self, dk_configured):
        token = json.dumps({"access_token": "t", "expires_in": 3600})
        product = json.dumps({
            "ProductDetails": {
                "ManufacturerPartNumber": "X",
                "Manufacturer": {"Value": "V"},
                "ProductStatus": {"Status": "Active"},
            }
        })
        # Sequence: token → 429 → product (retry succeeds)
        with _mock_dk_urlopen(token, _mk_http_error(429, "0.1"), product), \
             patch("tools.digikey_api.time.sleep") as ms:
            info = digikey_api.lookup("X", timeout_s=2)
        assert info is not None and info.part_number == "X"
        ms.assert_called()  # back-off actually slept

    def test_digikey_gives_up_after_max_retries(self, dk_configured):
        token = json.dumps({"access_token": "t", "expires_in": 3600})
        # Token + 3x 429 → exhausted (max_retries=2 → 3 attempts total)
        with _mock_dk_urlopen(
            token,
            _mk_http_error(429, "0.1"),
            _mk_http_error(429, "0.1"),
            _mk_http_error(429, "0.1"),
        ), patch("tools.digikey_api.time.sleep"):
            assert digikey_api.lookup("X", timeout_s=2) is None

    def test_digikey_retry_after_http_date_falls_back_gracefully(self, dk_configured):
        token = json.dumps({"access_token": "t", "expires_in": 3600})
        product = json.dumps({
            "ProductDetails": {"ManufacturerPartNumber": "X",
                               "Manufacturer": {"Value": "V"}},
        })
        # Unparseable Retry-After → default 2 s (we sleep-mock so this is cheap)
        with _mock_dk_urlopen(token, _mk_http_error(429, "not-a-number"), product), \
             patch("tools.digikey_api.time.sleep") as ms:
            info = digikey_api.lookup("X", timeout_s=2)
        assert info is not None
        # Sleep still happened; we don't assert the exact value because
        # the implementation may pick any sane default on parse failure.
        ms.assert_called()

    def test_mouser_retries_on_429(self, mouser_configured):
        ok = json.dumps({"SearchResults": {"NumberOfResult": 1, "Parts": [{
            "ManufacturerPartNumber": "X", "Manufacturer": "V",
            "LifecycleStatus": "Active",
        }]}})
        with _mock_mouser_urlopen(_mk_http_error(429, "0.1"), ok), \
             patch("tools.mouser_api._time.sleep") as ms:
            info = mouser_api.lookup("X", timeout_s=2)
        assert info is not None
        ms.assert_called()

    def test_mouser_gives_up_after_max_retries(self, mouser_configured):
        with _mock_mouser_urlopen(
            _mk_http_error(429, "0.1"),
            _mk_http_error(429, "0.1"),
            _mk_http_error(429, "0.1"),
        ), patch("tools.mouser_api._time.sleep"):
            assert mouser_api.lookup("X", timeout_s=2) is None


# ---------------------------------------------------------------------------
# #3 — Response-shape drift
# ---------------------------------------------------------------------------

class TestFieldNameFallbacks:

    def test_mouser_accepts_manufacturer_name_alt_field(self, mouser_configured):
        """Legacy Mouser responses used `ManufacturerName` not `Manufacturer`."""
        body = json.dumps({"SearchResults": {"NumberOfResult": 1, "Parts": [{
            "ManufacturerPartNumber": "X",
            "ManufacturerName": "LegacyVendor",
            "LifecycleStatus": "Active",
        }]}})
        with _mock_mouser_urlopen(body):
            info = mouser_api.lookup("X")
        assert info is not None
        assert info.manufacturer == "LegacyVendor"

    def test_mouser_accepts_alt_datasheet_field(self, mouser_configured):
        body = json.dumps({"SearchResults": {"NumberOfResult": 1, "Parts": [{
            "ManufacturerPartNumber": "X", "Manufacturer": "V",
            "Datasheet": "https://ex/ds.pdf",   # not `DataSheetUrl`
            "LifecycleStatus": "Active",
        }]}})
        with _mock_mouser_urlopen(body):
            info = mouser_api.lookup("X")
        assert info is not None
        assert info.datasheet_url == "https://ex/ds.pdf"

    def test_mouser_stock_field_aliases(self, mouser_configured):
        body = json.dumps({"SearchResults": {"NumberOfResult": 1, "Parts": [{
            "ManufacturerPartNumber": "X", "Manufacturer": "V",
            "QuantityAvailable": "42",  # used when AvailabilityInStock is absent
            "LifecycleStatus": "Active",
        }]}})
        with _mock_mouser_urlopen(body):
            info = mouser_api.lookup("X")
        assert info is not None
        assert info.stock_quantity == 42


# ---------------------------------------------------------------------------
# #4 — Region awareness
# ---------------------------------------------------------------------------

class TestRegionAwareness:

    def test_mouser_part_level_region_wins(self, mouser_configured):
        body = json.dumps({"SearchResults": {"NumberOfResult": 1, "Parts": [{
            "ManufacturerPartNumber": "X", "Manufacturer": "V",
            "Region": "IN",
            "LifecycleStatus": "Active",
        }]}})
        with _mock_mouser_urlopen(body):
            info = mouser_api.lookup("X")
        assert info is not None
        assert info.region == "IN"

    def test_mouser_falls_back_to_response_level_region(self, mouser_configured):
        body = json.dumps({"SearchResults": {
            "NumberOfResult": 1,
            "MouserRegionCodePrefix": "DE",
            "Parts": [{
                "ManufacturerPartNumber": "X", "Manufacturer": "V",
                "LifecycleStatus": "Active",
            }],
        }})
        with _mock_mouser_urlopen(body):
            info = mouser_api.lookup("X")
        assert info is not None
        assert info.region == "DE"

    def test_mouser_falls_back_to_api_url_tld(self, monkeypatch):
        monkeypatch.setenv("MOUSER_API_KEY", "z")
        monkeypatch.setenv("MOUSER_API_URL", "https://api.mouser.in/api/v2")
        body = json.dumps({"SearchResults": {"NumberOfResult": 1, "Parts": [{
            "ManufacturerPartNumber": "X", "Manufacturer": "V",
            "LifecycleStatus": "Active",
        }]}})
        with _mock_mouser_urlopen(body):
            info = mouser_api.lookup("X")
        assert info is not None
        assert info.region == "IN"

    def test_digikey_always_tags_region_us(self, dk_configured):
        token = json.dumps({"access_token": "t", "expires_in": 3600})
        product = json.dumps({"ProductDetails": {
            "ManufacturerPartNumber": "X",
            "Manufacturer": {"Value": "V"},
            "ProductStatus": {"Status": "Active"},
        }})
        with _mock_dk_urlopen(token, product):
            info = digikey_api.lookup("X")
        assert info is not None
        assert info.region == "US"


# ---------------------------------------------------------------------------
# #7 — Fuzzy MPN normalisation
# ---------------------------------------------------------------------------

class TestMpnNormalisation:

    @pytest.mark.parametrize("raw,stripped", [
        ("ADL8107-R7",       "ADL8107"),
        ("ADL8107-TR",       "ADL8107"),
        ("ADL8107-TR1000",   "ADL8107"),
        ("ADL8107-REEL",     "ADL8107"),
        ("ADL8107-REEL7",    "ADL8107"),
        ("ADL8107/TR",       "ADL8107"),
        ("ADL8107-ND",       "ADL8107"),
        ("ADL8107-CT-ND",    "ADL8107"),
        ("ADL8107-PBFREE",   "ADL8107"),
        ("SP4320-01WTG",     "SP4320"),
        ("ADL8107-SAMPLE",   "ADL8107"),
    ])
    def test_suffix_patterns_strip(self, raw, stripped):
        assert normalize_mpn(raw) == stripped

    def test_no_match_returns_input_unchanged(self):
        assert normalize_mpn("STM32F407VGT6") == "STM32F407VGT6"
        assert normalize_mpn("NE555") == "NE555"

    def test_empty_returns_empty(self):
        assert normalize_mpn("") == ""
        assert normalize_mpn(None) == ""  # type: ignore[arg-type]

    def test_fuzzy_candidates_ordering(self):
        """Original MPN first, then progressively-stripped forms,
        deduplicated."""
        out = _fuzzy_candidates("ADL8107-R7")
        assert out[0] == "ADL8107-R7"
        assert any(c == "ADL8107" for c in out)
        # Only 3 stripping passes max, no duplicates
        assert len(out) == len(set(x.upper() for x in out))

    def test_fuzzy_candidates_no_strip_needed(self):
        out = _fuzzy_candidates("NE555")
        assert out == ["NE555"]

    def test_lookup_retries_with_stripped_mpn(self, mouser_configured):
        """Distributor lookup: original MPN misses, stripped MPN hits."""
        # First call (original "ADL8107-R7") returns empty.
        empty = json.dumps({"SearchResults": {"NumberOfResult": 0, "Parts": []}})
        # Second call (stripped "ADL8107") returns a match.
        match = json.dumps({"SearchResults": {"NumberOfResult": 1, "Parts": [{
            "ManufacturerPartNumber": "ADL8107",
            "Manufacturer": "ADI", "LifecycleStatus": "Active",
        }]}})
        with _mock_mouser_urlopen(empty, match):
            # Skip datasheet-verify (network) for this test
            with patch("tools.distributor_search._skip_datasheet_verify",
                       return_value=True):
                info = distributor_search.lookup("ADL8107-R7")
        assert info is not None
        assert info.part_number == "ADL8107"
        assert info.source == "mouser"


# ---------------------------------------------------------------------------
# #6 — Datasheet HEAD verification on accept
# ---------------------------------------------------------------------------

class TestDatasheetVerificationOnAccept:

    def test_bad_datasheet_url_replaced_with_fallback_when_not_trusted(self, mouser_configured):
        """When the distributor's PDF fails its probe, the resolver swaps in
        a distributor MPN-search URL (last rung of the chain). Old behaviour
        stripped the URL entirely, leaving an empty BOM cell. The 2026-04-24
        rewrite kept this contract but moved the fallback off Google /
        manufacturer sites and onto DigiKey/Mouser. Mouser-sourced parts
        fall back to Mouser's catalog (since this part came from Mouser),
        DigiKey-sourced parts fall back to DigiKey."""
        body = json.dumps({"SearchResults": {"NumberOfResult": 1, "Parts": [{
            "ManufacturerPartNumber": "X", "Manufacturer": "V",
            "DataSheetUrl": "https://stale.example/bad.pdf",
            "LifecycleStatus": "Active",
        }]}})
        with _mock_mouser_urlopen(body), \
             patch("tools.datasheet_resolver.is_trusted_vendor_url", return_value=False), \
             patch("tools.datasheet_resolver.verify_url", return_value=False):
            info = distributor_search.lookup("X")
        assert info is not None
        # URL is no longer None — it falls through to the always-good
        # Mouser MPN-search link (this part originated from Mouser).
        assert info.datasheet_url is not None
        assert info.datasheet_url != "https://stale.example/bad.pdf"
        assert info.datasheet_url.startswith("https://www.mouser.com/c/?q="), (
            f"Mouser-sourced part must fall back to Mouser search, "
            f"got {info.datasheet_url!r}"
        )
        # Must NOT be off-platform.
        assert "google.com" not in info.datasheet_url
        assert "duckduckgo.com" not in info.datasheet_url
        assert "analog.com" not in info.datasheet_url

    def test_trusted_vendor_url_preserved_without_head_call(self, mouser_configured):
        body = json.dumps({"SearchResults": {"NumberOfResult": 1, "Parts": [{
            "ManufacturerPartNumber": "ADL8107", "Manufacturer": "ADI",
            "DataSheetUrl": "https://www.analog.com/en/products/adl8107.html",
            "LifecycleStatus": "Active",
        }]}})
        with _mock_mouser_urlopen(body), \
             patch("tools.datasheet_verify.verify_url") as mv, \
             patch("tools.datasheet_verify.is_trusted_vendor_url", return_value=True):
            info = distributor_search.lookup("ADL8107")
        assert info is not None
        assert info.datasheet_url is not None
        mv.assert_not_called()  # trusted → no HEAD probe

    def test_skip_env_bypasses_probe(self, mouser_configured, monkeypatch):
        monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
        body = json.dumps({"SearchResults": {"NumberOfResult": 1, "Parts": [{
            "ManufacturerPartNumber": "X", "Manufacturer": "V",
            "DataSheetUrl": "https://anything.example/ds.pdf",
            "LifecycleStatus": "Active",
        }]}})
        with _mock_mouser_urlopen(body), \
             patch("tools.datasheet_verify.verify_url") as mv:
            info = distributor_search.lookup("X")
        assert info is not None
        assert info.datasheet_url == "https://anything.example/ds.pdf"
        mv.assert_not_called()


# ---------------------------------------------------------------------------
# #5 — Cross-distributor price reconciliation (rf_audit)
# ---------------------------------------------------------------------------

class TestPriceReconciliation:
    """Exercises services.rf_audit.run_price_reconciliation_audit.

    Both DigiKey + Mouser must be configured before the check fires.
    """

    def _make_info(self, price, currency, source):
        from tools.digikey_api import PartInfo
        return PartInfo(
            part_number="ADL8107", manufacturer="ADI", description="",
            datasheet_url=None, product_url=None,
            lifecycle_status="active",
            unit_price_usd=price if currency == "USD" else None,
            stock_quantity=None, source=source,
            unit_price=price, unit_price_currency=currency,
        )

    def test_flags_large_price_delta(self, dk_configured, mouser_configured):
        from services.rf_audit import run_price_reconciliation_audit
        dk = self._make_info(5.00, "USD", "digikey")
        mo = self._make_info(8.00, "USD", "mouser")  # 60% more than DK
        with patch("tools.digikey_api.lookup", return_value=dk), \
             patch("tools.mouser_api.lookup", return_value=mo):
            issues = run_price_reconciliation_audit(
                [{"part_number": "ADL8107"}],
                pct_threshold=20.0,
            )
        assert any(i.category == "price_discrepancy" for i in issues)
        assert any("DigiKey is cheaper" in i.detail for i in issues)

    def test_skips_when_within_threshold(self, dk_configured, mouser_configured):
        from services.rf_audit import run_price_reconciliation_audit
        dk = self._make_info(5.00, "USD", "digikey")
        mo = self._make_info(5.50, "USD", "mouser")  # 10% — under 20% cap
        with patch("tools.digikey_api.lookup", return_value=dk), \
             patch("tools.mouser_api.lookup", return_value=mo):
            issues = run_price_reconciliation_audit(
                [{"part_number": "ADL8107"}],
                pct_threshold=20.0,
            )
        assert issues == []

    def test_skips_different_currencies(self, dk_configured, mouser_configured):
        from services.rf_audit import run_price_reconciliation_audit
        dk = self._make_info(5.00, "USD", "digikey")
        mo = self._make_info(500.0, "INR", "mouser")  # FX layer — skip
        with patch("tools.digikey_api.lookup", return_value=dk), \
             patch("tools.mouser_api.lookup", return_value=mo):
            issues = run_price_reconciliation_audit(
                [{"part_number": "ADL8107"}],
            )
        assert issues == []

    def test_skips_when_only_one_distributor_configured(self, dk_configured, monkeypatch):
        """Needs BOTH keys to compare — one-tier config → no issue."""
        monkeypatch.delenv("MOUSER_API_KEY", raising=False)
        from services.rf_audit import run_price_reconciliation_audit
        issues = run_price_reconciliation_audit([{"part_number": "ADL8107"}])
        assert issues == []

    def test_skips_when_lookup_misses_on_either_side(self, dk_configured, mouser_configured):
        from services.rf_audit import run_price_reconciliation_audit
        with patch("tools.digikey_api.lookup", return_value=None), \
             patch("tools.mouser_api.lookup",
                   return_value=self._make_info(5.0, "USD", "mouser")):
            issues = run_price_reconciliation_audit(
                [{"part_number": "ADL8107"}],
            )
        assert issues == []

    def test_skips_when_price_missing_on_either_side(self, dk_configured, mouser_configured):
        from services.rf_audit import run_price_reconciliation_audit
        dk = self._make_info(None, "USD", "digikey")
        mo = self._make_info(5.00, "USD", "mouser")
        with patch("tools.digikey_api.lookup", return_value=dk), \
             patch("tools.mouser_api.lookup", return_value=mo):
            issues = run_price_reconciliation_audit(
                [{"part_number": "ADL8107"}],
            )
        assert issues == []
