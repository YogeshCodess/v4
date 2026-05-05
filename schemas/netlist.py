"""Netlist data models for Phase 4."""

from pydantic import BaseModel, Field
from typing import Optional


class NetlistPin(BaseModel):
    """Single pin on a component."""
    pin_number: str
    pin_name: str
    pin_type: str = Field(description="power, ground, input, output, bidirectional, no_connect")
    voltage_level: Optional[str] = None
    signal_name: Optional[str] = None


class NetlistNode(BaseModel):
    """Component instance in the netlist."""
    instance_id: str = Field(..., description="e.g., U1, R1, C1")
    part_number: str
    component_name: str
    reference_designator: str
    pins: list[NetlistPin] = Field(default_factory=list)
    properties: dict[str, str] = Field(default_factory=dict)


class NetlistEdge(BaseModel):
    """Connection between two pins in the netlist."""
    net_name: str
    from_instance: str
    from_pin: str
    to_instance: str
    to_pin: str
    signal_type: str = Field(default="digital", description="digital, analog, power, ground, clock")
    notes: str = ""


class Netlist(BaseModel):
    """Complete netlist graph."""
    project_name: str
    nodes: list[NetlistNode] = Field(default_factory=list)
    edges: list[NetlistEdge] = Field(default_factory=list)
    power_nets: list[str] = Field(default_factory=list)
    ground_nets: list[str] = Field(default_factory=list)
    # Explicit per-IC power binding: {ref: {pin_name: rail_name}}.
    # Lets downstream tools verify every VCC/VDD/AVDD pin is bound to a
    # named rail without re-deriving it from the edge list.
    power_map: dict[str, dict[str, str]] = Field(default_factory=dict)
    mermaid_diagram: str = ""
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
