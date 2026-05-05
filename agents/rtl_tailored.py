"""
rtl_tailored.py — project-tailored RTL emitter (Verilog or VHDL).

Replaces the static skeleton in fpga_agent. Key difference: every output
artifact derives from the ProjectBrief that was already extracted from
P1-P7a (HRS, netlist, GLR, register map, peripheral list, FSMs).

Two projects with different briefs produce textually different RTL:
  - Their entity / module ports differ (peripherals -> ports).
  - Their register-file case statements differ (P7a addresses + names).
  - Their FSM modules differ (GLR FSM names + state lists).
  - Their testbenches differ (per-register check vectors).

If the ProjectBrief is empty / partial, the emitter falls back to a
generic but still parameter-driven skeleton (clock freq / part number
still come from the brief).
"""
from __future__ import annotations

import re
from typing import Iterable

from schemas.project_brief import FSM, Peripheral, ProjectBrief, Register


# ---------------------------------------------------------------------------
# Language-agnostic helpers
# ---------------------------------------------------------------------------

_PIN_TABLE_RE = re.compile(
    # markdown row:   | port | direction | pin | std | net |
    r"^\|\s*`?([A-Za-z_][A-Za-z0-9_]*)`?\s*\|"
    r"(?:\s*\w+\s*\|)?"            # optional direction column
    r"\s*([A-Z]?\d{1,3}|[A-Z]{1,2}\d{1,3})\s*\|",
    re.MULTILINE,
)

_PIN_NETLIST_RE = re.compile(
    # JSON or free-form:  "port_name": "PIN_ID"   OR   port_name = PIN_ID
    r"[\"\']?([a-z][a-z0-9_]+)[\"\']?\s*[:=]\s*[\"\']?([A-Z]\d{1,3}|[A-Z]{1,2}\d{1,3})[\"\']?",
)


def extract_pin_map(netlist_text: str, glr_text: str) -> dict[str, str]:
    """Parse netlist + GLR for `port_name -> PACKAGE_PIN` assignments.

    Looks for both markdown pinout tables and free-form
    `name = PIN_ID` lines. Returns an empty dict if nothing is parseable
    (the emitter then falls back to the `<PIN>` placeholder for that
    port, but always with a clear comment that no mapping was found).
    """
    pin_map: dict[str, str] = {}
    for blob in (netlist_text or "", glr_text or ""):
        for m in _PIN_TABLE_RE.finditer(blob):
            port = m.group(1).lower()
            pin = m.group(2).upper()
            if port and pin and len(pin) <= 4:
                pin_map.setdefault(port, pin)
        for m in _PIN_NETLIST_RE.finditer(blob):
            port = m.group(1).lower()
            pin = m.group(2).upper()
            if port and pin and len(pin) <= 4:
                pin_map.setdefault(port, pin)
    return pin_map


_VHDL_KEYWORDS = {
    "abs", "access", "after", "alias", "all", "and", "architecture",
    "array", "assert", "attribute", "begin", "block", "body", "buffer",
    "bus", "case", "component", "configuration", "constant", "disconnect",
    "downto", "else", "elsif", "end", "entity", "exit", "file", "for",
    "function", "generate", "generic", "group", "guarded", "if", "impure",
    "in", "inertial", "inout", "is", "label", "library", "linkage",
    "literal", "loop", "map", "mod", "nand", "new", "next", "nor", "not",
    "null", "of", "on", "open", "or", "others", "out", "package",
    "port", "postponed", "procedure", "process", "pure", "range",
    "record", "register", "reject", "rem", "report", "return", "rol",
    "ror", "select", "severity", "signal", "shared", "sla", "sll",
    "sra", "srl", "subtype", "then", "to", "transport", "type",
    "unaffected", "units", "until", "use", "variable", "wait", "when",
    "while", "with", "xnor", "xor",
}

_VERILOG_KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "wire", "reg",
    "always", "begin", "end", "if", "else", "case", "endcase",
    "default", "posedge", "negedge", "assign", "parameter", "localparam",
    "function", "endfunction", "task", "endtask", "for", "while",
    "initial", "integer", "logic",
}


def _safe_id(name: str, lang: str = "vhdl") -> str:
    """Return a syntactically-safe identifier for the target HDL."""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", name or "x").strip("_")
    if not s or s[0].isdigit():
        s = "n_" + s
    s = s.lower()
    reserved = _VHDL_KEYWORDS if lang == "vhdl" else _VERILOG_KEYWORDS
    if s in reserved:
        s += "_sig"
    return s


def _addr_int(addr: str) -> int | None:
    """Parse '0xABCD' / 'ABCD' / decimal -> int. Returns None on failure."""
    if not addr:
        return None
    s = str(addr).strip().lower().replace("`", "")
    try:
        if s.startswith("0x"):
            return int(s, 16)
        if all(c in "0123456789abcdef" for c in s) and any(c in s for c in "abcdef"):
            return int(s, 16)
        return int(s)
    except ValueError:
        return None


def _peripheral_ports(peripherals: Iterable[Peripheral], lang: str) -> list[tuple[str, str, int, str]]:
    """Materialise (name, direction, width, comment) tuples per peripheral.

    Each peripheral contributes 1-4 ports depending on bus type. Names are
    derived from peripheral.name so two projects with different BOMs
    produce visibly different port lists.
    """
    ports: list[tuple[str, str, int, str]] = []
    seen: set[str] = set()

    def add(name: str, dirn: str, width: int, comment: str) -> None:
        if name in seen:
            return
        seen.add(name)
        ports.append((name, dirn, width, comment))

    for p in peripherals:
        slug = _safe_id(p.name, lang)
        bus = (p.bus or "generic").lower()
        if bus == "spi":
            add(f"{slug}_clk",  "output", 1,  f"SPI CLK -> {p.name}")
            add(f"{slug}_mosi", "output", 1,  f"SPI MOSI -> {p.name}")
            add(f"{slug}_miso", "input",  1,  f"SPI MISO <- {p.name}")
            add(f"{slug}_cs_n", "output", 1,  f"SPI CS#  -> {p.name}")
        elif bus == "i2c":
            add(f"{slug}_scl",  "inout",  1,  f"I2C SCL <-> {p.name}")
            add(f"{slug}_sda",  "inout",  1,  f"I2C SDA <-> {p.name}")
        elif bus == "uart":
            add(f"{slug}_txd",  "output", 1,  f"UART TX -> {p.name}")
            add(f"{slug}_rxd",  "input",  1,  f"UART RX <- {p.name}")
        elif bus == "gpio":
            add(f"{slug}_io",   "inout",  1,  f"GPIO    <-> {p.name}")
        elif bus == "pwm":
            add(f"{slug}_pwm",  "output", 1,  f"PWM     -> {p.name}")
        elif bus == "adc":
            add(f"{slug}_data", "input", 16,  f"ADC data <- {p.name}")
            add(f"{slug}_data_valid", "input", 1, f"ADC data-valid <- {p.name}")
        elif bus == "dac":
            add(f"{slug}_data", "output", 16, f"DAC data -> {p.name}")
        elif bus == "lvds":
            add(f"{slug}_p",    "input",  1,  f"LVDS+ <- {p.name}")
            add(f"{slug}_n",    "input",  1,  f"LVDS- <- {p.name}")
        else:
            add(f"{slug}_io",   "inout",  1,  f"{bus.upper()} {p.name}")
    return ports


