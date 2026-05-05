"""Requirements and component data models."""

from pydantic import BaseModel, Field
from typing import Optional


class HardwareRequirement(BaseModel):
    """Single hardware requirement with IEEE traceability ID."""
    req_id: str = Field(..., description="e.g., REQ-HW-001")
    category: str = Field(..., description="functional, performance, interface, environmental, constraint")
    title: str
    description: str
    priority: str = Field(default="shall", description="shall, should, may")
    verification_method: str = Field(default="test", description="test, analysis, inspection, demonstration")
    source: str = Field(default="user", description="user, derived, standard")
    status: str = Field(default="active", description="active, deleted, deferred")


class ComponentRecommendation(BaseModel):
    """AI-recommended component with alternatives."""
    function: str = Field(..., description="What this component does in the design")
    primary: "ComponentOption"
    alternatives: list["ComponentOption"] = Field(default_factory=list)
    selection_rationale: str = ""


class ComponentOption(BaseModel):
    """Single component option."""
    part_number: str
    manufacturer: str
    description: str
    key_specs: dict[str, str] = Field(default_factory=dict)
    estimated_cost_usd: Optional[float] = None
    availability: str = Field(default="unknown", description="in_stock, limited, eol, unknown")
    lifecycle_status: str = Field(default="active", description="active, nrnd, eol, obsolete")
    datasheet_url: Optional[str] = None
    compliance: list[str] = Field(default_factory=list, description="RoHS, REACH, etc.")


class RequirementsDocument(BaseModel):
    """Complete structured requirements output from Phase 1."""
    project_name: str
    design_type: str
    summary: str = ""

    # Structured requirements
    requirements: list[HardwareRequirement] = Field(default_factory=list)

    # Component recommendations
    components: list[ComponentRecommendation] = Field(default_factory=list)

    # Design parameters extracted from conversation
    parameters: dict[str, str] = Field(default_factory=dict, description=(
        "Key design parameters: voltage, frequency, temperature_range, etc."
    ))

    # Mermaid diagram sources
    block_diagram_mermaid: str = ""
    architecture_mermaid: str = ""
