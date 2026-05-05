"""
datasheet_extractor.py - fetch a manufacturer datasheet, extract spec.

Four-layer extraction stack (each layer enriches the next):

  Layer 1: pypdf text extraction       - fast, works on text-based PDFs
  Layer 2: pdfplumber TABLE extraction - critical for register/opcode
                                         tables that pypdf flattens
  Layer 3: pytesseract OCR             - last resort for scanned PDFs
  Layer 4: distributor API enrichment  - DigiKey/Mouser parametric data
                                         fills package/voltage/lifecycle

The combined text (prose + tables + OCR) is sent to the LLM with a
strict ComponentSpec JSON schema. Distributor enrichment runs AFTER the
LLM, merging structured parametric fields the LLM didn't surface.

Specs with confidence < 0.85 land in
data/component_specs/_review_queue.jsonl so a human can verify them
against the source PDF before deployment.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from schemas.component_spec import ComponentSpec, FpgaSidePorts

log = logging.getLogger(__name__)

_EXTRACT_DIR     = Path(__file__).resolve().parent.parent / "data" / "component_specs" / "_extracted"
_REVIEW_QUEUE    = Path(__file__).resolve().parent.parent / "data" / "component_specs" / "_review_queue.jsonl"
_HASH_INDEX      = Path(__file__).resolve().parent.parent / "data" / "component_specs" / "_pdf_hashes.json"
_DIFF_REVIEW_QUEUE = Path(__file__).resolve().parent.parent / "data" / "component_specs" / "_diff_review_queue.jsonl"
_REVIEW_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# Datasheet PDF diff detection
# ---------------------------------------------------------------------------
#
# When we fetch a datasheet PDF we SHA-256 the bytes and remember the hash.
# On the next fetch, if the hash differs from what we stored we flag the
# MPN for re-review - the manufacturer has revised the datasheet and any
# extracted spec we cached earlier may be stale.
#
# The hash index lives at data/component_specs/_pdf_hashes.json and is a
# small dict {mpn: {sha256, url, first_seen, last_seen, fetch_count}}.
# Diff events are appended to data/component_specs/_diff_review_queue.jsonl
# for the operator (or `make review-specs`) to act on.


def _load_hash_index() -> dict[str, dict]:
    """Load the persistent {mpn -> {sha256, url, first_seen, ...}} map."""
    if not _HASH_INDEX.exists():
        return {}
    try:
        return json.loads(_HASH_INDEX.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("datasheet.hash_index_corrupt - resetting (%s)", e)
        return {}


def _save_hash_index(idx: dict[str, dict]) -> None:
    try:
        _HASH_INDEX.parent.mkdir(parents=True, exist_ok=True)
        _HASH_INDEX.write_text(json.dumps(idx, indent=2, sort_keys=True),
                               encoding="utf-8")
    except OSError as e:
        log.warning("datasheet.hash_index_write_failed: %s", e)


def _enqueue_diff_event(mpn: str, url: str, old_sha: str, new_sha: str) -> None:
    """Append a single line to _diff_review_queue.jsonl when a PDF changes."""
    rec = {
        "mpn": mpn,
        "datasheet_url": url,
        "old_sha256": old_sha,
        "new_sha256": new_sha,
        "detected_at": datetime.utcnow().isoformat(),
        "reason": "datasheet_pdf_changed_since_last_fetch",
    }
    try:
        _DIFF_REVIEW_QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with _DIFF_REVIEW_QUEUE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        log.warning("datasheet.diff_detected mpn=%s old=%s new=%s",
                    mpn, old_sha[:12], new_sha[:12])
    except OSError as e:
        log.warning("datasheet.diff_queue_write_failed: %s", e)


def _record_pdf_hash(mpn: str, url: str, raw: bytes) -> tuple[str, bool]:
    """Hash the PDF, persist the hash, and report whether it changed.

    Returns (sha256_hex, changed_since_last_fetch).
    For first-time fetches `changed=False` (nothing to compare against).
    """
    sha = hashlib.sha256(raw).hexdigest()
    idx = _load_hash_index()
    prev = idx.get(mpn)
    now = datetime.utcnow().isoformat()
    changed = False
    if prev and prev.get("sha256") and prev["sha256"] != sha:
        changed = True
        _enqueue_diff_event(mpn, url, prev["sha256"], sha)
        # Bump cache entry: invalidate by removing the cached extracted JSON
        # so the next extract_from_url call re-runs the LLM extractor.
        try:
            cp = _cache_path(mpn)
            if cp.exists():
                cp.unlink()
                log.info("datasheet.cache_invalidated_after_diff mpn=%s", mpn)
        except OSError:
            pass
    idx[mpn] = {
        "sha256": sha,
        "url": url,
        "first_seen": (prev or {}).get("first_seen", now),
        "last_seen": now,
        "fetch_count": (prev or {}).get("fetch_count", 0) + 1,
        "size_bytes": len(raw),
    }
    _save_hash_index(idx)
    return sha, changed


def list_diff_review_queue() -> list[dict]:
    """Return all PDF-diff events the operator hasn't acted on yet."""
    if not _DIFF_REVIEW_QUEUE.exists():
        return []
    out: list[dict] = []
    for line in _DIFF_REVIEW_QUEUE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def get_pdf_hash(mpn: str) -> Optional[str]:
    """Look up the cached SHA-256 for an MPN, or None if never fetched."""
    idx = _load_hash_index()
    rec = idx.get(mpn)
    return rec.get("sha256") if rec else None


