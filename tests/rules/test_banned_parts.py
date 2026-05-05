"""Tests for rules/banned_parts.py — P1.5."""
from __future__ import annotations

import pytest

from rules.banned_parts import (
    BANNED_MANUFACTURERS,
    BANNED_PART_PATTERNS,
    Rejection,
    classify_component,
    filter_components,
    is_banned_manufacturer,
    is_banned_part_number,
)


# ---------------------------------------------------------------------------
# is_banned_manufacturer
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["VPT", "VPT Inc.", "VPT, Inc.", "vpt inc"])
def test_banned_manufacturer_hit(name):
    assert is_banned_manufacturer(name) is not None


def test_banned_manufacturer_strips_punctuation_and_case():
    # Exotic casings / typos still get caught.
    assert is_banned_manufacturer("Vpt_Inc") is not None
    assert is_banned_manufacturer("VPT INC.") is not None


def test_non_banned_manufacturer_passes():
    assert is_banned_manufacturer("Analog Devices") is None
    assert is_banned_manufacturer("Qorvo") is None


def test_banned_manufacturer_none_input():
    assert is_banned_manufacturer(None) is None
    assert is_banned_manufacturer("") is None


# ---------------------------------------------------------------------------
# is_banned_part_number
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pn", [
    "HMC-C024", "HMCC024",
    "HMC1040", "HMC-1040LP5E", "hmc1040lp5e",
    "HMC1049LP5CE", "hmc-1049lp5ce",
    "HMC753", "HMC-753",
    "HMC-C017", "HMCC017",
])
def test_banned_part_numbers_matched(pn):
    assert is_banned_part_number(pn) is not None


@pytest.mark.parametrize("pn", [
    "HMC1049LP5E",   # active successor — NOT banned
    "ADL8107",
    "STM32F407",
    "",
])
def test_non_banned_part_passes(pn):
    assert is_banned_part_number(pn) is None


# ---------------------------------------------------------------------------
# classify_component
# ---------------------------------------------------------------------------

def test_classify_flat_bom_shape():
    rej = classify_component({
        "part_number": "HMC-C024",
        "manufacturer": "Analog Devices",
    })
    assert isinstance(rej, Rejection)
    assert "HMC-C024" in rej.part_number.upper() or "HMC" in rej.part_number


def test_classify_rich_recommendation_shape():
    rej = classify_component({
        "primary_part": "HMC1040LP5E",
        "primary_manufacturer": "Analog Devices",
    })
    assert rej is not None
    assert "NRND" in rej.reason or "1040" in rej.reason


def test_classify_passes_active_component():
    assert classify_component({
        "part_number": "HMC1049LP5E",
        "manufacturer": "Analog Devices",
    }) is None


def test_classify_catches_vpt_on_safe_part_number():
    rej = classify_component({
        "part_number": "SMHF2805D",
        "manufacturer": "VPT Inc.",
    })
    assert rej is not None
    assert "ban list" in rej.reason


# ---------------------------------------------------------------------------
# filter_components
# ---------------------------------------------------------------------------

def test_filter_splits_kept_and_rejected():
    bom = [
        {"part_number": "HMC8410", "manufacturer": "Analog Devices"},     # ok
        {"part_number": "HMC-C024", "manufacturer": "Analog Devices"},     # banned
        {"part_number": "STM32F407", "manufacturer": "ST"},                # ok
        {"part_number": "SMHF2805D", "manufacturer": "VPT Inc."},          # banned (mfr)
    ]
    kept, rejected = filter_components(bom)
    assert [c["part_number"] for c in kept] == ["HMC8410", "STM32F407"]
    assert len(rejected) == 2
    reasons = " ".join(r.reason for r in rejected)
    assert "NRND" in reasons or "EOL" in reasons
    assert "ban list" in reasons


def test_filter_empty_input():
    assert filter_components([]) == ([], [])
    assert filter_components(None) == ([], [])


def test_rejection_renders_as_audit_issue_dict():
    rej = Rejection(
        part_number="HMC-C024", manufacturer="ADI",
        reason="EOL (test)",
    )
    issue = rej.to_issue_dict()
    assert issue["severity"] == "critical"
    assert issue["category"] == "banned_part"
    assert "HMC-C024" in issue["detail"]
    # location is used by the audit renderer
    assert "component_recommendations" in issue["location"]


# ---------------------------------------------------------------------------
# Data shape invariants
# ---------------------------------------------------------------------------

def test_banned_manufacturers_non_empty_and_lowercase():
    assert BANNED_MANUFACTURERS
    for name in BANNED_MANUFACTURERS:
        assert name == name.lower(), f"Must be lowercased: {name}"


def test_banned_patterns_are_compiled_regexes_with_reasons():
    assert BANNED_PART_PATTERNS
    for pattern, reason in BANNED_PART_PATTERNS:
        assert hasattr(pattern, "match")
        assert isinstance(reason, str) and reason
