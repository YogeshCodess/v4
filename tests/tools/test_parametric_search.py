"""Tests for tools/parametric_search.py.

Distributor APIs are patched at the `tools.parametric_search` import
surface so the tests never hit the network.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tools import distributor_search, parametric_search as _ps
from tools.digikey_api import PartInfo
from tools.parametric_search import (
    _build_query,
    _dedupe_by_mpn,
    _is_obsolete,
    _normalise_stage,
    find_candidates,
    reset_cache,
)


# Every test clears the in-process caches first — otherwise `find_candidates`
# results would leak across tests (both caches are process-global). The
# cross-cache warm in `find_candidates` also populates distributor_search's
# exact-MPN cache, so resetting both keeps the two in lock-step across
# test runs.
@pytest.fixture(autouse=True)
def _clear_parametric_cache(monkeypatch):
    # Disable the persistent on-disk RAG cache so write-throughs from
    # find_candidates / lookup don't leak into the next test via the
    # shared SQLite file. Persistent cache has its own dedicated suite
    # (tests/services/test_component_cache.py) — we only exercise the
    # in-process behaviour here.
    monkeypatch.setenv("COMPONENT_CACHE_DISABLED", "1")
    reset_cache()
    distributor_search.reset_cache()
    yield
    reset_cache()
    distributor_search.reset_cache()


def _pi(
    pn: str,
    *,
    source: str = "digikey",
    lifecycle: str = "active",
    mfr: str = "Acme",
    ds: str | None = "https://ds/x.pdf",
) -> PartInfo:
    return PartInfo(
        part_number=pn,
        manufacturer=mfr,
        description=f"Part {pn}",
        datasheet_url=ds,
        product_url=f"https://product/{pn}",
        lifecycle_status=lifecycle,
        unit_price_usd=None,
        stock_quantity=100,
        source=source,
    )


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

def test_normalise_stage_canonicalises_case_and_separators():
    assert _normalise_stage("LNA") == "lna"
    assert _normalise_stage("  Bias-Tee ") == "bias_tee"
    assert _normalise_stage("ADC ") == "adc"


def test_build_query_seeds_known_stage():
    q = _build_query("lna", "2-18 GHz")
    assert "low noise amplifier" in q.lower()
    assert "2-18 GHz" in q


def test_build_query_falls_back_to_stage_string_when_unknown():
    q = _build_query("magic-widget", "red")
    # Unknown stage is used verbatim as the seed.
    assert "magic-widget" in q
    assert "red" in q


def test_build_query_handles_blank_hint():
    q = _build_query("mixer", "")
    assert "mixer" in q.lower()
    assert q.strip() == q  # no trailing whitespace


# ---------------------------------------------------------------------------
# Deduplication / filtering
# ---------------------------------------------------------------------------

def test_dedupe_keeps_first_occurrence_case_insensitive():
    a = _pi("ADL8107", source="digikey")
    b = _pi("adl8107", source="mouser")  # same MPN, different case
    c = _pi("BGA7210", source="mouser")
    out = _dedupe_by_mpn([a, b, c])
    assert [p.part_number for p in out] == ["ADL8107", "BGA7210"]
    # DigiKey's entry must win on duplicates — drives downstream lifecycle.
    assert out[0].source == "digikey"


def test_dedupe_drops_blank_mpns():
    a = _pi("")
    b = _pi("VALID-1")
    out = _dedupe_by_mpn([a, b])
    assert [p.part_number for p in out] == ["VALID-1"]


def test_is_obsolete():
    assert _is_obsolete(_pi("X", lifecycle="obsolete")) is True
    assert _is_obsolete(_pi("X", lifecycle="active")) is False
    assert _is_obsolete(_pi("X", lifecycle="nrnd")) is False
    assert _is_obsolete(_pi("X", lifecycle="unknown")) is False


# ---------------------------------------------------------------------------
# find_candidates — orchestration
# ---------------------------------------------------------------------------

@pytest.fixture
def both_configured(monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "cid")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "cs")
    monkeypatch.setenv("MOUSER_API_KEY", "mk")


def test_merges_digikey_and_mouser_with_digikey_first(both_configured):
    dk = [_pi("A", source="digikey"), _pi("B", source="digikey")]
    ms = [_pi("C", source="mouser"), _pi("D", source="mouser")]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=ms):
        out = find_candidates("lna", "2-18 GHz")
    assert [p.part_number for p in out] == ["A", "B", "C", "D"]


def test_obsolete_parts_are_dropped_by_default(both_configured):
    dk = [_pi("A-EOL", lifecycle="obsolete"), _pi("A-OK", lifecycle="active")]
    ms = [_pi("B-NRND", lifecycle="nrnd"), _pi("B-ACT", lifecycle="active")]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=ms):
        out = find_candidates("lna", "")
    mpns = [p.part_number for p in out]
    assert "A-EOL" not in mpns, "obsolete parts must be filtered"
    # NRND is kept — still ship-capable; caller can warn separately.
    assert "B-NRND" in mpns
    assert "A-OK" in mpns and "B-ACT" in mpns


def test_can_opt_in_to_obsolete_parts(both_configured):
    dk = [_pi("A-EOL", lifecycle="obsolete")]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=[]):
        out = find_candidates("lna", "", drop_obsolete=False)
    assert [p.part_number for p in out] == ["A-EOL"]


def test_max_total_caps_result_list(both_configured):
    dk = [_pi(f"D{i}") for i in range(10)]
    ms = [_pi(f"M{i}") for i in range(10)]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=ms):
        out = find_candidates("lna", "", max_per_source=5, max_total=6)
    assert len(out) == 6


def test_duplicate_mpn_across_sources_collapsed(both_configured):
    dk = [_pi("SHARED", source="digikey", mfr="MfgA")]
    ms = [_pi("SHARED", source="mouser", mfr="MfgB")]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=ms):
        out = find_candidates("lna", "")
    assert len(out) == 1
    assert out[0].source == "digikey"  # DigiKey wins on overlap


def test_empty_query_returns_empty(both_configured):
    # Blank stage + blank hint → nothing to search for.
    with patch("tools.parametric_search.digikey_api.keyword_search") as dk_mock, \
         patch("tools.parametric_search.mouser_api.keyword_search") as ms_mock:
        out = find_candidates("", "")
    assert out == []
    dk_mock.assert_not_called()
    ms_mock.assert_not_called()


def test_digikey_exception_does_not_break_mouser(both_configured):
    """If DigiKey throws (token expiry, 5xx, etc.) we must still return
    Mouser's results — retrieval must degrade gracefully."""
    ms = [_pi("M1", source="mouser")]
    with patch("tools.parametric_search.digikey_api.keyword_search",
               side_effect=RuntimeError("digikey exploded")), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=ms):
        out = find_candidates("lna", "2-18 GHz")
    assert [p.part_number for p in out] == ["M1"]


