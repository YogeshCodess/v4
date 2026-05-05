"""Tests for `tools.datasheet_resolver` — the fallback chain that
guarantees every BOM row gets a clickable datasheet link.

Coverage matrix:

  * `_distributor_search_url` — never empty, properly URL-encoded,
                                always lands on DigiKey
  * `build_chain`             — order, dedup, always ends with
                                distributor_search; NEVER includes
                                manufacturer or Google URLs
  * `_probe`                  — trusted short-circuit, cache hit,
                                cache miss → live → write
  * `resolve_datasheet`       — first probe pass wins, falls all the
                                way through, never returns is_valid=False
  * `resolve_url`             — convenience wrapper

History note (2026-04-24): the old `_slug`, `_search_fallback_url`,
and `_guess_mfr_url` helpers were removed when the resolver was
restricted to DigiKey/Mouser-only links per user feedback that
manufacturer-site fallbacks (analog.com / qorvo.com / ti.com) and
Google search fallbacks were producing wrong or off-platform URLs.

The cache singleton is redirected at a temp DB per test so we never
touch the shipped `data/component_cache.db`. `verify_url` is patched
where the resolver imports it (module-local symbol) so we exercise the
chain in isolation from any HTTP code.
"""
from __future__ import annotations

import urllib.parse
from pathlib import Path
from unittest.mock import patch

import pytest

from services import component_cache as cc
from tools import datasheet_resolver as dr
from tools.datasheet_resolver import (
    ResolvedDatasheet,
    _distributor_search_url,
    _probe,
    build_chain,
    resolve_datasheet,
    resolve_url,
)
from tools.digikey_api import PartInfo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_cache(tmp_path: Path, monkeypatch):
    """Point the singleton at a fresh temp DB so cache writes from the
    resolver don't pollute the shipped cache file. Reset after."""
    db = tmp_path / "resolver_cache.db"
    monkeypatch.setenv("COMPONENT_CACHE_PATH", str(db))
    monkeypatch.delenv("COMPONENT_CACHE_DISABLED", raising=False)
    cc.reset_default()
    yield cc.get_default()
    cc.reset_default()


@pytest.fixture
def cache_off(monkeypatch):
    """Disable the persistent cache so we exercise the strictly-live
    code path (used to prove the resolver still works without the RAG
    layer in place)."""
    monkeypatch.setenv("COMPONENT_CACHE_DISABLED", "1")
    cc.reset_default()
    yield
    cc.reset_default()


def _part(
    pn: str = "ADL8107",
    mfr: str = "Analog Devices Inc.",
    datasheet_url: str | None = "https://www.analog.com/media/en/datasheet/adl8107.pdf",
    product_url: str | None = "https://www.analog.com/en/products/ADL8107.html",
    source: str = "digikey",
) -> PartInfo:
    return PartInfo(
        part_number=pn,
        manufacturer=mfr,
        description="Wideband LNA 2-18 GHz",
        datasheet_url=datasheet_url,
        product_url=product_url,
        lifecycle_status="active",
        unit_price_usd=24.0,
        stock_quantity=180,
        source=source,
    )


# ---------------------------------------------------------------------------
# _distributor_search_url — replaces the old _slug / _guess_mfr_url /
# _search_fallback_url helpers. Single contract: always returns a
# DigiKey keyword-search URL for the given MPN, never empty, never
# off-platform.
# ---------------------------------------------------------------------------

class TestDistributorSearchUrl:
    def test_returns_digikey_search_url(self):
        url = _distributor_search_url("ADL8107")
        assert url.startswith(
            "https://www.digikey.com/en/products/result?keywords="
        )
        assert "ADL8107" in url

    def test_url_encodes_special_chars(self):
        # Spaces / slashes / "+" inside an MPN must be percent-encoded
        # so the URL is well-formed at DigiKey's edge. Mini-Circuits
        # MPNs end in "+" routinely (`ZHL-42W+`, `ZX85-12-8SA-S+`).
        url = _distributor_search_url("ZX85-12-8SA-S+")
        assert " " not in url
        assert url.endswith("ZX85-12-8SA-S%2B"), (
            "trailing + must be percent-encoded as %2B for DigiKey search"
        )

    def test_empty_mpn_still_returns_a_url(self):
        # Contract: never null. Even with no MPN we still return the
        # DigiKey search root with an empty keywords param.
        url = _distributor_search_url("")
        assert url == "https://www.digikey.com/en/products/result?keywords="

    def test_strips_surrounding_whitespace(self):
        url = _distributor_search_url("  ADL8107  ")
        assert url.endswith("ADL8107")

    def test_never_points_at_manufacturer_or_search_engine(self):
        # The whole point of this helper: stay inside DigiKey.
        for mpn in ("ADL8107", "qpa9120", "STM32F407", "ZHL-42W+", ""):
            url = _distributor_search_url(mpn)
            assert "digikey.com" in url
            assert "google.com" not in url
            assert "duckduckgo.com" not in url
            assert "analog.com" not in url
            assert "qorvo.com" not in url
            assert "ti.com" not in url


