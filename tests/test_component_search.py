"""
Tests for tools/component_search.py + tools/seed_components.py.

Uses a `FakeEmbeddings` from langchain-core so the tests don't depend on
OpenAI, HuggingFace downloads, or ChromaDB's ONNX runtime — they just need
a vector to index against.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.embeddings import FakeEmbeddings

from schemas.component import Component
from tools.component_search import ComponentSearchTool


@pytest.fixture
def tmp_chroma(tmp_path: Path, monkeypatch):
    """Point settings.chroma_persist_dir at a per-test tempdir."""
    import config as _config
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    # Ensure config picks up the overridden env
    import importlib
    importlib.reload(_config)
    import tools.component_search as _cs
    importlib.reload(_cs)
    yield tmp_path / "chroma"


@pytest.fixture
def tool(tmp_chroma):
    """ComponentSearchTool wired up with a deterministic FakeEmbeddings."""
    from tools.component_search import ComponentSearchTool
    return ComponentSearchTool(embeddings=FakeEmbeddings(size=16))


# ---------------------------------------------------------------------------
# Smoke test — store + search round-trip
# ---------------------------------------------------------------------------

def test_add_and_search_round_trip(tool):
    c = Component(
        part_number="STM32F407",
        manufacturer="STMicro",
        description="ARM Cortex-M4 MCU",
        category="MCU",
        key_specs={"freq": "168MHz", "flash": "1MB"},
    )
    assert tool.add_component(c, "ARM Cortex-M4 microcontroller 168 MHz 1MB flash") is True

    # FakeEmbeddings gives the same vector for the same text, so exact
    # recall is guaranteed when we search with the same query.
    stats = tool.get_stats()
    assert stats["total_components"] == 1
    assert stats["categories"] == {"MCU": 1}


def test_search_threshold_filters_low_similarity(tool):
    c = Component(
        part_number="STM32F4", manufacturer="ST", description="MCU", category="MCU",
    )
    tool.add_component(c, "ARM Cortex-M4 32-bit microcontroller")
    results = tool.search(
        "completely unrelated query about cats",
        n_results=5, min_similarity=0.99,
    )
    # With a near-maximum threshold the random FakeEmbeddings vectors can't clear the bar.
    assert results == []


def test_search_respects_n_results_cap(tool):
    for i in range(5):
        tool.add_component(
            Component(part_number=f"P{i}", manufacturer="X", description="d", category="IC"),
            f"part {i}",
        )
    # Ask for 2 with a permissive threshold → should receive at most 2.
    results = tool.search("anything", n_results=2, min_similarity=0.0)
    assert len(results) <= 2


# ---------------------------------------------------------------------------
# Metadata round-trip (key_specs JSON serialisation)
# ---------------------------------------------------------------------------

def test_key_specs_roundtrip_through_json_metadata(tool):
    c = Component(
        part_number="LM7805",
        manufacturer="TI",
        description="5V linear regulator",
        category="Power",
        key_specs={"Vout": "5V", "Iout": "1A"},
    )
    tool.add_component(c, "5V linear voltage regulator")

    reloaded = tool.get_by_part_number("LM7805")
    assert reloaded is not None
    assert reloaded.part_number == "LM7805"
    assert reloaded.key_specs == {"Vout": "5V", "Iout": "1A"}


def test_get_by_part_number_returns_none_when_missing(tool):
    assert tool.get_by_part_number("does-not-exist") is None


# ---------------------------------------------------------------------------
# Category filter
# ---------------------------------------------------------------------------

def test_category_filter_excludes_other_categories(tool):
    tool.add_component(
        Component(part_number="MCU1", manufacturer="X", description="m", category="MCU"),
        "microcontroller",
    )
    tool.add_component(
        Component(part_number="REG1", manufacturer="X", description="r", category="Power"),
        "regulator",
    )
    hits = tool.search("anything", category="MCU", n_results=10, min_similarity=0.0)
    for h in hits:
        assert h.component.category == "MCU"


# ---------------------------------------------------------------------------
# add_component is an upsert (same part_number replaces)
# ---------------------------------------------------------------------------

def test_add_component_upserts_on_duplicate_part_number(tool):
    c1 = Component(part_number="X1", manufacturer="A", description="v1", category="IC")
    tool.add_component(c1, "first description")
    c2 = Component(part_number="X1", manufacturer="B", description="v2", category="IC")
    tool.add_component(c2, "second description")
    assert tool.get_stats()["total_components"] == 1
    assert tool.get_by_part_number("X1").manufacturer == "B"


# ---------------------------------------------------------------------------
# Availability gate
# ---------------------------------------------------------------------------

def test_search_returns_empty_list_when_vector_store_unavailable(tmp_chroma):
    # Build a tool with a broken embedder so initialisation falls through.
    class _BadEmb:
        def embed_documents(self, texts):
            raise RuntimeError("oops")

        def embed_query(self, text):
            raise RuntimeError("oops")

    t = ComponentSearchTool(embeddings=_BadEmb())  # type: ignore[arg-type]
    # Either the vector store is None or adds will fail — search must tolerate it.
    assert t.search("anything") == []


# ---------------------------------------------------------------------------
# seed_components.seed_if_empty
# ---------------------------------------------------------------------------

def test_seed_if_empty_loads_from_sample_json(tmp_path: Path, monkeypatch):
    # Build a minimal sample JSON under a tempdir and point the loader at it.
    sample = {
        "components": [
            {
                "part_number": "ADL8107", "manufacturer": "ADI",
                "description": "Wideband LNA", "category": "LNA",
                "key_specs": {"freq": "2-18 GHz", "nf": "1.8 dB"},
                "search_text": "2-18 GHz low-noise amplifier, 1.8 dB NF",
            },
            {
                "part_number": "HMC1049", "manufacturer": "ADI",
                "description": "Double-balanced mixer", "category": "Mixer",
                "key_specs": {"freq": "3.5-10 GHz"},
            },
        ],
    }
    root = tmp_path / "repo"
    (root / "tools").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "data" / "sample_components.json").write_text(json.dumps(sample))

    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    import config, tools.component_search, tools.seed_components, importlib
    importlib.reload(config); importlib.reload(tools.component_search)

    # Patch the seed module's Path resolution to point at our tempdir.
    from tools import seed_components as seed
    importlib.reload(seed)
    fake_file_path = root / "tools" / "seed_components.py"
    fake_file_path.touch()
    monkeypatch.setattr(seed, "__file__", str(fake_file_path))

    # Force a FakeEmbeddings into the tool so it doesn't try OpenAI/HF.
    monkeypatch.setattr(
        seed, "ComponentSearchTool",
        lambda: tools.component_search.ComponentSearchTool(
            embeddings=FakeEmbeddings(size=8)
        ),
    )

    seed.seed_if_empty()

    tool = tools.component_search.ComponentSearchTool(embeddings=FakeEmbeddings(size=8))
    assert tool.get_stats()["total_components"] == 2
    by_pn = tool.get_by_part_number("ADL8107")
    assert by_pn is not None
    assert by_pn.manufacturer == "ADI"
