"""
Tests for all phase agents - with proper call_llm mocking.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from agents.requirements_agent import RequirementsAgent
from agents.srs_agent import SRSAgent
from agents.sdd_agent import SDDAgent
from agents.netlist_agent import NetlistAgent
from agents.glr_agent import GLRAgent
from agents.code_agent import CodeAgent
from agents.document_agent import DocumentAgent
from agents.compliance_agent import ComplianceAgent


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_project_context(tmp_path):
    """Create a mock project context with output directory."""
    return {
        "project_id": 1,
        "name": "TestProject",
        "description": "A test hardware project",
        "design_type": "digital",
        "output_dir": str(tmp_path / "output"),
        "conversation_history": [],
        "design_parameters": {},
    }


@pytest.fixture
def mock_llm_text_response():
    """Create a mock LLM text response (no tools)."""
    return {
        "content": "# Test Output\n\nThis is test content.",
        "tool_calls": [],
        "model_used": "claude-opus-4-6",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


@pytest.fixture
def mock_llm_tool_response():
    """Create a mock LLM response with tool call."""
    tool_block = {
        "id": "tool-123",
        "name": "generate_requirements",
        "input": {
            "project_summary": "LED blinker project",
            "requirements": [
                {
                    "req_id": "REQ-HW-001",
                    "category": "functional",
                    "title": "LED Control",
                    "description": "Shall control LED",
                    "priority": "shall",
                    "verification_method": "test",
                }
            ],
            "design_parameters": {"voltage": "3.3V", "current": "20mA"},
            "block_diagram_mermaid": "graph TD\nMCU-->LED",
            "architecture_mermaid": "graph TD\nPower-->MCU",
            "component_recommendations": [
                {
                    "function": "Microcontroller",
                    "primary_part": "STM32F103",
                    "primary_manufacturer": "ST",
                    "primary_description": "ARM Cortex-M3 MCU",
                    "primary_key_specs": {"flash": "64KB"},
                    "alternatives": [],
                    "selection_rationale": "Cost effective",
                }
            ],
        },
    }

    return {
        "content": "Generating requirements...",
        "tool_calls": [tool_block],
        "model_used": "claude-opus-4-6",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


@pytest.fixture
def mock_llm_netlist_response():
    """Create a mock LLM response for netlist generation.

    Includes a synthetic supply connector (J_PWR) and bulk decap (C1) so
    the post-execute DRC pass clears overall_pass — the netlist agent
    now gates `phase_complete` on DRC.
    """
    tool_block = {
        "id": "tool-456",
        "name": "generate_netlist",
        "input": {
            "nodes": [
                {"instance_id": "U1", "part_number": "STM32F103", "component_name": "MCU", "reference_designator": "U1"},
                {"instance_id": "R1", "part_number": "RES_1K", "component_name": "Resistor", "reference_designator": "R1"},
                {"instance_id": "J_PWR", "part_number": "PWR_HEADER", "component_name": "Supply Connector", "reference_designator": "J_PWR"},
                {"instance_id": "C1", "part_number": "CAP_100N", "component_name": "Decoupling Cap", "reference_designator": "C1"},
            ],
            "edges": [
                {
                    "net_name": "LED_CTRL",
                    "from_instance": "U1",
                    "from_pin": "PA0",
                    "to_instance": "R1",
                    "to_pin": "1",
                    "signal_type": "digital",
                },
                {
                    "net_name": "VCC",
                    "from_instance": "J_PWR",
                    "from_pin": "1",
                    "to_instance": "C1",
                    "to_pin": "1",
                    "signal_type": "power",
                },
            ],
            "power_nets": ["VCC"],
            "ground_nets": ["GND"],
            "mermaid_diagram": "graph TD\nU1[MCU]-->R1[Resistor]",
            "validation_notes": [],
        },
    }

    return {
        "content": "Generating netlist...",
        "tool_calls": [tool_block],
        "model_used": "claude-opus-4-6",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


# =============================================================================
# RequirementsAgent Tests
# =============================================================================

class TestRequirementsAgent:
    """Test Phase 1 RequirementsAgent."""

    def test_init(self):
        """Test initialization."""
        agent = RequirementsAgent()
        assert agent.phase_number == "P1"
        assert agent.phase_name == "Requirements Capture"
        assert len(agent.tools) >= 1
        tool_names = [t["name"] for t in agent.tools]
        assert "generate_requirements" in tool_names

    def test_get_system_prompt(self, mock_project_context):
        """System prompt is project-contextual and anti-hallucination aware.

        Structural checks only — the exact wording of the prompt is tuned
        constantly, but these invariants must hold for every iteration:
          - Non-empty string of reasonable length
          - Contains the project name + design_type (context injection works)
          - Mentions TBD/TBC/TBA (the anti-hallucination rule is present)
        """
        agent = RequirementsAgent()
        prompt = agent.get_system_prompt(mock_project_context)
        assert isinstance(prompt, str) and len(prompt) > 500
        assert mock_project_context["design_type"] in prompt
        assert mock_project_context["name"] in prompt
        # Anti-hallucination rule must be on the prompt (Gotcha #9).
        lowered = prompt.lower()
        assert "tbd" in lowered or "tbc" in lowered or "tba" in lowered

    def test_build_requirements_md(self, mock_project_context):
        """Test requirements markdown generation."""
        agent = RequirementsAgent()
        tool_input = {
            "project_summary": "Test project",
            "requirements": [
                {
                    "req_id": "REQ-HW-001",
                    "category": "functional",
                    "title": "LED Control",
                    "description": "Shall control LED",
                    "priority": "shall",
                    "verification_method": "test",
                }
            ],
            "design_parameters": {"voltage": "3.3V"},
        }

        md = agent._build_requirements_md(tool_input, "TestProject")

        assert "# Hardware Requirements" in md
        assert "REQ-HW-001" in md
        assert "| Voltage | 3.3V |" in md

    def test_build_components_md(self, mock_project_context):
        """Test component recommendations markdown generation."""
        agent = RequirementsAgent()
        tool_input = {
            "component_recommendations": [
                {
                    "function": "Microcontroller",
                    "primary_part": "STM32F103",
                    "primary_manufacturer": "ST",
                    "primary_description": "ARM Cortex-M3",
                    "primary_key_specs": {"Flash": "64KB"},
                    "alternatives": [
                        {
                            "part_number": "ATmega328P",
                            "manufacturer": "Atmel",
                            "trade_off": "Lower cost, less memory",
                        }
                    ],
                    "selection_rationale": "Good balance of features",
                }
            ]
        }

        md = agent._build_components_md(tool_input, "TestProject")

        assert "# Component Recommendations" in md
        assert "STM32F103" in md
        assert "ATmega328P" in md
        assert "| Flash | 64KB |" in md

    @pytest.mark.asyncio
    async def test_execute_conversation(self, mock_project_context):
        """Test normal conversation flow with mocked call_llm."""
        agent = RequirementsAgent()

        # Mock call_llm directly
        async def mock_call_llm(*args, **kwargs):
            return {"content": "Tell me more about requirements", "tool_calls": [], "stop_reason": "end_turn"}

        with patch.object(agent, "call_llm", side_effect=mock_call_llm):
            result = await agent.execute(mock_project_context, "I need an LED blinker")

        assert result["response"] != ""
        assert result["phase_complete"] is False
        assert result["outputs"] == {}

    @pytest.mark.asyncio
    async def test_execute_with_tool_call(self, mock_project_context, mock_llm_tool_response):
        """Test execution with generate_requirements tool call."""
        agent = RequirementsAgent()

        async def mock_call_llm(*args, **kwargs):
            return mock_llm_tool_response

        with patch.object(agent, "call_llm", side_effect=mock_call_llm):
            result = await agent.execute(mock_project_context, "Generate requirements")

        assert result["phase_complete"] is True
        assert "outputs" in result
        assert "requirements.md" in result["outputs"]
        assert "block_diagram.md" in result["outputs"]


# =============================================================================
# SRSAgent Tests
# =============================================================================

class TestSRSAgent:
    """Test Phase 8a SRSAgent."""

    def test_init(self):
        """Test initialization."""
        agent = SRSAgent()
        assert agent.phase_number == "P8a"
        assert agent.phase_name == "SRS Generation"
        assert "IEEE 830" in agent.get_system_prompt({})

    @pytest.mark.asyncio
    async def test_execute(self, mock_project_context, mock_llm_text_response):
        """Test SRS generation with mocked call_llm."""
        agent = SRSAgent()

        async def mock_call_llm(*args, **kwargs):
            return mock_llm_text_response

        with patch.object(agent, "call_llm", side_effect=mock_call_llm):
            result = await agent.execute(mock_project_context, "Generate SRS")

        assert "response" in result


# =============================================================================
# SDDAgent Tests
# =============================================================================

class TestSDDAgent:
    """Test Phase 8b SDDAgent."""

    def test_init(self):
        """Test initialization."""
        agent = SDDAgent()
        assert agent.phase_number == "P8b"
        assert agent.phase_name == "SDD Generation"
        assert "IEEE 1016" in agent.get_system_prompt({})

    @pytest.mark.asyncio
    async def test_execute_without_srs(self, mock_project_context):
        """Test execution without SRS file."""
        agent = SDDAgent()

        result = await agent.execute(mock_project_context, "Generate SDD")

        assert result["phase_complete"] is False
        assert "SRS not found" in result["response"]

    @pytest.mark.asyncio
    async def test_execute_with_srs(self, mock_project_context, mock_llm_text_response):
        """Test execution with SRS file."""
        output_dir = Path(mock_project_context["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        (output_dir / f"SRS_{mock_project_context['name'].replace(' ', '_')}.md").write_text("# SRS\nSoftware requirements")

        agent = SDDAgent()

        async def mock_call_llm(*args, **kwargs):
            return mock_llm_text_response

        with patch.object(agent, "call_llm", side_effect=mock_call_llm):
            result = await agent.execute(mock_project_context, "Generate SDD")

        assert result["phase_complete"] is True


# =============================================================================
# NetlistAgent Tests
# =============================================================================

class TestNetlistAgent:
    """Test Phase 4 NetlistAgent."""

    def test_init(self):
        """Test initialization."""
        agent = NetlistAgent()
        assert agent.phase_number == "P4"
        assert agent.phase_name == "Netlist Generation"
        assert len(agent.tools) == 1
        assert agent.tools[0]["name"] == "generate_netlist"

    def test_validate_netlist(self):
        """Test netlist validation with NetworkX."""
        agent = NetlistAgent()

        data = {
            "nodes": [
                {"instance_id": "U1", "part_number": "STM32F103", "component_name": "MCU"},
                {"instance_id": "R1", "part_number": "10K", "component_name": "Resistor"},
            ],
            "edges": [
                {
                    "net_name": "SIGNAL",
                    "from_instance": "U1",
                    "from_pin": "PA0",
                    "to_instance": "R1",
                    "to_pin": "1",
                    "signal_type": "digital",
                }
            ],
        }

        result = agent._validate_netlist(data)

        assert "total_nodes" in result
        assert result["total_nodes"] == 2
        assert result["total_edges"] == 1

    def test_build_visual_md(self):
        """Test visual markdown generation."""
        agent = NetlistAgent()

        data = {
            "nodes": [
                {"instance_id": "U1", "part_number": "STM32F103", "component_name": "MCU"},
            ],
            "edges": [
                {
                    "net_name": "SIGNAL",
                    "from_instance": "U1",
                    "from_pin": "PA0",
                    "to_instance": "R1",
                    "to_pin": "1",
                    "signal_type": "digital",
                }
            ],
            "mermaid_diagram": "graph TD\nU1[R1]",
            "validation_notes": [],
        }

        md = agent._build_visual_md(data, "TestProject", data.get("mermaid_diagram", ""))

        assert "# Logical Netlist" in md
        assert "U1" in md
        assert "SIGNAL" in md

    @pytest.mark.asyncio
    async def test_execute_without_requirements(self, mock_project_context):
        """Test execution without requirements file."""
        agent = NetlistAgent()

        result = await agent.execute(mock_project_context, "Generate netlist")

        assert result["phase_complete"] is False
        assert "not found" in result["response"].lower()

    @pytest.mark.asyncio
    async def test_execute_with_files(self, mock_project_context, mock_llm_netlist_response):
        """NetlistAgent produces a netlist.json entry in the outputs dict.

        The agent no longer writes to disk itself — PipelineService does
        the file writes via StorageAdapter (single write path). So the
        contract this test should enforce is "outputs dict contains the
        expected keys with valid JSON content", not "file exists on disk".
        """
        output_dir = Path(mock_project_context["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "requirements.md").write_text("# Requirements\nTest")
        (output_dir / "component_recommendations.md").write_text("# Components\nTest")

        agent = NetlistAgent()

        async def mock_call_llm(*args, **kwargs):
            return mock_llm_netlist_response

        with patch.object(agent, "call_llm", side_effect=mock_call_llm):
            result = await agent.execute(mock_project_context, "Generate netlist")

        assert result["phase_complete"] is True
        assert "netlist.json" in result["outputs"]
        # The value is a JSON string — round-trip to confirm it's well-formed.
        import json as _json
        _json.loads(result["outputs"]["netlist.json"])


# =============================================================================
# GLRAgent Tests
# =============================================================================

class TestGLRAgent:
    """Test Phase 6 GLRAgent."""

    def test_init(self):
        """Test initialization."""
        agent = GLRAgent()
        assert agent.phase_number == "P6"
        assert agent.phase_name == "GLR Generation"
        assert "Glue Logic" in agent.get_system_prompt({})

    def test_get_system_prompt(self):
        """GLR prompt covers the document's core concepts.

        Structural checks — the exact section titles are reshuffled often
        as the defence-doc template evolves. We only lock in the invariants
        that any valid GLR prompt must preserve.
        """
        agent = GLRAgent()
        prompt = agent.get_system_prompt({})
        assert isinstance(prompt, str) and len(prompt) > 200
        lowered = prompt.lower()
        assert "glue logic" in lowered or "glr" in lowered
        assert "fpga" in lowered
        assert "\n##" in prompt or "\n#" in prompt  # has markdown headings

    def test_load_file(self, tmp_path):
        """Test _load_file helper method."""
        agent = GLRAgent()
        test_file = tmp_path / "test.txt"
        test_file.write_text("Test content", encoding="utf-8")

        content = agent._load_file(test_file)
        assert content == "Test content"

    def test_load_file_not_exists(self, tmp_path):
        """Test _load_file returns empty string for missing files."""
        agent = GLRAgent()
        content = agent._load_file(tmp_path / "nonexistent.txt")
        assert content == ""

    @pytest.mark.asyncio
    async def test_execute_success(self, mock_project_context, mock_llm_text_response):
        """Test successful GLR generation with input files."""
        output_dir = Path(mock_project_context["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "requirements.md").write_text("# Requirements\nTest")
        (output_dir / "netlist_visual.md").write_text("# Netlist\nTest")
        (output_dir / "netlist.json").write_text('{"nodes": [], "edges": []}')

        agent = GLRAgent()

        async def mock_call_llm(*args, **kwargs):
            return mock_llm_text_response

        with patch.object(agent, "call_llm", side_effect=mock_call_llm):
            result = await agent.execute(mock_project_context, "Generate GLR")

        assert result["phase_complete"] is True
        assert "glr_specification.md" in result["outputs"]
        assert (output_dir / "glr_specification.md").exists()

    @pytest.mark.asyncio
    async def test_execute_without_files(self, mock_project_context, mock_llm_text_response):
        """Test GLR generation works even with missing input files."""
        output_dir = Path(mock_project_context["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        agent = GLRAgent()

        async def mock_call_llm(*args, **kwargs):
            return mock_llm_text_response

        with patch.object(agent, "call_llm", side_effect=mock_call_llm):
            result = await agent.execute(mock_project_context, "Generate GLR")

        # Should still complete, just with empty context
        assert result["phase_complete"] is True


# =============================================================================
# CodeAgent Tests
# =============================================================================

class TestCodeAgent:
    """Test Phase 8c CodeAgent."""

    def test_init(self):
        """Test initialization."""
        agent = CodeAgent()
        assert agent.phase_number == "P8c"
        assert agent.phase_name == "Code Generation"
        assert "MISRA-C" in agent.get_system_prompt({})

    def test_get_system_prompt(self):
        """CodeAgent prompt drives firmware + review generation.

        Structural assertions only — specific sub-sections (Qt GUI was
        removed, driver layer restructured) move around. Core invariants:
        the prompt names the MISRA-C 2012 standard and a review artefact.
        """
        agent = CodeAgent()
        prompt = agent.get_system_prompt({})
        assert isinstance(prompt, str) and len(prompt) > 200
        assert "MISRA-C 2012" in prompt
        assert "Code Review" in prompt
        # Firmware generation target — either drivers or C code must be named.
        lowered = prompt.lower()
        assert "driver" in lowered or "firmware" in lowered or "embedded c" in lowered

    def test_load_file(self, tmp_path):
        """Test _load_file helper method."""
        agent = CodeAgent()
        test_file = tmp_path / "test.txt"
        test_file.write_text("Test content", encoding="utf-8")

        content = agent._load_file(test_file)
        assert content == "Test content"

    def test_load_file_not_exists(self, tmp_path):
        """Test _load_file returns empty string for missing files."""
        agent = CodeAgent()
        content = agent._load_file(tmp_path / "nonexistent.txt")
        assert content == ""

    @pytest.mark.asyncio
    async def test_execute_without_prerequisites(self, mock_project_context):
        """Test execution without SRS/SDD."""
        agent = CodeAgent()

        result = await agent.execute(mock_project_context, "Generate code")

        assert result["phase_complete"] is False
        assert "required" in result["response"].lower()

    @pytest.mark.asyncio
    async def test_execute_with_prerequisites(self, mock_project_context):
        """Test successful code generation with SRS/SDD files."""
        output_dir = Path(mock_project_context["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        project_name = mock_project_context["name"]

        (output_dir / f"SRS_{project_name.replace(' ', '_')}.md").write_text("# SRS\nSoftware requirements")
        (output_dir / f"SDD_{project_name.replace(' ', '_')}.md").write_text("# SDD\nSoftware design")

        agent = CodeAgent()

        result = await agent.execute(mock_project_context, "Generate code")

        assert result["phase_complete"] is True
        # Check that driver files were generated
        # List all output keys for debugging
        output_keys = list(result["outputs"].keys())
        # Just check there are some outputs
        assert len(result["outputs"]) > 0, f"No outputs generated. Keys: {output_keys}"
        assert "code_review_report.md" in result["outputs"]
        # Check files were saved
        assert (output_dir / "code_review_report.md").exists()

    @pytest.mark.asyncio
    async def test_execute_with_continuation(self, mock_project_context):
        """Test code generation creates proper file structure."""
        output_dir = Path(mock_project_context["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        project_name = mock_project_context["name"]

        (output_dir / f"SRS_{project_name.replace(' ', '_')}.md").write_text("# SRS\nRequirements")
        (output_dir / f"SDD_{project_name.replace(' ', '_')}.md").write_text("# SDD\nDesign")

        agent = CodeAgent()

        result = await agent.execute(mock_project_context, "Generate code")

        assert result["phase_complete"] is True
        # Check that outputs were generated
        assert len(result["outputs"]) > 0
        # Check review report exists
        assert "code_review_report.md" in result["outputs"]
        # Verify files were saved to disk
        assert (output_dir / "code_review_report.md").exists()
        # Check that driver files were created
        driver_files = [k for k in result["outputs"].keys() if "driver" in str(k).lower() or "hal" in str(k).lower()]
        assert len(driver_files) > 0, f"No driver files found in outputs: {list(result['outputs'].keys())}"

    def test_parse_and_save_files(self, mock_project_context):
        """Test parsing code blocks from LLM output."""
        agent = CodeAgent()
        output_dir = Path(mock_project_context["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        content = """Some text

