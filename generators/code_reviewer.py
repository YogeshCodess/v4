"""Code Reviewer - Static analysis and MISRA-C checking."""

import logging
import re
from typing import Dict, List

logger = logging.getLogger(__name__)


class CodeReviewer:
    """Review generated code for quality and MISRA-C compliance."""

    def review(self, code: str, language: str = "c") -> Dict:
        findings = []
        findings.extend(self._check_misra_c(code))
        findings.extend(self._check_security(code))
        findings.extend(self._check_quality(code))
        score = self._calculate_score(findings, len(code.splitlines()))
        return {
            "language": language,
            "score": score,
            "findings": findings,
            "summary": self._generate_summary(findings, score),
        }

    def _check_misra_c(self, code: str) -> List[Dict]:
        findings = []
        if re.search(r'\b(malloc|calloc|realloc|free)\s*\(', code):
            findings.append({"rule": "MISRA-C 21.1", "severity": "warning", "message": "Dynamic allocation detected"})
        if re.search(r'\bgoto\s+', code):
            findings.append({"rule": "MISRA-C 15.1", "severity": "warning", "message": "goto statement detected"})
        return findings

    def _check_security(self, code: str) -> List[Dict]:
        findings = []
        unsafe = ['gets', 'strcpy', 'strcat', 'sprintf']
        for func in unsafe:
            if re.search(rf'\b{func}\s*\(', code):
                findings.append({"rule": "SECURITY", "severity": "error", "message": f"Unsafe function {func}"})
        return findings

    def _check_quality(self, code: str) -> List[Dict]:
        lines = code.splitlines()
        comments = sum(1 for line in lines if line.strip().startswith('//'))
        ratio = comments / max(len(lines), 1)
        findings = []
        if ratio < 0.1:
            findings.append({"rule": "QUALITY", "severity": "info", "message": "Low comment ratio"})
        return findings

    def _calculate_score(self, findings: List[Dict], line_count: int) -> int:
        score = 100
        for f in findings:
            if f["severity"] == "error":
                score -= 10
            elif f["severity"] == "warning":
                score -= 5
            elif f["severity"] == "info":
                score -= 1
        return max(0, score)

    def _generate_summary(self, findings: List[Dict], score: int) -> str:
        errors = sum(1 for f in findings if f["severity"] == "error")
        warnings = sum(1 for f in findings if f["severity"] == "warning")
        return f"""Code Review Summary
Status: {'PASS' if score >= 70 else 'REVIEW'}
Score: {score}/100
Errors: {errors}
Warnings: {warnings}
"""
