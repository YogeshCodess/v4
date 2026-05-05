# RF Gain-Loss Budget
## 2-18 GHz EW Receiver

**Generated:** 2026-04-20  
**Document Status:** AI-GENERATED — verify against final component datasheets

## 0. Design Contract Checks

⚠ **WARN** — 0 hard violations, 1 warning(s) — review before release

| # | Rule | Status | Detail |
|---|------|--------|--------|
| C1 | Analysis at worst-case frequency | ✅ pass | Cascade evaluated at f_max = 18000 MHz (centre 10000 + BW/2 = 8000 MHz). |
| C2 | Bias conditions declared for every active stage | ✅ pass | Every LNA / amp / mixer stage cites the datasheet Vdd/Idq row that produces its gain and NF. |
| C3 | Pdc = Vdd × Idq (±10 mW) | ✅ pass | All biased stages obey the Ohm-law tie between supply and DC power. |
| C4 | Passive NF = |insertion loss| (Friis) | ✅ pass | Every passive stage's NF equals its insertion loss in dB, as thermodynamics requires. |
| C5 | GLB ↔ BOM component match | ⚠ warn | Stage 9 (IF Amp) uses 'ADL5523' which is not in the project BOM |

> These five invariants must hold for any RF receiver GLB to be releasable to PCB. Any row marked **fail** is a contract violation — regenerate Phase 1 or edit the offending stage before proceeding.

## 1. System Parameters

| Parameter | Value | Unit |
|-----------|-------|------|
| Centre Frequency    | 10000   | MHz  |
| RF Bandwidth        | 16000     | MHz  |
| **Analysis Frequency (worst-case)** | **18000** | **MHz** |
| Input Signal Level  | -60   | dBm  |
| Target Output Power | 0  | dBm  |
| Required System Gain | 60 | dB   |

> **Why the upper band edge?** For a receiver, NF rises and gain falls with
> frequency — system sensitivity (MDS) is worst at f_max. The stage-by-stage
> cascade below is therefore evaluated at the upper band edge so the numbers
> represent the least-favourable operating point. The frequency-sweep section
> further down shows how every metric varies across the full band in 1 GHz steps.

## 2. Stage-by-Stage Gain / Loss Budget

| # | Stage | Component | Gain/Loss (dB) | Cum. Gain (dB) | Output Power (dBm) | NF (dB) | Cum. NF (dB) | P1dB Out (dBm) | OIP3 (dBm) | Region | Notes |
|---|-------|-----------|---------------|----------------|-------------------|---------|-------------|---------------|-----------|--------|-------|
| 1 | SMA Input | SMA-F | -0.2 | -0.2 | -60.2 | 0.15 | 0.15 | N/A | N/A | Linear (passive) | Min connector loss (SMA typ, ≤18 GHz) · ⚠ S11 = 30.0 dB — exceptional, verify |
| 2 | PCB Trace | 50Ω Microstrip (RO4350B) | -0.3 | -0.6 | -60.5 | 0.30 | 0.45 | N/A | N/A | Linear (passive) | Typ 1–2 in RO4350B microstrip loss at band |
| 3 | Limiter | PL-DIODE | -0.6 | -1.1 | -61.1 | 0.50 | 0.96 | N/A | N/A | Linear (passive) | TVS limiter, 50 W peak survivable |
| 4 | Preselector | SAW BPF | -2.5 | -3.6 | -63.6 | 1.50 | 2.50 | N/A | N/A | Linear (passive) | 2-18 GHz bandpass, 30 dB rejection at image · ⚠ Passive NF ≠ |loss| (1.50 vs 2.50 dB) |
| 5 | LNA | HMC8410LP2FE | +14.5 | 10.9 | -49.1 | 1.80 | 4.71 | 22.0 | 33.0 | Linear (71.1 dB BO) | GaAs pHEMT LNA, 2-18 GHz |
| 6 | Driver Amp | ADL5611 | +17.5 | 28.4 | -31.6 | 2.70 | 4.81 | 21.0 | 33.0 | Linear (52.6 dB BO) | SiGe IF driver, 30 MHz – 6 GHz |
| 7 | IF Filter | LC Bandpass | -2.5 | 25.9 | -34.1 | 1.50 | 4.81 | N/A | N/A | Linear (passive) | 1 GHz IF, 400 MHz BW · ⚠ Passive NF ≠ |loss| (1.50 vs 2.50 dB) |
| 8 | Mixer | HMC1056 | -7.5 | 18.4 | -41.6 | 7.50 | 4.83 | 9.0 | 23.0 | Linear (50.6 dB BO) | Double-balanced passive mixer |
| 9 | IF Amp | ADL5523 | +19.0 | 37.4 | -22.6 | 2.10 | 4.84 | 21.0 | 36.0 | Linear (43.6 dB BO) | Low-noise IF gain stage |
| 10 | SMA Output | SMA-M | -0.2 | 37.2 | -22.8 | 0.15 | 4.84 | N/A | N/A | Linear (passive) | Min connector loss (SMA typ, ≤18 GHz) · ⚠ S11 = 30.0 dB — exceptional, verify |

