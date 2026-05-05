"""
Structured DRC / ERC — P2.7.

The netlist_agent used to surface validation as free-form prose inside
`validation_notes`. That's human-readable but not machine-parseable, so
regressions slip through silently. This module replaces (augments) it
with a set of deterministic checks that emit structured violation rows.

Checks:
  1. **Shorts** — any net whose name looks like BOTH a power rail and
     a ground rail (e.g. endpoints of "VCC_GND" hint at a wiring error).
  2. **Power-net collisions** — a single node+pin landing on two
     different power nets (VCC_3V3 and VCC_5V0 on the same pin).
  3. **Floating nets** — any `signal_type == "signal"` net with only
     one endpoint, i.e. nothing receives it.
  4. **Orphan pins** — same (ref, pin) declared on multiple nets with
     different signal_types.
  5. **Unrecognised power-net naming** — a `power` signal_type whose
     name doesn't match the standard rail pattern.
  6. **Missing decoupling hint** — any active IC (has Vcc pin) without
     any capacitor-class node (C*) reference on the same power net.
     (Advisory only; RF layouts often rely on external decoupling.)

Output shape:

    {
      "checks_run": ["shorts","power_collision",...],
      "violations": [
        {"severity":"critical|high|medium|low|info",
         "rule": "shorts",
         "detail": "...",
         "location": "net/ref/pin"}
      ],
      "counts": {"critical":0,"high":0,"medium":0,"low":0,"info":0}
    }
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

_POWER_NAME_RE = re.compile(
    r"^(VCC|VDD|VEE|VSS|\+\d|AVDD|DVDD|VBAT|V\d+V\d+|\+?\d+V\d*)(_|$)",
    re.IGNORECASE,
)
_GROUND_NAME_RE = re.compile(
    r"^(GND|AGND|DGND|GNDA|GNDD|VSS|VGND|AC_?GND|RF_?GND|PGND|RGND)(_|$)",
    re.IGNORECASE,
)


def _is_power(name: str) -> bool:
    return bool(_POWER_NAME_RE.match(name or ""))


def _is_ground(name: str) -> bool:
    return bool(_GROUND_NAME_RE.match(name or ""))


def _is_capacitor_ref(ref: str) -> bool:
    return bool(re.match(r"^C\d", (ref or "").upper()))


# ---------------------------------------------------------------------------

def run_drc(netlist: dict[str, Any]) -> dict[str, Any]:
    """Run all DRC checks on a NetlistAgent-style payload with `nodes`
    and `edges` arrays. See module docstring for output shape."""
    nodes: list[dict] = list(netlist.get("nodes") or [])
    edges: list[dict] = list(netlist.get("edges") or [])
    power_nets: set[str] = set(netlist.get("power_nets") or [])
    ground_nets: set[str] = set(netlist.get("ground_nets") or [])

    violations: list[dict[str, Any]] = []

    # Build useful indexes upfront ------------------------------------------
    nets_to_endpoints: dict[str, list[tuple[str, str]]] = defaultdict(list)
    nets_to_type: dict[str, str] = {}
    pin_to_nets: dict[tuple[str, str], set[str]] = defaultdict(set)

    def _pin_key(ref: str, pin: str) -> tuple[str, str]:
        return (str(ref or ""), str(pin or ""))

    for e in edges:
        name = e.get("net_name") or e.get("signal") or ""
        if not name:
            continue
        s_ref = e.get("from_instance") or e.get("source")
        s_pin = e.get("from_pin") or e.get("source_pin")
        t_ref = e.get("to_instance") or e.get("target")
        t_pin = e.get("to_pin") or e.get("target_pin")
        if s_ref and s_pin is not None:
            nets_to_endpoints[name].append((s_ref, str(s_pin)))
            pin_to_nets[_pin_key(s_ref, s_pin)].add(name)
        if t_ref and t_pin is not None:
            nets_to_endpoints[name].append((t_ref, str(t_pin)))
            pin_to_nets[_pin_key(t_ref, t_pin)].add(name)
        stype = (e.get("signal_type") or e.get("type") or "").lower()
        if stype and name not in nets_to_type:
            nets_to_type[name] = stype

    nodes_by_ref = {
        (n.get("reference_designator") or n.get("instance_id") or n.get("id") or ""): n
        for n in nodes
    }

    # -- 1. Shorts — net name declared as both power and ground --------------
    for name in sorted(nets_to_endpoints):
        if _is_power(name) and _is_ground(name):
            violations.append({
                "severity": "critical", "rule": "short",
                "location": f"net/{name}",
                "detail": f"Net '{name}' matches both power and ground naming conventions.",
            })
        # A net listed in both the power_nets and ground_nets arrays is
        # likewise a short.
        if name in power_nets and name in ground_nets:
            violations.append({
                "severity": "critical", "rule": "short",
                "location": f"net/{name}",
                "detail": f"Net '{name}' is declared as both a power and a ground rail.",
            })

    # -- 2. Power-net collision on a single pin -----------------------------
    # Track pins that already triggered a power_collision so the generic
    # rule 2b doesn't double-flag them.
    power_collided: set[tuple[str, str]] = set()
    for (ref, pin), names in pin_to_nets.items():
        power_hits = [n for n in names if _is_power(n) or n in power_nets]
        if len(set(power_hits)) >= 2:
            violations.append({
                "severity": "critical", "rule": "power_collision",
                "location": f"pin/{ref}.{pin}",
                "detail": (
                    f"Pin {ref}.{pin} is connected to multiple power nets: "
                    + ", ".join(sorted(set(power_hits)))
                ),
            })
            power_collided.add((ref, pin))

    # -- 2b. ANY pin on multiple distinct nets (P5 — schematic short hunt) --
    # A pin is one electrical node. If two distinct net names land on it,
    # those nets are shorted together. Rule 2 only catches the case where
    # both nets are power rails; this rule catches the harder-to-spot
    # case of a signal/clock/analog short — exactly the failure mode of
    # the off-page-connector aliasing bug (single connector pin reused
    # for IF_OUT_P + IF_OUT_N, or LO_P + LO_N, collapsing the pair).
    #
    # We deliberately allow legitimate "split nets" — two nets carrying
    # the same logical signal that happen to share a name suffix — by
    # comparing distinct net *names*, not endpoint counts. Fan-out of
    # one net to N endpoints is fine.
    for (ref, pin), names in pin_to_nets.items():
        if (ref, pin) in power_collided:
            continue  # already flagged as a power_collision (rule 2)
        unique_nets = sorted(set(names))
        if len(unique_nets) < 2:
            continue
        # Categorise the colliding nets so the message is actionable.
        types = sorted({nets_to_type.get(n, "") for n in unique_nets} - {""})
        type_hint = f"types: {', '.join(types)}" if types else "(no signal_type set)"
        violations.append({
            "severity": "high", "rule": "pin_multiple_nets",
            "location": f"pin/{ref}.{pin}",
            "detail": (
                f"Pin {ref}.{pin} is connected to {len(unique_nets)} "
                f"distinct nets — {', '.join(unique_nets)} — {type_hint}. "
                "A single pin is one electrical node, so these nets are "
                "shorted together. If this is a differential pair "
                "(_P/_N) tied to one off-page connector pin, give each "
                "polarity its own connector pin."
            ),
        })

    # -- 3. Floating signal nets (fewer than 2 endpoints) -------------------
    # NOTE: power + ground nets are checked in rule 3b below, which applies
    # different semantics — they're fine with one trace segment as long as
    # at least one source + one sink are present somewhere in the payload.
    for name, endpoints in nets_to_endpoints.items():
        unique = {(r, p) for r, p in endpoints}
        stype = nets_to_type.get(name, "")
        if stype in ("power", "ground"):
            continue
        if len(unique) < 2:
            violations.append({
                "severity": "high", "rule": "floating_net",
                "location": f"net/{name}",
                "detail": (
                    f"Net '{name}' has {len(unique)} endpoint(s); "
                    "signal nets need at least one driver + one receiver."
                ),
            })

    # -- 3b. Dangling power / ground rails (P1.5) --------------------------
    # A power rail with only a single endpoint (the IC's VCC pin) and
    # nothing driving it — no regulator, no connector, no decap — is
    # fatal in silicon. Rule 3 exempted "power" / "ground" types from
    # the 2-endpoint requirement because segment-level layouts are
    # legal. This rule reintroduces the check at *rail* level: every
    # named power/ground rail in the payload must have ≥2 unique
    # endpoints OR appear as a driver ref (regulator / connector).
    _DRIVER_REF_PATTERNS = ("PWR", "REG", "VREG", "LDO", "PSU", "U_VREG",
                            "J_PWR", "J1", "J_VCC", "CONN")

    def _looks_like_driver(ref: str) -> bool:
        r = (ref or "").upper()
        return any(r.startswith(p) for p in _DRIVER_REF_PATTERNS)

    for name, endpoints in nets_to_endpoints.items():
        stype = nets_to_type.get(name, "")
        if stype not in ("power", "ground"):
            # Also catch nets named like rails even if signal_type wasn't set
            if not (_is_power(name) or _is_ground(name) or name in power_nets
                    or name in ground_nets):
                continue
        unique = {(r, p) for r, p in endpoints}
        if len(unique) < 2:
            violations.append({
                "severity": "high", "rule": "dangling_power_rail",
                "location": f"net/{name}",
                "detail": (
                    f"Power/ground rail '{name}' has only {len(unique)} "
                    "endpoint(s); no driver found. The rail isn't connected "
                    "to a regulator, supply connector, or bulk cap."
                ),
            })
            continue
        # Rail has ≥2 endpoints — verify at least one looks like a driver
        # (regulator output, supply connector, battery, etc.).  A rail
        # where every endpoint is an IC Vcc pin with no upstream source
        # is a silent integration failure.
        refs = {r for r, _ in unique}
        if not any(_looks_like_driver(r) for r in refs):
            violations.append({
                "severity": "medium", "rule": "power_rail_no_driver",
                "location": f"net/{name}",
                "detail": (
                    f"Power/ground rail '{name}' has {len(unique)} endpoints "
                    "but none of the reference designators look like a driver "
                    "(PWR*, REG*, LDO*, CONN*). Verify a supply source is "
                    "actually connected."
                ),
            })

    # -- 4. Unrecognised power naming ---------------------------------------
    for name, stype in nets_to_type.items():
        if stype != "power":
            continue
        if not _is_power(name):
            violations.append({
                "severity": "low", "rule": "power_naming",
                "location": f"net/{name}",
                "detail": (
                    f"Power net '{name}' does not match the standard rail "
                    "naming convention (VCC_*, VDD_*, V3V3, +5V, etc.)."
                ),
            })

    # -- 5. Missing decoupling hint (advisory) ------------------------------
    # For every named power net, check whether any capacitor-class ref is
    # attached. This is cheap and catches the egregious "no bulk caps"
    # mistake without requiring schematic knowledge of pin capacitance.
    known_power_nets = [
        n for n, st in nets_to_type.items() if st == "power" or _is_power(n)
    ]
    for pnet in known_power_nets:
        refs = {r for r, _ in nets_to_endpoints.get(pnet, [])}
        if not any(_is_capacitor_ref(r) for r in refs):
            if refs:  # only flag nets that have *some* endpoints
                violations.append({
                    "severity": "medium", "rule": "missing_decap",
                    "location": f"net/{pnet}",
                    "detail": (
                        f"Power net '{pnet}' has no capacitor-class reference "
                        "(C*) attached — add bulk + bypass decoupling."
                    ),
                })

    # -- 5b. Clock-domain crossing without declared synchronisers (P2.7) ---
    # A design that references ≥2 distinct clock nets AND carries any
    # signal edge between their associated components without a CDC
    # synchroniser is a metastability risk. We can't parse RTL from a
    # schematic JSON, so we use a conservative heuristic: if ≥2 clock
    # nets exist and no node carries a name / MPN hint for a CDC cell
    # (FIFO, synchroniser, dual-port RAM), raise a medium-severity
    # advisory. Caught once per design, not per-path.
    clock_nets = {
        n for n, st in nets_to_type.items() if st == "clock"
    }
    # Also treat nets named like clocks even if signal_type wasn't set.
    for name in nets_to_endpoints:
        if re.search(r"(?:^|_)(CLK|SCLK|MCLK|SCK|CLOCK)(?:_|$)",
                     name, re.IGNORECASE):
            clock_nets.add(name)
    if len(clock_nets) >= 2:
        # Look for CDC-cell hints in node descriptions / part numbers.
        cdc_hints = re.compile(
            r"(?:CDC|FIFO|SYNC|ASYNC|dual[- ]?port|2FF|synchroniser|synchronizer)",
            re.IGNORECASE,
        )
        has_cdc_cell = False
        for n in nodes:
            blob = " ".join(str(n.get(k) or "") for k in
                            ("part_number", "component_name", "name",
                             "description"))
            if cdc_hints.search(blob):
                has_cdc_cell = True
                break
        if not has_cdc_cell:
            violations.append({
                "severity": "medium", "rule": "cdc_boundary_undeclared",
                "location": "clocks/" + ",".join(sorted(clock_nets)),
                "detail": (
                    f"Design has {len(clock_nets)} distinct clock domains "
                    "(" + ", ".join(sorted(clock_nets)) + ") but no "
                    "CDC synchroniser / FIFO / dual-port cell is declared "
                    "in the BOM. Metastability risk unless RTL adds 2FF "
                    "synchronisers at every crossing."
                ),
            })

    # -- 6. Unknown component reference on an edge --------------------------
    known_refs = set(nodes_by_ref.keys())
    if known_refs:  # skip when the caller didn't pass nodes
        referenced: set[str] = set()
        for name, endpoints in nets_to_endpoints.items():
            for r, _ in endpoints:
                referenced.add(r)
        dangling = sorted(r for r in referenced if r not in known_refs and r)
        for r in dangling:
            violations.append({
                "severity": "high", "rule": "unknown_ref",
                "location": f"ref/{r}",
                "detail": f"Reference designator '{r}' appears in nets but not in the component list.",
            })

    # ------------------------------------------------------------------ meta
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for v in violations:
        counts[v.get("severity", "info")] = counts.get(v.get("severity", "info"), 0) + 1

    return {
        "checks_run": [
            "shorts", "power_collision", "pin_multiple_nets", "floating_net",
            "dangling_power_rail", "power_rail_no_driver",
            "power_naming", "missing_decap", "cdc_boundary_undeclared",
            "unknown_ref",
        ],
        "violations": violations,
        "counts": counts,
        "overall_pass": counts["critical"] == 0 and counts["high"] == 0,
    }


# ---------------------------------------------------------------------------
# Schematic-shape adapter (P1 — close the post-synthesis blind spot)
# ---------------------------------------------------------------------------
#
# `_synthesize_schematic` in the netlist agent emits a different shape
# than `run_drc` understands: a list of `sheets`, each with `components`
# and `nets[].endpoints[]`. The schematic post-synthesis adds connectors,
# off-page nets, decoupling caps, terminations and test points that the
# pre-synthesis DRC never sees, so a bad schematic can still ship even
# when `netlist_drc.json` reports `overall_pass: True`.
#
# `flatten_schematic_to_netlist` translates the schematic shape back into
# the `nodes` + `edges` form that `run_drc` reads, so the same set of
# rules (especially `pin_multiple_nets` for the off-page-connector
# aliasing bug) applies to the post-synthesis output. `run_schematic_drc`
# is the convenience wrapper used by callers that already have a
# `schematic_data` dict in hand.

def flatten_schematic_to_netlist(schematic_data: dict[str, Any]) -> dict[str, Any]:
    """Convert a multi-sheet schematic dict into `{nodes, edges, ...}`.

    Each sheet's components become nodes (with sheet-of-origin recorded
    on `_sheet`); each net becomes an edge per consecutive endpoint
    pair on that net (a star net with N>2 endpoints unfolds into N-1
    edges sharing the same `net_name`). Power and ground rails are
    derived from `net.type`.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    power_nets: set[str] = set()
    ground_nets: set[str] = set()

    seen_refs: set[str] = set()
    sheets = schematic_data.get("sheets") or []
    for sheet in sheets:
        sheet_id = sheet.get("id") or sheet.get("title") or ""
        for c in sheet.get("components") or []:
            ref = c.get("ref") or c.get("reference_designator") or ""
            if not ref or ref in seen_refs:
                continue
            seen_refs.add(ref)
            nodes.append({
                "instance_id": ref,
                "reference_designator": ref,
                "part_number": c.get("part_number") or c.get("value") or "",
                "component_name": c.get("value") or c.get("part_number") or "",
                "pins": c.get("pins") or [],
                "_sheet": sheet_id,
                "_type": c.get("type") or "",
            })
        for net in sheet.get("nets") or []:
            name = net.get("name") or ""
            ntype = (net.get("type") or "").lower()
            endpoints = net.get("endpoints") or []
            if not name or len(endpoints) < 2:
                # A net with one endpoint becomes a half-edge so the
                # floating-net rule still fires on it.
                if name and len(endpoints) == 1:
                    ep = endpoints[0]
                    edges.append({
                        "net_name": name,
                        "from_instance": ep.get("ref"),
                        "from_pin": ep.get("pin"),
                        "to_instance": None,
                        "to_pin": None,
                        "signal_type": ntype,
                    })
                continue
            # Convert the star net into N-1 edges. Sharing the same
            # `net_name` is what `pin_to_nets` keys on, so all endpoints
            # land in the same equivalence class regardless of how the
            # star is decomposed.
            anchor = endpoints[0]
            for ep in endpoints[1:]:
                edges.append({
                    "net_name": name,
                    "from_instance": anchor.get("ref"),
                    "from_pin": anchor.get("pin"),
                    "to_instance": ep.get("ref"),
                    "to_pin": ep.get("pin"),
                    "signal_type": ntype,
                })
            if ntype == "power":
                power_nets.add(name)
            elif ntype == "ground":
                ground_nets.add(name)

    return {
        "nodes": nodes,
        "edges": edges,
        "power_nets": sorted(power_nets),
        "ground_nets": sorted(ground_nets),
    }