# ---------------------------------------------------------------------------
# build_chain (pure function, no I/O)
# ---------------------------------------------------------------------------

class TestBuildChain:
    def test_full_chain_includes_all_three_rungs(self):
        chain = build_chain(_part())
        # 1: distributor PDF, 2: distributor product page, 3: DigiKey MPN-search.
        # The old `mfr_guess` (analog.com / qorvo.com / ...) and
        # `search_fallback` (google.com) rungs were dropped on
        # 2026-04-24 — every rung now stays inside DigiKey/Mouser.
        assert len(chain) == 3
        sources = [src for _, src in chain]
        assert sources == ["distributor_pdf", "product_url", "distributor_search"]

    def test_chain_always_ends_with_distributor_search(self):
        # Even a totally bare PartInfo (no datasheet, no product url) still
        # gets a valid DigiKey MPN-search URL as the last rung. Manufacturer
        # is irrelevant — we don't guess vendor URLs anymore.
        bare = _part(datasheet_url=None, product_url=None, mfr="ObscureCo")
        chain = build_chain(bare)
        assert chain[-1][1] == "distributor_search"
        assert chain[-1][0].startswith(
            "https://www.digikey.com/en/products/result?keywords="
        )
        assert "ADL8107" in chain[-1][0]

    def test_skips_missing_distributor_pdf(self):
        info = _part(datasheet_url=None)
        sources = [src for _, src in build_chain(info)]
        assert "distributor_pdf" not in sources
        assert sources[0] == "product_url"

    def test_skips_missing_product_url(self):
        info = _part(product_url=None)
        sources = [src for _, src in build_chain(info)]
        assert "product_url" not in sources
        assert sources[0] == "distributor_pdf"

    def test_dedupes_when_product_url_equals_datasheet_url(self):
        same = "https://www.digikey.com/en/products/detail/analog-devices/ADL8107"
        info = _part(datasheet_url=same, product_url=same)
        sources = [src for _, src in build_chain(info)]
        # product_url is dropped because it equals the datasheet rung.
        assert sources.count("distributor_pdf") == 1
        assert "product_url" not in sources

    def test_no_mfr_guess_rung_at_all(self):
        # The old `mfr_guess` rung is gone. Even for a vendor whose URL
        # template was previously hard-coded (Analog Devices), the chain
        # MUST NOT include any `analog.com` / `qorvo.com` / vendor URL
        # that wasn't supplied by the distributor.
        info = _part(
            datasheet_url=None, product_url=None, mfr="Analog Devices",
        )
        chain = build_chain(info)
        sources = [src for _, src in chain]
        assert "mfr_guess" not in sources
        assert "search_fallback" not in sources
        # And no URL on any rung points at a manufacturer site.
        for url, _ in chain:
            assert "analog.com" not in url
            assert "qorvo.com" not in url
            assert "ti.com" not in url
            assert "google.com" not in url
            assert "duckduckgo.com" not in url

    def test_minimal_chain_is_just_distributor_search(self):
        info = _part(datasheet_url=None, product_url=None, mfr="ObscureCo")
        chain = build_chain(info)
        assert len(chain) == 1
        assert chain[0][1] == "distributor_search"

    def test_digikey_sourced_part_falls_back_to_digikey(self):
        # source="digikey" → DigiKey search URL on the last rung.
        info = _part(datasheet_url=None, product_url=None, source="digikey")
        chain = build_chain(info)
        url, source = chain[-1]
        assert source == "distributor_search"
        assert url.startswith(
            "https://www.digikey.com/en/products/result?keywords="
        )

    def test_mouser_sourced_part_falls_back_to_mouser(self):
        # source="mouser" → Mouser keyword-search URL on the last rung.
        # Mirrors user feedback: "digikey/mouser, not only digikey".
        info = _part(datasheet_url=None, product_url=None, source="mouser")
        chain = build_chain(info)
        url, source = chain[-1]
        assert source == "distributor_search"
        assert url.startswith("https://www.mouser.com/c/?q=")
        assert "ADL8107" in url

    def test_unknown_source_defaults_to_digikey(self):
        # source="seed" / "chromadb" / "" — fall back to DigiKey since
        # it has the broader catalog.
        for src in ("seed", "chromadb", "", "unknown"):
            info = _part(datasheet_url=None, product_url=None, source=src)
            chain = build_chain(info)
            assert chain[-1][0].startswith(
                "https://www.digikey.com/en/products/result?keywords="
            ), f"source={src!r} should default to DigiKey"

    def test_pure_function_no_side_effects(self):
        # build_chain must not touch the network or the cache. We prove
        # this by patching both and asserting they were never called.
        with patch("tools.datasheet_resolver.verify_url") as mock_verify, \
             patch("tools.datasheet_resolver.get_default") as mock_cache:
            chain = build_chain(_part())
            assert len(chain) == 3
            mock_verify.assert_not_called()
            mock_cache.assert_not_called()


