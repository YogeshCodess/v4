"""
Defense RF receiver domains for Silicon to Software (S2S) V2.

Each sub-module represents one defense RF sub-domain:
  - radar: fire-control, surveillance, tracking, RWR
  - ew: HF/V/UHF monitoring, jammers, SIGINT, DF, ESM
  - satcom: ground terminals, user terminals, anti-jam
  - communication: tactical radios, data-links, COMINT

Adding a new domain = drop in a new folder with the same interface:
  - questions.py     -> get_questions() -> List[Question]
  - components.json  -> schema-validated parts list
  - standards.py     -> get_standards() -> List[StandardClause]
  - prompts.py       -> get_system_prompt_addition() -> str
"""

from typing import Optional

from domains._schema import Part, Question, StandardClause, ScreeningClass

__all__ = [
    "Part",
    "Question",
    "StandardClause",
    "ScreeningClass",
    "SUPPORTED_DOMAINS",
    "get_domain_module",
    "get_questions",
    "get_components",
    "get_system_prompt_addition",
]

SUPPORTED_DOMAINS = ["radar", "ew", "satcom", "communication"]


def get_domain_module(domain: str):
    """Return the domain sub-package (e.g. domains.radar) for dynamic dispatch."""
    if domain not in SUPPORTED_DOMAINS:
        raise ValueError(
            f"Unknown domain '{domain}'. Supported: {SUPPORTED_DOMAINS}"
        )
    import importlib

    return importlib.import_module(f"domains.{domain}")


def get_questions(domain: str, application: Optional[str] = None) -> list[Question]:
    return get_domain_module(domain).get_questions(application=application)


def get_components(domain: str, **filters) -> list[Part]:
    return get_domain_module(domain).get_components(**filters)


def get_system_prompt_addition(domain: str) -> str:
    return get_domain_module(domain).get_system_prompt_addition()
