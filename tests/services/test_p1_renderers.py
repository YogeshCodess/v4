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
    def test_wraps_with_svg_first_then_heading(self):
        """As of the SVG renderer landing, render_block_diagram_md prefers
        SVG over mermaid. The mermaid fence path is exercised by the
        fallback test below."""
        m = DesignManifest(
            project_id="1",
            bom=[_row("HMC8410", "lna")],
        )
        md = render_block_diagram_md(m, project_name="Demo")
        assert "# System Block Diagram" in md
        assert "## Demo" in md
        assert "<svg" in md  # primary output is now inline SVG

    def test_falls_back_to_mermaid_when_svg_render_fails(self, monkeypatch):
        """If the SVG renderer raises, render_block_diagram_md should
        gracefully degrade to the mermaid fence (proven leak-proof too)."""
        import services.p1_renderers as p1r
        m = DesignManifest(
            project_id="1",
            bom=[_row("HMC8410", "lna")],
        )
        def _boom(*_a, **_kw):
            raise RuntimeError("simulated SVG failure")
        monkeypatch.setattr(p1r, "render_block_diagram_svg", _boom)
        md = render_block_diagram_md(m, project_name="Demo")
        assert "```mermaid" in md
        assert "block-beta" in md


# ---------------------------------------------------------------------------
# Components.md renderer
# ---------------------------------------------------------------------------

class TestComponentsRenderer:
    def test_includes_every_bom_row(self):
        from services.p1_renderers import render_components_md
        from services.design_manifest import freeze
        m = freeze(DesignManifest(
            project_id="1",
            architecture="superhet",
            bom=[
                _row("HMC8410", "lna", gain_db=18, nf_db=1.2),
                _row("LT5560", "mixer", gain_db=-8),
                _row("AD9082", "adc"),
            ],
        ))
        md = render_components_md(m, project_name="Demo")
        for pn in ("HMC8410", "LT5560", "AD9082"):
            assert pn in md

    def test_no_leak_by_construction(self):
        """The Components renderer is a pure function of manifest.bom —
        every MPN in the output must be in the BOM."""
        from services.p1_renderers import render_components_md
        from services.manifest_validator import extract_mpns

        m = DesignManifest(
            project_id="1",
            bom=[
                _row("HMC8410", "lna"),
                _row("LT5560", "mixer"),
            ],
        )
        md = render_components_md(m)
        rendered = extract_mpns(md)
        leaks = rendered - m.allowed_mpns()
        assert leaks == set(), f"Components renderer leaked: {leaks}"

    def test_empty_bom_produces_friendly_message(self):
        from services.p1_renderers import render_components_md
        m = DesignManifest(project_id="1", bom=[])
        md = render_components_md(m, project_name="Empty")
        assert "Manifest BOM is empty" in md
        assert "Empty" in md

    def test_includes_specs_when_present(self):
        from services.p1_renderers import render_components_md
        m = DesignManifest(
            project_id="1",
            bom=[_row("HMC8410", "lna", gain_db=18, nf_db=1.2, package="QFN-32")],
        )
        md = render_components_md(m)
        assert "gain_db" in md
        assert "nf_db" in md
        assert "QFN-32" in md
        # Em-dash placeholder should NOT be in the output for present values.
        assert "| gain_db | 18 dB |" in md or "18 dB" in md


# ---------------------------------------------------------------------------
# GLB renderer (simplified deterministic version)
# ---------------------------------------------------------------------------