# ---------------------------------------------------------------------------
# _probe
# ---------------------------------------------------------------------------

class TestProbe:
    def test_empty_url_returns_false(self, temp_cache):
        assert _probe("") is False

    def test_trusted_vendor_short_circuits_no_live_probe(self, temp_cache):
        # Trusted vendor = no HEAD/GET. We prove this by patching verify_url
        # and asserting it was never called.
        with patch("tools.datasheet_resolver.verify_url") as mock_verify:
            ok = _probe("https://www.analog.com/en/products/adl8107.html")
        assert ok is True
        mock_verify.assert_not_called()
        # And the trusted result was written to the cache for later reads.
        hit = temp_cache.get_url_probe("https://www.analog.com/en/products/adl8107.html")
        assert hit is not None
        assert hit.is_valid is True

    def test_cache_hit_short_circuits_live_probe(self, temp_cache):
        url = "https://random-distributor.invalid/foo.pdf"
        # Pre-populate the cache so the next _probe should not hit verify_url.
        temp_cache.put_url_probe(url, True, status_code=200,
                                 content_type="application/pdf",
                                 is_trusted=False)
        with patch("tools.datasheet_resolver.verify_url") as mock_verify:
            ok = _probe(url)
        assert ok is True
        mock_verify.assert_not_called()

    def test_cache_miss_calls_verify_and_writes_back(self, temp_cache):
        url = "https://random-distributor.invalid/never-cached.pdf"
        # Cache is empty; verify_url should be called and the result cached.
        with patch("tools.datasheet_resolver.verify_url",
                   return_value=True) as mock_verify:
            ok = _probe(url)
        assert ok is True
        mock_verify.assert_called_once()
        # Subsequent probe must read from cache, not from verify_url.
        with patch("tools.datasheet_resolver.verify_url") as mock_verify2:
            ok2 = _probe(url)
        assert ok2 is True
        mock_verify2.assert_not_called()

    def test_negative_probe_is_cached(self, temp_cache):
        url = "https://broken-link.invalid/404.pdf"
        with patch("tools.datasheet_resolver.verify_url",
                   return_value=False) as mock_verify:
            ok = _probe(url)
        assert ok is False
        mock_verify.assert_called_once()
        # Negative result is cached so we don't re-probe the dead URL.
        with patch("tools.datasheet_resolver.verify_url") as mock_verify2:
            ok2 = _probe(url)
        assert ok2 is False
        mock_verify2.assert_not_called()

    def test_verify_url_exception_returns_false_safely(self, temp_cache):
        # If the underlying verifier crashes the resolver must return
        # False, not propagate.
        with patch("tools.datasheet_resolver.verify_url",
                   side_effect=RuntimeError("boom")):
            ok = _probe("https://something.invalid/x.pdf")
        assert ok is False

    def test_cache_disabled_falls_through_to_live_probe(self, cache_off):
        url = "https://random-distributor.invalid/x.pdf"
        with patch("tools.datasheet_resolver.verify_url",
                   return_value=True) as mock_verify:
            ok = _probe(url)
        assert ok is True
        mock_verify.assert_called_once()


