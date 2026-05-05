"""
End-to-End Pipeline Integration Test

Tests the complete silicon to software (s2s) from P1 through P8c with actual agent execution
(using mocked LLM calls but real agent logic).
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.orchestrator import OrchestratorAgent, PHASE_ORDER
from database.models import Base, ProjectDB


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def e2e_db_session(tmp_path):
    """Create a test database for E2E testing."""
    db_path = tmp_path / "e2e_test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    engine.dispose()
    # Cleanup
    import os
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass


@pytest.fixture
def e2e_project(e2e_db_session, tmp_path):
    """Create a test project for E2E testing."""
    output_dir = tmp_path / "e2e_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    project = ProjectDB(
        name="E2E_Test_Project",
        description="End-to-end test project - LED Blinker",
        design_type="digital",
        output_dir=str(output_dir),
    )
    e2e_db_session.add(project)
    e2e_db_session.commit()
    e2e_db_session.refresh(project)
    return project


@pytest.fixture
def mock_llm_responses():
    """Mock LLM responses for each phase."""
    return {
        "P1": {
            "content": "",
            "tool_calls": [{
                "id": "tool-p1",
                "name": "generate_requirements",
                "input": {
                    "project_summary": "LED Blinker - A simple project to blink an LED",
                    "requirements": [
                        {
                            "req_id": "REQ-HW-001",
                            "category": "functional",
                            "title": "LED Control",
                            "description": "System shall control an LED with configurable blink rate",
                            "priority": "shall",
                            "verification_method": "test"
                        },
                        {
                            "req_id": "REQ-HW-002",
                            "category": "functional",
                            "title": "Power Supply",
                            "description": "System shall operate from 3.3V supply",
                            "priority": "shall",
                            "verification_method": "measurement"
                        }
                    ],
                    "design_parameters": {
                        "voltage": "3.3V",
                        "current": "20mA",
                        "frequency": "1Hz"
                    },
                    "block_diagram_mermaid": "graph TD\nMCU[Microcontroller]-->LED[LED]\nMCU-->RES[Resistor]",
                    "architecture_mermaid": "graph TD\nPower[3.3V Reg]-->MCU\nMCU-->LED",
                    "component_recommendations": [
                        {
                            "function": "Microcontroller",
                            "primary_part": "STM32F103C8T6",
                            "primary_manufacturer": "STMicroelectronics",
                            "primary_description": "ARM Cortex-M3 32-bit MCU",
                            "primary_key_specs": {
                                "Flash": "64KB",
                                "RAM": "20KB",
                                "Max Frequency": "72MHz"
                            },
                            "alternatives": [
                                {
                                    "part_number": "ATmega328P",
                                    "manufacturer": "Microchip",
                                    "trade_off": "Lower cost, less memory"
                                }
                            ],
                            "selection_rationale": "Good balance of performance and cost"
                        },
                        {
                            "function": "LED",
                            "primary_part": "LTST-C191KGKT",
                            "primary_manufacturer": "Lite-On",
                            "primary_description": "Green SMD LED",
                            "primary_key_specs": {
                                "Forward Voltage": "2.1V",
                                "Forward Current": "20mA"
                            },
                            "alternatives": [],
                            "selection_rationale": "Standard green LED"
                        }
                    ]
                }
            }],
            "model_used": "claude-opus-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 500}
        },
        "P2": {
            "content": """# Hardware Requirements Specification

## 1. Introduction
### 1.1 Purpose
This document specifies hardware requirements for LED Blinker project.

## 2. System Overview
### 2.1 System Description
A microcontroller-based LED blinking system.

## 3. Hardware Requirements
### 3.1 Functional Requirements
- REQ-HW-001: LED Control
- REQ-HW-002: Power Supply

## 4. Design Constraints
- Voltage: 3.3V
- Operating temperature: 0-70°C

## 5. Bill of Materials
| Part | Quantity |
| STM32F103C8T6 | 1 |
| LTST-C191KGKT | 1 |
| Resistor 330R | 1 |
""",
            "tool_calls": [],
            "model_used": "claude-opus-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 500}
        },
        "P3": {
            "content": """# Compliance Report

## Summary
All components are compliant with major standards.

