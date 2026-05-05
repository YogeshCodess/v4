"""
Phase 8b: SDD (Software Design Document) Agent - IEEE 1016 Compliant

Generates software architecture from SRS with Mermaid diagrams.

P26 #16 (2026-04-26): parallelised the 60+ page SDD into 1 metadata-lock
call + 5 parallel section calls (mirrors the FPGA agent's pattern from
P26 #8). Total wall time: ~10 min sequential continuation passes →
~90-120 s parallel.

The metadata call locks the design contract that ALL 5 section
generators must use (module names, struct names, register addresses,
file layout, naming conventions). Each section call sees the SAME
metadata JSON in its context and is told to use those names verbatim
— this is what prevents "drift" between sections (one section calling
the driver `uart_drv.c` and another calling it `uart_driver.c`).
"""

import asyncio
import json
import logging
from pathlib import Path

from agents.base_agent import BaseAgent
from config import settings
from generators.sdd_generator import SDDGenerator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool definitions for the parallel SDD pipeline.
#
# Step 1: `lock_sdd_design` emits the JSON contract that all section
# generators share. This is the no-drift mechanism — each section sees
# the same metadata in its prompt and is forbidden from inventing new
# names.
#
# Step 2: 5 parallel section tools (run via `asyncio.gather`):
#   - generate_sdd_intro_overview      (sections 1.x + 2.1)
#   - generate_sdd_architecture        (sections 2.2-2.5)
#   - generate_sdd_modules_detail      (section 2.6 — full module specs)
#   - generate_sdd_runtime_design      (sections 2.7-2.10)
#   - generate_sdd_traceability        (section 3 + appendices)
#
# Each section tool returns a markdown blob; we concatenate in
# document order to produce the final SDD.
# ---------------------------------------------------------------------------

LOCK_SDD_DESIGN_TOOL = {
    "name": "lock_sdd_design",
    "description": (
        "Step 1 of SDD generation: emit ONLY the structural design "
        "contract that the 5 parallel section generators will share. "
        "Module names, struct names, file paths, register addresses, "
        "task/ISR names — once locked here they MUST be used VERBATIM "
        "by every downstream section. NO inventing new names later. "
        "Be specific to the actual project (derive from the SRS / GLR / "
        "HRS context provided), not generic boilerplate."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "modules": {
                "type": "array",
                "description": (
                    "Every software module. Each entry locks file name, "
                    "responsibility, and the public function prototype list. "
                    "Subsequent section calls MUST use these names exactly."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name":            {"type": "string"},
                        "file":            {"type": "string"},
                        "header":          {"type": "string"},
                        "responsibility":  {"type": "string"},
                        "public_api":      {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "file", "header", "responsibility"],
                },
            },
            "structs": {
                "type": "array",
                "description": "C structs shared across modules. Lock once, use everywhere.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":   {"type": "string"},
                        "fields": {"type": "array", "items": {"type": "string"}},
                        "purpose": {"type": "string"},
                    },
                    "required": ["name", "fields"],
                },
            },
            "enums": {
                "type": "array",
                "description": "C enums (state machines + error codes + modes).",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":   {"type": "string"},
                        "values": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "values"],
                },
            },
            "tasks": {
                "type": "array",
                "description": (
                    "RTOS tasks / superloop tasks. Each entry pins the "
                    "scheduling characteristics so the Resource Viewpoint "
                    "section computes a consistent schedule."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name":     {"type": "string"},
                        "priority": {"type": "string"},
                        "period_ms": {"type": "number"},
                        "description": {"type": "string"},
                    },
                    "required": ["name", "description"],
                },
            },
            "isrs": {
                "type": "array",
                "description": "Interrupt service routines + their latency budgets.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":               {"type": "string"},
                        "vector":             {"type": "string"},
                        "latency_target_us":  {"type": "number"},
                        "trigger":            {"type": "string"},
                    },
                    "required": ["name", "trigger"],
                },
            },
            "interfaces": {
                "type": "array",
                "description": "External + internal interfaces (UART, SPI, I2C, GPIO, IPC).",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":  {"type": "string"},
                        "kind":  {"type": "string"},
                        "peer":  {"type": "string"},
                    },
                    "required": ["name", "kind"],
                },
            },
            "register_map": {
                "type": "array",
                "description": (
                    "FPGA register map referenced by the SDD. Fields: "
                    "address, name, R/W, reset value. Pulled from GLR — "
                    "MUST match what the firmware code references."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "address":  {"type": "string"},
                        "name":     {"type": "string"},
                        "access":   {"type": "string"},
                        "reset":    {"type": "string"},
                        "purpose":  {"type": "string"},
                    },
                    "required": ["address", "name", "access"],
                },
            },
            "file_layout": {
                "type": "array",
                "description": "Directory + file structure (drivers/, app/, tests/, etc.)",
                "items": {"type": "string"},
            },
            "naming_conventions": {
                "type": "object",
                "description": (
                    "Naming + coding-standard prefixes used throughout the SDD."
                ),
                "properties": {
                    "function_prefix":  {"type": "string"},
                    "type_suffix":      {"type": "string"},
                    "constant_style":   {"type": "string"},
                    "macro_style":      {"type": "string"},
                },
            },
            "target_platform": {
                "type": "object",
                "description": "FPGA family + MCU family + RTOS choice.",
                "properties": {
                    "fpga":     {"type": "string"},
                    "mcu":      {"type": "string"},
                    "rtos":     {"type": "string"},
                    "language": {"type": "string"},
                    "toolchain": {"type": "string"},
                },
            },
        },
        "required": ["modules", "tasks", "interfaces", "target_platform"],
    },
}

# --- Section 1: intro + context (sections 1.x + 2.1) ---
GENERATE_SDD_INTRO_OVERVIEW_TOOL = {
    "name": "generate_sdd_intro_overview",
    "description": (
        "Step 2a: emit the SDD Introduction (sections 1.1-1.4) AND the "
        "Context Viewpoint (section 2.1). Use the metadata's "
        "interfaces[] for the context diagram and the target_platform "
        "for the platform paragraph. MUST include the IEEE 1016 "
        "document-control table at the top + a Mermaid `graph TD` "
        "context diagram in 2.1."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "intro_overview_md": {
                "type": "string",
                "description": (
                    "Markdown for sections 1.1 through 2.1. ~3000-5000 words."
                ),
            },
        },
        "required": ["intro_overview_md"],
    },
}

# --- Section 2: architecture (sections 2.2-2.5) ---
GENERATE_SDD_ARCHITECTURE_TOOL = {
    "name": "generate_sdd_architecture",
    "description": (
        "Step 2b: emit the SDD Architecture sections — 2.2 Composition "
        "(layered architecture diagram + module list), 2.3 Logical "
        "(class diagram), 2.4 Information (data structures from "
        "metadata.structs[]), 2.5 Interface (function prototypes from "
        "metadata.modules[].public_api). MUST include at least 3 "
        "Mermaid diagrams. Use the LOCKED module + struct names "
        "verbatim — do NOT rename them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "architecture_md": {
                "type": "string",
                "description": (
                    "Markdown for sections 2.2-2.5. ~5000-8000 words "
                    "with full struct definitions + function prototypes."
                ),
            },
        },
        "required": ["architecture_md"],
    },
}

# --- Section 3: full module detail (section 2.6) ---
GENERATE_SDD_MODULES_DETAIL_TOOL = {
    "name": "generate_sdd_modules_detail",
    "description": (
        "Step 2c: emit section 2.6 Module Details. For EVERY module in "
        "metadata.modules[] write: full file name + header file, "
        "responsibility paragraph, complete C function prototypes, "
        "internal state variables, configuration constants, plus a "
        "Mermaid sequence or state diagram of the module's main flow. "
        "Use the LOCKED public_api function names — do NOT rename."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "modules_detail_md": {
                "type": "string",
                "description": (
                    "Markdown for section 2.6 — one subsection per "
                    "module. ~6000-10000 words for a typical project."
                ),
            },
        },
        "required": ["modules_detail_md"],
    },
}

