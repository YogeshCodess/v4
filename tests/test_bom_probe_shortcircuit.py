"""Perf guardrail: distributor-search URLs must NOT be HEAD-probed.

DigiKey's `/en/products/result?keywords=MPN` and Mouser's `/c/?q=MPN`
both return 2xx for every query (result list, filter list, or "no
results" page — never 404). Probing them is 100% wasted latency.

User report (2026-04-24): "time? around 9 min can reduce?" — and a
prior screenshot at 11m 36s. Several seconds of that tail were
accounted for by HEAD-probing 16+ candidate URLs the system KNEW
would resolve.

P17 short-circuits trusted-distributor URLs to live=True without a
network round-trip. This module whitebox-asserts the short-circuit
lives in both BOM renderers, so a future refactor can't silently
bring back the wasted probes.
"""
from __future__ import annotations

import inspect

from agents.requirements_agent import RequirementsAgent


# ---------------------------------------------------------------------------
# Whitebox guards
# ---------------------------------------------------------------------------

_TRUSTED_MARKER_SNIPPETS = ("digikey.com", "mouser.com")


def test_components_md_shortcircuits_trusted_distributor_urls():
    """`_build_components_md` must classify URLs against
    `_TRUSTED_HOSTS` and skip HEAD probes for distributor search URLs."""
    src = inspect.getsource(RequirementsAgent._build_components_md)
    # The marker we added for P17 — both hosts listed as trusted.
    for host in _TRUSTED_MARKER_SNIPPETS:
        assert host in src, (
            f"_build_components_md must include {host!r} in its "
            f"trusted-hosts tuple. Without it the HEAD-probe pool "
            f"balloons by N*2 URLs per render."
        )
    # There must be a short-circuit path that sets url_ok=True without
    # calling the probe. We look for the pattern `url_ok[u] = True`
    # after a trusted-host check (the literal pattern from P17).
    assert "url_ok[u] = True" in src, (
        "_build_components_md must short-circuit trusted URLs to "
        "url_ok=True rather than submitting them to the HEAD probe pool."
    )


def test_response_summary_shortcircuits_trusted_distributor_urls():
    """Same guard for `_build_response_summary` (chat-draft renderer)."""
    src = inspect.getsource(RequirementsAgent._build_response_summary)
    for host in _TRUSTED_MARKER_SNIPPETS:
        assert host in src, (
            f"_build_response_summary must include {host!r} in its "
            f"trusted-hosts tuple."
        )
    assert "url_live[u] = True" in src, (
        "_build_response_summary must short-circuit trusted URLs to "
        "url_live=True rather than probing them."
    )


def test_both_renderers_check_all_four_distributor_hosts():
    """Both DigiKey (US + India) and Mouser (US + India) must be
    trusted — users in India routinely land on `digikey.in` /
    `mouser.in` and the probe short-circuit needs to cover them too."""
    expected = {"digikey.com", "digikey.in", "mouser.com", "mouser.in"}
    for fn in (RequirementsAgent._build_components_md,
               RequirementsAgent._build_response_summary):
        src = inspect.getsource(fn)
        missing = [h for h in expected if h not in src]
        assert not missing, (
            f"{fn.__name__} is missing trusted hosts: {missing}. "
            f"All four (DigiKey US+IN, Mouser US+IN) must be covered "
            f"for users in India to get the probe short-circuit."
        )
