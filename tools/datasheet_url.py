"""
Canonical datasheet URL builder — DigiKey + Mouser only.

Given `(manufacturer, part_number)`, produce the canonical product-page URL
for the BOM table. Per user feedback (2026-04-24), every URL emitted here
MUST stay inside the DigiKey/Mouser ecosystem — manufacturer-site URLs
(analog.com, qorvo.com, ti.com, skyworksinc.com, ...) and search-engine
URLs (duckduckgo.com, google.com) are no longer acceptable because they
either 404 (vendor URL schemes drift faster than templates can keep up)
or land users off-platform with no purchase / lifecycle context.

Public APIs:
  - `canonical_datasheet_url(mfr, part)` → `(digikey_search_url, "search")`
    Single URL, used when only one is needed.
  - `candidate_datasheet_urls(mfr, part)` → `[digikey_url, mouser_url]`
    Both major distributors. The BOM renderer in `requirements_agent.py`
    HEAD-probes them and picks the first live one (DigiKey wins by
    default since both endpoints always return 2xx, but a real Mouser
    PDF the LLM emits — `mouser.com/datasheet/...` — gets priority via
    the renderer's distributor-domain check).

URL shapes (both never 404 by construction):
  - DigiKey: `https://www.digikey.com/en/products/result?keywords={MPN}`
  - Mouser:  `https://www.mouser.com/c/?q={MPN}`

Both endpoints redirect to the part page when there's exactly one
match, otherwise show a filterable result list keyed to the MPN.

History note (2026-04-24): the manufacturer-URL templates
(`_adi_candidates`, `_ti`, `_qorvo`, `_skyworks`, `_minicircuits`,
`_macom`, `_st`, `_microchip`, `_infineon`, `_onsemi`, ...), the
`_BUILDERS` registry, and the DuckDuckGo fallback have been retained as
private functions for any test that still imports them, but neither
public API calls them anymore. The BOM renderer in
`agents/requirements_agent.py` (`_build_components_md` and the in-chat
draft renderer) is the dominant caller; both pick up the new behavior
automatically. Mirror change in `tools/datasheet_resolver.py`
(P11, 2026-04-24).
"""
from __future__ import annotations

import re
from typing import Tuple, Optional
from urllib.parse import quote

