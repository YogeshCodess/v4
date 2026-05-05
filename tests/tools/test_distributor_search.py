"""Tests for tools/distributor_search.py — the DigiKey → Mouser → seed
fallback chain. Individual clients are mocked; we exercise the order of
consultation + cache semantics + env-var opt-outs only."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tools import distributor_search
from tools.digikey_api import PartInfo


@pytest.fixture(autouse=True)
def _fresh_cache():
    distributor_search.reset_cache()
    yield
    distributor_search.reset_cache()


@pytest.fixture(autouse=True)
def _ensure_both_apis_configured(monkeypatch):
    """Default: both APIs configured so the fallback path is exercised.

    Also disables the persistent on-disk RAG cache (`services.component_cache`)
    so write-throughs from one test don't leak into the next via the
    shared SQLite file. These tests target the in-process fallback chain
    + RAM cache only; the persistent cache has its own dedicated suite
    (tests/services/test_component_cache.py) that exercises it directly.
    """
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
    monkeypatch.setenv("MOUSER_API_KEY", "z")
    monkeypatch.setenv("COMPONENT_CACHE_DISABLED", "1")
    monkeypatch.delenv("SKIP_DISTRIBUTOR_LOOKUP", raising=False)
    monkeypatch.delenv("SKIP_DIGIKEY", raising=False)
    monkeypatch.delenv("SKIP_MOUSER", raising=False)


def _info(source, pn="X", lifecycle="active"):
    return PartInfo(
        part_number=pn, manufacturer="M", description="",
        datasheet_url=None, product_url=None,
        lifecycle_status=lifecycle, unit_price_usd=None,
        stock_quantity=None, source=source,
    )


# ---------------------------------------------------------------------------
# Order of consultation
# ---------------------------------------------------------------------------

def test_digikey_hit_short_circuits_before_mouser_and_seed():
    with patch("tools.distributor_search.digikey_api.lookup",
               return_value=_info("digikey")) as mdk, \
         patch("tools.distributor_search.mouser_api.lookup") as mms, \
         patch("tools.distributor_search._seed_lookup") as msd:
        out = distributor_search.lookup("ADL8107")
    assert out is not None and out.source == "digikey"
    mdk.assert_called_once()
    mms.assert_not_called()
    msd.assert_not_called()


def test_falls_back_to_mouser_when_digikey_misses():
    with patch("tools.distributor_search.digikey_api.lookup",
               return_value=None), \
         patch("tools.distributor_search.mouser_api.lookup",
               return_value=_info("mouser")) as mms, \
         patch("tools.distributor_search._seed_lookup") as msd:
        out = distributor_search.lookup("ADL8107")
    assert out is not None and out.source == "mouser"
    mms.assert_called_once()
    msd.assert_not_called()


def test_falls_back_to_seed_when_both_apis_miss():
    with patch("tools.distributor_search.digikey_api.lookup", return_value=None), \
         patch("tools.distributor_search.mouser_api.lookup", return_value=None), \
         patch("tools.distributor_search._seed_lookup",
               return_value=_info("seed")):
        out = distributor_search.lookup("ADL8107")
    assert out is not None and out.source == "seed"


def test_returns_none_when_every_tier_misses():
    with patch("tools.distributor_search.digikey_api.lookup", return_value=None), \
         patch("tools.distributor_search.mouser_api.lookup", return_value=None), \
         patch("tools.distributor_search._seed_lookup", return_value=None):
        assert distributor_search.lookup("HALLUCINATED-Q1") is None


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def test_result_is_cached_per_part_number():
    with patch("tools.distributor_search.digikey_api.lookup",
               return_value=_info("digikey")) as mdk:
        distributor_search.lookup("ADL8107")
        distributor_search.lookup("ADL8107")
        distributor_search.lookup("ADL8107")
    assert mdk.call_count == 1  # cache hit on calls 2 + 3


def test_cache_uses_uppercased_key():
    with patch("tools.distributor_search.digikey_api.lookup",
               return_value=_info("digikey")) as mdk:
        distributor_search.lookup("adl8107")
        distributor_search.lookup("ADL8107")
        distributor_search.lookup("Adl8107")
    assert mdk.call_count == 1


def test_empty_part_number_is_not_cached_as_none():
    assert distributor_search.lookup("") is None
    # Cache should not have stored it — calling with a real name still works.
    with patch("tools.distributor_search.digikey_api.lookup",
               return_value=_info("digikey")) as mdk:
        distributor_search.lookup("ADL8107")
    mdk.assert_called_once()


# ---------------------------------------------------------------------------
# Env-var opt-outs
# ---------------------------------------------------------------------------

def test_skip_all_network_forces_seed_only(monkeypatch):
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
    with patch("tools.distributor_search.digikey_api.lookup") as mdk, \
         patch("tools.distributor_search.mouser_api.lookup") as mms, \
         patch("tools.distributor_search._seed_lookup",
               return_value=_info("seed")):
        out = distributor_search.lookup("ADL8107")
    assert out is not None and out.source == "seed"
    mdk.assert_not_called()
    mms.assert_not_called()


def test_skip_digikey_still_hits_mouser(monkeypatch):
    monkeypatch.setenv("SKIP_DIGIKEY", "1")
    with patch("tools.distributor_search.digikey_api.lookup") as mdk, \
         patch("tools.distributor_search.mouser_api.lookup",
               return_value=_info("mouser")):
        out = distributor_search.lookup("ADL8107")
    assert out is not None and out.source == "mouser"
    mdk.assert_not_called()


def test_skip_mouser_still_hits_digikey(monkeypatch):
    monkeypatch.setenv("SKIP_MOUSER", "1")
    with patch("tools.distributor_search.digikey_api.lookup",
               return_value=_info("digikey")), \
         patch("tools.distributor_search.mouser_api.lookup") as mms:
        out = distributor_search.lookup("ADL8107")
    assert out is not None and out.source == "digikey"
    mms.assert_not_called()


# ---------------------------------------------------------------------------
# Seed lookup (real file, 101 parts)
# ---------------------------------------------------------------------------

def test_seed_lookup_finds_adl8107(monkeypatch):
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
    out = distributor_search.lookup("ADL8107")
    assert out is not None
    assert out.source == "seed"
    assert out.manufacturer == "Analog Devices"
    assert out.lifecycle_status == "active"


def test_seed_lookup_misses_invented_mpn(monkeypatch):
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
    assert distributor_search.lookup("NONEXISTENT-MPN-9999") is None


# ---------------------------------------------------------------------------
# batch_lookup
# ---------------------------------------------------------------------------

def test_batch_lookup_returns_per_part_dict():
    with patch("tools.distributor_search.digikey_api.lookup",
               side_effect=[_info("digikey", pn="A"), None]):
        with patch("tools.distributor_search.mouser_api.lookup",
                   return_value=_info("mouser", pn="B")):
            out = distributor_search.batch_lookup(["A", "B"])
    assert out["A"] is not None and out["A"].source == "digikey"
    assert out["B"] is not None and out["B"].source == "mouser"


# ---------------------------------------------------------------------------
# any_api_configured
# ---------------------------------------------------------------------------

def test_any_api_configured_true_when_digikey_set(monkeypatch):
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    assert distributor_search.any_api_configured() is True


def test_any_api_configured_false_when_both_unset(monkeypatch):
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    assert distributor_search.any_api_configured() is False
