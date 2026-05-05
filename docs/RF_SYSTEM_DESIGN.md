# RF System Design: 5-18 GHz Wideband Signal Generator with Artix-7 FPGA

## Executive Summary

This document describes the design of a high-performance RF system with the following specifications:

| Parameter | Specification |
|-----------|---------------|
| **Frequency Range** | 5-18 GHz (Ultra-Wideband) |
| **Output Power** | 40 dBm (10 W) continuous wave |
| **FPGA Platform** | Xilinx Artix-7 FPGA |
| **Power Management** | Multi-stage buck converters |
| **Architecture** | Direct Digital Synthesis (DDS) + Multi-stage Amplification |

---

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          RF SYSTEM BLOCK DIAGRAM                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐              │
│  │  Artix-7 │    │   DAC    │    │   Up     │    │   PA     │              │
│  │   FPGA   │───▶|(12-bit) │───▶|  Mixer   │───▶|  Stages  │───▶ OUTPUT   │
│  │          │    │  (2.5)   │    │  (5-18)  │    │  (40dBm) │   (5-18GHz)  │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘              │
│       │                                   │                                  │
│       │ DDS/PLL Control                   │ LO Distribution                 │
│       ↓                                   ↓                                  │
│  ┌──────────┐                        ┌──────────┐                           │
│  │ PLL/VCO  │                        │   LO     │                           │
│  │ Synth    │                        │  Chain   │                           │
│  └──────────┘                        └──────────┘                           │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    POWER MANAGEMENT SYSTEM                          │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐   │   │
│  │  │ 48V IN  │─▶│ Buck #1 │─▶│ Buck #2 │─▶│ Buck #3 │─▶│ LDOs    │   │   │
│  │  │ (Main)  │  │ (12V)   │  │ (5V)    │  │ (3.3V)  │  │ (1.8V)  │   │   │
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘  └─────────┘   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Artix-7 FPGA Selection and Configuration

### Recommended FPGA: XC7A35T-FTG256I

| Specification | Value |
|---------------|-------|
| **Logic Cells** | 33,280 |
| **DSP Slices** | 90 (DSP48E1) |
| **Block RAM** | 1,800 Kb |
| **PLL/MMCM** | 2 PLLs, 1 MMCM |
| **Transceivers** | None (use external high-speed DAC) |
| **Package** | FTG256 (Industrial, -40°C to +100°C) |
| **I/O Voltage** | 1.2V, 1.8V, 2.5V, 3.3V |

### FPGA Responsibilities:

1. **DDS/Direct Digital Synthesis Control**
   - Generate frequency control words for PLL
   - Implement modulation waveforms (AM, FM, PM, QAM)
   - Store waveform data in Block RAM

2. **System Control Interface**
   - SPI/I2C control for all RF components
   - VCO tuning voltage generation via DAC
   - Gain control for amplifier stages

3. **User Interface**
   - Ethernet (10/100/1000) for remote control
   - USB 2.0 for configuration
   - LCD/status indicators

---

## 2. RF Signal Chain Design

### 2.1 Baseband Generation (DC - 2.5 GHz)

**Component**: Analog Devices AD9164 or equivalent

| Parameter | Specification |
|-----------|---------------|
| Resolution | 12-bit |
| Sample Rate | 2.5 GSPS |
| Output | Differential, DC coupled |
| SFDR | 65 dBc |
| Power | 1.8V analog, 1.0V digital |

### 2.2 Frequency Upconversion Stage (5-18 GHz)

#### Option A: Single Wideband Mixer Approach
```
Baseband (0-2.5 GHz) → Image Reject Filter → Wideband Mixer (5-18 GHz) → PA
```

**Mixer Selection**: HMC1099LP4E (Analog Devices)

| Parameter | Value |
|-----------|-------|
| RF Frequency | 7.5 - 18 GHz |
| LO Frequency | 5 - 15 GHz |
| IF Frequency | DC - 6 GHz |
| Conversion Loss | 8 dB |
| P1dB | +18 dBm |
| IP3 | +28 dBm |