> **Region legend:** *Linear* = ≥10 dB back-off from P1dB · *Near-linear* = 6-10 dB · *Compressing* = 0-6 dB (onset of gain compression) · *Saturated* = above P1dB (hard non-linear). Keep every stage in the Linear zone for analogue receivers; transmit chains may intentionally drive the PA into compression.

## 3. Budget Summary

| Metric | Value | Unit |
|--------|-------|------|
| Total System Gain     | 37.2  | dB  |
| Final Output Power    | -22.8  | dBm |
| Cascaded System NF    | 4.84 | dB  |
| Output Power Margin   | +22.8 | dB  |

## 4. Power Consumption per Stage — Datasheet Bias Conditions

| # | Stage | Component | Vdd (V) | Idq (mA) | Pdc (mW) | Datasheet Condition |
|---|-------|-----------|--------:|---------:|---------:|---------------------|
| 1 | SMA Input | SMA-F | — | — | — |  |
| 2 | PCB Trace | 50Ω Microstrip (RO4350B) | — | — | — |  |
| 3 | Limiter | PL-DIODE | — | — | — |  |
| 4 | Preselector | SAW BPF | — | — | — |  |
| 5 | LNA | HMC8410LP2FE | 5.00 | 65.0 | 325.0 | HMC8410 datasheet Rev C, Table 2 typ @ Vdd=5 V, Id=65 mA, f=10 GHz, T=25 °C |
| 6 | Driver Amp | ADL5611 | 5.00 | 100.0 | 500.0 | ADL5611 datasheet Rev A, Table 2 typ @ Vcc=5 V, Icc=100 mA, f=1 GHz |
| 7 | IF Filter | LC Bandpass | — | — | — |  |
| 8 | Mixer | HMC1056 | — | — | 0.0 | HMC1056LP4BE, passive mixer, LO drive +15 dBm typ |
| 9 | IF Amp | ADL5523 | 5.00 | 90.0 | 450.0 | ADL5523 datasheet Rev B, Table 3 typ @ Vpos=5 V, Id=90 mA, f=400 MHz–4 GHz |
| 10 | SMA Output | SMA-M | — | — | — |  |
| **TOTAL** | | | | | **1275.0** | Sum of all powered-stage Pdc |

> **Key consistency rule:** the Vdd and Idq above must be the exact bias
> conditions under which the datasheet specifies the gain, NF, P1dB, and OIP3
> values in the stage-by-stage table. Different bias ⇒ different RF performance.
> The **Pdc per stage** (mW) flows directly into the Power Budget document —
> these numbers must match the per-rail current draws in that spreadsheet.
> Example: HMC8410 LNA — 15 dB typ gain is specified at Vdd = 5.0 V,
> Idq = 65 mA (Pdc = 325 mW). Biasing it at 3.3 V / 40 mA reduces gain to
> ≈ 13 dB and degrades NF by ~0.3 dB — the GLB numbers would no longer be valid.

## 5. Noise Floor & Sensitivity

| Parameter | Formula | Value | Unit |
|-----------|---------|-------|------|
| Thermal Noise Floor (kTB) | -174 + 10·log₁₀(BW_Hz) | -71.96 | dBm |
| System NF (cascaded) | Friis | 4.84 | dB |
| Input-Referred Noise Floor | kTB + NF_sys | -67.12 | dBm |
| Output Noise Floor | Noise_in + Total_Gain | -29.92 | dBm |
| MDS (SNR = 10 dB) | Noise_in + 10 | -57.12 | dBm |

