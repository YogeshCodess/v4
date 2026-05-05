"""
Mouser Search API client — second-tier distributor lookup.

DigiKey is the primary, Mouser is the fallback when DigiKey doesn't
know the MPN (their catalogues overlap but differ at the edges). Same
`PartInfo` contract so callers don't care which distributor answered.

Auth: single API key (MOUSER_API_KEY) passed as `?apikey=`. No OAuth.
Docs: https://www.mouser.com/api-search/
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from tools.digikey_api import PartInfo

log = logging.getLogger(__name__)


def _config() -> tuple[str, str]:
    api_key = os.getenv("MOUSER_API_KEY", "").strip()
    api_base = os.getenv("MOUSER_API_URL", "https://api.mouser.com/api/v2").rstrip("/")
    return api_key, api_base


def is_configured() -> bool:
    return bool(_config()[0])


# ---------------------------------------------------------------------------

def lookup(part_number: str, *, timeout_s: float = 12.0) -> Optional[PartInfo]:
    """Search Mouser for a manufacturer part number.

    Returns a PartInfo on match, None otherwise (API not configured /
    HTTP error / not-found / unexpected shape).
    """
    if not part_number:
        return None
    api_key, api_base = _config()
    if not api_key:
        return None

    url = f"{api_base}/search/partnumber?apiKey={urllib.parse.quote(api_key)}"
    body = json.dumps({
        "SearchByPartRequest": {
            "mouserPartNumber": part_number,
            "partSearchOptions": "string",
        }
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "HardwarePipeline/2.0 (+mouser_api.py)",
        },
    )
    payload = _open_with_retry(
        req, timeout_s=timeout_s, what=f"mouser.lookup pn={part_number}",
    )
    if payload is None:
        return None
    return _parse_search_response(payload, part_number)


# ---------------------------------------------------------------------------
# Transport with 429 / Retry-After backoff
# ---------------------------------------------------------------------------
import time as _time


def _open_with_retry(
    req: urllib.request.Request,
    *,
    timeout_s: float,
    what: str,
    max_retries: int = 2,
) -> Optional[dict]:
    """Open `req` as a JSON endpoint with bounded 429 retry support.

    Returns the decoded JSON payload on success, None on any failure.
    Mouser's rate-limit errors come back as 429; Retry-After is usually
    set. Cap the backoff at 30 s per attempt and `max_retries` total."""
    ctx = ssl.create_default_context()
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            code = getattr(exc, "code", None)
            if code == 404:
                return None
            if code == 429 and attempt < max_retries:
                wait_s = _parse_retry_after(
                    exc.headers if hasattr(exc, "headers") else None
                )
                attempt += 1
                log.info(
                    "mouser.429_backoff attempt=%d wait=%.1fs %s",
                    attempt, wait_s, what,
                )
                _time.sleep(min(wait_s, 30.0))
                continue
            log.info("mouser.http_error %s code=%s: %s", what, code, exc)
            return None
        except (urllib.error.URLError, TimeoutError, ssl.SSLError,
                OSError, json.JSONDecodeError) as exc:
            log.info("mouser.transport_error %s: %s", what, exc)
            return None


def _parse_retry_after(headers) -> float:
    if headers is None:
        return 2.0
    raw = headers.get("Retry-After", "") if hasattr(headers, "get") else ""
    if not raw:
        return 2.0
    try:
        return max(0.1, float(raw))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        return max(0.1, (dt.timestamp() - _time.time()))
    except Exception:
        return 2.0


# ---------------------------------------------------------------------------
# Keyword search — parametric candidate retrieval
# ---------------------------------------------------------------------------

def keyword_search(
    keywords: str,
    *,
    records: int = 10,
    in_stock_only: bool = True,
    timeout_s: float = 10.0,
) -> list[PartInfo]:
    """Query Mouser's keyword endpoint and return a list of PartInfo.

    Powers `tools.parametric_search` — given a free-text query like
    `"LNA 2-18 GHz low noise"`, Mouser returns the best-matching MPNs
    with full metadata (manufacturer, datasheet URL, lifecycle, stock).
    The LLM then picks from this shortlist instead of inventing a part.

    Returns an empty list on any failure.
    """
    if not keywords:
        return []
    api_key, api_base = _config()
    if not api_key:
        return []

    url = f"{api_base}/search/keyword?apiKey={urllib.parse.quote(api_key)}"
    body = json.dumps({
        "SearchByKeywordRequest": {
            "keyword": keywords,
            "records": max(1, min(records, 50)),
            "searchOptions": "InStock" if in_stock_only else "None",
        }
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "HardwarePipeline/2.0 (+mouser_api.py)",
        },
    )
    payload = _open_with_retry(
        req, timeout_s=timeout_s, what=f"mouser.keyword q={keywords!r}",
    )
    if payload is None:
        return []

    parts = ((payload or {}).get("SearchResults") or {}).get("Parts") or []
    out: list[PartInfo] = []
    for part in parts:
        # Reuse the single-part parser by wrapping each hit in the same
        # envelope the partnumber endpoint returns.
        info = _parse_search_response(
            {"SearchResults": {"Parts": [part]}},
            (part.get("ManufacturerPartNumber") or "").strip(),
        )
        if info is not None:
            out.append(info)
    return out


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

# Mouser uses "LifecycleStatus" string directly. Values observed in the
# wild include "New Product" / "Newly Released" for recently-added MPNs
# — both of those are shippable (they behave like Active from the audit's
# point of view).
_ACTIVE_LIFECYCLES = {
    "Active", "In Production", "New Product", "Newly Released", "",
}
_NRND_LIFECYCLES = {"Not Recommended for New Designs", "NRND",
                    "Last Time Buy", "End of Life"}
_OBSOLETE_LIFECYCLES = {"Obsolete", "Discontinued"}


# Common currency symbols → ISO-4217 code. The Mouser API returns the
# price break with a symbol-prefixed string and a separate `Currency`
# field; we trust the `Currency` field first and fall back to the symbol
# only when the field is absent or empty.
_SYMBOL_TO_ISO = {
    "$": "USD", "US$": "USD", "C$": "CAD", "A$": "AUD",
    "€": "EUR", "£": "GBP", "¥": "JPY",
    "\u20B9": "INR",   # ₹
    "\u20AC": "EUR",   # €
    "\u00A3": "GBP",   # £
    "\u00A5": "JPY",   # ¥
}


def _parse_search_response(payload: dict, requested_pn: str) -> Optional[PartInfo]:
    # Shape: {"SearchResults": {"NumberOfResult": N, "Parts": [{...}, ...]}}
    results = (payload or {}).get("SearchResults") or {}
    parts = results.get("Parts") or []
    if not parts:
        return None

    requested_norm = requested_pn.strip().lower()
    best = None
    for p in parts:
        mfr_pn = (p.get("ManufacturerPartNumber") or "").strip()
        if mfr_pn.lower() == requested_norm:
            best = p
            break
    if best is None:
        # Mouser often returns fuzzy matches — only accept them if no
        # strong equality match was found AND only one candidate exists.
        if len(parts) == 1:
            best = parts[0]
        else:
            return None

    status_text = (best.get("LifecycleStatus") or "").strip()
    if status_text in _ACTIVE_LIFECYCLES:
        lifecycle = "active"
    elif status_text in _NRND_LIFECYCLES:
        lifecycle = "nrnd"
    elif status_text in _OBSOLETE_LIFECYCLES:
        lifecycle = "obsolete"
    else:
        lifecycle = "unknown"

    price_value, price_currency = _first_price_break(best.get("PriceBreaks") or [])
    price_usd = price_value if price_currency == "USD" else None

    # Stock count — Mouser has shipped several names for this field over
    # the years; try the common ones in order before giving up.
    stock = None
    for key in ("AvailabilityInStock", "Availability", "QuantityAvailable"):
        raw = best.get(key)
        if raw is None:
            continue
        try:
            stock = int(str(raw).strip() or 0)
            break
        except (TypeError, ValueError):
            continue

    product_url = (best.get("ProductDetailUrl") or "").strip() or None

    # Region of the stock figure / price book. Explicit API fields win,
    # then the returned product URL/currency, then MOUSER_API_URL's host.
    region = _infer_region(
        best,
        results=(payload or {}).get("SearchResults") or {},
        product_url=product_url,
        currency=price_currency,
    )

    # Prefer dedicated fields but tolerate the legacy all-caps variants.
    mfr = (
        best.get("Manufacturer")
        or best.get("ManufacturerName")
        or ""
    )
    desc = (
        best.get("Description")
        or best.get("ProductDescription")
        or ""
    )
    ds_url = (
        best.get("DataSheetUrl")
        or best.get("DatasheetUrl")
        or best.get("Datasheet")
        or ""
    )

    return PartInfo(
        part_number=(best.get("ManufacturerPartNumber") or requested_pn).strip(),
        manufacturer=str(mfr).strip(),
        description=str(desc).strip(),
        datasheet_url=str(ds_url).strip() or None,
        product_url=product_url,
        lifecycle_status=lifecycle,
        unit_price_usd=price_usd,
        stock_quantity=stock,
        region=region,
        source="mouser",
        unit_price=price_value,
        unit_price_currency=price_currency,
    )


def _infer_region(
    part: dict,
    *,
    results: dict,
    product_url: Optional[str] = None,
    currency: Optional[str] = None,
) -> str:
    """Best-effort region derivation for Mouser stock figures.

    Priority:
      1. part["Region"] / part["MouserRegion"] — explicit per-item.
      2. results["MouserRegionCodePrefix"] — response-level (e.g. "IN").
      3. product URL host TLD (mouser.in -> IN).
      4. price currency when it strongly implies a country (INR -> IN).
      5. MOUSER_API_URL host TLD.
      6. Empty string when nothing resolves.
    """
    for key in ("Region", "MouserRegion", "RegionCode"):
        v = (part.get(key) or "").strip().upper()
        if v:
            return v[:3]
    for key in ("MouserRegionCodePrefix", "RegionCode"):
        v = (results.get(key) or "").strip().upper()
        if v:
            return v[:3]
    url_region = _region_from_url(product_url or "")
    if url_region:
        return url_region
    cur = (currency or "").strip().upper()
    if cur == "INR":
        return "IN"
    if cur == "USD":
        return "US"
    if cur == "EUR":
        return "EU"
    api_region = _region_from_url(_config()[1])
    if api_region:
        return api_region
    return ""


def _region_from_url(url: str) -> str:
    try:
        from urllib.parse import urlparse
        host = urlparse(url or "").hostname or ""
        tld = host.rsplit(".", 1)[-1].upper()
        if tld in {"IN", "DE", "FR", "JP", "CN", "BR", "SG", "HK"}:
            return tld
        if tld == "UK":
            return "UK"
        if tld == "COM":
            return "US"
    except Exception:
        pass
    return ""


def _first_price_break(breaks: list) -> tuple[Optional[float], Optional[str]]:
    """Extract the 1-off price + ISO currency from Mouser's PriceBreaks.

    Mouser returns strings like `"$4.58"` or `"₹61.50"` plus a separate
    `Currency` field (`"USD"`, `"INR"`, ...). Prefer the explicit field
    and fall back to prefix-matching the symbol when the field is empty.
    Non-digit / non-separator characters are stripped before parsing so
    the numeric value survives any currency annotation.
    """
    if not breaks:
        return None, None
    first = breaks[0] or {}
    raw = (first.get("Price") or "").strip()
    if not raw:
        return None, None

    # Detect currency
    currency = (first.get("Currency") or "").strip().upper() or None
    if not currency:
        for sym, iso in _SYMBOL_TO_ISO.items():
            if raw.startswith(sym):
                currency = iso
                break

    # Keep digits, dot, minus, comma — strip everything else (symbols, whitespace).
    # Then normalise European decimal comma only when there's no dot.
    import re as _re
    numeric = _re.sub(r"[^0-9.,\-]", "", raw)
    if "," in numeric and "." not in numeric:
        numeric = numeric.replace(",", ".")
    else:
        numeric = numeric.replace(",", "")
    try:
        return float(numeric), currency
    except ValueError:
        return None, currency
