"""
Anti-hallucination regression tests — every agent must scrub TBD/TBC/TBA
placeholders out of its output.

Two complementary layers:

1. **Canonical regex** test — exercises the shared pattern
   `\\b(TBD|TBC|TBA)\\b` with `re.IGNORECASE` on a battery of positive /
   negative cases (word boundary, case folding, embedded substrings).

2. **Source presence** test — asserts each agent source file still contains
   the canonical regex. This is the cheap way to guarantee no future agent
   silently removes the scrubbing step. See CLAUDE.md Gotcha #9.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Canonical regex
# ---------------------------------------------------------------------------

SCRUB_RE = re.compile(r"\b(TBD|TBC|TBA)\b", flags=re.IGNORECASE)
REPLACEMENT = "[specify]"


def _scrub(text: str) -> str:
    return SCRUB_RE.sub(REPLACEMENT, text)


@pytest.mark.parametrize(
    "placeholder",
    ["TBD", "TBC", "TBA", "tbd", "Tbc", "tba"],
)
def test_scrub_replaces_standalone_placeholder(placeholder: str):
    assert _scrub(f"Gain: {placeholder} dB") == "Gain: [specify] dB"


def test_scrub_replaces_multiple_occurrences_in_one_line():
    result = _scrub("NF TBD, P1dB TBC, BW TBA")
    assert result == "NF [specify], P1dB [specify], BW [specify]"


def test_scrub_respects_word_boundary_embedded():
    """'TBDX' or 'MYTBD' must NOT be scrubbed — regex uses \\b."""
    assert _scrub("MYTBD") == "MYTBD"
    assert _scrub("TBDX") == "TBDX"
    assert _scrub("ATBAB") == "ATBAB"
    # Hyphen is a word-boundary delimiter so 'TBD-x' MUST still be scrubbed.
    assert _scrub("TBD-x") == "[specify]-x"


def test_scrub_handles_empty_and_whitespace_only():
    assert _scrub("") == ""
    assert _scrub("   \n\t   ") == "   \n\t   "


def test_scrub_preserves_non_placeholder_content():
    text = "# HRS\n\n- REQ-HW-001: gain 40 dB @ 2.4 GHz\n"
    assert _scrub(text) == text


def test_scrub_replaces_inside_markdown_table_cells():
    row = "| Vcc   | 3.3V | TBD | OK |"
    assert _scrub(row) == "| Vcc   | 3.3V | [specify] | OK |"


# ---------------------------------------------------------------------------
# Source-level presence — each agent must still emit the scrub pattern
# ---------------------------------------------------------------------------

AGENT_FILES = [
    "requirements_agent.py",
    "srs_agent.py",
    "sdd_agent.py",
    "netlist_agent.py",
    "glr_agent.py",
    "compliance_agent.py",
    "document_agent.py",
]

# Source patterns that must BOTH appear in each agent to guarantee the
# scrubber is still wired up. Tolerant to:
#   - TBA/TBC/TBD token ordering (agents spell them in different orders)
#   - arbitrary whitespace / newlines between the pattern and '[specify]'
#     (multi-line `re.sub(...)` calls are common)
_SCRUB_REGEX_SOURCE_RE = re.compile(
    r"""r['"]\\b\([A-Z|]{3,}\)\\b['"]""",
)
_TOKENS_IN_REGEX_RE = re.compile(r"""r['"]\\b\(([A-Z|]+)\)\\b['"]""")
_SPECIFY_REPLACEMENT_RE = re.compile(r"""['"]\[specify\]['"]""")

_AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"


@pytest.mark.parametrize("agent_file", AGENT_FILES)
def test_agent_source_contains_scrub_regex(agent_file: str):
    """If this fails, an agent removed the scrub step — regression risk."""
    source = (_AGENTS_DIR / agent_file).read_text(encoding="utf-8")

    # Must contain a raw regex `r'\b(TBD|TBC|TBA)\b'` (any token ordering)
    token_matches = _TOKENS_IN_REGEX_RE.findall(source)
    assert token_matches, (
        f"{agent_file} has no r'\\b(...)\\b' word-boundary regex literal"
    )
    # At least one of those regex literals must contain the 3 placeholders
    required = {"TBD", "TBC", "TBA"}
    found = False
    for alt in token_matches:
        tokens = set(alt.split("|"))
        if required.issubset(tokens):
            found = True
            break
    assert found, (
        f"{agent_file}: no r'\\b(...)\\b' regex covers all three of "
        "TBD, TBC, TBA — the anti-hallucination scrub is broken."
    )

    # Replacement literal must also appear (pairs the regex with [specify]).
    assert _SPECIFY_REPLACEMENT_RE.search(source), (
        f"{agent_file} is missing the '[specify]' replacement string"
    )


def test_all_agent_files_exist():
    """Catch typos in AGENT_FILES — keeps the parametrised test honest."""
    for name in AGENT_FILES:
        assert (_AGENTS_DIR / name).is_file(), f"Missing agent file: {name}"
