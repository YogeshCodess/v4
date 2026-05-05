"""
Unified part-number lookup: DigiKey → Mouser → local seed.

`services/rf_audit` uses this to decide whether a part number the LLM
emitted is real. Order matters:

  1. **DigiKey** — broadest catalogue, authoritative MPN database.
  2. **Mouser** — fallback; catches parts DigiKey drops or hasn't added yet.
  3. **Local seed** (`data/sample_components.json`) — air-gap / offline
     demo path; always populated from curated entries.

Missing everywhere → the MPN is treated as **hallucinated** and an
audit issue is raised.

Opt-outs:
  SKIP_DISTRIBUTOR_LOOKUP=1  → never hit the network; use seed only.
  SKIP_DIGIKEY=1             → skip DigiKey, still try Mouser.
  SKIP_MOUSER=1              → skip Mouser.
  SKIP_DATASHEET_VERIFY=1    → trust datasheet URLs without HEAD probe.

Two orthogonal features that make the lookup forgiving enough for
real-world LLM output:

  - `normalize_mpn` strips common packaging / reel / revision suffixes
    (-R7, -TR, -REEL, -01WTG, /S, …) so a strict exact-match lookup
    can still find parts the LLM typed with a variant suffix.

  - On an accepted result we HEAD-verify the distributor's datasheet
    URL and strip it if the probe fails, so a stale URL in Mouser's
    index can't leak into the final BOM.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import replace
from pathlib import Path
from typing import Optional

from tools.digikey_api import PartInfo
from tools import digikey_api, mouser_api

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SEED_PATH = _REPO_ROOT / "data" / "sample_components.json"

# Process-local lookup cache — avoids hammering the APIs for a BOM
# where the same MPN appears on multiple edges (e.g. power rail fans
# out to 10 ICs, all with the same decoupling cap).
_cache_lock = threading.Lock()
_cache: dict[str, Optional[PartInfo]] = {}
_seed_index: Optional[dict[str, dict]] = None


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------

def _skip_all_network() -> bool:
    return os.getenv("SKIP_DISTRIBUTOR_LOOKUP", "").strip() in {"1", "true", "yes"}


def _skip_digikey() -> bool:
    return os.getenv("SKIP_DIGIKEY", "").strip() in {"1", "true", "yes"}


def _skip_mouser() -> bool:
    return os.getenv("SKIP_MOUSER", "").strip() in {"1", "true", "yes"}


def _skip_datasheet_verify() -> bool:
    return os.getenv("SKIP_DATASHEET_VERIFY", "").strip() in {"1", "true", "yes"}


# ---------------------------------------------------------------------------
# MPN normalisation — fuzzy-match suffix stripping
# ---------------------------------------------------------------------------

# Suffix patterns we strip when exact match fails, in priority order.
# All matches are anchored to the end and are case-insensitive. Each
# entry is a regex; order matters — longer / more-specific first so a
# part like "ADL8107-REEL7" strips to "ADL8107", not "ADL8107-REE".
_SUFFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[-/]REEL\d*$", re.IGNORECASE),
    re.compile(r"[-/]TAPE$", re.IGNORECASE),
    re.compile(r"-TR\d+$", re.IGNORECASE),           # -TR1000
    re.compile(r"-?T&?R$", re.IGNORECASE),           # -TR, -T&R
    re.compile(r"-CT-ND$", re.IGNORECASE),           # DigiKey ordering suffix
    re.compile(r"-ND$", re.IGNORECASE),              # DigiKey part suffix
    re.compile(r"-R\d+$", re.IGNORECASE),            # -R7
    re.compile(r"/(?:TR|RL|T|R)$", re.IGNORECASE),   # /TR, /RL
    re.compile(r"-(?:PBFREE|PBF|LF|RoHS)$", re.IGNORECASE),
    re.compile(r"-\d{2,3}[A-Z]{2,5}$"),              # -01WTG / -300AZD
    re.compile(r"[-/]SAMPLE$", re.IGNORECASE),
    re.compile(r"\s+$"),                             # trailing whitespace
)


def normalize_mpn(part_number: str) -> str:
    """Return an alternate MPN with packaging / revision suffixes stripped.

    Returns the input unchanged when no pattern matches. Multiple
    patterns may match in sequence — we apply the first that does and
    stop (one strip per call); callers can feed the output back in to
    strip further suffixes if needed.
    """
    if not part_number:
        return ""
    pn = str(part_number).strip().upper()
    for pat in _SUFFIX_PATTERNS:
        m = pat.search(pn)
        if m:
            return pn[:m.start()].rstrip("-/ ")
    return pn


def _fuzzy_candidates(part_number: str) -> list[str]:
    """Return a short list of MPN variants to try: original first, then
    progressively-stripped forms. Dedupes the list so we don't query
    twice for the same key."""
    seen: set[str] = set()
    out: list[str] = []
    candidate = part_number.strip()
    for _ in range(3):  # three strip passes at most — covers "-R7-REEL-TR"
        key = candidate.strip().upper()
        if not key or key in seen:
            break
        seen.add(key)
        out.append(candidate)
        stripped = normalize_mpn(candidate)
        if stripped == key:
            break
        candidate = stripped
    return out


# ---------------------------------------------------------------------------
# Seed-JSON fallback
# ---------------------------------------------------------------------------

def _load_seed_index() -> dict[str, dict]:
    """Lazy-load the seed JSON into a {part_number_upper: entry} dict."""
    global _seed_index
    if _seed_index is not None:
        return _seed_index
    idx: dict[str, dict] = {}
    try:
        data = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
        for entry in data.get("components", []):
            pn = (entry.get("part_number") or "").strip().upper()
            if pn:
                idx[pn] = entry
    except Exception as exc:
        log.warning("distributor_search.seed_load_failed: %s", exc)
    _seed_index = idx
    return idx


def _seed_lookup(part_number: str) -> Optional[PartInfo]:
    pn = (part_number or "").strip().upper()
    if not pn:
        return None
    entry = _load_seed_index().get(pn)
    if not entry:
        return None
    status = (entry.get("lifecycle_status") or "unknown").lower()
    return PartInfo(
        part_number=entry.get("part_number", part_number),
        manufacturer=entry.get("manufacturer", ""),
        description=entry.get("description", ""),
        datasheet_url=entry.get("datasheet_url"),
        product_url=None,
        lifecycle_status=status,
        unit_price_usd=entry.get("estimated_cost_usd"),
        stock_quantity=None,
        source="seed",
    )


# ---------------------------------------------------------------------------
# Datasheet HEAD verification (tier-3 hardening)
# ---------------------------------------------------------------------------

def _verify_datasheet(info: PartInfo) -> PartInfo:
    """Resolve the best-available datasheet URL for `info` and rewrite it
    on the returned PartInfo.

    Replaces the old "strip on probe failure" behaviour, which was
    leaving BOM rows with empty Datasheet cells when the distributor's
    primary PDF URL had 404'd. The new resolver walks a fallback chain
    (distributor PDF → distributor product page → manufacturer guess →
    Google search), returns the first link that probes OK, and never
    returns empty. URL probes are cache-aside via
    `services.component_cache` so warm-cache runs do zero HTTP.

    Controlled by SKIP_DATASHEET_VERIFY=1 (skips probe + cache; trusts
    whatever URL the distributor handed back)."""
    if _skip_datasheet_verify():
        return info
    if not info.datasheet_url and not info.product_url:
        # Nothing to resolve from — fall through to the chain's Google
        # fallback so the BOM row at least has a search link.
        try:
            from tools.datasheet_resolver import resolve_url
        except Exception:
            return info
        try:
            return replace(info, datasheet_url=resolve_url(info, timeout=3.0))
        except Exception as exc:  # noqa: BLE001 — never break the live path
            log.debug("distributor_search.resolver_err pn=%s: %s",
                      info.part_number, exc)
            return info
    try:
        from tools.datasheet_resolver import resolve_url
    except Exception:
        return info
    try:
        best = resolve_url(info, timeout=3.0)
    except Exception as exc:  # noqa: BLE001
        log.debug("distributor_search.resolver_err pn=%s: %s",
                  info.part_number, exc)
        return info
    if best and best != info.datasheet_url:
        log.info("distributor_search.datasheet_repaired pn=%s old=%s new=%s",
                 info.part_number, info.datasheet_url, best)
    return replace(info, datasheet_url=best)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reset_cache() -> None:
    """Clear the process-local lookup cache + seed index. Test helper."""
    global _seed_index
    with _cache_lock:
        _cache.clear()
    _seed_index = None
    digikey_api.reset_cache()


def lookup(part_number: str, *, timeout_s: float = 12.0) -> Optional[PartInfo]:
    """Return a `PartInfo` for `part_number`, consulting in order:
    persistent disk cache, in-memory cache, DigiKey, Mouser, local seed.
    None when every tier misses.

    Three resilience features layered on top of the raw tiered lookup:

      1. **Persistent cache** — `services.component_cache` stores past
         hits across server restarts. A hot MPN is served from a single
         SQLite read (~ms) instead of a 200-2000 ms DigiKey round-trip.
         A negative cache (per-MPN "doesn't exist anywhere" with a
         shorter 24 h TTL) prevents thrashing on hallucinated MPNs that
         the LLM keeps re-emitting. Disabled by COMPONENT_CACHE_DISABLED=1.
      2. Fuzzy MPN match — if the original MPN misses everywhere, strip
         packaging/revision suffixes (-R7, -TR1000, -REEL, -01WTG, /S,
         etc.) and retry. The resulting PartInfo carries the distributor's
         canonical part_number, not the stripped variant.
      3. Datasheet URL resolver — `tools.datasheet_resolver` walks a
         fallback chain (distributor PDF → product page → mfr guess →
         Google search) so every component ends up with a working link.
         Replaces the old "strip on HEAD failure" behaviour that left
         the BOM with empty Datasheet cells.
    """
    if not part_number:
        return None
    key = part_number.strip().upper()

    # In-memory cache (per-process, fastest path — typical hit during a
    # single P1 finalize where the same MPN appears on multiple edges).
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    # Persistent on-disk cache (RAG layer). Survives server restart and
    # is shared across projects, so common RF parts (HMC8410, ADL8107,
    # etc.) are served from disk even on a cold process.
    if not _cache_disabled():
        try:
            from services.component_cache import get_default
            hit = get_default().get_mpn(part_number)
        except Exception as exc:  # noqa: BLE001 — cache must never break live path
            log.debug("distributor_search.persistent_cache_err pn=%s: %s",
                      part_number, exc)
            hit = None
        if hit is not None:
            if hit.is_negative:
                # Negatively cached miss within NEGATIVE_TTL. Don't burn
                # an API call; the LLM will see a hallucinated_part flag
                # the same as on a live miss.
                with _cache_lock:
                    _cache[key] = None
                return None
            if hit.part_info is not None:
                # Promote to in-memory cache for the rest of this run.
                with _cache_lock:
                    _cache[key] = hit.part_info
                return hit.part_info

    info: Optional[PartInfo] = None
    skip_net = _skip_all_network()

    # Try each candidate MPN (original first, then suffix-stripped
    # variants). Short-circuit on the first tier+variant that answers.
    for candidate in _fuzzy_candidates(part_number):
        if not skip_net and not _skip_digikey() and digikey_api.is_configured():
            info = digikey_api.lookup(candidate, timeout_s=timeout_s)
        if info is None and not skip_net and not _skip_mouser() and mouser_api.is_configured():
            info = mouser_api.lookup(candidate, timeout_s=timeout_s)
        if info is None:
            info = _seed_lookup(candidate)
        if info is not None:
            break

    if info is not None:
        info = _verify_datasheet(info)

    with _cache_lock:
        _cache[key] = info

    # Write through to the persistent cache. Negative result gets a
    # separate row with the shorter 24 h TTL — we want to re-query
    # eventually in case the part was added to a distributor between
    # runs, but not on every re-finalize within the same demo.
    if not _cache_disabled():
        try:
            from services.component_cache import get_default
            cache = get_default()
            if info is None:
                # Only cache misses when at least one live tier was
                # actually consulted; a SKIP_DISTRIBUTOR_LOOKUP=1 miss
                # is a "we didn't ask", not a "doesn't exist".
                if not skip_net:
                    cache.put_mpn_negative(part_number)
            else:
                cache.put_mpn(part_number, info)
        except Exception as exc:  # noqa: BLE001
            log.debug("distributor_search.persistent_cache_write_err pn=%s: %s",
                      part_number, exc)

    return info


def _cache_disabled() -> bool:
    """Honour the persistent-cache opt-out flag. Local helper so the
    import of `services.component_cache.cache_disabled` is lazy and
    avoids a hard dependency at module-import time (test order in
    pytest can otherwise import this module before the cache table
    exists)."""
    try:
        from services.component_cache import cache_disabled
        return cache_disabled()
    except Exception:
        return False


def batch_lookup(
    part_numbers: list[str], *, timeout_s: float = 12.0,
) -> dict[str, Optional[PartInfo]]:
    """Convenience wrapper — dict {part_number: PartInfo | None}. Serial
    for simplicity; most BOMs are <50 parts and latency dominates network
    round-trip, not CPU."""
    out: dict[str, Optional[PartInfo]] = {}
    for pn in part_numbers:
        out[pn] = lookup(pn, timeout_s=timeout_s)
    return out


def any_api_configured() -> bool:
    """True when at least one distributor can be queried live."""
    return digikey_api.is_configured() or mouser_api.is_configured()