# --- Section 4: runtime / dynamics / resource / build (sections 2.7-2.10) ---
GENERATE_SDD_RUNTIME_DESIGN_TOOL = {
    "name": "generate_sdd_runtime_design",
    "description": (
        "Step 2d: emit sections 2.7 State Dynamics (state machines from "
        "metadata.enums[] state-like enums), 2.8 Algorithm (key "
        "algorithms with pseudocode), 2.9 Resource (task scheduling "
        "table from metadata.tasks[], ISR latency budget from "
        "metadata.isrs[], memory budget), 2.10 Build System "
        "(CMakeLists.txt for drivers + firmware + Qt GUI + tests). "
        "MUST include Mermaid stateDiagram-v2 for each state machine."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "runtime_design_md": {
                "type": "string",
                "description": (
                    "Markdown for sections 2.7-2.10. ~4000-6000 words."
                ),
            },
        },
        "required": ["runtime_design_md"],
    },
}

# --- Section 5: traceability + appendices (section 3 + Appendix A-D) ---
GENERATE_SDD_TRACEABILITY_TOOL = {
    "name": "generate_sdd_traceability",
    "description": (
        "Step 2e: emit section 3 Traceability Matrix (every module + "
        "function from metadata back to a REQ-SW-xxx tag) + Appendix A "
        "(file layout from metadata.file_layout), Appendix B (full "
        "register map from metadata.register_map[]), Appendix C "
        "(MISRA-C 2012 compliance checklist), Appendix D (acronyms + "
        "glossary), and a revision history table. Plus the document's "
        "final References section."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "traceability_md": {
                "type": "string",
                "description": (
                    "Markdown for section 3 + Appendices A-D. "
                    "~3000-5000 words."
                ),
            },
        },
        "required": ["traceability_md"],
    },
}

_SDD_TOOLS = [
    LOCK_SDD_DESIGN_TOOL,
    GENERATE_SDD_INTRO_OVERVIEW_TOOL,
    GENERATE_SDD_ARCHITECTURE_TOOL,
    GENERATE_SDD_MODULES_DETAIL_TOOL,
    GENERATE_SDD_RUNTIME_DESIGN_TOOL,
    GENERATE_SDD_TRACEABILITY_TOOL,
]

