"""Tests for services/requirements_lock.py."""
from __future__ import annotations

import pytest

from services.requirements_lock import (
    RequirementsLock,
    compute_hash,
    freeze,
    is_stale,
    load_from_row,
    save_to_row,
    verify,
)


def _demo_lock(project_id: str = "p1") -> RequirementsLock:
    return RequirementsLock(
        project_id=project_id,
        domain="radar",
        requirements={
            "freq_band": "X",
            "inst_bw_mhz": 500,
            "noise_figure_db": 3.0,
            "sensitivity_dbm": -100.0,
        },
        architecture="superheterodyne",
        round1_confirmed=True,
        round2_confirmed=True,
        round3_confirmed=True,
        round4_confirmed=True,
    )


def test_freeze_populates_hash_and_timestamp():
    lk = freeze(_demo_lock(), llm_model="glm-4.7", llm_model_version="2025-10")
    assert lk.requirements_hash is not None
    assert len(lk.requirements_hash) == 64  # SHA256 hex
    assert lk.frozen_at is not None
    assert lk.llm_model == "glm-4.7"


def test_freeze_rejects_unconfirmed_rounds():
    lk = _demo_lock()
    lk.round3_confirmed = False
    with pytest.raises(ValueError):
        freeze(lk)


def test_same_content_same_hash():
    h1 = compute_hash(_demo_lock("a"))
    h2 = compute_hash(_demo_lock("b"))  # different project_id
    # project_id is NOT part of the content hash.
    assert h1 == h2


def test_changing_a_requirement_changes_hash():
    a = _demo_lock()
    b = _demo_lock()
    b.requirements["noise_figure_db"] = 2.5
    assert compute_hash(a) != compute_hash(b)


def test_verify_detects_tampering():
    lk = freeze(_demo_lock())
    assert verify(lk) is True
    lk.requirements["noise_figure_db"] = 2.0
    assert verify(lk) is False


def test_is_stale_detects_changed_requirements():
    frozen_run = freeze(_demo_lock())
    current = _demo_lock()
    current.requirements["inst_bw_mhz"] = 1000
    current = freeze(current)
    assert is_stale(frozen_run, current) is True


def test_roundtrip_save_load():
    lk = freeze(_demo_lock())
    row = save_to_row(lk)
    recovered = load_from_row(row)
    assert recovered is not None
    assert recovered.requirements_hash == lk.requirements_hash
    assert recovered.requirements == lk.requirements
    assert recovered.domain == lk.domain


def test_load_from_empty_row_returns_none():
    assert load_from_row({}) is None
    assert load_from_row({"requirements_locked_json": None}) is None


def test_save_unfrozen_lock_raises():
    lk = _demo_lock()
    lk.round1_confirmed = False  # won't be frozen, so no hash
    with pytest.raises(ValueError):
        save_to_row(lk)