# ---------------------------------------------------------------------------
# VHDL emitter
# ---------------------------------------------------------------------------


def _peripherals_from_glr_text(glr: str) -> list[Peripheral]:
    """Fallback peripheral synthesis from GLR markdown text.

    Used only when ProjectBrief.peripherals is empty (e.g. unit tests
    that invoke the emitter without going through the full builder).
    Mirrors the old _build_skeleton GLR-text detection.
    """
    text = (glr or "").lower()
    out: list[Peripheral] = []
    if "spi" in text:
        out.append(Peripheral(name="spi", bus="spi", address="0"))
    if "adc" in text or "lvds" in text or "digitiz" in text:
        out.append(Peripheral(name="adc", bus="adc", address=""))
    if "uart" in text or "serial" in text:
        out.append(Peripheral(name="uart_dbg", bus="uart", address=""))
    if "i2c" in text:
        out.append(Peripheral(name="i2c_bus", bus="i2c", address=""))
    if "gpio" in text:
        out.append(Peripheral(name="gpio_io", bus="gpio", address=""))
    if "dac" in text:
        out.append(Peripheral(name="dac", bus="dac", address=""))
    if "pwm" in text:
        out.append(Peripheral(name="pwm_out", bus="pwm", address=""))
    return out


def _data_width_from_glr_text(glr: str) -> int:
    """Pull data-width hint from GLR text. Defaults to 16."""
    text = (glr or "").lower()
    for w in (32, 24, 14, 12, 10, 8):
        if f"{w}-bit" in text or f"{w}bit" in text or f"{w} bit" in text:
            return w
    return 16


def _apply_adc_width_hint(per_ports, glr: str) -> None:
    """Mutate per_ports list in place: if ADC ports were synthesised,
    update their width to match the GLR data-width hint."""
    if not glr:
        return
    width = _data_width_from_glr_text(glr)
    for i, (name, dirn, w, comment) in enumerate(per_ports):
        if name.endswith("_data") and "ADC" in comment:
            per_ports[i] = (name, dirn, width, comment)


