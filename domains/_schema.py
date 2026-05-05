"""
Shared schemas across all domain modules.

These types are used by:
  - Component DB (domains/<domain>/components.json)
  - Cascade validator (tools/cascade_validator.py)
  - Red-team audit agent (agents/red_team_audit.py)
  - Round-1 elicitation agents (agents/requirements_agent.py)

Keep this file lightweight and dependency-free (Pydantic only).
"""

from enum import Enum
from typing import Optional, Literal
from pydantic import BaseModel, Field


class ScreeningClass(str, Enum):
    """MIL/Aero screening levels for part qualification."""
    COMMERCIAL = "commercial"           # 0 to +70 C
    INDUSTRIAL = "industrial"           # -40 to +85 C
    AUTOMOTIVE = "automotive"           # -40 to +125 C (AEC-Q100)
    MILITARY = "military"               # -55 to +125 C (MIL-PRF-38535 Class B)
    SPACE_B = "space_class_b"           # MIL-STD-883 Class B, screened
    SPACE_S = "space_class_s"           # MIL-STD-883 Class S, space-qualified
    RAD_HARD = "rad_hard"               # Radiation-hardened (TID/SEL/SEU specified)


class Part(BaseModel):
    """
    A defense-qualified electronic component.

    Extends the basic Component schema (schemas/component.py) with
    defense-specific metadata: screening class, temperature grade,
    ITAR classification, radiation tolerance.
    """
    part_number: str
    manufacturer: str
    category: str = Field(description="LNA, mixer, ADC, FPGA, synthesizer, regulator, etc.")
    description: str = ""

    # Frequency range (Hz)
    freq_min_hz: Optional[float] = None
    freq_max_hz: Optional[float] = None

    # RF performance
    noise_figure_db: Optional[float] = None
    gain_db: Optional[float] = None
    iip3_dbm: Optional[float] = None
    p1db_dbm: Optional[float] = None

    # Defense qualification
    screening_class: ScreeningClass = ScreeningClass.COMMERCIAL
    temp_min_c: Optional[float] = None
    temp_max_c: Optional[float] = None
    rad_tolerance_krad: Optional[float] = Field(
        default=None,
        description="Total Ionizing Dose tolerance in krad(Si). None = not rated."
    )
    itar_controlled: bool = False

    # Packaging / sourcing
    package: str = ""
    datasheet_url: Optional[str] = None
    datasheet_verified: bool = Field(
        default=False,
        description="True if URL returned HTTP 200 with PDF/HTML content type."
    )

    # Metadata
    domain: Literal["radar", "ew", "satcom", "communication", "shared"] = "shared"
    key_specs: dict = Field(default_factory=dict, description="Additional specs as k:v")
    notes: str = ""


class Question(BaseModel):
    """A Round-1 elicitation question for the P1 requirements agent."""
    id: str
    domain: str = Field(description="radar | ew | satcom | communication | shared")
    tier: int = Field(ge=1, le=3, description="1=mandatory, 2=application-adaptive, 3=follow-up")
    category: str = Field(description="RF performance, Linearity, Selectivity, etc.")
    text: str
    expected_format: str = Field(description="Hz, dB, dBm, enum, free-text, etc.")
    triggers: list[str] = Field(
        default_factory=list,
        description="Upstream answers that trigger this question (for tier 2/3)"
    )


class StandardClause(BaseModel):
    """A MIL-STD / DO / STANAG clause with applicability."""
    standard: str = Field(description="MIL-STD-461G, DO-254, STANAG 4193, etc.")
    clause: str = Field(description="CE101, Method 500.6, Section 11, etc.")
    short_title: str
    description: str
    typical_applicability: list[str] = Field(
        default_factory=list,
        description="radar, ew, satcom, communication, airborne, naval, etc."
    )
    severity: Literal["informational", "recommended", "required"] = "recommended"


class CascadeReport(BaseModel):
    """Output of cascade_validator.validate_cascade()."""
    noise_figure_db: float = Field(description="System NF from Friis")
    total_gain_db: float
    iip3_dbm: float = Field(description="Input-referred IIP3 of cascade")
    p1db_dbm: float = Field(description="Input-referred P1dB of cascade")
    temperature_c: float = Field(description="Temperature at which computed")
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    passed: bool = Field(description="True if no errors")


class AuditIssue(BaseModel):
    """One issue found by the red-team audit agent."""
    severity: Literal["critical", "high", "medium", "low", "info"]
    category: str = Field(description="hallucination, cascade_error, missing_citation, etc.")
    location: str = Field(description="Where in the output (section, field, line)")
    detail: str
    suggested_fix: Optional[str] = None


class AuditReport(BaseModel):
    """Output of red-team audit agent."""
    phase_id: str
    issues: list[AuditIssue] = Field(default_factory=list)
    hallucination_count: int = 0
    unresolved_citations: int = 0
    cascade_errors: int = 0
    overall_pass: bool = True
    confidence_score: float = Field(ge=0.0, le=1.0, default=1.0)
