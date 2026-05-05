"""
Seed the persistent component cache (`services.component_cache`) by
fan-out across DigiKey + Mouser keyword searches.

Two modes:

  --targeted   (default, ~30 min) — runs a curated set of ~120 RF/digital/
               passive queries, ~50 results each, dedupes to ~6-8 k unique
               MPNs. This is option **A** from the user spec: covers
               every category the LLM is likely to pick during a P1 run
               so the demo's *first* finalize hits a warm cache.

  --full       (~6-8 h, hits rate limits) — runs the targeted set PLUS
               broad-coverage paginated sweeps (offset 0..N) per category
               to push the cache to ~100 k MPNs. This is option **B** —
               the "fully air-gapped, doesn't need the live API at demo
               time" story. Will throttle on DigiKey 429s; uses
               `Retry-After` to back off cleanly.

What it writes:

  * `mpn_cache`         — every PartInfo returned by either distributor.
  * `parametric_cache`  — the merged shortlist per (stage, hint, opts) key
                          so a P1 wizard issuing the same query gets it
                          for free.
  * `url_probe_cache`   — for every distinct datasheet_url, runs a HEAD
                          probe so the BOM render path doesn't have to.

Usage:

    python -m scripts.seed_component_cache --targeted
    python -m scripts.seed_component_cache --full --max-workers 4
    python -m scripts.seed_component_cache --queries-only \\
        "low noise amplifier 2-18 GHz" "RF mixer 1-20 GHz"
    python -m scripts.seed_component_cache --stats   # print cache stats and exit
    python -m scripts.seed_component_cache --purge   # drop stale rows and VACUUM

Honours all the standard distributor env vars (DIGIKEY_*, MOUSER_API_KEY,
SKIP_*, COMPONENT_CACHE_PATH).

Exit codes:
   0  cache populated (any non-zero count)
   2  no API keys configured — nothing to do
   3  cache write failed
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Iterable, Optional

# Allow `python scripts/seed_component_cache.py` from the repo root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.component_cache import get_default  # noqa: E402
from tools import digikey_api, mouser_api          # noqa: E402
from tools.datasheet_verify import is_trusted_vendor_url, verify_url  # noqa: E402
from tools.digikey_api import PartInfo             # noqa: E402
from tools.parametric_search import (              # noqa: E402
    _build_query, _normalise_stage,
)

log = logging.getLogger("seed_component_cache")
logging.basicConfig(
    level=os.environ.get("LOGLEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# ---------------------------------------------------------------------------
# Targeted query set — option A
# ---------------------------------------------------------------------------
# Each entry is `(stage, hint)` matching the parametric_search API. The
# `stage` slug, when known, gets the canonical keyword boost from
# `_STAGE_KEYWORDS`; `hint` carries the spec context. Picked to mirror
# the kinds of components the P1 LLM emits for radar / EW / SDR /
# telemetry projects, plus the standard digital/power/passive supporting
# cast that shows up on every BOM.

_TARGETED_QUERIES: list[tuple[str, str]] = [
    # --- RF amplifiers ---------------------------------------------------
    ("lna",        "wideband 0.1-6 GHz"),
    ("lna",        "L-band 1-2 GHz"),
    ("lna",        "S-band 2-4 GHz"),
    ("lna",        "C-band 4-8 GHz"),
    ("lna",        "X-band 8-12 GHz"),
    ("lna",        "Ku-band 12-18 GHz"),
    ("lna",        "Ka-band 18-40 GHz"),
    ("lna",        "low noise figure < 1 dB"),
    ("lna",        "ultra low noise GaAs"),
    ("lna",        "GaN broadband military"),

    ("driver_amp", "wideband 6 GHz medium power"),
    ("driver_amp", "X-band 1W medium power"),
    ("driver_amp", "10 GHz IIP3 +30 dBm"),

    ("gain_block", "MMIC gain block 50 ohm"),
    ("gain_block", "ERA-5SM PHA-23+ MAR series"),
    ("gain_block", "Mini-Circuits cascadable gain"),

    ("pa",         "RF power amplifier 5W S-band"),
    ("pa",         "GaN HEMT 10W X-band"),
    ("pa",         "linear PA 100mW WCDMA"),
    ("pa",         "high efficiency Doherty PA"),

    # --- Mixers / converters --------------------------------------------
    ("mixer",      "double balanced mixer 0.5-6 GHz"),
    ("mixer",      "I/Q quadrature demodulator 100-6000 MHz"),
    ("mixer",      "wideband mixer 1-12 GHz LO drive +13"),
    ("mixer",      "passive ring mixer high IIP3"),
    ("mixer",      "image reject mixer Ku-band"),

    # --- Filters --------------------------------------------------------
    ("preselector", "ceramic bandpass filter 2.4 GHz"),
    ("preselector", "cavity bandpass filter L-band"),
    ("bpf",        "bandpass filter 5800 MHz"),
    ("bpf",        "SAW bandpass filter 1575 MHz GPS"),
    ("bpf",        "LTCC bandpass filter 2.45 GHz"),
    ("lpf",        "lowpass filter 6 GHz cut off"),
    ("lpf",        "lowpass filter 1 GHz LC"),
    ("hpf",        "highpass filter 100 MHz"),
    ("saw",        "SAW filter 433 MHz ISM"),
    ("saw",        "SAW filter 868 MHz EU LoRa"),
    ("saw",        "SAW filter 915 MHz ISM"),

    # --- Splitters / combiners / baluns ----------------------------------
    ("splitter",   "Wilkinson power divider 2-6 GHz"),
    ("splitter",   "0 degree two-way splitter 700-2700 MHz"),
    ("balun",      "RF balun 50 ohm 100-3000 MHz"),
    ("balun",      "transformer balun 1:4 RF"),

    # --- Switches / attenuators / limiters -------------------------------
    ("switch",     "RF switch SPDT DC-6 GHz absorptive"),
    ("switch",     "RF switch SP4T DC-3 GHz"),
    ("switch",     "PIN diode RF switch high power"),
    ("attenuator", "step attenuator 6-bit 0-31.5 dB"),
    ("attenuator", "DSA digital step attenuator"),
    ("attenuator", "fixed attenuator chip 0-30 dB"),
    ("limiter",    "RF limiter PIN diode 100W"),

    # --- LO / synthesiser / clocks ---------------------------------------
    ("vco",        "wideband VCO 2-4 GHz integrated"),
    ("vco",        "VCO 5-7 GHz low phase noise"),
    ("pll",        "PLL synthesizer integer-N 6 GHz"),
    ("pll",        "fractional-N PLL low phase noise"),
    ("tcxo",       "TCXO 10 MHz 0.5 ppm"),
    ("tcxo",       "TCXO 26 MHz 1 ppm"),
    ("ocxo",       "OCXO 100 MHz 0.01 ppm"),
    ("ocxo",       "OCXO 10 MHz aging"),

    # --- Bias tees, couplers --------------------------------------------
    ("bias_tee",   "bias tee 50 ohm 6 GHz"),

    # --- Data converters -------------------------------------------------
    ("adc",        "high speed ADC 1 GSPS 14-bit"),
    ("adc",        "RF sampling ADC 3 GSPS 12-bit"),
    ("adc",        "16-bit precision ADC SPI"),
    ("adc",        "SAR ADC 8-channel 1 MSPS"),
    ("dac",        "RF DAC 12-bit 3 GSPS"),
    ("dac",        "high speed DAC 14-bit 1 GSPS"),
    ("dac",        "audio DAC stereo I2S"),

    # --- FPGAs / SoCs ----------------------------------------------------
    ("fpga",       "Xilinx Zynq UltraScale+ RFSoC"),
    ("fpga",       "AMD Versal AI core"),
    ("fpga",       "Xilinx Artix-7"),
    ("fpga",       "Xilinx Kintex-7"),
    ("fpga",       "Intel Cyclone V SoC"),
    ("fpga",       "Lattice ECP5"),

    # --- MCUs ------------------------------------------------------------
    ("mcu",        "STM32 Cortex M7 480 MHz"),
    ("mcu",        "STM32 Cortex M4 LQFP100"),
    ("mcu",        "ESP32 WiFi BLE module"),
    ("mcu",        "Nordic nRF5340 BLE"),
    ("mcu",        "RP2040 dual core"),
    ("mcu",        "Microchip ATSAMD51"),

    # --- Power -----------------------------------------------------------
    ("ldo",        "low noise LDO 1A 3.3V"),
    ("ldo",        "RF LDO PSRR 80dB 1.8V"),
    ("ldo",        "low quiescent LDO 100mA 5V"),
    ("buck",       "buck converter 5A 3.3V synchronous"),
    ("buck",       "wide-Vin buck converter 36V"),
    ("buck",       "POL buck converter 1.0V GPU"),

    # --- Passives / interconnect ----------------------------------------
    ("",           "0402 capacitor MLCC RF X7R 100nF"),
    ("",           "0603 chip resistor 50 ohm thin film"),
    ("",           "high Q RF inductor wirewound"),
    ("",           "ferrite bead EMI 100MHz BLM18"),
    ("",           "SMA jack edge mount 50 ohm RF"),
    ("",           "U.FL connector RF micro miniature"),
    ("",           "MMCX coaxial connector RF"),
    ("",           "TNC connector RF panel mount"),
    ("",           "BNC connector PCB mount RF"),
    ("",           "MCX connector RF PCB"),
    ("",           "Samtec ERM-EHM high speed"),
    ("",           "Molex SlimStack BTB"),
    ("",           "JST PH XH connector header"),
    ("",           "Hirose DF40 board to board"),

    # --- Memory / storage ------------------------------------------------
    ("",           "DDR4 SDRAM 1Gb 1600 MHz BGA"),
    ("",           "DDR4 SDRAM 4Gb 2400 MHz"),
    ("",           "QSPI NOR flash 256Mb"),
    ("",           "eMMC 16GB 8-bit"),

    # --- Misc supporting ------------------------------------------------
    ("",           "RS485 transceiver isolated"),
    ("",           "CAN transceiver 5 Mbps"),
    ("",           "Ethernet PHY 10/100/1000 RGMII"),
    ("",           "isolated DC-DC RF receiver"),
    ("",           "TVS diode ESD ARRAY 5V"),
    ("",           "current sense amplifier 80V"),
    ("",           "instrumentation amplifier low offset"),
    ("",           "voltage reference 2.5V 0.05%"),
    ("",           "comparator low power rail-to-rail"),
    ("",           "op-amp JFET low noise audio"),
    ("",           "op-amp GBW 2GHz current feedback"),
]


# ---------------------------------------------------------------------------
# Full-mode pagination — option B
# ---------------------------------------------------------------------------
# DigiKey keyword_search caps `Limit` at 50 per call. To get >50 results
# per query in --full mode we re-run each query while bumping the
# `Offset` field. This wrapper hits the lower-level `digikey_api` directly
# because `parametric_search.find_candidates` doesn't expose offset.

_FULL_PAGES_PER_QUERY = 20  # 20 * 50 = 1000 parts max per category


# ---------------------------------------------------------------------------
# Stats / progress
# ---------------------------------------------------------------------------

@dataclass
class SeedStats:
    queries_run: int = 0
    queries_failed: int = 0
    rows_written: int = 0
    urls_probed: int = 0
    urls_valid: int = 0
    started_at: float = 0.0

    def elapsed_s(self) -> float:
        return time.time() - self.started_at


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _digikey_paginated(query: str, limit_per_page: int, max_pages: int,
                       timeout_s: float = 10.0) -> list[PartInfo]:
    """Hit DigiKey keyword_search across `max_pages` offset windows.

    Implements the `--full` mode pagination that
    `tools.parametric_search.find_candidates` doesn't expose. Per-page
    failures are logged but don't abort the whole sweep — we still
    write whatever pages succeeded."""
    if not digikey_api.is_configured():
        return []
    out: list[PartInfo] = []
    seen_keys: set[str] = set()
    for page in range(max_pages):
        offset = page * limit_per_page
        try:
            from tools.digikey_api import (
                _client_config, _get_token, _open_with_retry,
                _parse_product_details,
            )
            import json as _json
            import urllib.request as _urlreq
            token = _get_token()
            if not token:
                break
            client_id, _, api_host = _client_config()
            url = f"{api_host}/products/v4/search/keyword"
            body = _json.dumps({
                "Keywords": query,
                "Limit": min(limit_per_page, 50),
                "Offset": offset,
            }).encode("utf-8")
            req = _urlreq.Request(
                url, data=body, method="POST",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-DIGIKEY-Client-Id": client_id,
                    "X-DIGIKEY-Locale-Site": "US",
                    "X-DIGIKEY-Locale-Language": "en",
                    "X-DIGIKEY-Locale-Currency": "USD",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "HardwarePipeline/2.0 seed-component-cache",
                },
            )
            payload = _open_with_retry(
                req, timeout_s=timeout_s,
                what=f"seed.digikey.kw q={query!r} offset={offset}",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("seed.dk_page_err q=%r page=%d: %s", query, page, exc)
            break
        if payload is None:
            break
        products = (payload or {}).get("Products") or []
        if not products:
            break  # ran out of pages
        page_added = 0
        for prod in products:
            info = _parse_product_details({"Product": prod}, "")
            if info is None:
                continue
            key = (info.part_number or "").strip().upper()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            out.append(info)
            page_added += 1
        if page_added == 0:
            break  # all-dupes page → done paginating
    return out


def _mouser_keyword(query: str, limit: int = 50, timeout_s: float = 10.0) -> list[PartInfo]:
    if not mouser_api.is_configured():
        return []
    try:
        return mouser_api.keyword_search(query, records=min(limit, 50), timeout_s=timeout_s)
    except Exception as exc:
        log.warning("seed.mouser_err q=%r: %s", query, exc)
        return []


def _run_query(stage: str, hint: str, *, full_mode: bool, timeout_s: float = 10.0,
               ) -> list[PartInfo]:
    """Run one (stage, hint) query against DigiKey + Mouser, dedupe, return."""
    query = _build_query(stage, hint)
    if not query:
        return []
    dk: list[PartInfo] = []
    if digikey_api.is_configured():
        if full_mode:
            dk = _digikey_paginated(query, limit_per_page=50,
                                    max_pages=_FULL_PAGES_PER_QUERY,
                                    timeout_s=timeout_s)
        else:
            try:
                dk = digikey_api.keyword_search(query, limit=50, timeout_s=timeout_s)
            except Exception as exc:
                log.warning("seed.dk_err q=%r: %s", query, exc)
    ms: list[PartInfo] = []
    if mouser_api.is_configured():
        ms = _mouser_keyword(query, limit=50, timeout_s=timeout_s)

    # Dedupe by upper-cased MPN, DigiKey wins on collision (better
    # structured ProductStatus → lifecycle filtering).
    seen: set[str] = set()
    out: list[PartInfo] = []
    for info in dk + ms:
        key = (info.part_number or "").strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(info)
    return out


def _probe_datasheet_urls(infos: Iterable[PartInfo], stats: SeedStats,
                          *, max_workers: int = 8, timeout_s: float = 3.0,
                          ) -> None:
    """Pre-warm the URL probe cache by HEAD-probing every distinct
    datasheet URL we just wrote to mpn_cache. Trusted-vendor URLs are
    written without a probe (allowlist short-circuits)."""
    cache = get_default()
    probed: set[str] = set()
    to_probe: list[str] = []
    for info in infos:
        url = (info.datasheet_url or "").strip()
        if not url or url in probed:
            continue
        probed.add(url)
        if is_trusted_vendor_url(url):
            cache.put_url_probe(url, True, status_code=200,
                                content_type="text/html", is_trusted=True)
            stats.urls_probed += 1
            stats.urls_valid += 1
            continue
        to_probe.append(url)

    if not to_probe:
        return

    def _one(u: str) -> tuple[str, bool]:
        try:
            return u, verify_url(u, timeout=timeout_s)
        except Exception:
            return u, False

    with ThreadPoolExecutor(max_workers=max_workers,
                            thread_name_prefix="seed-probe") as ex:
        futures = [ex.submit(_one, u) for u in to_probe]
        for fut in as_completed(futures):
            u, ok = fut.result()
            cache.put_url_probe(u, ok, is_trusted=False)
            stats.urls_probed += 1
            if ok:
                stats.urls_valid += 1


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def seed(queries: list[tuple[str, str]], *, full_mode: bool, max_workers: int,
         skip_url_probes: bool = False) -> SeedStats:
    """Drive the full seed pipeline: parametric query → mpn_cache write
    → parametric_cache write → URL probe pre-warm. Logs per-query
    progress so you can tail the run."""
    cache = get_default()
    stats = SeedStats(started_at=time.time())

    # Run queries with bounded parallelism — DigiKey's per-key rate
    # limit means more than ~4 concurrent keyword searches just causes
    # Retry-After backoffs that net out the same total wall-clock.
    def _do_one(qpair: tuple[str, str]) -> tuple[tuple[str, str], list[PartInfo]]:
        stage, hint = qpair
        infos = _run_query(stage, hint, full_mode=full_mode)
        return qpair, infos

    try:
        from tools.parametric_search import _persistent_query_hash, _CACHE_TTL_S  # noqa: F401
    except Exception:
        _persistent_query_hash = None  # type: ignore[assignment]

    with ThreadPoolExecutor(max_workers=max_workers,
                            thread_name_prefix="seed-query") as ex:
        futures = {ex.submit(_do_one, q): q for q in queries}
        for fut in as_completed(futures):
            qpair, infos = fut.result()
            stats.queries_run += 1
            if not infos:
                stats.queries_failed += 1
                log.info("[seed] (%d/%d) MISS  %s",
                         stats.queries_run, len(queries), _build_query(*qpair))
                continue
            written = cache.bulk_put_mpns(infos)
            stats.rows_written += written

            # Also write the parametric shortlist under the same canonical
            # key parametric_search uses, so a wizard query during the
            # actual P1 chat goes straight to the disk cache instead of
            # re-running the live API. Cache key MUST match the 3-tuple
            # shape `find_candidates` builds — `(stage_norm, hint, drop_obsolete)`
            # — so hashes line up. `max_per_source` is intentionally NOT
            # in the key (a 50-result fetch supersedes 5-result requests
            # via slicing on read).
            if _persistent_query_hash is not None:
                stage, hint = qpair
                cache_key = (
                    _normalise_stage(stage),
                    (hint or "").strip().lower(),
                    True,                       # drop_obsolete
                )
                qhash = _persistent_query_hash(cache_key)
                if qhash:
                    cache.put_parametric(qhash, _build_query(stage, hint), infos)

            log.info(
                "[seed] (%d/%d) OK    %s → %d rows  (cum: %d rows / %.1fs)",
                stats.queries_run, len(queries), _build_query(*qpair),
                written, stats.rows_written, stats.elapsed_s(),
            )

            if not skip_url_probes:
                _probe_datasheet_urls(infos, stats)

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="seed_component_cache",
        description="Pre-populate the persistent component cache.",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--targeted", action="store_true",
                   help="Curated ~120 queries → ~6-8k MPNs (default, ~30 min).")
    g.add_argument("--full", action="store_true",
                   help="Targeted set + paginated sweeps → up to ~100k MPNs (~6-8 h).")
    g.add_argument("--queries-only", nargs="+", metavar="HINT",
                   help="Run only the given free-form hints (debug / spot-cache).")
    g.add_argument("--stats", action="store_true", help="Print cache stats and exit.")
    g.add_argument("--purge", action="store_true",
                   help="Drop stale rows past their TTL and VACUUM, then exit.")
    p.add_argument("--max-workers", type=int, default=4,
                   help="Concurrent queries (default 4; raise carefully — "
                        "DigiKey rate-limits at ~5 RPS / key).")
    p.add_argument("--skip-url-probes", action="store_true",
                   help="Skip the post-fetch URL probe pre-warm.")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)

    cache = get_default()

    if args.stats:
        s = cache.stats()
        print(f"Component cache @ {cache.db_path}")
        for k, v in s.items():
            print(f"  {k:24s} {v:>10,}")
        return 0

    if args.purge:
        s = cache.purge_stale()
        cache.vacuum()
        print(f"Purged from {cache.db_path}:")
        for k, v in s.items():
            print(f"  {k:24s} {v:>10,}")
        return 0

    if not (digikey_api.is_configured() or mouser_api.is_configured()):
        log.error(
            "No distributor API configured — set DIGIKEY_CLIENT_ID/SECRET "
            "and/or MOUSER_API_KEY before running the seed.")
        return 2

    # Build the query list for this mode.
    if args.queries_only:
        queries: list[tuple[str, str]] = [("", h) for h in args.queries_only]
    else:
        queries = list(_TARGETED_QUERIES)

    full_mode = bool(args.full)
    if full_mode:
        log.info("Running --full mode: %d categories x %d pages = up to %d parts.",
                 len(queries), _FULL_PAGES_PER_QUERY,
                 len(queries) * _FULL_PAGES_PER_QUERY * 50)
    else:
        log.info("Running --targeted mode: %d queries x 50 results each.",
                 len(queries))

    stats = seed(
        queries, full_mode=full_mode, max_workers=args.max_workers,
        skip_url_probes=args.skip_url_probes,
    )

    log.info(
        "Seed complete: %d queries (%d failed), %d MPN rows written, "
        "%d URLs probed (%d valid), elapsed %.1fs.",
        stats.queries_run, stats.queries_failed, stats.rows_written,
        stats.urls_probed, stats.urls_valid, stats.elapsed_s(),
    )
    final = cache.stats()
    log.info("Cache totals: %s", final)
    if final["mpn_cache"] == 0:
        log.error("No rows in mpn_cache — check API keys + network.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