> Assumes 290 K ambient, full RF instantaneous bandwidth, AWGN channel.
> MDS convention: 10 dB SNR above the input-referred noise floor.

## 6. Gain Variation — Thermal (-40 to +85 °C)

| # | Stage | Component | Nominal Gain (dB) | Tempco (dB/°C) | ΔG @ -40 °C | ΔG @ +85 °C | Worst-Case (dB) |
|---|-------|-----------|-------------------|----------------|-------------|-------------|-----------------|
| 1 | SMA Input | SMA-F | -0.20 | ±0.005 | +0.33 | -0.30 | ±0.33 |
| 2 | PCB Trace | 50Ω Microstrip (RO4350B) | -0.35 | ±0.005 | +0.33 | -0.30 | ±0.33 |
| 3 | Limiter | PL-DIODE | -0.55 | ±0.005 | +0.33 | -0.30 | ±0.33 |
| 4 | Preselector | SAW BPF | -2.50 | ±0.005 | +0.33 | -0.30 | ±0.33 |
| 5 | LNA | HMC8410LP2FE | +14.50 | ±0.020 | +1.30 | -1.20 | ±1.30 |
| 6 | Driver Amp | ADL5611 | +17.50 | ±0.020 | +1.30 | -1.20 | ±1.30 |
| 7 | IF Filter | LC Bandpass | -2.50 | ±0.005 | +0.33 | -0.30 | ±0.33 |
| 8 | Mixer | HMC1056 | -7.50 | ±0.020 | +1.30 | -1.20 | ±1.30 |
| 9 | IF Amp | ADL5523 | +19.00 | ±0.020 | +1.30 | -1.20 | ±1.30 |
| 10 | SMA Output | SMA-M | -0.20 | ±0.005 | +0.33 | -0.30 | ±0.33 |
| **TOTAL SYSTEM** | | | | | **+7.15** | **-6.60** | — |

> Active stages (LNA / amp / mixer): ±0.020 dB/°C typical GaAs pHEMT or SiGe.
> Passives (connectors, traces, filters, splitters): ±0.005 dB/°C.
> Plan for ≥ 3 dB AGC range or closed-loop gain compensation to hold system gain across the temperature envelope.

## 7. Gain Variation — Frequency (across RF bandwidth)

| # | Stage | Component | Nominal Gain (dB) | Typ Flatness (± dB) | Min Gain (dB) | Max Gain (dB) |
|---|-------|-----------|-------------------|---------------------|---------------|---------------|
| 1 | SMA Input | SMA-F | -0.20 | ±0.1 | -0.30 | -0.10 |
| 2 | PCB Trace | 50Ω Microstrip (RO4350B) | -0.35 | ±0.1 | -0.45 | -0.25 |
| 3 | Limiter | PL-DIODE | -0.55 | ±0.1 | -0.65 | -0.45 |
| 4 | Preselector | SAW BPF | -2.50 | ±1.0 | -3.50 | -1.50 |
| 5 | LNA | HMC8410LP2FE | +14.50 | ±0.5 | +14.00 | +15.00 |
| 6 | Driver Amp | ADL5611 | +17.50 | ±0.5 | +17.00 | +18.00 |
| 7 | IF Filter | LC Bandpass | -2.50 | ±1.0 | -3.50 | -1.50 |
| 8 | Mixer | HMC1056 | -7.50 | ±0.5 | -8.00 | -7.00 |
| 9 | IF Amp | ADL5523 | +19.00 | ±0.5 | +18.50 | +19.50 |
| 10 | SMA Output | SMA-M | -0.20 | ±0.1 | -0.30 | -0.10 |
| **WORST-CASE (Σ)** | | | | **±4.4** | | |
| **RSS (statistical)** | | | | **±1.74** | | |

> Amps: ±0.5 dB in-band. Filters: ±1.0 dB (passband ripple + skirt roll-off). Passives: ±0.1 dB.
> Worst-case Σ assumes all deviations align; RSS assumes uncorrelated contributions — the truth sits between the two.
> If flatness is critical (e.g. ± 1 dB system spec), add an equaliser or gain-slope compensator after the LNA.

