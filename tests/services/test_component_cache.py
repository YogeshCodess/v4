"""Tests for `services.component_cache` — the persistent RAG layer.

Exercises every public method of `ComponentCache` against a temp DB so
we never touch the real `data/component_cache.db`. Covers:

  * MPN cache: hit / miss / lifecycle-stale / negative
  * URL probe cache: trusted vs untrusted TTL
  * Parametric cache: hit / miss / stale
  * Bulk insert + stats + purge_stale
  * Singleton: get_default / reset_default / cache_disabled

These tests guard the contract that every distributor-search /
datasheet-verify caller depends on. If any of these break, the live
P1 finalize path also breaks — the cache is not a layer that's safe
to silently misbehave.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from services import component_cache as cc
from services.component_cache import (
    ComponentCache, MpnHit, UrlProbeHit,
    cache_disabled, get_default, reset_default,
)
from tools.digikey_api import PartInfo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cache(tmp_path: Path) -> ComponentCache:
    """Fresh cache pointed at a temp SQLite file. Reset between tests."""
    db = tmp_path / "test_cache.db"
    return ComponentCache(str(db))


def _mk_part(pn: str = "ADL8107", mfr: str = "Analog Devices Inc.",
             url: str = "https://www.analog.com/en/products/adl8107.html",
             status: str = "active", source: str = "digikey") -> PartInfo:
    return PartInfo(
        part_number=pn, manufacturer=mfr,
        description="Wideband LNA 2-18 GHz",
        datasheet_url=url, product_url=None,
        lifecycle_status=status, unit_price_usd=24.0,
        stock_quantity=180, source=source,
        unit_price=24.0, unit_price_currency="USD", region="US",
    )


# ---------------------------------------------------------------------------
# MPN cache
# ---------------------------------------------------------------------------

class TestMpnCache:
    def test_miss_returns_none(self, cache):
        assert cache.get_mpn("DOES-NOT-EXIST") is None

    def test_put_then_get_round_trip(self, cache):
        info = _mk_part()
        cache.put_mpn("ADL8107", info)
        hit = cache.get_mpn("ADL8107")
        assert hit is not None
        assert hit.part_info is not None
        assert hit.part_info.part_number == "ADL8107"
        assert hit.part_info.manufacturer == "Analog Devices Inc."
        assert hit.part_info.datasheet_url == "https://www.analog.com/en/products/adl8107.html"
        assert hit.is_negative is False
        assert hit.lifecycle_stale is False

    def test_lookup_is_case_insensitive(self, cache):
        cache.put_mpn("ADL8107", _mk_part())
        # Stored as upper; queries with mixed case must hit.
        assert cache.get_mpn("adl8107") is not None
        assert cache.get_mpn("Adl8107") is not None
        assert cache.get_mpn("ADL8107") is not None

    def test_negative_cache_round_trip(self, cache):
        cache.put_mpn_negative("HALLUCINATED-XYZ")
        hit = cache.get_mpn("HALLUCINATED-XYZ")
        assert hit is not None
        assert hit.is_negative is True
        assert hit.part_info is None

    def test_negative_overwritten_by_real_hit(self, cache):
        """If a future call finds a part that we previously cached as a
        miss, the real hit must take over — otherwise a part added to
        DigiKey after our cache populated would stay 'hallucinated'."""
        cache.put_mpn_negative("ADL8107")
        cache.put_mpn("ADL8107", _mk_part())
        hit = cache.get_mpn("ADL8107")
        assert hit is not None
        assert hit.is_negative is False
        assert hit.part_info is not None

    def test_identity_stale_returns_none(self, cache, monkeypatch):
        """When identity has aged past IDENTITY_TTL, get_mpn returns None
        so the caller does a full re-fetch — we don't want to serve a
        7-day-old datasheet URL when the part might have been renamed."""
        # TTL=0 → any non-zero integer-second age trips the `>` comparison.
        # Sleep 1.1s guarantees at least one integer-second tick (time.time()
        # truncated to int) since the put.
        monkeypatch.setattr(cc, "IDENTITY_TTL_S", 0)
        cache.put_mpn("ADL8107", _mk_part())
        time.sleep(1.1)
        assert cache.get_mpn("ADL8107") is None

    def test_lifecycle_stale_flag(self, cache, monkeypatch):
        """Identity fresh + lifecycle older than LIFECYCLE_TTL → return
        the part_info but flag lifecycle_stale=True so the caller can
        do a cheap stock/price refresh without re-fetching identity."""
        monkeypatch.setattr(cc, "IDENTITY_TTL_S", 3600)  # fresh identity
        monkeypatch.setattr(cc, "LIFECYCLE_TTL_S", 0)    # stale lifecycle on next sec tick
        cache.put_mpn("ADL8107", _mk_part())
        time.sleep(1.1)
        hit = cache.get_mpn("ADL8107")
        assert hit is not None
        assert hit.part_info is not None
        assert hit.lifecycle_stale is True

    def test_negative_stale_returns_none(self, cache, monkeypatch):
        """A negatively-cached miss past NEGATIVE_TTL must re-query —
        otherwise we'd permanently flag a now-real part as hallucinated."""
        monkeypatch.setattr(cc, "NEGATIVE_TTL_S", 0)
        cache.put_mpn_negative("MAYBE-FUTURE-PART")
        time.sleep(1.1)
        assert cache.get_mpn("MAYBE-FUTURE-PART") is None

    def test_update_lifecycle_only(self, cache, monkeypatch):
        """update_lifecycle bumps the lifecycle clock without bumping
        identity_cached_at — so a cheap stock refresh doesn't masquerade
        as a fresh identity write."""
        monkeypatch.setattr(cc, "IDENTITY_TTL_S", 100000)
        monkeypatch.setattr(cc, "LIFECYCLE_TTL_S", 100000)
        cache.put_mpn("ADL8107", _mk_part(status="nrnd"))
        time.sleep(0.05)
        # Pretend we re-checked stock + lifecycle and the part is now active.
        cache.update_lifecycle("ADL8107", _mk_part(status="active"))
        hit = cache.get_mpn("ADL8107")
        assert hit is not None
        assert hit.part_info is not None
        assert hit.part_info.lifecycle_status == "active"

    def test_empty_mpn_is_safe(self, cache):
        cache.put_mpn("", _mk_part())
        assert cache.get_mpn("") is None
        cache.put_mpn_negative("")  # no-op
        assert cache.get_mpn("") is None