def run_schematic_drc(schematic_data: dict[str, Any]) -> dict[str, Any]:
    """Flatten a multi-sheet schematic and run the same DRC rule set
    over it. Used as the post-synthesis check to catch additions made
    during `_synthesize_schematic` (off-page connectors, terminations,
    test points, decoupling caps) that never appear in the pre-synthesis
    netlist.

    Also runs the SCH-only checks added in P26.5: floating endpoints,
    RF_IN/RF_OUT connectivity, decoupling-cap topology, and waypoint
    coincidence (logical proxy for "missing junction dot")."""
    flat = flatten_schematic_to_netlist(schematic_data)
    drc = run_drc(flat)
    advanced = run_advanced_schematic_drc(schematic_data)
    drc["violations"].extend(advanced["violations"])
    for sev, n in advanced["counts"].items():
        drc["counts"][sev] = drc["counts"].get(sev, 0) + n
    drc["sch_issues"] = advanced["violations"]
    drc["overall_pass"] = drc["counts"]["critical"] == 0 and drc["counts"]["high"] == 0
    drc["source"] = "schematic_post_synthesis"
    drc["sheet_count"] = len(schematic_data.get("sheets") or [])
    return drc


# ---------------------------------------------------------------------------
# Advanced SCH checks — operate on the raw schematic_data structure (not
# the flattened netlist), so we keep per-pin / per-component / per-waypoint
# context for actionable error messages.
# ---------------------------------------------------------------------------

