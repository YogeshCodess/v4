"""
ProjectBrief builder - walks an existing project's P1-P7a outputs and
extracts a structured `ProjectBrief` from them.

This is the OPPOSITE direction from how the agents currently work today:
they each re-read the markdown and re-extract specs ad-hoc. This module
extracts ONCE, returns a typed object, and every downstream agent uses
the typed object.

The extractor is intentionally tolerant of partial / malformed inputs -
P1 might not have completed yet, P7a's register table might have weird
formatting, etc. Missing fields stay None / empty list rather than
raising. Every field that DOES populate uses regex patterns chosen to
match the actual output format from the corresponding agent.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from schemas.project_brief import FSM, Peripheral, ProjectBrief, Register

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Field extractors (each one tolerates missing input)
# ---------------------------------------------------------------------------


_FREQ_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:-|to|–|—)\s*(\d+(?:\.\d+)?)\s*(GHz|MHz|Mhz|gHz|kHz)",
    re.IGNORECASE,
)
_BW_RE = re.compile(
    r"bandwidth[^0-9]{0,20}(\d+(?:\.\d+)?)\s*(MHz|GHz|kHz)",
    re.IGNORECASE,
)
_NF_RE   = re.compile(r"(?:noise\s*figure|NF)[^0-9]{0,20}(\d+(?:\.\d+)?)\s*dB", re.IGNORECASE)
_GAIN_RE = re.compile(r"(?:total[^a-z]*gain|system\s*gain)[^0-9]{0,20}(\d+(?:\.\d+)?)\s*dB", re.IGNORECASE)
_POUT_RE = re.compile(r"(?:Pout|output\s*power)[^0-9]{0,20}(\d+(?:\.\d+)?)\s*dBm", re.IGNORECASE)
_ARCH_RE = re.compile(
    r"(superheterodyne|direct[-\s]conversion|low[-\s]?if|zero[-\s]?if|"
    r"image[-\s]reject|crystal\s*video|tuned\s*rf|sdr|direct\s*rf\s*sampling|"
    r"subsampling|undersampling|channelized|microscan|"
    r"dual[-\s]conversion|multi[-\s]conversion)",
    re.IGNORECASE,
)
_REG_ROW_RE = re.compile(
    r"^\|\s*`?(0x[0-9A-Fa-f]{2,4})`?\s*\|\s*`?([A-Z][A-Z0-9_]+)`?\s*\|"
    r"\s*([A-Z0-9/]+|—|-)\s*\|\s*`?(0x[0-9A-Fa-f]+|-|—)`?\s*\|\s*([^|]*)\|",
    re.MULTILINE,
)
# Peripheral patterns - we match buses by keyword density in component_recommendations
_BUS_KEYWORDS = {
    "spi":  ["spi", "mosi", "miso", "sclk", "ss_n"],
    "i2c":  ["i2c", "sda", "scl"],
    "uart": ["uart", "rs-232", "rs232", "rs-485", "rs485"],
    "gpio": ["gpio", "general purpose i/o"],
    "jtag": ["jtag", "tdi", "tdo"],
    "pcie": ["pcie", "pci express"],
    "adc":  ["adc", "data converter", "digitizer"],
    "dac":  ["dac"],
    "pwm":  ["pwm"],
    "lvds": ["lvds"],
}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return ""


def _first_match(rx: re.Pattern, text: str) -> Optional[re.Match]:
    return rx.search(text) if text else None


def _to_ghz(value: float, unit: str) -> float:
    u = unit.lower()
    if u == "ghz":
        return value
    if u == "mhz":
        return value / 1000.0
    if u == "khz":
        return value / 1_000_000.0
    return value


# ---------------------------------------------------------------------------
# Per-document extractors
# ---------------------------------------------------------------------------


_CLOCK_PATTERNS = [
    # "70 MHz system clock", "100 MHz clock", "Clock: 70 MHz", "fclk = 70 MHz"
    re.compile(r"(?:fpga|system|master|core|sample|sampling|reference|module|main)\s*clock[^\d]{0,40}(\d+(?:\.\d+)?)\s*MHz", re.IGNORECASE),
    re.compile(r"clock\s*(?:frequency|freq|rate|=|:)\s*[^\d]{0,20}(\d+(?:\.\d+)?)\s*MHz", re.IGNORECASE),
    re.compile(r"(?:fclk|clk_freq|f_clk|clk_mhz)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*MHz", re.IGNORECASE),
    # "@ 70 MHz" or "running at 70 MHz"
    re.compile(r"(?:running|operates|operating|run)\s*(?:at|@)?\s*(\d+(?:\.\d+)?)\s*MHz", re.IGNORECASE),
    # As a standalone, "70 MHz clock" anywhere in the doc
    re.compile(r"\b(\d+(?:\.\d+)?)\s*MHz\s+clock\b", re.IGNORECASE),
]


def _extract_clock_mhz(text: str) -> float | None:
    """Return the system clock frequency declared in `text`, in MHz.

    Walks `_CLOCK_PATTERNS` in priority order. Filters out implausible
    values - anything below 1 MHz (probably a kHz reference) or above
    1 GHz (probably an RF carrier mistakenly labelled "clock") is
    rejected.
    """
    if not text:
        return None
    for rx in _CLOCK_PATTERNS:
        m = rx.search(text)
        if m:
            try:
                v = float(m.group(1))
            except ValueError:
                continue
            if 1.0 <= v <= 1000.0:
                return v
    return None


def _extract_rf_specs(req_text: str, brief: ProjectBrief) -> None:
    """Pull frequency / BW / NF / gain / Pout / architecture from requirements.md."""
    m = _first_match(_FREQ_RANGE_RE, req_text)
    if m:
        lo, hi, unit = float(m.group(1)), float(m.group(2)), m.group(3)
        brief.frequency_min_ghz = _to_ghz(lo, unit)
        brief.frequency_max_ghz = _to_ghz(hi, unit)
    m = _first_match(_BW_RE, req_text)
    if m:
        v, unit = float(m.group(1)), m.group(2).lower()
        brief.bandwidth_mhz = v if unit == "mhz" else (v * 1000 if unit == "ghz" else v / 1000)
    m = _first_match(_NF_RE, req_text)
    if m:
        brief.target_nf_db = float(m.group(1))
    m = _first_match(_GAIN_RE, req_text)
    if m:
        brief.target_gain_db = float(m.group(1))
    m = _first_match(_POUT_RE, req_text)
    if m:
        brief.target_pout_dbm = float(m.group(1))
    m = _first_match(_ARCH_RE, req_text)
    if m:
        brief.architecture = m.group(1).lower().replace("‐", "-").replace("–", "-")


def _extract_peripherals(comp_text: str, glr_text: str, brief: ProjectBrief) -> None:
    """STRICT BOM-only peripheral filter (2026-05-02).

    Only adds a peripheral when its bus keyword AND a real part number
    appear together on the same line of `component_recommendations.md`.
    GLR text is intentionally ignored - it's documentation, not the
    source of truth for what was actually selected by the user.

    Result: a project that selected UART + I2C + SPI + ADC will only
    get those four peripherals, never a stray DAC / JTAG / LVDS that
    leaked from a GLR keyword mention.
    """
    if not comp_text:
        return
    addr_idx_per_bus: dict[str, int] = {}
    seen_keys: set[tuple[str, str]] = set()  # (bus, mpn) dedup
    for line in comp_text.splitlines():
        line_l = line.lower()
        # Find the part number first (uppercase token with at least one digit).
        mpn_m = re.search(r"\b[A-Z][A-Z0-9_-]{3,}[0-9][A-Z0-9_-]*\b", line)
        if not mpn_m:
            continue
        mpn = mpn_m.group(0)
        # Now check which bus(es) the line mentions.
        line_buses: list[str] = []
        for bus, kws in _BUS_KEYWORDS.items():
            if any(kw in line_l for kw in kws):
                line_buses.append(bus)
        # Implicit-bus fallback: if a part is described by its function
        # but not by its control bus, infer from the function keyword.
        # PLL / synthesizer / clock distribution / clock cleaner -> SPI
        # Power monitor / IO expander / sensor -> I2C
        if not line_buses:
            for kw, implied_bus in (
                ("pll", "spi"), ("synth", "spi"),
                ("clock distribution", "spi"), ("clock dist", "spi"),
                ("clock cleaner", "spi"), ("vco", "spi"),
                ("rf synth", "spi"),
                ("io expander", "i2c"), ("i/o expander", "i2c"),
                ("power monitor", "i2c"), ("temp sensor", "i2c"),
                ("sensor", "i2c"),
            ):
                if kw in line_l:
                    line_buses.append(implied_bus)
                    break
        if not line_buses:
            continue
        # Add one peripheral per (bus, MPN) pair, deduped.
        # Look for a datasheet URL on the same BOM line so the resolver
        # can call the LLM datasheet extractor when family inference is
        # weak or absent.
        url_m = re.search(r"https?://\S+", line)
        bom_url = url_m.group(0).rstrip(",.)") if url_m else ""
        for bus in line_buses:
            key = (bus, mpn)
            if key in seen_keys or len(brief.peripherals) >= 16:
                continue
            seen_keys.add(key)
            addr = addr_idx_per_bus.get(bus, 0)
            addr_idx_per_bus[bus] = addr + 1
            # Resolve a ComponentSpec for this peripheral so RTL emitters
            # downstream can parameterise on real datasheet values.
            spec_dict = None
            try:
                from services.component_spec_resolver import resolve
                resolved = resolve(mpn, hint_bus=bus, datasheet_url=bom_url)
                spec_dict = resolved.model_dump(exclude_none=True)
            except Exception as _spec_err:
                log.warning("component_spec.resolve_failed mpn=%s: %s", mpn, _spec_err)
            brief.peripherals.append(
                Peripheral(name=mpn, bus=bus, address=str(addr), spec=spec_dict)
            )


def _extract_registers_json(reg_json_path, brief: ProjectBrief) -> bool:
    """Prefer the structured register_map.json emitted by P7a (2026-05-02).

    Returns True if the JSON was found AND parsed (so the caller can skip
    the markdown fallback). Tolerant of malformed JSON - returns False on
    any parse failure so the markdown fallback can have a go.
    """
    try:
        if not reg_json_path.exists():
            return False
        data = json.loads(reg_json_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return False
    regs = data.get("registers") if isinstance(data, dict) else None
    if not isinstance(regs, list):
        return False
    for r in regs:
        if not isinstance(r, dict):
            continue
        name = (r.get("name") or "").strip()
        addr = (r.get("address") or "").strip()
        if not name or not addr:
            continue
        # access can be a top-level field OR derived from per-field access.
        access = (r.get("access") or "").strip().upper()
        if not access and isinstance(r.get("fields"), list):
            field_acc = {(f.get("access") or "").upper() for f in r["fields"]}
            access = "RW" if "RW" in field_acc or "W" in field_acc else "RO"
        access = access or "RW"
        brief.registers.append(Register(
            name=name,
            address=addr,
            access=access,
            reset_value=r.get("reset_value"),
            description=(r.get("description") or "").strip(),
        ))
    return True


def _extract_registers(rdt_text: str, brief: ProjectBrief) -> None:
    """Walk the markdown register table from P7a register_description_table.md."""
    if not rdt_text:
        return
    for m in _REG_ROW_RE.finditer(rdt_text):
        addr, name, access, reset, desc = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        if name in {"Address", "REG"} or addr.lower() == "0x????":
            continue
        brief.registers.append(Register(
            name=name,
            address=addr,
            access=access if access not in ("—", "-") else "RW",
            reset_value=reset if reset not in ("—", "-") else None,
            description=desc.strip(),
        ))


def _extract_fsms(glr_text: str, brief: ProjectBrief) -> None:
    """Pull FSM names + state lists from GLR markdown.

    GLR convention: a FSM section has '### FSM_NAME' or '## State Machine: FOO',
    and the states are bullet-list items underneath."""
    if not glr_text:
        return
    fsm_block = re.compile(
        r"(?:^|\n)#{2,3}\s*(?:State[\s_-]?Machine[:\s]+)?([A-Z][A-Z0-9_]+)\s*\n([^#]+)",
        re.MULTILINE,
    )
    for m in fsm_block.finditer(glr_text):
        name = m.group(1)
        if not name.endswith("_FSM") and "FSM" not in name:
            continue
        body = m.group(2)
        states: list[str] = []
        for line in body.splitlines():
            line_s = line.strip()
            if line_s.startswith(("-", "*")) and len(line_s) < 80:
                tok = re.search(r"\b(IDLE|ARM|CAPTURE|RUN|WAIT|DONE|ERROR|RESET|INIT|"
                                r"[A-Z][A-Z0-9_]{2,30})\b", line_s)
                if tok:
                    states.append(tok.group(1))
        if states:
            brief.fsms.append(FSM(name=name, states=states[:8]))


def _extract_compliance(req_text: str, brief: ProjectBrief) -> None:
    targets = []
    for label in ("MIL-STD-810", "MIL-STD-461", "ITAR", "RoHS", "REACH",
                  "FCC", "CE", "DO-178", "IEC 60601", "ISO 26262", "TEMPEST"):
        if label.lower() in req_text.lower():
            targets.append(label)
    brief.compliance_targets = targets


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_project_brief(
    *,
    project_id: int,
    project_name: str,
    output_dir: str,
    project_type: str = "receiver",
    design_scope: str = "full",
    application_class: str = "general",
    hdl_language: str = "verilog",
    clock_frequency_mhz: float = 100.0,
    fpga_part: str = "xc7a35tcpg236-1",
) -> ProjectBrief:
    """Read whatever P1-P7a outputs exist in `output_dir` and assemble a brief.

    Missing files are tolerated - the brief just keeps the corresponding
    fields at their defaults. Every code-gen agent calls this at the top of
    its execute() and prepends `to_prompt_preamble()` to the LLM system
    message.
    """
    out = Path(output_dir)
    req_text  = _read(out / "requirements.md")
    arch_text = _read(out / "architecture.md")
    comp_text = _read(out / "component_recommendations.md")
    glr_text  = _read(out / "glr_specification.md") or _read(out / "GLR.md")
    rdt_text  = _read(out / "register_description_table.md")
    reg_json_path = out / "register_map.json"

    brief = ProjectBrief(
        project_id=project_id,
        project_name=project_name,
        project_type=project_type,
        design_scope=design_scope,
        application_class=application_class,
        hdl_language=hdl_language,
        clock_frequency_mhz=clock_frequency_mhz,
        fpga_part=fpga_part,
    )

    _extract_rf_specs(req_text + "\n" + arch_text, brief)
    _extract_peripherals(comp_text, glr_text, brief)
    if not _extract_registers_json(reg_json_path, brief):
        _extract_registers(rdt_text, brief)
    _extract_fsms(glr_text, brief)

    # Clock frequency: GLR is most authoritative (the agent that defined
    # the FPGA timing), then HRS, then requirements. Fall back to the
    # default in the brief when nothing matched.
    _hrs_safe = "HRS_" + project_name.replace(" ", "_") + ".md"
    for src_text in (glr_text, _read(out / _hrs_safe), _read(out / "hrs.md"),
                     arch_text, req_text, comp_text):
        _clk = _extract_clock_mhz(src_text)
        if _clk is not None:
            brief.clock_frequency_mhz = _clk
            break
    _extract_compliance(req_text, brief)

    # Component count is a useful-enough signal even if the structured list
    # didn't fully parse - count markdown bullet lines or table rows.
    if comp_text:
        # Either bullet lines or table rows
        n = len(re.findall(r"^\s*\|\s*\d+\s*\|", comp_text, re.MULTILINE))
        if n == 0:
            n = sum(1 for line in comp_text.splitlines() if line.strip().startswith("-"))
        brief.component_count = n

    log.info(
        "project_brief.built project_id=%s fingerprint=%s freq=%s-%s GHz "
        "arch=%s peripherals=%d registers=%d fsms=%d",
        project_id, brief.fingerprint(),
        brief.frequency_min_ghz, brief.frequency_max_ghz,
        brief.architecture or "-",
        len(brief.peripherals), len(brief.registers), len(brief.fsms),
    )
    return brief
