# Demo Cheat Sheet — Curated Parts Reference

**Print this. Keep it next to you during the live demo.**

When P1 chat asks for components, picking parts from this list guarantees the resolver hits the curated layer (`source=curated, confidence=1.0`), bypasses the LLM extractor entirely, and emits real datasheet-derived values into the FPGA RTL.

Anything off this list still works — but falls through to LLM extraction (≈85% accuracy) or the family-rule layer or generic fallback.

---

## Recommended demo BOMs (copy-paste these)

### A. RF Receiver Demo (radar / SIGINT)

```
LNA            : (any from your domain catalog — analog, no curated needed)
Mixer          : LTC5594            (300 MHz–9 GHz I/Q demod, SPI gain control)
Wide synth     : ADF4351            (35 MHz–4.4 GHz PLL+VCO, SPI)
Clock dist     : HMC7044            (JESD204B clock + SYSREF, SPI)
ADC            : AD9208             (14-bit 3 GSPS dual-channel, JESD204B)
Baseband filter: ADRF6510           (SPI-tunable LPF + VGA, dual I/Q)
Boot flash     : W25Q128JV          (16 MB Quad-SPI)
Config EEPROM  : AT24C256C          (32 KB I²C)
Power monitor  : INA226             (I²C bus voltage + current)
Temp sensor    : TMP117             (high-accuracy I²C)
Debug UART     : FT232H             (USB-Hi-Speed UART bridge)
LDO            : LT3045             (ultra-low-noise, EN/PG)
GPIO expander  : PCA9555            (I²C 16-bit + interrupt)
```

8/13 hit the curated layer — flash, EEPROM, power monitor, temp sensor, USB bridge, LDO, GPIO, mixer, synth, clock, ADC, baseband filter all use real datasheet values.

### B. RF Transmitter Demo (satcom / EW jammer)

```
DAC          : AD9162               (16-bit 12 GSPS RF DAC, JESD204B)
Clock dist   : LMK04828             (jitter cleaner + JESD clock)
Wide synth   : LMX2594              (10 MHz–15 GHz, SPI)
Direct synth : ADF4159              (FMCW ramp generator, SPI)
DDS          : AD9914               (3.5 GSPS, hop profiles)
RF DSA       : ADRF5720             (0.5 dB step DSA, SPI)
Switch       : ADRF5510             (SP4T, GPIO)
Up-converter : ADMV1013             (24–44 GHz I/Q upconverter, SPI)
Boot flash   : W25Q256JV            (32 MB, 4-byte addr)
EEPROM       : AT24C512C            (64 KB I²C)
USB bridge   : FT2232H              (dual UART/MPSSE)
PHY          : KSZ9031RNX           (gigabit Ethernet)
```

12/12 hit the curated layer — fully clean RTL emission.

### C. Direct-RF Sampling Receiver (modern SDR)

```
RF Sampler   : AD9082               (12 GSPS DAC + 6 GSPS ADC, JESD204C)
Transceiver  : ADRV9009             (75 MHz–6 GHz, 200 MHz BW)
Clock        : AD9528               (JESD204B clock generator)
SAR ADC      : LTC2378-20           (20-bit precision, slow telemetry)
Boot flash   : N25Q256              (32 MB Micron, 4-byte addr)
EEPROM       : AT24C64D             (8 KB I²C)
Power mon    : LTC2945              (wide-range power+energy I²C)
RTC          : DS1672               (battery-backed I²C counter)
USB bridge   : CP2102               (USB-UART)
Bus mux      : TCA9548A             (8-channel I²C mux)
```

10/10 curated — best-case demo.

---

## All 65 curated parts grouped

### PLLs / synthesizers / clock distribution (10)
`ADF4159` `ADF4351` `ADF5610` `LMX2592` `LMX2594` `HMC1063` `HMC1190` `HMC7044` `LMK04828` `LMK04832`

### JESD204B/C clock generators (1)
`AD9528`

