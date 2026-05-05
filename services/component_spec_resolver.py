"""
component_spec_resolver.py - resolve a BOM MPN to a ComponentSpec.

Resolution order, fastest path first:

  1. In-process LRU cache (zero I/O on hot path)
  2. Curated JSON file at data/component_specs/<MPN>.json
  3. Family-prefix inference (covers the long tail of common parts)
  4. LLM datasheet extractor (uses datasheet URL from BOM if available)
  5. Generic fallback with explicit confidence=0 and a `needs_review`
     flag - the design report flags every unresolved component so the
     user knows where to look.

The resolver is the SINGLE place that anything in the codebase converts
"the user picked an MPN" into "here are its config parameters". The RTL
emitters consume the resulting ComponentSpec - they never touch a
datasheet themselves.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from schemas.component_spec import ComponentSpec, FpgaSidePorts

log = logging.getLogger(__name__)

_SPEC_DIR = Path(__file__).resolve().parent.parent / "data" / "component_specs"


# ---------------------------------------------------------------------------
# Family-prefix inference rules. Each rule is a regex against the MPN
# (case-insensitive) plus a builder that returns a partial dict of fields.
#
# These come from datasheet conventions: AT24C is the Microchip serial
# EEPROM family; N25Q / W25Q / S25FL are SPI flash; ADS54xx is TI's high-
# speed ADCs; HMC8xx are Analog Devices PLLs; LMK is TI clock distribution;
# FT232 is FTDI USB-UART bridges; MAX3232/MAX232 is RS-232 transceivers.
# ---------------------------------------------------------------------------


def _at24_eeprom(mpn: str) -> dict:
    # Density is encoded in the digit suffix (e.g. AT24C256 -> 256kb).
    m = re.search(r"AT24C(\d+)", mpn, re.I)
    density_kb = int(m.group(1)) if m else 256
    capacity_bytes = density_kb * 128                  # 1024/8 -> bytes
    addr_w = 16 if density_kb >= 32 else 8
    page = 64 if density_kb >= 64 else 32 if density_kb >= 8 else 16
    return dict(
        bus="i2c", family="AT24", manufacturer="Microchip",
        i2c_slave_addr_7bit=0x50, i2c_addr_width_bits=addr_w,
        i2c_page_size_bytes=page, i2c_max_clock_hz=400_000,
        i2c_write_cycle_ms=5,
        description=f"{density_kb} kbit ({capacity_bytes//1024} KB) I2C EEPROM",
        notes=[f"AT24-family inference: density {density_kb} kb"],
        fpga_ports=FpgaSidePorts(write_protect=True),
    )


_FLASH_OPCODES_NOR = {
    "read": 0x03, "fast_read": 0x0B, "page_program": 0x02,
    "sector_erase": 0x20, "chip_erase": 0xC7,
    "write_enable": 0x06, "write_disable": 0x04,
    "read_status": 0x05, "read_id": 0x9F,
}


def _n25q_w25q_flash(mpn: str) -> dict:
    """Micron N25Q / Winbond W25Q / Spansion S25FL family - SPI NOR Flash."""
    m = re.search(r"(?:N25Q|W25Q|S25FL)(\d+)", mpn, re.I)
    density_mbit = int(m.group(1)) if m else 256
    capacity = density_mbit * 1024 * 1024 // 8
    family = "N25Q" if mpn.upper().startswith("N25Q") else (
             "W25Q" if mpn.upper().startswith("W25Q") else "S25FL")
    return dict(
        bus="spi", family=family,
        manufacturer="Micron" if family == "N25Q" else (
                     "Winbond" if family == "W25Q" else "Cypress/Spansion"),
        spi_max_clock_hz=108_000_000, spi_mode=0,
        spi_data_width_bits=8, spi_dummy_cycles=8,
        flash_capacity_bytes=capacity,
        flash_page_size_bytes=256,
        flash_sector_size_bytes=4096,
        flash_opcodes=dict(_FLASH_OPCODES_NOR),
        description=f"{density_mbit} Mb ({capacity//(1024*1024)} MB) SPI NOR Flash",
        notes=[f"{family}-family inference: density {density_mbit} Mb"],
        fpga_ports=FpgaSidePorts(reset_n=True),
    )


def _ads_adc(mpn: str) -> dict:
    """TI ADS54xx / ADS58xx / ADS42xx high-speed ADC family."""
    m = re.search(r"ADS(\d+)", mpn, re.I)
    family_no = int(m.group(1)) if m else 5404
    # Crude classification:
    if 5400 <= family_no < 5500:
        sample_rate = 1_000_000_000  # 1 GSPS
        bits = 14
        intf = "lvds"
    elif 5800 <= family_no < 5900:
        sample_rate = 250_000_000
        bits = 16
        intf = "lvds"
    elif 4200 <= family_no < 4300:
        sample_rate = 250_000_000
        bits = 12
        intf = "parallel"
    else:
        sample_rate = 100_000_000
        bits = 12
        intf = "parallel"
    return dict(
        bus="adc", family="ADS54xx" if family_no >= 5400 else "ADS",
        manufacturer="Texas Instruments",
        adc_resolution_bits=bits, adc_max_sample_rate_hz=sample_rate,
        adc_interface=intf, adc_n_channels=2,
        spi_max_clock_hz=10_000_000, spi_mode=0, spi_data_width_bits=24,
        description=f"{bits}-bit {sample_rate//1_000_000} MSPS ADC, {intf.upper()} output",
        notes=[f"ADS54xx-family inference: ~{sample_rate//1_000_000} MSPS, {intf}"],
        fpga_ports=FpgaSidePorts(devclk=True, sysref=(intf == "jesd204b")),
    )


def _hmc_pll(mpn: str) -> dict:
    """ADI HMC PLL/synth family - SPI controlled, register-write at boot."""
    return dict(
        bus="spi", family="HMC", manufacturer="Analog Devices",
        spi_max_clock_hz=10_000_000, spi_mode=0, spi_data_width_bits=24,
        pll_config_reg_count=12, pll_config_bit_width=24,
        pll_lock_time_us=100,
        pll_min_output_freq_hz=25_000_000,
        pll_max_output_freq_hz=6_000_000_000,
        description="ADI HMC-series RF PLL synthesizer (SPI-configured)",
        notes=["HMC-family inference: 24-bit SPI control, ~12 config regs"],
        fpga_ports=FpgaSidePorts(sync=True),
    )


def _lmk_clock_dist(mpn: str) -> dict:
    """TI LMK clock-distribution / clock-cleaner."""
    return dict(
        bus="spi", family="LMK", manufacturer="Texas Instruments",
        spi_max_clock_hz=10_000_000, spi_mode=0, spi_data_width_bits=32,
        pll_config_reg_count=160, pll_config_bit_width=32,
        pll_lock_time_us=1000,
        pll_min_output_freq_hz=4_096,
        pll_max_output_freq_hz=3_240_000_000,
        description="TI LMK clock distribution + jitter cleaner",
        notes=["LMK-family inference: 32-bit SPI control, ~160 config regs"],
        fpga_ports=FpgaSidePorts(sync=True),
    )


def _ft232_uart_bridge(mpn: str) -> dict:
    """FTDI FT232x USB-to-serial bridge."""
    return dict(
        bus="uart", family="FT232", manufacturer="FTDI",
        uart_max_baud_hz=12_000_000, uart_levels="ttl",
        description="FTDI FT232 USB-to-serial bridge",
        notes=["FT232-family inference: TTL levels, host-side baud"],
        fpga_ports=FpgaSidePorts(),
    )


def _max32_uart_xceiver(mpn: str) -> dict:
    """MAXIM MAX232/MAX3232 RS-232 transceiver."""
    return dict(
        bus="uart", family="MAX32", manufacturer="Maxim",
        uart_max_baud_hz=460_800, uart_levels="rs232",
        description="MAX232/MAX3232 RS-232 transceiver (TTL <-> ±12V)",
        notes=["MAX232-family inference: RS-232 levels"],
        fpga_ports=FpgaSidePorts(),
    )


_FAMILY_RULES: tuple[tuple[re.Pattern, callable, float], ...] = (
    (re.compile(r"^AT24C\d+",       re.I), _at24_eeprom,      0.85),
    (re.compile(r"^(N25Q|W25Q|S25FL)\d+", re.I), _n25q_w25q_flash, 0.85),
    (re.compile(r"^ADS\d+",         re.I), _ads_adc,           0.75),
    (re.compile(r"^HMC[\dA-Z]+",    re.I), _hmc_pll,           0.70),
    (re.compile(r"^LMK\d+",         re.I), _lmk_clock_dist,    0.75),
    (re.compile(r"^FT232",          re.I), _ft232_uart_bridge, 0.85),
    (re.compile(r"^MAX3?232",       re.I), _max32_uart_xceiver, 0.85),
)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


@lru_cache(maxsize=512)
def _resolve_uncached(mpn: str, hint_bus: str = "",
                      datasheet_url: str = "") -> ComponentSpec:
    mpn = (mpn or "").strip()
    if not mpn:
        return _generic_fallback(mpn, hint_bus)

    # 1. Curated override - highest priority, hand-vetted.
    curated = _SPEC_DIR / f"{mpn}.json"
    if curated.exists():
        try:
            data = json.loads(curated.read_text(encoding="utf-8"))
            data["source"] = "curated"
            spec = ComponentSpec(**data)
            log.info("component_spec.curated mpn=%s confidence=%s",
                     mpn, spec.confidence)
            return spec
        except Exception as e:
            log.warning("component_spec.curated_parse_failed mpn=%s: %s",
                        mpn, e)

    # 2. Family inference - 9 prefix rules cover ~80% of common parts.
    family_spec: Optional[ComponentSpec] = None
    for rx, builder, conf in _FAMILY_RULES:
        if rx.match(mpn):
            try:
                fields = builder(mpn)
                fields.setdefault("mpn", mpn)
                fields["source"] = "family_inferred"
                fields["confidence"] = conf
                family_spec = ComponentSpec(**fields)
                log.info("component_spec.family_inferred mpn=%s "
                         "family=%s confidence=%s",
                         mpn, family_spec.family, conf)
                break
            except Exception as e:
                log.warning("component_spec.family_build_failed mpn=%s: %s",
                            mpn, e)

    # 3. Datasheet extractor (LLM). Run when we have a URL AND either:
    #    - no family match, OR
    #    - family match has confidence < 0.85 and we want to upgrade
    extractor_spec: Optional[ComponentSpec] = None
    if datasheet_url and (
        family_spec is None or family_spec.confidence < 0.85
    ):
        try:
            from services.datasheet_extractor import extract_from_url
            extractor_spec = extract_from_url(datasheet_url, mpn, hint_bus)
            if extractor_spec:
                log.info("component_spec.datasheet_extracted mpn=%s "
                         "confidence=%s", mpn, extractor_spec.confidence)
        except Exception as e:
            log.warning("component_spec.datasheet_extract_failed mpn=%s: %s",
                        mpn, e)

    # Pick the best of {family, extractor} by confidence.
    candidates = [s for s in (family_spec, extractor_spec) if s is not None]
    if candidates:
        return max(candidates, key=lambda s: s.confidence)

    # 4. Generic fallback - explicit confidence=0, flagged in design report.
    return _generic_fallback(mpn, hint_bus)


def _generic_fallback(mpn: str, hint_bus: str) -> ComponentSpec:
    bus = (hint_bus or "spi").lower()
    log.warning(
        "component_spec.generic_fallback mpn=%s bus=%s - "
        "RTL emitter will use generic logic; verify against datasheet.",
        mpn or "(empty)", bus,
    )
    return ComponentSpec(
        mpn=mpn or "UNKNOWN",
        bus=bus,
        source="generic_fallback",
        confidence=0.0,
        description=f"Generic {bus.upper()} device - DATASHEET-CHECK NEEDED",
        notes=[
            "DATASHEET-CHECK NEEDED: this peripheral did not match any "
            "curated spec or family rule. The RTL emitter has used "
            "generic defaults. Verify slave address, clock speed, "
            "command opcodes, and timing against the actual datasheet.",
        ],
    )


def resolve(mpn: str, hint_bus: str = "",
            datasheet_url: str = "") -> ComponentSpec:
    """Public entry point. Returns a ComponentSpec for ANY MPN.

    If a `datasheet_url` is provided (typically from the P1 BOM), the
    LLM datasheet extractor runs to fill in the gaps that family
    inference can't cover (slave addresses, opcode tables, JESD lane
    counts, etc.). The extractor's result wins ONLY when its confidence
    beats the family-inferred fallback.
    """
    return _resolve_uncached(mpn, hint_bus, datasheet_url)


def clear_cache() -> None:
    """Mostly for tests - drops the LRU between runs."""
    _resolve_uncached.cache_clear()