# ---------------------------------------------------------------------------
# URL probe cache
# ---------------------------------------------------------------------------

class TestUrlProbeCache:
    def test_miss_returns_none(self, cache):
        assert cache.get_url_probe("https://nope.example/x.pdf") is None

    def test_round_trip_with_status_and_content_type(self, cache):
        cache.put_url_probe(
            "https://example.com/x.pdf",
            True, status_code=200, content_type="application/pdf",
            is_trusted=False,
        )
        hit = cache.get_url_probe("https://example.com/x.pdf")
        assert hit is not None
        assert hit.is_valid is True
        assert hit.status_code == 200
        assert hit.content_type == "application/pdf"

    def test_negative_probe_cached(self, cache):
        cache.put_url_probe("https://nope.example/x.pdf", False)
        hit = cache.get_url_probe("https://nope.example/x.pdf")
        assert hit is not None
        assert hit.is_valid is False

    def test_untrusted_ttl(self, cache, monkeypatch):
        monkeypatch.setattr(cc, "URL_PROBE_TTL_S", 0)
        monkeypatch.setattr(cc, "URL_PROBE_TRUSTED_TTL_S", 100000)
        cache.put_url_probe("https://random.blog/x.pdf", True, is_trusted=False)
        time.sleep(1.1)
        # Untrusted URL ages out at the short TTL.
        assert cache.get_url_probe("https://random.blog/x.pdf") is None

    def test_trusted_ttl_is_longer(self, cache, monkeypatch):
        """Trusted-vendor URLs should survive the untrusted TTL."""
        monkeypatch.setattr(cc, "URL_PROBE_TTL_S", 0)
        monkeypatch.setattr(cc, "URL_PROBE_TRUSTED_TTL_S", 100000)
        cache.put_url_probe("https://www.analog.com/x.html", True, is_trusted=True)
        time.sleep(1.1)
        hit = cache.get_url_probe("https://www.analog.com/x.html")
        assert hit is not None
        assert hit.is_valid is True


# ---------------------------------------------------------------------------
# Parametric cache
# ---------------------------------------------------------------------------

