"""Component database models."""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class Component(BaseModel):
    """Component stored in ChromaDB / database."""
    part_number: str
    manufacturer: str
    description: str
    category: str = Field(default="", description="IC, passive, connector, etc.")
    subcategory: str = ""
    key_specs: dict[str, str] = Field(default_factory=dict)
    package: str = ""
    datasheet_url: Optional[str] = None
    datasheet_text: str = Field(default="", description="Extracted text from datasheet for RAG")
    compliance: list[str] = Field(default_factory=list)
    lifecycle_status: str = "active"
    estimated_cost_usd: Optional[float] = None
    last_updated: datetime = Field(default_factory=datetime.now)


class ComponentSearchResult(BaseModel):
    """Result from ChromaDB vector search."""
    component: Component
    relevance_score: float = Field(ge=0.0, le=1.0)
    match_reason: str = ""