# ─────────────────────────────────────────────────────────────────────────────
# Manufacturer aliases → canonical key
# ─────────────────────────────────────────────────────────────────────────────
#
# Maps the many spellings that LLMs emit ("ADI", "Analog", "Analog Devices Inc",
# "Hittite Microwave" (legacy, now ADI), etc.) to a single internal key.
_MFR_ALIASES = {
    # Analog Devices — absorbed Hittite (2014), Linear Technology (2017),
    # Maxim Integrated (2021). All three legacy domains redirect to
    # analog.com product pages keyed on the original part number.
    "adi":                      "analog_devices",
    "analog":                   "analog_devices",
    "analog devices":           "analog_devices",
    "analog devices inc":       "analog_devices",
    "analog devices, inc.":     "analog_devices",
    "hittite":                  "analog_devices",
    "hittite microwave":        "analog_devices",
    "linear":                   "analog_devices",
    "linear technology":        "analog_devices",
    "linear technology corp":   "analog_devices",
    "ltc":                      "analog_devices",
    "maxim":                    "analog_devices",
    "maxim integrated":         "analog_devices",

    # Texas Instruments — case-insensitive product URLs
    "ti":                   "texas_instruments",
    "texas instruments":    "texas_instruments",
    "texas instruments inc":"texas_instruments",
    "burr-brown":           "texas_instruments",  # absorbed 2000
    "national":             "texas_instruments",  # absorbed 2011
    "national semiconductor":"texas_instruments",

    # Qorvo — formed by TriQuint + RFMD merger (2015)
    "qorvo":    "qorvo",
    "triquint": "qorvo",
    "rfmd":     "qorvo",
    "rf micro devices": "qorvo",

    # MACOM
    "macom":      "macom",
    "macom technology": "macom",
    "macom technology solutions": "macom",
    "m/a-com":    "macom",
    "ma-com":     "macom",

    # Mini-Circuits
    "mini-circuits":   "mini_circuits",
    "mini circuits":   "mini_circuits",
    "minicircuits":    "mini_circuits",
    "mcl":             "mini_circuits",

    # Skyworks
    "skyworks":               "skyworks",
    "skyworks solutions":     "skyworks",
    "skyworks solutions inc": "skyworks",

    # NXP — absorbed Freescale (2015)
    "nxp":           "nxp",
    "nxp semiconductors": "nxp",
    "freescale":     "nxp",
    "freescale semiconductor": "nxp",

    # STMicroelectronics
    "st":             "st",
    "stmicro":        "st",
    "stmicroelectronics": "st",

    # Infineon — absorbed Cypress (2020), International Rectifier (2015)
    "infineon":  "infineon",
    "infineon technologies": "infineon",
    "cypress":   "infineon",
    "cypress semiconductor": "infineon",

    # Microchip — absorbed Atmel (2016), Microsemi (2018)
    "microchip":    "microchip",
    "microchip technology": "microchip",
    "atmel":        "microchip",
    "microsemi":    "microchip",

    # Renesas — absorbed IDT (2019), Dialog Semi (2021)
    "renesas":  "renesas",
    "renesas electronics": "renesas",
    "idt":      "renesas",
    "integrated device technology": "renesas",
    "dialog":   "renesas",
    "dialog semiconductor": "renesas",

    # Murata
    "murata": "murata",
    "murata manufacturing": "murata",

    # Vishay
    "vishay": "vishay",
    "vishay intertechnology": "vishay",

    # Coilcraft
    "coilcraft": "coilcraft",

    # ON Semiconductor (now onsemi)
    "on semi":     "onsemi",
    "on semiconductor": "onsemi",
    "onsemi":      "onsemi",

    # Xilinx → AMD (2022)
    "xilinx": "amd_xilinx",
    "amd":    "amd_xilinx",
    "amd xilinx": "amd_xilinx",

    # Intel (includes Altera, absorbed 2015)
    "intel":  "intel",
    "altera": "intel",

    # Lattice
    "lattice": "lattice",
    "lattice semiconductor": "lattice",

    # Silicon Labs
    "silicon labs":      "silabs",
    "silabs":            "silabs",
    "silicon laboratories": "silabs",

    # Pasternack
    "pasternack": "pasternack",

    # Crystek
    "crystek": "crystek",

    # Kyocera AVX
    "kyocera":    "kyocera_avx",
    "avx":        "kyocera_avx",
    "kyocera avx":"kyocera_avx",
}


# ─────────────────────────────────────────────────────────────────────────────
# Per-vendor URL builders
# ─────────────────────────────────────────────────────────────────────────────
#
# Each function takes the normalized part number and returns a product-page URL
# (NOT a PDF URL — vendor product pages usually link to the PDF via a "Download
# Data Sheet" button, which is the stable modern pattern).

# ADI / Hittite / Linear Tech product URLs key on the BASE part number,
# not the orderable part number with packaging suffix. The tricky part is that
# different ADI sub-families treat the letter between digits and package code
# differently:
#
#   HMC family (legacy Hittite):  HMC625BLP5E  → base = HMC625B
#     The letter `B` after digits is a SILICON REVISION — keep it.
#
#   Other ADI families (ADL, ADF, LTC, LT, MAX, AD, DAC, ADC, ADUM, ADRF, ...):
#     ADL5523ACPZ  → base = ADL5523
#     ADF4351BCPZ  → base = ADF4351
#     LTC6955IUFD  → base = LTC6955
#     The letter `A`/`B`/`I`/`J` is a TEMPERATURE GRADE — drop it.
#
# Known ADI package prefixes: LP/LC (LFCSP / leadless ceramic), CHIP(S) (bare
# die), CPZ/ACPZ/BCPZ (LFCSP Z-lead), IUFD/IUFF (QFN industrial grade),
# LFCSP, TQFP, BFD, UFD, ARMZ/ARZ/AQZ/BQZ/CQZ (SOIC), BN, HB, EP.

_ADI_PKG_TOKEN = (
    r"(?:LP|LC|CHIPS?|CPZ|ACPZ|BCPZ|IUFD|IUFF|LFCSP|TQFP|BFD|UFD|"
    r"ARMZ|ARZ|AQZ|BQZ|CQZ|BN|HB|EP)"
)

