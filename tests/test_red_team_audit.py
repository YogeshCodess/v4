"""End-to-end smoke test for agents/red_team_audit.py.

Covers the three failure modes the judges will ask us to demonstrate live:
  1. Agent inflates a cascade claim -> audit flags cascade_mismatch.
  2. Agent cites a standard clause that does not exist -> audit flags
     unresolved_citation.
  3. Agent fabricates a part number with no datasheet URL -> audit flags
     hallucinated_part.

The happy path passes cleanly with a real BOM and real citations.
"""
from __future__ import annotations

from agents.red_team_audit import audit, check_cosite_imd, extract_numeric_claims_from_text


KNOWN_PARTS = {"TGA2578", "ADL8107", "AD9625", "XCZU47DR"}


def _good_bom() -> list[dict]:
    """A reasonable X-band receiver front-end."""
    return [
        {"name": "LNA", "gain_db": 24.0, "nf_db": 1.8, "iip3_dbm": 30.0,
         "p1db_dbm": 18.0, "kind": "LNA"},
        {"name": "Filter", "gain_db": -2.0, "nf_db": 2.0, "kind": "filter"},
        {"name": "Mixer", "gain_db": -7.0, "nf_db": 7.0, "iip3_dbm": 10.0,
         "p1db_dbm": 5.0, "kind": "mixer"},
        {"name": "IF_Amp", "gain_db": 25.0, "nf_db": 3.0, "iip3_dbm": 20.0,
         "p1db_dbm": 10.0, "kind": "amp"},
    ]


def _good_parts() -> list[dict]:
    return [
        {"part_number": "TGA2578", "datasheet_url": "https://www.qorvo.com/products/p/TGA2578"},
        {"part_number": "AD9625", "datasheet_url": "https://www.analog.com/en/products/ad9625.html"},
        {"part_number": "XCZU47DR", "datasheet_url": "https://www.xilinx.com/..."},
    ]


def test_happy_path_passes():
    rep = audit(
        phase_id="P1",
        bom_stages=_good_bom(),
        claimed_cascade={
            # Accurate within 1 dB of the validator's output.
            "noise_figure_db": 2.3,
            "total_gain_db": 40.0,
        },
        citations=[("MIL-STD-461G", "RE102"), ("MIL-STD-810H", "Method 501.7")],
        claimed_parts=_good_parts(),
        known_parts=KNOWN_PARTS,
        bandwidth_hz=1_000_000,
        snr_required_db=10.0,
    )
    assert rep.overall_pass is True
    assert rep.hallucination_count == 0
    assert rep.unresolved_citations == 0
    assert rep.cascade_errors == 0
    assert rep.confidence_score > 0.7


def test_flags_cascade_claim_inflation():
    rep = audit(
        phase_id="P1",
        bom_stages=_good_bom(),
        claimed_cascade={
            # Agent claims 0.3 dB NF when the real cascade computes ~2.0 dB.
            # Delta ~1.7 dB exceeds the default 1.0 dB tolerance.
            "noise_figure_db": 0.3,
        },
        citations=[],
        claimed_parts=_good_parts(),
        known_parts=KNOWN_PARTS,
    )
    assert rep.overall_pass is False
    assert rep.cascade_errors >= 1
    assert any(i.category == "cascade_mismatch" for i in rep.issues)


def test_cascade_audit_tolerates_non_numeric_claims():
    """Regression for `p1_finalize.audit_failed: float() argument must be a
    string or a real number, not 'NoneType'`.  The LLM occasionally emits
    dicts / lists / "N/A" strings under one of the cascade claim keys;
    that single bad claim must NOT crash the entire audit — the bad key
    is skipped and the rest of the checks still run."""
    rep = audit(
        phase_id="P1",
        bom_stages=_good_bom(),
        claimed_cascade={
            # Each of these would historically crash with TypeError when
            # float() was called unconditionally.
            "noise_figure_db":  {"value": 2.0, "units": "dB"},  # nested dict
            "total_gain_db":    ["list", "not", "number"],      # list
            "iip3_dbm_input":   "N/A",                          # unparsable string
            "sensitivity_dbm":  None,                           # explicit None (already guarded)
        },
        citations=[],
        claimed_parts=_good_parts(),
        known_parts=KNOWN_PARTS,
    )
    # Audit completes without raising — this is the core regression guarantee.
    assert rep is not None
    # No cascade_mismatch issues should be emitted since every claim was
    # unparseable and therefore skipped.
    assert not any(i.category == "cascade_mismatch" for i in rep.issues)


