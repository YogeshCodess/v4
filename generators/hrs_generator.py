"""
HRS Generator - IEEE 29148:2018 Hardware Requirements Specification.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class HRSGenerator:
    """Generate IEEE 29148:2018 compliant Hardware Requirements Specification."""

    def generate(
        self,
        project_name: str,
        requirements: List[Dict],
        component_data: Optional[Dict] = None,
        metadata: Optional[Dict] = None,
    ) -> str:
        """Generate complete HRS markdown document."""
        meta = metadata or {}
        sections = []

        sections.append(self._title_block(project_name, meta))
        sections.append(self._revision_history(meta))
        sections.append(self._section_introduction(project_name, meta))
        sections.append(self._section_system_overview(project_name, meta))
        sections.append(self._section_hardware_requirements(requirements))
        sections.append(self._section_design_constraints(meta))
        sections.append(self._section_verification(requirements))

        return "\n\n".join(sections)

    def _title_block(self, project_name: str, meta: Dict) -> str:
        return f"""# Hardware Requirements Specification (HRS)

**Project:** {project_name}
**Document:** IEEE 29148:2018 Compliant
**Date:** {datetime.now().strftime('%Y-%m-%d')}
**Version:** {meta.get('version', '1.0')}

---
"""

    def _revision_history(self, meta: Dict) -> str:
        return """## Revision History

| Version | Date       | Author      | Description         |
|---------|------------|-------------|---------------------|
| 1.0     | {date} | {author} | Initial release     |

---
""".format(date=datetime.now().strftime('%Y-%m-%d'), author=meta.get('author', 'Silicon to Software (S2S) AI'))

    def _section_introduction(self, project_name: str, meta: Dict) -> str:
        return """## 1. Introduction

### 1.1 Purpose
This document specifies the hardware requirements for the {project} system.

### 1.2 Scope
This HRS covers all hardware components, interfaces, and environmental requirements.

### 1.3 Definitions
| Term        | Definition                                      |
|-------------|-------------------------------------------------|
| EMC         | Electromagnetic Compatibility                   |
| ESD         | Electrostatic Discharge                         |

---
""".format(project=project_name)

    def _section_system_overview(self, project_name: str, meta: Dict) -> str:
        return """## 2. System Overview

### 2.1 System Architecture
```mermaid
graph TB
    SYS[{project} System]
    PSU[Power Supply]
    MCU[Processor]
    IO[I/O Interfaces]

    SYS --> PSU
    SYS --> MCU
    MCU --> IO

    style SYS fill:#e1f5ff
    style MCU fill:#fff4e1
```

---
""".format(project=project_name)

    def _section_hardware_requirements(self, requirements: List[Dict]) -> str:
        sections = ["## 3. Hardware Requirements\n"]
        for i, req in enumerate(requirements or [], 1):
            req_id = req.get('id', f'REQ-HW-{i:03d}')
            sections.append(f"### {req_id}")
            sections.append(f"**Priority:** {req.get('priority', 'MEDIUM')}")
            sections.append(f"{req.get('text', 'TBD')}\n")
        return "\n".join(sections) + "\n---\n"

    def _section_design_constraints(self, meta: Dict) -> str:
        return """## 4. Design Constraints

| Parameter          | Requirement              |
|--------------------|--------------------------|
| Input Voltage      | {input_v} V DC           |
| Max Power          | {max_p} W                |
| Operating Temp     | {t_min} to {t_max} °C    |

---
""".format(input_v=meta.get('input_voltage', '12-24'), max_p=meta.get('max_power', 'TBD'), t_min=meta.get('temp_min', '-40'), t_max=meta.get('temp_max', '+85'))

    def _section_verification(self, requirements: List[Dict]) -> str:
        return """## 5. Verification

| Method | Description |
|--------|-------------|
| Inspection | Visual inspection |
| Measurement | Electrical measurement |
| Test | Functional testing |

---
"""

    def save(self, content: str, output_dir: Path, project_name: str) -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"HRS_{project_name.replace(' ', '_')}.md"
        filepath = output_dir / filename
        filepath.write_text(content, encoding="utf-8")
        return filepath
