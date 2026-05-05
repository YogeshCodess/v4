#!/usr/bin/env python3
"""
Expand domains/standards.json from the 17-clause seed to >= 40 entries
(target C2.2 in IMPLEMENTATION_PLAN.md).

Every added clause is a real, publicly documented MIL-STD / DO / STANAG / FCC /
IEEE entry. No fabricated clause IDs. Human reviewers should still spot-check
against the source documents before locking requirements against them.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

ALL_DOMAINS = ["airborne", "ground-mobile", "naval", "submarine",
               "satcom", "communication", "radar", "ew", "space"]

ADDITIONS = [
    # --- MIL-STD-461G additions ---------------------------------------------
    {"standard": "MIL-STD-461G", "clause": "CE106",
     "short_title": "Conducted Emissions, Antenna Port",
     "description": "Conducted emissions at transmitter antenna ports, 10 kHz to 40 GHz. Controls out-of-band/harmonic energy from transmitters.",
     "typical_applicability": ["radar", "ew", "satcom", "communication"],
     "severity": "required"},
    {"standard": "MIL-STD-461G", "clause": "CS103",
     "short_title": "Conducted Susceptibility, Antenna Port Intermodulation",
     "description": "Two-tone IMD susceptibility test at receiver antenna ports. Critical for co-site interference resilience.",
     "typical_applicability": ["ew", "communication", "radar"],
     "severity": "required"},
    {"standard": "MIL-STD-461G", "clause": "CS104",
     "short_title": "Conducted Susceptibility, Rejection of Undesired Signals",
     "description": "Out-of-band signal rejection at receiver antenna ports. Verifies filter/preselector performance.",
     "typical_applicability": ["ew", "communication", "radar"],
     "severity": "required"},
    {"standard": "MIL-STD-461G", "clause": "CS105",
     "short_title": "Conducted Susceptibility, Cross-Modulation",
     "description": "Cross-modulation susceptibility test for antenna-connected receivers.",
     "typical_applicability": ["ew", "communication"],
     "severity": "required"},
    {"standard": "MIL-STD-461G", "clause": "CS115",
     "short_title": "Conducted Susceptibility, Bulk Cable Injection Impulse Excitation",
     "description": "Impulse excitation bulk-cable injection test. Simulates ESD/transient energy coupled onto the cabling.",
     "typical_applicability": ["airborne", "ground-mobile", "naval"],
     "severity": "required"},
    {"standard": "MIL-STD-461G", "clause": "CS116",
     "short_title": "Conducted Susceptibility, Damped Sinusoidal Transients",
     "description": "Damped sinusoidal transient injection 10 kHz to 100 MHz on cables and power leads.",
     "typical_applicability": ["airborne", "ground-mobile", "naval"],
     "severity": "required"},
    {"standard": "MIL-STD-461G", "clause": "CS117",
     "short_title": "Conducted Susceptibility, Lightning Induced Transients",
     "description": "Pin-injection and cable-bundle tests simulating indirect lightning effects.",
     "typical_applicability": ["airborne"],
     "severity": "required"},
    {"standard": "MIL-STD-461G", "clause": "RE103",
     "short_title": "Radiated Emissions, Antenna Spurious and Harmonic Outputs",
     "description": "Spurious and harmonic radiated emissions from antenna port of transmitters.",
     "typical_applicability": ["radar", "ew", "satcom", "communication"],
     "severity": "required"},
    {"standard": "MIL-STD-461G", "clause": "RS101",
     "short_title": "Radiated Susceptibility, Magnetic Field",
     "description": "Radiated magnetic-field susceptibility 30 Hz to 100 kHz. Navy and some Army applications.",
     "typical_applicability": ["naval", "submarine"],
     "severity": "required"},
    {"standard": "MIL-STD-461G", "clause": "RS105",
     "short_title": "Radiated Susceptibility, Transient Electromagnetic Field",
     "description": "EMP-style transient field susceptibility test. Required for shielded UUTs with platform-level EMP requirements.",
     "typical_applicability": ["airborne", "ground-mobile", "naval"],
     "severity": "required"},

    # --- MIL-STD-810H additions ---------------------------------------------
    {"standard": "MIL-STD-810H", "clause": "Method 500.6",
     "short_title": "Low Pressure (Altitude)",
     "description": "Low pressure / altitude operational and storage testing. Required for airborne and high-altitude platforms.",
     "typical_applicability": ["airborne"],
     "severity": "required"},
    {"standard": "MIL-STD-810H", "clause": "Method 507.6",
     "short_title": "Humidity",
     "description": "Humidity exposure — aggravated and cyclic procedures. Required for equipment deployed in humid climates.",
     "typical_applicability": ["airborne", "ground-mobile", "naval"],
     "severity": "required"},
    {"standard": "MIL-STD-810H", "clause": "Method 509.7",
     "short_title": "Salt Fog",
     "description": "Salt fog corrosion test for equipment used near marine environments.",
     "typical_applicability": ["naval", "airborne"],
     "severity": "required"},
    {"standard": "MIL-STD-810H", "clause": "Method 510.7",
     "short_title": "Sand and Dust",
     "description": "Blowing dust and sand tests. Required for ground platforms in arid environments.",
     "typical_applicability": ["ground-mobile", "airborne"],
     "severity": "required"},
    {"standard": "MIL-STD-810H", "clause": "Method 511.7",
     "short_title": "Explosive Atmosphere",
     "description": "Operation of electronics in fuel-vapor / explosive atmospheres. Critical for aircraft and vehicle fuel-proximate electronics.",
     "typical_applicability": ["airborne", "ground-mobile"],
     "severity": "required"},
    {"standard": "MIL-STD-810H", "clause": "Method 521.4",
     "short_title": "Icing/Freezing Rain",
     "description": "Icing and freezing rain tests for exposed electronics (antennas, external sensors).",
     "typical_applicability": ["airborne", "naval"],
     "severity": "recommended"},

    # --- MIL-STD-704F (aircraft power) --------------------------------------
    {"standard": "MIL-STD-704F", "clause": "Section 4",
     "short_title": "Aircraft Electric Power, 115/200 VAC 3-phase",
     "description": "Steady-state, transient, and fault requirements for 115/200 VAC three-phase aircraft power.",
     "typical_applicability": ["airborne"],
     "severity": "required"},

    # --- DO-254 / DO-160G ----------------------------------------------------
    {"standard": "DO-160G", "clause": "Section 20",
     "short_title": "Radio Frequency Susceptibility (Radiated and Conducted)",
     "description": "Civil airborne RF susceptibility testing, 10 kHz to 40 GHz. Complement to Section 21 emissions.",
     "typical_applicability": ["airborne"],
     "severity": "required"},
    {"standard": "DO-160G", "clause": "Section 22",
     "short_title": "Lightning Induced Transient Susceptibility",
     "description": "Pin-injection and cable-bundle tests for indirect lightning effects on civil airborne equipment.",
     "typical_applicability": ["airborne"],
     "severity": "required"},
    {"standard": "DO-160G", "clause": "Section 25",
     "short_title": "Electrostatic Discharge (ESD)",
     "description": "Human-body-model ESD test for civil airborne equipment accessible to personnel.",
     "typical_applicability": ["airborne"],
     "severity": "required"},

    # --- STANAG / NATO -------------------------------------------------------
    {"standard": "STANAG 4193", "clause": "Part V",
     "short_title": "Identification Friend-or-Foe (IFF) Mode 5 Technical Characteristics",
     "description": "Mandates cryptographically secured IFF Mode 5 signals for NATO interoperability. Relevant to radar front-ends that implement IFF interrogation / transponder functions.",
     "typical_applicability": ["radar", "airborne"],
     "severity": "required"},
    {"standard": "STANAG 4609", "clause": "Edition 4",
     "short_title": "NATO Digital Motion Imagery Standard",
     "description": "Motion imagery metadata and encoding standard for tactical ISR systems.",
     "typical_applicability": ["ew", "airborne", "communication"],
     "severity": "recommended"},
    {"standard": "MIL-STD-188-181", "clause": "C",
     "short_title": "UHF SATCOM DAMA (5 kHz/25 kHz)",
     "description": "DAMA waveform requirements for military UHF SATCOM (FNBDP).",
     "typical_applicability": ["satcom", "communication"],
     "severity": "required"},
    {"standard": "MIL-STD-188-200", "clause": "Section 5",
     "short_title": "System Design and Engineering Standards for Tactical Communications",
     "description": "Baseline system-engineering requirements for tactical multi-channel radio.",
     "typical_applicability": ["communication"],
     "severity": "recommended"},

    # --- FCC Part 15 (CR compliance required for all non-military) ----------
    {"standard": "FCC Part 15", "clause": "15.247",
     "short_title": "Operation Within the Bands 902-928 MHz, 2.400-2.4835 GHz, 5.725-5.850 GHz",
     "description": "ISM unlicensed operation rules for frequency-hopping and digitally modulated systems.",
     "typical_applicability": ["communication"],
     "severity": "required"},
    {"standard": "FCC Part 15", "clause": "15.249",
     "short_title": "Operation Within the Bands 902-928 MHz, 2400-2483.5 MHz, 5725-5875 MHz (Low Power)",
     "description": "Low-power unlicensed operation limits for ISM band devices.",
     "typical_applicability": ["communication"],
     "severity": "required"},

    # --- IEEE / IEC ----------------------------------------------------------
    {"standard": "IEEE 1413.1", "clause": "Section 6",
     "short_title": "Reliability Prediction of Electronic Equipment",
     "description": "Guide for selecting and using reliability prediction methodologies (MTBF/MTTR).",
     "typical_applicability": ["radar", "ew", "satcom", "communication"],
     "severity": "recommended"},
    {"standard": "IEC 60068-2-30", "clause": "Db",
     "short_title": "Damp Heat Cyclic",
     "description": "Cyclic humidity test for commercial/industrial electronics.",
     "typical_applicability": ["communication"],
     "severity": "recommended"},

    # --- MIL-STD-1275 (ground vehicle power) --------------------------------
    {"standard": "MIL-STD-1275E", "clause": "Section 5",
     "short_title": "Characteristics of 28 VDC Ground Vehicle Power",
     "description": "Steady-state and transient requirements on 28 VDC vehicle power.",
     "typical_applicability": ["ground-mobile"],
     "severity": "required"},

    # --- MIL-STD-883 screening (for parts used in space / military) ---------
    {"standard": "MIL-STD-883", "clause": "Method 5004",
     "short_title": "Screening Procedures for Microcircuits",
     "description": "Class B / Class S screening sequence for microcircuits including burn-in, temperature cycling, and final electrical tests.",
     "typical_applicability": ["radar", "ew", "satcom", "space"],
     "severity": "required"},
    {"standard": "MIL-PRF-38535", "clause": "Class Q",
     "short_title": "Qualified Manufacturer's List (QML) Class Q",
     "description": "Military-grade microcircuit qualification level. Required for many programs of record.",
     "typical_applicability": ["radar", "ew", "satcom"],
     "severity": "required"},

    # --- Radiation -----------------------------------------------------------
    {"standard": "MIL-STD-883", "clause": "Method 1019",
     "short_title": "Ionizing Radiation (Total Dose) Test Procedure",
     "description": "Total Ionizing Dose test for space / rad-hard qualification.",
     "typical_applicability": ["satcom", "space"],
     "severity": "required"},
]


def main() -> int:
    path = REPO / "domains" / "standards.json"
    db = json.loads(path.read_text())
    existing = {(c["standard"], c["clause"]) for c in db.get("clauses", [])}
    added = 0
    for clause in ADDITIONS:
        key = (clause["standard"], clause["clause"])
        if key in existing:
            continue
        db["clauses"].append(clause)
        existing.add(key)
        added += 1
    db.setdefault("_meta", {})["expanded"] = True
    db["_meta"]["total_clauses"] = len(db["clauses"])
    path.write_text(json.dumps(db, indent=2))
    print(f"Clause DB: +{added} entries, total {len(db['clauses'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
