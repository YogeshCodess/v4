"""Regression test: the "Datasheet" link in component recommendations
must be labelled honestly based on where the URL actually points.

User feedback (2026-04-24):
  > "instead of datasheet here mention as digikey because its showing
  >  digikey product page right? or any idea to this?"

Context: since P11/P12 the datasheet URL resolver emits distributor
*search* URLs (digikey.com/en/products/result?keywords=MPN or
mouser.com/c/?q=MPN) when the original PDF URL is dead or missing.
Labelling those as "📄 Datasheet" misleads users into expecting a PDF
preview — the click actually lands them on a distributor catalog page.

Fix (P16): the link label is now chosen from the URL host:
  - `.pdf` / `/datasheet/` / `/media/` path  → "📄 Datasheet"
  - digikey.com / digikey.in                 → "🔗 DigiKey"
  - mouser.com / mouser.in                   → "🔗 Mouser"
  - anything else                            → "🔗 Product page"

This test file pins that rule against the two separate BOM-rendering
functions in `requirements_agent.py`:

  1. `_build_components_md`       (line ~7615) — writes the persistent
     `component_recommendations.md` file; rich rows with spec tables.
  2. `_build_response_summary`    (line ~3042) / its in-chat BOM table
     — emits a compact one-link-per-row summary in the chat response.

Both must use the same honest-labelling rule.
"""
from __future__ import annotations

import inspect

import pytest

from agents.requirements_agent import RequirementsAgent


# ---------------------------------------------------------------------------
# Whitebox static guards — the source of both renderers must reference the
# host-aware label helpers we just added. If someone reverts back to the
# blanket "[📄 Datasheet](...)" shape these guards fail.
# ---------------------------------------------------------------------------

def test_components_md_does_not_blanket_label_as_datasheet():
    src = inspect.getsource(RequirementsAgent._build_components_md)
    # No unconditional `[📄 Datasheet](ds_url)` — that misleading shape
    # must be replaced with a host-aware labeler.
    assert "[📄 Datasheet]({ds_url})" not in src, (
        "Unconditional '[📄 Datasheet](ds_url)' found in "
        "_build_components_md. Use a host-aware label — DigiKey / Mouser "
        "search URLs must not be presented as 'Datasheet'."
    )
    # Must reference the host check we added.
    assert "digikey.com" in src or "digikey.in" in src
    assert "mouser.com" in src or "mouser.in" in src


def test_response_summary_bom_does_not_blanket_label_as_datasheet():
    src = inspect.getsource(RequirementsAgent._build_response_summary)
    # Same guard for the chat-draft renderer.
    assert '[Datasheet]({chosen})' not in src, (
        "Unconditional '[Datasheet](chosen)' found in "
        "_build_response_summary. Use a host-aware label."
    )
    # Must reference the host check we added.
    assert "digikey.com" in src or "digikey.in" in src
    assert "mouser.com" in src or "mouser.in" in src


# ---------------------------------------------------------------------------
# Behaviour guards — exercise `_build_components_md` end-to-end on a few
# representative BOMs and assert the rendered markdown contains the right
# labels. We stub the HEAD probes so tests don't touch the network.
# ---------------------------------------------------------------------------

class _StubAgent(RequirementsAgent):
    def __init__(self):  # type: ignore[override]
        self._offered_candidate_mpns = set()
        self._offered_candidates_by_stage = {}

    def log(self, *_a, **_k):
        pass


def _bom_of(ds_url: str, source: str = "digikey") -> dict:
    """Build a minimal tool_input with one BOM row that has the given
    datasheet URL and distributor source."""
    return {
        "component_recommendations": [{
            "function": "Test Component",
            "primary_part": "TESTPART-1234",
            "primary_manufacturer": "TestMfr",
            "primary_description": "Test description",
            "datasheet_url": ds_url,
            "distributor_source": source,
            "primary_key_specs": {},
            "alternatives": [],
            "selection_rationale": "Test",
        }],
    }