SYSTEM_PROMPT = """You are a senior embedded software architect generating a comprehensive, publication-quality IEEE 1016-2009-compliant Software Design Document (SDD) for an embedded hardware system.

This document must be thorough — equivalent to 50+ pages of professional engineering content. Every section must be fully populated with project-specific, implementation-ready design details derived from the SRS and hardware context.

## DOCUMENT STRUCTURE (IEEE 1016-2009) — generate ALL sections in FULL:

# Software Design Document (SDD)

## Document Control
| Version | Date | Author | Description |
|---------|------|--------|-------------|
| 1.0 | — | — | Initial design |

---

# 1. Introduction

## 1.1 Purpose
State the full purpose of this SDD — what software system it describes, who will use it (firmware engineers, RTL designers, test engineers), and how it relates to the SRS.

## 1.2 Scope
Define the complete scope:
- Software components designed (BSP, HAL, drivers, application layer)
- What is explicitly NOT covered
- Target hardware platform (FPGA family, MCU if applicable)
- Programming language and toolchain

## 1.3 Definitions and Acronyms
Minimum 25 definitions: HAL, BSP, ISR, DMA, FIFO, CRC, WDT, PLL, UART, SPI, I2C, GPIO, MISRA, RTOS, IPC, API, NVMEM, POST, BIT, FSM, etc.

## 1.4 References
- IEEE 1016-2009 SDD standard
- SRS document (this project)
- HRS document
- GLR document
- MISRA C:2012 guidelines
- FPGA vendor documentation (Xilinx/Intel)
- Component datasheets

---

# 2. Design Viewpoints

## 2.1 Context Viewpoint — System Boundaries

Describe the complete software system context. Include a Mermaid context diagram:

```mermaid
graph TD
    HOST[Host PC / GUI Tool] -->|UART Commands| UART_DRV[UART Driver]
    UART_DRV --> REG_MAP[Register Map Handler]
    REG_MAP --> HAL[Hardware Abstraction Layer]
    HAL --> SPI_DRV[SPI Driver]
    HAL --> I2C_DRV[I2C Driver]
    HAL --> GPIO_DRV[GPIO Driver]
    SPI_DRV --> EEPROM[EEPROM]
    SPI_DRV --> FLASH[Configuration Flash]
    I2C_DRV --> TEMP[Temperature Sensor]
    I2C_DRV --> PWR_MON[Power Monitor]
    GPIO_DRV --> RF_CTRL[RF Control / TRP]
```

External interfaces:
- Host PC via UART (command/response register protocol)
- Debug interface via JTAG
- Hardware peripherals via SPI, I2C, GPIO

## 2.2 Composition Viewpoint — Software Architecture

Describe the complete layered software architecture. Include a component diagram:

```mermaid
graph TD
    APP[Application Layer] --> SCHED[Task Scheduler / Main Loop]
    SCHED --> MON[Monitor Task]
    SCHED --> CMD[Command Handler Task]
    SCHED --> CAL[Calibration Task]
    MON --> HAL
    CMD --> HAL
    CAL --> HAL
    HAL[Hardware Abstraction Layer] --> UART_DRV[UART Driver]
    HAL --> SPI_DRV[SPI Driver]
    HAL --> I2C_DRV[I2C Driver]
    HAL --> GPIO_DRV[GPIO Driver]
    HAL --> WDT_DRV[Watchdog Driver]
    HAL --> PLL_DRV[PLL Driver]
    HAL --> FLASH_DRV[Flash Driver]
    HAL --> EEPROM_DRV[EEPROM Driver]
    UART_DRV --> REG[FPGA Register Map]
    SPI_DRV --> REG
    I2C_DRV --> REG
    GPIO_DRV --> REG
```

### Module List with Responsibilities:

For EACH module, provide:
- Module name and source file
- Responsibility (1 paragraph)
- Public API (all function prototypes)
- Internal state variables
- Configuration constants

**Module: board_init** (board_init.c / board_init.h)
```c
// Responsibilities: Power-on initialization, clock setup, PLL configuration
int32_t Board_Init(void);
int32_t Board_GetVersion(BoardInfo_t *info);
int32_t Board_SelfTest(uint32_t *test_mask);

typedef struct {
    uint16_t board_id;
    uint8_t  hw_version_major;
    uint8_t  hw_version_minor;
    uint32_t fw_version;
    char     build_date[12];
} BoardInfo_t;
```

**Module: uart_driver** (uart_driver.c / uart_driver.h)
```c
// Responsibilities: UART framing, register read/write protocol, FIFO management
int32_t UART_Init(uint32_t baud_rate);
int32_t UART_Deinit(void);
int32_t UART_WriteReg(uint16_t addr, uint16_t data);
int32_t UART_ReadReg(uint16_t addr, uint16_t *data_out);
int32_t UART_BulkWrite(uint16_t start_addr, const uint16_t *data, uint8_t count);
int32_t UART_BulkRead(uint16_t start_addr, uint16_t *buf_out, uint8_t count);
int32_t UART_GetStatus(UART_Status_t *status);
void    UART_ISR(void);  // Interrupt service routine

typedef struct {
    bool tx_busy;
    bool rx_available;
    bool frame_error;
    uint8_t tx_fifo_count;
    uint8_t rx_fifo_count;
} UART_Status_t;
```

**Module: spi_driver** (spi_driver.c / spi_driver.h)
```c
// Responsibilities: SPI master for EEPROM and Flash communication
int32_t SPI_Init(uint8_t instance, uint32_t clock_hz, uint8_t cpol, uint8_t cpha);
int32_t SPI_Transfer(uint8_t instance, const uint8_t *tx, uint8_t *rx, uint16_t len);
int32_t SPI_ChipSelect(uint8_t instance, uint8_t cs_idx, bool active);
```

**Module: i2c_driver** (i2c_driver.c / i2c_driver.h)
```c
// Responsibilities: I2C master for temperature sensor and power monitor
int32_t I2C_Init(uint8_t instance, uint32_t clock_hz);
int32_t I2C_Write(uint8_t instance, uint8_t dev_addr, const uint8_t *data, uint8_t len);
int32_t I2C_Read(uint8_t instance, uint8_t dev_addr, uint8_t *buf, uint8_t len);
int32_t I2C_WriteReg(uint8_t instance, uint8_t dev_addr, uint8_t reg, uint8_t val);
int32_t I2C_ReadReg(uint8_t instance, uint8_t dev_addr, uint8_t reg, uint8_t *val_out);
```

**Module: temp_monitor** (temp_monitor.c / temp_monitor.h)
```c
// Responsibilities: Temperature reading, alert management, RF shutdown logic
int32_t TempMon_Init(const TempMon_Config_t *cfg);
int32_t TempMon_ReadAll(TempMon_Data_t *data_out);
int32_t TempMon_SetAlertThresh(float high_degC, float low_degC);
bool    TempMon_IsAlert(void);
void    TempMon_Task(void);  // Periodic task handler

typedef struct {
    float local_degC;
    float remote1_degC;
    float remote2_degC;
    bool  alert_active;
} TempMon_Data_t;
```

**Module: power_monitor** (power_monitor.c / power_monitor.h)
```c
// Responsibilities: Rail voltage/current monitoring, fault detection
int32_t PwrMon_Init(const PwrMon_Config_t *cfg);
int32_t PwrMon_ReadRail(uint8_t rail_idx, float *voltage_V, float *current_A);
int32_t PwrMon_ReadAll(PwrMon_Data_t *data_out);
bool    PwrMon_IsFault(void);
void    PwrMon_Task(void);
```

**Module: flash_driver** (flash_driver.c / flash_driver.h)
```c
// Responsibilities: Configuration flash read/write/erase
int32_t Flash_Init(void);
int32_t Flash_ReadID(uint32_t *id_out);
int32_t Flash_Read(uint32_t addr, uint8_t *buf, uint32_t len);
int32_t Flash_WritePage(uint32_t addr, const uint8_t *data, uint32_t len);
int32_t Flash_EraseSector(uint32_t sector_addr);
int32_t Flash_EraseChip(void);
int32_t Flash_WaitReady(uint32_t timeout_ms);
bool    Flash_IsBusy(void);
```

**Module: eeprom_driver** (eeprom_driver.c / eeprom_driver.h)
```c
// Responsibilities: EEPROM calibration data read/write
int32_t EEPROM_Init(void);
int32_t EEPROM_ReadByte(uint16_t addr, uint8_t *data_out);
int32_t EEPROM_WriteByte(uint16_t addr, uint8_t data);
int32_t EEPROM_ReadBlock(uint16_t addr, uint8_t *buf, uint16_t len);
int32_t EEPROM_WriteBlock(uint16_t addr, const uint8_t *data, uint16_t len);
```

**Module: pll_driver** (pll_driver.c / pll_driver.h)
```c
// Responsibilities: PLL configuration, lock monitoring
int32_t PLL_Init(const PLL_Config_t *cfg);
int32_t PLL_SetFrequency(uint32_t freq_hz);
int32_t PLL_WaitLock(uint32_t timeout_ms);
bool    PLL_IsLocked(void);
int32_t PLL_Reset(void);

typedef struct {
    uint32_t ref_freq_hz;
    uint32_t target_freq_hz;
    uint16_t n_divider;
    uint8_t  r_divider;
    uint8_t  clk_outputs_mask;
} PLL_Config_t;
```

**Module: cmd_handler** (cmd_handler.c / cmd_handler.h)
```c
// Responsibilities: Parse incoming UART commands, dispatch to register map, format responses
int32_t CmdHandler_Init(void);
void    CmdHandler_Process(void);  // Called from main loop
int32_t CmdHandler_ExecuteWrite(uint16_t addr, uint16_t data);
int32_t CmdHandler_ExecuteRead(uint16_t addr, uint16_t *data_out);
int32_t CmdHandler_ExecuteBulkWrite(uint16_t start, const uint16_t *data, uint8_t n);
int32_t CmdHandler_ExecuteBulkRead(uint16_t start, uint16_t *buf, uint8_t n);
```

**Module: watchdog** (watchdog.c / watchdog.h)
```c
// Responsibilities: Watchdog timer arming, petting, reset detection
int32_t WDT_Init(uint32_t timeout_ms);
void    WDT_Pet(void);
bool    WDT_WasResetCause(void);
void    WDT_Enable(void);
void    WDT_Disable(void);
```

Add more modules as required by the project (RF control, DAC driver, calibration manager, etc.)

## 2.3 Logical Viewpoint — Data Model

Define ALL key data structures used across the software:

```mermaid
classDiagram
    class BoardInfo_t {
        +uint16_t board_id
        +uint8_t hw_version_major
        +uint8_t hw_version_minor
        +uint32_t fw_version
        +char build_date[12]
    }
    class SystemState_t {
        +bool initialized
        +bool pll_locked
        +bool temp_alert
        +bool volt_fault
        +ErrorCode_t last_error
        +uint32_t uptime_sec
    }
    class TempMon_Data_t {
        +float local_degC
        +float remote1_degC
        +float remote2_degC
        +bool alert_active
    }
    class PwrMon_Data_t {
        +float v_5v
        +float v_3v3
        +float v_2v5
        +float v_1v8
        +float i_5v
        +float i_3v3
    }
    SystemState_t --> TempMon_Data_t
    SystemState_t --> PwrMon_Data_t
    SystemState_t --> BoardInfo_t
```

Define ALL enumerations:
```c
typedef enum {
    SYS_STATE_RESET = 0,
    SYS_STATE_INIT,
    SYS_STATE_RUNNING,
    SYS_STATE_FAULT,
    SYS_STATE_SHUTDOWN
} SystemState_e;

typedef enum {
    ERR_OK = 0x00,
    ERR_TIMEOUT = 0x01,
    ERR_COMM = 0x02,
    ERR_CHECKSUM = 0x03,
    ERR_PARAM = 0x04,
    ERR_NOT_INIT = 0x05,
    ERR_RESOURCE = 0x06,
    ERR_HARDWARE = 0x07,
    ERR_OVERFLOW = 0x08,
    ERR_FLASH_WRITE = 0x0A,
    ERR_FLASH_ERASE = 0x0B,
    ERR_EEPROM = 0x0C,
    ERR_PLL = 0x0D,
    ERR_TEMP_ALERT = 0x0E,
    ERR_VOLT_FAULT = 0x0F,
} ErrorCode_t;
```

## 2.4 Dependency Viewpoint — Module Dependencies

```mermaid
graph TD
    main --> board_init
    main --> cmd_handler
    main --> temp_monitor
    main --> power_monitor
    main --> watchdog
    board_init --> uart_driver
    board_init --> spi_driver
    board_init --> i2c_driver
    board_init --> pll_driver
    cmd_handler --> uart_driver
    temp_monitor --> i2c_driver
    power_monitor --> i2c_driver
    pll_driver --> uart_driver
    flash_driver --> spi_driver
    eeprom_driver --> spi_driver
```

Build order: hardware abstraction drivers → board init → peripheral drivers → application modules.

## 2.5 Interface Viewpoint — Complete API Specification

For EVERY public function, document:
- Function signature
- Parameters with types and valid ranges
- Return value and error codes
- Pre/post conditions
- Thread safety
- Example usage

Example full specification:
```c
/**
 * @brief Initialize the UART peripheral for register protocol communication.
 *
 * @param baud_rate  Target baud rate in bits/second. Valid range: 9600–12000000.
 * @return ERR_OK    on success
 * @return ERR_PARAM if baud_rate is outside valid range
 * @return ERR_HARDWARE if hardware initialization fails
 *
 * @pre  System clock must be initialized before calling this function.
 * @post UART is ready for WriteReg/ReadReg calls.
 * @note Not thread-safe. Call only during initialization.
 *
 * @example
 *   if (UART_Init(115200) != ERR_OK) { FATAL_ERROR(); }
 */
int32_t UART_Init(uint32_t baud_rate);
```

(Document ALL public functions of ALL modules similarly)

## 2.6 Interaction Viewpoint — Sequence Diagrams

**System Startup Sequence:**
```mermaid
sequenceDiagram
    participant RST as Reset
    participant BSP as board_init
    participant PLL as pll_driver
    participant UART as uart_driver
    participant APP as Application
    RST->>BSP: Board_Init()
    BSP->>BSP: Configure clocks
    BSP->>PLL: PLL_Init(&pll_cfg)
    PLL->>PLL: Write N/R dividers
    PLL->>PLL: Enable PLL
    loop Poll until lock or timeout
        PLL->>PLL: PLL_IsLocked()?
    end
    PLL-->>BSP: Locked OK
    BSP->>UART: UART_Init(115200)
    BSP-->>APP: Board ready
    APP->>APP: Load calibration from EEPROM
    APP->>APP: POST (self-test)
    APP->>APP: Main loop
```

**UART Register Write Sequence:**
```mermaid
sequenceDiagram
    participant HOST as Host PC
    participant CMD as cmd_handler
    participant UART as uart_driver
    participant REG as Register Map
    HOST->>UART: [0x57][ADDR_H][ADDR_L][DATA_H][DATA_L]
    UART->>CMD: CmdHandler_Process()
    CMD->>CMD: Parse frame, validate address
    CMD->>REG: Write register
    REG-->>CMD: Write complete
    CMD->>UART: Send ACK (0x06)
    UART-->>HOST: [0x06]
```

**Temperature Alert Sequence:**
```mermaid
sequenceDiagram
    participant TMR as Periodic Timer
    participant MON as temp_monitor
    participant I2C as i2c_driver
    participant RF as RF Control
    participant LOG as UART Logger
    TMR->>MON: TempMon_Task()
    MON->>I2C: I2C_ReadReg(TEMP_ADDR, TEMP_REG, &raw)
    I2C-->>MON: raw temperature data
    MON->>MON: Convert to °C, compare with threshold
    MON->>LOG: Log temperature via UART
    alt Temperature > THRESH_HIGH
        MON->>RF: Set TRP = LOW (RF off)
        MON->>LOG: UART log TEMP_ALERT
    else Temperature < THRESH_LOW (hysteresis)
        MON->>RF: Set TRP = HIGH (RF on)
    end
```

**Flash Write Sequence:**
```mermaid
sequenceDiagram
    participant APP as Application
    participant FLASH as flash_driver
    participant SPI as spi_driver
    APP->>FLASH: Flash_EraseSector(sector_addr)
    FLASH->>SPI: Send WRITE_ENABLE (0x06)
    FLASH->>SPI: Send SECTOR_ERASE (0x20, addr[23:0])
    loop Poll BUSY
        FLASH->>SPI: Read STATUS_REG1
        SPI-->>FLASH: STATUS byte
    end
    FLASH-->>APP: Erase complete
    APP->>FLASH: Flash_WritePage(addr, data, len)
    FLASH->>SPI: Send WRITE_ENABLE
    FLASH->>SPI: Send PAGE_PROGRAM (0x02, addr, data)
    FLASH->>FLASH: Wait BUSY cleared
    FLASH-->>APP: Write complete
    APP->>FLASH: Flash_Read(addr, verify_buf, len)
    APP->>APP: CRC32 verify
```

## 2.7 State Viewpoint — State Machines

**System State Machine:**
```mermaid
stateDiagram-v2
    [*] --> RESET
    RESET --> INIT: Board_Init() called
    INIT --> RUNNING: POST passed, all peripherals ready
    INIT --> FAULT: POST failed or hardware error
    RUNNING --> FAULT: Voltage fault or critical error
    RUNNING --> SHUTDOWN: Shutdown command received
    FAULT --> INIT: Watchdog reset
    SHUTDOWN --> [*]
```

**Command Handler State Machine:**
```mermaid
stateDiagram-v2
    [*] --> IDLE
    IDLE --> WAIT_ADDR_H: CMD byte received (0x57/0x52/0x42/0x62)
    WAIT_ADDR_H --> WAIT_ADDR_L: ADDR_MSB received
    WAIT_ADDR_L --> WAIT_DATA_H: ADDR_LSB received (write cmds only)
    WAIT_DATA_H --> WAIT_DATA_L: DATA_MSB received
    WAIT_DATA_L --> EXECUTE: DATA_LSB received
    WAIT_ADDR_L --> EXECUTE: ADDR_LSB received (read cmds only)
    EXECUTE --> IDLE: ACK/NAK sent
    IDLE --> IDLE: Invalid byte → NAK
```

**Temperature Monitor State Machine:**
```mermaid
stateDiagram-v2
    [*] --> NORMAL
    NORMAL --> ALERT_HIGH: temp > HIGH_THRESH
    ALERT_HIGH --> NORMAL: temp < (HIGH_THRESH - HYSTERESIS)
    ALERT_HIGH --> CRITICAL: temp > CRITICAL_THRESH
    CRITICAL --> [*]: System shutdown
    NORMAL --> ALERT_LOW: temp < LOW_THRESH
    ALERT_LOW --> NORMAL: temp > (LOW_THRESH + HYSTERESIS)
```

**PLL State Machine:**
```mermaid
stateDiagram-v2
    [*] --> DISABLED
    DISABLED --> CONFIGURING: PLL_Init() / PLL_SetFrequency()
    CONFIGURING --> LOCKING: N/R dividers written, PLL enabled
    LOCKING --> LOCKED: LOCKED bit set
    LOCKING --> ERROR: Timeout (100ms)
    LOCKED --> LOSS_OF_LOCK: LOCKED bit cleared
    LOSS_OF_LOCK --> CONFIGURING: Auto-retry
    ERROR --> CONFIGURING: Manual retry / watchdog reset
```

## 2.8 Algorithm Viewpoint — Key Algorithms

### 2.8.1 UART Frame Parser
```c
// Frame parser state machine using a ring buffer
// 1. Read next byte from RX FIFO
// 2. Match CMD byte: 0x57=write, 0x52=read, 0x42=bulk-write, 0x62=bulk-read
// 3. Accumulate ADDR_H, ADDR_L bytes
// 4. For write: accumulate DATA_H, DATA_L
// 5. Execute register operation
// 6. Send ACK (0x06) or NAK (0x15)
// Timeout: reset parser if inter-byte gap > 10ms
```

### 2.8.2 Temperature Conversion
```c
// Raw ADC count to degrees Celsius:
// For 10-bit signed register (2's complement):
//   temp_degC = (int16_t)(raw_count) * 0.25f;
// For I2C sensor (e.g. LM75 format):
//   temp_degC = ((int16_t)(raw_msb << 8 | raw_lsb) >> 5) * 0.125f;
```

### 2.8.3 Power Rail Monitoring
```c
// ADC count to Volts:
//   voltage_V = (float)adc_count * VREF / ADC_FULL_SCALE * VOLTAGE_DIVIDER_RATIO;
// ADC count to Amps (shunt resistor method):
//   current_A = (float)adc_count * VREF / ADC_FULL_SCALE / SHUNT_OHMS;
```

### 2.8.4 CRC-32 for Flash Verification
```c
uint32_t CRC32_Compute(const uint8_t *data, uint32_t len);
bool     CRC32_Verify(const uint8_t *data, uint32_t len, uint32_t expected_crc);
// Uses IEEE 802.3 polynomial: 0xEDB88320 (reflected)
```

---

# 3. Design Rationale

## 3.1 Architecture Choices

For EACH major design decision, provide:
- Decision made
- Alternatives considered
- Rationale for the chosen approach
- Trade-offs accepted

Example decisions to cover:
- Bare-metal vs RTOS
- Polling vs interrupt-driven communication
- Static vs dynamic memory allocation (always static — MISRA)
- Modular HAL vs direct register access
- CRC algorithm selection
- FIFO depth sizing

## 3.2 MISRA-C:2012 Compliance Strategy
- All functions return error codes (no void returns for operations that can fail)
- No dynamic allocation — all buffers statically declared
- All casts explicit — no implicit type conversions
- Bounds checking on all array accesses
- No recursion
- Maximum function complexity: cyclomatic ≤ 15
- Tools: PC-lint, MISRA Checker, Polyspace

---

# 4. Design Traceability Matrix

| SDD Component | Implements REQ-SW-xxx | Design Element |
|--------------|----------------------|----------------|
| board_init.Board_Init() | REQ-SW-001, REQ-SW-002 | System initialization |
| pll_driver.PLL_Init() | REQ-SW-003, REQ-SW-004 | PLL configuration |
| uart_driver.UART_WriteReg() | REQ-SW-012 | UART single write |
| uart_driver.UART_ReadReg() | REQ-SW-013 | UART single read |
| uart_driver.UART_BulkWrite() | REQ-SW-014 | UART bulk write |
| uart_driver.UART_BulkRead() | REQ-SW-015 | UART bulk read |
| temp_monitor.TempMon_Task() | REQ-SW-021 through REQ-SW-025 | Temperature monitoring |
| flash_driver.Flash_WritePage() | REQ-SW-031, REQ-SW-032 | Flash write with CRC verify |
| power_monitor.PwrMon_Task() | REQ-SW-041, REQ-SW-042 | Power monitoring |
(Continue for ALL REQ-SW-xxx from SRS)

---

# 5. Appendices

## Appendix A — File Structure
```
src/
├── main.c              # Main entry point, task scheduler
├── board/
│   ├── board_init.c    # Hardware initialization
│   ├── board_init.h
│   └── board_config.h  # Platform-specific #defines
├── drivers/
│   ├── uart_driver.c
│   ├── uart_driver.h
│   ├── spi_driver.c
│   ├── spi_driver.h
│   ├── i2c_driver.c
│   ├── i2c_driver.h
│   ├── gpio_driver.c
│   ├── gpio_driver.h
│   ├── pll_driver.c
│   ├── pll_driver.h
│   ├── flash_driver.c
│   ├── flash_driver.h
│   ├── eeprom_driver.c
│   └── eeprom_driver.h
├── app/
│   ├── cmd_handler.c   # UART command processor
│   ├── cmd_handler.h
│   ├── temp_monitor.c  # Temperature monitoring task
│   ├── temp_monitor.h
│   ├── power_monitor.c # Power monitoring task
│   ├── power_monitor.h
│   ├── watchdog.c
│   └── watchdog.h
└── utils/
    ├── crc32.c         # CRC-32 IEEE 802.3
    ├── crc32.h
    ├── ring_buffer.c   # Lock-free ring buffer
    └── ring_buffer.h
```

## Appendix B — Register Map Summary
(Summary of all FPGA registers accessed by software, grouped by BASE_ADDR)

## Appendix C — Memory Map
| Region | Start Address | Size | Usage |
|--------|--------------|------|-------|
| Flash | 0x00000000 | [X] MB | Firmware code + constants |
| RAM | 0x20000000 | [X] KB | Stack + BSS + heap (static) |
| FPGA Registers | 0x40000000 | [X] KB | Memory-mapped FPGA regs |
| EEPROM | SPI | [X] KB | Calibration data |
| Config Flash | SPI | [X] MB | FPGA bitstream |

## Appendix D — Coding Standards Checklist
- [ ] All functions return ErrorCode_t
- [ ] No malloc/calloc/free/realloc
- [ ] No recursion
- [ ] All array accesses bounds-checked
- [ ] All switch statements have default case
- [ ] All if/else fully braced
- [ ] All variables initialized at declaration
- [ ] Cyclomatic complexity ≤ 15 per function
- [ ] Doxygen headers on all public functions
- [ ] Unit test for each driver module

---

## 2.9 Resource Viewpoint — Real-Time Constraints

### 2.9.1 Task Scheduling Table
Define all periodic tasks and their timing budget:

| Task Name | Period | Worst-Case Exec Time | Priority | Deadline | CPU Load |
|-----------|--------|---------------------|----------|----------|---------|
| main_loop | 10ms | [X]µs | N/A | 10ms | [X]% |
| TempMon_Task | 1000ms | [X]µs | Low | 1000ms | [X]% |
| PwrMon_Task | 500ms | [X]µs | Low | 500ms | [X]% |
| WDT_Pet | 5000ms | [X]µs | Highest | 5000ms | [X]% |
| CmdHandler_Process | 1ms | [X]µs | Medium | 5ms | [X]% |

### 2.9.2 ISR Latency Budget
| Interrupt Source | Latency Requirement | Worst-Case Measured | Margin |
|-----------------|--------------------|--------------------|--------|
| UART RX | < [X]µs | [X]µs | [X]% |
| SPI Transfer Complete | < [X]µs | [X]µs | [X]% |
| Timer Tick | < [X]µs | [X]µs | [X]% |
| Temperature Alert GPIO | < [X]µs | [X]µs | [X]% |

### 2.9.3 Memory Budget
| Region | Total Available | Used | Remaining |
|--------|----------------|------|-----------|
| Code Flash | [X] KB | [X] KB | [X] KB |
| Data Flash | [X] KB | [X] KB | [X] KB |
| SRAM | [X] KB | [X] KB | [X] KB |
| EEPROM | [X] KB | [X] KB | [X] KB |
| Stack (worst path) | [X] KB | [X] KB | [X] KB |

---

## 2.10 Build System Viewpoint

### 2.10.1 CMakeLists.txt Structure
```cmake
cmake_minimum_required(VERSION 3.20)
project([ProjectName] VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

# Driver library (C)
add_library(drivers STATIC
    drivers/uart_driver.c
    drivers/spi_driver.c
    drivers/i2c_driver.c
    drivers/gpio_driver.c
    drivers/pll_driver.c
    drivers/flash_driver.c
    drivers/eeprom_driver.c
    utils/crc32.c
    utils/ring_buffer.c
)

# Application (C)
add_executable(firmware
    src/main.c
    src/board/board_init.c
    src/app/cmd_handler.c
    src/app/temp_monitor.c
    src/app/power_monitor.c
    src/app/watchdog.c
)
target_link_libraries(firmware PRIVATE drivers)
target_compile_options(firmware PRIVATE
    -Wall -Wextra -Werror
    -fstack-usage      # generate .su files for stack analysis
    -ffunction-sections -fdata-sections  # dead-code elimination
)

# Qt6 C++ GUI (optional)
find_package(Qt6 COMPONENTS Widgets SerialPort QUIET)
if (Qt6_FOUND)
    add_subdirectory(qt_gui)
endif()

# Unit Tests (CTest + Google Test)
enable_testing()
add_subdirectory(tests)
```

### 2.10.2 Cross-Compilation for ARM Target
```cmake
# Toolchain file: arm-none-eabi.cmake
set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR arm)
set(CMAKE_C_COMPILER arm-none-eabi-gcc)
set(CMAKE_CXX_COMPILER arm-none-eabi-g++)
set(CMAKE_EXE_LINKER_FLAGS "-specs=nosys.specs -specs=nano.specs" CACHE STRING "" FORCE)
```

### 2.10.3 Unit Test Infrastructure
```cmake
# tests/CMakeLists.txt
find_package(GTest REQUIRED)
add_executable(test_drivers
    test_uart_driver.cpp
    test_spi_driver.cpp
    test_i2c_driver.cpp
    test_flash_driver.cpp
    mock_hardware.cpp   # hardware mock layer for host testing
)
target_link_libraries(test_drivers PRIVATE drivers GTest::gtest_main)
include(GoogleTest)
gtest_discover_tests(test_drivers)
```

---

## ABSOLUTE RULES:
1. ALL modules must have complete, syntactically correct C99 function prototypes
2. ALL Mermaid diagrams must be valid (sequenceDiagram, stateDiagram-v2, graph TD, classDiagram). STRICT label rules: NO single-quotes ', double-quotes ", angle brackets < >, #, |, & or colons : inside node labels. NO 3+ consecutive dashes (---) inside labels. Use plain ASCII words only.
3. NEVER use TBD, TBC, or TBA — use actual values from the SRS/HRS/GLR or state explicit assumptions
4. The traceability matrix must map EVERY design element to its REQ-SW-xxx from the SRS
5. Include minimum 8 Mermaid diagrams across all viewpoints
6. Design must be 100% MISRA-C:2012 compliant — no exceptions
7. Be highly specific to the actual project hardware — derive module names, register addresses, and constants from the SRS/GLR context provided
8. Section 2.9 (Resource Viewpoint) MUST include the task scheduling table, ISR latency budget, and memory budget
9. Section 2.10 (Build System) MUST include the CMakeLists.txt structure for drivers + firmware + Qt6 GUI + unit tests
"""


