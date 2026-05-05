"""
Calculator Tool - Engineering calculations for hardware design.

Provides power budget, thermal analysis, RF link budget, and other calculations.
"""

import logging
import math
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PowerBudgetEntry:
    """Single entry in power budget."""
    name: str
    current_ma: float
    voltage_v: float
    power_mw: float
    duty_cycle: float = 1.0  # 0-1, for intermittent loads


@dataclass
class ThermalResult:
    """Thermal analysis result."""
    junction_temperature_c: float
    case_temperature_c: float
    heatsink_required: bool
    thermal_resistance_req: Optional[float]  # C/W


@dataclass
class RFLinkResult:
    """RF link budget analysis result."""
    path_loss_db: float
    received_power_dbm: float
    link_margin_db: float
    is_viable: bool


class CalculatorTool:
    """
    Engineering calculator for hardware design.

    Usage:
        calc = CalculatorTool()

        # Power budget
        total_power = calc.power_budget([...])

        # Thermal analysis
        thermal = calc.thermal_analysis(power=5.0, rja=50, ambient=25)

        # RF link budget
        link = calc.rf_link_budget(freq_mhz=2400, distance_km=1, tx_power=20, tx_gain=0, rx_gain=0)
    """

    # ==================== POWER BUDGET ====================

    def power_budget(
        self,
        entries: List[PowerBudgetEntry],
        efficiency: float = 0.85,
        margin: float = 1.2,
    ) -> Dict:
        """
        Calculate total power budget with efficiency and margin.

        Args:
            entries: List of power-consuming components
            efficiency: Power supply efficiency (0-1)
            margin: Design margin (typically 1.2 for 20% headroom)

        Returns:
            Dict with total power, current, battery life estimates
        """
        total_power_mw = 0
        total_current_ma = 0

        for entry in entries:
            # Calculate average power considering duty cycle
            avg_power = entry.power_mw * entry.duty_cycle
            total_power_mw += avg_power
            total_current_ma += entry.current_ma * entry.duty_cycle

        # Apply supply efficiency and margin
        input_power_mw = (total_power_mw / efficiency) * margin
        input_current_ma = (total_current_ma / efficiency) * margin

        return {
            "total_power_mw": round(total_power_mw, 2),
            "total_power_w": round(total_power_mw / 1000, 3),
            "input_power_mw": round(input_power_mw, 2),
            "input_power_w": round(input_power_mw / 1000, 3),
            "total_current_ma": round(total_current_ma, 2),
            "input_current_ma": round(input_current_ma, 2),
            "entries": [e.__dict__ for e in entries],
        }

    def battery_life(
        self,
        power_mw: float,
        capacity_mah: float,
        voltage: float = 3.7,
    ) -> Dict:
        """
        Estimate battery life.

        Args:
            power_mw: Average power consumption
            capacity_mah: Battery capacity in mAh
            voltage: Battery voltage

        Returns:
            Dict with life in hours, days
        """
        current_ma = power_mw / voltage
        life_hours = capacity_mah / current_ma if current_ma > 0 else 0

        return {
            "life_hours": round(life_hours, 1),
            "life_days": round(life_hours / 24, 1),
            "current_ma": round(current_ma, 2),
        }

    # ==================== THERMAL ANALYSIS ====================

    def thermal_analysis(
        self,
        power_w: float,
        rja: float,  # Junction-to-ambient thermal resistance (C/W)
        ambient_c: float = 25.0,
        max_junction_c: float = 150.0,
        rjc: Optional[float] = None,  # Junction-to-case
        rcs: Optional[float] = None,  # Case-to-sink
        rsa: Optional[float] = None,  # Sink-to-ambient
    ) -> ThermalResult:
        """
        Calculate junction temperature and determine if heatsink is needed.

        Args:
            power_w: Power dissipation in watts
            rja: Total thermal resistance junction-to-ambient (C/W)
            ambient_c: Ambient temperature (C)
            max_junction_c: Maximum allowed junction temperature (C)
            rjc: Junction-to-case thermal resistance (for heatsink calc)
            rcs: Case-to-sink thermal resistance
            rsa: Sink-to-ambient thermal resistance

        Returns:
            ThermalResult with temperatures and heatsink recommendation
        """
        # Simple calculation: Tj = Ta + (P * Rja)
        junction_temp = ambient_c + (power_w * rja)

        heatsink_required = junction_temp > max_junction_c
        thermal_resistance_req = None

        # If we have detailed thermal resistance, calculate required heatsink
        if heatsink_required and all([rjc, rcs]):
            # Required: Rsa_max = (Tj_max - Ta) / P - Rjc - Rcs
            max_total_r = (max_junction_c - ambient_c) / power_w
            thermal_resistance_req = max_total_r - rjc - rcs
            if thermal_resistance_req < 0:
                thermal_resistance_req = 0

        return ThermalResult(
            junction_temperature_c=round(junction_temp, 1),
            case_temperature_c=round(junction_temp - (power_w * rjc if rjc else 0), 1),
            heatsink_required=heatsink_required,
            thermal_resistance_req=round(thermal_resistance_req, 2) if thermal_resistance_req else None,
        )

    # ==================== RF LINK BUDGET ====================

    def rf_link_budget(
        self,
        freq_mhz: float,
        distance_km: float,
        tx_power_dbm: float,
        tx_gain_dbi: float = 0.0,
        rx_gain_dbi: float = 0.0,
        cable_loss_db: float = 0.0,
        margin_db: float = 10.0,
    ) -> RFLinkResult:
        """
        Calculate RF link budget using Friis transmission equation.

        Args:
            freq_mhz: Carrier frequency in MHz
            distance_km: Distance in kilometers
            tx_power_dbm: Transmit power in dBm
            tx_gain_dbi: Transmit antenna gain in dBi
            rx_gain_dbi: Receive antenna gain in dBi
            cable_loss_db: Combined cable losses in dB
            margin_db: Required link margin (typically 10-20 dB)

        Returns:
            RFLinkResult with path loss, received power, margin
        """
        # Free space path loss: FSPL(dB) = 20log10(d) + 20log10(f) + 32.44
        # where d is distance in km, f is frequency in MHz
        path_loss_db = (
            20 * math.log10(distance_km) +
            20 * math.log10(freq_mhz) +
            32.44
        )

        # Received power: Pr = Pt + Gt + Gr - Lc - FSPL
        received_power_dbm = (
            tx_power_dbm +
            tx_gain_dbi +
            rx_gain_dbi -
            cable_loss_db -
            path_loss_db
        )

        # Link margin: Received - Required (assume -100 dBm sensitivity)
        sensitivity_dbm = -100.0
        link_margin_db = received_power_dbm - sensitivity_dbm

        return RFLinkResult(
            path_loss_db=round(path_loss_db, 1),
            received_power_dbm=round(received_power_dbm, 1),
            link_margin_db=round(link_margin_db, 1),
            is_viable=link_margin_db >= margin_db,
        )

    # ==================== VOLTAGE REGULATOR ====================

    def voltage_regulator(
        self,
        input_v: float,
        output_v: float,
        current_a: float,
        efficiency: float = 0.9,
    ) -> Dict:
        """
        Calculate voltage regulator parameters.

        Args:
            input_v: Input voltage
            output_v: Output voltage
            current_a: Output current
            efficiency: Converter efficiency

        Returns:
            Dict with power dissipation, input current, etc.
        """
        # Output power
        output_power = output_v * current_a

        # Input power
        input_power = output_power / efficiency if efficiency > 0 else output_power

        # Input current
        input_current = input_power / input_v if input_v > 0 else 0

        # Power dissipation
        power_dissipation = input_power - output_power

        # Dropout voltage for LDO
        dropout_v = input_v - output_v

        return {
            "output_power_w": round(output_power, 3),
            "input_power_w": round(input_power, 3),
            "input_current_a": round(input_current, 3),
            "power_dissipation_w": round(power_dissipation, 3),
            "dropout_v": round(dropout_v, 2),
            "is_ldo_suitable": dropout_v >= 0.5,  # Typical LDO dropout
        }

    # ==================== ADC RESOLUTION ====================

    def adc_resolution(
        self,
        bits: int,
        vref: float = 3.3,
        signal_range_v: float = 0.0,
    ) -> Dict:
        """
        Calculate ADC resolution parameters.

        Args:
            bits: ADC bit resolution (e.g., 12, 16)
            vref: Reference voltage
            signal_range_v: Expected signal range (0 = full scale)

        Returns:
            Dict with LSB size, resolution counts, etc.
        """
        steps = 2 ** bits
        lsb = vref / steps

        signal_range = signal_range_v if signal_range_v > 0 else vref
        effective_resolution = signal_range / lsb

        return {
            "steps": steps,
            "lsb_mv": round(lsb * 1000, 3),
            "vref_v": vref,
            "effective_bits": round(math.log2(effective_resolution), 1) if effective_resolution > 0 else bits,
        }
