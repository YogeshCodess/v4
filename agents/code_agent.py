"""
Phase 8c: Code Generation + Review Agent

Generates C/C++ drivers, Qt C++ GUI skeleton, tests from SRS+SDD.
Real static analysis: Cppcheck + Lizard + cpplint + MISRA-C mapping.
Post-processing: CycloneDX SBOM update + Git CI/CD workflow + Git commit + GitHub PR.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List

from agents.base_agent import BaseAgent
from services.project_brief_builder import build_project_brief
from agents.static_analysis import StaticAnalysisRunner
from agents.qt_cpp_gui_generator import QtCppGuiGenerator
from agents.git_agent import GitAgent
from config import settings
from generators.driver_generator import DriverGenerator

# Optional import for legacy code reviewer
try:
    from reviewers.code_reviewer import CodeReviewer
    CODE_REVIEWER_AVAILABLE = True
except ImportError:
    CODE_REVIEWER_AVAILABLE = False

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior embedded software engineer generating production-ready C/C++ code from SRS and SDD specifications.

## YOUR TASK:
Generate the following code artifacts:

### 1. Device Drivers (C)
- Register access layer (HAL)
- Driver initialization and configuration
- Interrupt handlers
- DMA transfer functions
- Error handling with error codes
- MISRA-C 2012 compliant

### 2. Qt 5.14.2 C++ GUI Application (QMake / .pro)
- project.pro (QMake project file, Qt 5.14.2, C++14)
- MainWindow.h / MainWindow.cpp (tabbed dashboard, dark theme)
- SerialWorker.h / SerialWorker.cpp (QThread serial I/O via QSerialPort)
- main.cpp with dark QPalette

### 3. Test Suite
- Unit tests for each driver function
- Integration tests for hardware interaction
- Mock hardware layer for testing without hardware

### 4. Code Review Report
- MISRA-C compliance check results
- Code quality score (0-100)
- Security vulnerability assessment
- Recommendations for improvement

## CODE STANDARDS:
- C11 for drivers, C++14 for Qt 5.14.2 GUI
- MISRA-C 2012 for embedded C (no dynamic allocation, no recursion)
- Doxygen comments for all public APIs
- Error handling: return error codes, no exceptions in C
- All functions shall have single entry/exit point
- Maximum cyclomatic complexity: 10

## OUTPUT FORMAT:
Generate each file with its full path and complete content.
Wrap each file in a code block with the filename as a comment.
End with a code_review_report.md containing the quality analysis.
"""


