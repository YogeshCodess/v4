"""Unit tests for services.design_manifest — hash determinism + freeze contract."""
from __future__ import annotations

import json

import pytest

from services.design_manifest import (
    DesignManifest,
    compute_bom_hash,
    compute_manifest_hash,
    compute_requirements_hash,
    freeze,
    load_from_row,
    save_to_row,
    verify,
)


def _bom_row(pn: str, role: str = "lna", **extra) -> dict:
    return {"part_number": pn, "manufacturer": "ADI", "role": role, **extra}


# ---------------------------------------------------------------------------
# Hash determinism
# ---------------------------------------------------------------------------

class TestHashDeterminism:
    def test_requirements_hash_is_stable(self):
        h1 = compute_requirements_hash({"freq": "2-18 GHz"}, "superhet", "communication")
        h2 = compute_requirements_hash({"freq": "2-18 GHz"}, "superhet", "communication")
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex

    def test_requirements_hash_changes_with_content(self):
        h1 = compute_requirements_hash({"freq": "2-18 GHz"}, "superhet", "communication")
        h2 = compute_requirements_hash({"freq": "2-20 GHz"}, "superhet", "communication")
        assert h1 != h2

    def test_requirements_hash_changes_with_architecture(self):
        h1 = compute_requirements_hash({"freq": "2-18 GHz"}, "superhet", "communication")
        h2 = compute_requirements_hash({"freq": "2-18 GHz"}, "direct_rf", "communication")
        assert h1 != h2

    def test_bom_hash_strips_pricing(self):
        """Distributor price refresh must NOT mark phases stale.

        The BOM hash hashes only design-relevant fields (part_number, role,
        gain_db, etc.) — prices, stock counts, datasheet URLs are explicitly
        excluded so an hourly distributor refresh doesn't trigger a full
        downstream re-run.
        """
        a = [_bom_row("HMC8410", gain_db=18, unit_price_usd=12.50)]
        b = [_bom_row("HMC8410", gain_db=18, unit_price_usd=11.99)]  # price drop
        assert compute_bom_hash(a) == compute_bom_hash(b)

    def test_bom_hash_changes_when_part_swapped(self):
        a = [_bom_row("HMC8410", gain_db=18)]
        b = [_bom_row("HMC8411", gain_db=18)]
        assert compute_bom_hash(a) != compute_bom_hash(b)

    def test_bom_hash_normalises_part_number_aliases(self):
        """`primary_part` and `part_number` should hash the same."""
        a = [{"part_number": "HMC8410", "manufacturer": "ADI", "role": "lna"}]
        b = [{"primary_part": "HMC8410", "primary_manufacturer": "ADI", "role": "lna"}]
        assert compute_bom_hash(a) == compute_bom_hash(b)

    def test_bom_hash_is_order_sensitive(self):
        """Signal-chain order is part of the design — reorder = different hash."""
        a = [_bom_row("HMC8410", role="lna"), _bom_row("LT5560", role="mixer")]
        b = [_bom_row("LT5560", role="mixer"), _bom_row("HMC8410", role="lna")]
        assert compute_bom_hash(a) != compute_bom_hash(b)

    def test_manifest_hash_combines_all_three(self):
        req_h = compute_requirements_hash({}, "x", "communication")
        bom_h = compute_bom_hash([_bom_row("HMC8410")])
        h = compute_manifest_hash(req_h, bom_h, {"total_gain_db": 30})
        assert len(h) == 64
        # Changing design_parameters changes the manifest hash even with
        # the same req/bom.
        h2 = compute_manifest_hash(req_h, bom_h, {"total_gain_db": 35})
        assert h != h2


# ---------------------------------------------------------------------------
# Freeze contract
# ---------------------------------------------------------------------------

class TestFreeze:
    def _make(self) -> DesignManifest:
        return DesignManifest(
            project_id="42",
            requirements={"freq_range": "2-18 GHz", "nf_db": 3.0},
            architecture="superhet",
            design_parameters={"total_gain_db": 30},
            domain="communication",
            project_type="receiver",
            bom=[
                _bom_row("HMC8410", role="lna", gain_db=18, nf_db=1.2),
                _bom_row("LT5560", role="mixer", gain_db=-8, nf_db=10),
            ],
        )

    def test_freeze_populates_all_three_hashes(self):
        m = freeze(self._make())
        assert m.requirements_hash and len(m.requirements_hash) == 64
        assert m.bom_hash and len(m.bom_hash) == 64
        assert m.manifest_hash and len(m.manifest_hash) == 64
        assert m.frozen_at  # ISO-8601 timestamp

    def test_freeze_is_idempotent(self):
        """Freezing twice yields the same hashes (timestamp may drift)."""
        m1 = freeze(self._make())
        m2 = freeze(self._make())
        assert m1.requirements_hash == m2.requirements_hash
        assert m1.bom_hash == m2.bom_hash
        assert m1.manifest_hash == m2.manifest_hash

    def test_verify_after_freeze_returns_true(self):
        m = freeze(self._make())
        assert verify(m) is True

    def test_verify_after_mutation_returns_false(self):
        """If someone tampers with the BOM after freeze, verify catches it."""
        m = freeze(self._make())
        m.bom.append(_bom_row("INVENTED9999"))
        assert verify(m) is False

    def test_save_to_row_roundtrip(self):
        m = freeze(self._make())
        row = save_to_row(m)
        assert row["manifest_hash"] == m.manifest_hash
        # Round-trip the JSON blob through load_from_row and confirm
        # critical fields survive.
        m2 = load_from_row(row)
        assert m2 is not None
        assert m2.manifest_hash == m.manifest_hash
        assert m2.bom == m.bom
        assert m2.architecture == m.architecture

    def test_save_to_row_rejects_unfrozen(self):
        m = self._make()  # not frozen
        with pytest.raises(ValueError, match="frozen"):
            save_to_row(m)

    def test_load_from_row_returns_none_on_empty(self):
        assert load_from_row({"design_manifest_json": ""}) is None
        assert load_from_row({"design_manifest_json": None}) is None
        assert load_from_row({}) is None

    def test_load_from_row_returns_none_on_garbage(self):
        assert load_from_row({"design_manifest_json": "{not valid json"}) is None


# ---------------------------------------------------------------------------
# Allowed-MPN extraction
# ---------------------------------------------------------------------------

class TestAllowedMpns:
    def test_extracts_part_numbers(self):
        m = DesignManifest(
            project_id="1",
            bom=[_bom_row("hmc8410"), _bom_row("LT5560"), _bom_row("AD9082")],
        )
        assert m.allowed_mpns() == {"HMC8410", "LT5560", "AD9082"}

    def test_skips_rows_without_mpn(self):
        m = DesignManifest(
            project_id="1",
            bom=[_bom_row("HMC8410"), {"role": "lna", "manufacturer": "ADI"}],
        )
        assert m.allowed_mpns() == {"HMC8410"}

    def test_handles_mpn_aliases(self):
        m = DesignManifest(
            project_id="1",
            bom=[
                {"primary_part": "HMC8410"},
                {"mpn": "LT5560"},
                {"part_number": "AD9082"},
            ],
        )
        assert m.allowed_mpns() == {"HMC8410", "LT5560", "AD9082"}
