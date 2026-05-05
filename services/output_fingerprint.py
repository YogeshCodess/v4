"""
Structural fingerprint of code-generation output - the anti-repetition gate.

Use case: a real bug we observed was the LLM (or skeleton fallback)
producing the same hal.h / fpga_top.v across totally different projects.
The user couldn't tell because the files looked plausible. This module
computes a stable hash over the structural distinguishers of an output
bundle (peripheral function names, register addresses, FSM names, file
sizes by suffix). Two projects with different requirements should
produce different fingerprints; a regression that reintroduces
genericity will produce a collision the audit can flag.

Usage:
    from services.output_fingerprint import (
        compute_fingerprint, record_fingerprint, find_collisions,
    )
    fp = compute_fingerprint(output_dir)
    record_fingerprint(project_id, phase_id, fp)
    collisions = find_collisions(fp, exclude_project_id=project_id)
    if collisions:
        log.warning("output.fingerprint_collision: %s", collisions)
"""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fingerprint computation
# ---------------------------------------------------------------------------

_FUNC_DECL_RE = re.compile(
    r"^(?:hal_status_t|void|int|uint8_t|uint16_t|uint32_t)\s+(\w+)\s*\(",
    re.MULTILINE,
)
_VERILOG_MODULE_RE = re.compile(r"^\s*module\s+(\w+)", re.MULTILINE)
_VHDL_ENTITY_RE    = re.compile(r"^\s*entity\s+(\w+)\s+is", re.MULTILINE | re.IGNORECASE)
_REG_ADDR_RE       = re.compile(r"`?0x[0-9A-Fa-f]{2,4}`?\s*\|?\s*`?[A-Z][A-Z0-9_]+`?")
_FSM_NAME_RE       = re.compile(r"\b([A-Z][A-Z0-9_]{2,})_FSM\b")


def _extract_distinguishers(output_dir: Path) -> dict:
    """Pull the stable structural facts from a generated-output directory.

    We deliberately ignore comments, whitespace, doc strings - those
    flap on cosmetic edits without indicating a genericity regression.
    """
    funcs: set[str] = set()
    modules: set[str] = set()
    entities: set[str] = set()
    fsms: set[str] = set()
    addrs: set[str] = set()
    file_sizes: dict[str, int] = {}

    if not output_dir.exists():
        return {"functions": [], "modules": [], "entities": [],
                "fsms": [], "register_addresses": [], "file_sizes": {}}

    for f in output_dir.rglob("*"):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_sizes[ext] = file_sizes.get(ext, 0) + len(content)

        if ext in {".c", ".h", ".cpp", ".hpp"}:
            for m in _FUNC_DECL_RE.finditer(content):
                fn = m.group(1)
                # Skip BSP stubs - they're identical across projects.
                if fn.startswith("_uart_") or fn.startswith("_bsp_"):
                    continue
                funcs.add(fn)
        elif ext in {".v", ".sv"}:
            for m in _VERILOG_MODULE_RE.finditer(content):
                modules.add(m.group(1))
        elif ext == ".vhd":
            for m in _VHDL_ENTITY_RE.finditer(content):
                entities.add(m.group(1))

        if ext in {".v", ".sv", ".vhd", ".md"}:
            for m in _FSM_NAME_RE.finditer(content):
                fsms.add(m.group(1))

        if ext == ".md" and "register" in f.name.lower():
            for m in _REG_ADDR_RE.finditer(content):
                tok = m.group(0)
                addr_m = re.search(r"0x[0-9A-Fa-f]{2,4}", tok)
                if addr_m:
                    addrs.add(addr_m.group(0).lower())

    return {
        "functions":          sorted(funcs),
        "modules":            sorted(modules),
        "entities":           sorted(entities),
        "fsms":               sorted(fsms),
        "register_addresses": sorted(addrs),
        "file_sizes":         {k: v for k, v in file_sizes.items() if v > 0},
    }


def compute_fingerprint(output_dir: Path | str) -> str:
    """Stable 16-char hex hash over the distinguishers in `output_dir`."""
    p = Path(output_dir) if not isinstance(output_dir, Path) else output_dir
    d = _extract_distinguishers(p)
    canonical = (
        "F:" + ",".join(d["functions"]) + "|"
        "M:" + ",".join(d["modules"]) + "|"
        "E:" + ",".join(d["entities"]) + "|"
        "S:" + ",".join(d["fsms"]) + "|"
        "R:" + ",".join(d["register_addresses"]) + "|"
        "Z:" + ",".join(f"{k}={v//100}" for k, v in sorted(d["file_sizes"].items()))
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Persistence + collision detection
# ---------------------------------------------------------------------------


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS output_fingerprints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            phase_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, phase_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_fingerprint "
        "ON output_fingerprints(fingerprint)"
    )


def _resolve_db() -> Optional[str]:
    try:
        from config import settings
    except Exception:
        return None
    url = getattr(settings, "database_url", "")
    if not url.startswith("sqlite:///"):
        return None
    p = url[len("sqlite:///"):]
    if p.startswith("./"):
        import os
        p = os.path.join(os.getcwd(), p[2:])
    return p


def record_fingerprint(project_id: int, phase_id: str, fingerprint: str) -> bool:
    """Persist a fingerprint for (project, phase). Returns True on success."""
    db = _resolve_db()
    if not db:
        return False
    try:
        conn = sqlite3.connect(db, timeout=5.0)
        _ensure_table(conn)
        conn.execute(
            "INSERT OR REPLACE INTO output_fingerprints "
            "(project_id, phase_id, fingerprint) VALUES (?, ?, ?)",
            (project_id, phase_id, fingerprint),
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.Error as e:
        log.debug("fingerprint.record_failed: %s", e)
        return False


def find_collisions(
    fingerprint: str,
    *,
    exclude_project_id: Optional[int] = None,
) -> list[dict]:
    """Return rows from output_fingerprints that share `fingerprint`.

    Used by the audit gate: a non-empty list means another project
    produced byte-equivalent output, which is the genericity bug.
    """
    db = _resolve_db()
    if not db:
        return []
    try:
        conn = sqlite3.connect(db)
        _ensure_table(conn)
        conn.row_factory = sqlite3.Row
        if exclude_project_id is not None:
            rows = conn.execute(
                "SELECT project_id, phase_id, fingerprint, recorded_at "
                "FROM output_fingerprints "
                "WHERE fingerprint = ? AND project_id != ?",
                (fingerprint, exclude_project_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT project_id, phase_id, fingerprint, recorded_at "
                "FROM output_fingerprints WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error as e:
        log.debug("fingerprint.find_collisions_failed: %s", e)
        return []