```c
// File: driver.c
#include <stdio.h>
int main() { return 0; }
```

```cpp
// File: gui.cpp
void run() {}
```

More text
"""

        outputs = agent._parse_and_save_files(content, output_dir, "TestProject")

        assert "src/driver.c" in outputs
        assert "src/gui.cpp" in outputs
        assert (output_dir / "src" / "driver.c").exists()
        assert "int main" in outputs["src/driver.c"]

    def test_parse_and_save_files_sanitizes_paths(self, mock_project_context):
        """Test that file paths are sanitized."""
        agent = CodeAgent()
        output_dir = Path(mock_project_context["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        content = """```c
// File: ../malicious/path.c
bad code
```
"""

        outputs = agent._parse_and_save_files(content, output_dir, "TestProject")

        # Path separators should be replaced
        assert ".._malicious_path.c" in list(outputs.keys())[0]

    @pytest.mark.asyncio
    async def test_generate_review(self, mock_project_context):
        """Test _generate_review method."""
        agent = CodeAgent()

        review_response = {
            "content": "# Code Review\n\nScore: 90/100",
            "tool_calls": [],
            "model_used": "claude-opus-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }

        async def mock_call_llm(*args, **kwargs):
            return review_response

        with patch.object(agent, "call_llm", side_effect=mock_call_llm):
            result = await agent._generate_review("code content", "TestProject")

        assert "Code Review" in result


# =============================================================================
# DocumentAgent Tests
# =============================================================================

class TestDocumentAgent:
    """Test Phase 2 DocumentAgent."""

    def test_init(self):
        """Test initialization."""
        agent = DocumentAgent()
        assert agent.phase_number == "P2"
        assert agent.phase_name == "HRS Generation"
        assert "IEEE 29148" in agent.get_system_prompt({})

    def test_get_system_prompt(self):
        """Test system prompt contains required sections."""
        agent = DocumentAgent()
        prompt = agent.get_system_prompt({})
        assert "Introduction" in prompt
        assert "System Overview" in prompt
        assert "Hardware Requirements" in prompt
        assert "Design Constraints" in prompt
        assert "Traceability Matrix" in prompt

    def test_load_file(self, tmp_path):
        """Test _load_file helper method."""
        agent = DocumentAgent()
        test_file = tmp_path / "test.txt"
        test_file.write_text("Test content", encoding="utf-8")

        content = agent._load_file(test_file)
        assert content == "Test content"

    def test_load_file_not_exists(self, tmp_path):
        """Test _load_file returns empty string for missing files."""
        agent = DocumentAgent()
        content = agent._load_file(tmp_path / "nonexistent.txt")
        assert content == ""

    @pytest.mark.asyncio
    async def test_execute_without_prerequisites(self, mock_project_context):
        """Test execution without Phase 1 outputs."""
        output_dir = Path(mock_project_context["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        agent = DocumentAgent()
        result = await agent.execute(mock_project_context, "Generate HRS")

        assert result["phase_complete"] is False
        assert "not found" in result["response"].lower()

    @pytest.mark.asyncio
    async def test_execute_with_prerequisites(self, mock_project_context, mock_llm_text_response):
        """Test successful HRS generation with Phase 1 outputs."""
        output_dir = Path(mock_project_context["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        project_name = mock_project_context["name"]

        # Create Phase 1 outputs
        (output_dir / "requirements.md").write_text("# Requirements\nTest content")
        (output_dir / "block_diagram.md").write_text("```mermaid\ngraph TD\nA-->B\n```")
        (output_dir / "architecture.md").write_text("# Architecture\nTest")
        (output_dir / "component_recommendations.md").write_text("# Components\nTest")

        agent = DocumentAgent()

        async def mock_call_llm(*args, **kwargs):
            return mock_llm_text_response

        with patch.object(agent, "call_llm", side_effect=mock_call_llm):
            result = await agent.execute(mock_project_context, "Generate HRS")

        assert result["phase_complete"] is True
        hrs_key = [k for k in result["outputs"].keys() if k.startswith("HRS_")]
        assert len(hrs_key) > 0
        hrs_file = output_dir / f"HRS_{project_name.replace(' ', '_')}.md"
        assert hrs_file.exists()

    @pytest.mark.asyncio
    async def test_generate_hrs_with_continuation(self, mock_project_context):
        """Test HRS generation handles truncation."""
        output_dir = Path(mock_project_context["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        project_name = mock_project_context["name"]

        (output_dir / "requirements.md").write_text("# Requirements\nTest")

        agent = DocumentAgent()

        # Truncated response
        truncated_response = {
            "content": "# HRS Document\n\n## 1. Introduction\nPartial content...",
            "tool_calls": [],
            "model_used": "claude-opus-4-6",
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 10, "output_tokens": 8192},
        }

        # Continuation
        continuation_response = {
            "content": "\n## 2. System Overview\nRest of document",
            "tool_calls": [],
            "model_used": "claude-opus-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }

        call_count = [0]

        async def mock_call_llm(*args, **kwargs):
            call_count[0] += 1
            return truncated_response if call_count[0] == 1 else continuation_response

        with patch.object(agent, "call_llm", side_effect=mock_call_llm):
            result = await agent.execute(mock_project_context, "Generate HRS")

        assert result["phase_complete"] is True
        # Should have both parts
        assert "Introduction" in result["outputs"][f"HRS_{project_name.replace(' ', '_')}.md"]
        assert "System Overview" in result["outputs"][f"HRS_{project_name.replace(' ', '_')}.md"]


# =============================================================================
# ComplianceAgent Tests
# =============================================================================

class TestComplianceAgent:
    """Test Phase 3 ComplianceAgent."""

    def test_init(self):
        """Test initialization."""
        agent = ComplianceAgent()
        assert agent.phase_number == "P3"
        assert agent.phase_name == "Compliance Validation"
        assert "RoHS" in agent.get_system_prompt({})

    def test_get_system_prompt(self):
        """Test system prompt contains all required standards."""
        agent = ComplianceAgent()
        prompt = agent.get_system_prompt({})
        assert "RoHS" in prompt
        assert "REACH" in prompt
        assert "FCC Part 15" in prompt
        assert "CE Marking" in prompt
        assert "IEC 60601" in prompt  # Medical
        assert "ISO 26262" in prompt  # Automotive

    @pytest.mark.asyncio
    async def test_execute_without_components(self, mock_project_context):
        """Test execution without component recommendations."""
        output_dir = Path(mock_project_context["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        agent = ComplianceAgent()
        result = await agent.execute(mock_project_context, "Validate compliance")

        assert result["phase_complete"] is False
        assert "component data" in result["response"].lower() or "not found" in result["response"].lower()

    @pytest.mark.asyncio
    async def test_execute_with_components(self, mock_project_context):
        """Test successful compliance validation."""
        output_dir = Path(mock_project_context["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        # Create required input files
        (output_dir / "component_recommendations.md").write_text(
            "# Components\n| Part | Manufacturer |\n| STM32F103 | ST |"
        )
        (output_dir / "requirements.md").write_text("# Requirements\nDigital design")

        agent = ComplianceAgent()

        compliance_response = {
            "content": "# Compliance Report\n\n| Component | RoHS | REACH |\n| STM32F103 | PASS | PASS |",
            "tool_calls": [],
            "model_used": "claude-opus-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }

        async def mock_call_llm(*args, **kwargs):
            return compliance_response

        with patch.object(agent, "call_llm", side_effect=mock_call_llm):
            result = await agent.execute(mock_project_context, "Validate compliance")

        assert result["phase_complete"] is True
        assert "compliance_report.md" in result["outputs"]
        assert (output_dir / "compliance_report.md").exists()

    @pytest.mark.asyncio
    async def test_execute_without_requirements(self, mock_project_context):
        """Test execution works even without requirements file."""
        output_dir = Path(mock_project_context["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        # Only component file
        (output_dir / "component_recommendations.md").write_text(
            "# Components\n| Part | Manufacturer |\n| STM32F103 | ST |"
        )

        agent = ComplianceAgent()

        compliance_response = {
            "content": "# Compliance Report\n\nAll components compliant.",
            "tool_calls": [],
            "model_used": "claude-opus-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }

        async def mock_call_llm(*args, **kwargs):
            return compliance_response

        with patch.object(agent, "call_llm", side_effect=mock_call_llm):
            result = await agent.execute(mock_project_context, "Validate compliance")

        # Should still complete
        assert result["phase_complete"] is True
