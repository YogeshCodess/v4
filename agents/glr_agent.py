"""
Phase 6: GLR (Glue Logic Requirements) Generation Agent

Generates a complete Glue Logic Requirements document matching defense electronics
standards — scope, references, acronyms, FPGA description, pinout table,
functional specifications, and Requirement Traceability Matrix.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

from agents.base_agent import BaseAgent
from config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert FPGA/embedded hardware engineer generating a Glue Logic Requirements (GLR) document for a defense/industrial electronics project.

A GLR document specifies the I/O details and functional requirements of the FPGA, bridging the Netlist (P4) and FPGA HDL Design (P7) phases.

## DOCUMENT STRUCTURE — generate EVERY section below in FULL detail:

---

# Glue Logic Requirements (GLR)

## Document Control
| Document Title | Glue Logic Requirements |
| Version Date | {date} |
| Version Number | 0V01 |
| Prepared By | Name: . Sign: |
| Document Review By | Name: . Sign: |

---

## Amendments to the Document
| S. No. | Ver. No. | Ver. Date | Changed By | Section(s) Changed | Description of Change |
| 1 | 0V01 | {date} | - | - | Initial Version |

---

## 1. Scope of the Document
This document explains the IO details and functional requirements of the FPGA for {project_name}. Targeted audience: Hardware Design and Firmware teams.

---

## 2. References

### 2.1 External
| Doc. Type | Part No. | Description |
List all external component datasheets: FPGA, EEPROM, Flash memories, ADC/DAC, temperature sensors, power management ICs, communication ICs — all from the provided BOM.

### 2.2 Internal
| Reference | Document |
| [HRS] | Hardware Requirements Specification |
| [SCH] | Schematic |
| [GRS] | General Requirements Specification |
| [GDD] | General Design Document |

---

## 3. Acronyms and Abbreviations
Table with minimum 20 acronyms used in the document:
| Acronym | Expansion |

Must include: FPGA, UART, SPI, I2C, GPIO, JTAG, CLB, DSP, LUT, FF, IO, PCB, BOM, RoHS, EMC, ADC, DAC, DMA, RTL, HDL, VHDL, VCC, GND, etc.

---

## 4. Module Overview
Describe the hardware module in full. Divide into three subsections:

**RF SECTION:**
Describe RF components: transceivers, amplifiers, phase shifters, attenuators, frequency synthesizers. Include part numbers from BOM.

**DIGITAL SECTION:**
Describe the FPGA role: signal processing, control logic, communication interfaces, memory interfaces. Include FPGA part number.

**POWER SUPPLY SECTION:**
Describe power rails, regulators, sequencing. List voltage rails (e.g. +3.3V, +1.8V, +1.0V, +5V) and their sources.

---

## 5. Features
Bullet list of ALL key hardware features (minimum 8 items):
- FPGA: [exact part number and family from BOM]
- On-board clock oscillator: [frequency]
- Communication: [UART/SPI/I2C with speed]
- JTAG debugging support
- EEPROM: [part number, capacity, purpose]
- Storage Flash: [part number, capacity, purpose]
- Configuration Flash: [part number, capacity, purpose]
- Temperature Monitoring: [part number, interface]
- Power monitoring: [part number, interface]
- [any other notable features from the requirements]

---

## 6. FPGA Description
State the FPGA selection rationale. Then provide this complete specification table:

| S.NO | PARAMETERS | SPECIFICATION |
|------|-----------|---------------|
| 1 | Part Number | [actual part from BOM] |
| 2 | Logic Cells | [value] |
| 3 | CLB Flip-Flops | [value] |
| 4 | Number of Gates | [value] |
| 5 | Maximum Distributed RAM (Kb) | [value] |
| 6 | Total Block RAM (Kb) | [value] |
| 7 | Maximum Single-Ended I/Os | [value] |
| 8 | Maximum DSP Slices | [value] |
| 9 | No of IO Bank | [value] |

---

## 7. Block Diagram
(Reference to block diagram — described in text)

---

## 8. Pinout Details

**Table: FPGA Pin Out Details**

Generate a COMPLETE pinout table with ALL signals from the netlist. Minimum 35 signals:

| S.No | Signal Name | Pin No | Voltage Level | Direction wrt FPGA | Source | Destination | Default Condition | Voltage Standard |

Include signals in these groups:
- **Power & Ground**: VDD_FPGA, GND, VCCO (per bank)
- **Clock**: FPGA_CLK_125M (or as applicable), PLL_REF_CLK
- **JTAG**: TCK, TDI, TDO, TMS
- **Reset**: FPGA_RESET_N, POR_N
- **UART/Serial**: UART_TX, UART_RX, UART_CTS, UART_RTS
- **SPI (EEPROM)**: SPI_CLK, SPI_MOSI, SPI_MISO, SPI_CS_EEPROM_N
- **SPI (Flash)**: FLASH_CLK, FLASH_MOSI, FLASH_MISO, FLASH_CS_N
- **I2C (Temperature/Power)**: I2C_SCL, I2C_SDA
- **GPIO / Control**: TRP, LED_STATUS, FPGA_DONE, FPGA_INIT_N
- **RF Control** (if RF project): PHASE_SHIFT_DATA, PHASE_SHIFT_CLK, PA_ENABLE, ATT_DATA
- **High Speed** (if applicable): SRXIO_P/N, STXIO_P/N
- All additional signals from the netlist

For voltage standards, use: LVTTL (3.3V logic), LVCMOS33, LVCMOS18, SSTL15, DIFF_SSTL15 etc.

---

## 9. Functional Specifications

**Summary table first:**

| S.No. | Function Name | Description |
|-------|--------------|-------------|
| 1 | Serial Communication Interface | UART between PC & FPGA via USB-UART (RS422/RS232) |
| 2 | High Speed Communication Interface | [GTP/SERDES lines, data rate] |
| 3 | Power Supply Sequencing & Health Status | Based on supply voltage, control PA drain voltage and monitor health |
| 4 | Supply Voltage, Current & Temperature Monitoring | I2C-based monitoring |
| 5 | Flash Interfaces | Configuration and Storage flash via SPI/QSPI |
| 6 | TRP Configuration | TRP signal for RF ON/OFF control |
| 7 | FPGA Remote Programming | Configuration loading via communication interface |
| 8 | Phase Shifter Controlling (if RF) | Frequency/Azimuth/Elevation-based phase control |
| 9 | Beam Steering Calculation (if RF) | Beam steering logic |

Then provide DETAILED subsections:

### 9.1 Serial Communication Interface
- Interface type: UART
- Physical layer: [RS422 / RS232 / TTL]
- Baud rate: [value from requirements, e.g. 12 Mbps or 115200 bps]
- Frame format: 1 start bit, 8 data bits, 1 stop bit, no parity
- USB-UART converter IC: [part number from BOM]
- Signals: UART_TX (FPGA → PC), UART_RX (PC → FPGA)
- Protocol: Custom register-based command/response

### 9.2 High Speed Communication Interface
- Interface: [GTP/SERDES / Ethernet / PCIe / SRIO]
- Number of lanes: [value]
- Data rate per lane: [value, e.g. 5 Gbps]
- Protocol: [Serial RapidIO / PCIe / custom]
- Physical: [SFP / backplane connector]

### 9.3 Power On/Off Sequence
#### 9.3.1 Power ON/OFF Sequence
Step-by-step sequence:
1. Input supply (+V) detected — FPGA_PG_IN asserted
2. FPGA core voltage enabled (1.0V → 1.8V → 3.3V sequencing)
3. FPGA DONE signal asserted after configuration load
4. PA drain voltage enabled (TRP signal)
5. System READY status

#### 9.3.2 Mode Configuration
| Mode | Signal | Value | Description |
|------|--------|-------|-------------|
| Normal | MODE[1:0] | 2'b00 | Normal operating mode |
| Test | MODE[1:0] | 2'b01 | Built-in self-test |
| Programming | MODE[1:0] | 2'b10 | FPGA remote programming mode |

### 9.4 Supply Voltage, Current & Temperature Monitoring
#### 9.4.1 Supply Voltage and Current Monitoring
- IC Part Number: [from BOM — LTC2992 or similar]
- Interface: I2C at [address]
- Monitored rails: [list voltage rails]
- Measurement range: 0 to [max]V, 0 to [max]A
- Resolution: [value]mV / [value]mA

#### 9.4.2 Temperature Monitoring
- IC Part Number: [from BOM — AD7416 / AMC7834 / LM75 or similar]
- Interface: I2C at [address]
- Temperature range: [min] to [max] °C
- Resolution: [value]°C (10-bit ADC)
- Alert threshold: [value]°C

### 9.5 Flash & Interfaces
#### 9.5.1 Configuration Flash
- Part Number: [from BOM — IS25LP256D / S25FL512S or similar]
- Interface: QSPI (quad SPI)
- Capacity: [value] Mb
- Purpose: Stores FPGA programming bitstream for remote programming
- Programming: Via USB-UART interface through GUI tool

#### 9.5.2 Storage Flash (User Flash)
- Part Number: [from BOM — MT25QU02G or similar]
- Interface: QSPI
- Capacity: [value] Mb/Gb
- Purpose: Stores calibration data, attenuation/phase tables, gate voltage tables

### 9.6 TRP Configuration
- Signal: TRP (Transmit/Receive Pulse)
- Direction: FPGA → RF front-end
- Logic level: [3.3V LVTTL]
- Active state: HIGH = TX mode enabled
- Timing: Minimum [value] µs pulse width
- Control: Written via UART register command

### 9.7 FPGA Remote Programming
- Protocol: UART at [baud rate]
- Tool: GUI application on host PC
- Procedure:
  1. Host sends programming command via UART
  2. FPGA enters programming mode (MODE = 2'b10)
  3. Bitstream transferred in [value]-byte packets
  4. Configuration flash written via FPGA SPI master
  5. FPGA reboots from new configuration
- Fallback: JTAG programming via debug header

### 9.8 Phase Shifter Controlling (RF projects)
- Control basis: Frequency, Azimuth angle, Elevation angle
- Interface: SPI (CLK, DATA, LATCH)
- Phase resolution: [value] bits
- Update rate: [value] Hz
- Phase table: Pre-computed and stored in Storage Flash

### 9.9 Beam Steering Calculation
- Algorithm: [Taylor / Chebyshev / Uniform weighting]
- Computation: [FPGA-based / pre-computed LUT]
- Inputs: Target azimuth, elevation, frequency
- Outputs: Per-element phase and amplitude weights

### 9.10 Gate Voltage Writing in DAC (if applicable)
- DAC Part Number: [from BOM — AMC7834 or similar]
- Interface: SPI
- Number of channels: [value]
- Voltage range: [min] to [max]V
- Resolution: [value] bits
- Purpose: PA gate bias control for each RF channel

---

---

## 10. Software Register Address Map

This section is CRITICAL for firmware development — it defines the complete FPGA register address space as seen by the software.

### 10.1 Register Base Addresses

| Block Name | Base Address | Address Range | Description |
|------------|-------------|---------------|-------------|
| System / Identification | 0x0000 | 0x0000–0x00FF | Board ID, firmware version, status |
| UART Control | 0x0100 | 0x0100–0x01FF | Baud rate, FIFO control, status |
| SPI Control | 0x0200 | 0x0200–0x02FF | SPI master, chip-select control |
| I2C Control | 0x0300 | 0x0300–0x03FF | I2C master, device address, data |
| GPIO | 0x0400 | 0x0400–0x04FF | General purpose I/O control |
| PLL Control | 0x0500 | 0x0500–0x05FF | PLL N/R dividers, status, config |
| Temperature Monitor | 0x0600 | 0x0600–0x06FF | Temp sensor readings, alert threshold |
| Power Monitor | 0x0700 | 0x0700–0x07FF | Voltage/current ADC readings per rail |
| RF Control | 0x0800 | 0x0800–0x08FF | TRP, PA enable, attenuator, phase shift |
| Flash / EEPROM | 0x0900 | 0x0900–0x09FF | Flash address, data, command register |
| Diagnostics | 0x0A00 | 0x0A00–0x0AFF | Fault log, uptime counter, loopback |

### 10.2 Detailed Register Map

For EACH block, provide the full register table:

**Block 0x0000 — System / Identification**

| Offset | Register Name | Width | R/W | Reset Value | Bit-Field Description |
|--------|--------------|-------|-----|-------------|----------------------|
| 0x00 | BOARD_ID | 16 | R | 0x[XX] | Board identification code |
| 0x01 | FW_VERSION_MAJOR | 16 | R | 0x0001 | Firmware major version |
| 0x02 | FW_VERSION_MINOR | 16 | R | 0x0000 | Firmware minor version |
| 0x03 | SYS_STATUS | 16 | R | 0x0000 | [15:8] Reserved, [7] PLL_LOCKED, [6] TEMP_ALERT, [5] VOLT_FAULT, [4:0] Reserved |
| 0x04 | SYS_CTRL | 16 | R/W | 0x0000 | [0] SOFT_RESET, [1] WDT_ENABLE, [2] RF_ENABLE |

**Block 0x0100 — UART Control**

| Offset | Register Name | Width | R/W | Reset Value | Bit-Field Description |
|--------|--------------|-------|-----|-------------|----------------------|
| 0x00 | BAUD_DIV | 16 | R/W | 0x0036 | Baud rate divisor = FPGA_CLK / (16 × BAUD_RATE) |
| 0x01 | UART_CTRL | 16 | R/W | 0x0001 | [0] UART_ENABLE, [1] LOOPBACK_EN, [2] CRC_EN |
| 0x02 | UART_STATUS | 16 | R | 0x0000 | [0] TX_BUSY, [1] RX_AVAIL, [2] RX_OVERRUN, [3] FRAME_ERR |
| 0x03 | TX_FIFO_COUNT | 16 | R | 0x0000 | Number of bytes in TX FIFO |
| 0x04 | RX_FIFO_COUNT | 16 | R | 0x0000 | Number of bytes in RX FIFO |

**Block 0x0500 — PLL Control**

| Offset | Register Name | Width | R/W | Reset Value | Bit-Field Description |
|--------|--------------|-------|-----|-------------|----------------------|
| 0x00 | PLL_N_DIV | 16 | R/W | 0x0020 | PLL feedback N divider (integer) |
| 0x01 | PLL_R_DIV | 16 | R/W | 0x0001 | PLL reference R divider |
| 0x02 | PLL_CTRL | 16 | R/W | 0x0000 | [0] PLL_ENABLE, [1] PLL_RESET, [2] PLL_BYPASS |
| 0x03 | PLL_STATUS | 16 | R | 0x0000 | [0] PLL_LOCKED, [1] PLL_LOSS_OF_LOCK, [2] PLL_ERROR |
| 0x04 | PLL_LOCK_TIMEOUT | 16 | R/W | 0x0064 | Lock timeout in 1ms units (default 100ms) |

(Generate complete register tables for ALL blocks listed in Section 10.1, deriving bit definitions from the functional specifications in Section 9)

### 10.3 Register Access Rules
- All registers are 16-bit wide; accessed via UART Single/Bulk Read/Write protocol (Section 9.1)
- Read: set bit15 of address (address OR 0x8000)
- Write: address as-is
- Shadow registers: PLL_N_DIV and PLL_R_DIV are double-buffered; write PLL_CTRL[0]=0 then 1 to apply
- Atomic access: Bulk Write used for multi-register atomic updates (e.g. frequency change)

---

## 11. UART Register Protocol Specification

This section provides the EXACT byte-level frame format for the UART register protocol. Firmware MUST implement this exactly.

### 11.1 Physical Layer
- Baud rate: [value from Section 9.1] (configurable via UART_CTRL.BAUD_DIV)
- Frame format: 1 start bit, 8 data bits, 1 stop bit, no parity (8N1)
- Physical interface: [RS-422 / RS-232 / TTL — from BOM]
- Signal levels: [value from BOM] V logic

### 11.2 Command Frame Formats

**Single Register Write (CMD = 0x57 'W'):**
```
Byte 0: 0x57 (CMD)
Byte 1: ADDR[15:8] (address MSB)
Byte 2: ADDR[7:0]  (address LSB)
Byte 3: DATA[15:8] (data MSB)
Byte 4: DATA[7:0]  (data LSB)
→ Response: 0x06 (ACK) within 1ms, or 0x15 (NAK) on error
Total frame: 5 bytes TX, 1 byte RX
```

**Single Register Read (CMD = 0x52 'R'):**
```
Byte 0: 0x52 (CMD)
Byte 1: (ADDR[15:8] | 0x80)  (MSB with read bit set)
Byte 2: ADDR[7:0]             (address LSB)
→ Response: DATA[15:8], DATA[7:0] within 2ms
Total frame: 3 bytes TX, 2 bytes RX
```

**Bulk Register Write (CMD = 0x42 'B'):**
```
Byte 0: 0x42 (CMD)
Byte 1: ADDR[15:8] (start address MSB)
Byte 2: ADDR[7:0]  (start address LSB)
Byte 3: N          (register count, 1–64)
Byte 4..4+2N-1: DATA[0]_H, DATA[0]_L, ..., DATA[N-1]_H, DATA[N-1]_L
→ Response: 0x06 (ACK) within 5ms, or 0x15 (NAK)
Total frame: (4 + 2N) bytes TX, 1 byte RX
```

**Bulk Register Read (CMD = 0x62 'b'):**
```
Byte 0: 0x62 (CMD)
Byte 1: (ADDR[15:8] | 0x80)  (MSB with read bit set)
Byte 2: ADDR[7:0]             (start address LSB)
Byte 3: N                     (register count, 1–64)
→ Response: DATA[0]_H, DATA[0]_L, ..., DATA[N-1]_H, DATA[N-1]_L within 5ms
Total frame: 4 bytes TX, 2N bytes RX
```

**Error Response:**
```
0x15 (NAK) — sent by FPGA when:
  - CMD byte not recognized (not 0x57, 0x52, 0x42, 0x62)
  - Address out of valid range
  - Write to read-only register
  - Parser timeout (inter-byte gap > 50ms)
```

### 11.3 Protocol Timing Constraints
| Parameter | Min | Typical | Max | Unit |
|-----------|-----|---------|-----|------|
| Inter-byte gap (TX side) | — | — | 50 | ms |
| Single Write response time | — | 0.5 | 1 | ms |
| Single Read response time | — | 1 | 2 | ms |
| Bulk Write response time (N=64) | — | 3 | 5 | ms |
| Bulk Read response time (N=64) | — | 3 | 5 | ms |
| Parser reset on timeout | 50 | — | — | ms |

### 11.4 Software Implementation Notes
```c
// Firmware register write wrapper — always use this macro
#define FPGA_WRITE(addr, data)    UART_WriteReg((uint16_t)(addr), (uint16_t)(data))
// Firmware register read wrapper
#define FPGA_READ(addr, pdata)    UART_ReadReg((uint16_t)(addr) | 0x8000U, (pdata))
// Block registers by base address
#define REG_SYS_BASE    (0x0000U)
#define REG_UART_BASE   (0x0100U)
#define REG_SPI_BASE    (0x0200U)
#define REG_I2C_BASE    (0x0300U)
#define REG_GPIO_BASE   (0x0400U)
#define REG_PLL_BASE    (0x0500U)
#define REG_TEMP_BASE   (0x0600U)
#define REG_PWR_BASE    (0x0700U)
#define REG_RF_BASE     (0x0800U)
#define REG_FLASH_BASE  (0x0900U)
#define REG_DIAG_BASE   (0x0A00U)
```

---

## 12. FPGA Resource Utilization Estimate

| Resource | Available | Estimated Usage | Utilization % |
|---------|-----------|----------------|--------------|
| Slice LUTs | [from FPGA spec] | [estimate] | [X]% |
| Slice Flip-Flops | [from FPGA spec] | [estimate] | [X]% |
| Block RAM (36Kb) | [from FPGA spec] | [estimate] | [X]% |
| DSP Slices | [from FPGA spec] | [estimate] | [X]% |
| MMCM/PLL | [from FPGA spec] | [estimate] | [X]% |
| I/O Buffers | [from FPGA spec] | [estimate] | [X]% |

Synthesis tool: Vivado [version] / Quartus Prime [version]
Target device: [FPGA part number]
Timing constraint: [primary clock frequency] MHz

---

## Annexure A — Requirement Traceability Matrix

| S.No. | GLR-ID | Description | Source HRS Section | GLR Section | Verification Method | Status |
|-------|--------|-------------|-------------------|-------------|--------------------|----|
| 1 | GLR-001 | Serial Communication Interface | HRS §X | 9.1, 11 | Test | Open |
| 2 | GLR-002 | High Speed Communication | HRS §X | 9.2 | Test | Open |
| 3 | GLR-003 | Power Supply Sequencing | HRS §X | 9.3 | Test | Open |
| 4 | GLR-004 | Voltage/Current/Temperature Monitoring | HRS §X | 9.4 | Test | Open |
| 5 | GLR-005 | Flash Interfaces | HRS §X | 9.5 | Test | Open |
| 6 | GLR-006 | TRP Configuration | HRS §X | 9.6 | Inspection | Open |
| 7 | GLR-007 | FPGA Remote Programming | HRS §X | 9.7 | Demonstration | Open |
| 8 | GLR-008 | Phase Shifter Control | HRS §X | 9.8 | Test | Open |
| 9 | GLR-009 | Beam Steering | HRS §X | 9.9 | Analysis | Open |
| 10 | GLR-010 | Register Address Map | HRS §X | 10 | Inspection | Open |
| 11 | GLR-011 | UART Protocol Specification | HRS §X | 11 | Test | Open |
| 12 | GLR-012 | FPGA Resource Budget | HRS §X | 12 | Analysis | Open |

---

## CRITICAL RULES:
- Use ACTUAL part numbers from the provided BOM — never invent part numbers
- Signal names MUST match those in the provided netlist
- Voltage levels MUST match actual component datasheet values
- Do NOT write TBD, TBC, or TBA anywhere — use actual values, engineering defaults, or explicit assumptions
- The pinout table must have at minimum 35 rows
- Section 10 (Software Register Address Map) MUST be complete — this is a MANDATORY deliverable for firmware implementation
- Section 11 (UART Protocol Specification) MUST include exact byte-level frame format tables
- The RTM must reference every HRS requirement with Verification Method column
- Be highly specific and project-relevant — generic placeholder text is not acceptable
"""


