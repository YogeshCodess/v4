"""
component_spec.py - dynamic per-component specification.

Captures the datasheet-derived parameters needed to emit project-specific
RTL for ANY MPN. The schema is intentionally a UNION across all common
bus types so a single ComponentSpec object can describe an EEPROM, an
ADC, a PLL, or a SPI flash without per-bus subclassing.

Resolution sources, in priority order (see services/component_spec_resolver):

    curated           : data/component_specs/<MPN>.json (hand-written)
    family_inferred   : MPN prefix matches a known family (AT24/N25Q/...)
    llm_extracted     : LLM read the datasheet URL and extracted params
    generic_fallback  : nothing resolved - emit code with DATASHEET-CHECK
                        markers and a red flag in the design report

The `source` + `confidence` fields are stamped on every spec so the
consumer (RTL emitter, design report, audit) knows how much to trust it.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


SpecSource = Literal[
    "curated", "family_inferred", "llm_extracted", "generic_fallback",
]


class FpgaSidePorts(BaseModel):
    """Extra signals the FPGA must drive to / receive from the component
    that aren't part of the bus protocol itself.

    Example: an ADC over JESD204B needs DEVCLK + SYSREF from the FPGA;
    a PLL needs SYNC; an EEPROM needs WP tied off."""
    sysref: bool = False
    devclk: bool = False
    sync:   bool = False
    write_protect: bool = False
    driver_enable: bool = False
    reset_n: bool = False
    interrupt: bool = False
    extra: list[str] = Field(default_factory=list)


class ComponentSpec(BaseModel):
    """Datasheet-derived configuration parameters for one component.

    Most fields are Optional - only the ones relevant to the bus type
    will be populated. The RTL emitters check `bus` first then read the
    fields they care about.
    """
    # Identity ---------------------------------------------------------
    mpn:         str
    manufacturer: str = ""
    family:      str = ""                # e.g. "AT24", "N25Q", "ADS54xx"
    bus:         str                     # spi / i2c / uart / adc / dac / pll / flash / gpio / lvds
    description: str = ""
    datasheet_url: Optional[str] = None
    source:      SpecSource = "generic_fallback"
    confidence:  float = 0.0             # 0.0 - 1.0, surfaced in audit

    # I2C parameters ---------------------------------------------------
    i2c_slave_addr_7bit: Optional[int] = None    # e.g. 0x50
    i2c_addr_width_bits: Optional[int] = None    # e.g. 16 for 32KB EEPROM
    i2c_page_size_bytes: Optional[int] = None    # e.g. 64
    i2c_max_clock_hz:    Optional[int] = None    # e.g. 400_000
    i2c_write_cycle_ms:  Optional[int] = None    # e.g. 5

    # SPI parameters ---------------------------------------------------
    spi_max_clock_hz:    Optional[int] = None    # e.g. 20_000_000
    spi_mode:            Optional[int] = None    # 0..3 (CPOL,CPHA)
    spi_data_width_bits: Optional[int] = None    # 8/16/24/32
    spi_cs_active_low:   bool = True
    spi_dummy_cycles:    Optional[int] = None    # for fast-read flash

    # SPI Flash specifics ---------------------------------------------
    flash_capacity_bytes:  Optional[int] = None  # e.g. 32 * 1024 * 1024
    flash_page_size_bytes: Optional[int] = None  # e.g. 256
    flash_sector_size_bytes: Optional[int] = None # e.g. 4096
    flash_opcodes: dict[str, int] = Field(default_factory=dict)
    # Common keys: read, fast_read, page_program, sector_erase,
    #              chip_erase, write_enable, write_disable,
    #              read_status, write_status, read_id

    # PLL / clock-synth specifics --------------------------------------
    pll_config_reg_count:   Optional[int] = None   # how many regs to write at boot
    pll_config_bit_width:   Optional[int] = None   # SPI word size (24 typ)
    pll_lock_time_us:       Optional[int] = None
    pll_min_output_freq_hz: Optional[int] = None
    pll_max_output_freq_hz: Optional[int] = None

    # ADC specifics ----------------------------------------------------
    adc_resolution_bits: Optional[int] = None
    adc_max_sample_rate_hz: Optional[int] = None
    adc_interface: Optional[str] = None     # "parallel" | "lvds" | "jesd204b"
    adc_jesd_lanes:    Optional[int] = None
    adc_n_channels:    Optional[int] = None

    # DAC specifics ----------------------------------------------------
    dac_resolution_bits: Optional[int] = None
    dac_max_update_rate_hz: Optional[int] = None
    dac_interface: Optional[str] = None     # "parallel" | "lvds" | "jesd204b"
    dac_n_channels: Optional[int] = None

    # UART / USB-bridge specifics --------------------------------------
    uart_max_baud_hz: Optional[int] = None
    uart_levels:      Optional[str] = None  # "ttl" | "rs232" | "rs485"

    # FPGA-side ports the component requires ---------------------------
    fpga_ports: FpgaSidePorts = Field(default_factory=FpgaSidePorts)

    # Free-form notes (rendered in design report verbatim) -------------
    notes: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    def is_trustworthy(self) -> bool:
        """Used by the audit gate. A spec is 'trusted' if it was either
        hand-curated or LLM-extracted with high confidence (>=0.8). All
        other sources mean the RTL emitter is operating on guesses."""
        return self.source in ("curated", "llm_extracted") and self.confidence >= 0.8

    def needs_review(self) -> bool:
        return not self.is_trustworthy()