class SDDAgent(BaseAgent):
    """Phase 8b: IEEE 1016-compliant SDD generation."""

    def __init__(self):
        super().__init__(
            phase_number="P8b",
            phase_name="SDD Generation",
            model=settings.fast_model,
            max_tokens=16384,
            # P26 #16: tools registered so the parallel pipeline can
            # call any of the 6 (1 metadata + 5 sections). The legacy
            # text-only path (no tool_choice, no tools used) still works
            # as a fallback if metadata-locking fails.
            tools=_SDD_TOOLS,
        )
        self.sdd_generator = SDDGenerator()

    def get_system_prompt(self, project_context: dict) -> str:
        return SYSTEM_PROMPT

    async def execute(self, project_context: dict, user_input: str) -> dict:
        output_dir = Path(project_context.get("output_dir", "output"))
        output_dir.mkdir(parents=True, exist_ok=True)
        project_name = project_context.get("name", "Project")

        # Load SRS (primary input) and context
        srs = self._load_file(output_dir / f"SRS_{project_name.replace(' ', '_')}.md")
        hrs = self._load_file(output_dir / f"HRS_{project_name.replace(' ', '_')}.md")
        glr = self._load_file(output_dir / "glr_specification.md")

        if not srs:
            return {
                "response": "SRS not found. Complete Phase 8a first.",
                "phase_complete": False,
                "outputs": {},
            }

        from datetime import datetime
        today = datetime.now().strftime("%d %B %Y")

        # Common context block fed to the metadata call AND to every
        # parallel section call. Capping at 10K SRS / 5K GLR / 3K HRS
        # keeps every per-call input under ~20K tokens — section calls
        # ALSO get the metadata JSON, which adds ~2-4K tokens.
        context_block = (
            f"**Project:** {project_name}\n"
            f"**Date:** {today}\n\n"
            f"## Software Requirements Specification (SRS — primary input):\n{srs[:10000]}\n\n"
            f"## GLR Specification (FPGA registers, signal names, UART protocol):\n{glr[:5000] if glr else 'Not available.'}\n\n"
            f"## HRS (hardware context, power rails, interfaces):\n{hrs[:3000] if hrs else 'Not available.'}\n\n"
        )

        sdd_content = ""

        # ── PARALLEL PATH (P26 #16) ─────────────────────────────────────
        # Step 1: lock the metadata contract (modules, structs, register
        # map, file layout, naming conventions). This is the ONLY call
        # that decides those names — parallel sections must use them
        # verbatim.
        meta_user_message = (
            f"{context_block}\n"
            "Step 1 of SDD generation: call ONLY `lock_sdd_design` and "
            "emit the design contract. Pull module / struct / register "
            "names from the SRS + GLR + HRS context above. Be SPECIFIC "
            "and FINAL — five other section generators are about to fire "
            "in parallel using these names verbatim, and any divergence "
            "between sections (e.g. you say `uart_drv.c` here and the "
            "Modules section uses `uart_driver.c`) breaks the design. "
            "Locked-in metadata wins; sections cannot rename things."
        )

        metadata: dict | None = None
        try:
            meta_response = await self.call_llm(
                messages=[{"role": "user", "content": meta_user_message}],
                system=SYSTEM_PROMPT,
                tools=_SDD_TOOLS,
                tool_choice={"type": "tool", "name": "lock_sdd_design"},
            )
            if meta_response.get("tool_calls"):
                for tc in meta_response["tool_calls"]:
                    if tc["name"] == "lock_sdd_design":
                        metadata = tc["input"]
                        break
            if metadata and not meta_response.get("degraded"):
                # Step 2: 5 parallel section calls. Each call sees the
                # SAME locked metadata in its prompt and is asked to use
                # it verbatim — that's the no-drift mechanism.
                meta_blob = json.dumps(metadata, indent=2)[:6000]
                sections_context = (
                    f"{context_block}\n"
                    "## LOCKED SDD METADATA (Step 1 output)\n"
                    "Use these EXACT names — module file names, struct "
                    "names, register addresses, task names, ISR names. "
                    "Four other section generators are running in "
                    "parallel right now using the same metadata; "
                    "renaming anything will create internal "
                    "inconsistencies that fail the IEEE 1016 review.\n\n"
                    f"```json\n{meta_blob}\n```\n\n"
                    "Step 2 of SDD generation: emit ONLY this one "
                    "section tool's markdown."
                )
                sections_messages = [
                    {"role": "user", "content": sections_context},
                ]

                # P26 #21 (2026-05-04): GLM rate-limits 5 simultaneous calls
                # (HTTP 429 'Rate limit reached for requests'). All-parallel
                # made every section race the same 1-call-at-a-time bucket
                # and most lost — falling through to the legacy path which
                # ALSO hit 429s, then to the deterministic template (1.9 KB
                # output instead of 60+ pages). Cap concurrency at 2 so
                # bursts stay within the free-tier QPS budget while still
                # cutting wall time roughly in half vs. fully sequential.
                _section_sem = asyncio.Semaphore(2)

                async def _gen_section(tool_name: str, payload_field: str) -> str:
                    """Fire one section tool call. On failure return ""
                    so the caller can fall back without aborting the
                    whole phase."""
                    async with _section_sem:
                        try:
                            resp = await self.call_llm(
                                messages=sections_messages,
                                system=SYSTEM_PROMPT,
                                tools=_SDD_TOOLS,
                                tool_choice={"type": "tool", "name": tool_name},
                            )
                        except Exception as exc:
                            logger.warning(
                                "P8b section %s raised: %s",
                                tool_name, str(exc)[:200],
                            )
                            return ""
                        if resp.get("tool_calls"):
                            for tc in resp["tool_calls"]:
                                if tc["name"] == tool_name:
                                    val = tc["input"].get(payload_field, "")
                                    return val if isinstance(val, str) else ""
                        return ""

                self.log("P8b: dispatching 5 parallel SDD section sub-calls "
                         "(intro + architecture + modules + runtime + traceability)")
                intro_md, arch_md, mods_md, runtime_md, trace_md = await asyncio.gather(
                    _gen_section("generate_sdd_intro_overview",   "intro_overview_md"),
                    _gen_section("generate_sdd_architecture",     "architecture_md"),
                    _gen_section("generate_sdd_modules_detail",   "modules_detail_md"),
                    _gen_section("generate_sdd_runtime_design",   "runtime_design_md"),
                    _gen_section("generate_sdd_traceability",     "traceability_md"),
                    return_exceptions=False,
                )

                # Concatenate in document order. A missing section is
                # better than the whole phase failing — log it and
                # insert a clear placeholder so the reader sees the gap
                # instead of a silent omission.
                sections = [
                    ("Introduction & Context Viewpoint",  intro_md),
                    ("Architecture Viewpoints",            arch_md),
                    ("Module Detail",                      mods_md),
                    ("Runtime / Resource / Build",         runtime_md),
                    ("Traceability & Appendices",          trace_md),
                ]
                missing = [name for name, body in sections if not body.strip()]
                if missing:
                    logger.warning(
                        "P8b: %d/5 sections came back empty — %s "
                        "(non-fatal; SDD will assemble what's available)",
                        len(missing), ", ".join(missing),
                    )
                # P26 #21 (2026-05-04): lowered the success threshold from
                # 3-of-5 to 1-of-5. Even one section's worth of LLM-written
                # SDD content (typically 5-15 KB) is dramatically better
                # than the 1.9 KB deterministic template, and gating high
                # forced the whole phase to fall to template whenever GLM
                # rate-limited 3+ of the parallel calls.
                if sum(1 for _, body in sections if body.strip()) >= 1:
                    sdd_content = "\n\n".join(body for _, body in sections if body.strip())
                    if missing:
                        sdd_content += (
                            "\n\n---\n\n"
                            "> **Note:** The following sections were "
                            "not generated by the parallel pipeline "
                            f"and are pending regeneration: {', '.join(missing)}.\n"
                        )
                    self.log(
                        f"P8b parallel pipeline: {len(sdd_content)} chars "
                        f"({5 - len(missing)}/5 sections)"
                    )
        except Exception as e:
            self.log(f"P8b parallel SDD generation failed: {e} — falling "
                     f"back to legacy single-call path", "warning")
            sdd_content = ""

        # ── LEGACY PATH (sequential continuation passes) ────────────────
        # Used when the parallel path failed (metadata empty OR < 3 of 5
        # sections came back). Same as the old behaviour pre-P26 #16.
        if not sdd_content:
            user_message = (
                f"Generate a COMPLETE, DETAILED, 60+ page IEEE 1016-2009 Software Design Document for:\n\n"
                f"{context_block}"
                "INSTRUCTIONS:\n"
                "1. Generate ALL sections from the IEEE 1016 structure in your system prompt\n"
                "2. Include complete C struct definitions and ALL function prototypes for every module\n"
                "3. Include minimum 8 Mermaid diagrams (sequenceDiagram, stateDiagram-v2, graph TD, classDiagram)\n"
                "4. Every design element must trace back to a REQ-SW-xxx from the SRS\n"
                "5. Design must be MISRA-C:2012 compliant throughout\n"
                "6. Section 2.9 (Resource Viewpoint) MUST include: task scheduling table, ISR latency budget, and memory budget derived from HRS\n"
                "7. Section 2.10 (Build System) MUST include: CMakeLists.txt structure for drivers + firmware + Qt6 GUI + unit tests (Google Test)\n"
                "8. Appendix B MUST include: full FPGA register map (base address, offset, name, R/W, reset value) from GLR\n"
                "9. Derive module names, register addresses, constants from the SRS/GLR — no generic boilerplate\n"
                "10. NEVER use TBD/TBC/TBA — use actual values or explicit engineering assumptions"
            )

            # P26 #20 (2026-04-26): wall-time budget for the legacy
            # continuation loop. Pre-fix the loop could fire 5 sequential
            # ~90-120 s continuation passes (10+ min total) and then
            # STILL fall through to the template fallback when content
            # didn't reach 800 chars. The user reported a 12-15 min
            # phase that produced only the generic template — wasted
            # API calls + wasted wall time. Now we abort the loop early
            # if we've already spent more than this many seconds.
            import time as _time
            _legacy_t0 = _time.monotonic()
            _LEGACY_BUDGET_S = 300.0  # 5 min hard cap on legacy fallback

            try:
                response = await self.call_llm(
                    messages=[{"role": "user", "content": user_message}],
                    system=SYSTEM_PROMPT,
                )
                sdd_content = response.get("content", "")

                # Up to 5 continuation passes — each feeds accumulated text back as context
                _SDD_CONT_PROMPTS = [
                    (
                        "Continue the SDD from exactly where you left off. "
                        "Do NOT repeat any sections already written. "
                        "Complete remaining viewpoints, module interface definitions, "
                        "state machines, and interrupt/task scheduling design."
                    ),
                    (
                        "Continue the SDD. Do NOT repeat content already written. "
                        "Write detailed algorithm descriptions, data flow diagrams (Mermaid flowcharts), "
                        "and the complete sequence diagrams for every major hardware interaction."
                    ),
                    (
                        "Continue the SDD. Do NOT repeat content already written. "
                        "Complete the IEEE 1016 Information Viewpoint: "
                        "all data structures (C structs), enums, configuration tables, "
                        "and persistent data layout in non-volatile memory."
                    ),
                    (
                        "Continue the SDD. Do NOT repeat content already written. "
                        "Write the full Design Traceability Matrix "
                        "(SDD component/function → REQ-SW-xxx → REQ-HW-xxx). "
                        "Every SDD design decision must trace to at least one SRS requirement."
                    ),
                    (
                        "Finalize the SDD. Do NOT repeat content already written. "
                        "Write Appendix A (file/directory structure), Appendix B (memory map), "
                        "Appendix C (coding standards compliance checklist), "
                        "Appendix D (acronyms/glossary), and the revision history table."
                    ),
                ]

                # P26 #21 (2026-05-04): same end_turn-vs-max_tokens fix
                # as SRS. GLM stops with end_turn long before the 16384
                # token cap, so the continuation loop never fired and SDD
                # output stayed at ~5 KB. Now we keep firing while the
                # accumulated doc is short (< 25 KB ≈ ~10 IEEE 1016 pages).
                _SDD_TARGET_CHARS = 25000
                for _pass_idx, _cont_prompt in enumerate(_SDD_CONT_PROMPTS, start=1):
                    _truncated = response.get("stop_reason") == "max_tokens"
                    _too_short = len(sdd_content) < _SDD_TARGET_CHARS
                    if not _truncated and not _too_short:
                        break
                    # P26 #20: stop firing continuations if we've already
                    # blown the 5-minute wall-time budget. Better to ship
                    # whatever's accumulated than burn another 90-120 s
                    # only to hit the 800-char fallback gate.
                    elapsed = _time.monotonic() - _legacy_t0
                    if elapsed >= _LEGACY_BUDGET_S:
                        self.log(
                            f"SDD legacy continuation budget exhausted "
                            f"({elapsed:.0f}s >= {_LEGACY_BUDGET_S:.0f}s) "
                            f"after pass {_pass_idx-1}/5 — shipping what "
                            f"we have ({len(sdd_content)} chars)",
                            "warning",
                        )
                        break
                    self.log(
                        f"SDD continuation pass {_pass_idx}/5 "
                        f"(stop_reason={response.get('stop_reason')!r}, "
                        f"len={len(sdd_content)})..."
                    )
                    _cont = await self.call_llm(
                        messages=[
                            {"role": "user", "content": user_message},
                            {"role": "assistant", "content": sdd_content},
                            {"role": "user", "content": _cont_prompt},
                        ],
                        system=SYSTEM_PROMPT,
                    )
                    _cont_text = _cont.get("content", "") or ""
                    if not _cont_text.strip():
                        self.log(
                            f"SDD continuation {_pass_idx}/5 returned empty — "
                            f"stopping (final len={len(sdd_content)})"
                        )
                        break
                    sdd_content += "\n\n" + _cont_text
                    response = _cont  # check this response's stop_reason in next iteration
            except Exception as e:
                self.log(f"LLM SDD generation failed: {e} — falling back to template", "warning")

        # FALLBACK: template generator
        if not sdd_content or len(sdd_content) < 800:
            modules = await self._extract_modules(srs)
            interfaces = await self._extract_interfaces(srs, glr)
            state_machines = await self._extract_state_machines(srs)
            sdd_content = self.sdd_generator.generate(
                project_name=project_name,
                modules=modules,
                interfaces=interfaces,
                state_machines=state_machines,
                metadata={"version": project_context.get("version", "1.0")},
            )

        # Scrub any TBD/TBC/TBA the LLM wrote despite instructions
        import re as _re
        sdd_content = _re.sub(r'\b(TBD|TBC|TBA)\b', '[specify]', sdd_content, flags=_re.IGNORECASE)

        # P26 #17 (2026-04-26): coerce + re-render every embedded
        # `mermaid` block so LLM-emitted bracket mismatches don't
        # break the in-browser preview. See `tools.mermaid_coerce`
        # for the full bug-class background.
        try:
            from tools.mermaid_coerce import sanitize_mermaid_blocks_in_markdown
            sdd_content = sanitize_mermaid_blocks_in_markdown(sdd_content)
        except Exception as _exc:
            self.log(f"SDD mermaid sanitise skipped: {_exc}", "warning")

        # Save output
        sdd_file = self.sdd_generator.save(sdd_content, output_dir, project_name)
        self.log(f"SDD generated: {len(sdd_content)} chars")

        return {
            "response": "SDD document generated (IEEE 1016 compliant).",
            "phase_complete": True,
            "outputs": {sdd_file.name: sdd_content},
        }

    async def _extract_modules(self, srs: str) -> list:
        """Extract software modules from SRS by parsing headers and component names."""
        import re
        modules = []

        if not srs:
            return self._default_modules()

        # Look for section headers (## ...) that indicate modules
        header_pattern = r'^##\s+([^\n]+?)(?:\s*\(([^)]*)\))?$'
        matches = re.findall(header_pattern, srs, re.MULTILINE)

        for title, desc in matches:
            module_name = title.strip()
            # Skip generic headings
            if not any(skip in module_name.lower() for skip in ['introduction', 'overview', 'references', 'appendix']):
                modules.append({
                    "name": module_name,
                    "description": desc.strip() if desc else module_name,
                    "file": module_name.lower().replace(" ", "_") + ".c"
                })

        # Look for software component patterns
        component_pattern = r'(?:module|component|driver|subsystem)[\s:]+([A-Za-z0-9_\s]+?)(?:\n|,|;)'
        components = re.findall(component_pattern, srs, re.IGNORECASE)

        for comp in components:
            comp_name = comp.strip()
            if comp_name and len(comp_name) < 50:  # Filter out overly long matches
                if not any(m["name"].lower() == comp_name.lower() for m in modules):
                    modules.append({
                        "name": comp_name,
                        "description": f"{comp_name} implementation",
                        "file": comp_name.lower().replace(" ", "_") + ".c"
                    })

        # Look for common module patterns
        if not modules:
            keywords = ['initialization', 'driver', 'control', 'interface', 'handler', 'manager', 'service']
            for keyword in keywords:
                if keyword in srs.lower():
                    modules.append({
                        "name": keyword.capitalize(),
                        "description": f"{keyword.capitalize()} module",
                        "file": keyword + ".c"
                    })

        return modules if modules else self._default_modules()

    async def _extract_interfaces(self, srs: str, glr: str) -> list:
        """Extract API interfaces from SRS by parsing function signatures and protocol names."""
        import re
        interfaces = []

        content = (srs or "") + "\n" + (glr or "")
        if not content:
            return self._default_interfaces()

        # Look for function signatures
        func_pattern = r'(\w+)\s*\(\s*([^)]*?)\s*\)'
        functions = re.findall(func_pattern, content)

        # Extract unique function names
        func_names = set()
        for func_name, params in functions:
            if func_name and not func_name.startswith('#') and len(func_name) > 2:
                func_names.add(func_name)

        # Group by protocol/type
        protocol_funcs = {}
        protocols = ['UART', 'SPI', 'I2C', 'CAN', 'USB', 'GPIO', 'ADC', 'PWM', 'DMA']

        for protocol in protocols:
            matching = [f for f in func_names if protocol.lower() in f.lower()]
            if matching:
                protocol_funcs[protocol] = matching

        # Build interface list
        for protocol, funcs in protocol_funcs.items():
            interfaces.append({
                "name": protocol,
                "type": "Hardware",
                "functions": ", ".join(sorted(list(funcs)[:5]))
            })

        # Add HAL/abstraction layer if any init/control functions found
        if any(f in ' '.join(func_names) for f in ['init', 'control', 'config']):
            if not any(i["name"] == "HAL" for i in interfaces):
                interfaces.insert(0, {
                    "name": "HAL",
                    "type": "Internal",
                    "functions": "hal_init(), hal_read(), hal_write(), hal_control()"
                })

        return interfaces if interfaces else self._default_interfaces()

    async def _extract_state_machines(self, srs: str) -> list:
        """Extract state machines from SRS by parsing state-related keywords."""
        import re
        state_machines = []

        if not srs:
            return self._default_state_machines()

        # Look for state machine mentions
        sm_pattern = r'(?:state\s+machine|state\s+diagram|states?)[:\s]+([^\n]+)'
        sm_matches = re.findall(sm_pattern, srs, re.IGNORECASE)

        # Extract state names
        state_pattern = r'\b(?:init|idle|running|active|sleep|waiting|error|fault|shutdown|standby)\b'
        states = list(set(re.findall(state_pattern, srs, re.IGNORECASE)))

        if sm_matches:
            for sm_desc in sm_matches[:3]:  # Limit to 3 state machines
                state_machines.append({
                    "name": sm_desc.strip()[:50],
                    "states": states if states else ["Init", "Idle", "Running", "Error"]
                })

        if not state_machines:
            # Fallback: create default state machine if any control/state keywords found
            if re.search(r'state|mode|status|condition', srs, re.IGNORECASE):
                state_machines.append({
                    "name": "Main Control State Machine",
                    "states": states if states else ["Init", "Idle", "Running", "Error"]
                })

        return state_machines if state_machines else self._default_state_machines()

    def _default_modules(self) -> list:
        """Default modules."""
        return [
            {"name": "Main", "description": "Main application loop", "file": "main.c"},
            {"name": "HAL", "description": "Hardware abstraction layer", "file": "hal.c"},
            {"name": "Drivers", "description": "Device drivers", "file": "drivers.c"},
            {"name": "Comms", "description": "Communication interface", "file": "comms.c"},
        ]

    def _default_interfaces(self) -> list:
        """Default interfaces."""
        return [
            {"name": "HAL", "type": "Internal", "functions": "hal_init(), hal_read(), hal_write()"},
            {"name": "UART", "type": "Hardware", "functions": "uart_init(), uart_send(), uart_recv()"},
            {"name": "SPI", "type": "Hardware", "functions": "spi_transfer()"},
        ]

    def _default_state_machines(self) -> list:
        """Default state machines."""
        return [
            {"name": "Main State Machine", "states": ["Init", "Idle", "Running", "Error"]}
        ]

    def _load_file(self, path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""