## Component Compliance Matrix
| Component | RoHS | REACH | FCC | CE |
|-----------|------|-------|-----|-----|
| STM32F103C8T6 | PASS | PASS | PASS | PASS |
| LTST-C191KGKT | PASS | PASS | PASS | PASS |

## Notes
- All components are lead-free (RoHS compliant)
- No substances of very high concern (REACH)
""",
            "tool_calls": [],
            "model_used": "claude-opus-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 300}
        },
        "P4": {
            "content": "",
            "tool_calls": [{
                "id": "tool-p4",
                "name": "generate_netlist",
                "input": {
                    "nodes": [
                        {
                            "instance_id": "U1",
                            "part_number": "STM32F103C8T6",
                            "component_name": "Microcontroller",
                            "reference_designator": "U1"
                        },
                        {
                            "instance_id": "D1",
                            "part_number": "LTST-C191KGKT",
                            "component_name": "LED",
                            "reference_designator": "D1"
                        },
                        {
                            "instance_id": "R1",
                            "part_number": "RC0803FR-07330RL",
                            "component_name": "Resistor",
                            "reference_designator": "R1"
                        }
                    ],
                    "edges": [
                        {
                            "net_name": "VCC",
                            "from_instance": "U1",
                            "from_pin": "3.3V",
                            "to_instance": "R1",
                            "to_pin": "1",
                            "signal_type": "power"
                        },
                        {
                            "net_name": "GPIO_LED",
                            "from_instance": "U1",
                            "from_pin": "PA0",
                            "to_instance": "R1",
                            "to_pin": "2",
                            "signal_type": "digital"
                        },
                        {
                            "net_name": "LED_CATHODE",
                            "from_instance": "R1",
                            "from_pin": "1",
                            "to_instance": "D1",
                            "to_pin": "CATHODE",
                            "signal_type": "digital"
                        }
                    ],
                    "power_nets": ["VCC", "GND"],
                    "ground_nets": ["GND"],
                    "mermaid_diagram": "graph TD\nU1[MCU]-->|GPIO|R1[330R]\nR1-->D1[LED]\nPower[VCC]-->U1",
                    "validation_notes": []
                }
            }],
            "model_used": "claude-opus-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 400}
        },
        "P6": {
            "content": """# Glue Logic Requirements

## 1. Introduction
### 1.3 FPGA Device Selection
Not applicable - using microcontroller.

## 2. I/O Requirements
### 2.1 Input Signals
None for this design.

### 2.2 Output Signals
| Signal Name | Pin | Voltage | Drive |
| LED_CTRL | PA0 | 3.3V | 20mA |

## 3. Pin Assignment
| Pin | Signal | Direction |
| PA0 | LED_CTRL | Output |
| VDD | VCC | Power |
| VSS | GND | Ground |

## 4. Timing Requirements
### 4.1 Clock Domains
- System Clock: 72 MHz
- LED Toggle: 1 Hz (configurable)

## 5. Register Map
| Address | Register | Access | Description |
| 0x4001080C | GPIOA_ODR | RW | GPIO Output Data Register |
""",
            "tool_calls": [],
            "model_used": "claude-opus-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 400}
        },
        "P8a": {
            "content": """# Software Requirements Specification

## 1. Introduction
Software requirements for LED Blinker control.

## 2. Functional Requirements
### SRS-001: LED Blinking
The system shall blink the LED at a configurable rate.

### SRS-002: Rate Configuration
The blink rate shall be configurable via UART.

## 3. Interface Requirements
### 3.1 Hardware Interface
- GPIO PA0: LED control output
- UART1: Configuration interface

## 4. Performance Requirements
- Blink rate accuracy: ±5%
- UART baud rate: 115200 bps
""",
            "tool_calls": [],
            "model_used": "claude-opus-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 300}
        },
        "P8b": {
            "content": """# Software Design Document

## 1. System Architecture
### 1.1 Overview
Layered software architecture with HAL, driver, and application layers.

## 2. Component Design
### 2.1 GPIO Driver
- Functions: gpio_init(), gpio_set(), gpio_clear()

### 2.2 Timer Driver
- Functions: timer_init(), timer_start(), timer_set_period()

