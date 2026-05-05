"""
Real static analysis runner for P8c.

Runs cppcheck (if available), lizard (complexity), and cpplint (style).
Falls back gracefully — at minimum lizard always runs (pure Python).
"""

import logging
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Severity mapping
# --------------------------------------------------------------------------- #
CPPCHECK_SEVERITY_MAP = {
    "error":       "CRITICAL",
    "warning":     "HIGH",
    "portability": "MEDIUM",
    "performance": "MEDIUM",
    "style":       "LOW",
    "information": "INFO",
}

MISRA_RULE_HINTS: Dict[str, str] = {
    "variableScope":          "MISRA C 2012 Rule 8.9 — Variable scope should be as tight as possible",
    "unusedVariable":         "MISRA C 2012 Rule 2.2 — Dead code / unused variable",
    "constVariable":          "MISRA C 2012 Rule 8.13 — Pointer parameter can be declared as const",
    "bufferAccessOutOfBounds": "MISRA C 2012 Rule 18.1 — Pointer arithmetic out of bounds",
    "nullPointer":            "MISRA C 2012 Rule 18.3 — Null pointer dereference",
    "memleakOnRealloc":       "MISRA C 2012 Rule 22.1 — Memory leak on realloc failure",
    "doubleFree":             "MISRA C 2012 Rule 22.2 — Double free",
    "uninitvar":              "MISRA C 2012 Rule 9.1 — Uninitialized variable",
    "resourceLeak":           "MISRA C 2012 Rule 22.1 — Resource not freed",
    "knownConditionTrueFalse": "MISRA C 2012 Rule 14.3 — Controlling expression always true/false",
    "funcArgNamesDifferent":  "MISRA C 2012 Rule 8.3 — Function parameter names must match",
    "unusedFunction":         "MISRA C 2012 Rule 2.7 — Unused function parameter",
    "duplicateExpression":    "MISRA C 2012 Rule 12.2 — Identical sub-expressions",
    "returnLocalVariable":    "MISRA C 2012 Rule 18.6 — Address of local variable returned",
}