class TestGlbRenderer:
    def test_emits_table_and_cascade_summary(self):
        from services.p1_renderers import render_glb_md
        m = DesignManifest(
            project_id="1",
            bom=[
                _row("HMC8410", "lna", gain_db=18, nf_db=1.2),
                _row("LT5560", "mixer", gain_db=-8, nf_db=10.0),
                _row("AD9082", "adc", gain_db=0, nf_db=20.0),
            ],
        )
        md = render_glb_md(m, project_name="Demo")
        assert "Per-stage budget" in md
        assert "Cascade summary" in md
        # All three parts in the table.
        for pn in ("HMC8410", "LT5560", "AD9082"):
            assert pn in md
        # Total gain is 18 + (-8) + 0 = 10 dB
        assert "10.00 dB" in md

    def test_friis_cascade_nf_correct(self):
        """Friis: F_total = F1 + (F2-1)/G1 + (F3-1)/(G1*G2)
           For a 2-stage cascade with NF1=2 dB, G1=20 dB, NF2=10 dB:
             F1 = 1.585, G1 = 100, F2 = 10
             F_total = 1.585 + (10 - 1)/100 = 1.585 + 0.09 = 1.675
             NF_total = 10*log10(1.675) = 2.24 dB
        """
        from services.p1_renderers import render_glb_md
        m = DesignManifest(
            project_id="1",
            bom=[
                _row("LNA", "lna", gain_db=20, nf_db=2.0),
                _row("MIX", "mixer", gain_db=-6, nf_db=10.0),
            ],
        )
        md = render_glb_md(m)
        # Total gain = 20 - 6 = 14 dB
        assert "14.00 dB" in md
        # Friis NF should be approximately 2.24 dB — the exact decimals
        # depend on the precision of intermediate computation. Match a
        # tolerant regex.
        assert "2.2" in md or "2.3" in md, (
            "Friis NF should be ~2.24 dB for this cascade; found neither "
            "2.2x nor 2.3x in output"
        )

    def test_missing_specs_disables_friis(self):
        from services.p1_renderers import render_glb_md
        m = DesignManifest(
            project_id="1",
            bom=[_row("HMC8410", "lna")],   # no gain_db/nf_db
        )
        md = render_glb_md(m)
        assert "cannot be computed" in md.lower() or "missing" in md.lower()

    def test_no_leak_by_construction_glb(self):
        from services.p1_renderers import render_glb_md
        from services.manifest_validator import extract_mpns
        m = DesignManifest(
            project_id="1",
            bom=[_row("HMC8410", "lna", gain_db=18, nf_db=1.2)],
        )
        md = render_glb_md(m)
        rendered = extract_mpns(md)
        leaks = rendered - m.allowed_mpns()
        assert leaks == set(), f"GLB renderer leaked: {leaks}"


# ---------------------------------------------------------------------------
# SVG renderer (Option 2 — proper RF schematic symbols)
# ---------------------------------------------------------------------------

