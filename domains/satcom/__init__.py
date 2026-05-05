"""Satcom sub-domain: VSAT, SOTM, tracking terminals, SATCOM-on-the-move."""
import json
from pathlib import Path
from typing import Optional

from domains._schema import Part, Question
from domains.satcom.questions import SATCOM_QUESTIONS
from domains.satcom.prompts import SATCOM_SYSTEM_PROMPT_ADDITION

_HERE = Path(__file__).parent


def get_questions(application: Optional[str] = None) -> list[Question]:
    if application is None:
        return SATCOM_QUESTIONS
    return [q for q in SATCOM_QUESTIONS if not q.triggers or application in q.triggers]


def get_components(**filters) -> list[Part]:
    components_file = _HERE / "components.json"
    if not components_file.exists():
        return []
    raw = json.loads(components_file.read_text())
    parts = [Part(**p) for p in raw.get("components", [])]
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
    return SATCOM_SYSTEM_PROMPT_ADDITION