# HMC family: keep single letter after digits (silicon rev)
_ADI_HMC_SPLIT = re.compile(r"^(HMC\d+[A-Z]?)" + _ADI_PKG_TOKEN, re.IGNORECASE)

# Non-HMC ADI families: drop single letter after digits (temp grade)
_ADI_OTHER_SPLIT = re.compile(
    r"^((?:ADL|ADF|ADRF|LTC|LTM|LT|MAX|ADA|ADAR|ADN|ADRV|ADM|ADE|ADIS|ADXL|"
    r"ADCLK|ADUM|AD|DAC|ADC)\d+)[A-Z]?" + _ADI_PKG_TOKEN,
    re.IGNORECASE,
)


def _adi_base_part(part: str) -> str:
    """Strip ADI/Hittite/LT packaging suffix to produce the URL base part."""
    p = part.upper()
    # HMC family: silicon rev letter is part of the base
    m = _ADI_HMC_SPLIT.match(p)
    if m:
        return m.group(1).lower()
    # Non-HMC ADI families: temp-grade letter is not part of the base
    m = _ADI_OTHER_SPLIT.match(p)
    if m:
        return m.group(1).lower()
    return part.lower()


def _adi(part: str) -> str:
    """Single-URL form kept for back-compat. Prefer `_adi_candidates()`."""
    return _adi_candidates(part)[0]


def _adi_candidates(part: str) -> list[str]:
    """
    Return an ordered list of candidate URLs for an ADI/Hittite/LT/Maxim part.
    The renderer HEAD-checks each candidate and uses the first 2xx response.

    Empirically (as of 2026-04-20):
      - Modern ADI families (ADL, ADA, ADRF, ADF, AD, DAC, ADC, ADUM, ADCLK):
          /en/products/<part_lower>.html works (e.g. adl8104, ad9361)
      - Legacy Hittite (HMC*): URL path has churned through multiple migrations.
        Known patterns — try them in order:
            /en/products/<part_lower>.html
            /en/products/<part_upper>.html
            /en/products/<part_lower>       (no .html extension, newer CMS)
            /media/en/technical-documentation/data-sheets/<part_upper>.pdf
            /media/en/technical-documentation/data-sheets/<part>.pdf
      - Linear Technology (LTC*, LT*, LTM*): usually /en/products/<part_lower>.html
        but some migrated pages use the no-extension form. Include both.
      - Final fallback = ADI parametric-search scoped to Products — this ALWAYS
        resolves and always lands on the real product card, unlike the generic
        /en/search.html page which frequently renders empty results.
    """
    base = _adi_base_part(part)
    upper = base.upper()
    lower = base.lower()
    candidates = [
        f"https://www.analog.com/en/products/{lower}.html",
        f"https://www.analog.com/en/products/{upper}.html",
        f"https://www.analog.com/en/products/{lower}",
        f"https://www.analog.com/media/en/technical-documentation/data-sheets/{upper}.pdf",
        f"https://www.analog.com/media/en/technical-documentation/data-sheets/{base}.pdf",
        # Universal safety net — ADI parametric search, filtered to products.
        # Unlike /en/search.html#q=<x>, this URL reliably renders the product card.
        f"https://www.analog.com/en/parametric-search.html?query={upper}",
    ]
    return candidates


# TI package suffixes are typically 3-5 trailing letters after the numeric
# core (e.g. LM5175PWPR → PWPR = TSSOP + Reel). TI product URLs accept both
# base and orderable, but the base is cleaner. Split at the first package
# suffix if we can detect one.
_TI_PKG_SPLIT = re.compile(
    r"^([A-Z]{1,5}\d+[A-Z]?)"
    r"(?:PWPR?|PWR|DGKR|DGK|DBVR|DBV|DCKR|DCK|DR|D|DDAR|DDA|"
    r"DGQR|DGQ|RGWR|RGW|RGYR|RGY|NOPB|RTER|RTE|RGVR|RGV)",
    re.IGNORECASE,
)


def _ti_base_part(part: str) -> str:
    m = _TI_PKG_SPLIT.match(part.upper())
    if m:
        return m.group(1).lower()
    return part.lower()