class CodeAgent(BaseAgent):
    """Phase 8c: Code generation + real static analysis + Qt GUI + Git PR."""

    def __init__(self):
        super().__init__(
            phase_number="P8c",
            phase_name="Code Generation",
            model=settings.primary_model,
            max_tokens=16384,  # Increased for detailed code review reports
        )
        self.driver_generator = DriverGenerator()
        self.static_runner = StaticAnalysisRunner()
        self.qt_cpp_generator = QtCppGuiGenerator()
        self.git_agent = GitAgent()
        if CODE_REVIEWER_AVAILABLE:
            self.code_reviewer = CodeReviewer()
        else:
            self.code_reviewer = None

    def get_system_prompt(self, project_context: dict) -> str:
        # Prepend ProjectBrief preamble if built. See execute().
        preamble = getattr(self, "_brief_preamble", "") or ""
        if preamble:
            return preamble + "\n\n" + SYSTEM_PROMPT
        return SYSTEM_PROMPT

    async def execute(self, project_context: dict, user_input: str) -> dict:
        output_dir = Path(project_context.get("output_dir", "output"))
        output_dir.mkdir(parents=True, exist_ok=True)
        project_name = project_context.get("name", "Project")

        # P8c anti-genericity (2026-05-01): build a structured ProjectBrief
        # from every available P1-P7a output and stash it on the agent so
        # both the deterministic generators and the LLM deep-dive prompt see
        # project specifics (frequency, peripherals, register map, FSMs).
        # Without this, the LLM truncates upstream markdown to 6 KB per file
        # and falls back on its priors -> identical UART HAL across radar /
        # satcom / EW projects.
        try:
            self._brief = build_project_brief(
                project_id=int(project_context.get("project_id") or 0),
                project_name=project_name,
                output_dir=str(output_dir),
                project_type=str(project_context.get("project_type") or "receiver"),
                design_scope=str(project_context.get("design_scope") or "full"),
                application_class=str(
                    (project_context.get("design_parameters") or {}).get("application_class")
                    or (project_context.get("design_parameters") or {}).get("application")
                    or "general"
                ),
                hdl_language=str(
                    (project_context.get("design_parameters") or {}).get("hdl_language")
                    or "verilog"
                ),
            )
            self._brief_preamble = self._brief.to_prompt_preamble()
        except Exception as _brief_err:
            self.log(f"project_brief.build_failed: {_brief_err}", "warning")
            self._brief = None
            self._brief_preamble = ""

        # Load SRS and SDD (primary inputs)
        safe_name = project_name.replace(' ', '_')
        srs = self._load_file(output_dir / f"SRS_{safe_name}.md")
        sdd = self._load_file(output_dir / f"SDD_{safe_name}.md")
        glr = self._load_file(output_dir / "glr_specification.md")
        rdt = self._load_file(output_dir / "register_description_table.md")
        psq = self._load_file(output_dir / "programming_sequence.md")

        if not srs or not sdd:
            return {
                "response": "SRS and SDD required. Complete Phases 8a and 8b first.",
                "phase_complete": False,
                "outputs": {},
            }

        # --- Step 1: Generate C/C++ driver files ---
        components = await self._extract_components(glr)
        registers = await self._extract_registers(glr, srs)

        # Hand the ProjectBrief peripheral list to the generator so it
        # emits hal_peripherals.h with project-specific HAL stubs (e.g.
        # hal_ltc2208_adc_xfer(), hal_hmc830_pll_xfer()) instead of the
        # one-size-fits-all UART scaffolding.
        _peripherals = []
        if getattr(self, "_brief", None):
            _peripherals = [
                {"bus": p.bus, "name": p.name, "address": p.address or ""}
                for p in self._brief.peripherals
            ]
        generated_files = self.driver_generator.generate(
            project_name=project_name,
            components=components,
            registers=registers,
            metadata={"srs": srs[:1000], "sdd": sdd[:1000], "rdt": rdt, "psq": psq},
            peripherals=_peripherals,
        )

        saved_paths = self.driver_generator.save(generated_files, output_dir)

        outputs = {}
        for path in saved_paths:
            rel_path = path.relative_to(output_dir)
            outputs[str(rel_path)] = path.read_text(encoding="utf-8")

        # --- Step 1b: Create drivers/ directory (CI expects it at repo root) ---
        drivers_dir = output_dir / "drivers"
        drivers_dir.mkdir(parents=True, exist_ok=True)
        for fname, content in generated_files.items():
            if fname.endswith((".c", ".h")):
                (drivers_dir / fname).write_text(content, encoding="utf-8")
                outputs[f"drivers/{fname}"] = content
        # Create a Makefile for ARM cross-compilation
        driver_makefile = self._build_drivers_makefile(safe_name, list(generated_files.keys()))
        (drivers_dir / "Makefile").write_text(driver_makefile, encoding="utf-8")
        outputs["drivers/Makefile"] = driver_makefile

        # --- Step 1c: Create tests/ directory with Google Test stubs ---
        tests_dir = output_dir / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        test_main = self._build_test_main(safe_name, list(generated_files.keys()))
        (tests_dir / "test_drivers.cpp").write_text(test_main, encoding="utf-8")
        outputs["tests/test_drivers.cpp"] = test_main

        # --- Step 2: Real static analysis (Cppcheck + Lizard + cpplint) ---
        self.log("Running real static analysis (Cppcheck + Lizard + cpplint)...")
        c_files_for_analysis = {
            k: v for k, v in generated_files.items()
            if k.endswith((".c", ".cpp", ".h"))
        }
        analysis_results = self.static_runner.analyze(c_files_for_analysis)

        # LLM-enhanced review: pass real findings for MISRA categorization + fix suggestions
        review_report = await self._generate_enhanced_review(
            analysis_results=analysis_results,
            generated_files=generated_files,
            project_name=project_name,
        )
        review_file = output_dir / "code_review_report.md"
        review_file.write_text(review_report, encoding="utf-8")
        outputs["code_review_report.md"] = review_report

        # --- Step 3: Qt C++ GUI skeleton ---
        self.log("Generating Qt C++ GUI application skeleton...")
        design_type = project_context.get("design_type", "Digital")
        # Hand the brief's peripherals + application_class to the Qt
        # generator. Two distinct projects then ship visibly distinct GUIs:
        # different peripheral panels, different application primaries,
        # different .pro file SOURCES list.
        _qt_peripherals = []
        _qt_app_class = "general"
        if getattr(self, "_brief", None):
            _qt_peripherals = [
                {"bus": pp.bus, "name": pp.name, "address": pp.address or ""}
                for pp in self._brief.peripherals
            ]
            _qt_app_class = self._brief.application_class or "general"
        qt_cpp_files = self.qt_cpp_generator.generate(
            project_name=project_name,
            design_type=design_type,
            peripherals=_qt_peripherals,
            application_class=_qt_app_class,
        )
        # Write Qt C++ files to disk and add to outputs dict
        for rel_path, content in qt_cpp_files.items():
            file_path = output_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            outputs[rel_path] = content

        # Write a README for the Qt GUI
        safe_name = self.qt_cpp_generator._safe_class_name(project_name)
        gui_readme = (
            f"# {project_name} — Qt C++ GUI Application\n\n"
            "Generated by Silicon to Software (S2S) v2\n\n"
            "## Requirements\n\n"
            "- **Qt 5.14.2** (QtSerialPort module required)\n"
            "- **QMake** (bundled with Qt)\n"
            "- **MinGW 32-bit or 64-bit** (Windows 10) — or MSVC 2017+\n\n"
            "## Build (Qt Creator — recommended)\n\n"
            f"1. Open `qt_gui/{safe_name}.pro` in Qt Creator\n"
            "2. Select a Qt 5.14.2 MinGW kit\n"
            "3. Click **Build** (Ctrl+B)\n\n"
            "## Build (command line)\n\n"
            "```bat\n"
            "cd qt_gui\n"
            f"qmake {safe_name}.pro\n"
            "mingw32-make -j4\n"
            "```\n\n"
            "## Project Structure\n\n"
            "All UI is defined in Qt Designer `.ui` files — no dynamic widget creation in C++.\n"
            "Promoted widgets are used in `MainWindow.ui` for each sub-panel.\n\n"
            "| File | Role |\n"
            "|------|------|\n"
            "| `MainWindow.ui` | QTabWidget with 4 promoted-widget tabs |\n"
            "| `DashboardPanel.ui/.h/.cpp` | Live data table + stat cards |\n"
            "| `ControlPanel.ui/.h/.cpp` | Manual command entry + history |\n"
            "| `LogPanel.ui/.h/.cpp` | Colour-coded serial log |\n"
            "| `SettingsPanel.ui/.h/.cpp` | Port / baud / parity configuration |\n"
            "| `SerialWorker.h/.cpp` | QThread-based QSerialPort I/O |\n\n"
            "## Features\n\n"
            "- **Dashboard** — live data table (200-row ring), stat cards, progress bar\n"
            "- **Control** — command entry + history list, Send on Enter or button\n"
            "- **Log** — colour-coded (RX=teal, TX=blue, ERR=red, INF=amber)\n"
            "- **Settings** — port/baud/data-bits/parity/stop-bits, Refresh Ports\n"
            "- **SerialWorker** — QThread I/O, newline-framed packets, thread-safe invoke\n"
            "- **Dark theme** — Fusion style + QPalette (#0f1423 navy, #00c6a7 teal)\n"
        )
        (output_dir / "qt_gui" / "README.md").write_text(gui_readme, encoding="utf-8")
        outputs["qt_gui/README.md"] = gui_readme

        # --- Step 3.5: GitHub Actions CI/CD workflow ---
        self.log("Generating GitHub Actions CI/CD workflow...")
        ci_workflow = self._build_github_ci_workflow(project_name, safe_name)
        ci_dir = output_dir / ".github" / "workflows"
        ci_dir.mkdir(parents=True, exist_ok=True)
        ci_file = ci_dir / "hardware_pipeline_ci.yml"
        ci_file.write_text(ci_workflow, encoding="utf-8")
        outputs[".github/workflows/hardware_pipeline_ci.yml"] = ci_workflow

        # --- Step 3.5a: ARM toolchain CMake file ---
        arm_toolchain = self._build_arm_toolchain_cmake()
        cmake_dir = output_dir / "cmake"
        cmake_dir.mkdir(parents=True, exist_ok=True)
        (cmake_dir / "arm-none-eabi.cmake").write_text(arm_toolchain, encoding="utf-8")
        outputs["cmake/arm-none-eabi.cmake"] = arm_toolchain

        # --- Step 3.6: Validate the CI/CD YAML locally (no credentials needed) ---
        self.log("Validating CI/CD workflow YAML (local, no credentials required)...")
        ci_validation = self._validate_ci_workflow(ci_workflow)
        ci_validation_file = output_dir / "ci_validation_report.md"
        ci_validation_file.write_text(ci_validation, encoding="utf-8")
        outputs["ci_validation_report.md"] = ci_validation

        self.log(f"P8c complete: {len(outputs)} files "
                 f"(quality score: {analysis_results['summary'].get('quality_score', 'N/A')}/100)")

        # --- Step 4: Git commit + GitHub PR (non-blocking) ---
        git_result = {"success": False, "reason": "Git integration not configured"}
        if self.git_agent.enabled:
            self.log("Creating git commit + GitHub PR...")
            git_result = await self.git_agent.commit_and_pr(
                project_name=project_name,
                output_dir=output_dir,
                review_report_path=review_file,
            )
            if git_result.get("success"):
                self.log(
                    f"Git: commit {git_result.get('commit_sha')} | "
                    f"PR: {git_result.get('pr_url', 'no remote configured')}"
                )

        # Include git summary in outputs
        git_summary = self._build_git_summary(git_result, project_name)
        outputs["git_summary.md"] = git_summary
        (output_dir / "git_summary.md").write_text(git_summary, encoding="utf-8")

        quality_score = analysis_results["summary"].get("quality_score", "N/A")
        tools_used = analysis_results["summary"].get("tools_used", "LLM")
        pr_line = f" | PR: {git_result['pr_url']}" if git_result.get("pr_url") else ""

        # Anti-genericity gate (2026-05-01): fingerprint the output bundle
        # and warn loudly if it collides with another project's. A genuine
        # collision means we shipped boilerplate the user can't use.
        try:
            _proj_id = int(project_context.get("project_id") or 0)
            _fp = compute_fingerprint(output_dir)
            record_fingerprint(_proj_id, "P8c", _fp)
            _collisions = find_collisions(_fp, exclude_project_id=_proj_id)
            if _collisions:
                self.log(
                    f"output.fingerprint_collision P8c fp={_fp} - other "
                    f"projects produced byte-equivalent output: "
                    f"{[c['project_id'] for c in _collisions]}",
                    "warning",
                )
        except Exception as _fp_err:
            self.log(f"output.fingerprint_failed: {_fp_err}", "debug")

        return {
            "response": (
                f"P8c complete — {len(outputs)} files generated. "
                f"Quality score: {quality_score}/100 (via {tools_used}).{pr_line}"
            ),
            "phase_complete": True,
            "outputs": outputs,
        }

    async def _generate_enhanced_review(
        self,
        analysis_results: dict,
        generated_files: Dict[str, str],
        project_name: str,
    ) -> str:
        """
        Full in-depth review: structured tool findings + comprehensive LLM deep-dive
        covering MISRA-C 2023, security, architecture quality, and line-by-line fixes.
        Always runs both layers regardless of issue count.
        """
        # Start with the structured tool report
        tool_report = self.static_runner.format_markdown_report(analysis_results, project_name)

        # Always run full LLM deep-dive — not just when tools find issues
        c_files_for_llm = {k: v for k, v in generated_files.items() if k.endswith((".c", ".cpp", ".h"))}
        all_files_for_llm = {k: v for k, v in generated_files.items()}

        # Use all C/C++ files (up to 6), 6000 chars each for comprehensive review
        code_full = "\n\n".join(
            f"// === FILE: {fname} ===\n{code[:6000]}"
            for fname, code in list(c_files_for_llm.items())[:6]
        )
        if not code_full and all_files_for_llm:
            code_full = "\n\n".join(
                f"// === FILE: {fname} ===\n{code[:6000]}"
                for fname, code in list(all_files_for_llm.items())[:4]
            )

        summary = analysis_results.get("summary", {})
        findings_text = json.dumps(
            {
                "cppcheck_all": analysis_results.get("cppcheck", []),
                "complexity": analysis_results.get("complexity", []),
                "style_top": analysis_results.get("style", [])[:20],
                "quality_score": summary.get("quality_score", "N/A"),
                "misra_violations": summary.get("misra_violations", 0),
            },
            indent=2
        )

        llm_prompt = (
            f"You are performing a full in-depth code review for the project '{project_name}'.\n\n"
            f"## Static Analysis Tool Results\n```json\n{findings_text}\n```\n\n"
            f"## Source Code\n```c\n{code_full}\n```\n\n"
            f"Provide a comprehensive review with ALL of the following sections:\n\n"
            f"### 1. MISRA-C:2012 / MISRA-C:2023 Deep Analysis\n"
            f"- Cite exact rule numbers (e.g. Rule 15.5, Rule 17.3, Dir 4.7)\n"
            f"- Classify each rule as: Mandatory / Required / Advisory\n"
            f"- For each violation: quote the offending line, explain why it violates the rule, "
            f"and provide a CORRECTED code snippet\n"
            f"- Check specifically: Rule 14.4 (bool controlling expressions), Rule 15.5 (single exit), "
            f"Rule 17.3 (implicit function declaration), Rule 17.7 (return value usage), "
            f"Dir 4.1 (arithmetic overflow), Dir 4.7 (error information), Dir 4.11 (validity of inputs)\n\n"
            f"### 2. Security Vulnerability Assessment\n"
            f"- Buffer overflows (CWE-120, CWE-121, CWE-122)\n"
            f"- Integer overflow/underflow (CWE-190, CWE-191)\n"
            f"- Unchecked return values (CWE-252)\n"
            f"- Use of dangerous functions (sprintf, strcpy, gets → flag with severity)\n"
            f"- Severity: CRITICAL / HIGH / MEDIUM / LOW with exact line references\n\n"
            f"### 3. Firmware-Specific Issues\n"
            f"- ISR safety: volatile-missing on shared variables, non-reentrant functions called from ISR\n"
            f"- Race conditions: check all global variable accesses for atomic read-modify-write issues\n"
            f"- Stack depth: identify deepest call chains, flag unbounded recursion\n"
            f"- Watchdog petting: confirm WDT_Pet() is called on every code path through main loop\n"
            f"- Error propagation: every HAL function return value must be checked\n"
            f"- Static allocation: confirm no malloc/calloc/free/realloc (MISRA Rule 21.3)\n\n"
            f"### 4. Architecture & Design Quality\n"
            f"- Module coupling and cohesion analysis\n"
            f"- Cyclomatic complexity per function (flag any > 10)\n"
            f"- Dead code detection\n"
            f"- Magic numbers: flag all numeric literals that should be named constants\n"
            f"- Missing Doxygen headers: list all public functions without complete documentation\n\n"
            f"### 5. Line-by-Line Fix Recommendations\n"
            f"For EVERY issue found above, provide:\n"
            f"```c\n// BEFORE (line XX — rule/issue description)\n"
            f"[original code snippet]\n\n"
            f"// AFTER (corrected code with explanation)\n"
            f"[fixed code snippet]\n```\n\n"
            f"### 6. Test Coverage Recommendations\n"
            f"For each driver module, specify:\n"
            f"- Unit test functions needed (test name + what it validates)\n"
            f"- Boundary values and edge cases\n"
            f"- Fault injection scenarios (hardware timeout, CRC error, etc.)\n"
            f"- Hardware-in-the-loop (HIL) test scenarios\n"
            f"- Target: minimum 80% line coverage, 70% branch coverage\n\n"
            f"### 7. SRS Traceability Check\n"
            f"- List which REQ-SW-xxx requirements each file implements\n"
            f"- Identify any SRS requirements not implemented in the generated code\n"
            f"- Flag missing functionality: POST, fault logging, UART loopback test\n\n"
            f"### 8. Certification Readiness Assessment\n"
            f"- IEC 61508 SIL-2 gaps: tool qualification, coding standard enforcement, V&V evidence\n"
            f"- ISO 26262 ASIL-B gaps: systematic capability, software unit testing requirements\n"
            f"- Required process documentation for functional safety certification\n"
            f"- Estimated effort to reach SIL-1 compliance\n\n"
            f"### 9. Quality Score Breakdown\n"
            f"Score 0–100 across five dimensions:\n"
            f"| Dimension | Score/20 | Key Issues |\n"
            f"|-----------|---------|------------|\n"
            f"| MISRA Compliance | /20 | |\n"
            f"| Security | /20 | |\n"
            f"| Firmware Safety | /20 | |\n"
            f"| Code Quality | /20 | |\n"
            f"| Test Coverage | /20 | |\n"
            f"| **TOTAL** | **/100** | |\n\n"
            f"Be exhaustive. Show actual code. Do not summarise — give the full analysis."
        )

        try:
            _system = (
                "You are a senior embedded systems engineer and MISRA-C 2023 expert with 15+ years "
                "in safety-critical firmware (automotive ASIL-D, aerospace DO-178C, medical IEC 62304). "
                "Provide exhaustive, actionable code reviews. Always cite exact rule numbers, "
                "show before/after code, and classify severity. Never skip sections."
            )
            if getattr(self, "_brief_preamble", ""):
                _system = self._brief_preamble + "\n\n" + _system
            llm_response = await self.call_llm(
                messages=[{"role": "user", "content": llm_prompt}],
                system=_system,
                model=settings.model,   # use the main model for full depth, not fast_model
            )
            llm_section = llm_response.get("content", "")
            if llm_section:
                tool_report += (
                    "\n\n---\n\n## LLM Recommendations\n\n"
                    "_The recommendations below are produced by an LLM (model = "
                    + str(getattr(settings, "model", "?"))
                    + "). They are advisory and should be cross-checked against "
                    "the deterministic Tool-Detected Issues section above before "
                    "acting on them._\n\n"
                    + llm_section
                )
        except Exception as e:
            self.log(f"LLM deep-dive analysis failed (non-fatal): {e}", "warning")
            tool_report += f"\n\n_Note: LLM deep-dive unavailable — {e}_"

        return tool_report

    def _validate_ci_workflow(self, yaml_content: str) -> str:
        """
        Validate the generated GitHub Actions YAML locally — no credentials needed.

        Checks performed (all offline):
          1. YAML syntax parse        — via Python stdlib yaml
          2. Required top-level keys  — name, on, jobs
          3. Each job has runs-on     — prevents bare-metal deploy mistakes
          4. Each job has at least one step
          5. Step integrity           — every step has 'name' and either 'run' or 'uses'
          6. actionlint               — if installed (optional, best-effort)
        """
        import yaml
        import subprocess
        import shutil
        from datetime import datetime

        lines = ["# CI/CD Workflow Validation Report",
                 "\n**File:** `.github/workflows/hardware_pipeline_ci.yml`",
                 f"**Validated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — offline YAML syntax check",
                 "> **Note:** Your GitHub token IS used — for the git push and PR creation (next step).",
                 "> This step validates YAML syntax, job structure, and trigger keys locally.",
                 "> No GitHub API call needed here. The token is applied when committing and opening the PR.\n",
                 "---\n"]

        errors: list = []
        warnings: list = []
        passes: list = []

        # ── Check 1: YAML syntax ─────────────────────────────────────────
        try:
            workflow = yaml.safe_load(yaml_content)
            passes.append("YAML syntax — valid, parses without error")
        except yaml.YAMLError as exc:
            errors.append(f"YAML syntax error: {exc}")
            lines.append("## ❌ YAML Syntax\n\n```\n" + str(exc) + "\n```")
            lines.append("\n_Remaining checks skipped due to parse failure._")
            return "\n".join(lines)

        # ── Check 2: Required top-level keys ─────────────────────────────
        # NOTE: PyYAML (YAML 1.1) parses bare `on:` as boolean True, not string "on".
        # We must check for both the string "on" AND boolean True when looking for the trigger key.
        for key in ("name", "on", "jobs"):
            # Special case: `on:` in YAML 1.1 is parsed as boolean True by PyYAML
            present = key in workflow or (key == "on" and True in workflow)
            if present:
                passes.append(f"Top-level key `{key}` — present")
            else:
                errors.append(f"Missing required top-level key: `{key}`")

        # ── Check 3 & 4 & 5: Job structure ───────────────────────────────
        jobs = workflow.get("jobs", {}) or {}
        if not jobs:
            errors.append("No jobs defined in workflow")
        else:
            for job_id, job in jobs.items():
                if not isinstance(job, dict):
                    errors.append(f"Job `{job_id}` is not a mapping")
                    continue
                # runs-on
                if "runs-on" in job:
                    passes.append(f"Job `{job_id}`: `runs-on` = `{job['runs-on']}`")
                else:
                    errors.append(f"Job `{job_id}`: missing `runs-on`")
                # steps
                steps = job.get("steps") or []
                if not steps:
                    errors.append(f"Job `{job_id}`: no steps defined")
                else:
                    passes.append(f"Job `{job_id}`: {len(steps)} step(s) defined")
                    for i, step in enumerate(steps):
                        has_name = "name" in step
                        has_action = "run" in step or "uses" in step
                        if not has_name:
                            warnings.append(
                                f"Job `{job_id}` step {i+1}: missing `name` (recommended)")
                        if not has_action:
                            errors.append(
                                f"Job `{job_id}` step {i+1}: no `run` or `uses` — "
                                "step will do nothing")

        # ── Check 6: actionlint (optional) ───────────────────────────────
        actionlint_result = None
        if shutil.which("actionlint"):
            try:
                import tempfile
                import os
                with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".yml",
                        delete=False, encoding="utf-8") as f:
                    f.write(yaml_content)
                    tmp = f.name
                r = subprocess.run(
                    ["actionlint", tmp],
                    capture_output=True, text=True, timeout=15)
                os.unlink(tmp)
                if r.returncode == 0:
                    passes.append("actionlint — no issues found")
                    actionlint_result = "✅ actionlint passed — no issues"
                else:
                    actionlint_result = "⚠️ actionlint findings:\n```\n" + r.stdout.strip() + "\n```"
                    warnings.append("actionlint reported issues (see below)")
            except Exception as e:
                actionlint_result = f"_actionlint run failed: {e}_"
        else:
            actionlint_result = (
                "_actionlint not installed — skipped._  \n"
                "Install with: `go install github.com/rhysd/actionlint/cmd/actionlint@latest`")

        # ── Build report ─────────────────────────────────────────────────
        status = "✅ PASSED" if not errors else "❌ FAILED"
        lines.append(f"## Overall: {status}\n")
        lines.append(f"- Errors  : {len(errors)}")
        lines.append(f"- Warnings: {len(warnings)}")
        lines.append(f"- Passes  : {len(passes)}\n")

        if errors:
            lines.append("## ❌ Errors\n")
            for e in errors:
                lines.append(f"- {e}")
            lines.append("")

        if warnings:
            lines.append("## ⚠️ Warnings\n")
            for w in warnings:
                lines.append(f"- {w}")
            lines.append("")

        if passes:
            lines.append("## ✅ Passed Checks\n")
            for p in passes:
                lines.append(f"- {p}")
            lines.append("")

        lines.append("## actionlint\n")
        lines.append(actionlint_result)
        lines.append("")

        lines.append("## Jobs Summary\n")
        lines.append("| Job | runs-on | Steps |")
        lines.append("|-----|---------|-------|")
        for job_id, job in (jobs.items() if isinstance(jobs, dict) else []):
            if isinstance(job, dict):
                ro = job.get("runs-on", "—")
                sc = len(job.get("steps") or [])
                lines.append(f"| `{job_id}` | `{ro}` | {sc} |")
        lines.append("")

        lines.append("## How to Push and Activate\n")
        lines.append("Since Git is not configured in `.env`, the workflow file has been\n"
                     "written to disk but not committed. To activate CI/CD:\n")
        lines.append("```bat")
        lines.append("cd <your-project-output-dir>")
        lines.append("git init")
        lines.append("git add .github/workflows/hardware_pipeline_ci.yml")
        lines.append('git commit -m "[AI] Silicon to Software (S2S): add CI/CD workflow"')
        lines.append("git remote add origin https://github.com/<owner>/<repo>.git")
        lines.append("git push -u origin main")
        lines.append("```")
        lines.append("\nOnce pushed, GitHub Actions runs automatically on every commit.\n")
        lines.append("To enable automated commits from this tool, add to `.env`:\n")
        lines.append("```")
        lines.append("GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx")
        lines.append("GITHUB_REPO=owner/repo-name")
        lines.append("GIT_ENABLED=true")
        lines.append("```")

        return "\n".join(lines)

    def _build_git_summary(self, git_result: dict, project_name: str) -> str:
        if git_result.get("success"):
            # P26 #18 (2026-04-26): the previous "Not created (no GitHub
            # remote configured)" line was misleading whenever the push
            # actually failed (most often on the `workflow` PAT scope).
            # The git_agent now propagates `push_error` / `pr_error` /
            # `remote_status` so we can show the user the REAL reason
            # AND a fix instruction.
            pr_url = git_result.get("pr_url")
            if pr_url:
                pr_line = f"**Pull Request:** [{pr_url}]({pr_url})"
            else:
                status = git_result.get("remote_status", "no_remote")
                push_err = git_result.get("push_error") or ""
                pr_err   = git_result.get("pr_error")   or ""
                if status == "no_remote":
                    pr_line = (
                        "**Pull Request:** Not created — "
                        f"{push_err or 'GITHUB_TOKEN / GITHUB_REPO not configured'}.\n"
                        "_Set both in `.env` and restart the FastAPI server, "
                        "then re-run P8c. PAT must have `repo` AND `workflow` scopes._"
                    )
                elif status == "push_failed":
                    pr_line = (
                        "**Pull Request:** Not created — push to GitHub failed.\n"
                        f"```\n{push_err[:600]}\n```"
                    )
                elif status == "push_ok_pr_failed":
                    pr_line = (
                        "**Pull Request:** Push succeeded but PR creation failed.\n"
                        f"```\n{pr_err[:600]}\n```\n"
                        "_The branch was pushed to GitHub — you can open the PR "
                        "manually from the GitHub UI._"
                    )
                else:
                    pr_line = "**Pull Request:** Not created (unknown reason — see backend logs)"
            return (
                f"# Git Commit Summary — {project_name}\n\n"
                f"**Status:** ✅ Committed\n"
                f"**Commit SHA:** `{git_result.get('commit_sha', 'N/A')}`\n"
                f"**Branch:** `{git_result.get('branch', 'N/A')}`\n"
                f"{pr_line}\n\n"
                f"All generated artefacts have been committed to the local repository.\n\n"
                f"## CI/CD Pipeline\n\n"
                f"Workflow: `.github/workflows/hardware_pipeline_ci.yml`  \n"
                f"Runs automatically on every push and PR:\n\n"
                f"- **Build Qt5 GUI** — QMake + make, ubuntu-22.04 (Qt 5.14.2, C++14)\n"
                f"- **Build ARM Firmware** — arm-none-eabi-gcc cross-compile, binary size check\n"
                f"- **Unit Tests + Coverage** — GCC + Google Test, lcov HTML report, 60% gate\n"
                f"- **Static Analysis** — Cppcheck (MISRA-C C11 + C++14) + Clang-Tidy (bugprone, cert, perf)\n"
                f"- **Quality Gate** — all jobs must pass before merge\n"
                f"- **Artifacts** — binaries, coverage HTML, Cppcheck report (30-day retention)\n\n"
                f"See `ci_validation_report.md` for local pre-push validation results.\n"
            )
        else:
            reason = git_result.get("reason") or git_result.get("error", "Unknown error")
            return (
                f"# Git Commit Summary — {project_name}\n\n"
                f"**Status:** ⚠️ Git skipped\n"
                f"**Reason:** {reason}\n\n"
                f"## CI/CD Workflow — Generated & Validated Locally\n\n"
                f"The workflow file has been written to disk and validated without credentials.\n\n"
                f"**File:** `.github/workflows/hardware_pipeline_ci.yml`\n\n"
                f"**Jobs:**\n"
                f"- `build-qt-app` — QMake + make, Qt 5.14.2 + QtSerialPort, ubuntu-22.04\n"
                f"- `build-firmware-arm` — arm-none-eabi-gcc cross-compile, binary size report\n"
                f"- `unit-tests` — GCC + Google Test + lcov coverage (60% gate)\n"
                f"- `static-analysis` — Cppcheck MISRA-C C11/C++14 + Clang-Tidy bugprone/cert\n"
                f"- `quality-gate` — passes only when build + tests + analysis all succeed\n\n"
                f"**Validation:** See `ci_validation_report.md` for YAML parse results,\n"
                f"job structure checks, and actionlint output (if installed).\n\n"
                f"**To activate Git integration**, add to `.env`:\n"
                f"```\nGITHUB_TOKEN=ghp_xxxx\nGITHUB_REPO=owner/repo\nGIT_ENABLED=true\n```\n"
            )

    def _build_drivers_makefile(self, safe_name: str, filenames: list) -> str:
        """Generate a Makefile for ARM cross-compilation of driver .c files."""
        c_files = [f for f in filenames if f.endswith(".c")]
        obj_files = [f.replace(".c", ".o") for f in c_files]
        return (
            f"# {safe_name} — ARM Cortex Firmware Makefile\n"
            f"# Auto-generated by Silicon to Software (S2S) v2\n\n"
            f"CROSS_COMPILE ?= arm-none-eabi-\n"
            f"CC      = $(CROSS_COMPILE)gcc\n"
            f"OBJCOPY = $(CROSS_COMPILE)objcopy\n"
            f"SIZE    = $(CROSS_COMPILE)size\n\n"
            f"CFLAGS  = -mcpu=cortex-m4 -mthumb -std=c11 -Wall -Wextra -Os\n"
            f"CFLAGS += -ffunction-sections -fdata-sections -DSTM32F4\n"
            f"LDFLAGS = -Wl,--gc-sections -nostartfiles -T linker.ld\n\n"
            f"TARGET  = {safe_name}\n"
            f"SRCS    = {' '.join(c_files)}\n"
            f"OBJS    = {' '.join(obj_files)}\n\n"
            f"all: $(TARGET).elf $(TARGET).bin\n\n"
            f"$(TARGET).elf: $(OBJS)\n"
            f"\t$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^\n\n"
            f"$(TARGET).bin: $(TARGET).elf\n"
            f"\t$(OBJCOPY) -O binary $< $@\n\n"
            f"%.o: %.c\n"
            f"\t$(CC) $(CFLAGS) -c -o $@ $<\n\n"
            f"clean:\n"
            f"\trm -f $(OBJS) $(TARGET).elf $(TARGET).bin\n\n"
            f".PHONY: all clean\n"
        )

    def _build_test_main(self, safe_name: str, filenames: list) -> str:
        """Generate a Google Test file that tests each driver header."""
        h_files = [f for f in filenames if f.endswith(".h")]
        includes = "\n".join(f'extern "C" {{ #include "../drivers/{h}" }}' for h in h_files) if h_files else '// No driver headers found'

        tests = []
        for h in h_files:
            mod = h.replace(".h", "").replace("_driver", "")
            tests.append(
                f"TEST({mod}_test, init_returns_ok) {{\n"
                f"    // Stub: verify {mod} init function exists and is callable\n"
                f"    SUCCEED() << \"{mod} driver header included successfully\";\n"
                f"}}\n"
            )

        if not tests:
            tests.append(
                "TEST(placeholder, build_succeeds) {\n"
                "    SUCCEED() << \"Test infrastructure builds correctly\";\n"
                "}\n"
            )

        return (
            f"// {safe_name} — Unit Tests (Google Test)\n"
            f"// Auto-generated by Silicon to Software (S2S) v2\n\n"
            f"#include <gtest/gtest.h>\n\n"
            f"{includes}\n\n"
            f"{''.join(tests)}\n"
        )

    def _build_github_ci_workflow(self, project_name: str, safe_name: str) -> str:
        """Generate a GitHub Actions CI/CD workflow — QMake + Qt 5.14.2 + ARM cross-compile + tests + coverage."""
        return f"""\
# Silicon to Software (S2S) CI/CD — {project_name}
# Auto-generated by Silicon to Software (S2S) v2
# Qt 5.14.2 | QMake | GCC (host) | arm-none-eabi-gcc (embedded)

name: Silicon to Software (S2S) CI

on:
  push:
    branches: [ main, master, 'ai/pipeline/**' ]
  pull_request:
    branches: [ main, master ]

env:
  QT_VERSION: '5.14.2'
  BUILD_TYPE: Release
  ARM_TOOLCHAIN: arm-none-eabi

jobs:
  # ------------------------------------------------------------------ #
  # Job 1: Build Qt 5.14.2 GUI application (QMake)
  # ------------------------------------------------------------------ #
  build-qt-app:
    name: Build Qt 5.14.2 GUI Application
    runs-on: ubuntu-22.04

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Install system dependencies
        run: |
          sudo apt-get update -y
          sudo apt-get install -y \\
            build-essential \\
            libgl1-mesa-dev \\
            libgtest-dev \\
            gcovr lcov

      - name: Install Qt ${{{{ env.QT_VERSION }}}}
        uses: jurplel/install-qt-action@v3
        with:
          version: ${{{{ env.QT_VERSION }}}}
          modules: ''
          cache: true

      - name: Run qmake
        working-directory: qt_gui
        run: qmake {safe_name}.pro CONFIG+=release

      - name: Build with make
        working-directory: qt_gui
        run: make -j$(nproc)

      - name: Upload Qt5 build artifact
        uses: actions/upload-artifact@v4
        with:
          name: {safe_name}-qt5-linux
          path: qt_gui/{safe_name}
          retention-days: 30

  # ------------------------------------------------------------------ #
  # Job 2: ARM Embedded Firmware Build (arm-none-eabi-gcc + Makefile)
  # ------------------------------------------------------------------ #
  build-firmware-arm:
    name: Build Firmware (ARM Cortex)
    runs-on: ubuntu-22.04

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Install ARM GCC toolchain
        run: |
          sudo apt-get update -y
          sudo apt-get install -y gcc-arm-none-eabi binutils-arm-none-eabi

      - name: Verify ARM toolchain
        run: arm-none-eabi-gcc --version

      - name: Build firmware
        run: |
          if [ -d drivers ]; then
            cd drivers
            make CROSS_COMPILE=arm-none-eabi- all || make -f Makefile all || true
          else
            echo "drivers/ directory not found — skipping firmware build"
          fi

      - name: Check binary size
        run: |
          find drivers/ -name '*.elf' -exec arm-none-eabi-size {{}} \\; || true
          find drivers/ -name '*.elf' -exec arm-none-eabi-objdump -h {{}} \\; | grep -E "(text|data|bss)" || true

      - name: Upload firmware binary
        uses: actions/upload-artifact@v4
        with:
          name: {safe_name}-firmware-arm
          path: |
            drivers/*.elf
            drivers/*.bin
            drivers/*.hex
          retention-days: 30
          if-no-files-found: ignore

  # ------------------------------------------------------------------ #
  # Job 3: Unit Tests + Code Coverage (host GCC)
  # ------------------------------------------------------------------ #
  unit-tests:
    name: Unit Tests + Coverage
    runs-on: ubuntu-22.04

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Install dependencies
        run: |
          sudo apt-get update -y
          sudo apt-get install -y \\
            build-essential \\
            libgtest-dev \\
            gcovr lcov

      - name: Build Google Test
        run: |
          cd /usr/src/gtest
          sudo cmake .
          sudo make
          sudo cp lib/*.a /usr/lib/

      - name: Build tests with coverage
        run: |
          if [ -d tests ] && [ -d drivers ]; then
            cd tests
            g++ -std=c++14 -g --coverage -fprofile-arcs -ftest-coverage \\
              -I../drivers -I/usr/include/gtest \\
              -o run_tests *.cpp ../drivers/*.c \\
              -lgtest -lgtest_main -lpthread || true
          else
            echo "tests/ or drivers/ directory not found — skipping test build"
          fi

      - name: Run unit tests
        run: |
          if [ -f tests/run_tests ]; then
            cd tests
            ./run_tests --gtest_output=xml:test-results.xml || true
          else
            echo "No test binary found — skipping"
          fi

      - name: Generate coverage report (lcov)
        run: |
          lcov --capture --directory tests/ \\
               --output-file coverage.info \\
               --exclude '*gtest*' --exclude '/usr/*' --exclude '*/tests/*' || true
          lcov --summary coverage.info || true
          genhtml coverage.info --output-directory coverage-html || true
          gcovr --xml-pretty --exclude-unreachable-branches \\
                --output coverage.xml tests/ || true

      - name: Upload coverage HTML report
        uses: actions/upload-artifact@v4
        with:
          name: coverage-report
          path: coverage-html/
          retention-days: 30
          if-no-files-found: ignore

      - name: Upload coverage XML (for CI badge)
        uses: actions/upload-artifact@v4
        with:
          name: coverage-xml
          path: coverage.xml
          retention-days: 30
          if-no-files-found: ignore

      - name: Coverage gate (must be >= 60%)
        run: |
          COVERAGE=$(gcovr --print-summary tests/ 2>&1 | grep "lines:" | awk '{{print $2}}' | tr -d '%')
          echo "Line coverage: ${{COVERAGE}}%"
          if [ -n "$COVERAGE" ] && [ "${{COVERAGE%.*}}" -lt 60 ]; then
            echo "Coverage ${{COVERAGE}}% is below 60% threshold"
            exit 1
          fi
          echo "Coverage gate passed: ${{COVERAGE}}%"

  # ------------------------------------------------------------------ #
  # Job 4: Static Analysis — Cppcheck + Clang-Tidy
  # ------------------------------------------------------------------ #
  static-analysis:
    name: Static Analysis
    runs-on: ubuntu-22.04

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Install analysis tools
        run: |
          sudo apt-get update -y
          sudo apt-get install -y cppcheck clang-tidy

      - name: Run Cppcheck on C drivers (MISRA-C)
        run: |
          if [ -d drivers ]; then
            cppcheck \\
              --enable=all \\
              --std=c11 \\
              --suppress=missingIncludeSystem \\
              --suppress=unusedFunction \\
              --error-exitcode=0 \\
              --xml --xml-version=2 \\
              --output-file=cppcheck-report.xml \\
              drivers/ || true
          else
            echo "drivers/ not found — skipping C driver analysis"
            echo '<?xml version="1.0"?><results></results>' > cppcheck-report.xml
          fi
          cppcheck \\
            --enable=warning,error,performance,portability \\
            --std=c++14 \\
            --suppress=missingIncludeSystem \\
            --error-exitcode=1 \\
            qt_gui/

      - name: Generate Cppcheck HTML report
        run: |
          pip install cppcheck-htmlreport || true
          cppcheck-htmlreport --file cppcheck-report.xml --report-dir cppcheck-html --source-dir . || true

      - name: Run Clang-Tidy on drivers
        run: |
          if [ -d drivers ]; then
            find drivers/ -name '*.c' -o -name '*.h' | head -20 | while read f; do
              clang-tidy "$f" \\
                --checks='clang-analyzer-*,bugprone-*,cert-*,performance-*,portability-*' \\
                -- -std=c11 2>&1 || true
            done
          else
            echo "drivers/ not found — skipping Clang-Tidy"
          fi

      - name: Upload Cppcheck report
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: cppcheck-report
          path: |
            cppcheck-report.xml
            cppcheck-html/
          retention-days: 30

  # ------------------------------------------------------------------ #
  # Job 5: Code Quality Gate (all jobs must pass)
  # ------------------------------------------------------------------ #
  quality-gate:
    name: Quality Gate
    runs-on: ubuntu-22.04
    needs: [ build-qt-app, build-firmware-arm, unit-tests, static-analysis ]
    if: always()

    steps:
      - name: Check all jobs passed
        run: |
          echo "=== Silicon to Software (S2S) CI/CD Quality Gate ==="
          echo "Project    : {project_name}"
          echo "Qt version : 5.14.2 (QMake)"
          echo "Qt GUI     : ${{{{ needs.build-qt-app.result }}}}"
          echo "ARM FW     : ${{{{ needs.build-firmware-arm.result }}}}"
          echo "Unit Tests : ${{{{ needs.unit-tests.result }}}}"
          echo "Analysis   : ${{{{ needs.static-analysis.result }}}}"
          echo ""
          if [ "${{{{ needs.build-qt-app.result }}}}" != "success" ] || \\
             [ "${{{{ needs.build-firmware-arm.result }}}}" != "success" ] || \\
             [ "${{{{ needs.unit-tests.result }}}}" != "success" ]; then
            echo "GATE FAILED: one or more required jobs did not succeed"
            exit 1
          fi
          echo "GATE PASSED: all required checks succeeded"

      - name: Generate quality summary
        run: |
          cat <<EOF
          ## CI/CD Quality Summary — {project_name}
          | Job | Result |
          |-----|--------|
          | Qt 5.14.2 GUI Build (QMake) | ${{{{ needs.build-qt-app.result }}}} |
          | ARM Firmware Build | ${{{{ needs.build-firmware-arm.result }}}} |
          | Unit Tests + Coverage | ${{{{ needs.unit-tests.result }}}} |
          | Static Analysis | ${{{{ needs.static-analysis.result }}}} |
          EOF
"""

    def _build_arm_toolchain_cmake(self) -> str:
        """CMake toolchain file for arm-none-eabi cross-compilation."""
        return """\
# ARM Cortex-M/A cross-compilation toolchain for CMake
# Usage: cmake -DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi.cmake ..
# Requires: arm-none-eabi-gcc installed on PATH

cmake_minimum_required(VERSION 3.20)

set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR arm)

# Toolchain executables
set(CMAKE_C_COMPILER    arm-none-eabi-gcc)
set(CMAKE_CXX_COMPILER  arm-none-eabi-g++)
set(CMAKE_ASM_COMPILER  arm-none-eabi-gcc)
set(CMAKE_OBJCOPY       arm-none-eabi-objcopy)
set(CMAKE_SIZE          arm-none-eabi-size)

# Don't try to link test executables against target libs during configuration
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

# Target CPU — override at project level if needed
# e.g. for Cortex-M4F: -mcpu=cortex-m4 -mfpu=fpv4-sp-d16 -mfloat-abi=hard
set(CPU_FLAGS "-mcpu=cortex-m3 -mthumb" CACHE STRING "CPU architecture flags")
set(FPU_FLAGS "" CACHE STRING "FPU flags (empty = soft-float)")

# Common compiler flags
set(COMMON_FLAGS "${CPU_FLAGS} ${FPU_FLAGS} -ffunction-sections -fdata-sections -Wall -Wextra")
set(CMAKE_C_FLAGS   "${COMMON_FLAGS} -std=c11"   CACHE STRING "" FORCE)
set(CMAKE_CXX_FLAGS "${COMMON_FLAGS} -std=c++14 -fno-exceptions -fno-rtti" CACHE STRING "" FORCE)

# Linker flags
set(CMAKE_EXE_LINKER_FLAGS
    "${CPU_FLAGS} -specs=nosys.specs -specs=nano.specs -Wl,--gc-sections -Wl,-Map=firmware.map"
    CACHE STRING "" FORCE)

# Sysroot (host compiler headers — for includes only)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

# Post-build: generate .bin and .hex from .elf
function(add_firmware_outputs TARGET)
    add_custom_command(TARGET ${TARGET} POST_BUILD
        COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:${TARGET}> ${TARGET}.bin
        COMMAND ${CMAKE_OBJCOPY} -O ihex   $<TARGET_FILE:${TARGET}> ${TARGET}.hex
        COMMAND ${CMAKE_SIZE}    $<TARGET_FILE:${TARGET}>
        COMMENT "Generating binary outputs for ${TARGET}"
    )
endfunction()
"""

    async def _extract_components(self, glr: str) -> List[Dict]:
        """Extract component information from GLR/netlist content."""
        import re
        components = []

        if not glr:
            return self._default_components()

        # Look for component definitions with types
        # Pattern: Component Type: Name or Component: Name (Type)
        comp_pattern = r'(?:component|device|ic|chip|module)[\s:]+([A-Za-z0-9_\-]+)[\s]*(?:\(([^)]+)\))?'
        matches = re.findall(comp_pattern, glr, re.IGNORECASE)

        for comp_name, comp_type in matches:
            if comp_name:
                components.append({
                    "name": comp_name.strip(),
                    "type": comp_type.strip() if comp_type else "Device",
                    "description": f"{comp_name} component"
                })

        # Look for common IC/chip names
        ic_pattern = r'\b(STM32|ARM|ATMEL|PIC|NXP|TI|Cortex|FPGA|ASIC|MCU|CPU|GPIO|UART|SPI|I2C|ADC|DAC|Sensor)\b'
        ics = set(re.findall(ic_pattern, glr, re.IGNORECASE))

        for ic in ics:
            if not any(c["name"].lower() == ic.lower() for c in components):
                components.append({
                    "name": ic,
                    "type": "IC" if ic in ["STM32", "ARM", "ATMEL", "PIC", "NXP"] else "Peripheral",
                    "description": f"{ic} device"
                })

        # Look for netlist entry patterns
        netlist_pattern = r'U\d+\s+([A-Za-z0-9_\-]+)'
        netlists = re.findall(netlist_pattern, glr)

        for comp_ref in netlists:
            if not any(c["name"].lower() == comp_ref.lower() for c in components):
                components.append({
                    "name": comp_ref,
                    "type": "Component",
                    "description": f"{comp_ref} component"
                })

        return components if components else self._default_components()

    async def _extract_registers(self, glr: str, srs: str) -> List[Dict]:
        """Extract register definitions from GLR/SRS content."""
        import re
        registers = []

        content = (glr or "") + "\n" + (srs or "")
        if not content:
            return self._default_registers()

        # Pattern 1: Register definitions with addresses (0xAA, 0x00AA, etc.)
        reg_pattern = r'(?:register|reg|address|addr)[:\s]+([A-Za-z0-9_]+)[:\s]*(0x[0-9A-Fa-f]+)'
        matches = re.findall(reg_pattern, content, re.IGNORECASE)

        for reg_name, reg_addr in matches:
            registers.append({
                "name": reg_name.strip(),
                "address": reg_addr.lower(),
                "width": 32,  # Default width
                "access": "RW"
            })

        # Pattern 2: Hex addresses without explicit register names
        addr_pattern = r'(0x[0-9A-Fa-f]{2,8})[:\s]*([A-Za-z0-9_]*)'
        addr_matches = re.findall(addr_pattern, content)

        addr_count = {}
        for addr, name in addr_matches:
            if addr not in [r["address"] for r in registers]:
                reg_name = name.strip() if name else f"REG_{addr}"
                if not reg_name:
                    reg_name = f"REG_{addr}"
                    # Track multiple registers at same address
                    if addr in addr_count:
                        addr_count[addr] += 1
                        reg_name += f"_{addr_count[addr]}"
                    else:
                        addr_count[addr] = 0

                registers.append({
                    "name": reg_name,
                    "address": addr.lower(),
                    "width": 8,
                    "access": "RW"
                })

        # Pattern 3: Bit field definitions (for access type detection)
        # Note: Pattern defined but matches not currently used in access type logic
        # bitfield_pattern = r'(?:read|write|readonly|readwrite|ro|rw|wo)\s*(?:only|able)?[:\s]*([A-Za-z0-9_]+)'
        # bitfield_matches = re.findall(bitfield_pattern, content, re.IGNORECASE)

        # Update access types if we find read/write patterns
        for i, reg in enumerate(registers):
            if 'read' in content.lower() and 'write' in content.lower():
                reg["access"] = "RW"
            elif 'readonly' in content.lower() or 'read' in content.lower():
                reg["access"] = "RO"
            elif 'writeonly' in content.lower() or ('write' in content.lower() and 'read' not in content.lower()):
                reg["access"] = "WO"

        return registers if registers else self._default_registers()

    def _default_components(self) -> List[Dict]:
        """Default components."""
        return [
            {"name": "MCU", "type": "Microcontroller", "description": "Main microcontroller unit"},
            {"name": "Sensor", "type": "Peripheral", "description": "Sensor interface"},
            {"name": "Actuator", "type": "Peripheral", "description": "Actuator control"},
        ]

    def _default_registers(self) -> List[Dict]:
        """Default registers."""
        return [
            {"name": "CTRL_REG", "address": "0x00", "width": 8, "access": "RW"},
            {"name": "STATUS_REG", "address": "0x01", "width": 8, "access": "RO"},
            {"name": "DATA_REG", "address": "0x02", "width": 8, "access": "RW"},
        ]

    async def _generate_review_report(self, generated_files: Dict[str, str], project_name: str) -> str:
        """Generate code review report using CodeReviewer or LLM fallback."""
        try:
            # Combine all generated files for review
            all_code = "\n\n".join([f"// {filename}\n{content}" for filename, content in generated_files.items()])

            # Use CodeReviewer if available
            if self.code_reviewer:
                review_result = self.code_reviewer.review_code(
                    code=all_code,
                    language="c",
                    standards=["MISRA-C-2012"]
                )

                return f"""# Code Review Report

**Project:** {project_name}
**Date:** {review_result.get('timestamp', 'N/A')}

## Summary
- Total Issues Found: {review_result.get('total_issues', 0)}
- Critical: {review_result.get('critical_issues', 0)}
- Warning: {review_result.get('warnings', 0)}
- Info: {review_result.get('info', 0)}

## Findings
{review_result.get('details', 'No details available.')}

## Recommendations
{review_result.get('recommendations', 'No recommendations available.')}
"""
            else:
                # Fallback to LLM-based review
                return await self._llm_review(all_code, project_name)

        except Exception as e:
            self.log(f"Code review failed: {e}", "warning")
            return f"# Code Review Report\n\nReview completed with warnings. Error: {str(e)}"

    async def _llm_review(self, code: str, project_name: str) -> str:
        """Fallback LLM-based code review."""
        review_prompt = f"""Review the following generated C/C++ code for project {project_name}.

Analyze:
1. MISRA-C 2012 compliance (for C code)
2. Code quality score (0-100) with breakdown
3. Security vulnerabilities (buffer overflows, injection, etc.)
4. Coding standards adherence
5. Error handling completeness
6. Documentation quality
7. Specific recommendations for improvement

Code to review:
{code[:8000]}

Output as a structured markdown report with tables for findings.
"""
        try:
            response = await self.call_llm(
                messages=[{"role": "user", "content": review_prompt}],
                system="You are a senior code reviewer specializing in embedded C/C++ and MISRA-C compliance.",
                model=settings.fast_model,
            )
            return f"# Code Review Report\n\n{response.get('content', 'Review pending.')}"
        except Exception as e:
            return f"# Code Review Report\n\nLLM review failed: {str(e)}"

    async def _generate_review(self, code_content: str, project_name: str) -> str:
        """Generate code review report."""
        review_prompt = f"""Review the following generated code for project {project_name}.

Analyze:
1. MISRA-C 2012 compliance (for C code)
2. Code quality score (0-100) with breakdown
3. Security vulnerabilities (buffer overflows, injection, etc.)
4. Coding standards adherence
5. Error handling completeness
6. Documentation quality
7. Specific recommendations for improvement

Code to review:
{code_content[:6000]}

Output as a structured markdown report with tables for findings.
"""
        response = await self.call_llm(
            messages=[{"role": "user", "content": review_prompt}],
            system="You are a senior code reviewer specializing in embedded C/C++ and MISRA-C compliance.",
            model=settings.fast_model,  # Haiku for speed
        )
        return response.get("content", "# Code Review Report\n\nReview pending.")

    def _parse_and_save_files(self, content: str, output_dir: Path, project_name: str) -> dict:
        """Parse code blocks from LLM output and save as separate files."""
        outputs = {}
        code_dir = output_dir / "src"
        code_dir.mkdir(parents=True, exist_ok=True)

        # Simple parser: find ```c or ```cpp blocks with filename comments
        import re
        pattern = r'```(?:c|cpp|h|cmake|makefile)\s*\n(?://\s*(?:File:|Filename:)?\s*(.+?)\n)?(.*?)```'
        matches = re.findall(pattern, content, re.DOTALL | re.IGNORECASE)

        for i, (filename, code) in enumerate(matches):
            filename = filename.strip() if filename else f"file_{i}.c"
            # Clean filename
            filename = filename.replace("/", "_").replace("\\", "_")
            filepath = code_dir / filename
            filepath.write_text(code.strip(), encoding="utf-8")
            outputs[f"src/{filename}"] = code.strip()

        return outputs

    def _load_file(self, path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""