def test_skips_digikey_when_not_configured(monkeypatch):
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("MOUSER_API_KEY", "mk")
    ms = [_pi("M1", source="mouser")]
    with patch("tools.parametric_search.digikey_api.keyword_search") as dk_mock, \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=ms):
        out = find_candidates("lna", "")
    dk_mock.assert_not_called()
    assert [p.part_number for p in out] == ["M1"]


def test_skips_mouser_when_not_configured(monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "cid")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "cs")
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    dk = [_pi("D1", source="digikey")]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search") as ms_mock:
        out = find_candidates("lna", "")
    ms_mock.assert_not_called()
    assert [p.part_number for p in out] == ["D1"]


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------

def test_repeat_query_served_from_cache(both_configured):
    """Second call with identical args must not hit the distributors."""
    dk = [_pi("A", source="digikey")]
    ms = [_pi("B", source="mouser")]
    with patch("tools.parametric_search.digikey_api.keyword_search",
               return_value=dk) as dk_mock, \
         patch("tools.parametric_search.mouser_api.keyword_search",
               return_value=ms) as ms_mock:
        first = find_candidates("lna", "2-18 GHz")
        second = find_candidates("lna", "2-18 GHz")
    assert [p.part_number for p in first] == ["A", "B"]
    assert [p.part_number for p in second] == ["A", "B"]
    # Each distributor was called exactly once — second request was a cache hit.
    assert dk_mock.call_count == 1
    assert ms_mock.call_count == 1