def _ti(part: str) -> str:
    # TI uses lowercase part number (case-insensitive, but lower is canonical)
    # https://www.ti.com/product/lm5175
    return f"https://www.ti.com/product/{_ti_base_part(part)}"

def _qorvo(part: str) -> str:
    # https://www.qorvo.com/products/p/TGA2214-CP
    return f"https://www.qorvo.com/products/p/{part.upper()}"

def _macom(part: str) -> str:
    # https://www.macom.com/products/product-detail/MAAL-011138
    return f"https://www.macom.com/products/product-detail/{part.upper()}"

def _mini_circuits(part: str) -> str:
    # https://www.minicircuits.com/WebStore/dashboard.html?model=ZX60-P103LN%2B
    # The model= query is URL-encoded (the '+' suffix must be %2B)
    return f"https://www.minicircuits.com/WebStore/dashboard.html?model={quote(part, safe='')}"

def _skyworks(part: str) -> str:
    # Skyworks landed on a flat product-detail path in 2024
    # https://www.skyworksinc.com/Products/Amplifiers/SKY65404-31
    # Without the category slug the canonical fallback is their search.
    # We use the search form here; it reliably returns the product card.
    return f"https://www.skyworksinc.com/Search?k={quote(part)}"

def _nxp(part: str) -> str:
    # https://www.nxp.com/products/_/_/_:MRFX1K80H
    # Flat product-landing works with a simple pn-suffixed URL
    return f"https://www.nxp.com/products/{part.upper()}"

def _st(part: str) -> str:
    # https://www.st.com/en/rf-transistors/<part>.html — category varies
    # The canonical /search/en/partNumber path is stable.
    return f"https://www.st.com/content/st_com/en/search.html#q={quote(part)}-t=products"

def _infineon(part: str) -> str:
    # https://www.infineon.com/cms/en/product/<category>/<part>/
    # Category varies; the stable fallback is the part-number search.
    return f"https://www.infineon.com/cms/en/search.html#!term={quote(part)}&view=downloads"

def _microchip(part: str) -> str:
    # https://www.microchip.com/en-us/product/<part>
    return f"https://www.microchip.com/en-us/product/{part.upper()}"

def _renesas(part: str) -> str:
    # https://www.renesas.com/us/en/products/<category>/<part>
    return f"https://www.renesas.com/us/en/search?keywords={quote(part)}"

def _murata(part: str) -> str:
    # https://www.murata.com/en-global/products/productdetail?partno=<part>
    return f"https://www.murata.com/en-global/products/productdetail?partno={quote(part)}"

def _vishay(part: str) -> str:
    # https://www.vishay.com/en/product/<id>/ — internal-id keyed, search more reliable
    return f"https://www.vishay.com/en/search/?query={quote(part)}"

def _coilcraft(part: str) -> str:
    # https://www.coilcraft.com/en-us/products/rf/air-core-inductors/0402cs/
    return f"https://www.coilcraft.com/en-us/search/?searchtext={quote(part)}"

def _onsemi(part: str) -> str:
    # https://www.onsemi.com/products/<cat>/<part>
    return f"https://www.onsemi.com/products/search?q={quote(part)}"

def _amd_xilinx(part: str) -> str:
    # AMD/Xilinx: Kintex/Virtex/Zynq family parts resolve via product search
    return f"https://www.amd.com/en/search.html#q={quote(part)}"

def _intel(part: str) -> str:
    # Intel ark for product details
    return f"https://www.intel.com/content/www/us/en/search.html?ws={quote(part)}"

def _lattice(part: str) -> str:
    return f"https://www.latticesemi.com/en/Search?q={quote(part)}"

def _silabs(part: str) -> str:
    return f"https://www.silabs.com/search?q={quote(part)}"

def _pasternack(part: str) -> str:
    # https://www.pasternack.com/images/ProductPDF/<part>.pdf
    return f"https://www.pasternack.com/search.aspx?q={quote(part)}"

def _crystek(part: str) -> str:
    return f"https://www.crystek.com/crystal/spec-sheets/{part.lower()}.pdf"

def _kyocera_avx(part: str) -> str:
    return f"https://www.kyocera-avx.com/search/?q={quote(part)}"