_RF_NET_RE = re.compile(r"^RF_(IN|OUT)(_\d+)?$", re.IGNORECASE)
_PIN_KEY = tuple[str, str]


def _build_pin_index(schematic_data: dict[str, Any]) -> dict[_PIN_KEY, dict[str, Any]]:
    """(ref, pin) -> {component, sheet_id, type, value}."""
    idx: dict[_PIN_KEY, dict[str, Any]] = {}
    for sheet in schematic_data.get("sheets") or []:
        sid = sheet.get("id") or sheet.get("title") or ""
        for c in sheet.get("components") or []:
            ref = c.get("ref") or ""
            ctype = (c.get("type") or "").lower()
            if not ref:
                continue
            pins = c.get("pins") or []
            if pins:
                for p in pins:
                    pnum = str(p.get("num") or p.get("name") or "")
                    if pnum:
                        idx[(ref, pnum)] = {
                            "component": c, "sheet_id": sid,
                            "type": ctype, "value": c.get("value"),
                        }
            else:
                # Two-pin passives (R, C, L, D) don't enumerate pins in
                # the schema — they implicitly have pins "1" and "2".
                if ctype in ("resistor", "capacitor", "capacitor_polar",
                             "inductor", "diode", "diode_zener",
                             "diode_tvs", "diode_led"):
                    for pnum in ("1", "2"):
                        idx[(ref, pnum)] = {
                            "component": c, "sheet_id": sid,
                            "type": ctype, "value": c.get("value"),
                        }
    return idx


