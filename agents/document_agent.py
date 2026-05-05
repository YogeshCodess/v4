"""
Phase 2: HRS Document Generation Agent (IEEE 29148 Compliant)

Generates a 50-100 page Hardware Requirements Specification in markdown format.
Uses IEEE 29148:2018 section structure with requirement traceability.
"""

import asyncio
import json
import logging
from pathlib import Path

from agents.base_agent import BaseAgent
from config import settings
from generators.hrs_generator import HRSGenerator

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior hardware documentation engineer producing an IEEE 29148:2018-compliant Hardware Requirements Specification (HRS).

You will be given:
- Project requirements (with REQ-HW-xxx IDs)
- Component recommendations
- Design parameters
- Block diagram and architecture descriptions

## YOUR TASK:
Generate a COMPLETE, DETAILED Hardware Requirements Specification following this IEEE 29148 structure:

### DOCUMENT STRUCTURE (YOU MUST FOLLOW THIS EXACTLY):

# 1. Introduction
## 1.1 Purpose
## 1.2 Scope
## 1.3 Definitions, Acronyms, and Abbreviations
## 1.4 References
## 1.5 Overview

# 2. System Overview
## 2.1 System Description
## 2.2 System Block Diagram
## 2.3 System Architecture
## 2.4 Operating Environment

# 3. Hardware Requirements
## 3.1 Functional Requirements
## 3.2 Performance Requirements
## 3.3 Interface Requirements
### 3.3.1 External Interfaces
### 3.3.2 Internal Interfaces
### 3.3.3 Communication Interfaces
## 3.4 Environmental Requirements
## 3.5 Power Requirements
## 3.6 Physical Requirements

# 4. Design Constraints
## 4.1 Standards Compliance
## 4.2 Component Constraints
## 4.3 Manufacturing Constraints

# 5. Verification Requirements
## 5.1 Test Requirements
## 5.2 Analysis Requirements
## 5.3 Inspection Requirements

# 6. Bill of Materials (Preliminary)

# 7. Traceability Matrix

## RULES:
- Every requirement MUST have an ID (REQ-HW-xxx) and be traceable
- Include Mermaid diagrams where appropriate (block diagrams, timing, data flow)
- Be DETAILED and SPECIFIC - this is a production document, not a summary
- Include actual calculations (power budget, thermal analysis) where relevant
- Reference specific component part numbers from the recommendations
- Use tables for structured data (BOM, pin assignments, power budget)
- Target 50-100 pages of content (be thorough)
- Do NOT add any boilerplate approval/review disclaimers such as
  "This document shall be reviewed and approved by..." — omit all such lines
- Do NOT include a "Status Legend" or "Legend" section — it is template boilerplate.
  Instead, add a single metadata line at the document start: **Document Status: AI-GENERATED**
- Do NOT use placeholder values TBD, TBA, or TBC anywhere. Derive specific values from
  the provided component data, use engineering calculations, or state a justified assumption
  inline (e.g., "assumed 100 mA based on STM32 datasheet typical"). Every field must have
  a concrete value.
