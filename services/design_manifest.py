"""
DesignManifest — single source of truth for P1 outputs (Step 1).

Replaces / extends the original RequirementsLock (which only froze the spec
text + architecture choice) with a manifest that ALSO freezes the post-audit
BOM, the cascade summary, and the audit verdict. Every downstream artifact —
requirements.md, component_recommendations.md, gain_loss_budget.md,
block_diagram.md, HRS, GLR, netlist — is now a deterministic projection of
this single dict, and a leak detector (services/manifest_validator.py)
catches any artifact that mentions an MPN not present in `manifest.bom`.

Why this exists
---------------
The hackathon-final bug: the LLM emitted four parallel BOM-shaped fields
(`component_recommendations`, `gain_loss_budget.stages`, `requirements`
prose, mermaid labels). The audit only mutated the first one, so banned
parts were stripped from one file but survived in three others. The
manifest collapses those four shapes back to ONE — `manifest.bom` —
and every other artifact must derive from it.

Storage
-------
Persisted as JSON in `projects.design_manifest_json` (added by migration
008). The `manifest_hash` field is duplicated into `projects.manifest_hash`
for cheap stale-detection without parsing the JSON blob.

Hashes
------
Three nested SHA256s, in order of granularity:
  - requirements_hash : SHA256(requirements + architecture + domain)
                        Same as RequirementsLock.requirements_hash, kept
                        for backward compatibility with the existing
                        stale-phase detector.
  - bom_hash          : SHA256(canonicalised BOM rows — design-relevant
                        fields only, no prices / URLs / timestamps so a
                        distributor price refresh does NOT mark phases
                        stale).
  - manifest_hash     : SHA256(requirements_hash + bom_hash +
                        canonicalised design_parameters).
                        This is the hash downstream phases compare
                        against.

Backward compatibility
----------------------
RequirementsLock continues to exist and is still saved to the legacy
columns (requirements_hash / requirements_frozen_at /
requirements_locked_json). The manifest is purely additive — phases that
haven't been migrated to consume it keep working off the file system.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Canonical BOM-row projection
# ---------------------------------------------------------------------------

# Fields that participate in the BOM hash. Anything NOT in this list (prices,
# stock counts, distributor source, datasheet URL, hallucinated marker, etc.)
# is metadata that can change without invalidating the design — a
# distributor price refresh should not mark every downstream phase stale.
_BOM_HASHABLE_FIELDS: tuple[str, ...] = (
    "part_number",
    "manufacturer",
    "role",
    "kind",
    "qty",
    "package",
    "gain_db",
    "nf_db",
    "iip3_dbm",
    "p1db_dbm",
    "pout_dbm",
    "supply_voltage",
    "current_ma",
)


def _bom_row_for_hash(row: dict[str, Any]) -> dict[str, Any]:
    """Project a BOM row down to the design-relevant subset for hashing."""
    out: dict[str, Any] = {}
    # Resolve aliases (LLM ships both `part_number` and `primary_part`)
    pn = row.get("part_number") or row.get("primary_part") or row.get("mpn")
    mfr = row.get("manufacturer") or row.get("primary_manufacturer") or row.get("vendor")
    role = row.get("role") or row.get("kind") or row.get("name")
    if pn:
        out["part_number"] = str(pn).strip().upper()
    if mfr:
        out["manufacturer"] = str(mfr).strip()
    if role:
        out["role"] = str(role).strip().lower()
    for k in _BOM_HASHABLE_FIELDS:
        if k in {"part_number", "manufacturer", "role"}:
            continue  # already handled above
        if k in row and row[k] is not None:
            out[k] = row[k]
    return out


def _canonical_json(obj: Any) -> str:
    """Stable JSON string for hashing — sorted keys, compact separators."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str,
    )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DesignManifest:
    """Frozen post-audit snapshot of P1 outputs.

    Every downstream artifact MUST be derivable from this dict alone.
    """

    # Identity
    project_id: str
    schema_version: str = "2.0"

    # Wizard-captured inputs
    requirements: dict[str, Any] = field(default_factory=dict)
    architecture: Optional[str] = None
    design_parameters: dict[str, Any] = field(default_factory=dict)
    domain: str = "communication"
    project_type: str = "receiver"

    # The canonical BOM — single source of truth for component MPNs.
    # Post-audit: banned/EOL parts removed, distributor-canonicalised
    # manufacturer + datasheet URL, _hallucinated marker on unknowns.
    bom: list[dict[str, Any]] = field(default_factory=list)

    # Computed cascade summary (totals + verdict). Pure derivation from
    # `bom` — included here so downstream phases don't have to recompute.
    cascade_summary: dict[str, Any] = field(default_factory=dict)

    # Audit verdict
    audit_pass: bool = True
    audit_blocker_count: int = 0
    audit_issue_count: int = 0

    # Hashes (computed by freeze())
    requirements_hash: Optional[str] = None
    bom_hash: Optional[str] = None
    manifest_hash: Optional[str] = None

    # Metadata
    frozen_at: Optional[str] = None
    llm_model: Optional[str] = None
    llm_model_version: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        """Canonical JSON for the DB column."""
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DesignManifest":
        # Tolerate unknown fields (forward-compat with future schema versions)
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in d.items() if k in known}
        return cls(**clean)

    @classmethod
    def from_json(cls, blob: str) -> Optional["DesignManifest"]:
        if not blob:
            return None
        try:
            return cls.from_dict(json.loads(blob))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def allowed_mpns(self) -> set[str]:
        """The canonical set of MPNs the leak detector accepts. Uppercase
        for case-insensitive comparison."""
        out: set[str] = set()
        for row in self.bom or []:
            pn = row.get("part_number") or row.get("primary_part") or row.get("mpn")
            if pn:
                out.add(str(pn).strip().upper())
        return out


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def compute_requirements_hash(
    requirements: dict[str, Any],
    architecture: Optional[str],
    domain: str,
) -> str:
    """SHA256 over the spec inputs only. Matches the legacy
    RequirementsLock hash for backward compatibility with the existing
    stale-phase detector."""
    content = {
        "requirements": requirements or {},
        "architecture": architecture,
        "domain": domain,
    }
    return hashlib.sha256(_canonical_json(content).encode("utf-8")).hexdigest()


