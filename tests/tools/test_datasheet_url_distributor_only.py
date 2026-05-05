"""Distributor-only mandate for `tools.datasheet_url`.

Per user feedback (2026-04-24): every URL the BOM renderer puts in the
"Datasheet" column MUST stay inside the DigiKey/Mouser ecosystem — no
manufacturer-site URLs (analog.com, qorvo.com, ti.com, skyworksinc.com,
minicircuits.com), no search-engine URLs (duckduckgo.com, google.com).

These tests guard the two public APIs in `tools.datasheet_url` —
`canonical_datasheet_url` and `candidate_datasheet_urls` — against
re-introducing manufacturer / search-engine fallbacks. If you bump
this regression to add back any non-distributor host, you'll fail every
case in `BANNED_HOSTS` simultaneously.

Mirror tests in `tests/tools/test_datasheet_resolver.py::TestBuildChain`
guard the same invariant for the other datasheet pipeline (`PartInfo`-
based resolver). Both must agree.
"""
from __future__ import annotations

import pytest

from tools.datasheet_url import (
    canonical_datasheet_url,
    candidate_datasheet_urls,
)


DIGIKEY_PREFIX = "https://www.digikey.com/en/products/result?keywords="
MOUSER_PREFIX = "https://www.mouser.com/c/?q="

# Hosts that must NEVER appear in any URL the public APIs return.
BANNED_HOSTS = (
    # Manufacturer sites — templates drift, slug rules vary, frequent 404s.
    "analog.com",
    "qorvo.com",
    "ti.com",
    "skyworksinc.com",
    "minicircuits.com",
    "macom.com",
    "st.com",
    "microchip.com",
    "infineon.com",
    "onsemi.com",
    "murata.com",
    "vishay.com",
    "coilcraft.com",
    # Search engines — off-platform, no purchase / lifecycle data.
    "duckduckgo.com",
    "google.com",
    "bing.com",
)


# ---------------------------------------------------------------------------
# canonical_datasheet_url
# ---------------------------------------------------------------------------

class TestCanonicalDatasheetUrl:

    @pytest.mark.parametrize("mfr,part", [
        ("Analog Devices",     "ADL8107"),
        ("ADI",                "HMC8410"),
        ("Texas Instruments",  "LM5175"),
        ("Qorvo",              "TGA2214-CP"),
        ("Skyworks Solutions", "SKY65404-31"),
        ("Mini-Circuits",      "ZX60-P103LN+"),
        ("MACOM",              "MAAL-011138"),
        ("STMicroelectronics", "STM32F407"),
        ("Microchip",          "PIC32MX170F256B"),
        ("Infineon",           "BCR401U"),
        ("ROHM Semiconductor", "BD50GC0JEFJ-E2"),
        ("Mercury Systems",    "AM3063"),
        ("SomeUnknownCo",      "XYZ-999"),
    ])
    def test_always_returns_digikey_search_url(self, mfr, part):
        url, conf = canonical_datasheet_url(mfr, part)
        assert url.startswith(DIGIKEY_PREFIX), (
            f"{mfr}/{part}: expected DigiKey search URL, got {url!r}"
        )
        # Encoded MPN is in the URL (with `+` → `%2B` etc.)
        from urllib.parse import quote
        assert quote(part, safe="") in url
        # Confidence is always "search" — DigiKey's keyword endpoint is
        # a search URL even when it redirects to a single product.
        assert conf == "search"

    @pytest.mark.parametrize("mfr,part", [
        ("Analog Devices",     "ADL8107"),
        ("Qorvo",              "TGA2214-CP"),
        ("Skyworks Solutions", "SKY65404-31"),
        ("Mercury Systems",    "AM3063"),
        ("ROHM Semiconductor", "BD50GC0JEFJ-E2"),
        ("SomeUnknownCo",      "XYZ-999"),
    ])
    def test_url_never_points_at_manufacturer_or_search_engine(self, mfr, part):
        url, _ = canonical_datasheet_url(mfr, part)
        for host in BANNED_HOSTS:
            assert host not in url, (
                f"{mfr}/{part}: URL must not contain banned host {host!r} — "
                f"got {url!r}"
            )

    def test_empty_part_returns_empty_url(self):
        url, conf = canonical_datasheet_url("Analog Devices", "")
        assert url == ""
        assert conf == "unknown"

    def test_whitespace_part_returns_empty_url(self):
        url, _ = canonical_datasheet_url("Analog Devices", "   ")
        assert url == ""

    def test_manufacturer_argument_is_ignored(self):
        """The mfr argument is no longer used for URL building. Same MPN
        with different vendors must produce the same URL."""
        u1, _ = canonical_datasheet_url("Analog Devices", "ADL8107")
        u2, _ = canonical_datasheet_url("ADI", "ADL8107")
        u3, _ = canonical_datasheet_url("", "ADL8107")
        u4, _ = canonical_datasheet_url("ObscureCo Ltd", "ADL8107")
        assert u1 == u2 == u3 == u4


# ---------------------------------------------------------------------------
# candidate_datasheet_urls
# ---------------------------------------------------------------------------

class TestCandidateDatasheetUrls:

    @pytest.mark.parametrize("mfr,part", [
        ("Analog Devices",     "ADL8107"),
        ("Qorvo",              "TGA2214-CP"),
        ("Skyworks Solutions", "SKY65404-31"),
        ("Mercury Systems",    "AM3063"),
        ("ROHM Semiconductor", "BD50GC0JEFJ-E2"),
        ("SomeUnknownCo",      "XYZ-999"),
    ])
    def test_returns_digikey_then_mouser(self, mfr, part):
        """User feedback (2026-04-24): "digikey/mouser, not only digikey".
        Both major distributors get a slot; DigiKey first since it has
        the broader catalog, Mouser second."""
        urls = candidate_datasheet_urls(mfr, part)
        assert len(urls) == 2, (
            f"expected exactly 2 URLs (DigiKey + Mouser), got {urls!r}"
        )
        assert urls[0].startswith(DIGIKEY_PREFIX), (
            f"DigiKey must be first, got {urls[0]!r}"
        )
        assert urls[1].startswith(MOUSER_PREFIX), (
            f"Mouser must be second, got {urls[1]!r}"
        )

    @pytest.mark.parametrize("mfr,part", [
        ("Texas Instruments",  "LM5175"),
        ("Mini-Circuits",      "ZX60-P103LN+"),
        ("Mercury Systems",    "AM3063"),
        ("ROHM Semiconductor", "BD50GC0JEFJ-E2"),
        ("SomeUnknownCo",      "XYZ-999"),
    ])
    def test_no_url_points_at_manufacturer_or_search_engine(self, mfr, part):
        urls = candidate_datasheet_urls(mfr, part)
        for url in urls:
            for host in BANNED_HOSTS:
                assert host not in url, (
                    f"{mfr}/{part}: URL must not contain banned host "
                    f"{host!r} — got {url!r}"
                )

    def test_empty_part_returns_empty_list(self):
        assert candidate_datasheet_urls("Analog Devices", "") == []

    def test_minicircuits_plus_suffix_is_url_encoded(self):
        """`ZX60-P103LN+` → `+` must be percent-encoded as `%2B` so the
        URL is well-formed at the distributor's edge (raw `+` is parsed
        as a space by both DigiKey and Mouser)."""
        urls = candidate_datasheet_urls("Mini-Circuits", "ZX60-P103LN+")
        assert len(urls) == 2
        for u in urls:
            # Encoded MPN ending appears in both URLs.
            assert "ZX60-P103LN%2B" in u, (
                f"trailing + must be percent-encoded as %2B in {u!r}"
            )