class GLRAgent(BaseAgent):
    """Phase 6: Glue Logic Requirements generation — LLM-driven, full document."""

    def __init__(self):
        super().__init__(
            phase_number="P6",
            phase_name="GLR Generation",
            model=settings.primary_model,
            max_tokens=16384,
        )

    def get_system_prompt(self, project_context: dict) -> str:
        return SYSTEM_PROMPT

    async def execute(self, project_context: dict, user_input: str) -> dict:
        output_dir = Path(project_context.get("output_dir", "output"))
        output_dir.mkdir(parents=True, exist_ok=True)
        project_name = project_context.get("name", "Project")
        today = datetime.now().strftime("%d.%m.%Y")

        # Load all prior phase outputs as context
        requirements = self._load_file(output_dir / "requirements.md")
        components    = self._load_file(output_dir / "component_recommendations.md")
        netlist_vis   = self._load_file(output_dir / "netlist_visual.md")
        hrs           = self._load_file(output_dir / f"HRS_{project_name.replace(' ', '_')}.md")
        block_diag    = self._load_file(output_dir / "block_diagram.md")

        user_message = f"""Generate a COMPLETE, DETAILED Glue Logic Requirements (GLR) document for:

**Project:** {project_name}
**Date:** {today}

## Hardware Requirements (P1):
{requirements[:5000] if requirements else '(not available — use component data below)'}

## Component BOM (P1):
{components[:5000] if components else '(not available)'}

## Netlist Signal Connections (P4):
{netlist_vis[:6000] if netlist_vis else '(not available)'}

## HRS Specification (P2):
{hrs[:4000] if hrs else '(not available)'}

## System Block Diagram (P1):
{block_diag[:2000] if block_diag else '(not available)'}

---
Generate the FULL GLR document following EVERY section in your system prompt.
- Include a complete pinout table with 35+ signals derived from the netlist above
- Include ALL 9+ functional specification subsections with full detail
- Use actual part numbers from the BOM, actual signal names from the netlist
- Section 10 (Software Register Address Map) is MANDATORY — generate the complete register table for ALL blocks (System, UART, SPI, I2C, GPIO, PLL, Temp, Power, RF, Flash, Diagnostics) with base addresses, offsets, bit-field descriptions, R/W type, and reset values
- Section 11 (UART Protocol Specification) is MANDATORY — include exact byte-level frame format tables for all 4 command types (Single Write, Single Read, Bulk Write, Bulk Read) + timing table
- Section 12 (FPGA Resource Utilization) is MANDATORY — estimate LUTs, FFs, BRAM, DSP usage %
- Include the complete Requirement Traceability Matrix (Annexure A) with Verification Method column
- Write professional, engineering-grade content — no placeholder text
"""

        glr_content = ""
        try:
            response = await self.call_llm(
                messages=[{"role": "user", "content": user_message}],
                system=SYSTEM_PROMPT,
            )
            glr_content = response.get("content", "")

            # Up to 4 continuation passes if truncated
            _GLR_CONT_PROMPTS = [
                (
                    "Continue the GLR document from exactly where you left off. "
                    "Do NOT repeat sections already written. "
                    "Complete remaining functional specification sections (Section 9), "
                    "including all subsections not yet written."
                ),
                (
                    "Continue the GLR. Do NOT repeat content already written. "
                    "Write Section 10: Software Register Address Map — ALL blocks with complete tables "
                    "(base address, offset, register name, width, R/W, reset value, bit-field description). "
                    "Write Section 11: UART Protocol Specification — byte-level frame format tables for "
                    "Single Write, Single Read, Bulk Write, Bulk Read commands, plus timing constraints table. "
                    "Write Section 12: FPGA Resource Utilization Estimate."
                ),
                (
                    "Continue the GLR. Do NOT repeat content already written. "
                    "Write Annexure A: Requirement Traceability Matrix with ALL GLR-IDs and Verification Method column. "
                    "Write the Verification & Validation section with simulation test plan."
                ),
                (
                    "Finalize the GLR. Do NOT repeat content already written. "
                    "Complete any remaining sub-sections, add the document revision history table, "
                    "and close with the approval sign-off block."
                ),
            ]

            for _pass_idx, _cont_prompt in enumerate(_GLR_CONT_PROMPTS, start=1):
                if response.get("stop_reason") != "max_tokens" or not glr_content:
                    break

                self.log(f"GLR truncated — continuation pass {_pass_idx}/3...")
                _cont = await self.call_llm(
                    messages=[
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": glr_content},
                        {"role": "user", "content": _cont_prompt},
                    ],
                    system=SYSTEM_PROMPT,
                )
                glr_content += "\n\n" + _cont.get("content", "")
                response = _cont

        except Exception as e:
            self.log(f"LLM GLR generation failed: {e}", "warning")

        # Fallback if LLM completely failed
        if not glr_content or len(glr_content) < 500:
            glr_content = self._fallback_template(project_name, today)

        # Scrub forbidden placeholders
        glr_content = re.sub(r'\b(TBD|TBC|TBA)\b', '[specify]', glr_content, flags=re.IGNORECASE)

        # P26 #17 (2026-04-26): coerce + re-render every embedded
        # `mermaid` block so LLM-emitted bracket mismatches (e.g.
        # `["..."]}`), nested `[...]` inside quoted labels, or stray
        # glyphs don't ship to disk and break the in-browser preview.
        try:
            from tools.mermaid_coerce import sanitize_mermaid_blocks_in_markdown
            glr_content = sanitize_mermaid_blocks_in_markdown(glr_content)
        except Exception as _exc:
            self.log(f"GLR mermaid sanitise skipped: {_exc}", "warning")

        safe_name = project_name.replace(' ', '_')

        # Save under both naming conventions for compatibility
        # srs_agent.py reads "glr_specification.md"
        # rdt_psq_agent.py reads "GLR_{project_name}.md"
        for filename in [f"GLR_{safe_name}.md", "glr_specification.md"]:
            glr_file = output_dir / filename
            glr_file.write_text(glr_content, encoding="utf-8")

        self.log(f"GLR generated: {len(glr_content)} chars")

        return {
            "response": "GLR specification generated.",
            "phase_complete": True,
            "outputs": {
                f"GLR_{safe_name}.md": glr_content,
                "glr_specification.md": glr_content,
            },
        }

    def _fallback_template(self, project_name: str, date: str) -> str:
        """Minimal fallback GLR when LLM is unavailable."""
        return f"""# Glue Logic Requirements (GLR)
## For: {project_name}

| Document Title | Glue Logic Requirements |
|---|---|
| Version Date | {date} |
| Version Number | 0V01 |

## 1. Scope
This document specifies the I/O details and functional requirements of the FPGA for {project_name}.

## 2. References
### 2.1 External
| Doc. Type | Part No. | Description |
|---|---|---|
| Datasheet | FPGA | Artix-7 FPGA Family |

### 2.2 Internal
| Reference | Document |
|---|---|
| [HRS] | Hardware Requirements Specification |

## 3. Acronyms
| Acronym | Expansion |
|---|---|
| FPGA | Field Programmable Gate Array |
| UART | Universal Asynchronous Receiver-Transmitter |
| SPI | Serial Peripheral Interface |
| I2C | Inter-Integrated Circuit |
| GPIO | General Purpose Input/Output |
| JTAG | Joint Test Action Group |

## 4. Module Overview

**DIGITAL SECTION:**
FPGA-based control module providing command and control signal distribution.

**POWER SUPPLY SECTION:**
Multi-rail power supply: +3.3V (I/O), +1.8V (FPGA I/O), +1.0V (FPGA core).

## 5. Features
- Artix-7 FPGA for control and signal processing
- UART communication interface
- SPI interface for EEPROM and Flash memory
- I2C interface for temperature and power monitoring
- JTAG debug port

## 6. FPGA Description
| S.NO | PARAMETERS | SPECIFICATION |
|---|---|---|
| 1 | Part Number | XC7A200T-1FB676I |
| 2 | Logic Cells | 215,360 |
| 3 | CLB Flip-Flops | 33,650 |
| 4 | Number of Gates | 1,000,000 |
| 5 | Maximum Distributed RAM (Kb) | 2,888 |
| 6 | Total Block RAM (Kb) | 13,140 |
| 7 | Maximum Single-Ended I/Os | 400 |
| 8 | Maximum DSP Slices | 740 |
| 9 | No of IO Bank | 10 |

## 8. Pinout Details
| S.No | Signal Name | Pin No | Voltage Level | Direction wrt FPGA | Source | Destination | Default | Standard |
|---|---|---|---|---|---|---|---|---|
| 1 | UART_TX | - | 3.3V | OUTPUT | FPGA | USB-UART | High-Z | LVTTL |
| 2 | UART_RX | - | 3.3V | INPUT | USB-UART | FPGA | High-Z | LVTTL |
| 3 | SPI_CLK | - | 3.3V | OUTPUT | FPGA | EEPROM | Low | LVTTL |
| 4 | SPI_MOSI | - | 3.3V | OUTPUT | FPGA | EEPROM | Low | LVTTL |
| 5 | SPI_MISO | - | 3.3V | INPUT | EEPROM | FPGA | High-Z | LVTTL |
| 6 | SPI_CS_N | - | 3.3V | OUTPUT | FPGA | EEPROM | High | LVTTL |
| 7 | I2C_SCL | - | 3.3V | OUTPUT | FPGA | Temp Sensor | High | LVTTL |
| 8 | I2C_SDA | - | 3.3V | BIDIR | FPGA | Temp Sensor | High | LVTTL |
| 9 | TCK | - | 3.3V | INPUT | JTAG | FPGA | Low | LVTTL |
| 10 | TDI | - | 3.3V | INPUT | JTAG | FPGA | Low | LVTTL |
| 11 | TDO | - | 3.3V | OUTPUT | FPGA | JTAG | Low | LVTTL |
| 12 | TMS | - | 3.3V | INPUT | JTAG | FPGA | High | LVTTL |

## 9. Functional Specifications
| S.No. | Function Name | Description |
|---|---|---|
| 1 | Serial Communication | UART between PC & FPGA via USB-UART |
| 2 | Flash Interfaces | Configuration and storage flash via SPI |
| 3 | Temperature Monitoring | I2C-based temperature monitoring |
| 4 | FPGA Remote Programming | Configuration loading via UART |

### 9.1 Serial Communication Interface
UART interface between host PC and FPGA via USB-UART converter.
- Baud Rate: 115200 bps (default) or 12 Mbps (high speed)
- Frame: 1 start + 8 data + 1 stop, no parity

## Annexure A — RTM
| S.No. | Requirement ID | Description | GLR Section |
|---|---|---|---|
| 1 | HRS-001 | Serial Communication | 9.1 |

_Note: Re-run Phase 6 for complete LLM-generated GLR._
"""

    def _load_file(self, path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""
