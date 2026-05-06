"""Unit tests for services.manifest_validator — the cross-file MPN leak gate."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.design_manifest import DesignManifest, freeze
from services.manifest_validator import (
    Leak,
    ManifestLeakError,
    check_no_mpn_leak,
    extract_mpns,
    leaks_to_audit_issues,
)


# ---------------------------------------------------------------------------
# Token classification
# ---------------------------------------------------------------------------

class TestExtractMpns:
    def test_finds_simple_mpns(self):
        text = "Use HMC8410 and LT5560 for the front end."
        assert extract_mpns(text) == {"HMC8410", "LT5560"}

    def test_finds_dashed_mpns(self):
        text = "AD9082-FFV is a JESD204B converter."
        assert "AD9082-FFV" in extract_mpns(text)

    def test_excludes_standards(self):
        """Standards like IEEE 29148 must NOT be flagged as MPNs."""
        text = "Per IEEE-29148:2018 and ISO26262 the system shall ..."
        assert extract_mpns(text) == set()

    def test_excludes_requirement_ids(self):
        text = "REQ-HW-001 mandates that the LNA ... per FR0042."
        # REQ-HW-001 condenses to REQHW001, FR0042 -> FR0042 (FR is an excluded prefix)
        # If the matcher catches them, they should be filtered.
        result = extract_mpns(text)
        assert "REQHW001" not in result
        assert "FR0042" not in result

    def test_excludes_milstd(self):
        text = "Hardened to MIL-STD-461F and MIL-STD-810G."
        # Should produce no MPNs — these are standards.
        assert extract_mpns(text) == set()

    def test_excludes_protocol_versions(self):
        text = "Supports USB3, PCIE4, DDR4 memory interfaces."
        result = extract_mpns(text)
        # USB3, PCIE4, DDR4 are protocol versions, not MPNs.
        assert "USB3" not in result
        assert "PCIE4" not in result
        assert "DDR4" not in result

    def test_finds_real_mixed_with_excluded(self):
        text = (
            "Per IEEE-29148, the LNA HMC8410 (Analog Devices) must meet "
            "MIL-STD-810G environmental and FCC Part 15 emissions."
        )
        result = extract_mpns(text)
        assert "HMC8410" in result
        # Standards remain excluded
        assert all(s not in result for s in ("IEEE29148", "MILSTD810G", "FCC15"))


# ---------------------------------------------------------------------------
# Leak detection (the bug class)
# ---------------------------------------------------------------------------

@pytest.fixture
def manifest_with_two_parts() -> DesignManifest:
    return freeze(DesignManifest(
        project_id="42",
        requirements={"x": 1},
        architecture="superhet",
        bom=[
            {"part_number": "HMC8410", "manufacturer": "ADI", "role": "lna"},
            {"part_number": "LT5560", "manufacturer": "Linear", "role": "mixer"},
        ],
    ))


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "p1_output"


def _write(out: Path, name: str, body: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(body, encoding="utf-8")


class TestLeakDetection:
    def test_clean_outputs_no_leaks(self, manifest_with_two_parts, output_dir):
        _write(output_dir, "requirements.md",
               "## Front end\n\n- HMC8410: 18 dB gain LNA\n- LT5560: mixer\n")
        _write(output_dir, "gain_loss_budget.md",
               "| HMC8410 | 18 dB |\n| LT5560 | -8 dB |\n")
        leaks = check_no_mpn_leak(output_dir, manifest_with_two_parts)
        assert leaks == []

    def test_detects_hmc_bug_pattern(self, manifest_with_two_parts, output_dir):
        """The exact hackathon-final bug: HMC1234 in requirements.md but
        not in component_recommendations.md (which the manifest mirrors)."""
        _write(output_dir, "requirements.md",
               "## Front end\n\n- HMC8410: real LNA\n- HMC1234: AUDITED-OUT obsolete part\n")
        _write(output_dir, "component_recommendations.md",
               "| HMC8410 | ADI | LNA |\n| LT5560 | Linear | mixer |\n")
        _write(output_dir, "gain_loss_budget.md",
               "| HMC8410 | 18 dB |\n| HMC1234 | 12 dB |\n| LT5560 | -8 dB |\n")
        leaks = check_no_mpn_leak(output_dir, manifest_with_two_parts)
        # HMC1234 leaks in requirements.md AND gain_loss_budget.md
        leak_files = {l.file for l in leaks}
        assert "requirements.md" in leak_files
        assert "gain_loss_budget.md" in leak_files
        assert all(l.mpn == "HMC1234" for l in leaks)

    def test_skips_binary_files(self, manifest_with_two_parts, output_dir):
        """Excel / PDF / PNG outputs are not scanned — random byte sequences
        could match the MPN regex but they aren't real leaks."""
        _write(output_dir, "power_calculation.xlsx",
               "binary blob HMC9999 not actually text")
        leaks = check_no_mpn_leak(output_dir, manifest_with_two_parts)
        assert leaks == []

    def test_handles_missing_dir(self, manifest_with_two_parts, tmp_path):
        """Non-existent output_dir returns [] rather than crashing."""
        leaks = check_no_mpn_leak(tmp_path / "does_not_exist",
                                  manifest_with_two_parts)
        assert leaks == []

    def test_dashed_form_matches_undashed(self, output_dir):
        """`AD9082-FFV` in the BOM should match `AD9082FFV` on the page."""
        m = freeze(DesignManifest(
            project_id="1",
            bom=[{"part_number": "AD9082-FFV", "role": "adc"}],
        ))
        _write(output_dir, "requirements.md", "ADC: AD9082FFV (no dash)")
        leaks = check_no_mpn_leak(output_dir, m)
        assert leaks == []

    def test_file_filter_restricts_scan(self, manifest_with_two_parts, output_dir):
        """When file_filter is supplied, only the listed files are scanned."""
        _write(output_dir, "requirements.md", "HMC9999 leaking here")
        _write(output_dir, "block_diagram.md", "HMC9999 leaking here too")
        leaks = check_no_mpn_leak(
            output_dir, manifest_with_two_parts,
            file_filter=["requirements.md"],
        )
        assert len(leaks) == 1
        assert leaks[0].file == "requirements.md"


class TestAuditIssueConversion:
    def test_leak_to_issue_dict_shape(self):
        leaks = [Leak(file="requirements.md", mpn="HMC1234", line=42,
                       context="HMC1234 is obsolete")]
        issues = leaks_to_audit_issues(leaks)
        assert len(issues) == 1
        assert issues[0]["severity"] == "high"
        assert issues[0]["category"] == "manifest_mpn_leak"
        assert "HMC1234" in issues[0]["detail"]
        assert "L42" in issues[0]["location"]


class TestManifestLeakError:
    def test_error_summarises_leaks(self):
        leaks = [
            Leak(file="requirements.md", mpn="HMC1234"),
            Leak(file="glb.md", mpn="HMC1234"),
        ]
        with pytest.raises(ManifestLeakError) as exc:
            raise ManifestLeakError(leaks)
        assert "2 MPN leak" in str(exc.value)
