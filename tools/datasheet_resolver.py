"""
Datasheet URL resolver — guarantees every BOM row has a working "Datasheet"
link, even when the distributor's primary PDF URL has 404'd.

Problem the demo hit: `tools.distributor_search._verify_datasheet` HEAD-
probes the distributor-supplied datasheet URL and **strips it on probe
failure**, leaving the component with no link at all. The user then
sees a "Datasheet" column with empty cells — embarrassing and reads as
broken even though the part itself is real.

This resolver replaces the strip-on-failure behaviour with a fallback
chain that ALWAYS yields a clickable URL — and (revised 2026-04-24)
keeps every rung inside the DigiKey/Mouser ecosystem so users never
land on a stale manufacturer product page or a Google/DuckDuckGo
search results screen:

    1. distributor's primary datasheet PDF              (best — direct PDF)
    2. distributor's product page (digikey.com/...)     (always works)
    3. distributor's MPN-search URL                     (never null)

The first link in the chain whose probe passes (cached or live) wins.
Trusted-vendor URLs are accepted without a probe via the existing
`is_trusted_vendor_url` allowlist. The MPN-search fallback (rung 3)
is `https://www.digikey.com/en/products/result?keywords={MPN}` — it
never 404s, lands the user on the part's page when the catalog has
exactly one match, or a filterable list otherwise. Strictly better
than `analog.com/...html` guesses (often wrong) or Google search
fallbacks (off-platform).

History note: rungs 3 (`mfr_guess`) and 4 (`search_fallback` →
google.com) were removed on 2026-04-24 per user feedback that
manufacturer-site links were frequently incorrect. The
`_MFR_URL_PATTERNS` table and `_guess_mfr_url` helper are gone.

Cache integration: every probe result is read from / written to
`services.component_cache` so successive runs short-circuit without
any HTTP round-trip. The cache is opt-out via `COMPONENT_CACHE_DISABLED=1`.
"""
from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass

from services.component_cache import cache_disabled, get_default
from tools.datasheet_verify import is_trusted_vendor_url, verify_url
from tools.digikey_api import PartInfo

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resolver result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedDatasheet:
    """The chosen URL plus enough metadata for tests + log lines + the
    BOM table tooltip ("which fallback rung were we on?")."""
    url: str
    is_valid: bool
    source: str  # 'distributor_pdf' | 'product_url' | 'distributor_search'
    chain_position: int  # 1 = distributor PDF, 3 = DigiKey MPN search


# ---------------------------------------------------------------------------
# Probe with cache
# ---------------------------------------------------------------------------

