"""
RF cascade analysis — Friis NF + gain + IP3 accumulation.

The P1 requirements agent hands us a signal-chain list with per-stage
RF specs (NF / gain / IIP3 for RX, gain / OIP3 / Pout for TX) and this
module walks that list left-to-right and produces the cumulative cascade
numbers so the UI can draw a stage-by-stage bar chart and so the audit
layer can compare the cumulative result against the system claim.

Two signal-chain directions are supported:

**Receiver (direction="rx", default)** — Friis 1944 + Razavi 1997:
  NF_total_lin = NF1_lin + (NF2_lin - 1) / G1_lin
                         + (NF3_lin - 1) / (G1_lin * G2_lin) + ...
  G_total_db   = sum(Gi_db)
  1 / IIP3_total_lin = 1 / IIP3_1_lin + G1_lin / IIP3_2_lin
                                       + G1_lin * G2_lin / IIP3_3_lin + ...

**Transmitter (direction="tx")** — forward-cascade, output-referred:
  Pout_k       = Pin_system + sum(G_1..G_k)
  G_total_db   = sum(Gi_db)
  1 / OIP3_out_lin = sum_k [ G_{after_k,lin} / OIP3_k_out_lin ]
  PAE_system   = (Pout_N_watts - Pin_1_watts) / sum(Pdc_i_watts)

For TX, the dominant linearity concern is the final PA's output IP3
reflected back (or forward in the signal-flow sense) by the absence of
downstream gain: 1/OIP3 is a sum where each stage contributes its own
OIP3 only after being attenuated by the gain between it and the output.
Put plainly: **for a TX the last stage dominates**, mirror-image of
Friis where the first stage dominates NF.

All inputs are in dB / dBm; conversions to linear happen internally and
the output is reported back in dB / dBm so it slots straight into the
existing design_parameters shape.

`compute_cascade` is side-effect free and returns a JSON-safe dict ready
for `json.dumps`.
"""
from __future__ import annotations

import math
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Stage extraction
# ---------------------------------------------------------------------------

# Keys we'll probe on each component to dig out the RF figures. The LLM
# sometimes writes nf_db, sometimes noise_figure_db; distributors return
# noise_figure; etc. Normalise all of them.
_NF_KEYS = ("nf_db", "noise_figure_db", "noise_figure", "nf")
_GAIN_KEYS = ("gain_db", "gain", "conversion_gain_db", "conversion_gain")
_IIP3_KEYS = ("iip3_dbm", "iip3", "input_ip3_dbm")
_OIP3_KEYS = ("oip3_dbm", "oip3", "output_ip3_dbm")
_LOSS_KEYS = ("insertion_loss_db", "loss_db", "il_db", "insertion_loss")
# TX-specific keys
_POUT_KEYS = ("pout_dbm", "p_sat_dbm", "p1db_dbm", "output_power_dbm",
              "psat_dbm", "pout", "p1db")
_PAE_KEYS = ("pae_pct", "pae", "efficiency_pct", "drain_efficiency_pct",
             "drain_efficiency")
_PDC_KEYS = ("pdc_w", "pdc", "power_dissipation_w", "dc_power_w",
             "supply_power_w")


def _first_number(d: dict[str, Any], keys: tuple[str, ...]) -> Optional[float]:
    """Return the first numeric value found under any of `keys`, walking
    both the top-level dict and its `key_specs` / `specs` sub-dict."""
    specs = d.get("key_specs") or d.get("specs") or {}
    for k in keys:
        for source in (d, specs):
            if not isinstance(source, dict):
                continue
            v = source.get(k)
            if v is None:
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                # Distributor strings like "+15 dB" / "-1.5 dB"
                import re as _re
                m = _re.search(r"-?\d+(?:\.\d+)?", str(v))
                if m:
                    try:
                        return float(m.group(0))
                    except ValueError:
                        continue
    return None