def test_flags_fabricated_citation():
    rep = audit(
        phase_id="P1",
        bom_stages=_good_bom(),
        claimed_cascade={},
        citations=[("MIL-STD-461G", "RE999")],  # no such clause
        claimed_parts=_good_parts(),
        known_parts=KNOWN_PARTS,
    )
    assert rep.overall_pass is False
    assert rep.unresolved_citations == 1
    assert any(i.category == "unresolved_citation" for i in rep.issues)


def test_flags_hallucinated_part():
    rep = audit(
        phase_id="P1",
        bom_stages=_good_bom(),
        claimed_cascade={},
        citations=[],
        claimed_parts=[
            {"part_number": "TGA2578", "datasheet_url": "https://..."},
            {"part_number": "FAKE-XYZ-9000"},  # no URL, not in DB
        ],
        known_parts=KNOWN_PARTS,
    )
    assert rep.overall_pass is False
    assert rep.hallucination_count == 1
    assert any(i.category == "hallucinated_part" for i in rep.issues)


def test_confidence_score_decreases_with_issues():
    clean = audit(
        phase_id="P1",
        bom_stages=_good_bom(),
        claimed_cascade={},
        citations=[],
        claimed_parts=_good_parts(),
        known_parts=KNOWN_PARTS,
    )
    dirty = audit(
        phase_id="P1",
        bom_stages=_good_bom(),
        claimed_cascade={"noise_figure_db": 0.5},  # cascade mismatch
        citations=[("FAKE-STD", "X1")],              # unresolved citation
        claimed_parts=[{"part_number": "FAKE-1"}], # hallucinated part
        known_parts=KNOWN_PARTS,
    )
    assert dirty.confidence_score < clean.confidence_score
    assert dirty.overall_pass is False


def test_cosite_imd_detects_inband_third_order_product():
    # Receiver band 225-400 MHz (UHF tactical). Two co-sited emitters at
    # 150 MHz and 75 MHz produce 2*150 - 75 = 225 MHz which lands on the band edge.
    issues = check_cosite_imd(
        freq_range_mhz=(225.0, 400.0),
        cosite_emitters_mhz=[150.0, 75.0],
    )
    assert len(issues) >= 1
    assert all(i.category == "cosite_imd" for i in issues)
    assert any("225" in i.detail for i in issues)


def test_cosite_imd_flags_power_estimate_when_context_complete():
    # IIP3 context included: expect power estimate string to appear.
    issues = check_cosite_imd(
        freq_range_mhz=(225.0, 400.0),
        cosite_emitters_mhz=[150.0, 75.0],
        receiver_iip3_dbm=-5.0,
        antenna_isolation_db=30.0,
        emitter_power_dbm=30.0,
    )
    assert issues, "expected at least one IMD3 issue"
    # P_tone_in = 30 - 30 = 0 dBm; P_IMD3_in = 3*0 - 2*(-5) = +10 dBm.
    # 10 > -90 threshold -> critical.
    assert any(i.severity == "critical" for i in issues)
    assert any("IMD3" in i.detail for i in issues)


def test_cosite_imd_silent_when_products_outofband():
    # Two close emitters at 2400 and 2410 MHz. Products fall at 2390 and 2420 MHz.
    # Receiver band 5000-6000 MHz: no overlap.
    issues = check_cosite_imd(
        freq_range_mhz=(5000.0, 6000.0),
        cosite_emitters_mhz=[2400.0, 2410.0],
    )
    assert issues == []


def test_cosite_imd_handles_insufficient_emitters():
    assert check_cosite_imd(freq_range_mhz=(100.0, 200.0), cosite_emitters_mhz=[150.0]) == []
    assert check_cosite_imd(freq_range_mhz=(100.0, 200.0), cosite_emitters_mhz=[]) == []


def test_audit_consumes_cosite_context():
    rep = audit(
        phase_id="P1",
        bom_stages=_good_bom(),
        claimed_cascade={},
        citations=[],
        claimed_parts=_good_parts(),
        known_parts=KNOWN_PARTS,
        cosite_context={
            "freq_range_mhz": (225.0, 400.0),
            "cosite_emitters_mhz": [150.0, 75.0],
            "receiver_iip3_dbm": -5.0,
            "antenna_isolation_db": 30.0,
        },
    )
    # The cosite IMD3 product is critical; overall_pass must be False.
    assert rep.overall_pass is False
    assert any(i.category == "cosite_imd" for i in rep.issues)


def test_extract_numeric_claims_from_prose():
    text = "The cascade achieves NF = 2.3 dB with sensitivity = -95 dBm."
    claims = extract_numeric_claims_from_text(text)
    assert len(claims) >= 2
    metrics = [c["metric"] for c in claims]
    assert "noise_figure_db" in metrics
    assert "sensitivity" in metrics
