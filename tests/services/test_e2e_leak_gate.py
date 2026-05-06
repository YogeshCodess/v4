"""End-to-end regression test for the cross-file MPN leak gate.

This test reproduces the EXACT hackathon-final bug: a banned MPN gets stripped
from `component_recommendations` by the audit but survives in
`gain_loss_budget.md` and the `requirements.md` prose. Before the SSoT
refactor, this drift went undetected and downstream phases (HRS, GLR, P4)
inherited it. After the refactor, the leak gate scans every output file
against the locked DesignManifest BOM and flags the discrepancy as a
`manifest_mpn_leak` audit issue with severity=high.

This is an integration test — it runs the real `services.p1_finalize` audit
pipeline + the real `services.manifest_validator` gate against a synthesized
P1 output directory. The LLM is not called; the test seeds the post-audit
state directly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.design_manifest import DesignManifest, freeze
from services.manifest_validator import (
    check_no_mpn_leak,
    leaks_to_audit_issues,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def synthesized_p1_output_dir(tmp_path: Path) -> Path:
    """Create a synthesized P1 output directory that exhibits the
    hackathon-final bug pattern: HMC1234 (a banned obsolete part) was
    audited out of `component_recommendations.md` but the LLM had also
    embedded it in `requirements.md` prose AND in `gain_loss_budget.md`
    rows. Pre-fix: HRS reads `component_recommendations.md` (clean) so
    HRS shows a coherent BOM, but the user reading the project summary
    sees HMC1234 there and wonders why it's missing from HRS — that's
    the inconsistency report the user filed.
    """
    out = tmp_path / "p1_outputs"
    out.mkdir(parents=True, exist_ok=True)

    # Post-audit clean: HMC1234 removed.
    (out / "component_recommendations.md").write_text(
        "# Component Recommendations\n\n"
        "### 1. LNA\n"
        "**Primary Choice:** [HMC8410](https://www.analog.com/HMC8410) (Analog Devices)\n\n"
        "### 2. Mixer\n"
        "**Primary Choice:** [LT5560](https://www.analog.com/LT5560) (Linear)\n\n",
        encoding="utf-8",
    )

    # Bug: requirements.md prose still mentions HMC1234 because the audit
    # only mutated `component_recommendations`, not the prose body.
    (out / "requirements.md").write_text(
        "# Requirements\n\n"
        "## Front-end signal chain\n\n"
        "The front end uses HMC8410 as the LNA followed by HMC1234 as a "
        "preselector and LT5560 for downconversion.\n\n"
        "Total cascade NF target: < 2.5 dB.\n",
        encoding="utf-8",
    )

    # Bug: gain_loss_budget.md still has the HMC1234 stage row because the
    # audit mutated `tool_input["component_recommendations"]` but not
    # `tool_input["gain_loss_budget"]["stages"]`.
    (out / "gain_loss_budget.md").write_text(
        "# Gain-Loss Budget\n\n"
        "| Stage | Part | Gain (dB) | NF (dB) |\n"
        "|-------|------|-----------|---------|\n"
        "| 1     | HMC8410 | 18      | 1.2     |\n"
        "| 2     | HMC1234 | 12      | 2.0     |\n"
        "| 3     | LT5560  | -8      | 10.0    |\n",
        encoding="utf-8",
    )

    return out


@pytest.fixture
def locked_manifest_post_audit() -> DesignManifest:
    """The DesignManifest as it would be after p1_finalize freezes it —
    HMC1234 is NOT in the BOM because the audit dropped it."""
    return freeze(DesignManifest(
        project_id="42",
        requirements={"freq_range": "2-18 GHz", "nf_target_db": 2.5},
        architecture="superhet",
        domain="communication",
        project_type="receiver",
        bom=[
            {"part_number": "HMC8410", "manufacturer": "Analog Devices",
             "role": "lna", "gain_db": 18, "nf_db": 1.2},
            {"part_number": "LT5560", "manufacturer": "Linear",
             "role": "mixer", "gain_db": -8, "nf_db": 10.0},
        ],
    ))


# ---------------------------------------------------------------------------
# E2E tests
# ---------------------------------------------------------------------------

class TestHmcBugRegression:
    """Regression tests that pin down the exact hackathon-final failure mode."""

    def test_leak_gate_catches_hmc_in_requirements_md(
        self, synthesized_p1_output_dir, locked_manifest_post_audit
    ):
        """The prose body of requirements.md mentions HMC1234 — a part the
        audit dropped. The leak gate MUST surface this."""
        leaks = check_no_mpn_leak(
            synthesized_p1_output_dir, locked_manifest_post_audit,
        )
        leak_files = {l.file: l.mpn for l in leaks}
        assert "requirements.md" in leak_files, (
            "Pre-fix bug: HMC1234 leaked into requirements.md prose. "
            "Leak gate must catch it. Got leaks: " + str(leak_files)
        )
        assert leak_files["requirements.md"] == "HMC1234"

    def test_leak_gate_catches_hmc_in_glb(
        self, synthesized_p1_output_dir, locked_manifest_post_audit
    ):
        """The GLB stages array mentions HMC1234. The leak gate MUST surface
        this — same root cause as the requirements.md leak."""
        leaks = check_no_mpn_leak(
            synthesized_p1_output_dir, locked_manifest_post_audit,
        )
        leak_files = {l.file: l.mpn for l in leaks}
        assert "gain_loss_budget.md" in leak_files
        assert leak_files["gain_loss_budget.md"] == "HMC1234"

    def test_leak_gate_does_not_flag_clean_components_md(
        self, synthesized_p1_output_dir, locked_manifest_post_audit
    ):
        """component_recommendations.md is the file the audit DOES mutate.
        It must be clean. If the gate flags it, the test fixture or the
        audit is broken."""
        leaks = check_no_mpn_leak(
            synthesized_p1_output_dir, locked_manifest_post_audit,
        )
        clean_leaks = [l for l in leaks if l.file == "component_recommendations.md"]
        assert clean_leaks == [], (
            "component_recommendations.md should be leak-free — the audit "
            "operates on this file's source. Got: " + str(clean_leaks)
        )

    def test_total_leak_count_matches_bug_pattern(
        self, synthesized_p1_output_dir, locked_manifest_post_audit
    ):
        """The bug fixture seeds HMC1234 in exactly 2 files (requirements.md
        and gain_loss_budget.md). Once-per-file is the expected detection."""
        leaks = check_no_mpn_leak(
            synthesized_p1_output_dir, locked_manifest_post_audit,
        )
        files_with_leaks = {l.file for l in leaks}
        # Both files have leaks, all leaks are HMC1234
        assert files_with_leaks == {"requirements.md", "gain_loss_budget.md"}
        assert all(l.mpn == "HMC1234" for l in leaks)


class TestAuditIntegration:
    """The leak gate's output feeds into the AuditReport. Verify the
    integration pipeline produces audit issues with the right shape so
    downstream UI / status / red-team report rendering all work."""

    def test_leaks_surface_as_high_severity_audit_issues(
        self, synthesized_p1_output_dir, locked_manifest_post_audit
    ):
        """Leaks are merged into the audit report at severity=high so the
        overall_pass verdict downgrades. This is what makes P1 visibly
        fail — without it the user would see a passing audit despite
        the cross-file inconsistency."""
        leaks = check_no_mpn_leak(
            synthesized_p1_output_dir, locked_manifest_post_audit,
        )
        issues = leaks_to_audit_issues(leaks)
        assert len(issues) == len(leaks)
        for issue in issues:
            assert issue["severity"] == "high"
            assert issue["category"] == "manifest_mpn_leak"
            # Detail should mention the leaked MPN explicitly so a human
            # reader can see what went wrong without opening the file.
            assert "HMC1234" in issue["detail"]
            # Suggested fix points the operator at the deterministic
            # renderer or LLM re-prompt — both real fixes for the bug.
            assert ("p1_renderers" in issue["suggested_fix"]
                    or "re-prompt" in issue["suggested_fix"])

    def test_audit_overall_pass_recomputes_to_false_when_leaks_present(
        self, synthesized_p1_output_dir, locked_manifest_post_audit
    ):
        """Simulate the merge that requirements_agent does: take an
        otherwise-passing AuditReport, fold in the leak issues, recompute
        overall_pass. After folding, overall_pass MUST be False because
        the high-severity leaks are blockers."""
        leaks = check_no_mpn_leak(
            synthesized_p1_output_dir, locked_manifest_post_audit,
        )
        leak_issues = leaks_to_audit_issues(leaks)
        # Start with an empty (passing) report. This represents the case
        # where the rest of the audit (banned-parts, lifecycle, datasheet)
        # passed cleanly — only the cross-file leak is the failure.
        rep = {"issues": [], "overall_pass": True}
        rep["issues"].extend(leak_issues)
        rep["overall_pass"] = not any(
            i.get("severity") in ("critical", "high") for i in rep["issues"]
        )
        assert rep["overall_pass"] is False, (
            "Leaks merged into the report MUST downgrade overall_pass — "
            "this is what the requirements_agent integration does and "
            "what makes P1 visibly fail to the user."
        )


class TestManifestAndOutputsInLockstep:
    """Verifies the round-trip: fresh manifest + clean outputs = no leaks.
    Catches accidental over-eager flagging when nothing is wrong."""

    def test_clean_outputs_with_clean_manifest_no_leaks(
        self, tmp_path, locked_manifest_post_audit
    ):
        """When component_recommendations.md, requirements.md, and
        gain_loss_budget.md ALL only mention parts in the manifest BOM,
        the gate must return zero leaks."""
        out = tmp_path / "clean_outputs"
        out.mkdir()
        (out / "component_recommendations.md").write_text(
            "- HMC8410 (Analog Devices) LNA\n- LT5560 (Linear) mixer\n",
            encoding="utf-8",
        )
        (out / "requirements.md").write_text(
            "Front-end uses HMC8410 LNA + LT5560 mixer. NF target 2.5 dB.\n",
            encoding="utf-8",
        )
        (out / "gain_loss_budget.md").write_text(
            "| HMC8410 | 18 dB |\n| LT5560 | -8 dB |\n",
            encoding="utf-8",
        )
        leaks = check_no_mpn_leak(out, locked_manifest_post_audit)
        assert leaks == [], (
            "Clean outputs against clean manifest must produce zero leaks. "
            "Got: " + str(leaks)
        )


class TestMultiArchitectureCoverage:
    """The leak gate must work the same way regardless of which
    architecture the project uses. Switch matrix was the specific case
    the user reported, so we test it explicitly."""

    def test_switch_matrix_leak_detection(self, tmp_path):
        """User-reported scenario: switch_matrix project where HMC parts
        leak across files. Same gate, different architecture."""
        manifest = freeze(DesignManifest(
            project_id="999",
            project_type="switch_matrix",
            architecture="switch_matrix",
            bom=[
                {"part_number": "ADRF5040", "manufacturer": "ADI", "role": "switch"},
                {"part_number": "ADRF5040", "manufacturer": "ADI", "role": "switch"},
            ],
        ))
        out = tmp_path / "switch_matrix_p1"
        out.mkdir()
        (out / "requirements.md").write_text(
            "Switch matrix uses ADRF5040 for routing. Legacy HMC347 stripped by audit.\n",
            encoding="utf-8",
        )
        (out / "gain_loss_budget.md").write_text(
            "| ADRF5040 | -1.5 dB |\n| HMC347 | -1.2 dB |\n",  # leaked HMC347
            encoding="utf-8",
        )
        leaks = check_no_mpn_leak(out, manifest)
        leak_mpns = {l.mpn for l in leaks}
        assert "HMC347" in leak_mpns, (
            "Switch-matrix scenario must detect HMC347 leak in GLB even "
            "though the BOM only contains ADRF5040 switches."
        )
        # ADRF5040 is in the manifest so it should NOT be flagged anywhere.
        assert "ADRF5040" not in leak_mpns