#### Option B: Multi-Band Approach (Recommended)
```
                ┌─────────────┐
                │  Band 1     │ 5-8 GHz
                │  (Low)      │
Baseband ──────▶├─────────────┤
                │  Band 2     │ 8-13 GHz
                │  (Mid)      │
                ├─────────────┤
                │  Band 3     │ 13-18 GHz
                │  (High)     │
                └─────────────┘
```

### 2.3 Local Oscillator (LO) Distribution

**Primary LO Source**: ADF5355 (Microwave PLL)

| Parameter | Value |
|-----------|-------|
| Frequency Range | 53.125 MHz to 13.6 GHz |
| Output Power | -5 dBm typical |
| Phase Noise | -125 dBc/Hz @ 1 MHz offset @ 6 GHz |
| VCO Frequency | 4.6 - 8.0 GHz integrated |

**LO Distribution Chain**:
```
ADF5355 → HMC361 (Divide by 2) → HMC985 (RF Amplifier) → Power Splitter → Mixers
```

### 2.4 Power Amplifier Stages

**Output Power Target**: 40 dBm (10 W) continuous wave

#### Stage 1: Driver Amplifier
**Component**: HMC1131 or Qorvo TGA2705

| Parameter | Value |
|-----------|-------|
| Frequency | 5 - 18 GHz |
| Gain | 20 dB |
| P1dB | +23 dBm |
| Psat | +26 dBm |

#### Stage 2: Intermediate Power Amplifier
**Component**: CMD275 or custom GaAs MMIC

| Parameter | Value |
|-----------|-------|
| Frequency | 5 - 18 GHz |
| Gain | 18 dB |
| P1dB | +33 dBm |
| Psat | +36 dBm |

#### Stage 3: Final Power Amplifier (GaN)
**Component**: Qorvo TGA2590 or QPA1006

| Parameter | Value |
|-----------|-------|
| Frequency | 5 - 18 GHz (dual band optimized) |
| Gain | 25 dB |
| Psat | +42 dBm |
| PAE | 25% typical |
| Supply | 28V |

**Total Chain Gain**:
```
Mixer Loss: -8 dB
Driver Amp: +20 dB
Inter Amp: +18 dB
Final PA: +25 dB
Net Gain: +55 dB
Input 0 dBm → Output 55 dBm (attenuate to 40 dBm target)
```

---

## 3. Power Management Design

### 3.1 Power Budget Analysis

| Block | Voltage | Current | Power |
|-------|---------|---------|-------|
| Artix-7 FPGA | 1.0V (core) | 2A | 2W |
| Artix-7 FPGA | 1.8V (aux) | 0.5A | 0.9W |
| Artix-7 FPGA | 3.3V (IO) | 0.3A | 1W |
| DAC (AD9164) | 1.8V | 0.8A | 1.44W |
| DAC (AD9164) | 1.0V | 0.5A | 0.5W |
| PLL/Synths | 3.3V | 0.2A | 0.66W |
| Driver Amp | 5V | 0.5A | 2.5W |
| Inter Amp | 12V | 2A | 24W |
| Final PA | 28V | 4A | 112W |
| **Total** | - | - | **~145W** |

### 3.2 Buck Converter Design

#### Main Input: 48V DC ( Telecom/Industrial Standard)

**Buck Converter #1: 48V → 12V @ 5A (60W)**
**Component**: TI LM5118 or ADI LTC3895

| Parameter | Value |
|-----------|-------|
| Input Voltage | 36-72V |
| Output Voltage | 12V |
| Output Current | 5A continuous |
| Switching Freq | 200 kHz |
| Efficiency | >92% |

**Buck Converter #2: 12V → 5V @ 2A (10W)**
**Component**: TI TPS54160 or ADI LTC3638

| Parameter | Value |
|-----------|-------|
| Input Voltage | 8-16V |
| Output Voltage | 5V |
| Output Current | 2A |
| Switching Freq | 500 kHz |
| Efficiency | >95% |

**Buck Converter #3: 12V → 3.3V @ 1A (3.3W)**
**Component**: TI TPS54240 or ADI LT8610

| Parameter | Value |
|-----------|-------|
| Input Voltage | 8-16V |
| Output Voltage | 3.3V |
| Output Current | 1A |
| Switching Freq | 700 kHz |
| Efficiency | >94% |