def _probe(url: str, *, timeout: float = 3.0) -> bool:
    """Cache-aside HEAD/GET probe.

    Order of precedence:
      1. Trusted-vendor allowlist short-circuits (no probe).
      2. Persistent cache hit (within URL_PROBE_TTL).
      3. Live HEAD/GET via tools.datasheet_verify.verify_url.

    Live results are always written back to the cache; trusted results
    are stored too so air-gap demos still benefit from a hot cache.
    """
    if not url:
        return False
    trusted = is_trusted_vendor_url(url)
    if trusted:
        # Skip the probe entirely — vendor allowlist is the contract.
        # Still write to cache so stats show the URL was vetted.
        if not cache_disabled():
            try:
                get_default().put_url_probe(
                    url, True, status_code=200,
                    content_type="text/html", is_trusted=True,
                )
            except Exception as exc:  # noqa: BLE001 — cache failure must never break the live path
                log.debug("datasheet_resolver.cache_write_skipped url=%s: %s", url, exc)
        return True
    if not cache_disabled():
        try:
            cached = get_default().get_url_probe(url)
        except Exception as exc:  # noqa: BLE001
            log.debug("datasheet_resolver.cache_read_skipped url=%s: %s", url, exc)
            cached = None
        if cached is not None:
            return cached.is_valid
    # Live probe.
    try:
        ok = verify_url(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        log.debug("datasheet_resolver.probe_err url=%s: %s", url, exc)
        ok = False
    if not cache_disabled():
        try:
            get_default().put_url_probe(url, ok, is_trusted=False)
        except Exception as exc:  # noqa: BLE001
            log.debug("datasheet_resolver.cache_write_skipped url=%s: %s", url, exc)
    return ok


# ---------------------------------------------------------------------------
# Fallback chain construction
# ---------------------------------------------------------------------------

def _digikey_search_url(part_number: str) -> str:
    """DigiKey keyword-search URL for `part_number`."""
    q = urllib.parse.quote((part_number or "").strip())
    return f"https://www.digikey.com/en/products/result?keywords={q}"


def _mouser_search_url(part_number: str) -> str:
    """Mouser keyword-search URL for `part_number`."""
    q = urllib.parse.quote((part_number or "").strip())
    return f"https://www.mouser.com/c/?q={q}"


def _distributor_search_url(part_number: str, *, prefer_mouser: bool = False) -> str:
    """Last-resort URL — never empty, never off-platform.

    Returns DigiKey's keyword-search URL by default; pass
    `prefer_mouser=True` when the part originated from a Mouser
    lookup so the user lands on Mouser's catalog instead.

    Behaviour for both endpoints:
      - Single match: distributor redirects to the part's product page.
      - Multiple matches: filterable result list keyed to the MPN.
      - Zero matches: distributor's "no results" page.

    Replaces the old `mfr_guess` (vendor URL guesses, often wrong) and
    `search_fallback` (google.com — off-platform) rungs. Per user
    feedback (2026-04-24), the resolver must keep every link inside
    the DigiKey/Mouser ecosystem so users don't land on stale
    `analog.com/...` or DuckDuckGo pages — and "DigiKey OR Mouser, not
    only DigiKey", hence the `prefer_mouser` switch.
    """
    return _mouser_search_url(part_number) if prefer_mouser else _digikey_search_url(part_number)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_chain(info: PartInfo) -> list[tuple[str, str]]:
    """Return the candidate `(url, source_label)` chain in priority order.

    Pure function: no probes, no cache reads — handy for tests and for
    reasoning about what the resolver would TRY before any I/O. Always
    ends with a distributor-search URL so downstream callers know they
    can return chain[-1] on universal probe failure.

    The fallback distributor (DigiKey vs Mouser) is chosen from
    `info.source`: a Mouser-sourced part falls back to Mouser's catalog;
    everything else (digikey / seed / chromadb / unknown) falls back
    to DigiKey, which has the broader catalog.
    """
    chain: list[tuple[str, str]] = []
    if info.datasheet_url:
        chain.append((info.datasheet_url, "distributor_pdf"))
    if info.product_url and info.product_url != info.datasheet_url:
        chain.append((info.product_url, "product_url"))
    prefer_mouser = (info.source or "").strip().lower() == "mouser"
    chain.append((
        _distributor_search_url(info.part_number, prefer_mouser=prefer_mouser),
        "distributor_search",
    ))
    return chain


def resolve_datasheet(info: PartInfo, *, timeout: float = 3.0) -> ResolvedDatasheet:
    """Walk the fallback chain and return the first URL whose probe passes.

    Probes are cache-aside via `services.component_cache`, so a warm
    cache turns this into a single SQLite read per URL. The
    `distributor_search` rung (chain[-1]) is never probed — it's
    accepted as a working "find the datasheet on DigiKey/Mouser" link
    by definition (both endpoints never 404).
    """
    chain = build_chain(info)
    for pos, (url, source) in enumerate(chain, start=1):
        if source == "distributor_search":
            # Last rung — never probe; always return.
            return ResolvedDatasheet(
                url=url, is_valid=True, source=source, chain_position=pos,
            )
        if _probe(url, timeout=timeout):
            return ResolvedDatasheet(
                url=url, is_valid=True, source=source, chain_position=pos,
            )
    # Defensive: chain always ends with distributor_search so we never
    # reach here, but keep the path safe.
    prefer_mouser = (info.source or "").strip().lower() == "mouser"
    return ResolvedDatasheet(
        url=_distributor_search_url(info.part_number, prefer_mouser=prefer_mouser),
        is_valid=True, source="distributor_search", chain_position=len(chain),
    )


def resolve_url(info: PartInfo, *, timeout: float = 3.0) -> str:
    """Convenience: just the URL string (for the existing `_verify_datasheet`
    drop-in replacement). Never returns empty."""
    return resolve_datasheet(info, timeout=timeout).url


__all__ = [
    "ResolvedDatasheet",
    "build_chain",
    "resolve_datasheet",
    "resolve_url",
]
