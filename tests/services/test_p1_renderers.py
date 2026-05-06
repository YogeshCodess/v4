"""Unit tests for services.p1_renderers — block-beta block diagram renderer."""
from __future__ import annotations

from services.design_manifest import DesignManifest
from services.p1_renderers import (
    render_block_diagram_md,
    render_block_diagram_mermaid,
)


def _row(pn: str, role: str, **extra) -> dict:
    return {"part_number": pn, "manufacturer": "ADI", "role": role, **extra}


# ---------------------------------------------------------------------------
# Block-beta syntax
# ---------------------------------------------------------------------------

class TestBlockBetaSyntax:
    def test_emits_block_beta_directive(self):
        """We MUST use `block-beta`, not `flowchart`/`graph` — the user
        explicitly asked for block diagrams, not flowcharts."""
        m = DesignManifest(
            project_id="1",
            architecture="superhet",
            bom=[_row("HMC8410", "lna"), _row("LT5560", "mixer")],
        )
        out = render_block_diagram_mermaid(m)
        assert out.startswith("block-beta"), out[:60]
        assert "flowchart" not in out
        assert "graph LR" not in out
        assert "graph TD" not in out

    def test_columns_directive_present(self):
        m = DesignManifest(
            project_id="1",
            bom=[_row("HMC8410", "lna"), _row("LT5560", "mixer")],
        )
        assert "columns " in render_block_diagram_mermaid(m)

    def test_includes_every_bom_part(self):
        m = DesignManifest(
            project_id="1",
            bom=[
                _row("HMC8410", "lna"),
                _row("LT5560", "mixer"),
                _row("ADF4351", "lo"),
                _row("AD9082", "adc"),
            ],
        )
        out = render_block_diagram_mermaid(m)
        for pn in ("HMC8410", "LT5560", "ADF4351", "AD9082"):
            assert pn in out, f"Missing {pn} from rendered diagram"


# ---------------------------------------------------------------------------
# Topology dispatch
# ---------------------------------------------------------------------------

class TestTopologyDispatch:
    def test_switch_matrix_uses_tree_layout(self):
        """Switch-matrix project type should produce 3-column layout
        (inputs | switches | outputs)."""
        m = DesignManifest(
            project_id="1",
            project_type="switch_matrix",
            architecture="switch_matrix",
            design_parameters={"matrix_inputs": 4, "matrix_outputs": 4},
            bom=[
                _row("ADRF5040", "switch"),
                _row("ADRF5040", "switch"),
                _row("ADRF5040", "switch"),
            ],
        )
        out = render_block_diagram_mermaid(m)
        assert "block:inputs" in out
        assert "block:matrix" in out
        assert "block:outputs" in out
        assert "RF IN 1" in out
        assert "RF OUT 1" in out

    def test_linear_cascade_for_superhet(self):
        m = DesignManifest(
            project_id="1",
            architecture="superhet",
            bom=[_row("HMC8410", "lna"), _row("LT5560", "mixer")],
        )
        out = render_block_diagram_mermaid(m)
        # Linear cascade does NOT have block:inputs / block:matrix
        assert "block:matrix" not in out
        # But it should have edges between components
        assert "-->" in out

    def test_empty_bom_emits_placeholder(self):
        m = DesignManifest(project_id="1", architecture="superhet", bom=[])
        out = render_block_diagram_mermaid(m)
        assert "block-beta" in out
        # Placeholder text for empty BOM
        assert "No components" in out or "Approve P1" in out


# ---------------------------------------------------------------------------
# MPN-leak guarantee (the whole point)
# ---------------------------------------------------------------------------

class TestNoLeakByConstruction:
    def test_only_bom_mpns_in_output(self):
        """The renderer is a pure function of `manifest.bom` — the
        rendered diagram CANNOT contain MPNs that aren't in the BOM.
        This is the structural guarantee that closes the leak class."""
        from services.manifest_validator import extract_mpns

        m = DesignManifest(
            project_id="1",
            architecture="superhet",
            bom=[
                _row("HMC8410", "lna"),
                _row("LT5560", "mixer"),
                _row("ADF4351", "lo"),
            ],
        )
        out = render_block_diagram_mermaid(m)
        rendered_mpns = extract_mpns(out)
        bom_mpns = m.allowed_mpns()
        # Every rendered MPN must be in the BOM.
        leaks = rendered_mpns - bom_mpns
        assert leaks == set(), f"Renderer leaked MPNs not in BOM: {leaks}"


class TestMarkdownWrapper:
    def test_wraps_in_mermaid_fence(self):
        m = DesignManifest(
            project_id="1",
            bom=[_row("HMC8410", "lna")],
        )
        md = render_block_diagram_md(m, project_name="Demo")
        assert "# System Block Diagram" in md
        assert "## Demo" in md
        assert "```mermaid" in md
        assert "```\n" in md
        assert "block-beta" in md