def compute_bom_hash(bom: list[dict[str, Any]]) -> str:
    """SHA256 over the canonicalised BOM. Order-sensitive (signal-chain
    order is part of the design)."""
    rows = [_bom_row_for_hash(r) for r in (bom or [])]
    return hashlib.sha256(_canonical_json(rows).encode("utf-8")).hexdigest()


def compute_manifest_hash(
    requirements_hash: str,
    bom_hash: str,
    design_parameters: dict[str, Any],
) -> str:
    """Top-level hash — covers everything that determines downstream output."""
    content = {
        "req": requirements_hash,
        "bom": bom_hash,
        "dp": design_parameters or {},
    }
    return hashlib.sha256(_canonical_json(content).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Freeze
# ---------------------------------------------------------------------------

def freeze(
    manifest: DesignManifest,
    *,
    llm_model: Optional[str] = None,
    llm_model_version: Optional[str] = None,
) -> DesignManifest:
    """Compute all hashes + frozen_at timestamp. Mutates in place AND
    returns the same object for chaining."""
    manifest.requirements_hash = compute_requirements_hash(
        manifest.requirements, manifest.architecture, manifest.domain,
    )
    manifest.bom_hash = compute_bom_hash(manifest.bom)
    manifest.manifest_hash = compute_manifest_hash(
        manifest.requirements_hash,
        manifest.bom_hash,
        manifest.design_parameters,
    )
    manifest.frozen_at = datetime.now(timezone.utc).isoformat()
    if llm_model:
        manifest.llm_model = llm_model
    if llm_model_version:
        manifest.llm_model_version = llm_model_version
    return manifest


def verify(manifest: DesignManifest) -> bool:
    """Return True iff the stored hashes match the current content."""
    if not manifest.manifest_hash:
        return False
    expected_req = compute_requirements_hash(
        manifest.requirements, manifest.architecture, manifest.domain,
    )
    expected_bom = compute_bom_hash(manifest.bom)
    expected_full = compute_manifest_hash(
        expected_req, expected_bom, manifest.design_parameters,
    )
    return expected_full == manifest.manifest_hash


# ---------------------------------------------------------------------------
# Persistence helpers (caller writes them onto the DB row)
# ---------------------------------------------------------------------------

def save_to_row(manifest: DesignManifest) -> dict[str, Any]:
    """Serialise to the projects-row columns added by migration 008.

    Caller writes them with:
        UPDATE projects SET
            design_manifest_json = :payload,
            manifest_hash = :h
        WHERE id = :pid
    """
    if not manifest.manifest_hash:
        raise ValueError("Manifest must be frozen before save_to_row().")
    return {
        "design_manifest_json": manifest.to_json(),
        "manifest_hash": manifest.manifest_hash,
    }


def load_from_row(row: dict[str, Any]) -> Optional[DesignManifest]:
    """Inverse of save_to_row. Returns None if the row has no manifest."""
    blob = row.get("design_manifest_json")
    if not blob:
        return None
    return DesignManifest.from_json(blob if isinstance(blob, str) else _canonical_json(blob))