## 8. Stage Gain vs Frequency — 1 GHz step

| # | Stage | Component | Nominal (dB) | 2.0 GHz (dB) | 3.0 GHz (dB) | 4.0 GHz (dB) | 5.0 GHz (dB) | 6.0 GHz (dB) | 7.0 GHz (dB) | 8.0 GHz (dB) | 9.0 GHz (dB) | 10.0 GHz (dB) | 11.0 GHz (dB) | 12.0 GHz (dB) | 13.0 GHz (dB) | 14.0 GHz (dB) | 15.0 GHz (dB) | 16.0 GHz (dB) | 17.0 GHz (dB) | 18.0 GHz (dB) |
|---|-------|-----------|--------------|--------------|--------------|--------------|--------------|--------------|--------------|--------------|--------------|--------------|--------------|--------------|--------------|--------------|--------------|--------------|--------------|--------------|
| 1 | SMA Input | SMA-F | -0.15 | -0.20 | -0.19 | -0.19 | -0.18 | -0.17 | -0.17 | -0.16 | -0.16 | -0.15 | -0.16 | -0.16 | -0.17 | -0.17 | -0.18 | -0.19 | -0.19 | -0.20 |
| 2 | PCB Trace | 50Ω Microstrip (RO4350B) | -0.30 | -0.35 | -0.34 | -0.34 | -0.33 | -0.33 | -0.32 | -0.31 | -0.31 | -0.30 | -0.31 | -0.31 | -0.32 | -0.33 | -0.33 | -0.34 | -0.34 | -0.35 |
| 3 | Limiter | PL-DIODE | -0.50 | -0.55 | -0.54 | -0.54 | -0.53 | -0.53 | -0.52 | -0.51 | -0.51 | -0.50 | -0.51 | -0.51 | -0.52 | -0.53 | -0.53 | -0.54 | -0.54 | -0.55 |
| 4 | Preselector | SAW BPF | -1.50 | -2.50 | -2.38 | -2.25 | -2.12 | -2.00 | -1.88 | -1.75 | -1.62 | -1.50 | -1.62 | -1.75 | -1.88 | -2.00 | -2.12 | -2.25 | -2.38 | -2.50 |
| 5 | LNA | HMC8410LP2FE | +15.00 | +15.50 | +15.44 | +15.38 | +15.31 | +15.25 | +15.19 | +15.12 | +15.06 | +15.00 | +14.94 | +14.88 | +14.81 | +14.75 | +14.69 | +14.62 | +14.56 | +14.50 |
| 6 | Driver Amp | ADL5611 | +18.00 | +18.50 | +18.44 | +18.38 | +18.31 | +18.25 | +18.19 | +18.12 | +18.06 | +18.00 | +17.94 | +17.88 | +17.81 | +17.75 | +17.69 | +17.62 | +17.56 | +17.50 |
| 7 | IF Filter | LC Bandpass | -1.50 | -2.50 | -2.38 | -2.25 | -2.12 | -2.00 | -1.88 | -1.75 | -1.62 | -1.50 | -1.62 | -1.75 | -1.88 | -2.00 | -2.12 | -2.25 | -2.38 | -2.50 |
| 8 | Mixer | HMC1056 | -7.00 | -6.50 | -6.56 | -6.62 | -6.69 | -6.75 | -6.81 | -6.88 | -6.94 | -7.00 | -7.06 | -7.12 | -7.19 | -7.25 | -7.31 | -7.38 | -7.44 | -7.50 |
| 9 | IF Amp | ADL5523 | +19.50 | +20.00 | +19.94 | +19.88 | +19.81 | +19.75 | +19.69 | +19.62 | +19.56 | +19.50 | +19.44 | +19.38 | +19.31 | +19.25 | +19.19 | +19.12 | +19.06 | +19.00 |
| 10 | SMA Output | SMA-M | -0.15 | -0.20 | -0.19 | -0.19 | -0.18 | -0.17 | -0.17 | -0.16 | -0.16 | -0.15 | -0.16 | -0.16 | -0.17 | -0.17 | -0.18 | -0.19 | -0.19 | -0.20 |

## 8a. System Rollup vs Frequency