**Power Module for FPGA: 12V → 1.0V/1.8V**
**Component**: Enpirion EN5339 or TI TPS62913

| Parameter | Value |
|-----------|-------|
| Input Voltage | 4.5-14V |
| Output 1 | 1.0V @ 3A |
| Output 2 | 1.8V @ 1A |
| Switching Freq | 1 MHz |
| Efficiency | >90% |

**High Current 28V for PA: 48V → 28V @ 5A (140W)**
**Component**: Vicor PI3740 or TI LMR33630

| Parameter | Value |
|-----------|-------|
| Input Voltage | 36-72V |
| Output Voltage | 28V |
| Output Current | 5A continuous |
| Switching Freq | 100 kHz |
| Efficiency | >96% |

### 3.3 Power Sequencing

FPGA requires proper power sequencing:

```
1. VCCAUX (1.8V) → RAMP UP
2. VCCINT (1.0V) → RAMP UP (within 50ms of VCCAUX)
3. VCCO (3.3V, 2.5V, etc.) → RAMP UP
4. Configuration Load → BEGIN
```

**Sequencer**: TI TPS386000 or ADI ADM1266

### 3.4 EMI/EMC Considerations

1. **Switching Frequency Selection**: 200-500 kHz for 48V bus (reduce EMI)
2. **Filtering**:
   - Pi filters on all buck outputs
   - Common mode chokes on input
   - Ceramic bulk capacitors (10-100 uF)
3. **Layout Guidelines**:
   - Minimize high-current loop areas
   - Use ground planes extensively
   - Keep switching nodes away from RF circuits

---

## 4. Interface and Control

### 4.1 FPGA Interface Summary

| Interface | Purpose | Pins |
|-----------|---------|------|
| LVDS | DAC data clock (2.5 Gbps) | 2 |
| SPI | PLL, mixer, attenuator control | 4 |
| I2C | Temperature sensors, EEPROM | 2 |
| GPIO | PA enable, band switching | 8 |
| Ethernet | 10/100/1000 control | 4 (RGMII) |
| USB | Configuration/Debug | 2 |
| UART | Console debug | 2 |

### 4.2 Control Software Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                    HOST PC / CONTROLLER                        │
├────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │   GUI /      │  │   Python /   │  │   SCPI       │         │
│  │   Web UI     │  │   API        │  │   Commands   │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
└────────────────────────────────────────────────────────────────┘
                            │
                       Ethernet
                            │