def _build_pin_to_nets(schematic_data: dict[str, Any]) -> dict[_PIN_KEY, list[dict[str, Any]]]:
    """(ref, pin) -> list of nets that endpoint at this pin."""
    out: dict[_PIN_KEY, list[dict[str, Any]]] = defaultdict(list)
    for sheet in schematic_data.get("sheets") or []:
        for net in sheet.get("nets") or []:
            for ep in net.get("endpoints") or []:
                ref = ep.get("ref") or ""
                pin = str(ep.get("pin") or "")
                if ref and pin:
                    out[(ref, pin)].append(net)
    return out


def _is_decoupling_cap(comp: dict[str, Any]) -> bool:
    if (comp.get("type") or "").lower() not in ("capacitor", "capacitor_polar"):
        return False
    val = (comp.get("value") or "").strip().lower().replace(" ", "")
    if not val:
        return True
    # Decoupling range: 1pF .. 100µF. Anything bigger is a bulk cap, still
    # decoupling-class for our topology check.
    return any(u in val for u in ("pf", "nf", "uf", "µf", "f"))


def run_advanced_schematic_drc(schematic_data: dict[str, Any]) -> dict[str, Any]:
    """Four schematic-shape checks. Returns the same {violations, counts}
    shape as `run_drc` so it can be merged into the parent DRC report.
    """
    violations: list[dict[str, Any]] = []
    pin_idx = _build_pin_index(schematic_data)
    pin_to_nets = _build_pin_to_nets(schematic_data)
    sheets = schematic_data.get("sheets") or []

    # -- Check 1: floating / invalid endpoints --
    # Every (ref, pin) in a net's endpoints must reference a real component
    # pin. Catches "wire drawn but not snapped to pin" and typo refs.
    for sheet in sheets:
        sid = sheet.get("id") or sheet.get("title") or ""
        for net in sheet.get("nets") or []:
            nname = net.get("name") or "<unnamed>"
            for ep in net.get("endpoints") or []:
                ref = ep.get("ref") or ""
                pin = str(ep.get("pin") or "")
                if not ref or not pin:
                    violations.append({
                        "severity": "high",
                        "rule": "endpoint_missing_ref",
                        "type": "endpoint_invalid",
                        "net": nname,
                        "ref": ref or None,
                        "pin": pin or None,
                        "detail": (
                            f"Net '{nname}' on sheet '{sid}' has an endpoint "
                            f"missing ref or pin (ref={ref!r}, pin={pin!r})"
                        ),
                        "location": f"sheet:{sid} net:{nname}",
                    })
                    continue
                if (ref, pin) not in pin_idx:
                    violations.append({
                        "severity": "critical",
                        "rule": "endpoint_invalid_pin",
                        "type": "floating_endpoint",
                        "net": nname,
                        "ref": ref,
                        "pin": pin,
                        "detail": (
                            f"Net '{nname}' references {ref}.pin{pin} but "
                            f"that pin doesn't exist (component missing or "
                            f"pin number wrong) — wire drawn without "
                            f"snapping to a pin"
                        ),
                        "location": f"{ref}.pin{pin}",
                    })

    # -- Check 2: RF_IN_* / RF_OUT_* connectivity --
    # Every RF net needs ≥2 endpoints and each endpoint must terminate on
    # an IC or connector pin (not a stray net_label / ground symbol).
    for sheet in sheets:
        sid = sheet.get("id") or sheet.get("title") or ""
        for net in sheet.get("nets") or []:
            nname = net.get("name") or ""
            if not _RF_NET_RE.match(nname):
                continue
            eps = net.get("endpoints") or []
            if len(eps) < 2:
                violations.append({
                    "severity": "high",
                    "rule": "rf_net_underdriven",
                    "type": "rf_floating",
                    "net": nname,
                    "ref": eps[0].get("ref") if eps else None,
                    "pin": str(eps[0].get("pin")) if eps else None,
                    "detail": (
                        f"RF net '{nname}' has {len(eps)} endpoint(s); "
                        f"need ≥2 — wire likely drawn but not landing on "
                        f"an IC pin"
                    ),
                    "location": (
                        f"{eps[0].get('ref')}.pin{eps[0].get('pin')}"
                        if eps else f"sheet:{sid}"
                    ),
                })
                continue
            for ep in eps:
                ref = ep.get("ref") or ""
                pin = str(ep.get("pin") or "")
                meta = pin_idx.get((ref, pin))
                if not meta:
                    continue  # already flagged by check 1
                if meta["type"] not in ("ic", "connector"):
                    violations.append({
                        "severity": "high",
                        "rule": "rf_net_terminates_on_passive",
                        "type": "rf_endpoint_not_ic",
                        "net": nname,
                        "ref": ref,
                        "pin": pin,
                        "detail": (
                            f"RF net '{nname}' terminates on {ref}.pin{pin} "
                            f"(type={meta['type']!r}); RF inputs/outputs "
                            f"should land on IC or connector pins, not "
                            f"passive components"
                        ),
                        "location": f"{ref}.pin{pin}",
                    })

    # -- Check 3: decoupling-capacitor topology --
    # Each cap should have exactly one pin on a power net and one on a
    # ground net. Catches: floating cap pin, both pins on power, both
    # pins on ground, neither pin on power.
    for sheet in sheets:
        sid = sheet.get("id") or sheet.get("title") or ""
        for c in sheet.get("components") or []:
            if not _is_decoupling_cap(c):
                continue
            ref = c.get("ref") or ""
            if not ref:
                continue
            pin_nets: dict[str, list[dict[str, Any]]] = {
                "1": pin_to_nets.get((ref, "1")) or [],
                "2": pin_to_nets.get((ref, "2")) or [],
            }
            for pn in ("1", "2"):
                if not pin_nets[pn]:
                    violations.append({
                        "severity": "critical",
                        "rule": "cap_pin_floating",
                        "type": "cap_pin_floating",
                        "net": None,
                        "ref": ref,
                        "pin": pn,
                        "detail": (
                            f"Decoupling cap {ref}.pin{pn} is not on any "
                            f"net (floating terminal — cap can't bypass)"
                        ),
                        "location": f"{ref}.pin{pn}",
                    })
            if not pin_nets["1"] or not pin_nets["2"]:
                continue
            n1 = (pin_nets["1"][0].get("name") or "")
            n2 = (pin_nets["2"][0].get("name") or "")
            t1 = (pin_nets["1"][0].get("type") or "").lower()
            t2 = (pin_nets["2"][0].get("type") or "").lower()
            is_pow1 = t1 == "power" or _is_power(n1)
            is_pow2 = t2 == "power" or _is_power(n2)
            is_gnd1 = t1 == "ground" or _is_ground(n1)
            is_gnd2 = t2 == "ground" or _is_ground(n2)
            if not ((is_pow1 and is_gnd2) or (is_pow2 and is_gnd1)):
                violations.append({
                    "severity": "high",
                    "rule": "cap_topology_invalid",
                    "type": "cap_topology_invalid",
                    "net": f"{n1} | {n2}",
                    "ref": ref,
                    "pin": "1,2",
                    "detail": (
                        f"Decoupling cap {ref} has pin1='{n1}' (power={is_pow1} "
                        f"gnd={is_gnd1}) and pin2='{n2}' (power={is_pow2} "
                        f"gnd={is_gnd2}); expected one power + one ground"
                    ),
                    "location": f"{ref}.pin1={n1}, {ref}.pin2={n2}",
                })

    # -- Check 4: waypoint coincidence — proxy for "missing junction dot" --
    # Without explicit wire segments we can't geometry-check crossings,
    # but two DIFFERENT nets sharing the exact same waypoint coordinate
    # is a strong implicit-short signal: the renderer will draw the two
    # wires touching at that point with no visual junction.
    waypoint_owners: dict[tuple[float, float, str], list[str]] = defaultdict(list)
    for sheet in sheets:
        sid = sheet.get("id") or sheet.get("title") or ""
        for net in sheet.get("nets") or []:
            nname = net.get("name") or ""
            for wp in net.get("waypoints") or []:
                try:
                    key = (float(wp["x"]), float(wp["y"]), sid)
                except (KeyError, TypeError, ValueError):
                    continue
                waypoint_owners[key].append(nname)
    for (x, y, sid), names in waypoint_owners.items():
        unique = sorted(set(n for n in names if n))
        if len(unique) > 1:
            violations.append({
                "severity": "medium",
                "rule": "junction_missing",
                "type": "junction_missing",
                "net": ", ".join(unique),
                "ref": None,
                "pin": None,
                "detail": (
                    f"Waypoint ({x:.0f},{y:.0f}) on sheet '{sid}' is shared "
                    f"by {len(unique)} different nets ({', '.join(unique)}) "
                    f"— two crossing wires need either a junction dot or "
                    f"the same net name"
                ),
                "location": f"sheet:{sid} ({x:.0f},{y:.0f})",
            })

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for v in violations:
        counts[v["severity"]] = counts.get(v["severity"], 0) + 1
    return {
        "checks_run": [
            "floating_endpoints", "rf_net_connectivity",
            "decoupling_topology", "waypoint_coincidence",
        ],
        "violations": violations,
        "counts": counts,
    }


def _is_ground(name: str) -> bool:
    return bool(_GROUND_NAME_RE.match(name or ""))