def test_cache_key_is_case_insensitive(both_configured):
    """Case / whitespace differences in stage or hint should collide in the cache."""
    dk = [_pi("A", source="digikey")]
    with patch("tools.parametric_search.digikey_api.keyword_search",
               return_value=dk) as dk_mock, \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=[]):
        find_candidates("LNA", "2-18 GHz")
        find_candidates("  lna ", "2-18 GHz")   # normalised → same key
        find_candidates("Lna", "2-18 GHZ")      # hint case differs → same key
    assert dk_mock.call_count == 1


def test_smaller_max_per_source_hits_after_larger_seed(both_configured):
    """A larger fetch superseeds smaller requests — slicing is fine.

    Real-world driver: the seed script populates the cache with
    max_per_source=50; agents at runtime call with max_per_source=5 and
    must hit that cached superset. Before this fix, max_per_source was
    in the cache key and the agent always missed."""
    # Larger fetch first — 10 from each source (up to 20 merged).
    dk_large = [_pi(f"D{i}") for i in range(10)]
    ms_large = [_pi(f"M{i}") for i in range(10)]
    with patch("tools.parametric_search.digikey_api.keyword_search",
               return_value=dk_large) as dk_mock, \
         patch("tools.parametric_search.mouser_api.keyword_search",
               return_value=ms_large) as ms_mock:
        out_big = find_candidates("lna", "", max_per_source=10)
        # Subsequent smaller request must hit the cached superset.
        out_small = find_candidates("lna", "", max_per_source=5)
    assert len(out_big) == 20            # full merge
    assert len(out_small) == 10          # 2 * 5 = 10
    assert dk_mock.call_count == 1       # second call was a cache hit
    assert ms_mock.call_count == 1


def test_larger_max_per_source_misses_after_smaller_seed(both_configured):
    """When the cached list is too small to satisfy the new request, we
    must re-fetch instead of returning a short list — preserves the
    `len(result) <= 2*max_per_source` contract."""
    dk_small = [_pi(f"D{i}") for i in range(5)]
    with patch("tools.parametric_search.digikey_api.keyword_search",
               return_value=dk_small) as dk_mock, \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=[]):
        find_candidates("lna", "", max_per_source=5)
        find_candidates("lna", "", max_per_source=10)
    # Cached 5 < needed 20, so the second call refetched.
    assert dk_mock.call_count == 2


def test_cache_can_be_reset(both_configured):
    dk = [_pi("A", source="digikey")]
    with patch("tools.parametric_search.digikey_api.keyword_search",
               return_value=dk) as dk_mock, \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=[]):
        find_candidates("lna", "x")
        reset_cache()
        find_candidates("lna", "x")
    # Cache was cleared between calls → DigiKey was queried twice.
    assert dk_mock.call_count == 2


def test_cache_hit_honours_max_total(both_configured):
    """max_total is applied *after* the cache — changing it between calls
    must slice the cached shortlist correctly."""
    dk = [_pi(f"D{i}") for i in range(5)]
    ms = [_pi(f"M{i}") for i in range(5)]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=ms):
        first = find_candidates("lna", "", max_per_source=5, max_total=10)
        second = find_candidates("lna", "", max_per_source=5, max_total=3)
    assert len(first) == 10
    assert len(second) == 3


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------

def test_digikey_and_mouser_queried_in_parallel(both_configured):
    """Wall-clock time must be ≈ max(DigiKey, Mouser), not their sum.

    We simulate each distributor taking 200ms. Sequential would take
    ~400ms; parallel should finish in ~220ms or less.
    """
    import time as _time

    def slow_dk(*_a, **_kw):
        _time.sleep(0.2)
        return [_pi("A", source="digikey")]

    def slow_ms(*_a, **_kw):
        _time.sleep(0.2)
        return [_pi("B", source="mouser")]

    with patch("tools.parametric_search.digikey_api.keyword_search",
               side_effect=slow_dk), \
         patch("tools.parametric_search.mouser_api.keyword_search",
               side_effect=slow_ms):
        t0 = _time.monotonic()
        out = find_candidates("lna", "parallel-test")
        elapsed = _time.monotonic() - t0

    assert [p.part_number for p in out] == ["A", "B"]
    # Generous upper bound (0.35s) to absorb CI jitter but still well
    # below the 0.4s sequential lower bound.
    assert elapsed < 0.35, f"expected parallel exec, got {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Perf guard — TTL covers a full P1 elicitation session.
# ---------------------------------------------------------------------------

