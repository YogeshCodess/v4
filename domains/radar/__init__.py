"""Radar sub-domain: fire-control, surveillance, tracking, RWR."""
import json
from pathlib import Path
from typing import Optional

from domains._schema import Part, Question
from domains.radar.questions import RADAR_QUESTIONS
from domains.radar.prompts import RADAR_SYSTEM_PROMPT_ADDITION

_HERE = Path(__file__).parent


def get_questions(application: Optional[str] = None) -> list[Question]:
    """Return Round-1 questions for radar domain, optionally filtered by application type."""
    if application is None:
        return RADAR_QUESTIONS
    return [q for q in RADAR_QUESTIONS if not q.triggers or application in q.triggers]


def get_components(**filters) -> list[Part]:
    """Return curated radar parts, optionally filtered by category/freq/screening."""
    components_file = _HERE / "components.json"
    if not components_file.exists():
        return []
    raw = json.loads(components_file.read_text())
    parts = [Part(**p) for p in raw.get("components", [])]

    # Apply filters
    if "category" in filters:
        parts = [p for p in parts if p.category == filters["category"]]
    if "freq_min_hz" in filters:
        parts = [p for p in parts if p.freq_max_hz and p.freq_max_hz >= filters["freq_min_hz"]]
    if "freq_max_hz" in filters:
        parts = [p for p in parts if p.freq_min_hz and p.freq_min_hz <= filters["freq_max_hz"]]
    if "screening_class" in filters:
        parts = [p for p in parts if p.screening_class == filters["screening_class"]]
    return parts


def get_system_prompt_addition() -> str:
    """Domain-specific prompt text to append to the P1 system prompt when domain=radar."""
    return RADAR_SYSTEM_PROMPT_ADDITION