def test_real_pdf_url_labelled_as_datasheet(monkeypatch):
    """PDF URLs (and `/datasheet/`, `/media/` paths) still label as
    'Datasheet' — those ARE actual datasheets."""
    agent = _StubAgent()
    # Stub the HEAD probes so nothing touches the network.
    monkeypatch.setattr(
        "tools.datasheet_url.candidate_datasheet_urls",
        lambda mfr, part: [],
    )
    tool_input = _bom_of(
        "https://www.mouser.com/datasheet/3/1014/1/1175ff.pdf",
        source="mouser",
    )
    md = agent._build_components_md(tool_input, "TestProj")
    # Real PDF → keep "Datasheet" label (possibly with emoji prefix).
    assert "Datasheet" in md
    assert "/datasheet/" in md or ".pdf" in md


def test_digikey_search_url_labelled_as_digikey(monkeypatch):
    """DigiKey keyword-search URLs must NOT be labelled 'Datasheet' —
    they land on a catalog page, not a PDF."""
    agent = _StubAgent()
    monkeypatch.setattr(
        "tools.datasheet_url.candidate_datasheet_urls",
        lambda mfr, part: [],
    )
    tool_input = _bom_of(
        "https://www.digikey.com/en/products/result?keywords=TESTPART-1234",
        source="digikey",
    )
    md = agent._build_components_md(tool_input, "TestProj")
    # DigiKey host → label contains DigiKey, not "Datasheet".
    assert "DigiKey" in md
    # The exact literal `[📄 Datasheet](` is the misleading form we want gone.
    assert "[📄 Datasheet](" not in md, (
        f"DigiKey search URL labelled as 📄 Datasheet in output:\n{md}"
    )


def test_mouser_search_url_labelled_as_mouser(monkeypatch):
    """Mouser keyword-search URLs must be labelled 'Mouser'."""
    agent = _StubAgent()
    monkeypatch.setattr(
        "tools.datasheet_url.candidate_datasheet_urls",
        lambda mfr, part: [],
    )
    tool_input = _bom_of(
        "https://www.mouser.com/c/?q=TESTPART-1234",
        source="mouser",
    )
    md = agent._build_components_md(tool_input, "TestProj")
    assert "Mouser" in md
    # No misleading blanket "Datasheet" label on the search URL.
    assert "[📄 Datasheet](" not in md


def test_unknown_host_labelled_as_product_page(monkeypatch):
    """URLs on hosts we don't recognise fall back to 'Product page'
    rather than the misleading 'Datasheet' label."""
    agent = _StubAgent()
    monkeypatch.setattr(
        "tools.datasheet_url.candidate_datasheet_urls",
        lambda mfr, part: [],
    )
    tool_input = _bom_of("https://some-random-distributor.example/p/x")
    md = agent._build_components_md(tool_input, "TestProj")
    assert "Product page" in md or "Distributor" in md


# ---------------------------------------------------------------------------
# Dedupe — don't show two links pointing at the same URL just because it
# surfaced under different tool_input keys.
# ---------------------------------------------------------------------------

def test_same_url_not_emitted_twice_in_link_row(monkeypatch):
    """If `datasheet_url` and `digikey_url` both point at the same URL,
    the side-by-side quick-link row must not show it twice."""
    agent = _StubAgent()
    monkeypatch.setattr(
        "tools.datasheet_url.candidate_datasheet_urls",
        lambda mfr, part: [],
    )
    same_url = "https://www.digikey.com/en/products/result?keywords=TESTPART-1234"
    tool_input = {
        "component_recommendations": [{
            "function": "Test Component",
            "primary_part": "TESTPART-1234",
            "primary_manufacturer": "TestMfr",
            "primary_description": "x",
            "datasheet_url": same_url,
            "digikey_url": same_url,
            "distributor_source": "digikey",
            "primary_key_specs": {},
            "alternatives": [],
            "selection_rationale": "Test",
        }],
    }
    md = agent._build_components_md(tool_input, "TestProj")
    # Find the link row — the line that contains "🔗 DigiKey" (and only
    # that kind of link markup, NOT the heading which wraps the MPN).
    link_row = next(
        (
            line for line in md.split("\n")
            if "[🔗 DigiKey]" in line or "[🔗 Mouser]" in line
            or "[📄 Datasheet]" in line
        ),
        "",
    )
    assert link_row, f"no link row found in md:\n{md}"
    # Exactly one link markup in the row.
    assert link_row.count("[") == 1, (
        f"link row has {link_row.count('[')} links — expected 1 "
        f"(dedupe failed):\n{link_row}"
    )