def test_cache_ttl_covers_a_full_p1_session():
    """The TTL must outlast `generate_requirements` so the finalize_p1
    audit can reuse the shortlists warmed during find_candidate_parts.

    A single `generate_requirements` call alone takes 3-5 min on glm-5.1;
    the old 60s TTL guaranteed every audit-phase lookup missed the cache.
    300 s (5 min) keeps the cross-cache warm (see below) effective
    through the audit."""
    assert _ps._CACHE_TTL_S >= 300.0, (
        "Perf guardrail: shortening the cache TTL below 300 s reintroduces "
        "the audit-phase cache-miss storm that drove P1 to ~12 min."
    )


# ---------------------------------------------------------------------------
# Cross-cache warm — populate distributor_search's exact-MPN cache from
# keyword-search results so the audit phase doesn't re-hit the vendor
# APIs for parts we've already pulled shortlist data for.
# ---------------------------------------------------------------------------

def test_find_candidates_prewarms_distributor_cache(both_configured):
    dk = [_pi("ADL8107", source="digikey")]
    ms = [_pi("BGA7210", source="mouser")]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=ms):
        find_candidates("lna", "2-18 GHz")

    # Both MPNs must appear in distributor_search._cache under the
    # upper-cased key used by distributor_search.lookup.
    assert "ADL8107" in distributor_search._cache
    assert "BGA7210" in distributor_search._cache
    # Stored value is the PartInfo itself, not a copy/None.
    assert distributor_search._cache["ADL8107"].part_number == "ADL8107"
    assert distributor_search._cache["BGA7210"].part_number == "BGA7210"


def test_cross_cache_returns_part_on_lookup_without_api_call(both_configured):
    """After a keyword-search warm, `distributor_search.lookup` for any
    shortlisted MPN must serve from cache without calling DigiKey or Mouser."""
    dk = [_pi("ADL8107", source="digikey")]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=[]):
        find_candidates("lna", "2-18 GHz")

    # Now `lookup("ADL8107")` must hit the cache. We patch BOTH distributor
    # APIs at the module path `distributor_search` uses, so a cache miss
    # would surface as an assertion failure here.
    with patch("tools.distributor_search.digikey_api.lookup") as dk_mock, \
         patch("tools.distributor_search.mouser_api.lookup") as ms_mock:
        info = distributor_search.lookup("ADL8107")
    assert info is not None and info.part_number == "ADL8107"
    dk_mock.assert_not_called()
    ms_mock.assert_not_called()


def test_cross_cache_does_not_overwrite_existing_entry(both_configured):
    """If `distributor_search.lookup` has already populated an MPN (e.g. a
    prior manual probe with its own datasheet-URL verification), the
    parametric_search warm must NOT clobber it — lookup's result is more
    authoritative downstream."""
    pre_seeded = _pi("ADL8107", source="digikey", mfr="PRE-SEEDED")
    with distributor_search._cache_lock:
        distributor_search._cache["ADL8107"] = pre_seeded

    dk = [_pi("ADL8107", source="digikey", mfr="SHOULD-NOT-WIN")]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=[]):
        find_candidates("lna", "2-18 GHz")

    assert distributor_search._cache["ADL8107"].manufacturer == "PRE-SEEDED"


def test_cross_cache_handles_distributor_search_import_error(
    both_configured, monkeypatch
):
    """Defensive: if `tools.distributor_search` becomes unavailable for any
    reason (e.g. a refactor removes the module, or we hit a cyclic-import
    edge case during startup), `find_candidates` must still return results."""
    import sys

    # Snapshot then blow away the cached module. A fresh `from tools import
    # distributor_search as _ds` inside find_candidates will then raise
    # ImportError, which our try/except swallows at log.debug level.
    original = sys.modules.pop("tools.distributor_search", None)
    monkeypatch.setitem(sys.modules, "tools.distributor_search", None)

    try:
        dk = [_pi("ADL8107", source="digikey")]
        with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
             patch("tools.parametric_search.mouser_api.keyword_search", return_value=[]):
            out = find_candidates("lna", "2-18 GHz")
        assert [p.part_number for p in out] == ["ADL8107"]
    finally:
        if original is not None:
            sys.modules["tools.distributor_search"] = original
        else:
            sys.modules.pop("tools.distributor_search", None)
