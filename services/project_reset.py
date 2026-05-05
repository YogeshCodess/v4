"""
E1 — pure helpers for Judge-mode "wipe project state".

`ProjectService.reset_state()` is the DB-touching caller. The logic in this
module is pure (takes / returns plain dicts) so we can unit-test it in the
stdlib-only harness without standing up SQLAlchemy.

Contract
--------

``reset_payload(project_row)``
    Given a mapping with any of the resettable columns, return a new
    dictionary that represents the same project with every mutable column
    wiped back to a fresh default:

      - ``phase_statuses``           → ``{}``
      - ``conversation_history``     → ``[]``
      - ``design_parameters``        → ``{}``
      - ``requirements_hash``        → ``None``
      - ``requirements_frozen_at``   → ``None``
      - ``requirements_locked_json`` → ``None``
      - ``current_phase``            → ``"P1"``

    Identity fields (``id``, ``name``, ``description``, ``design_type``,
    ``output_dir``, ``created_at``) are preserved exactly. Unknown keys are
    forwarded through untouched.

``summarise_reset(before, after)``
    Diff the two dicts and return a tiny human-readable summary that the
    FastAPI handler and the JudgeMode overlay can display without touching
    the DB twice.
"""
from __future__ import annotations

from typing import Any, Mapping


RESETTABLE_COLUMNS: tuple[str, ...] = (
    "phase_statuses",
    "conversation_history",
    "design_parameters",
    "requirements_hash",
    "requirements_frozen_at",
    "requirements_locked_json",
)

IDENTITY_COLUMNS: tuple[str, ...] = (
    "id",
    "name",
    "description",
    "design_type",
    "output_dir",
    "created_at",
)

_DEFAULTS: dict[str, Any] = {
    "phase_statuses": lambda: {},
    "conversation_history": lambda: [],
    "design_parameters": lambda: {},
    "requirements_hash": lambda: None,
    "requirements_frozen_at": lambda: None,
    "requirements_locked_json": lambda: None,
}


def reset_payload(project_row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of ``project_row`` with every resettable column cleared.

    The input is treated read-only. Unknown keys are copied through so a
    caller that passes a subset of the schema still gets back a sensible
    dict. ``current_phase`` is reset to ``"P1"``.
    """
    if project_row is None:
        raise ValueError("project_row must not be None")

    out: dict[str, Any] = dict(project_row)  # shallow copy; we never mutate
    for col in RESETTABLE_COLUMNS:
        out[col] = _DEFAULTS[col]()
    # Pipeline always restarts from P1 after a reset — this is the header
    # the UI sticky-topbar relies on.
    out["current_phase"] = "P1"
    return out


def summarise_reset(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a compact summary of what a reset cleared.

    Shape::

        {
          "cleared_columns": ["phase_statuses", ...],
          "was_non_empty": True,
          "counts": {
              "phase_statuses": 8,
              "conversation_history": 42,
              ...
          },
          "current_phase": "P1",
        }

    ``was_non_empty`` is ``True`` if the project had ANY non-default data
    prior to the reset — useful for the UI to decide whether to announce
    "project already clean" vs "cleared N phase statuses".
    """
    counts: dict[str, int] = {}
    had_lock = bool(before.get("requirements_hash"))

    for col in ("phase_statuses", "conversation_history", "design_parameters"):
        val = before.get(col) or ({} if col != "conversation_history" else [])
        try:
            n = len(val)
        except TypeError:
            n = 0
        counts[col] = n

    was_non_empty = had_lock or any(n > 0 for n in counts.values())

    return {
        "cleared_columns": list(RESETTABLE_COLUMNS),
        "was_non_empty": was_non_empty,
        "counts": counts,
        "current_phase": after.get("current_phase", "P1"),
    }