def render_vhdl(brief: ProjectBrief, project_name: str, safe_name: str,
                glr: str = "") -> dict[str, str]:
    """Build a project-tailored VHDL bundle.

    Outputs:
      rtl/<top>.vhd       -- entity + register file + FSM stubs + peripheral ports
      rtl/<top>_tb.vhd    -- per-register read/write checks + FSM smoke
      rtl/constraints.xdc -- clock + a stub `set_property PACKAGE_PIN` line per peripheral port
      fpga_design_report.md -- summary tying every emitted symbol back to its P1-P7a source
    """
    module = _safe_id(safe_name, "vhdl") + "_top"
    clk_mhz = brief.clock_frequency_mhz or 100.0
    clk_period_ns = round(1000.0 / clk_mhz, 3)

    _peripherals = list(brief.peripherals)
    if not _peripherals and glr:
        _peripherals = _peripherals_from_glr_text(glr)
    per_ports = _peripheral_ports(_peripherals, "vhdl")
    _apply_adc_width_hint(per_ports, glr)
    regs: list[Register] = list(brief.registers)
    if not regs:
        # Fallback baseline registers when P7a hasn't run.
        regs = [
            Register(name="CTRL",     address="0x0000", access="RW",
                     reset_value="0x0000", description="Master control"),
            Register(name="STATUS",   address="0x0001", access="RO",
                     description="Status flags"),
            Register(name="VERSION",  address="0x0002", access="RO",
                     reset_value="0x0100", description="Firmware version"),
            Register(name="SCRATCH",  address="0x0003", access="RW",
                     reset_value="0x0000", description="Scratch register"),
        ]
    fsms: list[FSM] = list(brief.fsms)

    # ---- Header -----------------------------------------------------
    lines: list[str] = [
        f"-- =========================================================",
        f"-- Entity   : {module}",
        f"-- Project  : {project_name}",
        f"-- Generated: Silicon to Software (S2S) v2 (project-tailored VHDL)",
        f"-- Clock    : {clk_mhz} MHz ({clk_period_ns} ns period)",
        f"-- FPGA part: {brief.fpga_part}",
        f"-- Registers: {len(regs)}   FSMs: {len(fsms)}   Peripherals: {len(brief.peripherals)}",
        f"-- =========================================================",
        "library ieee;",
        "use ieee.std_logic_1164.all;",
        "use ieee.numeric_std.all;",
        "",
        f"entity {module} is",
        "    port (",
        "        clk        : in  std_logic;",
        "        rst_n      : in  std_logic;",
        "",
        "        -- Register bus (16-bit address, 16-bit data)",
        "        reg_addr   : in  std_logic_vector(15 downto 0);",
        "        reg_wdata  : in  std_logic_vector(15 downto 0);",
        "        reg_rdata  : out std_logic_vector(15 downto 0);",
        "        reg_wr     : in  std_logic;",
        "        reg_rd     : in  std_logic;",
        "",
        "        -- Status / interrupts",
        "        irq_out    : out std_logic;",
        "        busy       : out std_logic;",
        "        error_flag : out std_logic" + (";" if per_ports else ""),
    ]
    if per_ports:
        lines.append("")
        lines.append(f"        -- Peripheral I/O ({len(per_ports)} ports derived from BOM)")
        for i, (name, dirn, width, comment) in enumerate(per_ports):
            sep = ";" if i < len(per_ports) - 1 else ""
            wsig = "std_logic" if width == 1 else f"std_logic_vector({width-1} downto 0)"
            lines.append(f"        {name:<22}: {dirn:<6} {wsig}{sep}    -- {comment}")
    lines.append("    );")
    lines.append(f"end entity {module};")
    lines.append("")
    lines.append(f"architecture rtl of {module} is")

    # ---- Constants from the actual register map ---------------------
    lines.append("    -- Register address constants (from P7a register_description_table.md)")
    for r in regs:
        const_id = "ADDR_" + _safe_id(r.name, "vhdl").upper()
        addr_n = _addr_int(r.address) or 0
        lines.append(
            f'    constant {const_id:<22}: std_logic_vector(11 downto 0) := x"{addr_n & 0xFFF:03X}";'
        )

    # Storage signals for RW registers
    rw_regs = [r for r in regs if "W" in r.access.upper()]
    lines.append("")
    lines.append("    -- Backing storage for RW registers")
    for r in rw_regs:
        sig = _safe_id(r.name, "vhdl") + "_r"
        reset = _addr_int(r.reset_value) or 0
        lines.append(
            f'    signal {sig:<22}: std_logic_vector(15 downto 0) := x"{reset & 0xFFFF:04X}";'
        )
    lines.append("    signal busy_r       : std_logic := '0';")
    lines.append("    signal irq_r        : std_logic := '0';")
    lines.append("")

    # FSM state types
    if fsms:
        lines.append("    -- FSM state types (from GLR specification)")
        for fsm in fsms:
            t = "t_" + _safe_id(fsm.name, "vhdl")
            states = ", ".join("S_" + _safe_id(s, "vhdl").upper() for s in (fsm.states or ["IDLE"]))
            lines.append(f"    type {t} is ({states});")
            lines.append(f"    signal {_safe_id(fsm.name, 'vhdl')}_state : {t} := {('S_' + _safe_id((fsm.states or ['IDLE'])[0], 'vhdl')).upper()};")
        lines.append("")

    lines.append("begin")

    # ---- Register write process ------------------------------------
    lines.append("    -- =====================================================")
    lines.append("    -- Register write decoder")
    lines.append("    -- =====================================================")
    lines.append("    p_reg_write : process (clk)")
    lines.append("    begin")
    lines.append("        if rising_edge(clk) then")
    lines.append("            if rst_n = '0' then")
    for r in rw_regs:
        sig = _safe_id(r.name, "vhdl") + "_r"
        reset = _addr_int(r.reset_value) or 0
        lines.append(f'                {sig:<22} <= x"{reset & 0xFFFF:04X}";')
    lines.append("            elsif reg_wr = '1' then")
    lines.append("                case reg_addr(11 downto 0) is")
    for r in rw_regs:
        const_id = "ADDR_" + _safe_id(r.name, "vhdl").upper()
        sig = _safe_id(r.name, "vhdl") + "_r"
        lines.append(f"                    when {const_id:<22} => {sig} <= reg_wdata;")
    lines.append("                    when others             => null;")
    lines.append("                end case;")
    lines.append("            end if;")
    lines.append("        end if;")
    lines.append("    end process p_reg_write;")
    lines.append("")

    # ---- Register read process -------------------------------------
    lines.append("    -- =====================================================")
    lines.append("    -- Register read mux")
    lines.append("    -- =====================================================")
    lines.append("    p_reg_read : process (clk)")
    lines.append("    begin")
    lines.append("        if rising_edge(clk) then")
    lines.append("            if rst_n = '0' then")
    lines.append("                reg_rdata <= (others => '0');  -- Idle / no read in flight")
    lines.append("            elsif reg_rd = '1' then")
    lines.append("                case reg_addr(11 downto 0) is")
    for r in regs:
        const_id = "ADDR_" + _safe_id(r.name, "vhdl").upper()
        access = r.access.upper()
        if "W" in access:                         # RW
            sig = _safe_id(r.name, "vhdl") + "_r"
            rhs = sig
        elif r.reset_value:                       # RO with constant
            v = _addr_int(r.reset_value) or 0
            rhs = f'x"{v & 0xFFFF:04X}"'
        else:                                     # RO computed (status etc.)
            rhs = "(others => '0')"
        lines.append(f"                    when {const_id:<22} => reg_rdata <= {rhs};")
    lines.append("                    when others             => reg_rdata <= (others => '0');  -- Unmapped: read-as-zero")
    lines.append("                end case;")
    lines.append("            end if;")
    lines.append("        end if;")
    lines.append("    end process p_reg_read;")
    lines.append("")

    # ---- FSM stubs -------------------------------------------------
    for fsm in fsms:
        sig = _safe_id(fsm.name, "vhdl") + "_state"
        states = [s for s in (fsm.states or ["IDLE"])]
        lines.append("    -- =====================================================")
        lines.append(f"    -- FSM: {fsm.name}  (from GLR)")
        lines.append("    -- =====================================================")
        lines.append(f"    p_{_safe_id(fsm.name, 'vhdl')} : process (clk)")
        lines.append("    begin")
        lines.append("        if rising_edge(clk) then")
        lines.append("            if rst_n = '0' then")
        lines.append(f"                {sig} <= S_{_safe_id(states[0], 'vhdl').upper()};")
        lines.append("            else")
        lines.append(f"                case {sig} is")
        for i, s in enumerate(states):
            cur = "S_" + _safe_id(s, "vhdl").upper()
            nxt = "S_" + _safe_id(states[(i + 1) % len(states)], "vhdl").upper()
            lines.append(f"                    when {cur:<24} =>")
            lines.append(f"                        -- TODO: transition logic for {s}")
            lines.append(f"                        {sig} <= {nxt};")
        lines.append("                end case;")
        lines.append("            end if;")
        lines.append("        end if;")
        lines.append(f"    end process p_{_safe_id(fsm.name, 'vhdl')};")
        lines.append("")

    # ---- Peripheral interface stubs --------------------------------
    if per_ports:
        lines.append("    -- =====================================================")
        lines.append("    -- Peripheral I/O default drivers (replace with controller IP)")
        lines.append("    -- =====================================================")
        for name, dirn, width, _comment in per_ports:
            if dirn != "output":
                continue
            if width == 1:
                lines.append(f"    {name} <= '0';")
            else:
                lines.append(f"    {name} <= (others => '0');")
        lines.append("")

    lines.append("    -- Status / control wiring")
    lines.append("    busy       <= busy_r;")
    lines.append("    irq_out    <= irq_r;")
    lines.append("    error_flag <= '0';")
    lines.append("end architecture rtl;")

    vhdl_top = "\n".join(lines)

    # ---- Testbench --------------------------------------------------
    tb_lines = [
        "library ieee;",
        "use ieee.std_logic_1164.all;",
        "use ieee.numeric_std.all;",
        "",
        f"entity {module}_tb is end entity;",
        "",
        f"architecture sim of {module}_tb is",
        "    signal clk        : std_logic := '0';",
        "    signal rst_n      : std_logic := '0';",
        "    signal reg_addr   : std_logic_vector(15 downto 0) := (others => '0');",
        "    signal reg_wdata  : std_logic_vector(15 downto 0) := (others => '0');",
        "    signal reg_rdata  : std_logic_vector(15 downto 0);",
        "    signal reg_wr     : std_logic := '0';",
        "    signal reg_rd     : std_logic := '0';",
        "    signal irq_out, busy, error_flag : std_logic;",
        f"    constant CLK_PERIOD : time := {clk_period_ns} ns;",
        "begin",
        f"    dut: entity work.{module}",
        "    port map (",
        "        clk=>clk, rst_n=>rst_n,",
        "        reg_addr=>reg_addr, reg_wdata=>reg_wdata,",
        "        reg_rdata=>reg_rdata, reg_wr=>reg_wr, reg_rd=>reg_rd,",
        "        irq_out=>irq_out, busy=>busy, error_flag=>error_flag",
    ]
    # Stub the peripheral port mapping (open / unused inputs).
    for name, dirn, width, _comment in per_ports:
        if dirn == "input":
            stub = "'0'" if width == 1 else "(others => '0')"
            tb_lines.append(f"        , {name}=>{stub}")
        elif dirn == "inout":
            tb_lines.append(f"        , {name}=>open")
        else:
            tb_lines.append(f"        , {name}=>open")
    tb_lines.append("    );")
    tb_lines.append("    clk <= not clk after CLK_PERIOD/2;")
    tb_lines.append("    process")
    tb_lines.append("    begin")
    tb_lines.append("        wait for 5 * CLK_PERIOD;")
    tb_lines.append("        rst_n <= '1';")
    tb_lines.append("        wait for 2 * CLK_PERIOD;")
    tb_lines.append('        report "Reset released";')
    # Per-register check vectors - one per RW register.
    for r in rw_regs[:8]:
        const_id = "ADDR_" + _safe_id(r.name, "vhdl").upper()
        tb_lines.append(f"        -- {r.name} R/W check ({r.address})")
        tb_lines.append(f'        reg_addr  <= x"0{const_id[5:].lower()[:3] if False else "%03X" % (_addr_int(r.address) or 0)}";'.replace('"0%03X"', f'"{(_addr_int(r.address) or 0) & 0xFFFF:04X}"'))
        tb_lines.append('        reg_wdata <= x"BEEF";  reg_wr <= \'1\';')
        tb_lines.append("        wait for CLK_PERIOD;  reg_wr <= '0'; wait for CLK_PERIOD;")
        tb_lines.append("        reg_rd <= '1'; wait for CLK_PERIOD; reg_rd <= '0'; wait for CLK_PERIOD;")
        tb_lines.append(f'        assert reg_rdata = x"BEEF" report "{r.name} R/W mismatch" severity error;')
    tb_lines.append('        report "All register checks complete";')
    tb_lines.append("        wait;")
    tb_lines.append("    end process;")
    tb_lines.append("end architecture;")
    vhdl_tb = "\n".join(tb_lines)

    # ---- XDC --------------------------------------------------------
    xdc_lines = [
        f"# Constraints : {project_name}",
        f"# Target      : {brief.fpga_part}",
        f"create_clock -period {clk_period_ns:.3f} -name clk -waveform {{0 {clk_period_ns/2:.3f}}} [get_ports clk]",
        "",
        "# Per-peripheral pin stubs - REPLACE with your board package map.",
    ]
    for name, _d, _w, comment in per_ports:
        xdc_lines.append(f"# set_property PACKAGE_PIN <PIN>  [get_ports {{{name}}}]   ;# {comment}")
        xdc_lines.append(f"# set_property IOSTANDARD LVCMOS33 [get_ports {{{name}}}]")
    xdc = "\n".join(xdc_lines)

    # ---- Design report ---------------------------------------------
    rep = [
        f"# FPGA Design Report — {project_name}",
        "",
        "## Source brief",
        f"- Application class: `{brief.application_class}`",
        f"- HDL language: **VHDL-2008**",
        f"- Module: `{module}`",
        f"- Clock: {clk_mhz} MHz on {brief.fpga_part}",
        f"- Architecture (from P1): {brief.architecture or '_not specified_'}",
        f"- Frequency band: {brief.frequency_min_ghz}–{brief.frequency_max_ghz} GHz",
        "",
        "## Register file (from P7a register_description_table.md)",
        "",
        "| Address | Name | Access | Reset | Description |",
        "|---------|------|--------|-------|-------------|",
    ]
    for r in regs:
        rep.append(
            f"| `{r.address}` | `{r.name}` | {r.access} | "
            f"`{r.reset_value or '-'}` | {r.description or '-'} |"
        )
    if fsms:
        rep += ["", "## State machines (from GLR)", ""]
        for fsm in fsms:
            rep.append(f"### `{fsm.name}`")
            rep.append("")
            rep.append("States: " + ", ".join(f"`{s}`" for s in fsm.states))
            rep.append("")
    if brief.peripherals:
        rep += ["## Peripheral I/O (from P1/P4 BOM)", "",
                "| Bus | Name | VHDL ports |",
                "|-----|------|------------|"]
        for p in brief.peripherals:
            slug = _safe_id(p.name, "vhdl")
            rep.append(f"| {p.bus.upper()} | {p.name} | `{slug}_*` |")
    rep += ["", "## Files", "",
            f"- `rtl/{safe_name}_top.vhd` ({len(vhdl_top.splitlines())} lines)",
            f"- `rtl/{safe_name}_top_tb.vhd` ({len(vhdl_tb.splitlines())} lines)",
            "- `rtl/constraints.xdc`",
            ""]

    return {
        "rtl/fpga_top.vhd":         vhdl_top,
        "rtl/fpga_testbench.vhd":   vhdl_tb,
        "rtl/constraints.xdc":         xdc,
        "fpga_design_report.md":       "\n".join(rep),
    }


