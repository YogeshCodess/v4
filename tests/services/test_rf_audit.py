"""Tests for services/rf_audit.py — P0.2 / P1.5 / P1.6 glue.

Network HEAD probes are always stubbed. We only exercise the orchestration
logic + issue production.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from services.rf_audit import (
    run_all,
    run_banned_parts_audit,
    run_datasheet_audit,
    run_topology_audit,
)


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

def test_topology_audit_emits_issue_for_missing_mixer():
    mermaid = "flowchart TD\n ANT[Antenna] --> LNA[LNA]\n LNA --> ADC[ADC]"
    issues = run_topology_audit(mermaid, architecture="superhet_single")
    assert any(i.severity == "critical" and "mixer" in i.detail.lower() for i in issues)
    for i in issues:
        assert i.category == "topology"
        assert i.location == "block_diagram_mermaid"


def test_topology_audit_passes_clean_superhet():
    mermaid = (
        "flowchart TD\n"
        " ANT[Antenna] --> BPF[Preselector BPF]\n"
        " BPF --> LNA[LNA]\n"
        " LNA --> MIX[Mixer]\n"
        " LO[Synthesizer PLL] --> MIX\n"
        " MIX --> IF[IF Filter]\n"
    )
    issues = run_topology_audit(mermaid, architecture="superhet_single")
    assert issues == []


def test_topology_audit_empty_mermaid():
    issues = run_topology_audit("", architecture="superhet_single")
    assert len(issues) == 1
    assert issues[0].severity == "critical"


# ---------------------------------------------------------------------------
# Datasheet verification
# ---------------------------------------------------------------------------

def test_datasheet_audit_flags_unresolvable_url(monkeypatch):
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "")  # allow network
    with patch("services.rf_audit.verify_url", return_value=False), \
         patch("services.rf_audit.is_trusted_vendor_url", return_value=False):
        issues = run_datasheet_audit([
            {"part_number": "FAKE123", "datasheet_url": "https://bogus.example/fake.pdf"},
        ])
    assert len(issues) == 1
    assert issues[0].severity == "high"
    assert issues[0].category == "datasheet_url"
    assert "FAKE123" in issues[0].detail


def test_datasheet_audit_trusted_vendor_short_circuits(monkeypatch):
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "")
    with patch("services.rf_audit.verify_url", return_value=False), \
         patch("services.rf_audit.is_trusted_vendor_url", return_value=True):
        issues = run_datasheet_audit([
            {"part_number": "ADL8107", "datasheet_url": "https://www.analog.com/..."},
        ])
    # Trusted-vendor URL → no issue even though HEAD would have failed.
    assert issues == []


def test_datasheet_audit_missing_url_flagged_medium(monkeypatch):
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "")
    issues = run_datasheet_audit([
        {"part_number": "X1"},  # no datasheet_url field
    ])
    assert len(issues) == 1
    assert issues[0].severity == "medium"
    assert "no `datasheet_url`" in issues[0].detail


def test_datasheet_audit_network_disabled_still_accepts_trusted_urls(monkeypatch):
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
    with patch("services.rf_audit.verify_url") as mock_verify, \
         patch("services.rf_audit.is_trusted_vendor_url", return_value=True):
        issues = run_datasheet_audit([
            {"part_number": "X", "datasheet_url": "https://www.ti.com/foo"},
        ])
    mock_verify.assert_not_called()
    assert issues == []


def test_datasheet_audit_network_disabled_flags_untrusted(monkeypatch):
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
    with patch("services.rf_audit.verify_url") as mock_verify, \
         patch("services.rf_audit.is_trusted_vendor_url", return_value=False):
        issues = run_datasheet_audit([
            {"part_number": "X", "datasheet_url": "https://random.blog/x.pdf"},
        ])
    mock_verify.assert_not_called()
    assert len(issues) == 1
    assert "network disabled" in issues[0].detail


def test_datasheet_audit_skips_distributor_verified_urls(monkeypatch):
    """Perf guardrail: when `run_part_validation_audit` already enriched a
    component with a distributor-verified URL (the `_distributor_url_verified`
    marker), `run_datasheet_audit` MUST skip its own HEAD probe.

    Re-probing the same URL seconds after `tools.distributor_search.
    _verify_datasheet` already cleared it adds no safety — the URL is
    either still good (no value) or transiently flapping (false positive
    — would strip a real datasheet). On dense BOMs (12-15 components)
    this redundancy was the second-largest contributor to finalize_p1
    wall-clock after the distributor lookups themselves, ~30-60 s.
    """
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "")  # network allowed
    with patch("services.rf_audit.verify_url") as mock_verify, \
         patch("services.rf_audit.is_trusted_vendor_url", return_value=False):
        issues = run_datasheet_audit([
            {"part_number": "X",
             "datasheet_url": "https://untrusted.example/x.pdf",
             "_distributor_url_verified": True},
        ])
    # No HEAD probe issued, no issue raised — the URL is trusted by virtue
    # of the distributor marker.
    mock_verify.assert_not_called()
    assert issues == []


def test_datasheet_audit_still_probes_unverified_urls(monkeypatch):
    """Regression guard: components WITHOUT the `_distributor_url_verified`
    marker (e.g., the LLM's original URL passed through because the
    distributor returned no replacement) must still be HEAD-probed.

    Without this guard, a future refactor that accidentally treats every
    component as verified would let hallucinated URLs ship to the BOM.
    """
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "")  # network allowed
    with patch("services.rf_audit.verify_url", return_value=True) as mock_verify, \
         patch("services.rf_audit.is_trusted_vendor_url", return_value=False):
        issues = run_datasheet_audit([
            {"part_number": "X", "datasheet_url": "https://untrusted.example/x.pdf"},
        ])
    mock_verify.assert_called_once()
    assert issues == []


# ---------------------------------------------------------------------------
# Banned parts
# ---------------------------------------------------------------------------

def test_banned_parts_audit_returns_cleaned_list_and_issues():
    bom = [
        {"part_number": "HMC8410", "manufacturer": "ADI"},
        {"part_number": "HMC-C024", "manufacturer": "ADI"},
    ]
    cleaned, issues = run_banned_parts_audit(bom)
    assert [c["part_number"] for c in cleaned] == ["HMC8410"]
    assert len(issues) == 1
    assert issues[0].category == "banned_part"


# ---------------------------------------------------------------------------
# run_all orchestrator
# ---------------------------------------------------------------------------

def test_run_all_runs_every_check_and_mutates_bom(monkeypatch):
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
    tool_input = {
        "block_diagram_mermaid": (
            "flowchart TD\n"
            " ANT[Antenna] --> BPF[Preselector BPF]\n"
            " BPF --> LNA[LNA]\n"
            " LNA --> MIX[Mixer]\n"
            " LO[LO] --> MIX\n"
            " MIX --> IF[IF Filter]\n"
        ),
        "component_recommendations": [
            # active part, trusted-vendor URL → pass
            {"part_number": "HMC8410",
             "manufacturer": "Analog Devices",
             "datasheet_url": "https://www.analog.com/en/products/hmc8410.html"},
            # banned — must be stripped
            {"part_number": "HMC-C024",
             "manufacturer": "Analog Devices",
             "datasheet_url": "https://www.analog.com/en/products/hmc-c024.html"},
        ],
    }
    with patch("services.rf_audit.is_trusted_vendor_url", return_value=True):
        new_input, issues = run_all(tool_input, architecture="superhet_single")

    # Banned part removed from the BOM
    parts = [c["part_number"] for c in new_input["component_recommendations"]]
    assert parts == ["HMC8410"]

    # One banned_part issue surfaced
    banned = [i for i in issues if i.category == "banned_part"]
    assert len(banned) == 1


def test_run_all_empty_bom_does_not_raise(monkeypatch):
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
    new_input, issues = run_all(
        {"block_diagram_mermaid": ""},
        architecture=None,
    )
    # Empty mermaid still yields the "no nodes" critical topology issue
    assert any(i.category == "topology" for i in issues)


# ---------------------------------------------------------------------------
# Live part validation (DigiKey → Mouser → seed)
# ---------------------------------------------------------------------------

def test_part_validation_flags_hallucinated_mpn(monkeypatch):
    """When DigiKey / Mouser are configured and every tier misses, the
    LLM-invented MPN surfaces as a critical hallucinated_part issue."""
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
    from services.rf_audit import run_part_validation_audit
    from unittest.mock import patch
    with patch("services.rf_audit._distributor_lookup", return_value=None):
        enriched, issues = run_part_validation_audit([
            {"part_number": "HALLUCINATED-XYZ",
             "manufacturer": "Made Up Corp",
             "datasheet_url": "https://fake.example/nope.pdf"},
        ])
    assert any(i.category == "hallucinated_part" for i in issues)
    assert any("HALLUCINATED-XYZ" in i.detail for i in issues)
    assert [c["part_number"] for c in enriched] == ["HALLUCINATED-XYZ"]


def test_part_validation_enriches_when_found(monkeypatch):
    """A DigiKey hit should overwrite the LLM's manufacturer/datasheet
    fields with the authoritative distributor values."""
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
    from services.rf_audit import run_part_validation_audit
    from tools.digikey_api import PartInfo
    from unittest.mock import patch
    real = PartInfo(
        part_number="ADL8107", manufacturer="Analog Devices Inc.",
        description="Wideband LNA 2-18 GHz",
        datasheet_url="https://www.analog.com/en/products/adl8107.html",
        product_url="https://www.digikey.com/...",
        lifecycle_status="active", unit_price_usd=24.0,
        stock_quantity=180, source="digikey",
    )
    with patch("services.rf_audit._distributor_lookup", return_value=real):
        enriched, issues = run_part_validation_audit([
            # LLM guessed the manufacturer + datasheet wrong
            {"part_number": "ADL8107", "manufacturer": "ADI Wrong Name",
             "datasheet_url": "https://llm-invented.example/adl8107.pdf"},
        ])
    assert issues == []
    c = enriched[0]
    assert c["manufacturer"] == "Analog Devices Inc."  # overwritten
    assert c["datasheet_url"] == "https://www.analog.com/en/products/adl8107.html"
    assert c["lifecycle_status"] == "active"
    assert c["distributor_source"] == "digikey"
    assert c["product_url"] == "https://www.digikey.com/..."
    assert c["digikey_url"] == "https://www.digikey.com/..."
    assert c["unit_price_usd"] == 24.0
    assert c["stock_quantity"] == 180


def test_part_validation_enriches_primary_shape_with_mouser_fields(monkeypatch):
    monkeypatch.setenv("MOUSER_API_KEY", "z")
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
    from services.rf_audit import run_part_validation_audit
    from tools.digikey_api import PartInfo
    from unittest.mock import patch

    real = PartInfo(
        part_number="STM32F407VGT6",
        manufacturer="STMicroelectronics",
        description="ARM Cortex-M4 MCU",
        datasheet_url=None,
        product_url="https://www.mouser.in/ProductDetail/STMicroelectronics/STM32F407VGT6",
        lifecycle_status="active",
        unit_price_usd=None,
        stock_quantity=0,
        source="mouser",
        unit_price=1106.05,
        unit_price_currency="INR",
        region="IN",
    )
    with patch("services.rf_audit._distributor_lookup", return_value=real):
        enriched, issues = run_part_validation_audit([{
            "function": "MCU control",
            "primary_part": "STM32F407VGT6",
            "primary_manufacturer": "Wrong",
            "primary_description": "Old text",
        }])

    assert issues == []
    c = enriched[0]
    assert c["primary_manufacturer"] == "STMicroelectronics"
    assert c["primary_description"] == "ARM Cortex-M4 MCU"
    assert c["manufacturer"] == "STMicroelectronics"
    assert c["description"] == "ARM Cortex-M4 MCU"
    assert c["mouser_url"].startswith("https://www.mouser.in/")
    assert c["product_url"] == c["mouser_url"]
    assert c["distributor_source"] == "mouser"
    assert c["unit_price"] == 1106.05
    assert c["unit_price_currency"] == "INR"
    assert c["stock_region"] == "IN"


def test_part_validation_flags_nrnd_parts(monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
    from services.rf_audit import run_part_validation_audit
    from tools.digikey_api import PartInfo
    from unittest.mock import patch
    nrnd = PartInfo(
        part_number="OLD-PART", manufacturer="Vendor", description="",
        datasheet_url=None, product_url=None,
        lifecycle_status="nrnd", unit_price_usd=None,
        stock_quantity=None, source="digikey",
    )
    with patch("services.rf_audit._distributor_lookup", return_value=nrnd):
        _, issues = run_part_validation_audit([{"part_number": "OLD-PART"}])
    assert any(i.category == "nrnd_part" and i.severity == "high" for i in issues)


def test_part_validation_flags_obsolete_parts(monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
    from services.rf_audit import run_part_validation_audit
    from tools.digikey_api import PartInfo
    from unittest.mock import patch
    obs = PartInfo(
        part_number="DEAD", manufacturer="V", description="",
        datasheet_url=None, product_url=None,
        lifecycle_status="obsolete", unit_price_usd=None,
        stock_quantity=None, source="digikey",
    )
    with patch("services.rf_audit._distributor_lookup", return_value=obs):
        _, issues = run_part_validation_audit([{"part_number": "DEAD"}])
    assert any(i.category == "obsolete_part" and i.severity == "critical" for i in issues)


def test_part_validation_skips_when_no_distributor_configured(monkeypatch):
    """Air-gap: no keys set and seed miss → no issues raised (we can't
    distinguish hallucination from "no oracle")."""
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
    from services.rf_audit import run_part_validation_audit
    from unittest.mock import patch
    with patch("services.rf_audit._distributor_lookup", return_value=None):
        _, issues = run_part_validation_audit([{"part_number": "Invented-9"}])
    # Without live configuration we refuse to accuse the LLM of fabrication.
    assert not any(i.category == "hallucinated_part" for i in issues)


def test_part_validation_marks_distributor_verified_url(monkeypatch):
    """Contract: when the distributor cascade returns a `datasheet_url`,
    the enriched component MUST be tagged `_distributor_url_verified=True`.

    This is the upstream half of the audit-redundancy-elimination fix.
    Without this marker, `run_datasheet_audit` will re-probe URLs that
    `tools.distributor_search._verify_datasheet` already cleared seconds
    earlier — wasting 30-60 s on dense BOMs.
    """
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
    from services.rf_audit import run_part_validation_audit
    from tools.digikey_api import PartInfo
    from unittest.mock import patch
    real = PartInfo(
        part_number="ADL8107", manufacturer="Analog Devices Inc.",
        description="Wideband LNA 2-18 GHz",
        datasheet_url="https://www.analog.com/en/products/adl8107.html",
        product_url=None, lifecycle_status="active",
        unit_price_usd=None, stock_quantity=None, source="digikey",
    )
    with patch("services.rf_audit._distributor_lookup", return_value=real):
        enriched, _ = run_part_validation_audit([
            {"part_number": "ADL8107",
             "datasheet_url": "https://llm-invented.example/adl8107.pdf"},
        ])
    assert enriched[0]["_distributor_url_verified"] is True
    assert enriched[0]["datasheet_url"] == \
        "https://www.analog.com/en/products/adl8107.html"


def test_part_validation_does_not_mark_when_distributor_returns_no_url(monkeypatch):
    """Negative contract: when the distributor record carries no
    `datasheet_url` (a real case for older parts where the distributor
    record exists but the PDF link is missing), the LLM's original URL
    passes through UNMARKED so `run_datasheet_audit` still probes it.

    Marking it would let an unverified LLM URL skip the safety net.
    """
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
    from services.rf_audit import run_part_validation_audit
    from tools.digikey_api import PartInfo
    from unittest.mock import patch
    no_url = PartInfo(
        part_number="OLD-PART", manufacturer="Vendor",
        description="", datasheet_url=None,  # ← distributor has no URL
        product_url=None, lifecycle_status="active",
        unit_price_usd=None, stock_quantity=None, source="digikey",
    )
    with patch("services.rf_audit._distributor_lookup", return_value=no_url):
        enriched, _ = run_part_validation_audit([
            {"part_number": "OLD-PART",
             "datasheet_url": "https://llm-guess.example/old.pdf"},
        ])
    # LLM URL preserved (distributor didn't override) and NOT marked verified
    # — the datasheet audit must still probe it.
    assert enriched[0]["datasheet_url"] == "https://llm-guess.example/old.pdf"
    assert "_distributor_url_verified" not in enriched[0]


def test_run_all_integrates_part_validation(monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
    from unittest.mock import patch
    from services.rf_audit import run_all
    from tools.digikey_api import PartInfo
    tool_input = {
        "block_diagram_mermaid": (
            "flowchart TD\n"
            " ANT[Antenna] --> BPF[Preselector BPF]\n"
            " BPF --> LNA[LNA]\n"
            " LNA --> MIX[Mixer]\n"
            " LO[LO] --> MIX\n"
            " MIX --> IF[IF Filter]\n"
        ),
        "component_recommendations": [
            {"part_number": "ADL8107", "manufacturer": "Wrong",
             "datasheet_url": "https://wrong.example/"},
            {"part_number": "INVENTED-7777", "manufacturer": "LLM Fictions",
             "datasheet_url": "https://invented.example/"},
        ],
    }
    real = PartInfo(
        part_number="ADL8107", manufacturer="Analog Devices",
        description="", datasheet_url="https://www.analog.com/en/products/adl8107.html",
        product_url=None, lifecycle_status="active",
        unit_price_usd=None, stock_quantity=None, source="digikey",
    )
    def _fake(pn, **_k):
        return real if pn == "ADL8107" else None
    with patch("services.rf_audit._distributor_lookup", side_effect=_fake):
        new_input, issues = run_all(tool_input, architecture="superhet_single")

    # ADL8107 kept + enriched; INVENTED-7777 kept but flagged hallucinated.
    parts = {c["part_number"]: c for c in new_input["component_recommendations"]}
    assert parts["ADL8107"]["manufacturer"] == "Analog Devices"
    assert any(
        i.category == "hallucinated_part" and "INVENTED-7777" in i.detail
        for i in issues
    )


def test_run_all_handles_bom_key_alias(monkeypatch):
    """Accept both `component_recommendations` and `bom` as the key."""
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
    tool_input = {
        "block_diagram_mermaid": "flowchart TD\n LNA[LNA] --> OUT[Output]",
        "bom": [{"part_number": "HMC-C024", "manufacturer": "ADI"}],
    }
    new_input, issues = run_all(tool_input, architecture="recommend")
    assert new_input["bom"] == []
    assert any(i.category == "banned_part" for i in issues)


# ---------------------------------------------------------------------------
# Candidate-pool audit — gate for retrieval-augmented selection
# ---------------------------------------------------------------------------

def test_candidate_pool_audit_flags_mpns_outside_pool(monkeypatch):
    """When the LLM is supposed to pick from find_candidate_parts but
    emits an MPN that was never surfaced, `not_from_candidate_pool`
    must fire."""
    from services.rf_audit import run_candidate_pool_audit
    bom = [
        {"part_number": "ADL8107",  "manufacturer": "Analog Devices"},  # in pool
        {"part_number": "ROGUE-99", "manufacturer": "Nobody"},          # NOT in pool
    ]
    offered = {"ADL8107", "PMA3-83LN+", "HMC8410"}
    issues = run_candidate_pool_audit(bom, offered)
    categories = [i.category for i in issues]
    assert categories == ["not_from_candidate_pool"]
    assert "ROGUE-99" in issues[0].detail
    assert issues[0].severity == "high"


def test_candidate_pool_audit_is_case_insensitive():
    """MPN casing from the LLM may differ from the distributor's canonical
    case. Treat them equal — an `adl8107` pick against an `ADL8107` offer
    is a hit, not a miss."""
    from services.rf_audit import run_candidate_pool_audit
    bom = [{"part_number": "adl8107"}]
    offered = {"ADL8107"}
    assert run_candidate_pool_audit(bom, offered) == []


def test_candidate_pool_audit_silent_when_no_offers(monkeypatch):
    """Backward-compat: runs that never used the retrieval tool (legacy
    conversations, air-gap mode) must NOT be penalised — skip silently
    when `offered_mpns` is None or empty."""
    from services.rf_audit import run_candidate_pool_audit
    bom = [{"part_number": "ANY-PART"}]
    assert run_candidate_pool_audit(bom, None) == []
    assert run_candidate_pool_audit(bom, set()) == []


def test_run_all_threads_offered_mpns_through(monkeypatch):
    """The new `offered_candidate_mpns` kwarg on run_all must propagate
    into the candidate-pool audit — end-to-end regression."""
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
    tool_input = {
        "block_diagram_mermaid": "flowchart TD\n LNA[LNA] --> OUT[Output]",
        "component_recommendations": [
            {"part_number": "FROM-POOL",    "manufacturer": "X"},
            {"part_number": "OUTSIDE-POOL", "manufacturer": "Y"},
        ],
    }
    # Stub part validation so it doesn't hit the network.
    with patch("services.rf_audit.run_part_validation_audit",
               return_value=(tool_input["component_recommendations"], [])):
        _, issues = run_all(
            tool_input,
            architecture="recommend",
            offered_candidate_mpns={"FROM-POOL"},
        )
    pool_issues = [i for i in issues if i.category == "not_from_candidate_pool"]
    assert len(pool_issues) == 1
    assert "OUTSIDE-POOL" in pool_issues[0].detail


def test_run_all_without_offered_mpns_does_not_emit_pool_issues(monkeypatch):
    """No pool issues when the caller doesn't thread the set in (default behaviour)."""
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
    tool_input = {
        "block_diagram_mermaid": "flowchart TD\n LNA[LNA] --> OUT[Output]",
        "component_recommendations": [{"part_number": "ANY", "manufacturer": "X"}],
    }
    with patch("services.rf_audit.run_part_validation_audit",
               return_value=(tool_input["component_recommendations"], [])):
        _, issues = run_all(tool_input, architecture="recommend")
    assert not any(i.category == "not_from_candidate_pool" for i in issues)
