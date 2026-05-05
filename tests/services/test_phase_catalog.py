"""Single-source-of-truth invariants for `services.phase_catalog`.

These tests pin the contract that `pipeline_service`, `project_service`,
and `stale_phases` all derive their phase lists from `phase_catalog`.
When someone adds or renames a phase, only `phase_catalog.AUTO_PHASE_SPECS`
should need editing — any drift caught here means a service has started
hard-coding its own copy again and the P7-style bug (P7 runs but isn't
reset downstream) can silently return.
"""
from __future__ import annotations

from services.phase_catalog import (
    AUTO_PHASE_IDS,
    AUTO_PHASE_SPECS,
    DOWNSTREAM_OF_P1,
    MANUAL_PHASE_IDS,
)


class TestCatalogShape:
    def test_auto_phase_ids_derived_from_specs(self):
        assert AUTO_PHASE_IDS == tuple(spec[0] for spec in AUTO_PHASE_SPECS)

    def test_every_spec_is_a_4_tuple(self):
        for spec in AUTO_PHASE_SPECS:
            assert len(spec) == 4, f"{spec!r} should be (id, module, cls, name)"
            assert all(isinstance(x, str) for x in spec)

    def test_no_phase_id_duplicates(self):
        ids = [spec[0] for spec in AUTO_PHASE_SPECS]
        assert len(ids) == len(set(ids))

    def test_p1_not_in_auto_phases(self):
        # P1 is owned by the chat flow, not the pipeline.
        assert "P1" not in AUTO_PHASE_IDS

    def test_p5_is_manual_not_auto(self):
        assert "P5" in MANUAL_PHASE_IDS
        assert "P5" not in AUTO_PHASE_IDS

    def test_p7_is_auto_not_manual(self):
        # Bug fix regression: P7 used to be manual (treated as a human
        # FPGA task). Now it runs via FpgaAgent and must be tracked as
        # an AI phase so P1 re-locks reset it.
        assert "P7" in AUTO_PHASE_IDS
        assert "P7" not in MANUAL_PHASE_IDS

    def test_p7a_present(self):
        # Sister phase to P7 — register-map + programming sequence gets
        # auto-generated as an AI phase too.
        assert "P7a" in AUTO_PHASE_IDS

    def test_downstream_covers_every_auto_phase(self):
        # Every AI phase is downstream of P1 today. If that ever stops
        # being true (e.g. a P8d that only depends on P7), this test
        # should be split rather than quietly weakened.
        assert set(DOWNSTREAM_OF_P1) == set(AUTO_PHASE_IDS)


class TestDownstreamServicesAgree:
    """Any service that re-exposes the catalog must stay in sync with
    the authoritative tuples here. Drift caused the original P7 bug."""

    def test_pipeline_service_matches_catalog(self):
        from services.pipeline_service import AUTO_PHASES
        assert tuple(AUTO_PHASES) == AUTO_PHASE_SPECS

    def test_project_service_downstream_matches_catalog(self):
        from services.project_service import (
            _DOWNSTREAM_AI_PHASES as module_tuple,
            ProjectService,
        )
        assert tuple(module_tuple) == DOWNSTREAM_OF_P1
        assert tuple(ProjectService._DOWNSTREAM_AI_PHASES) == DOWNSTREAM_OF_P1

    def test_stale_phases_ai_list_includes_p1_plus_catalog(self):
        from services.stale_phases import AI_PHASES, MANUAL_PHASES
        assert AI_PHASES == ("P1",) + AUTO_PHASE_IDS
        assert MANUAL_PHASES == MANUAL_PHASE_IDS

    def test_stale_phases_manual_and_ai_are_disjoint(self):
        from services.stale_phases import AI_PHASES, MANUAL_PHASES
        assert set(AI_PHASES).isdisjoint(set(MANUAL_PHASES))


class TestP1ResetIncludesP7:
    """End-to-end: when P1 is re-completed, P7 must be reset to pending
    alongside every other downstream phase. This is the original bug the
    catalog refactor fixes."""

    def test_downstream_reset_list_includes_p7(self):
        from services.project_service import ProjectService
        assert "P7" in ProjectService._DOWNSTREAM_AI_PHASES

    def test_downstream_reset_list_includes_p7a(self):
        from services.project_service import ProjectService
        assert "P7a" in ProjectService._DOWNSTREAM_AI_PHASES