# ---------------------------------------------------------------------------
# Verilog emitter (mirror of VHDL, same data sources)
# ---------------------------------------------------------------------------

def _verilog_inst_block(per_ports, regs, bus_set, register_storage_names) -> list[str]:
    """Generate component instantiations to splice into fpga_top.v.

    `register_storage_names` is the set of `<name>_r` signals that the
    register-write process declares - so we know which register backings
    actually exist before we wire them to a controller.
    """
    lines: list[str] = []

    def first_port(suffixes):
        for n, _d, _w, _c in per_ports:
            for s in suffixes:
                if n.endswith(s):
                    return n
        return None

    def reg_name(canonical):
        cn = canonical.lower() + "_r"
        return cn if cn in register_storage_names else None

    # ---- UART engine ----
    uart_tx = first_port(("_txd",))
    uart_rx = first_port(("_rxd",))
    if "uart" in bus_set and uart_tx and uart_rx:
        lines.append("")
        lines.append("    // ---- UART engine -> register-bus master --------------------")
        lines.append("    wire [15:0] uart_reg_addr, uart_reg_wdata;")
        lines.append("    wire        uart_reg_wr, uart_reg_rd;")
        lines.append("    uart_engine #(.CLK_DIV(434)) u_uart (")
        lines.append("        .clk(clk), .rst_n(rst_n),")
        lines.append(f"        .rxd({uart_rx}), .txd({uart_tx}),")
        lines.append("        .reg_addr(uart_reg_addr), .reg_wdata(uart_reg_wdata),")
        lines.append("        .reg_rdata(reg_rdata),")
        lines.append("        .reg_wr(uart_reg_wr), .reg_rd(uart_reg_rd)")
        lines.append("    );")
        lines.append("    // NOTE: top-level reg_wr/reg_rd from the UART engine are merged")
        lines.append("    // with the external reg-bus inputs by the caller. For a")
        lines.append("    // standalone simulation, tie reg_addr_top/wdata_top to")
        lines.append("    // uart_reg_addr/wdata.")

    # ---- SPI master + PLL config ----
    spi_clk  = first_port(("_clk",)) if "spi" in bus_set else None
    spi_mosi = first_port(("_mosi",))
    spi_miso = first_port(("_miso",))
    spi_cs_n = first_port(("_cs_n",))
    if "spi" in bus_set and spi_clk and spi_mosi and spi_miso and spi_cs_n:
        lines.append("")
        lines.append("    // ---- SPI master shared by PLL config + flash --------------")
        lines.append("    wire        spi_start_w;")
        lines.append("    wire [23:0] spi_tx_w, spi_rx_w;")
        lines.append("    wire        spi_done_w;")
        lines.append("    spi_master #(.DATA_W(24), .CLK_DIV(16)) u_spi (")
        lines.append("        .clk(clk), .rst_n(rst_n),")
        lines.append("        .start(spi_start_w), .tx_data(spi_tx_w),")
        lines.append("        .rx_data(spi_rx_w), .done(spi_done_w),")
        lines.append(f"        .sclk({spi_clk}), .mosi({spi_mosi}), .miso({spi_miso}), .cs_n({spi_cs_n})")
        lines.append("    );")
        # PLL config sequencer if PLL_* registers exist.
        if any("PLL" in r.name for r in regs):
            ctrl = reg_name("pll_ctrl")
            ndiv = reg_name("pll_n_div")
            rdiv = reg_name("pll_r_div")
            if ctrl and ndiv and rdiv:
                lines.append("    wire pll_locked_w;")
                lines.append("    pll_config u_pll (")
                lines.append("        .clk(clk), .rst_n(rst_n),")
                lines.append(f"        .pll_ctrl({ctrl}), .pll_n_div({ndiv}), .pll_r_div({rdiv}),")
                lines.append("        .pll_locked(pll_locked_w),")
                lines.append("        .spi_start(spi_start_w), .spi_tx_data(spi_tx_w),")
                lines.append("        .spi_rx_data(spi_rx_w), .spi_done(spi_done_w)")
                lines.append("    );")

    # ---- ADC capture ----
    adc_data  = first_port(("_data",))
    adc_dv    = first_port(("_data_valid",))
    if ("adc" in bus_set or any("ADC" in r.name for r in regs)) and adc_data and adc_dv:
        ctrl = reg_name("adc_ctrl")
        if ctrl:
            lines.append("")
            lines.append("    // ---- ADC capture FSM -------------------------------------")
            lines.append("    wire [15:0] adc_status_w, adc_ch1_w, adc_ch2_w;")
            lines.append("    adc_capture u_adc (")
            lines.append("        .clk(clk), .rst_n(rst_n),")
            lines.append(f"        .adc_data_valid({adc_dv}), .adc_data({adc_data}),")
            lines.append(f"        .adc_ctrl({ctrl}), .adc_status(adc_status_w),")
            lines.append("        .ch1_data(adc_ch1_w), .ch2_data(adc_ch2_w)")
            lines.append("    );")

    # ---- I2C master + EEPROM ----
    i2c_scl = first_port(("_scl",))
    i2c_sda = first_port(("_sda",))
    if "i2c" in bus_set and i2c_scl and i2c_sda:
        lines.append("")
        lines.append("    // ---- I2C master + EEPROM driver --------------------------")
        lines.append("    wire        i2c_start_w, i2c_stop_w, i2c_we_w, i2c_done_w, i2c_ack_err_w;")
        lines.append("    wire [7:0]  i2c_byte_in_w, i2c_byte_out_w;")
        lines.append("    i2c_master #(.CLK_DIV(250)) u_i2c (")
        lines.append("        .clk(clk), .rst_n(rst_n),")
        lines.append("        .start(i2c_start_w), .stop(i2c_stop_w), .we(i2c_we_w),")
        lines.append("        .byte_in(i2c_byte_in_w), .byte_out(i2c_byte_out_w),")
        lines.append("        .done(i2c_done_w), .ack_err(i2c_ack_err_w),")
        lines.append(f"        .scl({i2c_scl}), .sda({i2c_sda})")
        lines.append("    );")
        ee_ctrl = reg_name("eeprom_ctrl")
        ee_addr = reg_name("eeprom_addr")
        ee_data = reg_name("eeprom_data")
        if ee_ctrl and ee_addr and ee_data:
            lines.append("    wire [15:0] eeprom_status_w, eeprom_data_out_w;")
            lines.append("    eeprom_driver u_eeprom (")
            lines.append("        .clk(clk), .rst_n(rst_n),")
            lines.append(f"        .eeprom_ctrl({ee_ctrl}), .eeprom_addr({ee_addr}), .eeprom_data_in({ee_data}),")
            lines.append("        .eeprom_data_out(eeprom_data_out_w),")
            lines.append("        .eeprom_status(eeprom_status_w),")
            lines.append("        .i2c_start(i2c_start_w), .i2c_stop(i2c_stop_w), .i2c_we(i2c_we_w),")
            lines.append("        .i2c_byte_in(i2c_byte_in_w), .i2c_byte_out(i2c_byte_out_w),")
            lines.append("        .i2c_done(i2c_done_w), .i2c_ack_err(i2c_ack_err_w)")
            lines.append("    );")

    # ---- Flash controller ----
    if any("FLASH_CTRL" == r.name for r in regs):
        fc = reg_name("flash_ctrl")
        fal = reg_name("flash_addr_low")
        fah = reg_name("flash_addr_high")
        fd = reg_name("flash_data")
        fk = reg_name("flash_key")
        if fc and fk:
            lines.append("")
            lines.append("    // ---- Flash controller (SPI Flash, key-protected) ----------")
            lines.append("    wire [15:0] flash_data_out_w, flash_status_w;")
            lines.append("    flash_ctrl #(.UNLOCK_KEY(16'hA5C3)) u_flash (")
            lines.append("        .clk(clk), .rst_n(rst_n),")
            lines.append(f"        .flash_ctrl({fc}),")
            _zero = "16'h0"
            _fal = fal or _zero
            _fah = fah or _zero
            _fd  = fd  or _zero
            lines.append(f"        .flash_addr_low({_fal}), .flash_addr_high({_fah}),")
            lines.append(f"        .flash_data_in({_fd}), .flash_key({fk}),")
            lines.append("        .flash_data_out(flash_data_out_w), .flash_status(flash_status_w),")
            lines.append("        .spi_start(), .spi_tx_data(), .spi_rx_data(spi_rx_w), .spi_done(spi_done_w)")
            lines.append("    );")

    # ---- GPIO controller ----
    if any("GPIO" in r.name for r in regs):
        gc = reg_name("gpio_ctrl")
        gim = reg_name("gpio_irq_mask")
        gpio_pin = first_port(("_io",))
        if gc and gim and gpio_pin:
            lines.append("")
            lines.append("    // ---- GPIO controller with IRQ ----------------------------")
            lines.append("    wire [15:0] gpio_input_w, gpio_irq_status_w;")
            lines.append("    wire        gpio_irq_w;")
            lines.append("    gpio_ctrl #(.W(8)) u_gpio (")
            lines.append("        .clk(clk), .rst_n(rst_n),")
            lines.append(f"        .gpio_ctrl({gc}), .gpio_input(gpio_input_w),")
            lines.append(f"        .gpio_irq_mask({gim}), .gpio_irq_status(gpio_irq_status_w),")
            lines.append(f"        .gpio_io({{8{{{gpio_pin}}}}}), .gpio_irq(gpio_irq_w)")
            lines.append("    );")

    return lines


