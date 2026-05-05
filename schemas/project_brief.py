"""
Structured ProjectBrief - the single source of truth for code-generation phases.

The genericity problem in P7/P7a/P8c was caused by:
  1. Each agent re-reading markdown via .read_text() and truncating to 6 KB.
  2. Each agent re-extracting specs via regex (lossy, inconsistent across phases).
  3. System prompts that said "write a HAL" without referencing the actual
     peripherals / registers / frequency / application class of THIS project.

The brief solves all three: it walks the existing P1-P7a outputs once,
extracts the 12-15 distinguishing specs as structured fields, and lets every
downstream agent prepend the brief to its LLM prompt. The LLM is then
primed with project specifics before being asked to write code, so radar
projects no longer produce the same boilerplate as satcom projects.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Peripheral(BaseModel):
    """One physical interface the firmware must talk to."""
    name: str = Field(..., description="Human name, e.g. 'SPI ADC #0'")
    bus: str = Field(..., description="Bus type: spi | i2c | uart | gpio | jtag | pcie | dac | adc | pwm")
    address: Optional[str] = Field(None, description="Device address or slave-select id")
    description: str = ""
    # 2026-05-02: datasheet-derived parameters resolved by
    # services.component_spec_resolver. Populated lazily by the brief
    # builder; None for peripherals that pre-date the resolver.
    spec: Optional[dict] = Field(None, description="ComponentSpec.model_dump()")


class Register(BaseModel):
    """One register from the P7a register map."""
    name: str
    address: str
    access: str = "RW"      # RO / RW / W1C
    reset_value: Optional[str] = None
    description: str = ""


class FSM(BaseModel):
    """One state machine from the P7 / GLR design."""
    name: str
    states: list[str] = Field(default_factory=list)
    description: str = ""


class ProjectBrief(BaseModel):
    """The structured fingerprint of a project, derived from P1-P7a outputs.

    Every code-generation agent SHOULD prepend `to_prompt_preamble()` to its
    LLM system message so the model sees what makes THIS project different
    before being asked to generate code.
    """
    # Identity ------------------------------------------------------------
    project_name: str
    project_id: int = 0
    application_class: str = "general"
    project_type: str = "receiver"   # receiver | transmitter | transceiver | switch_matrix | power_supply
    design_scope: str = "full"

    # RF specs (from P1) --------------------------------------------------
    frequency_min_ghz: Optional[float] = None
    frequency_max_ghz: Optional[float] = None
    bandwidth_mhz: Optional[float] = None
    target_nf_db: Optional[float] = None
    target_gain_db: Optional[float] = None
    target_pout_dbm: Optional[float] = None    # TX only

    # Hardware (from P1 + P4) --------------------------------------------
    architecture: str = ""           # e.g. "superheterodyne dual-conversion"
    peripherals: list[Peripheral] = Field(default_factory=list)
    component_count: int = 0

    # FPGA / RTL (from P6 + P7) ------------------------------------------
    clock_frequency_mhz: float = 100.0
    fpga_part: str = "xc7a35tcpg236-1"
    hdl_language: str = "verilog"    # verilog | vhdl
    fsms: list[FSM] = Field(default_factory=list)

    # Register map (from P7a) --------------------------------------------
    registers: list[Register] = Field(default_factory=list)

    # Compliance + environment -------------------------------------------
    compliance_targets: list[str] = Field(default_factory=list)  # MIL-STD-810, ITAR, RoHS, etc.
    operating_temp_min_c: Optional[float] = None
    operating_temp_max_c: Optional[float] = None

    # ------------------------------------------------------------------
    def fingerprint(self) -> str:
        """Stable hash over the structural distinguishers of this brief.

        Used by the anti-repetition audit: if two projects with different
        requirements produce the same fingerprint, the genericity bug is
        back. Hash inputs are deliberately limited to project-distinguishing
        fields - we want different SPI counts to produce different hashes,
        but free-form descriptions should not flap the hash on cosmetic edits.
        """
        import hashlib
        canonical = "|".join([
            self.application_class,
            self.project_type,
            self.architecture,
            f"{self.frequency_min_ghz}-{self.frequency_max_ghz}GHz",
            f"BW{self.bandwidth_mhz}",
            self.hdl_language,
            ";".join(sorted(f"{p.bus}:{p.name}" for p in self.peripherals)),
            ";".join(sorted(f"{r.address}:{r.name}" for r in self.registers)),
            ";".join(sorted(f.name for f in self.fsms)),
        ])
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def to_prompt_preamble(self) -> str:
        """Render a compact ~250-word brief that prepends every code-gen prompt.

        The format is intentionally bullet-heavy and number-rich so the LLM
        can lift specifics straight into generated code. Empty fields are
        skipped so the brief stays dense even on partially-filled projects.
        """
        lines: list[str] = []
        lines.append("PROJECT BRIEF (use these specifics in every generated artifact):")
        lines.append(f"- Name: {self.project_name}")
        lines.append(f"- Application class: {self.application_class}")
        lines.append(f"- Project type: {self.project_type}")
        if self.frequency_min_ghz is not None and self.frequency_max_ghz is not None:
            lines.append(
                f"- Frequency range: {self.frequency_min_ghz}-{self.frequency_max_ghz} GHz"
            )
        if self.bandwidth_mhz:
            lines.append(f"- Instantaneous bandwidth: {self.bandwidth_mhz} MHz")
        if self.target_nf_db is not None:
            lines.append(f"- Target system NF: {self.target_nf_db} dB")
        if self.target_gain_db is not None:
            lines.append(f"- Target system gain: {self.target_gain_db} dB")
        if self.target_pout_dbm is not None:
            lines.append(f"- Target Pout: {self.target_pout_dbm} dBm")
        if self.architecture:
            lines.append(f"- RF architecture: {self.architecture}")
        if self.peripherals:
            lines.append(
                "- Peripherals (the HAL must implement EXACTLY these "
                f"{len(self.peripherals)} interfaces):"
            )
            for p in self.peripherals:
                addr = f" @ {p.address}" if p.address else ""
                lines.append(f"    * {p.bus.upper()} - {p.name}{addr}")
        lines.append(f"- Clock: {self.clock_frequency_mhz} MHz on {self.fpga_part}")
        lines.append(f"- HDL language: {self.hdl_language.upper()}")
        if self.fsms:
            fsm_summary = ", ".join(f"{f.name}({len(f.states)})" for f in self.fsms)
            lines.append(f"- FSMs ({len(self.fsms)}): {fsm_summary}")
        if self.registers:
            lines.append(
                f"- Register map: {len(self.registers)} registers - the HAL "
                "and RTL register file MUST use the addresses below:"
            )
            for r in self.registers[:30]:
                lines.append(f"    * {r.address} {r.name} ({r.access}) - {r.description[:60]}")
            if len(self.registers) > 30:
                lines.append(f"    * ... and {len(self.registers) - 30} more in register_description_table.md")
        if self.compliance_targets:
            lines.append(f"- Compliance targets: {', '.join(self.compliance_targets)}")
        lines.append("")
        lines.append(
            "EVERY function, register accessor, FSM state, peripheral driver, "
            "GUI panel and test case you generate MUST reference these specifics. "
            "Generic boilerplate is a regression - if you would write the same "
            "code for a satcom modem and a radar receiver, you are doing it wrong."
        )
        return "\n".join(lines)