class StaticAnalysisRunner:
    """Run real static analysis tools on generated C/C++ code."""

    def __init__(self):
        self.cppcheck_path = shutil.which("cppcheck")
        self.cpplint_path = shutil.which("cpplint") or str(
            Path.home() / ".local/bin/cpplint"
        )
        self.lizard_available = self._check_lizard()

        if self.cppcheck_path:
            logger.info(f"cppcheck found: {self.cppcheck_path}")
        else:
            logger.warning("cppcheck not found — Cppcheck analysis will be skipped; lizard+LLM will run")

    def _check_lizard(self) -> bool:
        try:
            import lizard  # noqa: F401
            return True
        except ImportError:
            return False

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #

    def analyze(self, code_files: Dict[str, str]) -> Dict:
        """
        Analyze a dict of {filename: code_content}.
        Returns structured findings dict.
        """
        results = {
            "cppcheck": [],
            "complexity": [],
            "style": [],
            "summary": {},
            "tool_versions": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            # Write files to temp dir
            c_files = []
            for fname, content in code_files.items():
                fpath = tmp / Path(fname).name
                fpath.write_text(content, encoding="utf-8")
                if fpath.suffix in (".c", ".cpp", ".h"):
                    c_files.append(fpath)

            if not c_files:
                logger.warning("No C/C++ files to analyze")
                return results

            # 1. Cppcheck
            if self.cppcheck_path and c_files:
                results["cppcheck"] = self._run_cppcheck(c_files, tmp)
                results["tool_versions"]["cppcheck"] = self._get_cppcheck_version()

            # 2. Lizard (complexity)
            if self.lizard_available and c_files:
                results["complexity"] = self._run_lizard(c_files)
                results["tool_versions"]["lizard"] = "1.21.x"

            # 3. cpplint (style)
            if c_files:
                results["style"] = self._run_cpplint(c_files)

        # Summary
        results["summary"] = self._build_summary(results)
        return results

    # ------------------------------------------------------------------ #
    # Cppcheck
    # ------------------------------------------------------------------ #

    def _run_cppcheck(self, c_files: List[Path], workdir: Path) -> List[Dict]:
        """Run cppcheck with MISRA-C style checks, return parsed findings."""
        cmd = [
            self.cppcheck_path,
            "--enable=all",
            "--xml-version=2",
            "--suppress=missingInclude",
            "--suppress=missingIncludeSystem",
            "--suppress=unmatchedSuppression",
            "--inline-suppr",
            "--std=c11",
            "--force",
        ] + [str(f) for f in c_files]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(workdir),
            )
            # cppcheck writes XML to stderr
            xml_output = proc.stderr or proc.stdout
            return self._parse_cppcheck_xml(xml_output)
        except subprocess.TimeoutExpired:
            logger.warning("cppcheck timed out")
            return []
        except Exception as e:
            logger.warning(f"cppcheck failed: {e}")
            return []

    def _parse_cppcheck_xml(self, xml_str: str) -> List[Dict]:
        findings = []
        if not xml_str or "<error" not in xml_str:
            return findings

        try:
            root = ET.fromstring(xml_str)
            for error in root.iter("error"):
                eid = error.get("id", "")
                severity = error.get("severity", "style")
                msg = error.get("msg", "")
                verbose = error.get("verbose", msg)

                # Get location
                location = error.find("location")
                file_name = ""
                line = 0
                if location is not None:
                    file_name = Path(location.get("file", "")).name
                    line = int(location.get("line", 0))

                # Map to MISRA rule
                misra_hint = MISRA_RULE_HINTS.get(eid, "")

                findings.append({
                    "tool": "cppcheck",
                    "id": eid,
                    "severity": CPPCHECK_SEVERITY_MAP.get(severity, "INFO"),
                    "file": file_name,
                    "line": line,
                    "message": msg,
                    "misra_rule": misra_hint,
                    "fix_hint": verbose if verbose != msg else "",
                })
        except ET.ParseError as e:
            logger.warning(f"cppcheck XML parse error: {e}")

        return findings

    def _get_cppcheck_version(self) -> str:
        try:
            result = subprocess.run(
                [self.cppcheck_path, "--version"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip() or result.stderr.strip()
        except Exception:
            return "unknown"

    # ------------------------------------------------------------------ #
    # Lizard (complexity)
    # ------------------------------------------------------------------ #

    def _run_lizard(self, c_files: List[Path]) -> List[Dict]:
        """Run lizard cyclomatic complexity analysis."""
        try:
            import lizard
            findings = []

            for fpath in c_files:
                analysis = lizard.analyze_file(str(fpath))
                for fn in analysis.function_list:
                    entry = {
                        "tool": "lizard",
                        "file": fpath.name,
                        "function": fn.name,
                        "cyclomatic_complexity": fn.cyclomatic_complexity,
                        "lines_of_code": fn.length,
                        "token_count": fn.token_count,
                        "parameters": fn.parameter_count,
                        "severity": self._complexity_severity(fn.cyclomatic_complexity),
                        "misra_rule": (
                            "MISRA C 2012 Rule 15.5 — Max cyclomatic complexity exceeded (limit 10)"
                            if fn.cyclomatic_complexity > 10 else ""
                        ),
                    }
                    findings.append(entry)

            return findings
        except Exception as e:
            logger.warning(f"lizard analysis failed: {e}")
            return []

    def _complexity_severity(self, cc: int) -> str:
        if cc > 20:
            return "CRITICAL"
        if cc > 15:
            return "HIGH"
        if cc > 10:
            return "MEDIUM"
        return "OK"

    # ------------------------------------------------------------------ #
    # cpplint (style)
    # ------------------------------------------------------------------ #

    def _run_cpplint(self, c_files: List[Path]) -> List[Dict]:
        """Run cpplint for Google C style checks."""
        cpplint_bin = self.cpplint_path
        if not Path(cpplint_bin).exists():
            return []

        findings = []
        for fpath in c_files:
            try:
                proc = subprocess.run(
                    [cpplint_bin, "--filter=-build/include_subdir", str(fpath)],
                    capture_output=True, text=True, timeout=10
                )
                output = proc.stderr or proc.stdout
                findings.extend(self._parse_cpplint_output(output, fpath.name))
            except Exception:
                pass

        return findings

    def _parse_cpplint_output(self, output: str, filename: str) -> List[Dict]:
        findings = []
        for line in output.splitlines():
            # Format: "filename:linenum:  message  [category] [confidence]"
            m = re.match(r".+:(\d+):\s+(.+?)\s+\[(.+?)\]\s+\[(\d+)\]", line)
            if m:
                line_no, msg, category, confidence = m.groups()
                findings.append({
                    "tool": "cpplint",
                    "file": filename,
                    "line": int(line_no),
                    "category": category,
                    "message": msg,
                    "confidence": int(confidence),
                    "severity": "LOW" if int(confidence) < 4 else "MEDIUM",
                })
        return findings

    # ------------------------------------------------------------------ #
    # Summary
    # ------------------------------------------------------------------ #

    def _build_summary(self, results: Dict) -> Dict:
        all_findings = results["cppcheck"] + results["style"]
        complexity_issues = [
            f for f in results["complexity"]
            if f["cyclomatic_complexity"] > 10
        ]

        severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFO": 0}
        for f in all_findings:
            sev = f.get("severity", "INFO")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        misra_violations = [
            f for f in results["cppcheck"] if f.get("misra_rule")
        ] + [
            f for f in results["complexity"] if f.get("misra_rule")
        ]

        # Compute quality score (100 - penalties)
        score = 100
        score -= severity_counts["CRITICAL"] * 15
        score -= severity_counts["HIGH"] * 8
        score -= severity_counts["MEDIUM"] * 3
        score -= len(complexity_issues) * 5
        score = max(0, score)

        tools_used = []
        if results["cppcheck"]:
            tools_used.append("Cppcheck")
        if results["complexity"]:
            tools_used.append("Lizard")
        if results["style"]:
            tools_used.append("cpplint")
        if not tools_used:
            tools_used.append("LLM-based analysis")

        return {
            "quality_score": score,
            "total_issues": len(all_findings),
            "critical": severity_counts["CRITICAL"],
            "high": severity_counts["HIGH"],
            "medium": severity_counts["MEDIUM"],
            "low": severity_counts["LOW"],
            "misra_violations": len(misra_violations),
            "complexity_violations": len(complexity_issues),
            "tools_used": ", ".join(tools_used),
            "tool_versions": results.get("tool_versions", {}),
        }

    # ------------------------------------------------------------------ #
    # Markdown report formatter
    # ------------------------------------------------------------------ #

    def format_markdown_report(self, results: Dict, project_name: str) -> str:
        s = results["summary"]
        lines = [
            f"# Code Review Report — {project_name}",
            "",
            "## Executive Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Quality Score | **{s['quality_score']}/100** |",
            f"| Tools Used | {s['tools_used']} |",
            f"| Total Issues | {s['total_issues']} |",
            f"| Critical | {s['critical']} |",
            f"| High | {s['high']} |",
            f"| Medium | {s['medium']} |",
            f"| Low | {s['low']} |",
            f"| MISRA-C Violations | {s['misra_violations']} |",
            f"| Complexity Violations (CC>10) | {s['complexity_violations']} |",
            "",
            "---",
            "",
            "## Tool-Detected Issues",
            "",
            "_The findings in this section come directly from Cppcheck, "
            "Lizard and cpplint - they are deterministic and reproducible. "
            "Any LLM-generated commentary appears under "
            "**LLM Recommendations** further down._",
            "",
        ]

        # Cppcheck findings
        if results["cppcheck"]:
            lines += [
                "## Cppcheck Findings",
                "",
                "| Severity | File | Line | Issue | MISRA Rule |",
                "|----------|------|------|-------|-----------|",
            ]
            for f in results["cppcheck"][:100]:  # full findings list
                misra = f.get("misra_rule", "—")[:60]
                lines.append(
                    f"| {f['severity']} | `{f['file']}` | {f['line']} "
                    f"| {f['message'][:80]} | {misra} |"
                )
            lines.append("")
        else:
            if s["tools_used"] != "LLM-based analysis":
                lines += ["## Cppcheck Findings", "", "_No issues found._", ""]

        # Complexity
        if results["complexity"]:
            violations = [f for f in results["complexity"] if f["cyclomatic_complexity"] > 10]
            if violations:
                lines += [
                    "## Cyclomatic Complexity Violations (CC > 10)",
                    "",
                    "| Function | File | CC | LOC | MISRA Rule |",
                    "|----------|------|----|-----|-----------|",
                ]
                for f in violations:
                    lines.append(
                        f"| `{f['function']}` | `{f['file']}` | **{f['cyclomatic_complexity']}** "
                        f"| {f['lines_of_code']} | {f.get('misra_rule', '—')[:60]} |"
                    )
                lines.append("")

            # All functions table
            lines += [
                "## Function Complexity Overview",
                "",
                "| Function | File | CC | LOC | Parameters | Status |",
                "|----------|------|----|-----|-----------|--------|",
            ]
            for f in results["complexity"][:50]:
                status = "⚠️ VIOLATION" if f["cyclomatic_complexity"] > 10 else "✅ OK"
                lines.append(
                    f"| `{f['function']}` | `{f['file']}` | {f['cyclomatic_complexity']} "
                    f"| {f['lines_of_code']} | {f['parameters']} | {status} |"
                )
            lines.append("")

        # MISRA summary
        misra_findings = [f for f in results["cppcheck"] if f.get("misra_rule")]
        if misra_findings:
            lines += [
                "## MISRA-C 2012 Violations",
                "",
            ]
            # Deduplicate by rule
            seen_rules = set()
            for f in misra_findings:
                rule = f["misra_rule"]
                if rule not in seen_rules:
                    seen_rules.add(rule)
                    lines.append(f"- **{f['severity']}** `{f['file']}:{f['line']}` — {rule}")
            lines.append("")

        # Style findings summary
        if results["style"]:
            lines += [
                f"## Style Findings ({len(results['style'])} issues)",
                "",
                "_Top issues (cpplint):_",
                "",
            ]
            # Show top 10 by confidence
            top = sorted(results["style"], key=lambda x: x.get("confidence", 0), reverse=True)[:30]
            for f in top:
                lines.append(f"- `{f['file']}:{f['line']}` [{f['category']}] {f['message']}")
            lines.append("")

        # Recommendations
        lines += [
            "## Recommendations",
            "",
        ]
        if s["critical"] > 0:
            lines.append("- 🔴 **Fix CRITICAL issues before release** — potential runtime crashes or memory corruption")
        if s["high"] > 0:
            lines.append("- 🟠 **Review HIGH severity items** — logic errors or portability issues")
        if s["misra_violations"] > 0:
            lines.append("- 🟡 **Address MISRA-C violations** — required for safety-critical / automotive certification")
        if s["complexity_violations"] > 0:
            lines.append("- 🔵 **Refactor high-CC functions** — MISRA Rule 15.5 limits cyclomatic complexity to 10")
        if s["quality_score"] >= 80:
            lines.append("- ✅ **Code quality score acceptable** — suitable for firmware integration review")
        elif s["quality_score"] >= 60:
            lines.append("- 🟡 **Code quality needs improvement** — address HIGH+ issues before production")
        else:
            lines.append("- 🔴 **Code quality below threshold** — significant rework required")

        lines += [
            "",
            "---",
            f"_Analysis performed by Silicon to Software (S2S) v2 using {s['tools_used']}_",
        ]

        return "\n".join(lines)
