"""Regression tests for the MPN-shape pre-emit gate in RequirementsAgent.

The gate exists to catch the most embarrassing class of BOM hallucination:
the LLM stuffing a description like ``"Discrete thin-film 50 Ohm pad"``
into the ``part_number`` field. The audit catches it later as
``hallucinated_part``, but by then the BOM is captured and the user has
to scroll past it in the red-team report. The shape gate rejects it
inside the tool handler so the LLM gets an immediate corrective and
must use a real MPN format (or omit the row entirely).
"""
from __future__ import annotations

import pytest

from agents.requirements_agent import RequirementsAgent


# ---------------------------------------------------------------------------
# _looks_like_mpn — pure shape check
# ---------------------------------------------------------------------------

class TestLooksLikeMPN:

    @pytest.mark.parametrize("mpn", [
        "ADL8107",                  # Analog Devices LNA
        "HMC624LP4E",               # Hittite VGA
        "ZX85-12-8SA-S+",           # Mini-Circuits bias-tee with trailing +
        "CL05B104KP5NNNC",          # Samsung MLCC
        "GRM188R71C104KA01D",       # Murata MLCC
        "LMK04832",                 # TI clock jitter cleaner
        "MAX2870",                  # Maxim PLL
        "XCKU040-2FFVA1156I",       # Xilinx Kintex UltraScale (with /)
        "MADL-011017",              # Real-looking even if hallucinated
        "TQFN-32",                  # Package descriptor (uppercase + dash)
        "LFCN-1450+",               # Mini-Circuits LPF
        "TCW2-133+",                # Mini-Circuits balun
    ])
    def test_real_mpns_pass(self, mpn):
        assert RequirementsAgent._looks_like_mpn(mpn) is True, (
            f"{mpn!r} should be accepted as MPN-shaped"
        )

    @pytest.mark.parametrize("bad", [
        "Discrete thin-film 50 Ohm pad",   # the user-screenshot case
        "low noise amplifier",              # role description
        "PCB Trace 50 Ohm Microstrip",      # description
        "thin film attenuator",             # all-lowercase, no digits
        "",                                 # empty
        " ",                                # whitespace only
        "X",                                # too short (1 char)
        "XY",                               # too short (2 chars)
        "A" * 41,                           # too long (>40)
        "ADL 8107",                         # contains space
        "ADL\t8107",                        # contains tab
        "ADL\n8107",                        # contains newline
        "(LNA)",                            # leading punctuation
        "-prefixed",                        # leading dash
    ])
    def test_descriptions_and_garbage_rejected(self, bad):
        assert RequirementsAgent._looks_like_mpn(bad) is False, (
            f"{bad!r} should be rejected — looks like a description or junk"
        )

    def test_empty_and_none_safe(self):
        assert RequirementsAgent._looks_like_mpn("") is False
        assert RequirementsAgent._looks_like_mpn(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Boundary cases the regex must not get wrong
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_three_char_alphanumeric_passes(self):
        # Exactly at the lower bound — `R10` is a passive value, but it
        # could plausibly be a short MPN (e.g. `Q1`-style transistor refs
        # are too short, but `R10` and `D11` both fit).
        assert RequirementsAgent._looks_like_mpn("R10") is True
        assert RequirementsAgent._looks_like_mpn("D11") is True

    def test_forty_char_long_mpn_passes(self):
        long_mpn = "X" * 39 + "1"   # 40 chars, has digit
        assert RequirementsAgent._looks_like_mpn(long_mpn) is True

    def test_uppercase_no_digit_passes(self):
        # Some package codes / families lack digits but are clearly
        # identifier-shaped: all-uppercase ASCII.
        assert RequirementsAgent._looks_like_mpn("QFN") is True
        assert RequirementsAgent._looks_like_mpn("BGA") is True

    def test_lowercase_no_digit_rejected(self):
        # The killer case: lowercase word with no digits is almost
        # certainly a description, not a part.
        assert RequirementsAgent._looks_like_mpn("attenuator") is False
        assert RequirementsAgent._looks_like_mpn("amplifier") is False
