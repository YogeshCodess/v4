"""
Phase 8a: SRS (Software Requirements Specification) Agent - IEEE 830/29148 Compliant

Generates SRS from HRS + GLR, mapping hardware requirements to software functions.
"""

import logging
from pathlib import Path

from agents.base_agent import BaseAgent
from services.project_brief_builder import build_project_brief
from config import settings
from generators.srs_generator import SRSGenerator

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = r"""You are a senior software architect generating a comprehensive, publication-quality IEEE 830-1998 / ISO/IEC/IEEE 29148:2018-compliant Software Requirements Specification (SRS) for an embedded hardware system.

This document must be thorough — equivalent to 60+ pages of professional content. Every section must be fully populated with project-specific, measurable, concrete, testable requirements derived from the hardware context provided.

IEEE 29148:2018 defines a four-level requirements hierarchy:
  Level 1 — Stakeholder Requirements (StRS) — what stakeholders need
  Level 2 — System Requirements (SyRS) — what the system shall do
  Level 3 — Software Requirements (SRS) — what the software shall implement
  Level 4 — Software Architecture Requirements — design constraints

This SRS is Level 3. Every requirement MUST include a Verification Method (T=Test, I=Inspection, A=Analysis, D=Demonstration).

## DOCUMENT STRUCTURE (IEEE 830-1998 / IEEE 29148:2018) — generate ALL sections in FULL:

# Software Requirements Specification (SRS)

## Document Control
| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | {date} | — | Initial Release |

---

# 1. Introduction

## 1.1 Purpose
State the full purpose of this SRS document. Describe what system is being specified, who will implement it, and how this document will be used throughout the project lifecycle.

## 1.2 Scope
Define the software system scope in detail:
- Product name and identifier
- What the software will do (and explicitly what it will NOT do)
- Benefits, objectives, and goals
- Relationship to hardware components

## 1.3 Definitions, Acronyms, and Abbreviations
Minimum 30 definitions. Include: SRS, SDD, HRS, GLR, StRS, SyRS, RTOS, HAL, BSP, ISR, MISRA, UART, SPI, I2C, GPIO, ADC, DAC, DMA, FIFO, NVM, CRC, WDT, PLL, MCU, FPGA, API, BSS, RTM, JTAG, QSPI, TRP, ConOps, ASIL, SIL, IPC, RPC, etc.

## 1.4 References
List minimum 10 references:
- IEEE 830-1998: Recommended Practice for Software Requirements Specifications
- ISO/IEC/IEEE 29148:2018: Systems and Software Engineering — Life Cycle Processes — Requirements Engineering
- IEEE 1016-2009: Software Design Descriptions
- MISRA C:2012: Guidelines for the Use of the C Language in Critical Systems
- IEC 61508: Functional Safety of E/E/PE Safety-related Systems
- Hardware Requirements Specification (HRS) — this project
- Glue Logic Requirements (GLR) — this project
- Component datasheets (FPGA, EEPROM, Flash, sensors, power monitors)
- Project Block Diagram (P1)
- Netlist Specification (P4)

## 1.5 Overview
Describe the structure of this document and how to use it. State which sections address functional requirements, non-functional requirements, and how the traceability matrix links this SRS to the HRS.

---

# 2. Overall Description

## 2.1 Product Perspective
Full description of how this software fits into the larger system:
- System context diagram (Mermaid flowchart)
- Hardware interfaces it must drive
- External systems it communicates with
- Software stack layers (bare metal / RTOS / application)

## 2.2 Product Functions
Summary list of ALL major software functions (minimum 15):
- System initialization and boot sequence
- Hardware abstraction layer (HAL) for each peripheral
- UART command/response handler (register read/write protocol)
- PLL configuration and lock management
- Temperature monitoring and alert handling
- Voltage/current monitoring
- EEPROM read/write driver
- Configuration Flash driver (read, write, erase)
- LED and GPIO control
- Watchdog timer management
- JTAG/debug interface support
- Power-on self-test (POST)
- Error logging and fault handling
- RF control (if applicable): TRP, phase shifter, beam steering
- Calibration data loading from flash/EEPROM
(Add project-specific functions from the HRS/GLR)

## 2.3 User Characteristics
Describe the intended users:
- Firmware engineers (primary developers)
- Test engineers (system-level test)
- Field engineers (diagnostics via UART)
- System integrators

## 2.4 Constraints
Minimum 8 constraints:
- MISRA-C:2012 compliance mandatory
- Real-time response constraints (latency limits per interface)
- Memory budget: flash size, RAM size
- Clock frequency constraints
- RTOS or bare-metal execution model
- Coding language: C (C99/C11)
- Toolchain requirements (GCC, IAR, Vivado SDK)
- Hardware revision compatibility

## 2.5 Assumptions and Dependencies
List all assumptions the software makes about:
- Hardware behavior (power sequencing complete before software runs)
- Operating temperature range
- Clock stability before peripheral init
- Network/communication availability

---

# 3. Specific Requirements

## 3.1 External Interface Requirements

### 3.1.1 Hardware Interfaces
For EACH hardware interface, provide:
- Interface name and protocol (with exact timing: setup time, hold time, max clock frequency)
- C struct definition mapping FPGA registers (base address + offsets in hex)
- Driver API function prototypes with full Doxygen-style parameter docs
- Error handling and recovery procedure

**3.1.1.1 UART Interface**
```c
typedef struct {
    volatile uint16_t BAUD_DIV;    // 0x0100: Baud rate divisor
    volatile uint16_t CTRL;        // 0x0101: Control register
    volatile uint16_t STATUS;      // 0x0102: Status register
    volatile uint16_t TX_COUNT;    // 0x0103: TX FIFO count
    volatile uint16_t RX_COUNT;    // 0x0104: RX FIFO count
} UART_RegMap_t;

// Driver API
int32_t UART_Init(uint32_t baud_rate);
int32_t UART_WriteReg(uint16_t addr, uint16_t data);
int32_t UART_ReadReg(uint16_t addr, uint16_t *data);
int32_t UART_BulkWrite(uint16_t start_addr, const uint16_t *data, uint8_t count);
int32_t UART_BulkRead(uint16_t start_addr, uint16_t *buf, uint8_t count);
```

**3.1.1.2 SPI Interface (EEPROM/Flash)**
```c
typedef struct {
    volatile uint16_t CTRL;        // Control
    volatile uint16_t ADDR;        // Address
    volatile uint16_t DATA;        // Data FIFO
    volatile uint16_t STATUS;      // Status
} SPI_RegMap_t;

int32_t SPI_Init(uint32_t clock_hz, uint8_t mode);
int32_t EEPROM_ReadByte(uint16_t addr, uint8_t *data);
int32_t EEPROM_WriteByte(uint16_t addr, uint8_t data);
int32_t Flash_ReadSector(uint32_t addr, uint8_t *buf, uint32_t len);
int32_t Flash_WriteSector(uint32_t addr, const uint8_t *buf, uint32_t len);
int32_t Flash_EraseSector(uint32_t addr);
```

**3.1.1.3 I2C Interface (Temperature/Power monitoring)**
```c
int32_t I2C_Init(uint32_t clock_hz);
int32_t I2C_ReadReg8(uint8_t dev_addr, uint8_t reg, uint8_t *data);
int32_t I2C_WriteReg8(uint8_t dev_addr, uint8_t reg, uint8_t data);
int32_t TempSensor_ReadTemp(uint8_t sensor_id, float *temp_degC);
int32_t PowerMon_ReadVoltage(uint8_t channel, float *voltage_V);
int32_t PowerMon_ReadCurrent(uint8_t channel, float *current_A);
```

### 3.1.2 Software Interfaces
- Operating system / RTOS API (if applicable)
- Standard C library usage
- Logging framework interface

### 3.1.3 Communication Interfaces
Full byte-level protocol specification for the UART register command protocol:

**Frame Formats (from GLR):**

| Command | CMD byte | Frame Structure | Response |
|---------|----------|-----------------|----------|
| Single Write | 0x57 ('W') | [0x57][ADDR_H][ADDR_L][DATA_H][DATA_L] | [0x06] ACK |
| Single Read  | 0x52 ('R') | [0x52][ADDR_H\|0x80][ADDR_L] | [DATA_H][DATA_L] |
| Bulk Write   | 0x42 ('B') | [0x42][ADDR_H][ADDR_L][N][D0_H][D0_L]...[Dn_H][Dn_L] | [0x06] ACK |
| Bulk Read    | 0x62 ('b') | [0x62][ADDR_H\|0x80][ADDR_L][N] | [D0_H][D0_L]...[Dn_H][Dn_L] |
| Error NAK    | 0x15 | Sent by FPGA on invalid command/address | — |

- Address space: 16-bit (0x0000–0xFFFF); read addresses have bit15 set (OR 0x8000)
- Maximum bulk count N: 64 registers per transaction
- Timeout: host must respond within 10ms; FPGA resets parser after 50ms inter-byte gap
- ACK byte: 0x06; NAK byte: 0x15
- No CRC in baseline protocol; CRC-16 CCITT optional (feature flag in config flash)

## 3.2 Functional Requirements

Generate MINIMUM 75 functional requirements with IDs REQ-SW-001 through REQ-SW-075+.
Group them by subsystem. For EACH requirement, include:
- **REQ-SW-xxx**: [The software SHALL ...] — measurable, testable statement
- **Source**: REQ-HW-xxx or GLR §x.x (traceability up the hierarchy)
- **Priority**: [M]andatory / [D]esirable / [O]ptional (MoSCoW)
- **Verification**: [T]est / [I]nspection / [A]nalysis / [D]emonstration

### 3.2.1 System Initialization (REQ-SW-001 to REQ-SW-010)
REQ-SW-001: The software SHALL complete power-on self-test within 500ms of reset de-assertion.
REQ-SW-002: The software SHALL verify BOARD_ID register matches expected value 0x[XX] on startup; fault if mismatch.
REQ-SW-003: The software SHALL configure PLL to target frequency [X] MHz within [Y] ms.
REQ-SW-004: The software SHALL poll PLL_STATUS.LOCKED bit with 100ms timeout; assert ERROR if timeout.
REQ-SW-005: The software SHALL initialize all SPI peripherals before enabling application tasks.
REQ-SW-006: The software SHALL load calibration data from EEPROM into RAM on startup.
REQ-SW-007: The software SHALL initialize watchdog timer with [X]ms timeout before entering main loop.
REQ-SW-008: The software SHALL log firmware version to UART on startup.
REQ-SW-009: The software SHALL perform RAM BIST (built-in self-test) on [X] KB of SRAM.
REQ-SW-010: The software SHALL set LED_STATUS to BLINKING at 1Hz during initialization.
(Continue with all 50+ requirements across all subsystems)

### 3.2.2 UART Communication Driver (REQ-SW-011 to REQ-SW-020)
REQ-SW-011: The UART driver SHALL support baud rates of [list from HRS].
REQ-SW-012: The driver SHALL implement the Single Write command (0x57) as defined in the GLR frame format.
REQ-SW-013: The driver SHALL implement the Single Read command (0x52) with ADDR bit15=1.
REQ-SW-014: The driver SHALL implement the Bulk Write command (0x42) for up to 64 consecutive registers.
REQ-SW-015: The driver SHALL implement the Bulk Read command (0x62) for up to 64 consecutive registers.
REQ-SW-016: The driver SHALL respond to an invalid command byte with NAK (0x15) within [X]µs.
REQ-SW-017: The driver SHALL support a TX FIFO of at least 256 bytes.
REQ-SW-018: The driver SHALL support an RX FIFO of at least 256 bytes.
REQ-SW-019: The driver SHALL clear UART_STATUS.FRAME_ERR flag on read.
REQ-SW-020: The driver SHALL recover from framing errors without hardware reset.

### 3.2.3 Temperature Monitoring (REQ-SW-021 to REQ-SW-030)
REQ-SW-021: The software SHALL read temperature from all configured sensors every [X] seconds.
REQ-SW-022: The software SHALL generate a TEMP_ALERT interrupt when temperature exceeds [X]°C.
REQ-SW-023: The software SHALL log temperature to UART status register every [X] seconds.
REQ-SW-024: The software SHALL disable RF output (TRP=LOW) when temperature exceeds [X]°C.
REQ-SW-025: The software SHALL re-enable RF output when temperature drops below [X-5]°C (hysteresis).
... (continue through REQ-SW-030)

### 3.2.4 Flash Management (REQ-SW-031 to REQ-SW-040)
REQ-SW-031: The Flash driver SHALL support read, write, and sector-erase operations.
REQ-SW-032: The Flash driver SHALL verify written data with read-back CRC.
... (continue)

### 3.2.5 Power Management (REQ-SW-041 to REQ-SW-050)
REQ-SW-041: The software SHALL monitor all power rails every [X] ms via ADC registers.
REQ-SW-042: The software SHALL assert a fault condition if any rail deviates >5% from nominal.
... (continue)

### 3.2.6 RF / Application-Specific (REQ-SW-051+)
(Generate RF-specific requirements if the project is RF, otherwise generate application-specific requirements from HRS)

### 3.2.7 Diagnostics and Built-In Test (REQ-SW-071 to REQ-SW-075+)
REQ-SW-071: The software SHALL implement a Power-On Self-Test (POST) covering RAM BIST, peripheral communication check, and PLL lock verification.
REQ-SW-072: The software SHALL log all detected faults to a circular fault log buffer in EEPROM (minimum 64 entries, FIFO).
REQ-SW-073: The software SHALL expose a UART diagnostic command (0xD0) that dumps the fault log buffer to the host.
REQ-SW-074: The software SHALL maintain a software execution counter (uptime seconds) readable via UART register.
REQ-SW-075: The software SHALL implement a built-in loopback test for the UART driver (internal Tx→Rx at startup).

## 3.3 Performance Requirements
Minimum 12 performance requirements with concrete measurable values. Include verification method:
- REQ-PERF-001: Main loop execution cycle SHALL complete within [X] ms
- REQ-PERF-002: UART register write SHALL complete within [X] µs end-to-end
- REQ-PERF-003: Temperature read cycle SHALL complete within [X] ms
- REQ-PERF-004: SPI Flash page write SHALL complete within [X] ms
- REQ-PERF-005: PLL lock acquisition SHALL complete within [X] ms
- REQ-PERF-006: System startup SHALL complete within [X] ms
- REQ-PERF-007: ISR latency SHALL not exceed [X] µs
- REQ-PERF-008: Watchdog pet interval SHALL be [X] ms maximum
- REQ-PERF-009: RAM usage SHALL not exceed [X]% of available RAM
- REQ-PERF-010: Flash usage SHALL not exceed [X]% of available flash

## 3.4 Design Constraints
Minimum 8 design constraints with rationale:
- Coding standard: MISRA-C:2012 (mandatory — safety-critical embedded)
- Language: C (C99) — no C++ unless specified
- No dynamic memory allocation (malloc/free forbidden)
- Stack depth analysis required (static stack usage analyzer)
- All interrupts must complete within [X] µs
- All global variables must be volatile-qualified
- No recursion allowed
- CRC-32 on all non-volatile data writes

## 3.5 Software System Attributes

### 3.5.1 Reliability
- MTBF requirement: [X] hours
- Error detection and recovery for every peripheral
- Watchdog recovery mechanism
- Graceful degradation: system must continue in degraded mode if a non-critical peripheral fails

### 3.5.2 Availability
- System availability target: [X]%
- Maximum unplanned downtime: [X] hours/year
- Startup time after power cycle: < [X] seconds

### 3.5.3 Security
- No remote code execution paths
- UART register writes validated against allowed address ranges
- Firmware update authentication: CRC-32 check before applying bitstream

### 3.5.4 Maintainability
- Cyclomatic complexity per function: ≤ 15
- All functions documented with Doxygen headers
- Unit test coverage: ≥ 80% line coverage for all HAL drivers

### 3.5.5 Portability
- Hardware abstraction layer must isolate all hardware dependencies
- Platform config in single header file (board_config.h)

---

# 4. Verification and Validation

## 4.1 Unit Test Requirements
For each driver module, define test cases covering:
- Normal operation
- Boundary conditions
- Error/fault injection
(Minimum 3 test cases per subsystem listed)

## 4.2 Integration Test Requirements
- UART loopback test
- SPI EEPROM write-read-verify test
- Temperature sensor read and alert test
- Flash sector erase-write-read-CRC test
- PLL lock acquisition test

## 4.3 System Test Requirements
- Full power-on sequence test
- Endurance test: [X] hours continuous operation
- Temperature stress test: [Tmin] to [Tmax] °C
- EMC/EMI compliance test per MIL-STD or IEC standard

---

# 5. Requirements Traceability Matrix

| REQ-SW-xxx | Description | Traces To (REQ-HW-xxx / GLR Section) |
|-----------|-------------|--------------------------------------|
(Map ALL REQ-SW-xxx to hardware requirements from HRS and GLR sections)

---

# 6. Appendices

## Appendix A — Error Codes
```c
typedef enum {
    ERR_OK           = 0x00,
    ERR_TIMEOUT      = 0x01,
    ERR_COMM         = 0x02,
    ERR_CHECKSUM     = 0x03,
    ERR_PARAM        = 0x04,
    ERR_NOT_INIT     = 0x05,
    ERR_RESOURCE     = 0x06,
    ERR_HARDWARE     = 0x07,
    ERR_OVERFLOW     = 0x08,
    ERR_UNDERFLOW    = 0x09,
    ERR_FLASH_WRITE  = 0x0A,
    ERR_FLASH_ERASE  = 0x0B,
    ERR_EEPROM       = 0x0C,
    ERR_PLL          = 0x0D,
    ERR_TEMP_ALERT   = 0x0E,
    ERR_VOLT_FAULT   = 0x0F,
} ErrorCode_t;
```

## Appendix B — Register Map Summary
(Summary of all FPGA registers software accesses, from GLR)

## Appendix C — Mermaid Diagrams

### System Initialization Sequence
```mermaid
sequenceDiagram
    participant HW as Hardware
    participant BSP as BSP/HAL
    participant APP as Application
    HW->>BSP: Power-on Reset released
    BSP->>BSP: Clock init, PLL config
    BSP->>BSP: Peripheral init (UART, SPI, I2C)
    BSP->>APP: Board ready
    APP->>APP: Load calibration from EEPROM
    APP->>APP: POST (self-test)
    APP->>HW: Enable outputs (LED, RF if applicable)
```

### UART Register Command Flow
```mermaid
sequenceDiagram
    participant HOST as Host PC
    participant DRV as UART Driver
    participant REG as Register Map
    HOST->>DRV: Send Write Command (0x57, ADDR, DATA)
    DRV->>REG: Write register[ADDR] = DATA
    REG-->>DRV: Write complete
    DRV-->>HOST: ACK (0x06)
```

### Temperature Alert State Machine
```mermaid
stateDiagram-v2
    [*] --> NORMAL
    NORMAL --> ALERT: temp > THRESH_HIGH
    ALERT --> NORMAL: temp < THRESH_LOW (hysteresis)
    ALERT --> SHUTDOWN: temp > THRESH_CRITICAL
    SHUTDOWN --> [*]: Power cycle required
```

---

## 3.6 Stakeholder Requirements Traceability (IEEE 29148:2018 §6.2)

Provide a two-level trace showing how software requirements link to hardware/system requirements:

| REQ-SW-xxx | SW Requirement Summary | Maps To (HRS/GLR/SyRS) | Verification |
|-----------|------------------------|------------------------|-------------|
(All 75+ requirements must appear as rows)

---

# 4. Verification and Validation

## 4.1 Unit Test Requirements
For each driver module, define minimum 3 test cases:
- Normal operation path
- Boundary condition (min/max values)
- Fault injection (hardware not responding, timeout)

## 4.2 Integration Test Requirements
- UART loopback self-test (REQ-SW-075 verification)
- SPI EEPROM write–read–verify (REQ-SW-006 verification)
- Temperature sensor alert trigger test
- Flash sector erase–write–read–CRC test
- PLL lock acquisition and loss-of-lock recovery test

## 4.3 System Test Requirements
- Full power-on sequence test with timing measurements
- Endurance test: 72 hours continuous operation at nominal temperature
- Temperature stress test across rated operating range
- EMC/EMI pre-compliance test (conducted emissions, radiated emissions)
- UART protocol conformance test: all four command types with error injection

## 4.4 Formal Verification (if SIL ≥ 2)
- Static analysis tool coverage report (Polyspace, PC-lint)
- Stack usage analysis (all call paths worst-case bounded)
- Data flow analysis for all state machine transitions

---

# 5. Requirements Traceability Matrix

| REQ-SW-xxx | Description | Source (REQ-HW/GLR §) | Priority | Verification | Status |
|-----------|-------------|----------------------|----------|-------------|--------|
(Map ALL REQ-SW-xxx to hardware requirements from HRS and GLR sections; include Priority and Verification columns)

---

# 6. Appendices

## Appendix A — Error Codes
```c
typedef enum {
    ERR_OK           = 0x00,
    ERR_TIMEOUT      = 0x01,
    ERR_COMM         = 0x02,
    ERR_CHECKSUM     = 0x03,
    ERR_PARAM        = 0x04,
    ERR_NOT_INIT     = 0x05,
    ERR_RESOURCE     = 0x06,
    ERR_HARDWARE     = 0x07,
    ERR_OVERFLOW     = 0x08,
    ERR_UNDERFLOW    = 0x09,
    ERR_FLASH_WRITE  = 0x0A,
    ERR_FLASH_ERASE  = 0x0B,
    ERR_EEPROM       = 0x0C,
    ERR_PLL          = 0x0D,
    ERR_TEMP_ALERT   = 0x0E,
    ERR_VOLT_FAULT   = 0x0F,
    ERR_LOOPBACK     = 0x10,
    ERR_POST_FAIL    = 0x11,
    ERR_WATCHDOG     = 0x12,
    ERR_ADDR_RANGE   = 0x13,
} ErrorCode_t;
```

## Appendix B — FPGA Register Map (Software View)
Provide a complete table of all software-accessible FPGA registers:

| Base Address | Block | Offset | Register Name | Width | R/W | Reset Value | Description |
|-------------|-------|--------|--------------|-------|-----|-------------|-------------|
(Derive all entries from GLR §10 register address map)

## Appendix C — Mermaid Diagrams

### System Initialization Sequence
```mermaid
sequenceDiagram
    participant HW as Hardware
    participant BSP as BSP/HAL
    participant APP as Application
    HW->>BSP: Power-on Reset released
    BSP->>BSP: Clock init, PLL config
    BSP->>BSP: Peripheral init UART SPI I2C
    BSP->>APP: Board ready
    APP->>APP: Load calibration from EEPROM
    APP->>APP: POST self-test
    APP->>HW: Enable outputs LED RF
```

### UART Register Command Flow
```mermaid
sequenceDiagram
    participant HOST as Host PC
    participant DRV as UART Driver
    participant REG as Register Map
    HOST->>DRV: Send Write Command 0x57 ADDR DATA
    DRV->>REG: Write register ADDR equals DATA
    REG-->>DRV: Write complete
    DRV-->>HOST: ACK 0x06
```

### Temperature Alert State Machine
```mermaid
stateDiagram-v2
    [*] --> NORMAL
    NORMAL --> ALERT: temp above THRESH HIGH
    ALERT --> NORMAL: temp below THRESH LOW hysteresis
    ALERT --> SHUTDOWN: temp above THRESH CRITICAL
    SHUTDOWN --> [*]: Power cycle required
```

### Software Layer Architecture
```mermaid
graph TD
    APP[Application Layer] --> HAL[Hardware Abstraction Layer]
    HAL --> UART[UART Driver]
    HAL --> SPI[SPI Driver]
    HAL --> I2C[I2C Driver]
    HAL --> GPIO[GPIO Driver]
    HAL --> WDT[Watchdog Driver]
    UART --> FPGA[FPGA Register Map]
    SPI --> EEPROM[EEPROM Device]
    SPI --> FLASH[Flash Memory]
    I2C --> TEMP[Temp Sensor]
    I2C --> PWRMON[Power Monitor]
```

## Appendix D — Acronyms and Glossary
(Full glossary of 30+ terms used in this document)

## Appendix E — Document Revision History
| Rev | Date | Author | Description |
|-----|------|--------|-------------|
| 1.0 | — | — | Initial Release |

---

## ABSOLUTE RULES:
1. Generate MINIMUM 75 REQ-SW-xxx requirements spread across all subsystems
2. Every requirement MUST include Source, Priority (M/D/O), and Verification (T/I/A/D)
3. Every requirement must be specific, measurable, and testable — no vague language
4. NEVER use TBD, TBC, or TBA — derive values from HRS/GLR or state explicit engineering assumptions
5. All C code examples must be syntactically correct C99
6. Include minimum 5 Mermaid diagrams (sequence, state, flowchart, graph TD). STRICT Mermaid label rules: NO single-quotes ', double-quotes ", angle brackets < >, #, |, & or colons : inside node labels. NO 3+ consecutive dashes (---) inside labels. Use plain ASCII words only in labels.
7. The traceability matrix (Section 5) must include ALL REQ-SW-xxx with Source, Priority, Verification columns
8. Section 3.1.3 MUST include the UART byte-level frame format table
9. Be highly specific to the actual project — generic boilerplate is not acceptable
"""