def render_verilog(brief: ProjectBrief, project_name: str, safe_name: str, glr: str = "") -> dict[str, str]:
    """Project-tailored Verilog mirror of render_vhdl. Same registers,
    same FSMs, same peripheral ports - just emitted as Verilog-2001."""
    module = _safe_id(safe_name, "verilog") + "_top"
    clk_mhz = brief.clock_frequency_mhz or 100.0
    clk_period_ns = round(1000.0 / clk_mhz, 3)

    _peripherals = list(brief.peripherals)
    if not _peripherals and glr:
        _peripherals = _peripherals_from_glr_text(glr)
    per_ports = _peripheral_ports(_peripherals, "verilog")
    _apply_adc_width_hint(per_ports, glr)
    regs = list(brief.registers) or [
        Register(name="CTRL",    address="0x0000", access="RW",
                 reset_value="0x0000", description="Master control"),
        Register(name="STATUS",  address="0x0001", access="RO",
                 description="Status flags"),
        Register(name="VERSION", address="0x0002", access="RO",
                 reset_value="0x0100", description="Firmware version"),
        Register(name="SCRATCH", address="0x0003", access="RW",
                 reset_value="0x0000", description="Scratch register"),
    ]
    fsms = list(brief.fsms)

    v: list[str] = []
    v.append(f"// =========================================================")
    v.append(f"// Module   : {module}")
    v.append(f"// Project  : {project_name}")
    v.append(f"// Generated: Silicon to Software (S2S) v2 (project-tailored Verilog)")
    v.append(f"// Clock    : {clk_mhz} MHz")
    v.append(f"// Registers: {len(regs)}  FSMs: {len(fsms)}  Peripherals: {len(brief.peripherals)}")
    v.append(f"// =========================================================")
    v.append("`timescale 1ns / 1ps")
    v.append("")
    v.append(f"module {module} (")
    v.append("    input  wire        clk,")
    v.append("    input  wire        rst_n,")
    v.append("")
    v.append("    input  wire [15:0] reg_addr,")
    v.append("    input  wire [15:0] reg_wdata,")
    v.append("    output reg  [15:0] reg_rdata,")
    v.append("    input  wire        reg_wr,")
    v.append("    input  wire        reg_rd,")
    v.append("")
    v.append("    output wire        irq_out,")
    v.append("    output wire        busy,")
    v.append("    output wire        error_flag" + ("," if per_ports else ""))
    if per_ports:
        v.append("")
        for i, (name, dirn, width, comment) in enumerate(per_ports):
            sep = "," if i < len(per_ports) - 1 else ""
            range_s = "" if width == 1 else f" [{width-1}:0]"
            v.append(f"    {dirn:<6} wire{range_s} {name}{sep}    // {comment}")
    v.append(");")
    v.append("")

    v.append("    // Register address localparams (from P7a)")
    for r in regs:
        addr_n = _addr_int(r.address) or 0
        v.append(f"    localparam ADDR_{_safe_id(r.name, 'verilog').upper():<14} = 12'h{addr_n & 0xFFF:03X};")
    v.append("")
    rw_regs = [r for r in regs if "W" in r.access.upper()]
    for r in rw_regs:
        reset = _addr_int(r.reset_value) or 0
        v.append(f"    reg [15:0] {_safe_id(r.name, 'verilog')}_r = 16'h{reset & 0xFFFF:04X};")
    v.append("    reg busy_r = 1'b0;")
    v.append("    reg irq_r  = 1'b0;")
    v.append("")
    v.append("    // Register write")
    v.append("    always @(posedge clk) begin")
    v.append("        if (!rst_n) begin")
    for r in rw_regs:
        reset = _addr_int(r.reset_value) or 0
        v.append(f"            {_safe_id(r.name, 'verilog')}_r <= 16'h{reset & 0xFFFF:04X};")
    v.append("        end else if (reg_wr) begin")
    v.append("            case (reg_addr[11:0])")
    for r in rw_regs:
        v.append(f"                ADDR_{_safe_id(r.name, 'verilog').upper()}: {_safe_id(r.name, 'verilog')}_r <= reg_wdata;")
    v.append("                default: ;")
    v.append("            endcase")
    v.append("        end")
    v.append("    end")
    v.append("")
    v.append("    // Register read mux")
    v.append("    always @(posedge clk) begin")
    v.append("        if (!rst_n) reg_rdata <= 16'h0000;")
    v.append("        else if (reg_rd) begin")
    v.append("            case (reg_addr[11:0])")
    for r in regs:
        access = r.access.upper()
        if "W" in access:
            rhs = _safe_id(r.name, "verilog") + "_r"
        elif r.reset_value:
            rhs = f"16'h{(_addr_int(r.reset_value) or 0) & 0xFFFF:04X}"
        else:
            rhs = "16'h0000"
        v.append(f"                ADDR_{_safe_id(r.name, 'verilog').upper()}: reg_rdata <= {rhs};")
    v.append("                default: reg_rdata <= 16'h0000;  // Unmapped: read-as-zero")
    v.append("            endcase")
    v.append("        end")
    v.append("    end")
    v.append("")

    # FSM stubs (same as VHDL)
    for fsm in fsms:
        sig = _safe_id(fsm.name, "verilog") + "_state"
        states = fsm.states or ["IDLE"]
        v.append(f"    // FSM: {fsm.name}")
        for i, s in enumerate(states):
            v.append(f"    localparam S_{_safe_id(s, 'verilog').upper()} = {len(states).bit_length()}'d{i};")
        v.append(f"    reg [{(len(states)-1).bit_length()-1 if len(states) > 1 else 0}:0] {sig} = S_{_safe_id(states[0], 'verilog').upper()};")
        v.append("    always @(posedge clk) begin")
        v.append(f"        if (!rst_n) {sig} <= S_{_safe_id(states[0], 'verilog').upper()};")
        v.append("        else begin")
        v.append(f"            case ({sig})")
        for i, s in enumerate(states):
            nxt = states[(i + 1) % len(states)]
            v.append(f"                S_{_safe_id(s, 'verilog').upper()}: {sig} <= S_{_safe_id(nxt, 'verilog').upper()};")
        v.append("            endcase")
        v.append("        end")
        v.append("    end")
        v.append("")

    v.append("    assign busy       = busy_r;")
    v.append("    assign irq_out    = irq_r;")
    v.append("    assign error_flag = 1'b0;")
    # 2026-05-02: instantiate every component module the project uses.
    # Without this block the rtl/*.v files sit on disk unreferenced.
    _bus_set_local = {pp.bus for pp in (_peripherals or [])}
    _reg_storage_names = {(_safe_id(r.name, "verilog") + "_r") for r in rw_regs}
    v.extend(_verilog_inst_block(per_ports, regs, _bus_set_local, _reg_storage_names))
    v.append("endmodule")
    verilog_top = "\n".join(v)

    # Testbench
    tb = []
    tb.append("`timescale 1ns / 1ps")
    tb.append(f"module {module}_tb;")
    tb.append("    reg         clk = 0, rst_n = 0;")
    tb.append("    reg  [15:0] reg_addr = 0, reg_wdata = 0;")
    tb.append("    wire [15:0] reg_rdata;")
    tb.append("    reg         reg_wr = 0, reg_rd = 0;")
    tb.append("    wire        irq_out, busy, error_flag;")
    tb.append(f"    {module} dut(.*);")
    tb.append(f"    always #{clk_period_ns/2:.2f} clk = ~clk;")
    tb.append("    initial begin")
    tb.append("        rst_n = 0; #50; rst_n = 1; #20;")
    for r in rw_regs[:6]:
        addr_n = _addr_int(r.address) or 0
        tb.append(f"        reg_addr = 16'h{addr_n & 0xFFFF:04X}; reg_wdata = 16'hBEEF; reg_wr = 1; @(posedge clk); reg_wr = 0; @(posedge clk);")
        tb.append(f"        reg_rd = 1; @(posedge clk); reg_rd = 0;")
        tb.append(f'        if (reg_rdata !== 16\'hBEEF) $display("FAIL {r.name}");')
        tb.append('        else $display("PASS %s", "%s");' % (r.name, r.name))
    tb.append("        $finish;")
    tb.append("    end")
    tb.append("endmodule")
    verilog_tb = "\n".join(tb)

    # XDC: use the pin map extracted from netlist/GLR. When a port has
    # no pin assignment, leave the line commented with an explicit note so
    # the user knows that port still needs a manual entry.
    pin_map = extract_pin_map(glr or "", glr or "")
    xdc_lines = [
        f"# Constraints : {project_name}",
        f"# Target      : {brief.fpga_part}",
        f"# Pin assignments: {sum(1 for n,_,_,_ in per_ports if n.lower() in pin_map)}/{len(per_ports)} resolved from netlist",
        "",
        f"create_clock -period {clk_period_ns:.3f} -name clk -waveform {{0 {clk_period_ns/2:.3f}}} [get_ports clk]",
        "",
    ]
    if "clk" in pin_map:
        xdc_lines.append(f"set_property PACKAGE_PIN {pin_map['clk']} [get_ports clk]")
        xdc_lines.append("set_property IOSTANDARD LVCMOS33 [get_ports clk]")
    for name, _d, _w, comment in per_ports:
        pin = pin_map.get(name.lower())
        if pin:
            xdc_lines.append(f"set_property PACKAGE_PIN {pin} [get_ports {{{name}}}]    ;# {comment}")
            xdc_lines.append(f"set_property IOSTANDARD LVCMOS33 [get_ports {{{name}}}]")
        else:
            xdc_lines.append(f"# TODO no pin in netlist for `{name}` -> add `set_property PACKAGE_PIN <PIN> [get_ports {{{name}}}]`   ;# {comment}")
    xdc = "\n".join(xdc_lines)

    # Per-component module files - one per peripheral the project uses.
    from agents import rtl_components as _rc
    bus_set = {p.bus for p in (_peripherals or [])}
    extra_files: dict[str, str] = {}
    # Build a ComponentSpec lookup keyed by bus so the per-component emitter
    # gets its parts' actual datasheet parameters (slave addr / opcodes /
    # PLL register count / etc.) instead of a hardcoded default.
    from schemas.component_spec import ComponentSpec as _ComponentSpec
    spec_by_bus: dict[str, _ComponentSpec] = {}
    spec_by_family: dict[str, _ComponentSpec] = {}
    for _per in (_peripherals or []):
        if not _per.spec:
            continue
        try:
            _s = _ComponentSpec(**_per.spec)
            spec_by_bus.setdefault(_per.bus, _s)
            if _s.family:
                spec_by_family.setdefault(_s.family.upper(), _s)
        except Exception:
            continue

    def _spec_for(*candidates):
        """Return the first matching ComponentSpec from `spec_by_bus`
        keyed by bus name, OR by family name. None when no match."""
        for c in candidates:
            if c in spec_by_bus:
                return spec_by_bus[c]
            if c.upper() in spec_by_family:
                return spec_by_family[c.upper()]
        return None

    if "uart" in bus_set:
        extra_files["rtl/uart_engine.v"] = _rc.uart_engine(clk_mhz)
    if "spi" in bus_set:
        extra_files["rtl/spi_master.v"] = _rc.spi_master()
        # PLL config sequencer is emitted whenever the register map mentions PLL_*
        if any("PLL" in r.name for r in regs):
            _pll_spec = _spec_for("HMC", "LMK", "PLL")
            extra_files["rtl/pll_config.v"] = _rc.pll_config_sequencer(_pll_spec)
    if "adc" in bus_set or any("ADC" in r.name for r in regs):
        extra_files["rtl/adc_capture.v"] = _rc.adc_capture(brief.bandwidth_mhz and 14 or 14)
    if "i2c" in bus_set:
        extra_files["rtl/i2c_master.v"] = _rc.i2c_master()
        if any("EEPROM" in r.name for r in regs):
            _ee_spec = _spec_for("i2c", "AT24")
            extra_files["rtl/eeprom_driver.v"] = _rc.eeprom_driver(_ee_spec)
    if any("FLASH" in r.name for r in regs):
        _fl_spec = _spec_for("N25Q", "W25Q", "S25FL", "spi")
        # Filter to flash-only spec (not e.g. PLL spec on SPI bus)
        if _fl_spec and not _fl_spec.flash_opcodes:
            _fl_spec = None
        extra_files["rtl/flash_ctrl.v"] = _rc.flash_ctrl(_fl_spec)
    if "gpio" in bus_set or any("GPIO" in r.name for r in regs):
        extra_files["rtl/gpio_ctrl.v"] = _rc.gpio_ctrl(8)

    # Design report - now lists every emitted module + the pin-map state.
    pin_resolved = sum(1 for n,_,_,_ in per_ports if n.lower() in pin_map)
    rep_lines = [
        f"# FPGA Design Report - {project_name}",
        "",
        f"- Application class: `{brief.application_class}`",
        f"- HDL language    : **Verilog-2001**",
        f"- Module          : `{module}`",
        f"- Clock           : {clk_mhz} MHz on {brief.fpga_part}",
        f"- Registers       : {len(regs)}",
        f"- FSMs            : {len(fsms)}",
        f"- Peripherals     : {len(brief.peripherals)}",
        f"- Pin assignments : {pin_resolved}/{len(per_ports)} resolved from netlist",
        "",
        "## Component spec audit",
        "",
        "Each peripheral's RTL is parameterised by a ComponentSpec resolved from",
        "(1) curated JSON, (2) MPN-family inference, (3) LLM-extracted datasheet,",
        "or (4) generic fallback. Anything below confidence 0.8 needs datasheet",
        "review before deployment.",
        "",
        "| Peripheral | Bus | Spec source | Confidence | Status |",
        "|------------|-----|-------------|------------|--------|",
    ]
    for _per in (_peripherals or []):
        _spec_d = _per.spec or {}
        _src = _spec_d.get("source", "(unresolved)")
        _conf = _spec_d.get("confidence", 0.0)
        _ok = "OK" if _conf >= 0.8 else ("REVIEW" if _conf >= 0.4 else "DATASHEET-CHECK NEEDED")
        rep_lines.append(
            f"| `{_per.name}` | {_per.bus.upper()} | {_src} | {_conf:.2f} | {_ok} |"
        )
    rep_lines += [
        "",
        "## Files emitted",
        "",
        "| File | Purpose |",
        "|------|---------|",
        "| `rtl/fpga_top.v` | Top-level module - register file + instantiates the controllers below |",
        "| `rtl/fpga_testbench.v` | Self-checking R/W vectors per RW register |",
        "| `rtl/fpga_coverage.sv` | SystemVerilog covergroups (per-register address + access type + per-FSM state) |",
        "| `rtl/constraints.xdc` | Vivado timing + pin assignments |",
    ]
    for fname in extra_files:
        purpose = {
            "rtl/uart_engine.v":   "8N1 UART TX/RX + GLR register-bus framing decoder (single + bulk W/R)",
            "rtl/spi_master.v":    "Generic SPI master, mode 0, parameterised data width + clock divisor",
            "rtl/pll_config.v":    "Sequences PLL_N_DIV / PLL_R_DIV writes out the SPI bus to the PLL chip",
            "rtl/adc_capture.v":   "ADC capture FSM: arms on ADC_CTRL[0], latches into ADC_CHn_DATA",
            "rtl/i2c_master.v":    "Byte-level I2C master with start/stop/restart, open-drain SCL/SDA",
            "rtl/eeprom_driver.v": "AT24-series EEPROM sequencer over the I2C master",
            "rtl/flash_ctrl.v":    "SPI Flash controller; writes gated behind FLASH_KEY register",
            "rtl/gpio_ctrl.v":     "Per-pin direction + edge-detect IRQ + write-1-to-clear status",
        }.get(fname, "Component controller")
        rep_lines.append(f"| `{fname}` | {purpose} |")
    rep = chr(10).join(rep_lines) + chr(10)

    # Coverage file - SystemVerilog covergroups derived from the
    # register map + FSMs in the brief. Always emitted (stub covergroups
    # if registers/FSMs are empty), so testbench compilation is stable.
    try:
        from agents.rtl_coverage import render_coverage_sv
        coverage_sv = render_coverage_sv(brief)
    except Exception as _cov_err:
        coverage_sv = (
            f"// fpga_coverage.sv stub - generator raised: {_cov_err}\n"
            "class fpga_coverage; function new(); endfunction\n"
            "  function void sample_register_access(input bit [31:0] addr,\n"
            "      input bit is_write, input bit [31:0] data,\n"
            "      input bit [31:0] reset_value); endfunction\n"
            "  function void report(); endfunction\n"
            "endclass : fpga_coverage\n"
        )

    out = {
        "rtl/fpga_top.v":         verilog_top,
        "rtl/fpga_testbench.v":   verilog_tb,
        "rtl/fpga_coverage.sv":   coverage_sv,
        "rtl/constraints.xdc":    xdc,
        "fpga_design_report.md":  rep,
    }
    out.update(extra_files)
    return out
