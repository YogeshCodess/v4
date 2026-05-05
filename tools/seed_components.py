"""
Component seeding — load data/sample_components.json into the vector store
on first boot when the collection is empty.

Delegates the actual add to `ComponentSearchTool.add_component` so the
on-disk storage shape and metadata layout stay consistent with search.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from schemas.component import Component
from tools.component_search import ComponentSearchTool

logger = logging.getLogger(__name__)


def _coerce_specs(raw: dict) -> dict[str, str]:
    """Chroma metadata must be primitive — stringify nested specs."""
    if not isinstance(raw, dict):
        return {}
    return {k: str(v) for k, v in raw.items()}


def seed_if_empty() -> None:
    """Populate the vector store from data/sample_components.json, but only
    when it's empty. Safe to call on every startup (idempotent no-op when
    already populated)."""
    tool = ComponentSearchTool()

    if not tool._vs:
        logger.warning("Vector store not available — skipping seed")
        return

    if tool.get_stats().get("total_components", 0) > 0:
        logger.info("Vector store already populated — skipping seed")
        return

    sample_file = Path(__file__).parent.parent / "data" / "sample_components.json"
    if not sample_file.exists():
        logger.warning("Sample components file not found: %s", sample_file)
        return

    try:
        data = json.loads(sample_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to load %s: %s", sample_file, exc)
        return

    components = data.get("components", [])
    logger.info("Seeding %d sample components...", len(components))

    added = 0
    for comp_data in components:
        component = Component(
            part_number=comp_data.get("part_number", ""),
            manufacturer=comp_data.get("manufacturer", ""),
            description=comp_data.get("description", ""),
            category=comp_data.get("category", "Unknown"),
            key_specs=_coerce_specs(comp_data.get("key_specs", {})),
            datasheet_url=comp_data.get("datasheet_url", ""),
            lifecycle_status=comp_data.get("lifecycle_status", "unknown"),
            estimated_cost_usd=comp_data.get("estimated_cost_usd"),
        )
        # Prefer explicit search_text if present (richer than description)
        description_text = comp_data.get("search_text") or component.description
        if tool.add_component(component, description_text):
            added += 1

    logger.info("Seeded %d/%d components", added, len(components))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    seed_if_empty()