┌────────────────────────────────────────────────────────────────┐
│                      ARTIX-7 FPGA                               │
├────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │  Ethernet    │  │  Control     │  │  Waveform    │         │
│  │  Stack       │  │  Logic       │  │  Generator   │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
│         │                │                 │                   │
│         └────────────────┴─────────────────┘                   │
│                      Device Interface                           │
│              (SPI / I2C / GPIO / LVDS)                         │
└────────────────────────────────────────────────────────────────┘
```

---

## 5. Mechanical and Thermal Design

### 5.1 Thermal Analysis

**Power Dissipation by Module**:
- Final PA: ~85W (112W - 27W RF output)
- Intermediate PA: ~20W
- Driver Amp: ~2W
- FPGA + Digital: ~5W
- Power Converters: ~10W
- **Total Dissipation**: ~122W

**Cooling Requirements**:
- Forced air cooling: Minimum 40 CFM @ 0.5" H2O
- Heat sink for PA: Thermal resistance < 0.5°C/W
- Recommended: 2x 40mm fans @ 10,000 RPM each

### 5.2 Mechanical Enclosure

**Recommended**: 19" Rack Mount, 2U Height

| Dimension | Value |
|-----------|-------|
| Width | 482 mm (19") |
| Height | 88 mm (2U) |
| Depth | 300 mm |
| Weight | ~8 kg |
| Front Panel | SMA RF output, N-Type (High Power) |
| Rear Panel | AC input, Ethernet, USB, Fan exhaust |

### 5.3 Shielding Considerations

1. **RF Shielding**:
   - Aluminum enclosure with EMI gaskets
   - RF cage sections for PA module
   - Absorber material on interior surfaces

2. **Ventilation**:
   - Honeycomb air vents (EMI type)
   - Filtered intake, exhaust on rear

---

## 6. Key Component Selection Summary

| Ref | Description | Manufacturer | Part Number | Qty |
|-----|-------------|--------------|-------------|-----|
| U1 | Artix-7 FPGA | Xilinx | XC7A35T-FTG256I | 1 |
| U2 | 12-bit DAC, 2.5 GSPS | Analog Devices | AD9164BCPZ | 1 |
| U3 | Microwave PLL | Analog Devices | ADF5355CCPZ | 1 |
| U4 | Wideband Mixer | Analog Devices | HMC1099LP4E | 1-3 |
| U5 | Driver Amplifier | Analog Devices | HMC1131LP5E | 1 |
| U6 | Inter Amplifier | Custom MMIC | CMD275 | 1 |
| U7 | Final PA (GaN) | Qorvo | TGA2590-SM | 1 |
| U8 | Buck 48→12V | TI | LM5118MH | 1 |
| U9 | Buck 12→5V | TI | TPS54160 | 1 |
| U10 | Buck 12→3.3V | TI | TPS54240 | 1 |
| U11 | FPGA Power Module | Enpirion | EN5339 | 1 |
| U12 | Buck 48→28V | Vicor | PI3740-00-HV | 1 |
| U13 | Power Sequencer | TI | TPS386000 | 1 |

---

## 7. Compliance and Safety

### 7.1 Regulatory Compliance

| Standard | Description | Status |
|----------|-------------|--------|
| FCC Part 15 | RF Device Emissions | Design for compliance |
| CE | European EMC Directive | Filtered I/O, shielded enclosure |
| RoHS | Lead-free | All components RoHS compliant |
| REACH | Substances of concern | Document all materials |

### 7.2 Safety Features

1. **Interlock**: RF output disable when cover removed
2. **Over-Temperature**: PA shutdown at 85°C case temperature
3. **Over-Current**: Current foldback on PA supply
4. **Reverse Polarity**: Input protection diode
5. **ESD Protection**: TVS diodes on all external connectors

---

## 8. Estimated Cost Breakdown

| Category | Cost (USD) |
|----------|------------|
| Artix-7 FPGA + Config Flash | $75 |
| High-Speed DAC | $150 |
| PLL/Synth Components | $80 |
| RF Mixers | $120 ($40 x 3) |
| Amplifier Chain | $250 |
| Power Conversion | $150 |
| PCB + Assembly | $200 |
| Mechanical + Connectors | $100 |
| **Total (Volume 100)** | **~$1,125** |

---

## 9. Development Roadmap

### Phase 1: Proof of Concept (4 weeks)
- [ ] FPGA development board setup
- [ ] SPI control for PLL
- [ ] Basic DDS generation
- [ ] Single frequency output verification

### Phase 2: RF Chain Integration (6 weeks)
- [ ] DAC integration and characterization
- [ ] Mixer LO chain build
- [ ] Driver amplifier integration
- [ ] Band switching validation

### Phase 3: Power Amplifier (4 weeks)
- [ ] PA module design and fab
- [ ] Thermal management testing
- [ ] Power optimization
- [ ] Safety interlock integration

### Phase 4: Power System (3 weeks)
- [ ] Buck converter board design
- [ ] Power sequencing implementation
- [ ] EMI testing and mitigation
- [ ] Efficiency validation

### Phase 5: System Integration (4 weeks)
- [ ] Complete assembly
- [ ] Firmware development
- [ ] PC GUI development
- [ ] Calibration procedures

### Phase 6: Testing & Qualification (4 weeks)
- [ ] Full frequency sweep validation
- [ ] Power output verification
- [ ] Environmental testing
- [ ] Compliance testing (FCC/CE)

---

## 10. References and Datasheets

1. **Xilinx Artix-7 FPGA datasheet**
2. **Analog Devices AD9164 DAC datasheet**
3. **Qorvo TGA2590 PA datasheet**
4. **Microwave RF Design** - Pozar
5. **GaN RF Power Devices** - Application Notes
6. **Switching Power Supply Design** - Pressman

---

*Document Revision: 1.0*
*Date: February 2026*
*Author: Silicon to Software (S2S) AI System*