"""


class DocumentAgent(BaseAgent):
    """Phase 2: IEEE 29148-compliant HRS generation."""

    def __init__(self):
        super().__init__(
            phase_number="P2",
            phase_name="HRS Generation",
            model=settings.primary_model,  # Use primary model for quality document generation
            max_tokens=16384,  # Max for HRS — section-by-section generation
        )
        self.hrs_generator = HRSGenerator()

    def get_system_prompt(self, project_context: dict) -> str:
        return SYSTEM_PROMPT

    async def execute(self, project_context: dict, user_input: str) -> dict:
        output_dir = Path(project_context.get("output_dir", "output"))
        output_dir.mkdir(parents=True, exist_ok=True)
        project_name = project_context.get("name", "Project")

        # Load Phase 1 outputs
        requirements_content = self._load_file(output_dir / "requirements.md")
        block_diagram = self._load_file(output_dir / "block_diagram.md")
        architecture = self._load_file(output_dir / "architecture.md")
        components = self._load_file(output_dir / "component_recommendations.md")

        if not requirements_content:
            return {
                "response": "Phase 1 outputs not found. Please complete Requirements Capture first.",
                "phase_complete": False,
                "outputs": {},
            }

        # PRIMARY PATH: LLM writes the full IEEE 29148 document from P1 context
        user_message = (
            f"Generate a complete IEEE 29148:2018 Hardware Requirements Specification for:\n\n"
            f"**Project:** {project_name}\n\n"
            f"## Phase 1 Requirements\n{requirements_content[:8000]}\n\n"
            f"## Block Diagram\n{block_diagram[:3000] if block_diagram else 'Not captured.'}\n\n"
            f"## System Architecture\n{architecture[:3000] if architecture else 'Not captured.'}\n\n"
            f"## Component Recommendations\n{components[:5000] if components else 'Not captured.'}\n\n"
            "Generate ALL sections per the IEEE 29148 structure in your system prompt. "
            "Be thorough and project-specific. Include real power calculations, interface tables, "
            "and Mermaid diagrams. Do NOT skip any section."
        )

        hrs_content = ""
        try:
            hrs_content = await self._generate_hrs(user_message, project_name)
        except Exception as e:
            self.log(f"LLM HRS generation failed: {e} — falling back to template", "warning")

        # FALLBACK: template generator if LLM failed or returned too little
        # Section-by-section generation produces several thousand chars minimum; < 2000 means most sections failed
        if not hrs_content or len(hrs_content) < 2000:
            structured_requirements = await self._extract_requirements(requirements_content, project_name)
            component_data = await self._extract_components(components)
            metadata = {
                "version": project_context.get("version", "1.0"),
                "author": project_context.get("author", "Silicon to Software (S2S) AI"),
                "input_voltage": project_context.get("design_parameters", {}).get("input_voltage", "12-24"),
                "max_power": project_context.get("design_parameters", {}).get("max_power", "per design spec"),
                "temp_min": project_context.get("design_parameters", {}).get("temp_min", "-40"),
                "temp_max": project_context.get("design_parameters", {}).get("temp_max", "+85"),
            }
            hrs_content = self.hrs_generator.generate(
                project_name=project_name,
                requirements=structured_requirements,
                component_data=component_data,
                metadata=metadata,
            )

        # Strip boilerplate review/approval disclaimer lines the LLM sometimes adds
        import re as _re
        hrs_content = _re.sub(
            r'\*?This (?:document|HRS|specification) shall be reviewed and approved[^\n]*\n?',
            '',
            hrs_content,
            flags=_re.IGNORECASE,
        ).strip()

        # Strip any "Status Legend" section (template boilerplate the LLM occasionally adds)
        # Removes the heading + all lines until the next heading or end-of-section marker
        hrs_content = _re.sub(
            r'#{1,4}\s*Status\s+Legend\b.*?(?=\n#{1,4}\s|\n---|\Z)',
            '',
            hrs_content,
            flags=_re.IGNORECASE | _re.DOTALL,
        ).strip()

        # Replace any remaining TBD/TBA/TBC placeholders
        hrs_content = _re.sub(
            r'\b(TBD|TBA|TBC)\b',
            '[specify]',
            hrs_content,
            flags=_re.IGNORECASE,
        )

        # P26 #17 (2026-04-26): coerce + re-render every embedded
        # `mermaid` block so LLM-emitted bracket mismatches (e.g.
        # `["..."]}`), nested `[...]` inside quoted labels, or stray
        # glyphs don't ship to disk and break the in-browser preview.
        # Real bug from project rx_band:
        #   L257: `MIX2["..."]}` (extra `}`)
        #   L1540: `LDO5C["+5V_CH[1:4]..."]` (nested brackets)
        try:
            from tools.mermaid_coerce import sanitize_mermaid_blocks_in_markdown
            hrs_content = sanitize_mermaid_blocks_in_markdown(hrs_content)
        except Exception as _exc:
            self.log(f"HRS mermaid sanitise skipped: {_exc}", "warning")

        # Save output
        hrs_file = self.hrs_generator.save(hrs_content, output_dir, project_name)
        self.log(f"HRS generated: {len(hrs_content)} chars -> {hrs_file}")

        return {
            "response": f"HRS document generated ({len(hrs_content)} characters).",
            "phase_complete": True,
            "outputs": {hrs_file.name: hrs_content},
        }

    async def _generate_hrs(self, user_message: str, project_name: str) -> str:
        """Generate HRS section-by-section to avoid token-limit truncation.

        Each major IEEE 29148 section is generated in its own LLM call.
        Sections are concatenated to produce a complete, untruncated document.
        """
        system = self.get_system_prompt({})

        # Context block injected into every section call
        context_block = user_message

        sections = [
            (
                "Section 1 — Introduction",
                (
                    f"{context_block}\n\n"
                    "Write ONLY Section 1 of the HRS:\n"
                    "# 1. Introduction\n"
                    "## 1.1 Purpose\n## 1.2 Scope\n## 1.3 Definitions, Acronyms, and Abbreviations\n"
                    "## 1.4 References\n## 1.5 Overview\n\n"
                    "Be specific to the project. Include a full definitions table. 2-4 pages."
                ),
            ),
            (
                "Section 2 — System Overview",
                (
                    f"{context_block}\n\n"
                    "Write ONLY Section 2 of the HRS:\n"
                    "# 2. System Overview\n"
                    "## 2.1 System Description\n## 2.2 System Block Diagram\n"
                    "## 2.3 System Architecture\n## 2.4 Operating Environment\n\n"
                    "Include a Mermaid block diagram. Describe the full system architecture in detail. 4-6 pages."
                ),
            ),
            (
                "Section 3a — Functional & Performance Requirements",
                (
                    f"{context_block}\n\n"
                    "Write ONLY Section 3.1 and 3.2 of the HRS:\n"
                    "## 3.1 Functional Requirements\n"
                    "## 3.2 Performance Requirements\n\n"
                    "Each requirement MUST have a unique REQ-HW-xxx ID, description, rationale, and priority. "
                    "List at least 15-20 functional requirements and 8-10 performance requirements with "
                    "specific measurable values. Use markdown tables where helpful."
                ),
            ),
            (
                "Section 3b — Interface, Environmental, Power & Physical Requirements",
                (
                    f"{context_block}\n\n"
                    "Write ONLY Sections 3.3 through 3.6 of the HRS:\n"
                    "## 3.3 Interface Requirements\n### 3.3.1 External Interfaces\n"
                    "### 3.3.2 Internal Interfaces\n### 3.3.3 Communication Interfaces\n"
                    "## 3.4 Environmental Requirements\n"
                    "## 3.5 Power Requirements\n"
                    "## 3.6 Physical Requirements\n\n"
                    "Include detailed pin tables for interfaces. Include power budget table with each rail "
                    "(voltage, typical current, max current, power). Include thermal requirements. "
                    "All requirements must have REQ-HW-xxx IDs."
                ),
            ),
            (
                "Section 4 — Design Constraints",
                (
                    f"{context_block}\n\n"
                    "Write ONLY Section 4 of the HRS:\n"
                    "# 4. Design Constraints\n"
                    "## 4.1 Standards Compliance\n## 4.2 Component Constraints\n## 4.3 Manufacturing Constraints\n\n"
                    "List specific standards (IPC-2221, IPC-7711, RoHS, REACH, FCC, CE, UL). "
                    "Include component sourcing constraints, lifecycle considerations. 3-4 pages."
                ),
            ),
            (
                "Section 5 — Verification Requirements",
                (
                    f"{context_block}\n\n"
                    "Write ONLY Section 5 of the HRS:\n"
                    "# 5. Verification Requirements\n"
                    "## 5.1 Test Requirements\n## 5.2 Analysis Requirements\n## 5.3 Inspection Requirements\n\n"
                    "Include a detailed test plan with test cases for key requirements. "
                    "Specify which requirements are verified by test, analysis, or inspection. "
                    "Use a table: REQ-ID | Test Method | Pass Criteria | Priority. 5-8 pages."
                ),
            ),
            (
                "Section 6 — Bill of Materials",
                (
                    f"{context_block}\n\n"
                    "Write ONLY Section 6 (Preliminary BOM) of the HRS:\n"
                    "# 6. Bill of Materials (Preliminary)\n\n"
                    "Create a detailed BOM table with columns: Item No | Reference Designator | "
                    "Part Number | Description | Manufacturer | Qty | Unit Cost (USD) | Total Cost | Notes.\n"
                    "Include ALL major components: ICs, passives, connectors, power components, crystals, etc. "
                    "Group by category. Include a total cost summary. 3-5 pages."
                ),
            ),
            (
                "Section 7 — Traceability Matrix",
                (
                    f"{context_block}\n\n"
                    "Write ONLY Section 7 (Traceability Matrix) of the HRS:\n"
                    "# 7. Traceability Matrix\n\n"
                    "Create a comprehensive requirement traceability matrix as a markdown table:\n"
                    "REQ-ID | Requirement Summary | Source | Verification Method | Phase | Status\n"
                    "Include ALL REQ-HW-xxx requirements from the document. "
                    "Minimum 25-30 rows. End with a summary count by verification method. 4-6 pages."
                ),
            ),
        ]

        total_sections = len(sections)

        # P26 (2026-04-25) — speed-up.
        #
        # Concurrency raised from 5 → 8: with 8 sections and previously
        # 5 concurrent slots, sections 6–8 had to wait a full LLM round
        # trip (30–60s). With 8 slots ALL sections fire at once. The GLM
        # / DeepSeek free tier comfortably handles 8 concurrent requests.
        #
        # Continuation passes capped at 2 (was 5). Each pass adds 30–60s.
        # In practice sections rarely truncate at max_tokens=16384; when
        # they do, 2 passes is enough for even the BOM and traceability
        # tables (>4000 rows would be needed to overflow a third pass).
        # Real-world impact: the worst-case section time drops from
        # 5 × 60s = 5min to 2 × 60s = 2min. Combined with the higher
        # concurrency, total HRS phase wall-time drops from ~10min
        # (user-reported) to ~2-3min for typical projects.
        _sem = asyncio.Semaphore(8)
        _MAX_CONTINUATION_PASSES = 2

        async def _generate_section(idx: int, section_name: str, section_prompt: str):
            """Generate one HRS section with retry + continuation.

            Retry: up to 2 attempts with 5s backoff on failure (rate-limit/transient).
            Continuation: up to 2 passes when the LLM hits max_tokens mid-section
            (was 5 — see P26 speed-up note above).
            """
            async with _sem:
                for attempt in range(1, 3):  # max 2 attempts
                    self.log(f"Generating HRS [{idx}/{total_sections}] {section_name}"
                             f"{'' if attempt == 1 else f' (retry {attempt})'} ...")
                    try:
                        resp = await self.call_llm(
                            messages=[{"role": "user", "content": section_prompt}],
                            system=system,
                        )
                        section_text = resp.get("content", "")

                        # Continuation passes if truncated at max_tokens —
                        # capped at 2 (was 5; see P26 note above).
                        for _sec_pass in range(1, _MAX_CONTINUATION_PASSES + 1):
                            if resp.get("stop_reason") != "max_tokens" or not section_text:
                                break
                            self.log(
                                f"  [{idx}/{total_sections}] {section_name} truncated — "
                                f"continuation pass {_sec_pass}/{_MAX_CONTINUATION_PASSES}..."
                            )
                            resp = await self.call_llm(
                                messages=[
                                    {"role": "user", "content": section_prompt},
                                    {"role": "assistant", "content": section_text},
                                    {"role": "user", "content": (
                                        f"Continue writing {section_name} from exactly where you stopped. "
                                        "Do NOT repeat content already written. "
                                        "Complete all sub-sections, tables, and requirement entries for this section."
                                    )},
                                ],
                                system=system,
                            )
                            section_text += "\n" + resp.get("content", "")

                        if section_text.strip():
                            self.log(f"  [{idx}/{total_sections}] {section_name} complete ({len(section_text)} chars)")
                            return (idx, section_text.strip())
                        return (idx, None)
                    except Exception as e:
                        self.log(f"  [{idx}/{total_sections}] {section_name} attempt {attempt} failed: {e}", "warning")
                        if attempt < 2:
                            await asyncio.sleep(5)  # backoff before retry
                        continue
                # All attempts exhausted
                self.log(f"  [{idx}/{total_sections}] {section_name} FAILED after 2 attempts", "warning")
                return (idx, None)

        # Dispatch ALL 8 sections — semaphore limits concurrency to 5.
        self.log(f"HRS generation: dispatching {total_sections} sections (5 concurrent, 5 continuations each)...")
        tasks = [
            _generate_section(idx, name, prompt)
            for idx, (name, prompt) in enumerate(sections, 1)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect successful sections, sorted by original index to preserve order.
        ordered: list[tuple[int, str]] = []
        for r in results:
            if isinstance(r, Exception):
                self.log(f"HRS parallel task raised: {r}", "warning")
                continue
            if not isinstance(r, tuple):
                continue
            idx, text = r
            if text:
                ordered.append((idx, text))
        ordered.sort(key=lambda x: x[0])
        all_sections = [text for _, text in ordered]

        if not all_sections:
            self.log("HRS generation failed — no sections generated", "warning")
            return ""

        # Require at least 4 of 8 sections to consider the LLM output usable.
        if len(all_sections) < 4:
            self.log(f"HRS generation partial ({len(all_sections)}/{total_sections} sections) — using template fallback", "warning")
            return ""  # Return empty to trigger template fallback

        # Join sections with a horizontal rule for readability
        full_hrs = "\n\n---\n\n".join(all_sections)
        self.log(f"HRS generation complete: {len(full_hrs)} characters across {len(all_sections)} sections")
        return full_hrs

    def _load_file(self, path: Path) -> str:
        """Load a file's content or return empty string."""
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    async def _extract_requirements(self, requirements_content: str, project_name: str) -> list:
        """Extract structured requirements list from Phase 1 requirements markdown."""
        if not requirements_content:
            return [{"id": "REQ-HW-001", "text": "System shall meet all specified requirements", "priority": "HIGH"}]
        import re
        reqs = []
        for match in re.finditer(r'(REQ-HW-\d+)[^\n]*?\|[^\|]+\|([^\|]+)\|([^\|]+)', requirements_content):
            reqs.append({
                "id": match.group(1),
                "text": match.group(2).strip(),
                "priority": match.group(3).strip(),
            })
        if not reqs:
            reqs = [{"id": "REQ-HW-001", "text": "System shall meet all specified requirements", "priority": "HIGH"}]
        return reqs

    async def _extract_components(self, components_content: str) -> dict:
        """Extract component data from markdown for template fallback."""
        if not components_content:
            return {}
        return {"components_markdown": components_content[:5000]}