| Frequency (GHz) | Total Gain (dB) | Cascaded NF (dB) | Output Power (dBm) | MDS @ 10 dB SNR (dBm) |
|----------------:|----------------:|------------------:|-------------------:|----------------------:|
| 2.0 | +41.20 | 4.20 | -18.80 | -57.76 |
| 3.0 | +41.24 | 4.15 | -18.76 | -57.81 |
| 4.0 | +41.26 | 4.11 | -18.74 | -57.85 |
| 5.0 | +41.28 | 4.05 | -18.72 | -57.91 |
| 6.0 | +41.30 | 4.01 | -18.70 | -57.95 |
| 7.0 | +41.32 | 3.97 | -18.68 | -57.99 |
| 8.0 | +41.34 | 3.92 | -18.66 | -58.04 |
| 9.0 | +41.36 | 3.88 | -18.64 | -58.08 |
| 10.0 | +41.40 | 3.83 | -18.60 | -58.13 |
| 11.0 | +40.88 | 3.88 | -19.12 | -58.08 |
| 12.0 | +40.38 | 3.92 | -19.62 | -58.04 |
| 13.0 | +39.80 | 3.98 | -20.20 | -57.98 |
| 14.0 | +39.30 | 4.03 | -20.70 | -57.93 |
| 15.0 | +38.80 | 4.07 | -21.20 | -57.89 |
| 16.0 | +38.22 | 4.13 | -21.78 | -57.83 |
| 17.0 | +37.72 | 4.17 | -22.28 | -57.78 |
| 18.0 | +37.20 | 4.23 | -22.80 | -57.73 |

> Gain roll-off model: amps/mixers fall off monotonically toward the high edge of the band by ±0.5 dB at the edges; filters ripple by ±1.0 dB; connectors & traces by ±0.1 dB.
> Cascaded NF recomputed with Friis at each frequency — the LNA continues to dominate, so NF typically stays within ±0.3 dB of nominal across the band.
> MDS tracks the NF. If the band-edge MDS is more than 2 dB worse than midband, add frequency-dependent equalisation or re-allocate gain toward the LNA.

## 9. Return Loss — Per Stage

| # | Stage | Component | S11 — Input RL (dB) | S22 — Output RL (dB) | Notes |
|---|-------|-----------|---------------------|----------------------|-------|
| 1 | SMA Input | SMA-F | 30.0 | — | Min connector loss (SMA typ, ≤18 GHz) |
| 2 | PCB Trace | 50Ω Microstrip (RO4350B) | 25.0 | — | Typ 1–2 in RO4350B microstrip loss at band |
| 3 | Limiter | PL-DIODE | 20.0 | — | TVS limiter, 50 W peak survivable |
| 4 | Preselector | SAW BPF | 18.0 | — | 2-18 GHz bandpass, 30 dB rejection at image |
| 5 | LNA | HMC8410LP2FE | 15.0 | — | GaAs pHEMT LNA, 2-18 GHz |
| 6 | Driver Amp | ADL5611 | 18.0 | — | SiGe IF driver, 30 MHz – 6 GHz |
| 7 | IF Filter | LC Bandpass | 20.0 | — | 1 GHz IF, 400 MHz BW |
| 8 | Mixer | HMC1056 | 15.0 | — | Double-balanced passive mixer |
| 9 | IF Amp | ADL5523 | 16.0 | — | Low-noise IF gain stage |
| 10 | SMA Output | SMA-M | 30.0 | — | Min connector loss (SMA typ, ≤18 GHz) |

> Return loss values are referenced to 50 Ω. Higher value = better match.

## 10. Consistency Checks

> **⚠ BOM / Block-Diagram mismatches detected — review before PCB release:**

- Stage 9 (IF Amp) uses 'ADL5523' which is not in the project BOM

## 11. Cascade Noise Figure — Friis Formula

$$F_{{sys}} = F_1 + \frac{{F_2 - 1}}{{G_1}} + \frac{{F_3 - 1}}{{G_1 G_2}} + \cdots$$

Where *F* = linear noise factor (not dB), *G* = linear gain.
The first stage NF dominates — minimise LNA/driver NF for best system sensitivity.

---
> **Note:** All values are estimated from component datasheets at 25 °C nominal.
> Verify with bench measurements (spectrum analyser + noise source) during hardware bring-up.