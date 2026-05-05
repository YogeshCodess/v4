"""
Requirements Lock — A1.1 (Workstream A / Technical).

Once the P1 agent has elicited all 4 rounds of requirements and the user has
explicitly confirmed them, we FREEZE the requirement set. Any downstream phase
(P2 HRS, P3 Compliance, P4 Netlist, P6 GLR, P8 SRS/SDD) MUST be generated against
the locked version. If the user modifies requirements later, we stamp a new
hash and mark every downstream phase as 'stale'.

This gives us:
  - reproducibility:   same hash + same model version = same output
  - auditability:      one SHA256 per requirement set, verifiable from DB
  - stale detection:   diff hash between run time and current requirements

Storage
-------
The lock itself is JSON-serializable — it is stored in the `projects` row
(columns: requirements_hash: TEXT, requirements_frozen_at: DATETIME,
requirements_locked_json: JSON).

Migration (run once when deploying this module):
    ALTER TABLE projects ADD COLUMN requirements_hash TEXT;
    ALTER TABLE projects ADD COLUMN requirements_frozen_at DATETIME;
    ALTER TABLE projects ADD COLUMN requirements_locked_json TEXT;   -- JSON
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class RequirementsLock:
    """Immutable snapshot of the confirmed requirements set."""

    # Mandatory identification
    project_id: str
    domain: str                      # radar / ew / satcom / communication
    schema_version: str = "1.0"

    # The actual requirements payload — keep as plain dict for hashability.
    requirements: dict[str, Any] = field(default_factory=dict)

    # Architecture choice from Round 2 (if applicable)
    architecture: Optional[str] = None

    # Per-round confirmations
    round1_confirmed: bool = False
    round2_confirmed: bool = False
    round3_confirmed: bool = False
    round4_confirmed: bool = False

    # Computed fields (filled by freeze())
    requirements_hash: Optional[str] = None
    frozen_at: Optional[str] = None        # ISO-8601 UTC
    llm_model: Optional[str] = None        # model used for elicitation
    llm_model_version: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RequirementsLock":
        return cls(**d)


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _canonical_json(obj: Any) -> str:
    """Stable JSON string for hashing — sorted keys, compact separators."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(lock: RequirementsLock) -> str:
    """
    SHA256 over the CONTENT of the requirements (not identity fields like
    project_id, or timestamps). Changing requirements.freq_range -> new hash.
    Changing project_id -> same hash.
    """
    content = {
        "schema_version": lock.schema_version,
        "domain": lock.domain,
        "requirements": lock.requirements,
        "architecture": lock.architecture,
    }
    blob = _canonical_json(content).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# Freeze / verify / diff
# ---------------------------------------------------------------------------

def freeze(
    lock: RequirementsLock,
    llm_model: Optional[str] = None,
    llm_model_version: Optional[str] = None,
) -> RequirementsLock:
    """
    Finalize a lock: all 4 rounds must be confirmed. Populates `requirements_hash`
    and `frozen_at`. Returns the same object mutated in place AND returned for chaining.
    """
    if not all([lock.round1_confirmed, lock.round2_confirmed,
                lock.round3_confirmed, lock.round4_confirmed]):
        missing = [f"round{i}" for i, ok in enumerate(
            [lock.round1_confirmed, lock.round2_confirmed,
             lock.round3_confirmed, lock.round4_confirmed], start=1) if not ok]
        raise ValueError(
            f"Cannot freeze requirements: rounds not confirmed: {missing}"
        )
    lock.requirements_hash = compute_hash(lock)
    lock.frozen_at = datetime.now(timezone.utc).isoformat()
    lock.llm_model = llm_model
    lock.llm_model_version = llm_model_version
    return lock


def verify(lock: RequirementsLock) -> bool:
    """Return True iff the stored hash matches the current content."""
    if lock.requirements_hash is None:
        return False
    return compute_hash(lock) == lock.requirements_hash


def is_stale(
    lock_at_run: RequirementsLock,
    current_lock: RequirementsLock,
) -> bool:
    """
    True if the requirements have changed since the referenced run was generated.
    Downstream phases (HRS/BOM/compliance/netlist) call this to decide whether
    to show a "⚠ stale — requirements changed" flag in the UI.
    """
    return lock_at_run.requirements_hash != current_lock.requirements_hash


# ---------------------------------------------------------------------------
# Persistence helpers (SQLite via raw SQL — keeps this module ORM-agnostic)
# ---------------------------------------------------------------------------

def save_to_row(lock: RequirementsLock) -> dict[str, Any]:
    """
    Serialize to the 3 project-row fields. Caller writes them into the DB:
        UPDATE projects SET
            requirements_hash = :h,
            requirements_frozen_at = :ts,
            requirements_locked_json = :payload
        WHERE id = :pid
    """
    if lock.requirements_hash is None:
        raise ValueError("Lock must be frozen before save_to_row().")
    return {
        "requirements_hash": lock.requirements_hash,
        "requirements_frozen_at": lock.frozen_at,
        "requirements_locked_json": _canonical_json(lock.to_dict()),
    }


def load_from_row(row: dict[str, Any]) -> Optional[RequirementsLock]:
    """Inverse of save_to_row. Returns None if the row has no lock stored."""
    payload = row.get("requirements_locked_json")
    if not payload:
        return None
    data = json.loads(payload)
    return RequirementsLock.from_dict(data)
