"""
Golden-regression harness.

Loads every YAML under tests/golden/<domain>/<id>.yaml, drives the cascade
validator and the standards-clause resolver against the expected values, and
asserts that:

  (a) the computed cascade noise figure is within the per-scenario tolerance,
  (b) every cited standard-clause pair resolves in the DB.

This is the single automated check that catches:
  - silent math regressions in tools/cascade_validator.py
  - accidentally renamed / dropped clauses in domains/standards.json
  - domain-question files drifting from the example bank

Run via `python -m pytest tests/test_golden.py -v` or `scripts/run_golden.py`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from domains.standards import validate_citations
from tools.cascade_validator import validate_cascade_from_dicts


_GOLDEN_DIR = Path(__file__).parent / "golden"


def _load_yaml(path: Path) -> dict:
    """Minimal YAML loader — avoids requiring PyYAML at install time."""
    try:
        import yaml  # type: ignore
        return yaml.safe_load(path.read_text())
    except ImportError:
        pass
    # Fall back to a very small subset parser that handles our schema only.
    return _mini_yaml(path.read_text())


def _mini_yaml(text: str) -> dict:
    """
    Sub-set YAML parser: supports key: value, nested mappings via indentation,
    inline flow mappings ({a: b, ...}) and flow lists ([a, b, ...]).
    Good enough for the golden scenarios in this repo; not a general YAML.
    """
    import ast, re

    def parse_scalar(s: str):
        s = s.strip()
        if not s:
            return None
        if s.startswith("[") or s.startswith("{"):
            # Replace YAML-ish bare keys/strings inside with Python literals.
            return _flow_to_python(s)
        if s.startswith('"') or s.startswith("'"):
            return ast.literal_eval(s)
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        if re.fullmatch(r"-?\d+\.\d+", s):
            return float(s)
        if s in ("true", "True"):
            return True
        if s in ("false", "False"):
            return False
        if s in ("null", "None", "~"):
            return None
        return s  # bare string

    def _flow_to_python(s: str):
        # Convert "{a: b, c: [x, y]}" into python literal via a small rewrite.
        # Quote any bare words except numbers.
        def repl(m):
            w = m.group(0)
            if re.fullmatch(r"-?\d+(\.\d+)?", w):
                return w
            if w in ("true", "True", "false", "False", "null", "None"):
                return {"true": "True", "True": "True",
                        "false": "False", "False": "False",
                        "null": "None", "None": "None"}[w]
            return f"'{w}'"
        pyish = re.sub(r"[A-Za-z_][A-Za-z0-9_\-+/\. ]*", repl, s)
        pyish = pyish.replace(":", ":")  # keep
        return ast.literal_eval(pyish)

    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    list_key = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if line.startswith("- "):
            # list item under the last-seen key
            item = line[2:].strip()
            val = parse_scalar(item)
            if isinstance(parent, dict) and list_key is not None:
                parent.setdefault(list_key, []).append(val)
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, rest = m.group(1), m.group(2)
        if rest == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
            list_key = key  # for lists directly under this key
        else:
            parent[key] = parse_scalar(rest)
    return root


def _all_scenarios() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    if not _GOLDEN_DIR.exists():
        return out
    for yml in sorted(_GOLDEN_DIR.rglob("*.yaml")):
        out.append((f"{yml.parent.name}/{yml.stem}", yml))
    return out


@pytest.mark.parametrize("scen_id,path", _all_scenarios(),
                         ids=[s[0] for s in _all_scenarios()] or ["none"])
def test_golden_scenario_cascade(scen_id: str, path: Path):
    data = _load_yaml(path)
    bom = data.get("bom", [])
    assert bom, f"{scen_id}: empty BOM"
    expected = data.get("expected_cascade", {})
    tol = float(expected.get("tolerance_db", 1.0))

    rep = validate_cascade_from_dicts(
        stages=bom,
        bandwidth_hz=1_000_000,
        snr_required_db=10.0,
        temperature_c=25.0,
    )

    if "noise_figure_db" in expected:
        delta = abs(rep.noise_figure_db - float(expected["noise_figure_db"]))
        assert delta <= tol, (
            f"{scen_id}: computed NF {rep.noise_figure_db:.2f} dB differs from "
            f"expected {expected['noise_figure_db']} by {delta:.2f} dB (> {tol})"
        )
    if "total_gain_db" in expected:
        delta = abs(rep.total_gain_db - float(expected["total_gain_db"]))
        assert delta <= tol, (
            f"{scen_id}: computed gain {rep.total_gain_db:.2f} dB differs from "
            f"expected {expected['total_gain_db']} by {delta:.2f} dB (> {tol})"
        )


@pytest.mark.parametrize("scen_id,path", _all_scenarios(),
                         ids=[s[0] for s in _all_scenarios()] or ["none"])
def test_golden_scenario_citations(scen_id: str, path: Path):
    data = _load_yaml(path)
    cites = data.get("expected_citations", [])
    pairs = [(c[0], c[1]) for c in cites]
    missing = validate_citations(pairs)
    assert not missing, f"{scen_id}: unresolved citations {missing}"