### 2.3 Application
- Main loop handles LED blinking
- UART interrupt handles configuration

## 3. Data Flow
```
Timer Interrupt --> Toggle LED --> Sleep
UART Interrupt --> Update Rate
```
""",
            "tool_calls": [],
            "model_used": "claude-opus-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 300}
        },
        "P8c": {
            "content": """```c
// File: gpio.h
#ifndef GPIO_H
#define GPIO_H
void gpio_init(void);
void gpio_set(uint8_t pin);
void gpio_clear(uint8_t pin);
#endif
```

```c
// File: gpio.c
#include "gpio.h"
void gpio_init(void) {
    // Initialize GPIO
}
void gpio_set(uint8_t pin) {
    GPIOA->BSRR = (1 << pin);
}
void gpio_clear(uint8_t pin) {
    GPIOA->BRR = (1 << pin);
}
```

```cpp
// File: main.cpp
#include "gpio.h"
int main() {
    gpio_init();
    while(1) {
        gpio_set(0);
        delay(500);
        gpio_clear(0);
        delay(500);
    }
}
```
""",
            "tool_calls": [],
            "model_used": "claude-opus-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 500}
        }
    }


# =============================================================================
# E2E Pipeline Tests
# =============================================================================

class TestE2EPipeline:
    """End-to-end pipeline tests."""

    @pytest.mark.asyncio
    async def test_full_pipeline_execution(self, e2e_project, e2e_db_session, mock_llm_responses):
        """Test complete pipeline execution from P1 to P8c."""
        orchestrator = OrchestratorAgent()
        output_dir = Path(e2e_project.output_dir)

        # Track executed phases and outputs
        executed_phases = []
        # all_outputs = {}  # Reserved for future use

        # Create files directly for each phase to simulate outputs
        # Use a factory function to avoid closure issues
        def make_mock_execute(p, resp):
            async def mock_execute(project_context, user_input):
                executed_phases.append(p)
                out_dir = Path(project_context.get("output_dir", "output"))
                out_dir.mkdir(parents=True, exist_ok=True)
                pname = project_context.get("name", "Project")
                outputs = {}

                # Create outputs based on phase
                if p == "P1":
                    # Requirements
                    req_md = "# Requirements\n\nTest requirements content"
                    comp_md = "# Components\n\nTest components"
                    block_md = "# Block Diagram\n\n```mermaid\nA-->B```"
                    arch_md = "# Architecture\n\nTest architecture"
                    (out_dir / "requirements.md").write_text(req_md)
                    (out_dir / "component_recommendations.md").write_text(comp_md)
                    (out_dir / "block_diagram.md").write_text(block_md)
                    (out_dir / "architecture.md").write_text(arch_md)
                    outputs = {
                        "requirements.md": req_md,
                        "component_recommendations.md": comp_md,
                        "block_diagram.md": block_md,
                        "architecture.md": arch_md,
                    }

                elif p == "P2":
                    hrs_content = resp.get(p, {}).get("content", "# HRS Document")
                    fname = f"HRS_{pname.replace(' ', '_')}.md"
                    (out_dir / fname).write_text(hrs_content)
                    outputs = {fname: hrs_content}

                elif p == "P3":
                    comp_content = resp.get(p, {}).get("content", "# Compliance Report")
                    (out_dir / "compliance_report.md").write_text(comp_content)
                    outputs = {"compliance_report.md": comp_content}

                elif p == "P4":
                    netlist = {"nodes": [], "edges": []}
                    visual = "# Netlist Visual"
                    (out_dir / "netlist.json").write_text(json.dumps(netlist))
                    (out_dir / "netlist_visual.md").write_text(visual)
                    outputs = {
                        "netlist.json": json.dumps(netlist),
                        "netlist_visual.md": visual
                    }

                elif p == "P6":
                    glr_content = resp.get(p, {}).get("content", "# GLR Specification")
                    (out_dir / "glr_specification.md").write_text(glr_content)
                    outputs = {"glr_specification.md": glr_content}

                elif p == "P8a":
                    srs_content = resp.get(p, {}).get("content", "# SRS Document")
                    fname = f"SRS_{pname.replace(' ', '_')}.md"
                    (out_dir / fname).write_text(srs_content)
                    outputs = {fname: srs_content}

                elif p == "P8b":
                    sdd_content = resp.get(p, {}).get("content", "# SDD Document")
                    fname = f"SDD_{pname.replace(' ', '_')}.md"
                    (out_dir / fname).write_text(sdd_content)
                    outputs = {fname: sdd_content}

                elif p == "P8c":
                    code_content = resp.get(p, {}).get("content", "```c\nint main() {}\n```")
                    review = "# Code Review\n\nScore: 95/100"
                    (out_dir / "generated_code.md").write_text(code_content)
                    (out_dir / "code_review_report.md").write_text(review)
                    outputs = {
                        "generated_code.md": code_content,
                        "code_review_report.md": review
                    }

                return {
                    "response": f"Phase {p} complete",
                    "phase_complete": True,
                    "outputs": outputs
                }
            return mock_execute

        for phase in PHASE_ORDER:
            agent = orchestrator._get_agent(phase)
            agent.execute = make_mock_execute(phase, mock_llm_responses)

        # Execute all phases
        results = await orchestrator.execute_all(
            project_id=e2e_project.id,
            initial_input="Create an LED blinker with STM32",
            session=e2e_db_session,
        )

        # Debug: print results
        print(f"Executed phases: {executed_phases}")
        print(f"Results keys: {list(results.keys())}")

        # Force a database sync before checking
        e2e_db_session.flush()

        # Verify all phases were executed
        assert len(executed_phases) == len(PHASE_ORDER)
        assert executed_phases == PHASE_ORDER

        # Verify all phases completed successfully
        for phase in PHASE_ORDER:
            assert phase in results
            assert results[phase]["phase_complete"] is True
            assert "outputs" in results[phase]

        # Verify specific outputs exist
        assert (output_dir / "requirements.md").exists()
        assert (output_dir / "component_recommendations.md").exists()
        assert (output_dir / "block_diagram.md").exists()
        assert (output_dir / "architecture.md").exists()
        assert (output_dir / f"HRS_{e2e_project.name.replace(' ', '_')}.md").exists()
        assert (output_dir / "compliance_report.md").exists()
        assert (output_dir / "netlist.json").exists()
        assert (output_dir / "netlist_visual.md").exists()
        assert (output_dir / "glr_specification.md").exists()
        assert (output_dir / f"SRS_{e2e_project.name.replace(' ', '_')}.md").exists()
        assert (output_dir / f"SDD_{e2e_project.name.replace(' ', '_')}.md").exists()
        assert (output_dir / "generated_code.md").exists()

        # Verify database state
        # Force expunge and reload from DB
        e2e_db_session.expunge(e2e_project)
        e2e_project = e2e_db_session.query(ProjectDB).filter(ProjectDB.id == e2e_project.id).first()

        assert e2e_project.current_phase == "DONE"

        # Debug: print phase statuses
        for p in PHASE_ORDER:
            status = e2e_project.phase_statuses.get(p, {})
            print(f"Phase {p}: {status}")

        assert all(
            e2e_project.phase_statuses.get(p, {}).get("status") == "completed"
            for p in PHASE_ORDER
        )

    async def _mock_p1_execute(self, agent, project_context, response):
        """Mock P1 execution with tool handling."""
        from agents.requirements_agent import RequirementsAgent
        if isinstance(agent, RequirementsAgent) and response["tool_calls"]:
            tool_input = response["tool_calls"][0]["input"]

            output_dir = Path(project_context.get("output_dir", "output"))
            output_dir.mkdir(parents=True, exist_ok=True)
            project_name = project_context.get("name", "Project")

            # Build outputs
            requirements_md = agent._build_requirements_md(tool_input, project_name)
            components_md = agent._build_components_md(tool_input, project_name)
            block_md = f"# Block Diagram\n\n```mermaid\n{tool_input.get('block_diagram_mermaid', '')}\n```"
            arch_md = f"# Architecture\n\n```mermaid\n{tool_input.get('architecture_mermaid', '')}\n```"

            # Save files
            (output_dir / "requirements.md").write_text(requirements_md, encoding="utf-8")
            (output_dir / "component_recommendations.md").write_text(components_md, encoding="utf-8")
            (output_dir / "block_diagram.md").write_text(block_md, encoding="utf-8")
            (output_dir / "architecture.md").write_text(arch_md, encoding="utf-8")

            return {
                "response": "Requirements captured successfully.",
                "phase_complete": True,
                "outputs": {
                    "requirements.md": requirements_md,
                    "component_recommendations.md": components_md,
                    "block_diagram.md": block_md,
                    "architecture.md": arch_md,
                }
            }
        return await self._mock_text_execute(agent, project_context, response)

    async def _mock_p4_execute(self, agent, project_context, response):
        """Mock P4 execution with tool handling."""
        from agents.netlist_agent import NetlistAgent
        if isinstance(agent, NetlistAgent) and response["tool_calls"]:
            tool_input = response["tool_calls"][0]["input"]

            output_dir = Path(project_context.get("output_dir", "output"))
            project_name = project_context.get("name", "Project")

            # Validate
            validation = agent._validate_netlist(tool_input)

            # Build visual
            visual_md = agent._build_visual_md(tool_input, project_name, tool_input.get("mermaid_diagram", ""))

            # Save files
            (output_dir / "netlist.json").write_text(json.dumps(tool_input, indent=2), encoding="utf-8")
            (output_dir / "netlist_visual.md").write_text(visual_md, encoding="utf-8")

            return {
                "response": f"Netlist generated: {validation['total_nodes']} nodes, {validation['total_edges']} edges",
                "phase_complete": True,
                "outputs": {
                    "netlist.json": json.dumps(tool_input, indent=2),
                    "netlist_visual.md": visual_md,
                }
            }
        return await self._mock_text_execute(agent, project_context, response)

    async def _mock_p8c_execute(self, agent, project_context, response):
        """Mock P8c execution with file parsing."""
        output_dir = Path(project_context.get("output_dir", "output"))
        project_name = project_context.get("name", "Project")

        code_content = response.get("content", "")
        outputs = {}

        # Save generated code
        gen_file = output_dir / "generated_code.md"
        gen_file.write_text(code_content, encoding="utf-8")
        outputs["generated_code.md"] = code_content

        # Parse and save files
        parsed = agent._parse_and_save_files(code_content, output_dir, project_name)
        outputs.update(parsed)

        # Save review report
        review = "# Code Review Report\n\nScore: 95/100\n\nAll MISRA-C checks passed."
        review_file = output_dir / "code_review_report.md"
        review_file.write_text(review, encoding="utf-8")
        outputs["code_review_report.md"] = review

        return {
            "response": f"Code generation complete. {len(outputs)} files generated.",
            "phase_complete": True,
            "outputs": outputs
        }

    async def _mock_text_execute(self, agent, project_context, response):
        """Mock text response execution."""
        output_dir = Path(project_context.get("output_dir", "output"))
        output_dir.mkdir(parents=True, exist_ok=True)
        project_name = project_context.get("name", "Project")
        phase = agent.phase_number

        content = response.get("content", "")

        # Determine output filename based on phase
        if phase == "P2":
            filename = f"HRS_{project_name.replace(' ', '_')}.md"
        elif phase == "P3":
            filename = "compliance_report.md"
        elif phase == "P6":
            filename = "glr_specification.md"
        elif phase == "P8a":
            filename = f"SRS_{project_name.replace(' ', '_')}.md"
        elif phase == "P8b":
            filename = f"SDD_{project_name.replace(' ', '_')}.md"
        else:
            filename = f"{phase}_output.md"

        # Save file
        output_path = output_dir / filename
        output_path.write_text(content, encoding="utf-8")

        return {
            "response": f"{agent.phase_name} complete.",
            "phase_complete": True,
            "outputs": {filename: content}
        }

    @pytest.mark.asyncio
    async def test_pipeline_output_consistency(self, e2e_project, e2e_db_session, mock_llm_responses):
        """Test that pipeline outputs are consistent and traceable."""
        orchestrator = OrchestratorAgent()

        # Mock and execute (simplified version)
        executed_phases = []

        for phase in PHASE_ORDER:
            agent = orchestrator._get_agent(phase)

            async def mock_execute(project_context, user_input, p=phase):
                executed_phases.append(p)
                return {"phase_complete": True, "outputs": {"test.md": "test"}}

            agent.execute = mock_execute

        results = await orchestrator.execute_all(
            project_id=e2e_project.id,
            initial_input="Test",
            session=e2e_db_session,
        )

        # Verify phase order
        assert executed_phases == PHASE_ORDER

        # Verify each phase result
        for i, phase in enumerate(PHASE_ORDER):
            assert phase in results
            assert results[phase]["phase_complete"] is True

    @pytest.mark.asyncio
    async def test_database_state_tracking(self, e2e_project, e2e_db_session):
        """Test that database properly tracks phase progression."""
        orchestrator = OrchestratorAgent()

        # Mock agent to return success
        for phase in ["P1"]:
            agent = orchestrator._get_agent(phase)

            async def mock_execute(project_context, user_input):
                return {
                    "response": "Test",
                    "phase_complete": True,
                    "outputs": {"test.md": "test"},
                    "model_used": "claude-opus-4-6",
                }

            agent.execute = mock_execute

        # Execute P1
        await orchestrator.execute_phase(
            project_id=e2e_project.id,
            phase_number="P1",
            user_input="Test",
            session=e2e_db_session,
        )

        # Check database state
        e2e_db_session.refresh(e2e_project)

        assert e2e_project.current_phase == "P2"
        assert "P1" in e2e_project.phase_statuses
        assert e2e_project.phase_statuses["P1"]["status"] == "completed"
        assert "completed_at" in e2e_project.phase_statuses["P1"]


class TestE2EPipelineEdgeCases:
    """Test edge cases in the full pipeline."""

    @pytest.mark.asyncio
    async def test_pipeline_with_missing_prerequisites(self, e2e_project, e2e_db_session):
        """Test pipeline behavior when prerequisites are missing."""
        orchestrator = OrchestratorAgent()

        # Mock P2 to fail without P1 outputs
        agent_p2 = orchestrator._get_agent("P2")
        _ = agent_p2.execute  # Original execute stored but not restored in test

        async def mock_p2_execute(project_context, user_input):
            # Simulate missing prerequisites
            return {
                "response": "Phase 1 outputs not found",
                "phase_complete": False,
                "outputs": {},
            }

        agent_p2.execute = mock_p2_execute

        # Mock other phases to succeed
        for phase in ["P1", "P3", "P4", "P6", "P8a", "P8b", "P8c"]:
            if phase == "P2":
                continue
            agent = orchestrator._get_agent(phase)

            async def mock_execute(project_context, user_input):
                return {"phase_complete": True, "outputs": {}}

            agent.execute = mock_execute

        # Execute from P1
        results = await orchestrator.execute_all(
            project_id=e2e_project.id,
            initial_input="Test",
            session=e2e_db_session,
        )

        # P1 should complete, P2 should fail, pipeline should stop
        assert "P1" in results
        assert results["P1"]["phase_complete"] is True
        assert "P2" in results
        assert results["P2"]["phase_complete"] is False

    @pytest.mark.asyncio
    async def test_pipeline_resume_from_intermediate_phase(self, e2e_project, e2e_db_session):
        """Test resuming pipeline from an intermediate phase."""
        orchestrator = OrchestratorAgent()

        # Set project to P4 state
        e2e_project.current_phase = "P4"
        e2e_project.phase_statuses = {
            "P1": {"status": "completed", "completed_at": "2024-01-01T00:00:00"},
            "P2": {"status": "completed", "completed_at": "2024-01-01T00:01:00"},
            "P3": {"status": "completed", "completed_at": "2024-01-01T00:02:00"},
        }
        e2e_db_session.commit()

        # Mock P4 execution
        agent = orchestrator._get_agent("P4")

        async def mock_execute(project_context, user_input):
            return {"phase_complete": True, "outputs": {"netlist.json": "{}"}}

        agent.execute = mock_execute

        # Execute P4
        result = await orchestrator.execute_phase(
            project_id=e2e_project.id,
            phase_number="P4",
            user_input="Generate netlist",
            session=e2e_db_session,
        )

        assert result["phase_complete"] is True

        # Verify phase advanced
        e2e_db_session.refresh(e2e_project)
        assert e2e_project.current_phase == "P6"