# ---------------------------------------------------------------------------
# Layer 0: HTTP fetch
# ---------------------------------------------------------------------------


_USER_AGENT = ("Mozilla/5.0 (compatible; SiliconToSoftware-Datasheet-Extractor/2.1; "
               "+https://example.invalid/s2s)")


def _http_get(url: str, timeout: float = 15.0) -> Optional[bytes]:
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            ct = (resp.headers.get("Content-Type") or "").lower()
            data = resp.read(8 * 1024 * 1024)   # cap at 8 MB (PDF datasheets are large)
            log.info("datasheet.fetched url=%s ct=%s bytes=%d",
                     url[:80], ct, len(data))
            return data
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            ssl.SSLError, OSError) as e:
        log.warning("datasheet.fetch_failed url=%s: %s", url[:80], e)
        return None


# ---------------------------------------------------------------------------
# Layer 1: pypdf text extraction
# ---------------------------------------------------------------------------


def _pypdf_text(raw: bytes, max_chars: int = 30000) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(io.BytesIO(raw))
        chunks: list[str] = []
        total = 0
        for page in reader.pages[:40]:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            chunks.append(t)
            total += len(t)
            if total >= max_chars:
                break
        return "\n".join(chunks)[:max_chars]
    except Exception as e:
        log.warning("datasheet.pypdf_failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Layer 2: pdfplumber table extraction
# ---------------------------------------------------------------------------


def _pdfplumber_tables(raw: bytes, max_tables: int = 20) -> str:
    """Return the first N tables from the PDF as pipe-separated markdown.

    Critical for SPI flash opcode tables and I2C register maps - those
    are tabular data that pypdf flattens into one ambiguous blob. By
    feeding the LLM a structured table representation we eliminate
    most opcode/address-width hallucinations."""
    try:
        import pdfplumber
    except ImportError:
        return ""
    try:
        out: list[str] = []
        n_tables = 0
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page_idx, page in enumerate(pdf.pages[:30]):
                try:
                    tables = page.extract_tables()
                except Exception:
                    continue
                for tbl in tables or []:
                    if not tbl or len(tbl) < 2:
                        continue
                    out.append(f"\n[Table from page {page_idx + 1}]")
                    for row in tbl[:25]:
                        cells = [str(c).strip() if c is not None else "" for c in row]
                        out.append("| " + " | ".join(cells) + " |")
                    n_tables += 1
                    if n_tables >= max_tables:
                        break
                if n_tables >= max_tables:
                    break
        if out:
            log.info("datasheet.tables_extracted count=%d", n_tables)
        return "\n".join(out)
    except Exception as e:
        log.warning("datasheet.pdfplumber_failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Layer 3: OCR fallback
# ---------------------------------------------------------------------------


def _ocr_text(raw: bytes, max_chars: int = 20000, max_pages: int = 8) -> str:
    """Last-resort OCR for image-only scanned PDFs."""
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
    except ImportError:
        log.info("datasheet.ocr_libs_missing - skipping OCR fallback")
        return ""
    try:
        images = convert_from_bytes(raw, dpi=200, first_page=1, last_page=max_pages)
        chunks: list[str] = []
        total = 0
        for img in images:
            try:
                t = pytesseract.image_to_string(img) or ""
            except Exception:
                t = ""
            chunks.append(t)
            total += len(t)
            if total >= max_chars:
                break
        ocr = "\n".join(chunks)[:max_chars]
        if ocr.strip():
            log.info("datasheet.ocr_extracted chars=%d", len(ocr))
        return ocr
    except Exception as e:
        log.warning("datasheet.ocr_failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Combined fetch
# ---------------------------------------------------------------------------


def fetch_datasheet_text(url: str, max_chars: int = 30000,
                         mpn: Optional[str] = None) -> Optional[str]:
    """Return the combined text (prose + tables + OCR) of a datasheet.

    When `mpn` is provided we also SHA-256 the raw PDF bytes and record
    the hash in `_pdf_hashes.json`. If the hash differs from the stored
    one, the cached extracted spec is invalidated and a diff event is
    appended to `_diff_review_queue.jsonl` so the operator can re-review.
    """
    raw = _http_get(url)
    if raw is None:
        return None

    # Diff detection - hash the raw bytes BEFORE parsing so we catch any
    # change (PDF revision, format change, redirect to a different file).
    if mpn:
        try:
            _record_pdf_hash(mpn, url, raw)
        except Exception as e:
            log.warning("datasheet.hash_record_failed mpn=%s: %s", mpn, e)

    if raw[:4] == b"%PDF":
        prose = _pypdf_text(raw, max_chars=max_chars)
        tables = _pdfplumber_tables(raw)
        # If neither pypdf nor pdfplumber got anything useful, try OCR.
        if not prose.strip() and not tables.strip():
            log.warning("datasheet.text_empty url=%s - trying OCR fallback", url[:80])
            ocr = _ocr_text(raw)
            return ocr if ocr.strip() else None
        # Prose first, then tables for the LLM to cross-reference.
        return (prose + "\n\n=== TABLES (pdfplumber) ===\n" + tables)[:max_chars + 8000]

    # HTML or text content
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return None
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>",   " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars] if text else None


# ---------------------------------------------------------------------------
# LLM extraction (same schema as before)
# ---------------------------------------------------------------------------


_EXTRACTOR_SYSTEM_PROMPT = """You are an electronics-parts datasheet
extractor. Read the datasheet text + tables and produce a JSON object
that matches the ComponentSpec schema. Use ONLY values stated in the
datasheet - NEVER guess. If a field is not stated, omit it.

The text contains extracted prose followed by '=== TABLES (pdfplumber) ==='
and structured tables. Cross-reference both. When a value appears in a
table that's the most reliable source - prefer table values for
opcodes, register addresses, page sizes, and bit-field layouts.

Return raw JSON only, no markdown fences.
"""


def _build_user_message(text: str, mpn: str, hint_bus: str) -> str:
    return (
        f"Extract a ComponentSpec for MPN: {mpn}\n"
        f"Hint bus (from BOM context): {hint_bus or '(unknown)'}\n\n"
        f"Datasheet content (text + extracted tables):\n\n"
        f"-----\n{text[:35000]}\n-----\n\n"
        "Return the JSON object now."
    )


async def _extract_via_llm_async(text: str, mpn: str, hint_bus: str) -> Optional[dict]:
    try:
        from agents.base_agent import BaseAgent
    except Exception as e:
        log.warning("datasheet.base_agent_import_failed: %s", e)
        return None

    class _ExtractAgent(BaseAgent):
        def get_system_prompt(self, ctx: dict) -> str:
            return _EXTRACTOR_SYSTEM_PROMPT
        async def execute(self, *args, **kwargs):
            raise NotImplementedError

    agent = _ExtractAgent(phase_number="datasheet", phase_name="DS Extract")
    try:
        resp = await agent.call_llm(
            messages=[{"role": "user", "content": _build_user_message(text, mpn, hint_bus)}],
            system=_EXTRACTOR_SYSTEM_PROMPT,
        )
    except Exception as e:
        log.warning("datasheet.llm_call_failed mpn=%s: %s", mpn, e)
        return None
    body = (resp.get("content") or "").strip()
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", body, flags=re.I | re.M).strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        log.warning("datasheet.json_parse_failed mpn=%s: %s body=%s...", mpn, e, body[:200])
        return None


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import threading
        out: list = [None]
        def _runner():
            out[0] = asyncio.run(coro)
        t = threading.Thread(target=_runner)
        t.start(); t.join(timeout=120)
        return out[0]
    return asyncio.run(coro)


def extract_spec_via_llm(text: str, mpn: str, hint_bus: str = "") -> Optional[ComponentSpec]:
    if not text:
        return None
    data = _run_async(_extract_via_llm_async(text, mpn, hint_bus))
    if not isinstance(data, dict):
        return None
    data.setdefault("mpn", mpn)
    data.setdefault("source", "llm_extracted")
    data.setdefault("confidence", 0.5)
    data.setdefault("bus", hint_bus or data.get("bus") or "spi")
    try:
        return ComponentSpec(**data)
    except Exception as e:
        log.warning("datasheet.spec_validate_failed mpn=%s: %s", mpn, e)
        return None


# ---------------------------------------------------------------------------
# Second-pass validation: re-check extracted JSON against the source PDF
# ---------------------------------------------------------------------------
#
# The first extraction pass can hallucinate when a value spans multiple
# lines or sits inside a poorly-OCR'd table. The second pass sends the
# JSON spec + the original PDF text BACK to the LLM and asks "what
# claims contradict the source?". Each contradiction lowers `confidence`
# by 0.2; if confidence falls below the review threshold the spec is
# requeued for human review.
#
# This is the cheapest hallucination guard for parts that don't hit the
# curated library. Cost: one extra LLM call per uncached LLM extraction
# (does NOT run for curated-spec hits, family-inferred hits, or
# generic-fallback hits).


_VALIDATOR_SYSTEM_PROMPT = """You are a careful datasheet fact-checker.
You will be given (1) a JSON ComponentSpec that was extracted from a
datasheet by another model, and (2) the source datasheet text. Identify
fields in the JSON whose values are NOT supported by - or directly
contradict - the datasheet text.

Rules:
- Only flag fields you can verify against the supplied text. If you
  cannot find a field discussed in the text at all, do NOT flag it -
  absence is not a contradiction.
- A field is `supported` if the datasheet states the same value
  (allow tolerance: 5% for analog values, exact for opcodes/addresses).
- A field is `contradicted` if the datasheet states a different value.
- Return raw JSON, no markdown fences. Schema:
    {"contradictions": [
        {"field": "<field_name>", "claimed": <value>,
         "datasheet_says": <value>, "evidence": "<short quote>"}
     ]}
- Empty list means everything checks out.
"""


def _build_validator_message(spec_json: str, text: str, mpn: str) -> str:
    return (
        f"MPN: {mpn}\n\n"
        f"Extracted ComponentSpec JSON:\n-----\n{spec_json}\n-----\n\n"
        f"Source datasheet text (first 30 KB):\n-----\n{text[:30000]}\n-----\n\n"
        "List contradictions as JSON now."
    )


async def _validate_via_llm_async(spec: ComponentSpec, text: str) -> Optional[list[dict]]:
    try:
        from agents.base_agent import BaseAgent
    except Exception as e:
        log.debug("datasheet.validator.base_agent_import_failed: %s", e)
        return None

    class _ValidatorAgent(BaseAgent):
        def get_system_prompt(self, ctx: dict) -> str:
            return _VALIDATOR_SYSTEM_PROMPT
        async def execute(self, *args, **kwargs):
            raise NotImplementedError

    agent = _ValidatorAgent(phase_number="datasheet", phase_name="DS Validate")
    spec_json = spec.model_dump_json(indent=2, exclude_none=True)
    try:
        resp = await agent.call_llm(
            messages=[{"role": "user",
                       "content": _build_validator_message(spec_json, text, spec.mpn)}],
            system=_VALIDATOR_SYSTEM_PROMPT,
        )
    except Exception as e:
        log.debug("datasheet.validator.llm_call_failed mpn=%s: %s", spec.mpn, e)
        return None
    body = (resp.get("content") or "").strip()
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", body, flags=re.I | re.M).strip()
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        log.debug("datasheet.validator.json_parse_failed mpn=%s: %s", spec.mpn, e)
        return None
    if not isinstance(data, dict):
        return None
    cons = data.get("contradictions")
    return cons if isinstance(cons, list) else []


def validate_spec_against_pdf(spec: ComponentSpec, text: str,
                              confidence_drop_per_issue: float = 0.2,
                              ) -> tuple[ComponentSpec, list[dict]]:
    """Run a second-pass LLM check; return the spec (possibly with
    lowered confidence) and the list of contradictions found.

    For curated specs (`spec.source == "curated"`) we skip validation -
    they're authoritative by definition.
    """
    if spec is None or not text:
        return spec, []
    if spec.source == "curated":
        # curated specs are the source of truth; nothing to validate against
        return spec, []
    cons = _run_async(_validate_via_llm_async(spec, text))
    if cons is None:
        # validator unavailable / parse failed - log but don't penalize
        log.debug("datasheet.validator.unavailable mpn=%s", spec.mpn)
        return spec, []
    if not cons:
        # second pass found no contradictions - bump confidence a touch
        spec.confidence = min(1.0, spec.confidence + 0.05)
        spec.notes = list(spec.notes) + [
            "Second-pass validation: no contradictions found against source PDF",
        ]
        log.info("datasheet.validator.clean mpn=%s conf=%.2f", spec.mpn, spec.confidence)
        return spec, []
    # contradictions found - drop confidence + add notes
    spec.confidence = max(0.0, spec.confidence - confidence_drop_per_issue * len(cons))
    summary = "; ".join(
        f"{c.get('field','?')} claimed={c.get('claimed')!r} but datasheet says {c.get('datasheet_says')!r}"
        for c in cons[:5]
    )
    spec.notes = list(spec.notes) + [
        f"Second-pass validation flagged {len(cons)} contradiction(s): {summary}",
    ]
    log.warning("datasheet.validator.flagged mpn=%s n=%d conf=%.2f",
                spec.mpn, len(cons), spec.confidence)
    return spec, cons


# ---------------------------------------------------------------------------
# Layer 4: distributor enrichment
# ---------------------------------------------------------------------------


def enrich_with_distributor(spec: ComponentSpec) -> ComponentSpec:
    """Cross-reference DigiKey / Mouser parametric data to fill gaps the
    LLM extractor missed.

    Uses tools.distributor_search.lookup() which returns a PartInfo
    dataclass with manufacturer / description / datasheet_url /
    lifecycle_status / unit_price / stock_quantity / source.

    Silently no-ops when distributor APIs aren't configured (no API key)
    or when the part isn't found - we never let this block extraction.
    """
    try:
        from tools.distributor_search import lookup as _dist_lookup
    except Exception:
        return spec
    try:
        info = _dist_lookup(spec.mpn, timeout_s=8.0)
    except Exception as e:
        log.debug("distributor_enrich.lookup_failed mpn=%s: %s", spec.mpn, e)
        return spec
    if info is None:
        return spec
    notes = list(spec.notes)
    if getattr(info, "manufacturer", None) and not spec.manufacturer:
        spec.manufacturer = info.manufacturer
    if getattr(info, "description", None) and not spec.description:
        spec.description = info.description
    if getattr(info, "datasheet_url", None) and not spec.datasheet_url:
        spec.datasheet_url = info.datasheet_url
    if getattr(info, "lifecycle_status", None):
        notes.append(f"Lifecycle: {info.lifecycle_status} (per {info.source})")
    if getattr(info, "unit_price_usd", None) is not None:
        notes.append(f"Price: USD {info.unit_price_usd:.4f} (per {info.source})")
    elif getattr(info, "unit_price", None) is not None:
        cur = getattr(info, "unit_price_currency", "")
        notes.append(f"Price: {cur} {info.unit_price:.4f} (per {info.source})")
    if getattr(info, "stock_quantity", None) is not None:
        notes.append(f"Stock: {info.stock_quantity} (per {info.source})")
    spec.notes = notes
    log.info("distributor_enrich.applied mpn=%s source=%s lifecycle=%s",
             spec.mpn, info.source, info.lifecycle_status)
    return spec


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


def enqueue_for_review(spec: ComponentSpec, datasheet_url: str = "") -> None:
    """Append a low-confidence spec to the review queue for human gating."""
    if spec.confidence >= _REVIEW_THRESHOLD:
        return
    try:
        _REVIEW_QUEUE.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "queued_at": datetime.utcnow().isoformat() + "Z",
            "mpn": spec.mpn,
            "bus": spec.bus,
            "confidence": spec.confidence,
            "source": spec.source,
            "datasheet_url": datasheet_url or spec.datasheet_url or "",
            "extracted_spec_path": str(_cache_path(spec.mpn)),
            "reason": ("low_confidence" if spec.confidence < _REVIEW_THRESHOLD
                       else "needs_review"),
        }
        with open(_REVIEW_QUEUE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        log.info("review_queue.enqueued mpn=%s confidence=%s",
                 spec.mpn, spec.confidence)
    except OSError as e:
        log.debug("review_queue.write_failed: %s", e)


def list_review_queue() -> list[dict]:
    """Read the review queue. Used by `make review-specs` Makefile target."""
    if not _REVIEW_QUEUE.exists():
        return []
    out = []
    with open(_REVIEW_QUEUE, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


# ---------------------------------------------------------------------------
# Public entry: URL -> spec, with cache + enrichment + review-queue gate
# ---------------------------------------------------------------------------


def _cache_path(mpn: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", mpn or "UNK").strip("_") or "UNK"
    return _EXTRACT_DIR / f"{safe}.json"


def extract_from_url(url: str, mpn: str, hint_bus: str = "") -> Optional[ComponentSpec]:
    if not url or not mpn:
        return None
    cache = _cache_path(mpn)
    if cache.exists():
        try:
            return ComponentSpec(**json.loads(cache.read_text(encoding="utf-8")))
        except Exception:
            log.warning("datasheet.cache_corrupt mpn=%s - re-extracting", mpn)

    text = fetch_datasheet_text(url, mpn=mpn)
    if not text:
        return None
    spec = extract_spec_via_llm(text, mpn, hint_bus)
    if spec is None:
        return None

    # Layer 3.5: second-pass validation. Sends extracted JSON back to LLM
    # to fact-check against the source text. Lowers confidence per
    # contradiction. Skipped for curated specs (they don't go through
    # this path anyway, but defensive guard inside the validator).
    spec, _contradictions = validate_spec_against_pdf(spec, text)

    # Layer 4: distributor enrichment fills gaps with parametric data.
    spec = enrich_with_distributor(spec)

    # Persist and queue for review if needed.
    try:
        _EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(spec.model_dump_json(indent=2, exclude_none=True),
                         encoding="utf-8")
        log.info("datasheet.cached mpn=%s -> %s", mpn, cache)
    except OSError as e:
        log.warning("datasheet.cache_write_failed mpn=%s: %s", mpn, e)
    enqueue_for_review(spec, datasheet_url=url)
    return spec
