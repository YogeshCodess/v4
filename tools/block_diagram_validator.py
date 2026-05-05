"""
Block-diagram topology validator — P0.1.

Given a Mermaid flowchart (the LLM's emitted block diagram) and the
architecture the user selected in the v21 wizard, assert the topology
actually matches the architecture. Without this check, the LLM can emit
a "Superheterodyne" diagram with no mixer, or a "Direct RF Sampling"
diagram with no ADC, and nothing in the pipeline catches it.

This is topology-only — the cascade validator already covers the
numbers. Here we parse the Mermaid graph into a DAG of labelled nodes
and run a small rules engine per architecture.

Return shape (always a list, empty means OK):

    [{"severity": "critical"|"high"|"medium",
      "category":  "topology",
      "detail":    "human-readable explanation",
      "suggested_fix": "what to change",
      "architecture": "superhet_single"|...}]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# Node keyword → canonical role
# ---------------------------------------------------------------------------
#
# Each role is a set of case-insensitive substrings. A node whose label
# contains ANY of these substrings is classified as that role. Order matters
# for ambiguous labels (e.g. "LNA Mixer Module" hits LNA first via the more
# specific keyword "lna").

ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "antenna":     ("antenna", "ant ", " ant", "aerial"),
    "limiter":     ("limiter", "pin diode", "pin-diode"),
    "preselector": ("preselector", "pre-select", "preselect", "band-pass",
                    "bandpass", "bpf", "rf filter", "pre-filter"),
    "lna":         ("lna", "low-noise", "low noise amp", "low noise amplifier"),
    "balanced_lna":("balanced lna", "balanced amp"),
    "mixer":       ("mixer", "downconvert", "downconversion", "demod mixer",
                    "i/q mixer", "iq mixer", "quadrature mixer",
                    "upconvert", "upconversion", "up-convert", "modulator mixer"),
    "lo":          ("local oscillator", " lo ", "lo input", "lo port",
                    "synthesizer", "pll", "synth"),
    "if_filter":   ("if filter", "if bpf", "if select", "channel filter",
                    "saw filter", "crystal filter"),
    "if_amp":      ("if amp", "if amplifier", "if gain"),
    "baseband_lpf":("baseband", "bb filter", "low-pass", "lpf"),
    "adc":         ("adc", "analog-to-digital", "analog to digital", "digitiser",
                    "digitizer", "sampler"),
    "dac":         ("dac", "digital-to-analog", "digital to analog",
                    "rf dac", "waveform gen"),
    "clock":       ("clock", "sample clock", "tcxo", "ocxo", "reference osc"),
    "fpga":        ("fpga", "dsp", "fpga/dsp", "processor"),
    "detector":    ("crystal video", "log video", "log detector", "power detector",
                    "schottky detector"),
    "filter_bank": ("filter bank", "polyphase", "channelized", "channelised",
                    "fft bank"),
    "t_r_switch":  ("t/r switch", "tr switch", "t r switch", "transmit/receive"),
    # TX-side roles
    "iq_modulator":("iq modulator", "i/q modulator", "quadrature modulator",
                    "iq mod"),
    "predriver":   ("predriver", "pre-driver", "pre driver", "gain block"),
    "driver":      ("driver amp", "drv amp", "driver", " drv ", "driver stage",
                    "va driver", "pa driver"),
    "power_amp":   ("power amplifier", "power amp", " pa ", " pa/", "class-a",
                    "class-ab", "class a/ab", "class ab", "class-c", "class-e",
                    "class-f", "doherty", "hpa", "spa", "sspa"),
    "harmonic_filter": ("harmonic filter", "harmonic reject", "harm filt",
                        "output filter", "anti-harmonic", "lowpass filter",
                        "lpf output"),
    "isolator":    ("isolator", "circulator", "ferrite isolator"),
    "coupler":     ("coupler", "directional coupler", "bidirectional coupler",
                    "tap", "output tap"),
    "bias_tee":    ("bias tee", "bias-tee", "biastee"),
    "output":      ("output", "baseband out", "iq out", "to host", "data out",
                    "rf out", "antenna feed"),
}

# Architectures that fall under the "downconversion" umbrella.
# If the user picks one of these, we require a mixer + LO pair.
_DOWNCONVERSION_ARCHS = {
    "superhet_single", "superhet_double",
    "direct_conversion", "low_if", "image_reject",
}

# Architectures that fall under the "digital back-end" umbrella.
_DIGITAL_ARCHS = {
    "direct_rf_sample", "subsampling", "digital_if", "channelized",
}

# Front-end-only architectures (no mixer, no ADC expected).
_FRONT_END_ARCHS = {
    "std_lna_filter", "balanced_lna", "lna_filter_limiter",
    "active_antenna", "multi_band_switched",
}

# Detector-only special topologies.
_DETECTOR_ARCHS = {"crystal_video", "log_video"}

# Transmitter architecture groups (mirror rfArchitect.ts.category).
_TX_LINEAR_ARCHS = {
    "tx_driver_pa_classab", "tx_doherty", "tx_dpd_linearized",
}
_TX_SATURATED_ARCHS = {
    "tx_class_c_pulsed", "tx_pulse_radar",
}
_TX_UPCONVERT_ARCHS = {
    "tx_iq_mod_upconvert", "tx_superhet_upconvert", "tx_direct_dac",
}
_ALL_TX_ARCHS = _TX_LINEAR_ARCHS | _TX_SATURATED_ARCHS | _TX_UPCONVERT_ARCHS
_ALL_TX_ARCHS |= {"tx_recommend"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class Violation:
    severity: str
    category: str
    detail: str
    suggested_fix: str
    architecture: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "category": self.category,
            "detail": self.detail,
            "suggested_fix": self.suggested_fix,
            "architecture": self.architecture,
        }


@dataclass
class ParsedDiagram:
    """Outcome of `parse_mermaid` — nodes with classified roles + edges."""
    nodes: list["Node"] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)

    def roles(self) -> set[str]:
        """Flat set of all roles detected in any node."""
        out: set[str] = set()
        for n in self.nodes:
            out.update(n.roles)
        return out

    def node_by_id(self, node_id: str) -> Optional["Node"]:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None


@dataclass
class Node:
    node_id: str
    label: str
    roles: set[str]


def parse_mermaid(mermaid: str) -> ParsedDiagram:
    """Extract nodes + edges from a Mermaid flowchart. Best-effort — we
    don't parse every Mermaid feature, just the flowchart shapes that the
    RF-design prompts emit: `id[label]`, `id(label)`, `id-->id2`,
    `id -->|edge label| id2`."""
    diagram = ParsedDiagram()
    if not mermaid:
        return diagram

    # Find every "ID[Label]" / "ID(Label)" / "ID{Label}" declaration.
    node_re = re.compile(
        r"(?P<id>[A-Za-z_][\w-]*)\s*(?:\[(?P<b>[^\]]+)\]|\((?P<p>[^)]+)\)|\{(?P<c>[^}]+)\})"
    )
    seen_ids: set[str] = set()
    for m in node_re.finditer(mermaid):
        nid = m.group("id")
        if nid in seen_ids:
            continue
        seen_ids.add(nid)
        label = (m.group("b") or m.group("p") or m.group("c") or "").strip()
        diagram.nodes.append(Node(
            node_id=nid,
            label=label,
            roles=_classify_label(label),
        ))

    # Fallback: bare identifiers with no brackets (e.g., `A --> B`) still
    # represent nodes. Capture them with empty labels so they don't vanish.
    # The source/target identifier may be followed by an inline label
    # declaration (`A[LNA]`, `B(Mixer)`, `C{Foo}`) before the arrow; skip
    # over that so the edge is still captured.
    edge_re = re.compile(
        r"(?P<s>[A-Za-z_][\w-]*)"
        r"(?:\s*(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\}))?"
        r"\s*-->"
        r"(?:\s*\|[^|]*\|)?"
        r"\s*"
        r"(?P<t>[A-Za-z_][\w-]*)"
    )
    for m in edge_re.finditer(mermaid):
        s, t = m.group("s"), m.group("t")
        for nid in (s, t):
            if nid not in seen_ids:
                seen_ids.add(nid)
                diagram.nodes.append(Node(node_id=nid, label="", roles=set()))
        diagram.edges.append((s, t))

    return diagram


def _classify_label(label: str) -> set[str]:
    if not label:
        return set()
    l = label.lower()
    out: set[str] = set()
    for role, keywords in ROLE_KEYWORDS.items():
        if any(kw in l for kw in keywords):
            out.add(role)
    return out


# ---------------------------------------------------------------------------
# Architecture rules
# ---------------------------------------------------------------------------

def validate(mermaid: str, architecture: Optional[str],
             *, application: Optional[str] = None) -> list[Violation]:
    """Return a list of violations for the given diagram + architecture.

    `architecture` is one of the IDs from
    `hardware-pipeline-v5-react/src/data/rfArchitect.ts::ALL_ARCHITECTURES`
    (e.g. "superhet_single"), or None when the wizard hasn't chosen yet.
    """
    diagram = parse_mermaid(mermaid)
    if not diagram.nodes:
        return [Violation(
            severity="critical", category="topology",
            detail="Block diagram has no recognisable nodes.",
            suggested_fix=(
                "Re-emit a Mermaid `flowchart` with component nodes such as "
                "`LNA[LNA]`, `MIX[Mixer]`, `ADC[ADC]`."
            ),
            architecture=architecture,
        )]

    violations: list[Violation] = []
    arch = (architecture or "").strip().lower()
    is_tx = arch in _ALL_TX_ARCHS

    # -------------------------------------------------------------- common
    if is_tx:
        # TX: require at least one PA (or driver) — that sets the Pout floor.
        has_pa = any("power_amp" in n.roles for n in diagram.nodes)
        has_driver = any({"driver", "predriver"} & n.roles for n in diagram.nodes)
        if not has_pa and not has_driver:
            violations.append(Violation(
                severity="critical", category="topology",
                detail=(
                    "No PA or driver found in the block diagram. Every "
                    "transmitter needs at least a driver/PA to reach the "
                    "target output power."
                ),
                suggested_fix="Insert a driver → PA stage before the antenna feed.",
                architecture=architecture,
            ))
    else:
        # RX: an LNA must exist — it sets the noise floor.
        has_lna = any({"lna", "balanced_lna"} & n.roles for n in diagram.nodes)
        has_detector = any("detector" in n.roles for n in diagram.nodes)
        if not has_lna and not has_detector:
            violations.append(Violation(
                severity="critical", category="topology",
                detail=(
                    "No LNA (or detector) found in the block diagram. Every "
                    "receiver chain sets its noise floor in the first active "
                    "stage — without an LNA, Friis sensitivity is meaningless."
                ),
                suggested_fix="Insert an LNA as the first active stage after the antenna / preselector.",
                architecture=architecture,
            ))

        # Preselector ought to come before the LNA so it attenuates out-of-band
        # interferers without the LNA desensitising on them.
        if has_lna and _has_preselector(diagram):
            if not _preselector_before_lna(diagram):
                violations.append(Violation(
                    severity="high", category="topology",
                    detail=(
                        "A preselector / RF band-pass filter exists but does not "
                        "precede the LNA. Out-of-band interferers will reach the "
                        "LNA and reduce effective SFDR."
                    ),
                    suggested_fix="Route the preselector BPF between the antenna and the LNA.",
                    architecture=architecture,
                ))

    # -------------------------------------------------------- per-architecture

    if arch in _DOWNCONVERSION_ARCHS:
        violations += _check_downconversion(diagram, arch)
    elif arch in _DIGITAL_ARCHS:
        violations += _check_digital(diagram, arch)
    elif arch in _FRONT_END_ARCHS:
        violations += _check_front_end(diagram, arch)
    elif arch in _DETECTOR_ARCHS:
        violations += _check_detector(diagram, arch)
    elif arch in _TX_LINEAR_ARCHS:
        violations += _check_tx_linear(diagram, arch)
    elif arch in _TX_SATURATED_ARCHS:
        violations += _check_tx_saturated(diagram, arch)
    elif arch in _TX_UPCONVERT_ARCHS:
        violations += _check_tx_upconversion(diagram, arch)
    elif arch and arch not in ("recommend", "tx_recommend"):
        # Unknown architecture — warn but don't block.
        violations.append(Violation(
            severity="medium", category="topology",
            detail=f"Unknown architecture id '{architecture}' — topology rules not applied.",
            suggested_fix="Pass one of the v21 wizard architecture IDs.",
            architecture=architecture,
        ))

    return violations


# ---------------------------------------------------------------------------
# Per-family checks
# ---------------------------------------------------------------------------

def _check_downconversion(diag: ParsedDiagram, arch: str) -> list[Violation]:
    out: list[Violation] = []
    roles = diag.roles()

    if "mixer" not in roles:
        out.append(Violation(
            severity="critical", category="topology",
            detail=f"Architecture '{arch}' requires a mixer but none was found.",
            suggested_fix="Add a mixer node (e.g. MIX[Mixer]) between the RF and IF stages.",
            architecture=arch,
        ))
    if "lo" not in roles:
        out.append(Violation(
            severity="critical", category="topology",
            detail=f"Architecture '{arch}' requires a local oscillator / synthesizer driving the mixer.",
            suggested_fix="Add an LO / PLL node feeding the mixer's LO port.",
            architecture=arch,
        ))

    # Double-conversion architectures need TWO mixers.
    if arch == "superhet_double":
        mixer_count = sum(1 for n in diag.nodes if "mixer" in n.roles)
        if mixer_count < 2:
            out.append(Violation(
                severity="critical", category="topology",
                detail=(
                    f"'superhet_double' requires two mixer stages (RF→IF1 and IF1→IF2). "
                    f"Found {mixer_count}."
                ),
                suggested_fix="Add a second mixer and its LO to achieve two-stage downconversion.",
                architecture=arch,
            ))

    # Superhet variants need at least one IF filter.
    if arch in {"superhet_single", "superhet_double"} and "if_filter" not in roles:
        out.append(Violation(
            severity="high", category="topology",
            detail=f"'{arch}' is missing an IF channel-select filter after the mixer.",
            suggested_fix="Insert an IF SAW / crystal / LC filter on the mixer output.",
            architecture=arch,
        ))

    return out


def _check_digital(diag: ParsedDiagram, arch: str) -> list[Violation]:
    out: list[Violation] = []
    roles = diag.roles()

    if "adc" not in roles:
        out.append(Violation(
            severity="critical", category="topology",
            detail=f"Architecture '{arch}' requires an ADC but none was found.",
            suggested_fix="Add an ADC node driving the FPGA / DSP backend.",
            architecture=arch,
        ))
    if "clock" not in roles:
        out.append(Violation(
            severity="high", category="topology",
            detail=(
                f"Architecture '{arch}' needs a sample clock (TCXO / OCXO / PLL) "
                "feeding the ADC — clock phase-noise dominates digitised SFDR."
            ),
            suggested_fix="Add a CLK / TCXO node feeding the ADC sample input.",
            architecture=arch,
        ))

    if arch == "digital_if":
        if "mixer" not in roles:
            out.append(Violation(
                severity="high", category="topology",
                detail="'digital_if' requires an analog mixer stage before the ADC.",
                suggested_fix="Add a mixer between the LNA and the IF path into the ADC.",
                architecture=arch,
            ))
    elif arch == "direct_rf_sample":
        if "mixer" in roles:
            out.append(Violation(
                severity="medium", category="topology",
                detail=(
                    "'direct_rf_sample' should NOT have an analog mixer — the whole "
                    "point is to digitise RF directly. Review the topology label."
                ),
                suggested_fix="Remove the mixer node or change the architecture selection.",
                architecture=arch,
            ))
    elif arch == "channelized":
        if "filter_bank" not in roles:
            out.append(Violation(
                severity="high", category="topology",
                detail=(
                    "'channelized' requires a polyphase / FFT filter bank across "
                    "the ADC output. None was found."
                ),
                suggested_fix="Add a filter-bank node (polyphase FFT) between the ADC and the DSP.",
                architecture=arch,
            ))

    return out


def _check_front_end(diag: ParsedDiagram, arch: str) -> list[Violation]:
    out: list[Violation] = []
    roles = diag.roles()

    # Front-end designs must NOT show a mixer or ADC — out-of-scope.
    if "mixer" in roles:
        out.append(Violation(
            severity="medium", category="topology",
            detail=(
                f"Front-end-only architecture '{arch}' should not contain a "
                "mixer — downconversion is explicitly out of scope."
            ),
            suggested_fix="Remove the mixer or switch the architecture to a downconversion variant.",
            architecture=arch,
        ))
    if "adc" in roles:
        out.append(Violation(
            severity="medium", category="topology",
            detail=(
                f"Front-end-only architecture '{arch}' should not contain an "
                "ADC — digitisation is explicitly out of scope."
            ),
            suggested_fix="Remove the ADC node or switch the architecture to digital_if / direct_rf_sample.",
            architecture=arch,
        ))

    if arch == "lna_filter_limiter" and "limiter" not in roles:
        out.append(Violation(
            severity="high", category="topology",
            detail="'lna_filter_limiter' requires a PIN-diode limiter node — none found.",
            suggested_fix="Add a limiter between the antenna and the LNA for survivability.",
            architecture=arch,
        ))
    return out


def _check_tx_linear(diag: ParsedDiagram, arch: str) -> list[Violation]:
    """Linear PA chains — Class A/AB, Doherty, DPD.
    Require: driver → PA → harmonic filter → (coupler) → antenna.
    No LNA/mixer/ADC (if present, flag — this is a transmitter).
    """
    out: list[Violation] = []
    roles = diag.roles()

    if "power_amp" not in roles and "driver" not in roles:
        out.append(Violation(
            severity="critical", category="topology",
            detail=f"TX architecture '{arch}' requires a driver or PA node — none found.",
            suggested_fix="Add a driver and a final-stage PA before the output filter.",
            architecture=arch,
        ))

    if "harmonic_filter" not in roles and "preselector" not in roles:
        out.append(Violation(
            severity="high", category="topology",
            detail=(
                f"TX architecture '{arch}' lacks a harmonic / output filter. "
                "Every transmitter needs a post-PA low-pass or band-pass filter "
                "to meet the regulatory harmonic-emission mask."
            ),
            suggested_fix="Insert a harmonic / band-pass filter between the PA and the antenna.",
            architecture=arch,
        ))

    # DPD implies a baseband DAC + feedback path — flag when neither is present.
    if arch == "tx_dpd_linearized":
        if "dac" not in roles and "fpga" not in roles:
            out.append(Violation(
                severity="medium", category="topology",
                detail=(
                    "DPD-linearized PA requires a digital predistortion path "
                    "(DAC + FPGA/DSP). Neither is present in the diagram."
                ),
                suggested_fix="Add DAC + FPGA/DSP blocks feeding the modulator.",
                architecture=arch,
            ))

    # RX-side artefacts shouldn't appear in a pure TX chain.
    if "lna" in roles or "balanced_lna" in roles:
        out.append(Violation(
            severity="medium", category="topology",
            detail=(
                f"TX architecture '{arch}' shouldn't contain an LNA — that's "
                "a receiver component. If this is a transceiver, switch to a "
                "TRX topology (not yet wired)."
            ),
            suggested_fix="Remove the LNA or change the project to transceiver scope.",
            architecture=arch,
        ))
    return out


def _check_tx_saturated(diag: ParsedDiagram, arch: str) -> list[Violation]:
    """Saturated PA — Class C/E/F, radar pulsed.
    Tighter output filter requirement (harmonics are much worse in saturated
    operation) and isolator/circulator highly recommended.
    """
    out: list[Violation] = []
    roles = diag.roles()

    # Same PA-presence rule as linear.
    if "power_amp" not in roles and "driver" not in roles:
        out.append(Violation(
            severity="critical", category="topology",
            detail=f"TX architecture '{arch}' requires a driver or PA node — none found.",
            suggested_fix="Add a driver and a saturated PA.",
            architecture=arch,
        ))

    if "harmonic_filter" not in roles:
        out.append(Violation(
            severity="high", category="topology",
            detail=(
                f"Saturated TX architecture '{arch}' MUST include a harmonic "
                "filter. Class-C/E/F PAs produce strong H2/H3 that will fail "
                "regulatory spurious masks without post-PA filtering."
            ),
            suggested_fix="Insert a steep low-pass harmonic filter after the PA.",
            architecture=arch,
        ))

    if arch == "tx_pulse_radar" and "isolator" not in roles:
        out.append(Violation(
            severity="medium", category="topology",
            detail=(
                "Pulsed radar PA chain without an isolator/circulator. Mismatch "
                "at the antenna during pulse ring-down can load-pull the PA."
            ),
            suggested_fix="Insert a circulator (or isolator) between the PA and the antenna.",
            architecture=arch,
        ))
    return out


def _check_tx_upconversion(diag: ParsedDiagram, arch: str) -> list[Violation]:
    """TX upconversion front-ends — IQ modulator, superhet TX, direct-DAC."""
    out: list[Violation] = []
    roles = diag.roles()

    if arch == "tx_iq_mod_upconvert":
        if "iq_modulator" not in roles and "mixer" not in roles:
            out.append(Violation(
                severity="critical", category="topology",
                detail="IQ-modulator TX architecture requires an IQ-modulator / quadrature modulator node.",
                suggested_fix="Add an IQ modulator between the baseband DAC and the driver.",
                architecture=arch,
            ))
    if arch == "tx_superhet_upconvert":
        if "mixer" not in roles:
            out.append(Violation(
                severity="critical", category="topology",
                detail="Superhet TX requires a mixer + LO — no mixer found.",
                suggested_fix="Insert an up-convert mixer between the IF source and the driver.",
                architecture=arch,
            ))
        if "lo" not in roles:
            out.append(Violation(
                severity="high", category="topology",
                detail="Superhet TX requires a local oscillator / synthesizer — none found.",
                suggested_fix="Add a PLL/synth driving the mixer's LO port.",
                architecture=arch,
            ))
    if arch == "tx_direct_dac":
        if "dac" not in roles:
            out.append(Violation(
                severity="critical", category="topology",
                detail="Direct-DAC TX architecture requires an RF DAC node — none found.",
                suggested_fix="Add an RF DAC feeding the driver stage directly.",
                architecture=arch,
            ))

    # All upconversion topologies still need a PA + harmonic filter.
    if "power_amp" not in roles and "driver" not in roles:
        out.append(Violation(
            severity="high", category="topology",
            detail=f"TX architecture '{arch}' lacks a driver / PA — output will be below spec.",
            suggested_fix="Insert a driver → PA after the upconverter.",
            architecture=arch,
        ))
    if "harmonic_filter" not in roles and "preselector" not in roles:
        out.append(Violation(
            severity="medium", category="topology",
            detail=f"TX architecture '{arch}' lacks a harmonic / output filter.",
            suggested_fix="Add a post-PA band-pass filter before the antenna.",
            architecture=arch,
        ))
    return out


def _check_detector(diag: ParsedDiagram, arch: str) -> list[Violation]:
    out: list[Violation] = []
    roles = diag.roles()

    if "detector" not in roles:
        out.append(Violation(
            severity="critical", category="topology",
            detail=f"Architecture '{arch}' requires a detector node (crystal video / log video).",
            suggested_fix="Add the detector stage after the LNA / BPF.",
            architecture=arch,
        ))
    # Detectors are non-coherent → no mixer, no LO.
    if "mixer" in roles or "lo" in roles:
        out.append(Violation(
            severity="medium", category="topology",
            detail=(
                f"Architecture '{arch}' is a detector-only (non-coherent) "
                "receiver — mixer / LO stages are unexpected."
            ),
            suggested_fix="Remove mixer/LO or pick a different architecture.",
            architecture=arch,
        ))
    return out


# ---------------------------------------------------------------------------
# Preselector-ordering helper
# ---------------------------------------------------------------------------

def _has_preselector(diag: ParsedDiagram) -> bool:
    return any("preselector" in n.roles for n in diag.nodes)


def _preselector_before_lna(diag: ParsedDiagram) -> bool:
    """Return True if every preselector node has a path reaching an LNA node."""
    lnas = [n.node_id for n in diag.nodes if {"lna", "balanced_lna"} & n.roles]
    pres = [n.node_id for n in diag.nodes if "preselector" in n.roles]
    if not lnas or not pres:
        return True  # nothing to check

    # Build a forward-adjacency map once.
    adj: dict[str, set[str]] = {}
    for s, t in diag.edges:
        adj.setdefault(s, set()).add(t)

    def _reaches(src: str, targets: set[str]) -> bool:
        seen, stack = {src}, [src]
        while stack:
            cur = stack.pop()
            for nb in adj.get(cur, ()):
                if nb in targets:
                    return True
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        return False

    lna_set = set(lnas)
    return all(_reaches(p, lna_set) for p in pres)


# ---------------------------------------------------------------------------
# Convenience renderer
# ---------------------------------------------------------------------------

def format_violations(violations: Iterable[Violation]) -> str:
    """Produce a short markdown section for the audit report."""
    violations = list(violations)
    if not violations:
        return "_Block-diagram topology: passed all checks._"
    lines = ["### Block-diagram topology", "",
             "| Severity | Detail | Suggested fix |",
             "|---|---|---|"]
    for v in violations:
        detail = v.detail.replace("|", "\\|")
        fix = v.suggested_fix.replace("|", "\\|")
        lines.append(f"| {v.severity} | {detail} | {fix} |")
    return "\n".join(lines)