# ---------------------------------------------------------------------------
# resolve_datasheet
# ---------------------------------------------------------------------------

class TestResolveDatasheet:
    def test_returns_distributor_pdf_when_it_probes_ok(self, temp_cache):
        # Patch the trusted check so we exercise the live-probe branch
        # rather than the trusted short-circuit (analog.com is in the
        # allowlist by default).
        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url",
                   return_value=True):
            result = resolve_datasheet(_part())
        assert isinstance(result, ResolvedDatasheet)
        assert result.url == "https://www.analog.com/media/en/datasheet/adl8107.pdf"
        assert result.source == "distributor_pdf"
        assert result.chain_position == 1
        assert result.is_valid is True

    def test_falls_through_to_product_url_when_pdf_probe_fails(self, temp_cache):
        # First chain entry probes False, second probes True. Mirror the
        # default _part() URLs exactly (incl. MPN case in product page).
        responses = {"https://www.analog.com/media/en/datasheet/adl8107.pdf": False,
                     "https://www.analog.com/en/products/ADL8107.html": True}
        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url",
                   side_effect=lambda u, timeout=3.0: responses.get(u, False)):
            result = resolve_datasheet(_part())
        assert result.source == "product_url"
        assert result.chain_position == 2
        assert result.url == "https://www.analog.com/en/products/ADL8107.html"

    def test_falls_all_the_way_to_distributor_search_when_nothing_probes(self, temp_cache):
        # Nothing in the chain passes a probe. Resolver MUST return the
        # DigiKey MPN-search URL — never an empty string, never an
        # off-platform Google/DuckDuckGo URL, never a guessed mfr URL.
        info = _part(
            datasheet_url="https://distributor.invalid/dead.pdf",
            product_url="https://distributor.invalid/dead.html",
            mfr="Analog Devices",  # would have triggered the old mfr_guess
        )
        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url",
                   return_value=False):
            result = resolve_datasheet(info)
        assert result.source == "distributor_search"
        assert result.url.startswith(
            "https://www.digikey.com/en/products/result?keywords="
        )
        assert "ADL8107" in result.url
        # is_valid is True even for the fallback — DigiKey's keyword-search
        # URL never 404s by construction.
        assert result.is_valid is True
        # And we MUST NOT have ended up on a manufacturer site or Google.
        assert "analog.com" not in result.url
        assert "google.com" not in result.url

    def test_distributor_search_is_never_probed(self, temp_cache):
        """The DigiKey MPN-search URL is the contractual last-resort.
        Don't probe it — it'd waste a request and pollute the cache for
        a URL that never 404s by design."""
        info = _part(
            datasheet_url=None, product_url=None, mfr="ObscureCo",
        )
        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url") as mock_verify:
            result = resolve_datasheet(info)
        assert result.source == "distributor_search"
        mock_verify.assert_not_called()

    def test_trusted_vendor_url_returned_without_live_probe(self, temp_cache):
        # analog.com is in the trusted allowlist — distributor PDF should
        # win on the first rung without any HEAD/GET.
        with patch("tools.datasheet_resolver.verify_url") as mock_verify:
            result = resolve_datasheet(_part())
        assert result.source == "distributor_pdf"
        assert result.chain_position == 1
        mock_verify.assert_not_called()

    def test_never_returns_empty_url(self, temp_cache):
        # No matter how broken the input, the URL must be non-empty.
        bare = _part(datasheet_url=None, product_url=None, mfr="ObscureCo")
        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url",
                   return_value=False):
            result = resolve_datasheet(bare)
        assert result.url
        assert result.url.strip()


# ---------------------------------------------------------------------------
# resolve_url (convenience wrapper)
# ---------------------------------------------------------------------------

class TestResolveUrl:
    def test_returns_just_the_url_string(self, temp_cache):
        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url",
                   return_value=True):
            url = resolve_url(_part())
        assert isinstance(url, str)
        assert url == "https://www.analog.com/media/en/datasheet/adl8107.pdf"

    def test_never_empty_even_on_total_failure(self, temp_cache):
        bare = _part(datasheet_url=None, product_url=None, mfr="ObscureCo")
        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url",
                   return_value=False):
            url = resolve_url(bare)
        assert url
        assert url.startswith(
            "https://www.digikey.com/en/products/result?keywords="
        )