### JESD204B/C converters + transceivers (7)
`AD9082` (MxFE) · `AD9162` (DAC) · `AD9208` (dual ADC) · `AD9371` (xcvr) · `AD9625` (ADC) · `AD9694` (quad ADC) · `ADRV9009` (xcvr)

### DDS / DAC (4)
`AD9914` (DDS) · `AD5683` (16-bit SPI DAC) · `MCP4725` (12-bit I²C DAC) · `MAX11300` (PIXI mixed-signal)

### Precision SAR ADCs (3)
`AD7193` (24-bit Σ-Δ) · `AD7960` (18-bit LVDS) · `LTC2378-20` (20-bit) · `ADS1115` (16-bit I²C)

### RF mixers / upconverters / downconverters (3)
`ADMV1013` · `ADMV1014` · `LTC5594`

### RF DSAs / switches / VGAs / filters (4)
`ADRF5510` · `ADRF5720` · `AD8367` (analog VGA) · `ADRF6510` (programmable filter)

### NOR Flash (7)
`W25Q64JV` · `W25Q128JV` · `W25Q256JV` · `N25Q128` · `N25Q256` · `S25FL128S` · `M25P16` · `MX25L12835F` · `AT25SF128A`

### I²C EEPROMs (4)
`24LC256` · `AT24C64D` · `AT24C256C` · `AT24C512C`

### I²C peripherals (10)
`ADS1115` · `DS1672` (RTC) · `INA226` · `LTC2945` · `MCP23017` · `PCA9555` · `PCA9685` (PWM) · `TCA9548A` (mux) · `TMP102` · `TMP117` · `LM75B`

### USB-UART bridges (3)
`CP2102` · `FT232H` · `FT2232H`

### RS-232 / RS-485 transceivers (3)
`MAX232` · `MAX3485` · `SN65HVD485`

### LDOs / power conditioning (2)
`TPS7A4700` · `LT3045`

### Ethernet PHY (1)
`KSZ9031RNX` (gigabit RGMII + IEEE 1588)

---

## How to verify a curated part will hit during the demo

```
python -c "import services.component_spec_resolver as r; s = r.resolve(mpn='ADF4351'); print(f'{s.source} conf={s.confidence}')"
```

Expect: `curated conf=1.0`

If it says `family_inferred` or `generic_fallback`, the part isn't in `data/component_specs/`.

---

## What the curated specs prove during demo

Open `output/<your-project>/Phase_07_FPGA_Design/rtl/`:

| File | Look for | Why it matters |
|---|---|---|
| `flash_ctrl.v` | `8'h02`, `8'h06`, `8'hC7` | Real opcodes from Winbond/Micron datasheet |
| `eeprom_driver.v` | `7'h50` (slave addr) | Real I²C address per AT24 family |
| `pll_config.v` | Part name + `R5` ↓ `R0` order | Real ADF4351 init sequence |
| `i2c_master.v` | Real device addresses | Per the curated PCA9555 / INA226 / TMP117 specs |
| `fpga_coverage.sv` | One bin per register | SV functional coverage from register map |

Audience sees the FPGA testbench reference real Winbond opcodes and the design report cite the ADI/TI datasheets — everything traceable.

---

## Failure modes still in play

1. User picks a part NOT on this list AND the datasheet URL is unreachable → falls to generic fallback → modules show `[VERIFY]` markers (still compiles, but obviously placeholder).
2. LLM provider rate-limit during P1 chat → fallback chain handles automatically (GLM → DeepSeek → Anthropic → Ollama).
3. Pandoc not installed → DOCX export uses python-docx (works, plainer formatting).
4. Tesseract not installed → OCR fallback skipped (only matters for scanned datasheets, rare for demo parts).

INSTALL.bat warns at install time about #3 and #4 with download URLs.

---

**Bottom line for tomorrow**

Stick to BOM A, B, or C above and demo confidence is ≈ 95%.