# Categories that contribute to the RF cascade. Passive filters / pads
# that the LLM emits as "insertion_loss_db" become negative gain stages.
_ACTIVE_CATEGORIES = {
    "RF-LNA", "LNA", "RF-Amplifier", "Amplifier", "RF-PA",
    "RF-Mixer", "Mixer", "RF-Downconverter", "RF-Upconverter",
    "RF-ADC", "ADC", "RF-VGA", "VGA",
    "RF-Filter", "Filter", "RF-Attenuator", "Attenuator",
    "RF-Coupler", "Coupler",
    # TX-specific
    "RF-Driver", "Driver", "RF-PreDriver", "PreDriver",
    "RF-Modulator", "IQ-Modulator", "RF-DAC", "DAC",
    "RF-Balun", "Balun", "RF-Circulator", "Circulator",
    "RF-Isolator", "Isolator",
}


def _is_rf_stage(c: dict[str, Any]) -> bool:
    cat = str(c.get("category") or "").strip()
    if cat in _ACTIVE_CATEGORIES:
        return True
    # Signal-chain heuristic: if the component advertises any RF figure
    # (RX: NF/gain; TX: gain/OIP3/Pout), it's in the chain regardless of
    # how its category was labelled.
    for keyset in (_NF_KEYS, _GAIN_KEYS, _LOSS_KEYS,
                   _OIP3_KEYS, _POUT_KEYS):
        if _first_number(c, keyset) is not None:
            return True
    return False


