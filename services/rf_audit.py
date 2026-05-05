"""
Post-LLM RF audit checks — P0.1 / P0.2 / P1.5 / P1.6 wiring.

When the P1 `generate_requirements` tool call returns, we now run three
**structural** checks on the output before it reaches the BOM:

  1. Block-diagram topology matches the wizard-selected architecture
     (tools/block_diagram_validator.py).
  2. Every `datasheet_url` actually resolves (tools/datasheet_verify.py),
     or at least points at a curated trusted-vendor domain when the
     deployment is air-gapped.
  3. No component is on the banned-manufacturer or EOL / NRND list
     (rules/banned_parts.py).

These checks produce `AuditIssue` rows that get merged into the
`AuditReport` the finalize step was already building, so the existing
UI rendering + overall_pass gating picks them up automatically.

Controlled by one env var for air-gapped demos:
  SKIP_DATASHEET_VERIFY=1  →  skip the network HEAD probes, fall back
                              to the trusted-vendor allowlist only.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any, Optional

from domains._schema import AuditIssue
from rules.banned_parts import filter_components
from tools.block_diagram_validator import validate as _validate_topology
from tools.datasheet_verify import is_trusted_vendor_url, verify_url
from tools.distributor_search import (
    any_api_configured as _distributor_configured,
    lookup as _distributor_lookup,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

def run_topology_audit(
    mermaid: Optional[str],
    architecture: Optional[str],
) -> list[AuditIssue]:
    """Run the block-diagram topology validator and translate its
    violations into `AuditIssue` rows. An empty Mermaid input yields one
    critical issue (the diagram must exist)."""
    violations = _validate_topology(mermaid or "", architecture=architecture)
    issues: list[AuditIssue] = []
    for v in violations:
        issues.append(AuditIssue(
            severity=v.severity,  # validator uses the same four labels
            category="topology",
            location="block_diagram_mermaid",
            detail=v.detail,
            suggested_fix=v.suggested_fix,
        ))
    return issues


# ---------------------------------------------------------------------------
# Datasheet URLs
# ---------------------------------------------------------------------------

def _should_verify_network() -> bool:
    return os.getenv("SKIP_DATASHEET_VERIFY", "").strip() not in {"1", "true", "yes"}


def run_datasheet_audit(
    component_recommendations: list[dict[str, Any]],
    *,
    timeout_s: float = 4.0,
    parallelism: int = 6,
) -> list[AuditIssue]:
    """Probe every `datasheet_url` — one issue per component whose URL
    neither resolves via HEAD/GET nor matches the trusted-vendor
    allowlist."""
    issues: list[AuditIssue] = []
    if not component_recommendations:
        return issues

    allow_network = _should_verify_network()

    # Build (index, url, component) triples so we can correlate
    # results with the original component row.
    targets: list[tuple[int, str, dict[str, Any]]] = []
    for idx, c in enumerate(component_recommendations):
        url = (c.get("datasheet_url") or c.get("datasheet") or "").strip()
        if not url:
            issues.append(_missing_url_issue(idx, c))
            continue
        targets.append((idx, url, c))

    if not targets:
        return issues

    # Short-circuit on the trusted-vendor allowlist first so air-gapped
    # environments (SKIP_DATASHEET_VERIFY=1) still mark known-good URLs
    # as verified without hitting the network. Also honour the
    # `_distributor_url_verified` perf marker written by `_merge_part_info`
    # — `tools.distributor_search._verify_datasheet` has already HEAD-probed
    # the URL seconds ago, re-probing adds no safety (either still good →
    # wasted RTT, or transiently flapping → false positive that strips a
    # real datasheet).
    trusted: dict[int, bool] = {
        idx: (is_trusted_vendor_url(url) or bool(c.get("_distributor_url_verified")))
        for idx, url, c in targets
    }

    live_results: dict[int, bool] = {}
    if allow_network:
        to_probe = [(idx, url) for idx, url, _ in targets if not trusted[idx]]
        if to_probe:
            with ThreadPoolExecutor(max_workers=parallelism) as ex:
                futures = {
                    ex.submit(verify_url, url, timeout=timeout_s): idx
                    for idx, url in to_probe
                }
                for fut in as_completed(futures):
                    idx = futures[fut]
                    try:
                        live_results[idx] = bool(fut.result())
                    except Exception as exc:  # noqa: BLE001
                        log.warning("datasheet_verify.exception idx=%s: %s", idx, exc)
                        live_results[idx] = False

    for idx, url, c in targets:
        if trusted[idx]:
            continue  # trusted-vendor URL → pass
        if live_results.get(idx, False):
            continue  # HEAD/GET resolved OK
        pn = c.get("part_number") or c.get("primary_part") or "unknown"
        suffix = "(network disabled)" if not allow_network else "(HEAD/GET failed)"
        issues.append(AuditIssue(
            severity="high",
            category="datasheet_url",
            location=f"component_recommendations/{pn}",
            detail=(
                f"Datasheet URL for `{pn}` did not resolve and is not on the "
                f"trusted-vendor allowlist: {url} {suffix}"
            ),
            suggested_fix=(
                "Replace with the manufacturer's canonical product-page URL "
                "(analog.com / ti.com / qorvo.com / macom.com etc.)."
            ),
        ))

    return issues


def _missing_url_issue(idx: int, c: dict[str, Any]) -> AuditIssue:
    pn = c.get("part_number") or c.get("primary_part") or f"row-{idx}"
    return AuditIssue(
        severity="medium",
        category="datasheet_url",
        location=f"component_recommendations/{pn}",
        detail=f"Component `{pn}` has no `datasheet_url` field.",
        suggested_fix="Populate `datasheet_url` with the manufacturer's product page.",
    )


# ---------------------------------------------------------------------------
# Banned parts
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Live part-number validation (DigiKey → Mouser → local seed)
# ---------------------------------------------------------------------------

def _source_url_key(source: str) -> Optional[str]:
    src = (source or "").strip().lower()
    if src == "digikey":
        return "digikey_url"
    if src == "mouser":
        return "mouser_url"
    return None


def _merge_part_info(row: dict[str, Any], info) -> dict[str, Any]:
    """Overlay distributor-authoritative fields onto a BOM row.

    P1 has two BOM shapes in circulation: rich rows use
    primary_part/primary_manufacturer/primary_description, while older
    rows use part_number/manufacturer/description. Keep both shapes
    coherent so docs, audits, and downstream netlist generation all see
    the same distributor-backed values.
    """
    merged = {**row}

    if info.manufacturer:
        if "primary_part" in merged or "primary_manufacturer" in merged:
            merged["primary_manufacturer"] = info.manufacturer
        merged["manufacturer"] = info.manufacturer

    if info.description:
        if "primary_part" in merged or "primary_description" in merged:
            merged["primary_description"] = info.description
        merged["description"] = info.description

    if info.datasheet_url:
        merged["datasheet_url"] = info.datasheet_url
        merged.pop("datasheet", None)
        # Mark this URL as having been verified by the distributor's
        # `lookup` chain — `tools.distributor_search._verify_datasheet`
        # HEAD-probes every non-trusted-vendor URL with a 3 s timeout
        # before returning a `PartInfo`, so the URL we just wrote is
        # at most the few seconds of fan-out latency stale.
        # `run_datasheet_audit` honours this marker and skips its own
        # HEAD probe, which on dense BOMs (12-15 components) was the
        # second-largest contributor to finalize_p1 wall-clock after
        # the distributor lookups themselves.
        merged["_distributor_url_verified"] = True

    if info.lifecycle_status != "unknown":
        merged["lifecycle_status"] = info.lifecycle_status

    if info.source:
        merged["distributor_source"] = info.source

    if info.product_url:
        merged["product_url"] = info.product_url
        merged["distributor_url"] = info.product_url
        source_key = _source_url_key(info.source)
        if source_key:
            merged[source_key] = info.product_url

    if info.unit_price_usd is not None:
        merged["unit_price_usd"] = info.unit_price_usd
    if info.unit_price is not None:
        merged["unit_price"] = info.unit_price
    if info.unit_price_currency:
        merged["unit_price_currency"] = info.unit_price_currency
    if info.stock_quantity is not None:
        merged["stock_quantity"] = info.stock_quantity
    if info.region:
        merged["stock_region"] = info.region

    return merged

def run_part_validation_audit(
    component_recommendations: list[dict[str, Any]],
    *, timeout_s: float = 6.0,
    max_workers: int = 6,
    overall_timeout_s: float = 60.0,
) -> tuple[list[dict[str, Any]], list[AuditIssue]]:
    """Look every MPN up via the distributor cascade. When a part is
    found we enrich the original component dict with the distributor's
    canonical manufacturer name, datasheet URL, and lifecycle status so
    downstream docs use the authoritative values, not the LLM's guesses.

    Issues produced:
      - `hallucinated_part` (critical) — MPN not found anywhere
      - `nrnd_part` (high) — found but flagged NRND by the distributor
      - `obsolete_part` (critical) — found but obsolete / discontinued
      - `part_validation_timeout` (high) — overall deadline exceeded

    Parallelism:
      Looks are fanned out across `max_workers` threads (default 6) so a
      50-part BOM doesn't block a FastAPI worker for 10+ minutes. A
      hard overall wall-clock deadline of `overall_timeout_s` seconds
      ensures we always return — parts that didn't finish by then are
      flagged `part_validation_timeout` and the component is passed
      through without enrichment.

    Returns (enriched_components, issues).
    """
    issues: list[AuditIssue] = []
    if not component_recommendations:
        return [], issues

    live_configured = _distributor_configured()

    # Enumerate components once so positional order survives the fan-out.
    entries: list[tuple[int, dict[str, Any], str]] = []
    for idx, c in enumerate(component_recommendations):
        pn = (
            c.get("part_number")
            or c.get("primary_part")
            or c.get("mpn")
            or ""
        ).strip()
        entries.append((idx, c, pn))

    # Resolve MPNs in parallel. `lookups[idx]` will be None, the PartInfo,
    # or the sentinel `_TIMEOUT` when the per-part or overall deadline
    # fires before a worker finished.
    _TIMEOUT = object()
    lookups: dict[int, Any] = {}

    def _resolve(pn: str):
        if not pn:
            return None
        try:
            return _distributor_lookup(pn, timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001
            log.warning("distributor_lookup_failed pn=%s: %s", pn, exc)
            return None

    to_fetch = [(idx, pn) for idx, _, pn in entries if pn]
    if to_fetch:
        # Use as_completed's own timeout so the iterator itself unblocks
        # at the overall deadline — even when no future has finished yet.
        # Don't use the context manager: its __exit__ waits for in-flight
        # tasks, which would nullify the deadline. Shut down non-blocking
        # with cancel_futures so stragglers are abandoned, not awaited.
        ex = ThreadPoolExecutor(max_workers=max(1, max_workers))
        try:
            futures = {ex.submit(_resolve, pn): idx for idx, pn in to_fetch}
            try:
                for fut in as_completed(futures, timeout=overall_timeout_s):
                    idx = futures[fut]
                    try:
                        lookups[idx] = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        log.warning("distributor_future_err idx=%d: %s", idx, exc)
                        lookups[idx] = None
            except FuturesTimeout:
                pass
            for fut, idx in futures.items():
                if not fut.done():
                    fut.cancel()
                    lookups.setdefault(idx, _TIMEOUT)
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    enriched: list[dict[str, Any]] = []

    for idx, c, pn in entries:
        if not pn:
            enriched.append(c)
            continue

        info = lookups.get(idx)
        if info is _TIMEOUT:
            # The pipeline must still finish even when distributors are
            # misbehaving; raise a distinct audit issue so the operator
            # knows a bulk re-run is warranted.
            issues.append(AuditIssue(
                severity="high",
                category="part_validation_timeout",
                location=f"component_recommendations/{pn}",
                detail=(
                    f"Distributor lookup for `{pn}` did not complete within "
                    f"the {overall_timeout_s:.0f}s overall deadline; part not "
                    "validated this run."
                ),
                suggested_fix=(
                    "Re-run the audit later, or call the distributor lookup "
                    "tool manually for this MPN."
                ),
            ))
            enriched.append(c)
            continue

        if info is None:
            # MPN unknown to every oracle we tried.
            if live_configured:
                issues.append(AuditIssue(
                    severity="critical",
                    category="hallucinated_part",
                    location=f"component_recommendations/{pn}",
                    detail=(
                        f"Part `{pn}` was not found on DigiKey, Mouser, or in "
                        "the local component seed — the LLM may have invented it."
                    ),
                    suggested_fix=(
                        "Replace with a verifiable active-production part from "
                        "data/sample_components.json or a real distributor MPN."
                    ),
                ))
                # P0.1 — blank the fields the LLM fabricated alongside the
                # invented MPN. If we leave `datasheet_url` in place, the
                # downstream datasheet audit may accept it because its
                # domain is on the trusted-vendor allowlist, and a human
                # reviewer then sees a `hallucinated_part` flag next to a
                # plausible-looking URL + manufacturer — a dangerous mix.
                scrubbed = {**c}
                for k in ("datasheet_url", "datasheet",
                          "product_url", "unit_price_usd",
                          "unit_price", "unit_price_currency",
                          "stock_quantity", "stock_region",
                          "lifecycle_status", "distributor_source",
                          "distributor_url", "digikey_url", "mouser_url"):
                    scrubbed.pop(k, None)
                scrubbed["_hallucinated"] = True
                enriched.append(scrubbed)
            else:
                # No live oracle configured — leave the component unchanged
                # so air-gap / offline demos don't strip legitimate parts.
                enriched.append(c)
            continue

        # Found — flag lifecycle issues before accepting.
        if info.lifecycle_status == "obsolete":
            issues.append(AuditIssue(
                severity="critical",
                category="obsolete_part",
                location=f"component_recommendations/{pn}",
                detail=(
                    f"Part `{pn}` is marked OBSOLETE by {info.source}. "
                    "Shipping an obsolete MPN risks immediate BOM redesign."
                ),
                suggested_fix="Replace with an active-production successor.",
            ))
        elif info.lifecycle_status == "nrnd":
            issues.append(AuditIssue(
                severity="high",
                category="nrnd_part",
                location=f"component_recommendations/{pn}",
                detail=(
                    f"Part `{pn}` is NRND (Not Recommended for New Designs) "
                    f"per {info.source}."
                ),
                suggested_fix="Prefer an active-production alternative for new builds.",
            ))

        # Enrich the component dict with authoritative values. The LLM's
        # fields survive only when the distributor didn't provide one.
        enriched.append(_merge_part_info(c, info))

    return enriched, issues


def run_banned_parts_audit(
    component_recommendations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[AuditIssue]]:
    """Filter banned / EOL / NRND parts out of the BOM.

    Returns (cleaned_bom, issues). Callers should replace the original
    `component_recommendations` array with `cleaned_bom` so downstream
    document generation doesn't emit the banned parts.
    """
    kept, rejected = filter_components(component_recommendations or [])
    issues = [AuditIssue(**rej.to_issue_dict()) for rej in rejected]
    return kept, issues


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_price_reconciliation_audit(
    component_recommendations: list[dict[str, Any]],
    *,
    pct_threshold: float = 20.0,
    timeout_s: float = 6.0,
) -> list[AuditIssue]:
    """Cross-check DigiKey vs Mouser prices per MPN.

    When BOTH distributors are configured AND both return a price in the
    **same currency**, flag any component where the delta exceeds
    `pct_threshold` percent. Currency mismatch is skipped silently (INR
    vs USD has an FX layer we don't touch). Missing keys / lookup miss
    on either tier → skipped. Purely advisory (severity="medium") so a
    legitimate difference (quantity-break differences, promo pricing)
    doesn't block the pipeline.

    Issue category: `price_discrepancy`.
    """
    issues: list[AuditIssue] = []
    if not component_recommendations:
        return issues

    try:
        from tools import digikey_api, mouser_api
    except Exception:
        return issues
    if not (digikey_api.is_configured() and mouser_api.is_configured()):
        return issues  # need BOTH to compare

    for c in component_recommendations:
        pn = (
            c.get("part_number")
            or c.get("primary_part")
            or c.get("mpn")
            or ""
        ).strip()
        if not pn:
            continue
        try:
            dk = digikey_api.lookup(pn, timeout_s=timeout_s)
            mo = mouser_api.lookup(pn, timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001
            log.debug("price_reconcile.lookup_err pn=%s: %s", pn, exc)
            continue
        if not (dk and mo):
            continue
        if dk.unit_price is None or mo.unit_price is None:
            continue
        if not dk.unit_price_currency or dk.unit_price_currency != mo.unit_price_currency:
            continue  # different currencies — skip (no FX layer)

        dk_p, mo_p = float(dk.unit_price), float(mo.unit_price)
        if dk_p <= 0 or mo_p <= 0:
            continue
        denom = min(dk_p, mo_p)
        delta_pct = abs(dk_p - mo_p) / denom * 100.0
        if delta_pct < pct_threshold:
            continue
        cheaper = "DigiKey" if dk_p < mo_p else "Mouser"
        issues.append(AuditIssue(
            severity="medium",
            category="price_discrepancy",
            location=f"component_recommendations/{pn}",
            detail=(
                f"Unit price for `{pn}` differs by {delta_pct:.0f}% between "
                f"distributors: DigiKey = {dk_p:.2f} {dk.unit_price_currency}, "
                f"Mouser = {mo_p:.2f} {mo.unit_price_currency}. {cheaper} is cheaper."
            ),
            suggested_fix=(
                "Verify the quantity break assumed for each distributor and "
                "pick the one that matches the intended build volume."
            ),
        ))
    return issues


def run_phase_noise_audit(
    component_recommendations: list[dict[str, Any]],
    design_parameters: Optional[dict[str, Any]],
) -> list[AuditIssue]:
    """P2.8 — compare claimed system phase-noise floor against the LO's
    datasheet phase-noise. Wraps `tools.phase_noise_validator`.

    Skipped when `design_parameters` has no `phase_noise_dbchz` claim
    or when no LO / synthesizer components are in the BOM.
    """
    if not design_parameters or not component_recommendations:
        return []
    claim = design_parameters.get("phase_noise_dbchz")
    if claim is None:
        # Some prompts use `phase_noise_floor_dbc_hz` — accept both.
        claim = design_parameters.get("phase_noise_floor_dbc_hz")
    if claim is None:
        return []
    try:
        from tools.phase_noise_validator import validate_phase_noise
    except Exception:
        return []
    offset = design_parameters.get("phase_noise_offset_hz", 10_000.0)
    raw = validate_phase_noise(
        claim,
        offset_hz=float(offset) if offset else 10_000.0,
        components=list(component_recommendations),
    )
    return [AuditIssue(**i) for i in raw]


def run_cascade_completeness_audit(
    component_recommendations: list[dict[str, Any]],
    design_parameters: Optional[dict[str, Any]],
) -> list[AuditIssue]:
    """Direction-agnostic cascade completeness check.

    Runs `compute_cascade()` for whatever direction the project is and
    surfaces every active stage that came in with a missing primary spec
    (`gain_db` / `nf_db` for RX, `gain_db` / `pout_dbm` for TX). Without
    this, the cascade silently zero-fills and the audit can't tell the
    difference between a real BOM and one where the LLM hallucinated a
    component without specs.
    """
    if not component_recommendations:
        return []
    try:
        from tools.rf_cascade import compute_cascade
    except Exception:
        return []
    direction = "rx"
    if design_parameters:
        d = str(
            design_parameters.get("direction")
            or design_parameters.get("project_type")
            or ""
        ).strip().lower()
        if d in {"tx", "transmitter", "transceiver"}:
            direction = "tx"
        elif d in {"power_supply"}:
            return []  # nothing to validate
    cascade = compute_cascade(list(component_recommendations), direction=direction)
    issues: list[AuditIssue] = []
    for w in (cascade.get("missing_spec_warnings") or []):
        issues.append(AuditIssue(
            severity="high",
            category="cascade_missing_spec",
            location=f"components/{w.get('part_number') or w.get('stage')}",
            detail=(
                f"Active stage '{w.get('stage')}' "
                f"(part {w.get('part_number') or 'n/a'}, "
                f"category {w.get('category') or 'n/a'}) is missing "
                f"`{w.get('missing')}` — cascade math used an implicit "
                "zero-fill, so any system NF / gain / Pout claim derived "
                "from this BOM is unbacked by data."
            ),
            suggested_fix=(
                "Populate the missing spec from the manufacturer datasheet "
                "or drop the stage from the BOM if it is not actually in "
                "the signal chain."
            ),
        ))
    return issues


def run_tx_cascade_audit(
    component_recommendations: list[dict[str, Any]],
    design_parameters: Optional[dict[str, Any]],
) -> list[AuditIssue]:
    """TX cascade verdict → AuditIssues. Runs `tools/rf_cascade.py`
    in TX direction and emits structured issues when the claimed
    Pout / OIP3 / PAE targets aren't met, or when any stage is
    computed to be in compression (drive > datasheet Pout spec).

    Fires only when the project is a transmitter — detected from
    `design_parameters.direction == "tx"` or `project_type == "transmitter"`.
    Silently returns [] on receiver projects so the RX audit stays lean.
    """
    if not design_parameters or not component_recommendations:
        return []
    direction = str(
        design_parameters.get("direction")
        or design_parameters.get("project_type")
        or ""
    ).strip().lower()
    # P26 #13: transmitter + transceiver run TX-side audits (PA thermal /
    # OIP3-PAE headroom). receiver, switch_matrix, power_supply skip.
    if direction not in {"tx", "transmitter", "transceiver"}:
        return []

    try:
        from tools.rf_cascade import compute_cascade
    except Exception:
        return []

    cascade = compute_cascade(
        list(component_recommendations),
        direction="tx",
        claimed_pout_dbm=design_parameters.get("pout_dbm")
                         or design_parameters.get("output_power_dbm"),
        claimed_oip3_dbm=design_parameters.get("oip3_dbm"),
        claimed_total_gain_db=design_parameters.get("total_gain_db"),
        claimed_pae_pct=design_parameters.get("pae_pct"),
        input_power_dbm=float(
            design_parameters.get("tx_input_power_dbm") or -20.0
        ),
    )

    issues: list[AuditIssue] = []
    totals = cascade.get("totals") or {}
    claims = cascade.get("claims") or {}
    verdict = cascade.get("verdict") or {}

    # Pout shortfall — high severity because it changes link-budget /
    # regulatory conformance directly.
    if verdict.get("pout_pass") is False:
        headroom = verdict.get("pout_headroom_db")
        issues.append(AuditIssue(
            severity="high",
            category="tx_pout_shortfall",
            location="design_parameters/pout_dbm",
            detail=(
                f"TX cascade Pout {totals.get('pout_dbm'):.1f} dBm misses the "
                f"claimed {claims.get('pout_dbm'):.1f} dBm target "
                f"(Δ {headroom:+.1f} dB). Driver / PA gain or PA saturation "
                "spec is insufficient for the claimed output power."
            ),
            suggested_fix=(
                "Raise driver / PA gain, swap in a higher-Pout PA, or relax "
                "the Pout requirement."
            ),
        ))

    # OIP3 shortfall — high severity (linearity is a hard floor for
    # modulated comms / multi-tone radar).
    if verdict.get("oip3_pass") is False:
        headroom = verdict.get("oip3_headroom_db")
        issues.append(AuditIssue(
            severity="high",
            category="tx_oip3_shortfall",
            location="design_parameters/oip3_dbm",
            detail=(
                f"TX cascade OIP3 {totals.get('oip3_dbm'):.1f} dBm misses the "
                f"claimed {claims.get('oip3_dbm'):.1f} dBm target "
                f"(Δ {headroom:+.1f} dB). The last stage's OIP3 dominates the "
                "cascade — swap for a higher-linearity PA or add backoff."
            ),
            suggested_fix=(
                "Select a PA with OIP3 ≥ claim + 3 dB margin, or move to a "
                "Doherty / DPD-linearized architecture."
            ),
        ))

    # PAE shortfall — medium (often accepted with a Pdc budget increase).
    if verdict.get("pae_pass") is False:
        delta = verdict.get("pae_delta_pct")
        measured = totals.get("pae_pct")
        claimed = claims.get("pae_pct")
        issues.append(AuditIssue(
            severity="medium",
            category="tx_pae_shortfall",
            location="design_parameters/pae_pct",
            detail=(
                f"Computed system PAE {measured:.1f} % misses the claimed "
                f"{claimed:.1f} % target (Δ {delta:+.1f} %). Thermal / DC "
                "power budget will exceed plan."
            ),
            suggested_fix=(
                "Switch the PA to a higher-efficiency class (C / E / F / "
                "Doherty) or relax the PAE target."
            ),
        ))

    # Compression — critical. Any stage computed to be driven beyond
    # its datasheet Pout spec will ship in hard saturation, producing
    # excess harmonics and potentially damaging the device.
    comp_warnings = totals.get("compression_warnings") or []
    for w in comp_warnings:
        issues.append(AuditIssue(
            severity="critical",
            category="tx_compression",
            location=f"components/{(w.split(':', 1)[0] or 'stage').strip()}",
            detail=(
                "TX stage is computed to be in hard compression: " + w +
                ". Ship-as-is will clip, saturate, and produce out-of-spec "
                "harmonics."
            ),
            suggested_fix=(
                "Reduce upstream drive level (add attenuator, lower VGA), "
                "or replace the stage with a higher-Pout device."
            ),
        ))

    # Missing primary specs — when an active stage doesn't ship gain_db /
    # nf_db, the cascade math silently treats it as 0 dB. That used to
    # let hallucinated BOMs sail through with a believable system NF.
    # Now the cascade tool surfaces the gaps; we elevate each to high
    # severity so the audit gate catches them.
    for w in (cascade.get("missing_spec_warnings") or []):
        issues.append(AuditIssue(
            severity="high",
            category="cascade_missing_spec",
            location=f"components/{w.get('part_number') or w.get('stage')}",
            detail=(
                f"Active stage '{w.get('stage')}' "
                f"(part {w.get('part_number') or 'n/a'}, "
                f"category {w.get('category') or 'n/a'}) is missing "
                f"`{w.get('missing')}` — cascade math used an implicit "
                "zero-fill, so any system NF / gain claim derived from this "
                "BOM is unbacked by data."
            ),
            suggested_fix=(
                "Populate the missing spec from the manufacturer datasheet or "
                "drop the stage from the BOM if it is not actually in the "
                "signal chain."
            ),
        ))

    return issues


def run_pa_thermal_audit(
    component_recommendations: list[dict[str, Any]],
    design_parameters: Optional[dict[str, Any]],
) -> list[AuditIssue]:
    """PA junction-temperature check. Fires only on TX projects where
    at least one PA carries the data needed (pdc_w + either pout_dbm or
    pae_pct). Reads ambient + heatsink params from design_parameters
    when the wizard supplied them; otherwise uses conservative defaults.
    """
    if not design_parameters or not component_recommendations:
        return []
    direction = str(
        design_parameters.get("direction")
        or design_parameters.get("project_type")
        or ""
    ).strip().lower()
    # P26 #13: transmitter + transceiver run TX-side audits (PA thermal /
    # OIP3-PAE headroom). receiver, switch_matrix, power_supply skip.
    if direction not in {"tx", "transmitter", "transceiver"}:
        return []

    try:
        from tools.pa_thermal_validator import validate_pa_thermal
    except Exception:
        return []

    raw = validate_pa_thermal(
        list(component_recommendations),
        ambient_temp_c=float(
            design_parameters.get("ambient_temp_c")
            or design_parameters.get("max_ambient_temp_c")
            or 25.0
        ),
        heatsink_theta_sa=(
            float(design_parameters["heatsink_theta_sa"])
            if design_parameters.get("heatsink_theta_sa") is not None else None
        ),
        case_sink_theta_cs=(
            float(design_parameters["case_sink_theta_cs"])
            if design_parameters.get("case_sink_theta_cs") is not None else None
        ),
    )
    return [AuditIssue(**i) for i in raw]


def run_acpr_mask_audit(
    design_parameters: Optional[dict[str, Any]],
) -> list[AuditIssue]:
    """Regulatory-mask check. Fires on any project that carries both a
    spur_mask selection and a claimed ACPR / harmonic rejection. Not
    strictly TX-only — an RX with a planned retransmit also benefits
    from the check — but in practice only TX projects fill the fields.
    """
    if not design_parameters:
        return []
    mask = (design_parameters.get("spur_mask")
            or design_parameters.get("regulatory_mask"))
    if not mask:
        return []
    aclr = (design_parameters.get("aclr_dbc")
            or design_parameters.get("acpr_dbc")
            or design_parameters.get("aclr"))
    harmonic = (design_parameters.get("harmonic_rej")
                or design_parameters.get("harmonic_rejection_dbc")
                or design_parameters.get("harmonic_dbc"))
    try:
        from tools.acpr_mask_validator import validate_acpr_mask
    except Exception:
        return []
    raw = validate_acpr_mask(
        claimed_aclr_dbc=aclr,
        claimed_harmonic_dbc=harmonic,
        mask_name=mask,
    )
    return [AuditIssue(**i) for i in raw]


def run_bom_linkage_audit(
    component_recommendations: list[dict[str, Any]],
    netlist_nodes: Optional[list[dict[str, Any]]],
) -> list[AuditIssue]:
    """P2.9 — BOM ↔ schematic cross-reference. Fires only when the caller
    supplies a list of netlist nodes (i.e. post-P4). Earlier phases
    (P1 / P2 / P3) get an empty list because the schematic doesn't
    exist yet."""
    if not netlist_nodes:
        return []
    try:
        from tools.bom_linkage import validate_bom_schematic_linkage
    except Exception:
        return []
    raw = validate_bom_schematic_linkage(
        component_recommendations or [],
        list(netlist_nodes),
    )
    return [AuditIssue(**i) for i in raw]


def run_candidate_pool_audit(
    component_recommendations: list[dict[str, Any]],
    offered_mpns: Optional[set[str]],
) -> list[AuditIssue]:
    """Flag BOM entries whose MPN was NOT surfaced by find_candidate_parts.

    Retrieval-augmented selection requires the LLM to pick from the
    distributor shortlist.  When `offered_mpns` is empty / None we skip
    the check silently — not every conversation uses the retrieval tool
    yet (e.g. legacy runs, air-gap mode).  When the set is non-empty we
    flag every component whose MPN is not in it at severity="high":
    the part may still be real (rf_audit's hallucination check covers
    that), but it bypassed the process gate and deserves reviewer
    attention.
    """
    if not offered_mpns or not component_recommendations:
        return []
    offered_upper = {m.strip().upper() for m in offered_mpns if m}
    issues: list[AuditIssue] = []
    for c in component_recommendations:
        pn = (
            c.get("part_number")
            or c.get("primary_part")
            or c.get("mpn")
            or ""
        ).strip()
        if not pn:
            continue
        if pn.upper() in offered_upper:
            continue
        issues.append(AuditIssue(
            severity="high",
            category="not_from_candidate_pool",
            location=f"component_recommendations/{pn}",
            detail=(
                f"Part `{pn}` was not in the `find_candidate_parts` shortlist "
                "for this turn — the LLM either skipped the retrieval step or "
                "picked an MPN outside the returned candidates."
            ),
            suggested_fix=(
                "Re-run P1 and ensure the LLM calls find_candidate_parts for "
                "every signal-chain stage, then selects only from the returned "
                "`candidates[].part_number` list."
            ),
        ))
    return issues


def run_all(
    tool_input: dict[str, Any],
    architecture: Optional[str],
    *,
    timeout_s: float = 4.0,
    offered_candidate_mpns: Optional[set[str]] = None,
    design_parameters: Optional[dict[str, Any]] = None,
    netlist_nodes: Optional[list[dict[str, Any]]] = None,
) -> tuple[dict[str, Any], list[AuditIssue]]:
    """Run every post-LLM check and return (possibly-mutated tool_input,
    combined issues). The tool_input is returned with banned parts
    removed from `component_recommendations` so the BOM the user sees is
    pre-filtered.

    When `offered_candidate_mpns` is supplied (the set of MPNs returned
    by `find_candidate_parts` during the same conversation turn), an
    extra `not_from_candidate_pool` audit issue is emitted for any BOM
    entry that bypassed the shortlist.
    """
    issues: list[AuditIssue] = []

    # 1. Topology
    issues.extend(run_topology_audit(
        tool_input.get("block_diagram_mermaid"),
        architecture,
    ))

    # 2. Banned parts — clean the BOM before the distributor lookup so we
    # don't waste API calls on parts we're about to drop anyway.
    bom_key = "component_recommendations"
    if bom_key not in tool_input and "bom" in tool_input:
        bom_key = "bom"
    original = tool_input.get(bom_key) or []
    cleaned, banned_issues = run_banned_parts_audit(original)
    issues.extend(banned_issues)

    # 3. Live part validation — DigiKey → Mouser → seed. Closes the
    # last-mile hallucination gap: parts the LLM invents and that aren't
    # in any distributor catalogue get flagged here. Also enriches
    # component dicts with the distributor's canonical manufacturer +
    # datasheet URL, so downstream docs use authoritative values.
    enriched, part_issues = run_part_validation_audit(cleaned, timeout_s=timeout_s)
    issues.extend(part_issues)

    # Persist the cleaned + enriched BOM back onto tool_input
    if banned_issues or part_issues or enriched != cleaned:
        tool_input = {**tool_input, bom_key: enriched}

    # 4. Datasheet URLs (on the enriched BOM — post-distributor, the URLs
    # should mostly be authoritative already, but any that still slipped
    # through get validated here as a belt-and-braces check).
    issues.extend(run_datasheet_audit(enriched, timeout_s=timeout_s))

    # 5. Candidate-pool gate — advisory check that the LLM used the
    # retrieval tool. Only fires when the caller threaded the set in.
    issues.extend(run_candidate_pool_audit(enriched, offered_candidate_mpns))

    # 6. Price reconciliation — compare DigiKey vs Mouser prices on
    # MPNs that both distributors know about. Advisory (medium) so a
    # legitimate price difference doesn't block the pipeline, but it
    # catches the case where one distributor's stale price line could
    # mislead the BOM cost roll-up.
    issues.extend(run_price_reconciliation_audit(enriched, timeout_s=timeout_s))

    # 7. Phase-noise budget (P2.8) — runs only when P1 supplied a
    # `phase_noise_dbchz` claim. Compares against the dominant LO's
    # datasheet number and flags a "the cascade can't be better than
    # its LO" violation if the claim is too aggressive.
    dp = design_parameters if design_parameters is not None \
        else tool_input.get("design_parameters")
    issues.extend(run_phase_noise_audit(enriched, dp))

    # 8. TX cascade verdict → AuditIssues. Fires only when
    # design_parameters.direction == "tx". Surfaces Pout / OIP3 / PAE
    # shortfalls and per-stage compression warnings that the existing
    # CascadeChart already shows — same information, now in the audit
    # report so overall_pass reflects TX-specific failures.
    issues.extend(run_tx_cascade_audit(enriched, dp))

    # 8b. Cascade completeness — direction-agnostic. Catches the case where
    # the BOM itself is missing primary specs (gain_db / nf_db on an active
    # stage). Without this, the cascade math silently zero-fills and a
    # hallucinated system NF / gain claim slips through unchallenged.
    issues.extend(run_cascade_completeness_audit(enriched, dp))

    # 9. PA thermal envelope. TX-only. Computes Tj per PA from pdc_w +
    # (pae_pct or pout_dbm), θ_jc by technology, and the caller-supplied
    # heatsink θ. Flags critical when Tj > Tj_max, high when inside the
    # derating margin.
    issues.extend(run_pa_thermal_audit(enriched, dp))

    # 10. Regulatory ACPR / harmonic mask. Fires when design_parameters
    # carries both a spur_mask choice and a claimed ACPR (or harmonic
    # rejection). Compares the claim against the published limit for
    # the mask family (MIL-STD-461, FCC Part 15 A/B, ETSI EN 300,
    # FCC Part 97). Catches the "PA is 20 dB too hot for type-approval"
    # case pre-hardware.
    issues.extend(run_acpr_mask_audit(dp))

    # 11. BOM ↔ schematic linkage (P2.9) — runs only in P4 context
    # where `netlist_nodes` exists. Flags missing + invented parts
    # between the BOM and the generated schematic.
    issues.extend(run_bom_linkage_audit(enriched, netlist_nodes))

    return tool_input, issues