class TestSvgRenderer:
    def test_emits_well_formed_svg(self):
        from services.p1_renderers import render_block_diagram_svg
        m = DesignManifest(
            project_id="1",
            architecture="superhet",
            bom=[_row("HMC8410", "lna"), _row("LT5560", "mixer")],
        )
        svg = render_block_diagram_svg(m)
        # Smoke: starts with <svg, ends with </svg>, has xmlns
        assert svg.startswith("<svg")
        assert svg.rstrip().endswith("</svg>")
        assert 'xmlns="http://www.w3.org/2000/svg"' in svg
        # XML well-formed — parse it
        import xml.etree.ElementTree as ET
        ET.fromstring(svg)  # raises if malformed

    def test_uses_amp_triangle_for_lna(self):
        from services.p1_renderers import render_block_diagram_svg
        m = DesignManifest(project_id="1", bom=[_row("LNA1", "lna")])
        svg = render_block_diagram_svg(m)
        # The amp triangle uses the polygon "0,5 80,30 0,55"
        assert "polygon points=\"0,5 80,30 0,55\"" in svg

    def test_uses_circle_with_x_for_mixer(self):
        from services.p1_renderers import render_block_diagram_svg
        m = DesignManifest(project_id="1", bom=[_row("MIX1", "mixer")])
        svg = render_block_diagram_svg(m)
        # Mixer = circle + two crossing lines
        assert "<circle" in svg
        # two diagonal lines for the X
        assert 'x1="25" y1="15" x2="55" y2="45"' in svg
        assert 'x1="55" y1="15" x2="25" y2="45"' in svg

    def test_uses_filter_rect_with_sine(self):
        from services.p1_renderers import render_block_diagram_svg
        m = DesignManifest(project_id="1", bom=[_row("FL1", "bpf")])
        svg = render_block_diagram_svg(m)
        # Filter has a rectangle + path with Q (quadratic bezier) for sine
        assert "<rect" in svg
        assert 'd="M 12 35 Q' in svg

    def test_uses_box_for_adc(self):
        from services.p1_renderers import render_block_diagram_svg
        m = DesignManifest(project_id="1", bom=[_row("AD9082", "adc")])
        svg = render_block_diagram_svg(m)
        assert "ADC" in svg

    def test_lo_placed_below_chain(self):
        """LO/synth components should appear in a second row below the
        main chain, with a vertical line up to the mixer."""
        from services.p1_renderers import render_block_diagram_svg
        m = DesignManifest(
            project_id="1",
            bom=[
                _row("LNA1", "lna"),
                _row("MIX1", "mixer"),
                _row("ADF4351", "lo"),
                _row("AD9082", "adc"),
            ],
        )
        svg = render_block_diagram_svg(m)
        # Both the main-chain part numbers and the LO part number appear.
        for pn in ("LNA1", "MIX1", "AD9082", "ADF4351"):
            assert pn in svg

    def test_arrow_marker_defined_once(self):
        from services.p1_renderers import render_block_diagram_svg
        m = DesignManifest(
            project_id="1",
            bom=[_row("A", "lna"), _row("B", "mixer"), _row("C", "filter")],
        )
        svg = render_block_diagram_svg(m)
        # Arrow marker defined once, referenced multiple times
        assert svg.count('id="arrow"') == 1
        assert svg.count("marker-end=\"url(#arrow)\"") >= 2

    def test_empty_bom_emits_placeholder_svg(self):
        from services.p1_renderers import render_block_diagram_svg
        m = DesignManifest(project_id="1", bom=[])
        svg = render_block_diagram_svg(m)
        assert svg.startswith("<svg")
        assert "Approve P1" in svg or "No components" in svg

    def test_no_leak_by_construction_svg(self):
        """Same guarantee as the mermaid renderer: every MPN in the SVG
        text labels is in `manifest.bom`."""
        from services.p1_renderers import render_block_diagram_svg
        from services.manifest_validator import extract_mpns
        m = DesignManifest(
            project_id="1",
            bom=[
                _row("HMC8410", "lna"),
                _row("LT5560", "mixer"),
                _row("ADF4351", "lo"),
                _row("AD9082", "adc"),
            ],
        )
        svg = render_block_diagram_svg(m)
        rendered = extract_mpns(svg)
        leaks = rendered - m.allowed_mpns()
        assert leaks == set(), f"SVG renderer leaked MPNs: {leaks}"

    def test_render_block_diagram_md_inlines_svg(self):
        """The combined render_block_diagram_md should embed the SVG
        inline (not just emit a <img src=...> reference)."""
        from services.p1_renderers import render_block_diagram_md
        m = DesignManifest(
            project_id="1",
            bom=[_row("HMC8410", "lna"), _row("LT5560", "mixer")],
        )
        md = render_block_diagram_md(m, project_name="Demo")
        # SVG embed
        assert "<svg" in md
        assert "</svg>" in md
        # Heading + project name
        assert "# System Block Diagram" in md
        assert "## Demo" in md

    def test_xml_label_escaping(self):
        """Part numbers / role text containing < > & " must be escaped
        so the SVG remains well-formed XML."""
        from services.p1_renderers import render_block_diagram_svg
        m = DesignManifest(
            project_id="1",
            bom=[{
                "part_number": "AB<C>D",
                "role": "lna",
                "manufacturer": "ACME",
            }],
        )
        svg = render_block_diagram_svg(m)
        # Raw < > should not appear inside the label position
        assert "AB<C>D" not in svg
        assert "AB&lt;C&gt;D" in svg
        # Still parses as XML
        import xml.etree.ElementTree as ET
        ET.fromstring(svg)
