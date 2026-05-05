"""
DigiKey ProductSearch API client — closes the component-hallucination gap.

The requirements_agent can invent a part number ("HMC8999LP4E") and ship
it with a fabricated datasheet URL. Before this module landed, nothing
in the pipeline actually asked DigiKey whether that MPN exists. Now
`lookup(part_number)` hits DigiKey's ProductSearch v3 API and returns
a `PartInfo` on success or `None` on miss — which `services/rf_audit`
translates into a `hallucinated_part` AuditIssue so the user sees the
invention instead of trusting it.

Auth: OAuth2 client-credentials — needs DIGIKEY_CLIENT_ID +
DIGIKEY_CLIENT_SECRET in .env. When the keys are missing, every lookup
returns None and logs once at INFO level (NOT air-gap fail: the rest of
the pipeline keeps running; the part is then validated against the
local seed + Mouser fallback).

Tokens are cached in-process until expiry + 60 s jitter so we don't
re-authenticate every call.

No external deps — stdlib urllib only.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 429 circuit-breaker
#
# DigiKey's rate-limit Retry-After is often several hours (e.g. 24 000 s).
# If we naïvely sleep-and-retry, EACH component stage burns 2 × 30 s = 60 s
# before giving up, and with 12-15 stages the pipeline stalls for 12-15 min.
#
# Solution: the first 429 opens the circuit for min(Retry-After, 300 s).
# Every subsequent call in the same process checks the circuit and returns
# immediately (no HTTP round-trip) until the window expires.  On a typical
# run this collapses the post-LLM component-lookup tail from ~4 min down
# to ~5 s total (first stage 5 s backoff + circuit open, then all remaining
# stages instant).
# ---------------------------------------------------------------------------
_rl_lock: threading.Lock = threading.Lock()
_rate_limited_until: float = 0.0   # monotonic timestamp; 0 means "not limited"
_CIRCUIT_MAX_S: float = 300.0      # never lock out longer than 5 min


def _mark_rate_limited(wait_s: float) -> None:
    """Open (or extend) the 429 circuit-breaker."""
    duration = min(wait_s, _CIRCUIT_MAX_S)
    with _rl_lock:
        global _rate_limited_until
        _rate_limited_until = time.monotonic() + duration
    log.info(
        "digikey.circuit_open — skipping requests for %.0fs "
        "(Retry-After=%.0fs, capped at %.0fs)",
        duration, wait_s, _CIRCUIT_MAX_S,
    )


def _is_rate_limited() -> bool:
    """Return True if the circuit-breaker is currently open."""
    with _rl_lock:
        return time.monotonic() < _rate_limited_until


def reset_rate_limit() -> None:
    """Reset the circuit-breaker.  Exposed for tests and manual recovery."""
    with _rl_lock:
        global _rate_limited_until
        _rate_limited_until = 0.0


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PartInfo:
    """Normalised response from a distributor lookup. Matches the shape
    `rf_audit` expects so DigiKey / Mouser / seed can all be fused.

    Pricing fields:
      - `unit_price_usd` is populated ONLY when the distributor reported
        the first-break price in USD.  Regional API keys (e.g. mouser.in
        → INR) return localised prices; those land in `unit_price` +
        `unit_price_currency` so callers can still show the number
        without silently mislabeling it as dollars.
    """
    part_number: str
    manufacturer: str
    description: str
    datasheet_url: Optional[str]
    product_url: Optional[str]
    lifecycle_status: str        # "active" | "nrnd" | "obsolete" | "unknown"
    unit_price_usd: Optional[float]
    stock_quantity: Optional[int]
    source: str                  # "digikey" | "mouser" | "seed" | "chromadb"
    unit_price: Optional[float] = None            # first-break price in native currency
    unit_price_currency: Optional[str] = None     # ISO-4217 code, e.g. "USD", "INR"
    # Region the stock figure applies to. "US" / "IN" / "DE" etc. when the
    # distributor returns it; "" when the API doesn't differentiate (DigiKey
    # is always US-keyed for our account). Consumers showing stock numbers
    # MUST surface this so a buyer in Bangalore doesn't see "180 in stock"
    # and assume same-day ship when that inventory is Texas-only.
    region: str = ""

    def to_dict(self) -> dict:
        return {
            "part_number": self.part_number,
            "manufacturer": self.manufacturer,
            "description": self.description,
            "datasheet_url": self.datasheet_url,
            "product_url": self.product_url,
            "lifecycle_status": self.lifecycle_status,
            "unit_price_usd": self.unit_price_usd,
            "unit_price": self.unit_price,
            "unit_price_currency": self.unit_price_currency,
            "stock_quantity": self.stock_quantity,
            "region": self.region,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Token cache (process-local)
# ---------------------------------------------------------------------------

_token_lock = threading.Lock()
_cached_token: dict = {"access_token": None, "expires_at": 0.0}


def _client_config() -> tuple[str, str, str]:
    """Return (client_id, client_secret, api_host). Empty strings when
    the env vars are missing — callers must handle that case.

    api_host is normalised to the scheme+host (e.g. `https://api.digikey.com`).
    The OAuth and product-details paths are versioned independently at
    DigiKey, so we never rely on a trailing version segment here —
    callers compose the correct path themselves.
    """
    client_id = os.getenv("DIGIKEY_CLIENT_ID", "").strip()
    client_secret = os.getenv("DIGIKEY_CLIENT_SECRET", "").strip()
    raw = os.getenv("DIGIKEY_API_URL", "https://api.digikey.com").strip().rstrip("/")
    # Strip any version suffix (e.g. /v3, /v1, /products/v4) — we need the host.
    parsed = urllib.parse.urlparse(raw)
    api_host = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else raw
    return client_id, client_secret, api_host


def is_configured() -> bool:
    cid, cs, _ = _client_config()
    return bool(cid and cs)


def reset_cache() -> None:
    """Clear the in-process token cache — test-helper."""
    with _token_lock:
        _cached_token["access_token"] = None
        _cached_token["expires_at"] = 0.0


# ---------------------------------------------------------------------------
# OAuth2 client-credentials flow
# ---------------------------------------------------------------------------

def _fetch_token(*, timeout_s: float = 8.0) -> Optional[str]:
    """Request a fresh access token. Returns None on any failure so the
    caller can fall through to Mouser / seed lookups."""
    client_id, client_secret, api_host = _client_config()
    if not (client_id and client_secret):
        return None
    # DigiKey's OAuth2 endpoint is anchored at /v1 regardless of which
    # Product Information API version the caller is using.
    token_url = f"{api_host}/v1/oauth2/token"
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode("ascii")
    req = urllib.request.Request(
        token_url, data=body, method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "HardwarePipeline/2.0 (+digikey_api.py)",
        },
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, ssl.SSLError, OSError, json.JSONDecodeError) as exc:
        log.info("digikey.token_fetch_failed: %s", exc)
        return None

    access = payload.get("access_token")
    expires_in = int(payload.get("expires_in") or 0)
    if not access:
        log.warning("digikey.token_missing_access_token")
        return None
    with _token_lock:
        _cached_token["access_token"] = access
        # Expire 60 s early so we never use a just-about-to-expire token.
        _cached_token["expires_at"] = time.time() + max(60, expires_in - 60)
    log.info("digikey.token_refreshed expires_in=%d", expires_in)
    return access


def _get_token() -> Optional[str]:
    """Return a cached token when valid, else request a new one."""
    with _token_lock:
        if (
            _cached_token["access_token"]
            and _cached_token["expires_at"] > time.time()
        ):
            return _cached_token["access_token"]
    return _fetch_token()


# ---------------------------------------------------------------------------
# Part lookup
# ---------------------------------------------------------------------------

def lookup(part_number: str, *, timeout_s: float = 12.0) -> Optional[PartInfo]:
    """Search DigiKey for a manufacturer part number.

    Returns:
      PartInfo when DigiKey recognises the MPN.
      None when: API not configured, auth failed, HTTP error, MPN unknown.

    This is the pipeline's "is this MPN real?" oracle. Do NOT raise —
    callers rely on a sentinel None to trigger the next-tier fallback.
    """
    if not part_number:
        return None
    if not is_configured():
        return None
    if _is_rate_limited():
        log.debug("digikey.circuit_open_skip lookup pn=%r", part_number)
        return None

    token = _get_token()
    if not token:
        return None

    client_id, _, api_host = _client_config()
    # Current DigiKey Product Information API is v4; path is stable even
    # though OAuth lives under /v1. Encode the MPN for safety.
    url = f"{api_host}/products/v4/search/{urllib.parse.quote(part_number)}/productdetails"
    req = urllib.request.Request(
        url, method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "X-DIGIKEY-Client-Id": client_id,
            "X-DIGIKEY-Locale-Site":      "US",
            "X-DIGIKEY-Locale-Language":  "en",
            "X-DIGIKEY-Locale-Currency":  "USD",
            "Accept": "application/json",
            "User-Agent": "HardwarePipeline/2.0 (+digikey_api.py)",
        },
    )
    payload = _open_with_retry(
        req, timeout_s=timeout_s, what=f"digikey.lookup pn={part_number}",
    )
    if payload is None:
        return None
    return _parse_product_details(payload, part_number)


# ---------------------------------------------------------------------------
# Transport with 429 / Retry-After backoff
# ---------------------------------------------------------------------------

def _open_with_retry(
    req: urllib.request.Request,
    *,
    timeout_s: float,
    what: str,
    max_retries: int = 2,
) -> Optional[dict]:
    """Open `req` as a JSON endpoint with bounded 429 retry support.

    Returns the decoded JSON payload on success, None on any failure
    (404, 401, network error, too-many-retries). 401 resets the token
    cache. 429 reads Retry-After (seconds) and sleeps before retrying,
    capped at 30 s per attempt and `max_retries` attempts total.

    All callers in this module should go through this function instead
    of urlopen directly so the retry policy stays in one place.
    """
    ctx = ssl.create_default_context()
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            code = getattr(exc, "code", None)
            if code == 404:
                log.debug("digikey.not_found %s", what)
                return None
            if code == 401:
                reset_cache()
                log.info("digikey.401_stale_token %s", what)
                return None
            if code == 429:
                wait_s = _parse_retry_after(exc.headers if hasattr(exc, "headers") else None)
                # Open the circuit so all subsequent calls in this
                # process skip DigiKey instantly — stops the 60-s-per-
                # stage cascade that causes 12-min P1 runs.
                _mark_rate_limited(wait_s)
                if attempt < max_retries:
                    attempt += 1
                    log.info(
                        "digikey.429_backoff attempt=%d wait=%.1fs %s",
                        attempt, wait_s, what,
                    )
                    # 5 s cap (down from 30 s).  One quick retry in case
                    # the rate-limit window resets; if it 429s again we
                    # give up immediately rather than burning another 30 s.
                    time.sleep(min(wait_s, 5.0))
                    continue
            log.info("digikey.http_error %s code=%s: %s", what, code, exc)
            return None
        except (urllib.error.URLError, TimeoutError, ssl.SSLError,
                OSError, json.JSONDecodeError) as exc:
            log.info("digikey.transport_error %s: %s", what, exc)
            return None


def _parse_retry_after(headers) -> float:
    """Interpret a Retry-After header: either delta-seconds or HTTP-date.
    Fall back to 2 s if parsing fails."""
    if headers is None:
        return 2.0
    raw = headers.get("Retry-After", "") if hasattr(headers, "get") else ""
    if not raw:
        return 2.0
    try:
        return max(0.1, float(raw))
    except ValueError:
        pass
    # HTTP-date form — best-effort parse
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        return max(0.1, (dt.timestamp() - time.time()))
    except Exception:
        return 2.0


# ---------------------------------------------------------------------------
# Keyword search — parametric candidate retrieval
# ---------------------------------------------------------------------------

def keyword_search(
    keywords: str,
    *,
    limit: int = 10,
    timeout_s: float = 10.0,
) -> list[PartInfo]:
    """Query DigiKey's v4 keyword endpoint and return a list of PartInfo.

    Unlike `lookup`, this is a *retrieval* call: the caller supplies a
    natural-language query (e.g. `"LNA 2-18 GHz"`) and DigiKey returns
    the best-matching MPNs. Used by `tools.parametric_search` to build
    a real-part shortlist for the LLM to pick from.

    Returns an empty list on any failure — callers fall through to
    Mouser / seed as with `lookup`.
    """
    if not keywords:
        return []
    if not is_configured():
        return []
    if _is_rate_limited():
        log.debug("digikey.circuit_open_skip keyword=%r", keywords)
        return []
    token = _get_token()
    if not token:
        return []

    client_id, _, api_host = _client_config()
    url = f"{api_host}/products/v4/search/keyword"
    body = json.dumps({
        "Keywords": keywords,
        "Limit": max(1, min(limit, 50)),
        "Offset": 0,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "X-DIGIKEY-Client-Id": client_id,
            "X-DIGIKEY-Locale-Site": "US",
            "X-DIGIKEY-Locale-Language": "en",
            "X-DIGIKEY-Locale-Currency": "USD",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "HardwarePipeline/2.0 (+digikey_api.py)",
        },
    )
    payload = _open_with_retry(
        req, timeout_s=timeout_s, what=f"digikey.keyword q={keywords!r}",
    )
    if payload is None:
        return []

    products = (payload or {}).get("Products") or []
    out: list[PartInfo] = []
    for prod in products:
        # Each product has the same shape as the `Product` inside a
        # productdetails response — reuse the same parser.
        info = _parse_product_details({"Product": prod}, "")
        if info is not None:
            out.append(info)
    return out


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

# DigiKey ProductStatus codes we consider "ship-safe".
_ACTIVE_PRODUCT_STATUSES = {"Active", "Active / Preferred"}
_NRND_PRODUCT_STATUSES = {"Last Time Buy", "Obsolete Available", "Not Recommended for New Designs"}


def _parse_product_details(payload: dict, requested_pn: str) -> Optional[PartInfo]:
    """Translate DigiKey's JSON shape into a PartInfo.

    DigiKey returns a wrapper {"ProductDetails": {...}} — be defensive
    because the API has mutated over v2→v3→v4 without breaking the outer
    shape. If the shape is unexpected, log and fall through to None.
    """
    if not isinstance(payload, dict):
        return None
    details = (
        payload.get("ProductDetails")
        or payload.get("Product")
        or payload
    )
    if not isinstance(details, dict):
        return None

    mfr_info = details.get("Manufacturer") or {}
    manufacturer = (
        mfr_info.get("Value")
        or mfr_info.get("Name")
        or details.get("ManufacturerName")
        or ""
    )
    mfr_pn = (
        details.get("ManufacturerPartNumber")
        or details.get("ManufacturerProductNumber")
        or requested_pn
    )
    # v3 exposed ProductDescription as a flat string; v4 nests it under
    # Description = {"ProductDescription": str, "DetailedDescription": str}.
    desc_obj = details.get("Description")
    if isinstance(desc_obj, dict):
        description = (
            desc_obj.get("ProductDescription")
            or desc_obj.get("DetailedDescription")
            or ""
        )
    else:
        description = (
            details.get("ProductDescription")
            or details.get("DetailedDescription")
            or (desc_obj if isinstance(desc_obj, str) else "")
            or ""
        )
    datasheet_url = details.get("PrimaryDatasheet") or details.get("DatasheetUrl")
    # DigiKey v4 sometimes returns protocol-relative URLs (`//mm.digikey.com/...`).
    # Normalise to https so downstream consumers (docs, audit, UI) can link safely.
    if isinstance(datasheet_url, str) and datasheet_url.startswith("//"):
        datasheet_url = "https:" + datasheet_url
    product_url = details.get("ProductUrl") or details.get("ProductPath")

    # Lifecycle status
    status_raw = (details.get("ProductStatus") or {})
    if isinstance(status_raw, dict):
        status_text = status_raw.get("Status") or status_raw.get("Value") or ""
    else:
        status_text = str(status_raw)
    if status_text in _ACTIVE_PRODUCT_STATUSES:
        lifecycle = "active"
    elif status_text in _NRND_PRODUCT_STATUSES:
        lifecycle = "nrnd"
    elif status_text:
        lifecycle = "obsolete"
    else:
        lifecycle = "unknown"

    price = None
    try:
        up = details.get("UnitPrice")
        if up is not None:
            price = float(up)
    except (TypeError, ValueError):
        price = None

    stock = None
    try:
        q = details.get("QuantityAvailable") or details.get("Quantity")
        if q is not None:
            stock = int(q)
    except (TypeError, ValueError):
        stock = None

    return PartInfo(
        part_number=str(mfr_pn).strip(),
        manufacturer=str(manufacturer).strip(),
        description=str(description).strip(),
        datasheet_url=str(datasheet_url).strip() if datasheet_url else None,
        product_url=str(product_url).strip() if product_url else None,
        lifecycle_status=lifecycle,
        unit_price_usd=price,
        unit_price=price,
        unit_price_currency="USD" if price is not None else None,
        stock_quantity=stock,
        # DigiKey API v3/v4 returns US inventory for our account. Regional
        # DigiKey sites are separately provisioned; if we ever add EU or
        # APAC keys, parse the locale-site from _client_config() headers.
        region="US",
        source="digikey",
    )