class TestParametricCache:
    def test_miss_returns_none(self, cache):
        assert cache.get_parametric("missing-hash") is None

    def test_round_trip(self, cache):
        infos = [_mk_part("HMC8410", source="digikey"),
                 _mk_part("ADL8107", source="digikey")]
        cache.put_parametric("h:lna:0-6ghz", "low noise amplifier 0-6 GHz", infos)
        out = cache.get_parametric("h:lna:0-6ghz")
        assert out is not None
        assert {p.part_number for p in out} == {"HMC8410", "ADL8107"}

    def test_stale_returns_none(self, cache, monkeypatch):
        monkeypatch.setattr(cc, "PARAMETRIC_TTL_S", 0)
        cache.put_parametric("h:lna:test", "label", [_mk_part()])
        time.sleep(1.1)
        assert cache.get_parametric("h:lna:test") is None

    def test_long_label_truncated(self, cache):
        """The schema caps query_label at 200 chars to keep the cache file
        compact — long labels just get clipped, not rejected."""
        label = "x" * 500
        cache.put_parametric("h:long", label, [_mk_part()])
        # Just make sure round-trip still works (clipping shouldn't lose
        # the row).
        assert cache.get_parametric("h:long") is not None


# ---------------------------------------------------------------------------
# Bulk insert / stats / purge
# ---------------------------------------------------------------------------

class TestBulkAndOps:
    def test_bulk_put_writes_all_in_one_txn(self, cache):
        n = 500
        infos = [_mk_part(pn=f"PART-{i:04d}") for i in range(n)]
        t0 = time.perf_counter()
        written = cache.bulk_put_mpns(infos)
        dt = time.perf_counter() - t0
        assert written == n
        # 500 single-row commits would take ~500 ms; one transaction
        # should be a small fraction of that. Generous bound to avoid
        # flakes on slow CI but tight enough to catch a regression to
        # per-row autocommit.
        assert dt < 2.0, f"Bulk insert too slow ({dt:.2f}s) — commits per row?"
        assert cache.stats()["mpn_cache"] == n

    def test_stats_reports_correct_counts(self, cache):
        cache.put_mpn("X", _mk_part(pn="X"))
        cache.put_mpn_negative("Y")
        cache.put_url_probe("https://a.example/x.pdf", True)
        cache.put_url_probe("https://b.example/x.pdf", False)
        cache.put_parametric("h", "label", [_mk_part()])
        s = cache.stats()
        assert s["mpn_cache"] == 1
        assert s["mpn_negative"] == 1
        assert s["url_probe_cache"] == 2
        assert s["url_probe_valid"] == 1
        assert s["parametric_cache"] == 1

    def test_purge_stale_drops_only_old_rows(self, cache, monkeypatch):
        """purge_stale must delete rows past their TTL but leave fresh
        rows untouched — the seed script runs this after a long sweep
        and we don't want it nuking the parts we just wrote."""
        monkeypatch.setattr(cc, "IDENTITY_TTL_S", 0)
        monkeypatch.setattr(cc, "PARAMETRIC_TTL_S", 0)
        monkeypatch.setattr(cc, "URL_PROBE_TTL_S", 0)
        monkeypatch.setattr(cc, "NEGATIVE_TTL_S", 0)
        cache.put_mpn("OLD", _mk_part(pn="OLD"))
        cache.put_url_probe("https://old.example/", True, is_trusted=False)
        cache.put_parametric("h:old", "old", [_mk_part()])
        cache.put_mpn_negative("OLD-MISS")
        time.sleep(1.1)
        # New rows go in just before the purge — survive.
        cache.put_mpn("NEW", _mk_part(pn="NEW"))
        cache.put_url_probe("https://new.example/", True, is_trusted=False)
        out = cache.purge_stale()
        assert out["mpn_identity"] == 1
        assert out["url_probe"] == 1
        assert out["parametric"] == 1
        assert out["negative"] == 1
        # Fresh rows survived.
        assert cache.get_mpn("NEW") is not None
        assert cache.get_url_probe("https://new.example/") is not None


# ---------------------------------------------------------------------------
# Singleton + opt-out
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_default_returns_same_instance(self, tmp_path, monkeypatch):
        monkeypatch.setenv("COMPONENT_CACHE_PATH", str(tmp_path / "singleton.db"))
        reset_default()
        a = get_default()
        b = get_default()
        assert a is b
        reset_default()

    def test_reset_default_creates_new_instance(self, tmp_path, monkeypatch):
        monkeypatch.setenv("COMPONENT_CACHE_PATH", str(tmp_path / "reset.db"))
        reset_default()
        a = get_default()
        reset_default()
        b = get_default()
        assert a is not b
        reset_default()

    def test_cache_disabled_env_var(self, monkeypatch):
        monkeypatch.setenv("COMPONENT_CACHE_DISABLED", "1")
        assert cache_disabled() is True
        monkeypatch.setenv("COMPONENT_CACHE_DISABLED", "")
        assert cache_disabled() is False
