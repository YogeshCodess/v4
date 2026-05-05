"""Project data models."""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class PhaseStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProjectPhaseStatus(BaseModel):
    """Status of a single phase in the pipeline."""
    phase_number: str = Field(..., description="Phase identifier (e.g., 'P1', 'P8a')")
    phase_name: str
    status: PhaseStatus = PhaseStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    output_files: list[str] = Field(default_factory=list)
    error_message: Optional[str] = None


VALID_DESIGN_SCOPES = {"full", "front-end", "downconversion", "dsp"}


class ProjectCreate(BaseModel):
    """Input for creating a new project."""
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="")
    design_type: str = Field(
        default="rf",
        description="Type: rf, digital"
    )
    design_scope: str = Field(
        default="full",
        description="Scope: full | front-end | downconversion | dsp"
    )


class Project(BaseModel):
    """Complete project state."""
    id: Optional[int] = None
    name: str
    description: str = ""
    design_type: str = "rf"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    # Phase statuses
    phases: dict[str, ProjectPhaseStatus] = Field(default_factory=lambda: {
        "P1": ProjectPhaseStatus(phase_number="P1", phase_name="Requirements Capture"),
        "P2": ProjectPhaseStatus(phase_number="P2", phase_name="HRS Generation"),
        "P3": ProjectPhaseStatus(phase_number="P3", phase_name="Compliance Validation"),
        "P4": ProjectPhaseStatus(phase_number="P4", phase_name="Netlist Generation"),
        "P5": ProjectPhaseStatus(phase_number="P5", phase_name="PCB Layout (Manual)"),
        "P6": ProjectPhaseStatus(phase_number="P6", phase_name="GLR Generation"),
        "P7": ProjectPhaseStatus(phase_number="P7", phase_name="FPGA HDL (Manual)"),
        "P8a": ProjectPhaseStatus(phase_number="P8a", phase_name="SRS Generation"),
        "P8b": ProjectPhaseStatus(phase_number="P8b", phase_name="SDD Generation"),
        "P8c": ProjectPhaseStatus(phase_number="P8c", phase_name="Code Generation"),
    })

    # Output directory for this project
    output_dir: str = ""

    # Conversation history for Phase 1
    conversation_history: list[dict] = Field(default_factory=list)
