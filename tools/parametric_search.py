"""
Parametric component retrieval — closes the "LLM invents part numbers" gap.

The LLM used to pick components from its training knowledge and the audit
would only *retroactively* catch inventions (hallucinated_part blockers).
This tool flips the flow:

  stage + spec hint  -->  live distributor query  -->  real candidate list  -->  LLM picks one

The LLM never needs to invent an MPN; it selects from a shortlist of real,
in-stock parts returned by DigiKey + Mouser.

Public API:
    candidates = find_candidates("LNA", "2-18 GHz low noise wideband")
    for c in candidates:
        print(c.part_number, c.manufacturer, c.datasheet_url)

Results are de-duplicated by MPN (upper-cased), obsolete parts are dropped,
and the list is capped so the LLM's context stays bounded.

Performance:
  * DigiKey and Mouser are queried **in parallel** (ThreadPoolExecutor) —
    total latency is max(DigiKey, Mouser), not sum.
  * Successful results are cached in-process with a 60-second TTL keyed
    on (stage, hint, max_per_source, drop_obsolete). A typical P1 flow
    makes 7-10 retrieval calls over ~30s; the cache absorbs repeated
    stages (e.g. LNA queried twice with the same hint) and cuts the
    wall-clock cost of the second query to ~0.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait as _futures_wait
from typing import Iterable, Optional

from tools import digikey_api, mouser_api
from tools.digikey_api import PartInfo

log = logging.getLogger(__name__)


# Canonical RF-chain stages → default keyword boost. Callers pass extra
# spec context; this map seeds the query so the right distributor
# category is matched even when the caller's hint is sparse.
_STAGE_KEYWORDS: dict[str, str] = {
    "lna":         "low noise amplifier LNA",
    "driver_amp":  "driver amplifier RF",
    "gain_block":  "gain block RF amplifier",
    "pa":          "RF power amplifier",
    "mixer":       "RF mixer double balanced",
    "limiter":     "RF limiter PIN diode",
    "bpf":         "bandpass filter RF",
    "lpf":         "lowpass filter RF",
    "hpf":         "highpass filter RF",
    "preselector": "ceramic bandpass filter RF preselector",
    "saw":         "SAW filter RF",
    "splitter":    "power splitter combiner RF",
    "balun":       "balun transformer RF",
    "attenuator":  "RF attenuator step",
    "switch":      "RF switch SPDT SP4T",
    "vco":         "VCO voltage controlled oscillator",
    "pll":         "PLL synthesiser RF",
    "adc":         "analog to digital converter",
    "dac":         "digital to analog converter",
    "fpga":        "FPGA",
    "mcu":         "microcontroller ARM Cortex",
    "ldo":         "LDO voltage regulator",
    "buck":        "buck DC-DC converter",
    "bias_tee":    "bias tee RF",
    "tcxo":        "TCXO temperature compensated oscillator",
    "ocxo":        "OCXO oven controlled oscillator",
}


# ---------------------------------------------------------------------------
# In-process cache
# ---------------------------------------------------------------------------

# TTL spans a full P1 elicitation session. A typical run:
#   Round 1-3 (find_candidate_parts fan-out)  ->  LLM generate_requirements
#   (~3-5 min on glm-5.1)  ->  finalize_p1 audit (exact MPN re-lookups)
# With a 60 s TTL the second half of that pipeline always missed the cache
# because generate_requirements alone exceeds the window. 300 s (5 min)
# keeps the shortlist warm through the end of finalize_p1's audit so the
# exact-MPN lookup of every chosen component can be served from the in-
# process cache we pre-populate below (see `find_candidates`). Distributor
# stock/lifecycle moves on an hour+ cadence, so 5 min is still fresh.
_CACHE_TTL_S = 300.0

_cache_lock = threading.Lock()
_cache: dict[tuple, tuple[float, list[PartInfo]]] = {}


def reset_cache() -> None:
    """Clear the retrieval cache. Test helper; also exposed for callers
    that want to force a fresh pull after mutating env vars."""
    with _cache_lock:
        _cache.clear()


def _cache_get(key: tuple) -> Optional[tuple[int, list[PartInfo]]]:
    """Return ``(fetched_max_per_source, parts)`` or None on miss/stale.

    The stored ``max_per_source`` lets callers decide whether the cached
    list is large enough to satisfy a new request — see ``find_candidates``
    for the gating rule.
    """
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        ts, fetched_mps, value = entry
        if now - ts > _CACHE_TTL_S:
            # Stale — evict so repeated misses don't grow the dict.
            _cache.pop(key, None)
            return None
        # Return a shallow copy so callers can mutate without affecting
        # future cache hits. PartInfo is frozen, so list() is enough.
        return fetched_mps, list(value)


def _cache_put(key: tuple, max_per_source: int, value: list[PartInfo]) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), int(max_per_source), list(value))


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------

def _normalise_stage(stage: str) -> str:
    return (stage or "").strip().lower().replace("-", "_").replace(" ", "_")


def _build_query(stage: str, hint: str) -> str:
    """Compose the keyword query sent to each distributor.

    Uses the stage's canonical keyword seed when we know the stage,
    otherwise falls back to the stage string itself. The caller's
    `hint` (e.g. `"2-18 GHz NF < 2 dB"`) is appended so distributor
    search engines can score on the spec constraints.
    """
    seed = _STAGE_KEYWORDS.get(_normalise_stage(stage), stage)
    parts = [seed.strip(), (hint or "").strip()]
    return " ".join(p for p in parts if p)


def _is_obsolete(info: PartInfo) -> bool:
    return info.lifecycle_status == "obsolete"


def _dedupe_by_mpn(infos: Iterable[PartInfo]) -> list[PartInfo]:
    """Keep the first occurrence of each MPN (case-insensitive).

    Order matters: callers pass DigiKey results before Mouser so DigiKey
    wins on overlap — DigiKey exposes a structured `ProductStatus`
    whereas Mouser's `LifecycleStatus` is occasionally `None`, and the
    structured status drives lifecycle filtering downstream.
    """
    seen: set[str] = set()
    out: list[PartInfo] = []
    for info in infos:
        key = (info.part_number or "").strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(info)
    return out


# ---------------------------------------------------------------------------
# Parallel fetchers
# ---------------------------------------------------------------------------

def _fetch_digikey(query: str, max_per_source: int, timeout_s: float) -> list[PartInfo]:
    if not digikey_api.is_configured():
        return []
    try:
        return digikey_api.keyword_search(
            query, limit=max_per_source, timeout_s=timeout_s,
        )
    except Exception as exc:
        log.warning("parametric_search.digikey_failed q=%r: %s", query, exc)
        return []


def _fetch_mouser(query: str, max_per_source: int, timeout_s: float) -> list[PartInfo]:
    if not mouser_api.is_configured():
        return []
    try:
        return mouser_api.keyword_search(
            query, records=max_per_source, timeout_s=timeout_s,
        )
    except Exception as exc:
        log.warning("parametric_search.mouser_failed q=%r: %s", query, exc)
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_candidates(
    stage: str,
    hint: str = "",
    *,
    max_per_source: int = 5,
    max_total: Optional[int] = None,
    drop_obsolete: bool = True,
    timeout_s: float = 10.0,
) -> list[PartInfo]:
    """Return a merged, de-duplicated candidate list from DigiKey + Mouser.

    Args:
        stage:             Canonical stage id (e.g. "lna", "mixer", "adc")
                           OR any free-text if the stage is unknown.
        hint:              Extra spec context (frequency range, NF target,
                           package, etc.) appended to the distributor query.
        max_per_source:    Upper bound on results fetched from each API.
        max_total:         Cap on the final merged list (default =
                           2 * max_per_source).
        drop_obsolete:     When True (default), parts with lifecycle
                           "obsolete" are removed from the result.
        timeout_s:         Per-call HTTP timeout.

    Returns:
        List of PartInfo, DigiKey hits first then Mouser, deduped.
        Empty list when both distributors fail or nothing matches.
    """
    query = _build_query(stage, hint)
    if not query:
        return []

    # Cache key normalises the inputs so "LNA" + "2-18 GHz" hits the same
    # bucket as "  lna " + "2-18 GHz ". `max_per_source` is intentionally
    # **not** in the key — a stored 50-result fetch can satisfy a 5-result
    # request via slicing. The stored entry remembers the budget it was
    # fetched with; we accept the hit when that budget is at least as
    # large as the new request. This is what lets the seed
    # (max_per_source=50) warm the cache for agent calls (defaults to 5).
    cache_key = (
        _normalise_stage(stage),
        (hint or "").strip().lower(),
        bool(drop_obsolete),
    )
    requested_mps = int(max_per_source)
    cached_entry = _cache_get(cache_key)
    if cached_entry is not None:
        fetched_mps, cached = cached_entry
        if fetched_mps >= requested_mps:
            log.debug("parametric_search.cache_hit q=%r stored_mps=%d req_mps=%d",
                      query, fetched_mps, requested_mps)
            if max_total is None:
                max_total = 2 * requested_mps
            return cached[:max_total]

    # Persistent on-disk cache (RAG layer). Survives restarts and lets a
    # warm cache short-circuit a 4-8 s parallel DigiKey+Mouser fan-out.
    # Hash the same canonical key tuple we use in-process so the two
    # cache layers never disagree. The persistent layer doesn't track
    # `max_per_source`, so we fall back to a size heuristic: the stored
    # list must hold at least `requested_mps` parts to be considered a
    # hit. The seed always fetches with mps=50 (much larger than any
    # agent call, default 5), so a stored entry shorter than the agent's
    # request usually means the universe is genuinely that small —
    # refetching won't add more parts. The small-pool case (e.g. PLL
    # returning 5 unique parts) therefore stays a cache hit; only when
    # the cached pool is smaller than what the caller asked for do we
    # bother going live again.
    persistent_hash = _persistent_query_hash(cache_key)
    if persistent_hash and not _persistent_disabled():
        try:
            from services.component_cache import get_default
            persistent = get_default().get_parametric(persistent_hash)
        except Exception as exc:  # noqa: BLE001 — cache must never break the live path
            log.debug("parametric_search.persistent_read_err q=%r: %s", query, exc)
            persistent = None
        if persistent and len(persistent) >= requested_mps:
            # Promote into the in-memory cache so subsequent calls within
            # this process don't pay another SQLite read. We don't know
            # the original fetch budget, but the size meets the request,
            # so tag it with `requested_mps` (the smallest budget it
            # could have served — conservative).
            _cache_put(cache_key, requested_mps, persistent)
            _cross_populate_distributor_cache(persistent)
            if max_total is None:
                max_total = 2 * requested_mps
            return persistent[:max_total]

    # Parallel fetch — total latency is max(DigiKey, Mouser), not sum.
    # Wall-clock deadline = per-request HTTP timeout + one 5-second
    # DigiKey 429 backoff + 2 s margin.  If a fetcher hasn't finished
    # within that window (e.g. DigiKey is still on its second retry) we
    # cancel it and return whatever the completed source gave us.  This
    # prevents a single 429 storm from holding up 12+ sequential stages
    # for 30 s each (old worst-case: 2 retries × 30 s cap = 60 s/stage).
    _wall_s = timeout_s + 7.0
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="parametric-search") as pool:
        dk_future = pool.submit(_fetch_digikey, query, max_per_source, timeout_s)
        ms_future = pool.submit(_fetch_mouser,  query, max_per_source, timeout_s)
        _done, _pending = _futures_wait([dk_future, ms_future], timeout=_wall_s)
        dk = dk_future.result() if dk_future in _done else []
        ms = ms_future.result() if ms_future in _done else []
        for _f in _pending:
            _f.cancel()
    if _pending:
        log.warning(
            "parametric_search.deadline stage=%r wall_s=%.0f "
            "pending_sources=%d — returning %d partial result(s)",
            stage, _wall_s, len(_pending), len(dk) + len(ms),
        )

    merged = _dedupe_by_mpn(dk + ms)
    if drop_obsolete:
        merged = [p for p in merged if not _is_obsolete(p)]

    # Cache the merged+filtered list *before* applying max_total — that
    # lets a subsequent call with a different max_total still reuse the
    # same underlying shortlist. We tag the entry with the budget used
    # for this fetch so a future larger-budget request can tell this
    # cache is too small and refetch.
    _cache_put(cache_key, requested_mps, merged)

    # Cross-populate the exact-MPN lookup cache used by `finalize_p1`'s
    # audit (services/rf_audit.py -> tools.distributor_search.lookup).
    _cross_populate_distributor_cache(merged)

    # Write through to the persistent on-disk parametric cache. Survives
    # restart, so the next demo run (even after a server bounce) hits
    # the cache instead of the live API. Best-effort: failure to write
    # the cache must never break the live response.
    if persistent_hash and not _persistent_disabled():
        try:
            from services.component_cache import get_default
            get_default().put_parametric(persistent_hash, query, merged)
        except Exception as exc:  # noqa: BLE001
            log.debug("parametric_search.persistent_write_err q=%r: %s", query, exc)
        # Also write each PartInfo into the persistent MPN cache so the
        # subsequent finalize_p1 audit (which calls `distributor_search.
        # lookup` per BOM row) hits warm rows instead of round-tripping.
        try:
            from services.component_cache import get_default
            get_default().bulk_put_mpns(merged)
        except Exception as exc:  # noqa: BLE001
            log.debug("parametric_search.bulk_mpn_err q=%r: %s", query, exc)

    # Cross-populate the exact-MPN lookup cache used by `finalize_p1`'s
    # audit (services/rf_audit.py -> tools.distributor_search.lookup).
    # Every PartInfo we just pulled via keyword_search IS a verified
    # distributor record — feeding it into the exact-lookup cache keyed
    # by upper-cased MPN means the audit phase skips a redundant round-
    # trip to DigiKey/Mouser per component. A typical P1 run has 10-15
    # BOM rows whose MPNs are a subset of the shortlists; this collapses
    # the 60-90 s audit down to ~10 s.
    #
    # Safety: we only INSERT — never overwrite an existing entry — so a
    # prior `distributor_search.lookup` result (which already ran the
    # datasheet URL verification pass) keeps priority. If the entry is
    # net-new, the downstream render-time URL probe in requirements_agent
    # still catches any dead datasheet link.
    try:
        from tools import distributor_search as _ds
        with _ds._cache_lock:  # type: ignore[attr-defined]
            for _info in merged:
                _key = (_info.part_number or "").strip().upper()
                if _key and _key not in _ds._cache:  # type: ignore[attr-defined]
                    _ds._cache[_key] = _info  # type: ignore[attr-defined]
    except Exception as _exc:  # pragma: no cover — best-effort warm
        log.debug("parametric_search.cross_cache_skip: %s", _exc)

    if max_total is None:
        max_total = 2 * max_per_source
    return merged[:max_total]


# ---------------------------------------------------------------------------
# Persistent-cache helpers
# ---------------------------------------------------------------------------

def _persistent_disabled() -> bool:
    """Lazy import of the cache opt-out so this module can be imported
    in environments where `services.component_cache` isn't available
    yet (e.g. partial test runs)."""
    try:
        from services.component_cache import cache_disabled
        return cache_disabled()
    except Exception:
        return False


def _persistent_query_hash(cache_key: tuple) -> str:
    """Stable string hash of the canonical in-process cache key. Using
    SHA-1 truncated to 16 hex chars keeps the on-disk PRIMARY KEY short
    while still being collision-free in practice (we never expect more
    than ~1k distinct queries)."""
    try:
        payload = repr(cache_key).encode("utf-8")
        return "param:" + hashlib.sha1(payload).hexdigest()[:16]
    except Exception:
        return ""


def _cross_populate_distributor_cache(merged: list[PartInfo]) -> None:
    """Inject every freshly-retrieved PartInfo into `distributor_search`'s
    in-memory exact-MPN cache so the subsequent finalize_p1 audit serves
    its per-row `lookup()` calls from RAM.

    Safety: we only INSERT — never overwrite an existing entry — so a
    prior `distributor_search.lookup` result (which already ran the
    datasheet URL verification pass) keeps priority. If the entry is
    net-new, the downstream render-time URL probe in requirements_agent
    still catches any dead datasheet link.
    """
    if not merged:
        return
    try:
        from tools import distributor_search as _ds
        with _ds._cache_lock:  # type: ignore[attr-defined]
            for _info in merged:
                _key = (_info.part_number or "").strip().upper()
                if _key and _key not in _ds._cache:  # type: ignore[attr-defined]
                    _ds._cache[_key] = _info  # type: ignore[attr-defined]
    except Exception as _exc:  # pragma: no cover — best-effort warm
        log.debug("parametric_search.cross_cache_skip: %s", _exc)


# Disable cache entirely when the caller sets this env var — useful for
# the `scripts/demo_parametric_search.py` smoke test and for ops who
# want to force live traffic in debugging.
if os.getenv("PARAMETRIC_SEARCH_DISABLE_CACHE", "").strip() in {"1", "true", "yes"}:
    _CACHE_TTL_S = 0.0