_BUILDERS = {
    "analog_devices":     (_adi,            "canonical"),
    "texas_instruments":  (_ti,             "canonical"),
    "qorvo":              (_qorvo,          "canonical"),
    "macom":              (_macom,          "canonical"),
    "mini_circuits":      (_mini_circuits,  "canonical"),
    "skyworks":           (_skyworks,       "search"),
    "nxp":                (_nxp,            "canonical"),
    "st":                 (_st,             "search"),
    "infineon":           (_infineon,       "search"),
    "microchip":          (_microchip,      "canonical"),
    "renesas":            (_renesas,        "search"),
    "murata":             (_murata,         "canonical"),
    "vishay":             (_vishay,         "search"),
    "coilcraft":          (_coilcraft,      "search"),
    "onsemi":             (_onsemi,         "search"),
    "amd_xilinx":         (_amd_xilinx,     "search"),
    "intel":              (_intel,          "search"),
    "lattice":            (_lattice,        "search"),
    "silabs":             (_silabs,         "search"),
    "pasternack":         (_pasternack,     "search"),
    "crystek":            (_crystek,        "canonical"),
    "kyocera_avx":        (_kyocera_avx,    "search"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Part-number normalization
# ─────────────────────────────────────────────────────────────────────────────
#
# Some LLM outputs include packaging suffixes or whitespace that break URL
# pattern matching. This normalizer is conservative: it only strips whitespace,
# "#" quantity markers, and trailing packaging codes that are separated by a
# space or slash. It never truncates the main part number.
_PACKAGING_TAIL = re.compile(r"(?:\s+|/)(?:TR|TRAY|REEL|CUT|T&R|BULK)\b.*$", re.IGNORECASE)

def normalize_part_number(part_no: str) -> str:
    """Trim whitespace and strip packaging tails; preserve original casing."""
    if not part_no:
        return ""
    p = part_no.strip()
    # Strip packaging tails appended with a space or slash
    p = _PACKAGING_TAIL.sub("", p)
    # Collapse internal whitespace
    p = re.sub(r"\s+", "", p)
    return p


def _normalize_mfr(mfr: str) -> Optional[str]:
    if not mfr:
        return None
    key = mfr.strip().lower()
    # Strip common suffixes
    for suf in (", inc.", " inc.", " inc", " corporation", " corp.", " corp",
                " ltd.", " ltd", " gmbh", ", ltd.", " semiconductor", " technology",
                " technologies"):
        if key.endswith(suf):
            key = key[: -len(suf)]
    key = key.strip()
    return _MFR_ALIASES.get(key)


# ─────────────────────────────────────────────────────────────────────────────
# Internal: distributor URL builders — the only two hosts the public
# APIs are allowed to emit. Both endpoints accept a `keywords=`/`q=`
# query parameter and never 404 — they redirect to the part page when
# there's exactly one match, otherwise they show a filterable result
# list keyed to the MPN.
# ─────────────────────────────────────────────────────────────────────────────
def _digikey_search_url(part_number: str) -> str:
    """DigiKey keyword-search URL for `part_number`."""
    part = (part_number or "").strip()
    return f"https://www.digikey.com/en/products/result?keywords={quote(part, safe='')}"


def _mouser_search_url(part_number: str) -> str:
    """Mouser keyword-search URL for `part_number`. Mouser's catalog
    skews differently from DigiKey's (better Mini-Circuits coverage,
    weaker general semis), so keeping both in the candidate list gives
    the BOM renderer a real second option."""
    part = (part_number or "").strip()
    return f"https://www.mouser.com/c/?q={quote(part, safe='')}"


# ─────────────────────────────────────────────────────────────────────────────
# Public API — DigiKey + Mouser only, as of 2026-04-24
# ─────────────────────────────────────────────────────────────────────────────
def canonical_datasheet_url(
    manufacturer: str,
    part_number: str,
) -> Tuple[str, str]:
    """
    Build the canonical datasheet URL for `(manufacturer, part_number)`.

    Always returns `(digikey_search_url, "search")`.  The `manufacturer`
    argument is intentionally ignored — vendor templates frequently 404
    (ADI / Qorvo / Skyworks URL schemes drift, slug rules vary by family),
    and DuckDuckGo / Google fallbacks land users off-platform without
    purchase / lifecycle data.  This function deliberately returns only
    one URL; for callers that want both DigiKey and Mouser candidates
    use `candidate_datasheet_urls` instead.

    Returns `(url, confidence)`:
      - `confidence` is always `"search"` because DigiKey's keyword
        endpoint is a search URL (it redirects to the part page when
        there's exactly one match — still a "search" semantically).
      - `url` is non-empty unless `part_number` normalises to "".

    Never raises. Safe to call on LLM output.
    """
    part = normalize_part_number(part_number)
    if not part:
        return ("", "unknown")
    return (_digikey_search_url(part), "search")


def confidence_badge(confidence: str) -> str:
    """Small UI helper — map confidence to a badge string for the BOM table."""
    return {
        "canonical": "✓",
        "search":    "⚲",
        "unknown":   "?",
    }.get(confidence, "?")


def candidate_datasheet_urls(manufacturer: str, part_number: str) -> list[str]:
    """
    Return the ordered list of candidate URLs for `(mfr, part)`.

    Returns `[digikey_search_url, mouser_search_url]` — both major
    distributors get a slot. The BOM renderer in
    `agents/requirements_agent.py` HEAD-probes each candidate; both
    endpoints always return 2xx so the FIRST one (DigiKey) wins by
    default.  The renderer's separate "prefer LLM URL when it's on a
    distributor domain" logic decides whether a real Mouser product
    PDF / page (`mouser.com/datasheet/...`) wins ahead of either
    fallback — see `_distributor_url_priority` there.

    The `manufacturer` argument is intentionally ignored — vendor name
    is not used for URL building anymore.

    Empty list iff `part_number` normalises to "".
    """
    part = normalize_part_number(part_number)
    if not part:
        return []
    return [_digikey_search_url(part), _mouser_search_url(part)]


# ─────────────────────────────────────────────────────────────────────────────
# Quick smoke test (runs when invoked as script).
# Updated 2026-04-24 to assert distributor-only behavior — every URL
# must land on digikey.com OR mouser.com, never on a manufacturer site
# or a search engine.
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    DK_PREFIX = "https://www.digikey.com/en/products/result?keywords="
    MS_PREFIX = "https://www.mouser.com/c/?q="
    BANNED = ("analog.com", "qorvo.com", "ti.com", "skyworksinc.com",
              "minicircuits.com", "macom.com", "st.com", "microchip.com",
              "infineon.com", "onsemi.com", "duckduckgo.com", "google.com")
    cases = [
        ("Analog Devices",      "ADL8104"),
        ("ADI",                 "HMC8410"),
        ("Texas Instruments",   "LM5175"),
        ("Qorvo",               "TGA2214-CP"),
        ("Skyworks",            "SKY65404-31"),
        ("Mini-Circuits",       "ZX60-P103LN+"),
        ("Mercury Systems",     "AM3063"),
        ("ROHM Semiconductor",  "BD50GC0JEFJ-E2"),
        ("SomeUnknownCo",       "XYZ-999"),
    ]
    print(f"{'Vendor':<24} {'Part':<20} canonical URL    +    candidate list")
    print("-" * 120)
    ok = True
    for mfr, part in cases:
        url, conf = canonical_datasheet_url(mfr, part)
        cands = candidate_datasheet_urls(mfr, part)
        print(f"{mfr:<24} {part:<20} {conf:<8} {url}")
        for c in cands:
            print(f"{'':<24} {'':<20}        +  {c}")
        # Canonical: DigiKey only.
        if not url.startswith(DK_PREFIX):
            print(f"   !! canonical EXPECTED to start with {DK_PREFIX}")
            ok = False
        # Candidates: DigiKey first, Mouser second.
        if len(cands) != 2:
            print(f"   !! candidates EXPECTED to be [digikey, mouser]")
            ok = False
        elif not cands[0].startswith(DK_PREFIX) or not cands[1].startswith(MS_PREFIX):
            print(f"   !! candidates EXPECTED order [digikey, mouser]")
            ok = False
        # Both must be free of banned hosts.
        for u in [url, *cands]:
            for bad in BANNED:
                if bad in u:
                    print(f"   !! found banned host {bad!r} in URL {u!r}")
                    ok = False
    print("-" * 120)
    print("ALL PASS" if ok else "SOME CHECKS FAILED")