class SRSAgent(BaseAgent):
    """Phase 8a: IEEE 830-compliant SRS generation."""

    def __init__(self):
        super().__init__(
            phase_number="P8a",
            phase_name="SRS Generation",
            model=settings.fast_model,
            max_tokens=16384,
        )
        self.srs_generator = SRSGenerator()

    def get_system_prompt(self, project_context: dict) -> str:
        return SYSTEM_PROMPT

    async def execute(self, project_context: dict, user_input: str) -> dict:
        output_dir = Path(project_context.get("output_dir", "output"))
        output_dir.mkdir(parents=True, exist_ok=True)
        project_name = project_context.get("name", "Project")

        # Build the ProjectBrief so the LLM is primed with project-specific
        # facts (frequency, peripherals, register count). Without this the
        # SRS reads as generic IEEE 830 boilerplate.
        try:
            self._brief = build_project_brief(
                project_id=int(project_context.get("project_id") or 0),
                project_name=project_name,
                output_dir=str(output_dir),
                project_type=str(project_context.get("project_type") or "receiver"),
                design_scope=str(project_context.get("design_scope") or "full"),
                application_class=str(
                    (project_context.get("design_parameters") or {}).get("application_class")
                    or "general"
                ),
            )
            self._brief_preamble = self._brief.to_prompt_preamble()
        except Exception as _brief_err:
            self.log(f"project_brief.build_failed: {_brief_err}", "warning")
            self._brief = None
            self._brief_preamble = ""

        # Load prior phase outputs
        requirements = self._load_file(output_dir / "requirements.md")
        hrs = self._load_file(output_dir / f"HRS_{project_name.replace(' ', '_')}.md")
        glr = self._load_file(output_dir / "glr_specification.md")

        # ── Cross-phase context: load locked DesignManifest ─────────────
        # SRS produces software requirements that depend on hardware
        # interfaces. The hardware interfaces are derived from the BOM,
        # so the manifest BOM is the canonical input. Without this, the
        # SRS could enumerate interfaces for parts the audit dropped.
        manifest = None
        manifest_bom_block = ""
        try:
            from services.project_service import ProjectService as _PS
            _pid = project_context.get("project_id")
            if _pid is not None:
                manifest = _PS().get_design_manifest(int(_pid))
            if manifest and manifest.bom:
                _rows = []
                for row in manifest.bom[:30]:
                    pn = row.get("part_number") or row.get("primary_part") or ""
                    mfr = row.get("manufacturer") or row.get("primary_manufacturer") or ""
                    role = row.get("role") or row.get("kind") or ""
                    _rows.append(f"- `{pn}` ({mfr}) — role={role}")
                manifest_bom_block = (
                    "## Locked BOM (DesignManifest — authoritative for SW interface derivation)\n"
                    f"manifest_hash: `{manifest.manifest_hash}`\n\n"
                    "**RULE:** Hardware interfaces (Section 3.1) and the FPGA "
                    "register map (Appendix B) MUST account only for the parts "
                    "below. No invented MPNs.\n\n"
                    + "\n".join(_rows) + "\n\n"
                )
                self.log(
                    f"P8a SRS loaded DesignManifest hash="
                    f"{(manifest.manifest_hash or '')[:12]} "
                    f"({len(manifest.bom)} parts)",
                    "info",
                )
        except Exception as _exc:
            self.log(f"P8a DesignManifest load skipped: {_exc}", "info")

        from datetime import datetime
        today = datetime.now().strftime("%d %B %Y")

        # PRIMARY PATH: LLM writes the full IEEE 830 SRS from project context
        user_message = (
            f"Generate a COMPLETE, DETAILED, 60+ page IEEE 830-1998 / IEEE 29148:2018 Software Requirements Specification for:\n\n"
            f"**Project:** {project_name}\n"
            f"**Date:** {today}\n\n"
            + manifest_bom_block
            + f"## Hardware Requirements Specification (P2 — primary input):\n"
            f"{hrs[:8000] if hrs else 'Not yet generated — use P1 requirements below.'}\n\n"
            f"## P1 Requirements & BOM:\n"
            f"{requirements[:5000] if requirements else 'Not captured.'}\n\n"
            f"## GLR Specification (P6 — register addresses, UART protocol, pinout):\n"
            f"{glr[:6000] if glr else 'Not yet generated.'}\n\n"
            "INSTRUCTIONS:\n"
            "1. Generate ALL sections from the IEEE 830/29148 structure in your system prompt\n"
            "2. Generate MINIMUM 75 REQ-SW-xxx requirements — number them sequentially; each MUST have Source, Priority, Verification columns\n"
            "3. Map every REQ-SW to at least one REQ-HW or GLR section in the traceability matrix (Section 5)\n"
            "4. Include actual C function prototypes and struct definitions for every hardware interface\n"
            "5. Section 3.1.3 MUST contain the full UART byte-level frame format table (Single Write, Single Read, Bulk Write, Bulk Read, NAK)\n"
            "6. Include Appendix B with complete FPGA register map table (base address, offset, name, R/W, reset value)\n"
            "7. Include minimum 5 Mermaid diagrams (sequenceDiagram, stateDiagram-v2, graph TD)\n"
            "8. Derive all values from the HRS/GLR — no TBD/TBC/TBA placeholders anywhere\n"
            "9. Each section must be fully written — no placeholders, no 'to be completed'\n"
            "10. Be highly specific to this actual project — no generic boilerplate"
        )

        srs_content = ""
        try:
            _system = SYSTEM_PROMPT
            if getattr(self, "_brief_preamble", ""):
                _system = self._brief_preamble + "\n\n" + SYSTEM_PROMPT
            response = await self.call_llm(
                messages=[{"role": "user", "content": user_message}],
                system=_system,
            )
            srs_content = response.get("content", "")

            # Up to 5 continuation passes — each feeds accumulated text back as context
            _SRS_CONT_PROMPTS = [
                (
                    "Continue the SRS document from exactly where you left off. "
                    "Do NOT repeat any sections already written. "
                    "Continue generating requirements (REQ-SW-xxx), performance requirements, "
                    "design constraints, interface requirements, and safety requirements."
                ),
                (
                    "Continue writing the SRS from where you stopped. "
                    "Do NOT repeat content already written. "
                    "Focus on: V&V requirements, non-functional requirements, "
                    "software quality attributes, and environmental/reliability constraints."
                ),
                (
                    "Continue the SRS. Do NOT repeat content already written. "
                    "Write the full Requirement Traceability Matrix (REQ-SW-xxx → REQ-HW-xxx / GLR sections). "
                    "Every REQ-SW requirement must appear as a row in the traceability table."
                ),
                (
                    "Continue. Do NOT repeat content already written. "
                    "Write Appendix A (Error Codes enum in C), Appendix B (Register Map summary table), "
                    "Appendix C (all Mermaid diagrams not yet included), Appendix D (acronyms and glossary)."
                ),
                (
                    "Finalize the SRS. Add any remaining incomplete appendices or sections. "
                    "Do NOT repeat content already written. "
                    "End with the document revision history table and sign-off block."
                ),
            ]

            # P26 #21 (2026-05-04): the loop used to fire ONLY on
            # stop_reason="max_tokens". GLM-5.1 routinely stops with
            # "end_turn" after only ~2-5 KB of content (well under the
            # 16384-token cap), and the loop would break immediately —
            # leaving an under-800-char SRS that fell through to the
            # 1.3 KB deterministic template. Now we ALSO fire continuations
            # while accumulated content is short (< 25 KB ≈ 8-10 IEEE 830
            # pages), regardless of stop_reason. The loop still exits
            # early once the doc is long enough OR the model truly is
            # done (continuation returned empty).
            _SRS_TARGET_CHARS = 25000
            for _pass_idx, _cont_prompt in enumerate(_SRS_CONT_PROMPTS, start=1):
                _truncated = response.get("stop_reason") == "max_tokens"
                _too_short = len(srs_content) < _SRS_TARGET_CHARS
                if not _truncated and not _too_short:
                    break
                self.log(
                    f"SRS continuation pass {_pass_idx}/5 "
                    f"(stop_reason={response.get('stop_reason')!r}, "
                    f"len={len(srs_content)})..."
                )
                _cont = await self.call_llm(
                    messages=[
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": srs_content},
                        {"role": "user", "content": _cont_prompt},
                    ],
                    system=SYSTEM_PROMPT,
                )
                _cont_text = _cont.get("content", "") or ""
                if not _cont_text.strip():
                    # Model has truly nothing more to write — stop looping.
                    self.log(
                        f"SRS continuation {_pass_idx}/5 returned empty — "
                        f"stopping (final len={len(srs_content)})"
                    )
                    break
                srs_content += "\n\n" + _cont_text
                response = _cont  # check this response's stop_reason in next iteration
        except Exception as e:
            self.log(f"LLM SRS generation failed: {e} — falling back to template", "warning")

        # FALLBACK: template generator
        if not srs_content or len(srs_content) < 800:
            self.log(
                f"SRS LLM output too short ({len(srs_content)} chars) - "
                "running deterministic template generator", "warning",
            )
            try:
                hw_requirements = await self._extract_hw_requirements(requirements, hrs)
                sw_features = await self._extract_sw_features(glr, hrs)
                srs_content = self.srs_generator.generate(
                    project_name=project_name,
                    hw_requirements=hw_requirements,
                    sw_features=sw_features,
                    metadata={"version": project_context.get("version", "1.0")},
                )
            except Exception as _gen_err:
                # Last-resort minimal SRS so the pipeline never produces
                # an empty SRS file. Uses the brief for project specifics.
                self.log(
                    f"SRSGenerator.generate failed: {_gen_err} - emitting "
                    "minimal-but-complete IEEE 830 stub from ProjectBrief",
                    "error",
                )
                srs_content = self._emergency_stub(project_name)

        # Scrub any TBD/TBC/TBA the LLM wrote despite instructions
        import re as _re
        srs_content = _re.sub(r'\b(TBD|TBC|TBA)\b', '[specify]', srs_content, flags=_re.IGNORECASE)

        # P26 #17 (2026-04-26): coerce + re-render every embedded
        # `mermaid` block so LLM-emitted bracket mismatches don't
        # break the in-browser preview. See `tools.mermaid_coerce`
        # for the full bug-class background.
        try:
            from tools.mermaid_coerce import sanitize_mermaid_blocks_in_markdown
            srs_content = sanitize_mermaid_blocks_in_markdown(srs_content)
        except Exception as _exc:
            self.log(f"SRS mermaid sanitise skipped: {_exc}", "warning")

        # Save output
        srs_file = self.srs_generator.save(srs_content, output_dir, project_name)
        self.log(f"SRS generated: {len(srs_content)} chars")

        return {
            "response": "SRS document generated (IEEE 830 compliant).",
            "phase_complete": True,
            "outputs": {srs_file.name: srs_content},
        }

    def _emergency_stub(self, project_name: str) -> str:
        """Minimal IEEE 830 outline that still uses ProjectBrief specifics.

        Triggered only when both the LLM AND the template generator fail.
        """
        from datetime import datetime as _dt
        b = getattr(self, "_brief", None)
        peripherals = ""
        registers_md = ""
        if b is not None:
            if b.peripherals:
                peripherals = "\n".join(
                    f"- {p.bus.upper()} - {p.name}" for p in b.peripherals
                )
            if b.registers:
                registers_md = "\n".join(
                    f"| `{r.address}` | `{r.name}` | {r.access} |"
                    for r in b.registers[:30]
                )
        date = _dt.now().strftime("%d %B %Y")
        parts = [
            f"# Software Requirements Specification - {project_name}",
            "",
            f"_Generated: {date}_",
            "_Standard: IEEE 830-1998 / IEEE 29148:2018_",
            "",
            "**EMERGENCY STUB**: the LLM and the template generator both "
            "failed. Re-run P8a once the upstream issue is resolved.",
            "",
            "## 1 Introduction",
            "",
            f"This document specifies the software requirements for **{project_name}**.",
            "",
            "## 2 Overall Description",
            "",
        ]
        if peripherals:
            parts += ["### 2.1 Peripheral interfaces", "", peripherals, ""]
        if registers_md:
            parts += [
                "### 2.2 Register interface",
                "",
                "| Address | Name | Access |",
                "|---------|------|--------|",
                registers_md,
                "",
            ]
        parts += [
            "## 3 Specific Requirements",
            "",
            "REQ-SW-001: The firmware shall initialise all peripherals to a "
            "known state on power-up.",
            "",
            "REQ-SW-002: The firmware shall expose the FPGA register bus over "
            "the host UART link using the framing defined in the GLR.",
            "",
            "## 4 Verification",
            "",
            "Verification methods: T (Test), I (Inspection), A (Analysis), "
            "D (Demonstration). See traceability matrix.",
            "",
            "## 5 Traceability Matrix",
            "",
            "| REQ-SW-id | Source | Verification |",
            "|-----------|--------|-------------|",
            "| REQ-SW-001 | HRS | T |",
            "| REQ-SW-002 | GLR | T |",
        ]
        return chr(10).join(parts)

    async def _extract_hw_requirements(self, requirements: str, hrs: str) -> list:
        """Extract hardware requirements from documents using REQ-HW-xxx patterns."""
        import re
        reqs = []

        # Search in HRS first, then requirements
        content = hrs if hrs else requirements
        if not content:
            return []

        # Pattern: REQ-HW-nnn with optional title and description
        pattern = r'REQ-HW-(\d+)[:\s]+([^\n]+?)(?:\n|$)'
        matches = re.findall(pattern, content, re.MULTILINE)

        for req_id, title in matches:
            reqs.append({
                "req_id": f"REQ-HW-{req_id}",
                "title": title.strip(),
                "description": title.strip()
            })

        # Fallback: extract from header sections mentioning hardware
        if not reqs:
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if any(keyword in line.lower() for keyword in ['hardware', 'interface', 'pin', 'power', 'voltage', 'frequency']):
                    if line.strip() and not line.startswith('#'):
                        reqs.append({
                            "req_id": f"REQ-HW-{len(reqs)+1:03d}",
                            "title": line.strip(),
                            "description": line.strip()
                        })

        return reqs if reqs else [
            {"req_id": "REQ-HW-001", "title": "System Power", "description": "System must operate from 3.3V or 5V supply"},
            {"req_id": "REQ-HW-002", "title": "Communication", "description": "System must support UART/SPI/I2C interfaces"},
        ]

    async def _extract_sw_features(self, glr: str, hrs: str) -> list:
        """Extract software features from GLR/HRS content."""
        import re
        features = []

        # Search for interface protocols and control algorithms
        content = (glr + "\n" + hrs) if (glr or hrs) else ""
        if not content:
            return self._default_sw_features()

        # Look for protocol mentions
        protocol_pattern = r'\b(SPI|I2C|UART|CAN|USB|GPIO|ADC|PWM|DMA|RTC)\b'
        protocols = set(re.findall(protocol_pattern, content, re.IGNORECASE))

        for i, protocol in enumerate(sorted(protocols), 1):
            features.append({
                "id": f"F-{i:02d}",
                "name": f"{protocol.upper()} Interface Driver",
                "text": f"Support for {protocol} protocol communication"
            })

        # Look for control/algorithm keywords
        algo_keywords = [
            (r'control\s+loop|controller|servo', 'Control Loop'),
            (r'filter|filtering|calibration', 'Signal Filtering'),
            (r'interrupt|timer|timing', 'Interrupt Handler'),
            (r'initialization|boot|startup', 'System Initialization'),
            (r'error\s+handling|fault|exception', 'Error Handling'),
            (r'state\s+machine|transition', 'State Machine'),
            (r'data\s+acquisition|sampling|conversion', 'Data Acquisition'),
        ]

        for pattern, feature_name in algo_keywords:
            if re.search(pattern, content, re.IGNORECASE):
                idx = len(features) + 1
                features.append({
                    "id": f"F-{idx:02d}",
                    "name": feature_name,
                    "text": f"{feature_name} and processing"
                })

        # Return defaults if no patterns matched
        return features if features else self._default_sw_features()

    def _default_sw_features(self) -> list:
        """Default software features."""
        return [
            {"id": "F-01", "name": "System Initialization", "text": "System initialization and boot"},
            {"id": "F-02", "name": "Device Control", "text": "Device control and configuration"},
            {"id": "F-03", "name": "Data Acquisition", "text": "Data acquisition and processing"},
        ]

    def _load_file(self, path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""