def extract_stages(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter `components` down to the RF-cascade-relevant ones and
    normalise each entry to a dict carrying both RX (nf_db, iip3_dbm)
    and TX (oip3_dbm, pout_dbm, pae_pct, pdc_w) specs — downstream
    `compute_cascade` picks the columns it needs based on `direction`.

    When a component advertises `insertion_loss_db` instead of gain (i.e.
    passive filters), we treat it as -loss dB gain so it still shows up
    in the cumulative calculation.
    """
    stages: list[dict[str, Any]] = []
    for c in components or []:
        if not isinstance(c, dict):
            continue
        if not _is_rf_stage(c):
            continue
        nf = _first_number(c, _NF_KEYS)
        gain = _first_number(c, _GAIN_KEYS)
        if gain is None:
            loss = _first_number(c, _LOSS_KEYS)
            if loss is not None:
                gain = -abs(loss)
        iip3 = _first_number(c, _IIP3_KEYS)
        oip3 = _first_number(c, _OIP3_KEYS)
        pout = _first_number(c, _POUT_KEYS)
        pae = _first_number(c, _PAE_KEYS)
        pdc = _first_number(c, _PDC_KEYS)
        stages.append({
            "name": (c.get("component_name")
                     or c.get("function")
                     or c.get("part_number")
                     or c.get("name")
                     or "stage"),
            "part_number": c.get("part_number") or "",
            "category": c.get("category") or "",
            "nf_db": nf,
            "gain_db": gain,
            "iip3_dbm": iip3,
            "oip3_dbm": oip3,
            "pout_dbm": pout,
            "pae_pct": pae,
            "pdc_w": pdc,
        })
    return stages


# ---------------------------------------------------------------------------
# Friis cascade
# ---------------------------------------------------------------------------

def _db_to_lin(x: float) -> float:
    return 10.0 ** (x / 10.0)


def _lin_to_db(x: float) -> float:
    if x <= 0:
        # Friis asymptote — a perfectly noiseless / infinite-IP3 stage
        # would land here. Clamp so the renderer doesn't get -inf.
        return float("-inf")
    return 10.0 * math.log10(x)


def compute_cascade(
    components: list[dict[str, Any]],
    *,
    direction: str = "rx",
    claimed_nf_db: Optional[float] = None,
    claimed_iip3_dbm: Optional[float] = None,
    claimed_total_gain_db: Optional[float] = None,
    claimed_pout_dbm: Optional[float] = None,
    claimed_oip3_dbm: Optional[float] = None,
    claimed_pae_pct: Optional[float] = None,
    input_power_dbm: float = -20.0,
) -> dict[str, Any]:
    """Walk the RF stages left-to-right and compute cascade totals.

    `direction`: "rx" (default) uses Friis NF + input-referred IIP3
    cascade. "tx" uses forward-cascade output Pout + output-referred
    OIP3 + PAE roll-up.

    Returns a dict with:
      - `direction`: echoed back
      - `stages`: per-stage input/output values for the chosen direction
      - `totals`: system roll-up (RX: {nf_db, gain_db, iip3_dbm}; TX:
                  {pout_dbm, gain_db, oip3_dbm, pae_pct, pdc_total_w})
      - `claims`: echoed back for the UI
      - `verdict`: pass/fail + headroom per claim

    Stages with missing primary specs are skipped in the cumulative
    calculation (their cum_* values carry forward from the previous
    stage). This mirrors what an RF engineer does manually when a
    passive coupling cap has no spec — you just assume 0 dB.
    """
    direction = (direction or "rx").lower()
    # P26 #13: project_type aliases mapped to cascade direction.
    # Receiver / Tx are explicit; transceiver runs as Tx (the louder side
    # of the budget — Rx already passes if Tx headroom holds); switch
    # matrix is a passive routing fabric — treat as Rx (insertion-loss +
    # IIP3 cascade still applies); power_supply has no RF cascade so we
    # short-circuit to an empty rollup so callers get a well-formed
    # result instead of an exception.
    _DIRECTION_ALIAS = {
        "receiver": "rx",
        "transmitter": "tx",
        "transceiver": "tx",
        "switch_matrix": "rx",
        "power_supply": "none",
    }
    direction = _DIRECTION_ALIAS.get(direction, direction)
    if direction == "none":
        return {
            "direction": "none",
            "stages": [],
            "totals": {},
            "claims": {},
            "verdict": {
                "ok": True,
                "notes": [
                    "power_supply project — RF cascade not applicable; "
                    "use efficiency / regulation budgets instead",
                ],
            },
        }
    if direction not in ("rx", "tx"):
        direction = "rx"

    if direction == "tx":
        return _compute_tx_cascade(
            components,
            claimed_pout_dbm=claimed_pout_dbm,
            claimed_oip3_dbm=claimed_oip3_dbm,
            claimed_total_gain_db=claimed_total_gain_db,
            claimed_pae_pct=claimed_pae_pct,
            input_power_dbm=input_power_dbm,
        )

    stages = extract_stages(components)

    cum_g_lin = 1.0    # cumulative gain at the *input* of stage i
    cum_nf_lin = 1.0   # cumulative noise factor at the output so far (=1 before any stage)
    cum_recip_iip3_lin = 0.0  # sum of (g_preceding / iip3_i_lin)

    total_gain_db = 0.0
    first_active = True

    # Track stages that came in with missing primary specs — historically
    # the cascade silently treated these as 0 dB / no contribution, which
    # let a hallucinated BOM produce a system-NF claim that wasn't actually
    # backed by data. Now we surface every gap explicitly so the audit
    # layer (rf_audit / red_team_audit) can elevate it to CRITICAL.
    missing_spec_warnings: list[dict[str, Any]] = []
    stages_missing_nf = 0
    stages_missing_gain = 0

    for s in stages:
        nf = s["nf_db"]
        g = s["gain_db"]
        iip3 = s["iip3_dbm"]
        if nf is None:
            stages_missing_nf += 1
            missing_spec_warnings.append({
                "stage": s["name"],
                "part_number": s["part_number"],
                "missing": "nf_db",
                "category": s["category"],
            })
        if g is None:
            stages_missing_gain += 1
            missing_spec_warnings.append({
                "stage": s["name"],
                "part_number": s["part_number"],
                "missing": "gain_db",
                "category": s["category"],
            })

        # Gain accumulates unconditionally (0 dB when missing).
        g_lin = _db_to_lin(g) if g is not None else 1.0
        if g is not None:
            total_gain_db += g

        # NF Friis — only advance when we actually have NF for this stage.
        nf_contribution: Optional[float] = None
        if nf is not None:
            nf_lin = _db_to_lin(nf)
            if first_active:
                cum_nf_lin = nf_lin
                nf_contribution = nf  # the very first stage sets the floor
                first_active = False
            else:
                contribution = (nf_lin - 1.0) / cum_g_lin
                cum_nf_lin += contribution
                nf_contribution = _lin_to_db(1.0 + contribution) if contribution > 0 else 0.0

        # IIP3 Friis — use the gain *preceding* this stage.
        iip3_contribution: Optional[float] = None
        if iip3 is not None:
            iip3_lin_watts = _db_to_lin(iip3) / 1000.0  # dBm → mW → W
            if iip3_lin_watts > 0:
                cum_recip_iip3_lin += cum_g_lin / iip3_lin_watts
                iip3_contribution = iip3

        # Advance the running input-gain for the next stage *after* we've
        # consumed this one with the pre-stage gain.
        cum_g_lin *= g_lin

        s["cum_gain_db"] = _lin_to_db(cum_g_lin)
        s["cum_nf_db"] = (_lin_to_db(cum_nf_lin)
                          if cum_nf_lin > 0 and not first_active else None)
        s["cum_iip3_dbm"] = (
            _lin_to_db(1.0 / cum_recip_iip3_lin * 1000.0)
            if cum_recip_iip3_lin > 0 else None
        )
        s["nf_contribution_db"] = nf_contribution
        s["iip3_contribution_dbm"] = iip3_contribution

    totals = {
        "nf_db": (_lin_to_db(cum_nf_lin) if not first_active else None),
        "gain_db": total_gain_db if stages else None,
        "iip3_dbm": (_lin_to_db(1.0 / cum_recip_iip3_lin * 1000.0)
                     if cum_recip_iip3_lin > 0 else None),
        "stage_count": len(stages),
        # Data-completeness signal — what fraction of stages provided each
        # primary spec. Anything below 100% means the cascade math used
        # implicit zero-fills that the audit layer should call out.
        "stages_missing_nf_db": stages_missing_nf,
        "stages_missing_gain_db": stages_missing_gain,
        "data_completeness_pct": (
            round(100.0 * (1.0 - (stages_missing_nf + stages_missing_gain)
                           / max(2 * len(stages), 1)), 1)
        ),
    }

    verdict: dict[str, Any] = {
        "nf_pass": None, "gain_pass": None, "iip3_pass": None,
        "nf_headroom_db": None, "iip3_headroom_db": None, "gain_delta_db": None,
    }
    if claimed_nf_db is not None and totals["nf_db"] is not None:
        verdict["nf_headroom_db"] = float(claimed_nf_db) - totals["nf_db"]
        verdict["nf_pass"] = totals["nf_db"] <= float(claimed_nf_db)
    if claimed_iip3_dbm is not None and totals["iip3_dbm"] is not None:
        verdict["iip3_headroom_db"] = totals["iip3_dbm"] - float(claimed_iip3_dbm)
        verdict["iip3_pass"] = totals["iip3_dbm"] >= float(claimed_iip3_dbm)
    if claimed_total_gain_db is not None and totals["gain_db"] is not None:
        verdict["gain_delta_db"] = totals["gain_db"] - float(claimed_total_gain_db)
        # Allow ±3 dB slack on gain — it's usually VGA-adjustable.
        verdict["gain_pass"] = abs(verdict["gain_delta_db"]) <= 3.0

    return {
        "direction": "rx",
        "stages": stages,
        "totals": totals,
        "claims": {
            "nf_db": claimed_nf_db,
            "iip3_dbm": claimed_iip3_dbm,
            "total_gain_db": claimed_total_gain_db,
        },
        "verdict": verdict,
        "missing_spec_warnings": missing_spec_warnings,
    }


# ---------------------------------------------------------------------------
# TX cascade — forward-propagating
# ---------------------------------------------------------------------------

def _compute_tx_cascade(
    components: list[dict[str, Any]],
    *,
    claimed_pout_dbm: Optional[float],
    claimed_oip3_dbm: Optional[float],
    claimed_total_gain_db: Optional[float],
    claimed_pae_pct: Optional[float],
    input_power_dbm: float,
) -> dict[str, Any]:
    """TX-direction cascade: forward Pout accumulation, output-referred
    OIP3 cascade, system PAE from DC power roll-up.

    Key differences from the RX path:
      - OIP3 is specified per-stage as output-referred; the system OIP3
        follows 1/OIP3_sys = sum_k [ G_{after_k,lin} / OIP3_k_out_lin ],
        so the **last amplifier dominates** (no downstream gain to
        attenuate its nonlinearity).
      - Each stage's output power is the running input power plus its
        gain. If any stage's Pout spec is less than the computed
        drive, the chain is already in compression — flagged below.
      - PAE_system = (Pout_W - Pin_W) / sum(Pdc_W). Falls back to the
        arithmetic mean of per-stage PAE when Pdc values aren't
        populated — rough, but useful for a sanity figure.
    """
    stages = extract_stages(components)

    total_gain_db = 0.0
    cum_gain_lin = 1.0
    # Keep (index, oip3_out_lin_watts) tuples so we can post-compute
    # 1/OIP3_sys using the gain *after* each stage.
    oip3_tuples: list[tuple[int, float]] = []
    running_pout_dbm = input_power_dbm
    sum_pdc_w = 0.0
    pae_samples: list[float] = []

    compression_flags: list[str] = []

    for idx, s in enumerate(stages):
        g = s["gain_db"]
        oip3 = s["oip3_dbm"]
        pout_spec = s["pout_dbm"]
        pae_stage = s["pae_pct"]
        pdc_stage = s["pdc_w"]

        pin_this_stage = running_pout_dbm
        g_lin = _db_to_lin(g) if g is not None else 1.0
        if g is not None:
            total_gain_db += g
            running_pout_dbm = pin_this_stage + g

        s["pin_dbm"] = pin_this_stage
        s["pout_computed_dbm"] = running_pout_dbm
        s["cum_gain_db"] = _lin_to_db(cum_gain_lin * g_lin)

        # Compression check: does the stage's Pout spec allow the
        # computed drive? Flag when computed > spec + 1 dB (beyond P1dB).
        if pout_spec is not None and running_pout_dbm > pout_spec + 1.0:
            compression_flags.append(
                f"{s.get('part_number') or s.get('name') or f'stage-{idx}'}:"
                f" drive {running_pout_dbm:.1f} dBm vs spec {pout_spec:.1f} dBm"
            )
            s["compression_warning"] = True
        else:
            s["compression_warning"] = False

        # OIP3: store output-referred linear watts so we can post-scale
        # by downstream gain. Stored at the stage's *output*, consistent
        # with how datasheets quote OIP3.
        if oip3 is not None:
            oip3_lin_w = _db_to_lin(oip3) / 1000.0
            if oip3_lin_w > 0:
                oip3_tuples.append((idx, oip3_lin_w))

        # PAE bookkeeping. If per-stage PAE only is given (no Pdc), we
        # can still take a weighted mean later. If Pdc is given, roll it.
        if pae_stage is not None:
            pae_samples.append(float(pae_stage))
        if pdc_stage is not None:
            try:
                sum_pdc_w += float(pdc_stage)
            except (TypeError, ValueError):
                pass

        cum_gain_lin *= g_lin

    # System OIP3 (output-referred). Each stage k's output-referred
    # OIP3 reflected forward to the system output becomes
    # OIP3_k,sys = G_after_k,lin × OIP3_k,out_lin (both carrier and IM3
    # scale by the same downstream gain, so the intercept point moves
    # up by that gain). Contributions sum as reciprocals in watts:
    #   1 / OIP3_sys = sum_k  1 / (G_after_k,lin × OIP3_k,out_lin)
    total_lin_gain = cum_gain_lin
    recip_sum = 0.0
    for idx, oip3_w in oip3_tuples:
        # Gain from stage (idx+1) to end = total / prefix_through_idx
        prefix = 1.0
        for j in range(idx + 1):
            gj = stages[j].get("gain_db")
            if gj is not None:
                prefix *= _db_to_lin(gj)
        g_after = total_lin_gain / prefix if prefix > 0 else 1.0
        denom = g_after * oip3_w
        if denom > 0:
            recip_sum += 1.0 / denom

    system_oip3_dbm: Optional[float] = None
    if recip_sum > 0:
        oip3_w = 1.0 / recip_sum
        system_oip3_dbm = _lin_to_db(oip3_w * 1000.0)

    # Populate cum_oip3 per stage (referred to that stage's output).
    # When the chain passes through stage k with gain G_k, the earlier
    # stages' reciprocal contributions at stage (k-1)'s output get
    # divided by G_k (because OIP3 referred forward scales UP by G_k,
    # so 1/OIP3 scales DOWN by G_k). Then stage k's own 1/OIP3_k,out
    # adds on top.
    running_recip = 0.0
    for s in stages:
        g = s["gain_db"]
        g_lin = _db_to_lin(g) if g is not None else 1.0
        # Refer earlier stages' reciprocal through this stage's gain
        if g_lin > 0:
            running_recip = running_recip / g_lin
        if s["oip3_dbm"] is not None:
            oip3_w = _db_to_lin(s["oip3_dbm"]) / 1000.0
            if oip3_w > 0:
                running_recip += 1.0 / oip3_w
        s["cum_oip3_dbm"] = (_lin_to_db((1.0 / running_recip) * 1000.0)
                             if running_recip > 0 else None)

    # System PAE. Prefer the Pdc-based exact formula; fall back to mean.
    system_pae_pct: Optional[float] = None
    if sum_pdc_w > 0 and stages:
        pin_w = _db_to_lin(input_power_dbm) / 1000.0
        pout_w = _db_to_lin(running_pout_dbm) / 1000.0
        if pout_w > pin_w:
            system_pae_pct = (pout_w - pin_w) / sum_pdc_w * 100.0
    elif pae_samples:
        system_pae_pct = sum(pae_samples) / len(pae_samples)

    totals = {
        "pout_dbm": running_pout_dbm if stages else None,
        "gain_db": total_gain_db if stages else None,
        "oip3_dbm": system_oip3_dbm,
        "pae_pct": system_pae_pct,
        "pdc_total_w": sum_pdc_w if sum_pdc_w > 0 else None,
        "stage_count": len(stages),
        "input_power_dbm": input_power_dbm,
        "compression_warnings": compression_flags,
    }

    verdict: dict[str, Any] = {
        "pout_pass": None, "gain_pass": None,
        "oip3_pass": None, "pae_pass": None,
        "pout_headroom_db": None, "oip3_headroom_db": None,
        "gain_delta_db": None, "pae_delta_pct": None,
        "no_compression": len(compression_flags) == 0,
    }
    if claimed_pout_dbm is not None and totals["pout_dbm"] is not None:
        verdict["pout_headroom_db"] = totals["pout_dbm"] - float(claimed_pout_dbm)
        # Pout within +/- 1 dB of claim → pass (PAs are typically set
        # by supply trim).
        verdict["pout_pass"] = abs(verdict["pout_headroom_db"]) <= 1.0
    if claimed_oip3_dbm is not None and totals["oip3_dbm"] is not None:
        verdict["oip3_headroom_db"] = totals["oip3_dbm"] - float(claimed_oip3_dbm)
        verdict["oip3_pass"] = totals["oip3_dbm"] >= float(claimed_oip3_dbm)
    if claimed_total_gain_db is not None and totals["gain_db"] is not None:
        verdict["gain_delta_db"] = totals["gain_db"] - float(claimed_total_gain_db)
        verdict["gain_pass"] = abs(verdict["gain_delta_db"]) <= 3.0
    if claimed_pae_pct is not None and totals["pae_pct"] is not None:
        verdict["pae_delta_pct"] = totals["pae_pct"] - float(claimed_pae_pct)
        # PAE often falls short of claim; flag anything < -5% as a fail.
        verdict["pae_pass"] = verdict["pae_delta_pct"] >= -5.0

    return {
        "direction": "tx",
        "stages": stages,
        "totals": totals,
        "claims": {
            "pout_dbm": claimed_pout_dbm,
            "oip3_dbm": claimed_oip3_dbm,
            "total_gain_db": claimed_total_gain_db,
            "pae_pct": claimed_pae_pct,
        },
        "verdict": verdict,
    }
