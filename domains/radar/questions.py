"""
Radar-domain Round-1 elicitation questions.

Tier 1: mandatory (asked for all radar projects)
Tier 2: application-adaptive (asked based on radar type)
Tier 3: architecture-specific follow-ups
"""

from domains._schema import Question

RADAR_QUESTIONS: list[Question] = [
    # ========== Tier 1: Mandatory ==========
    Question(
        id="radar.t1.freq_band",
        domain="radar",
        tier=1,
        category="RF Performance",
        text="Which radar frequency band? (L, S, C, X, Ku, K, Ka)",
        expected_format="enum",
    ),
    Question(
        id="radar.t1.radar_type",
        domain="radar",
        tier=1,
        category="Application",
        text="Radar type? (fire-control / surveillance / tracking / maritime-patrol / RWR / monopulse)",
        expected_format="enum",
    ),
    Question(
        id="radar.t1.platform",
        domain="radar",
        tier=1,
        category="Application",
        text="Platform? (airborne / naval / ground-mobile / ground-fixed / missile)",
        expected_format="enum",
    ),
    Question(
        id="radar.t1.instantaneous_bw",
        domain="radar",
        tier=1,
        category="RF Performance",
        text="Instantaneous bandwidth (MHz)?",
        expected_format="MHz",
    ),
    Question(
        id="radar.t1.noise_figure",
        domain="radar",
        tier=1,
        category="RF Performance",
        text="System noise figure target (dB)?",
        expected_format="dB",
    ),
    Question(
        id="radar.t1.sensitivity",
        domain="radar",
        tier=1,
        category="RF Performance",
        text="Minimum detectable signal / MDS (dBm)?",
        expected_format="dBm",
    ),
    Question(
        id="radar.t1.dynamic_range",
        domain="radar",
        tier=1,
        category="RF Performance",
        text="Spurious-free dynamic range SFDR (dB)?",
        expected_format="dB",
    ),
    Question(
        id="radar.t1.iip3",
        domain="radar",
        tier=1,
        category="Linearity",
        text="IIP3 at receiver input (dBm)?",
        expected_format="dBm",
    ),
    # ========== Tier 2: Pulse / Coherent ==========
    Question(
        id="radar.t2.pulse_width",
        domain="radar",
        tier=2,
        category="Pulse Handling",
        text="Pulse width range: minimum PW (ns) and maximum PW (us)?",
        expected_format="ns/us",
        triggers=["pulsed"],
    ),
    Question(
        id="radar.t2.pri",
        domain="radar",
        tier=2,
        category="Pulse Handling",
        text="PRI / PRF range? (us or Hz)",
        expected_format="us/Hz",
        triggers=["pulsed"],
    ),
    Question(
        id="radar.t2.coherent",
        domain="radar",
        tier=2,
        category="Processing",
        text="Coherent processing required? (yes / no / optional)",
        expected_format="enum",
    ),
    Question(
        id="radar.t2.range_resolution",
        domain="radar",
        tier=2,
        category="Performance",
        text="Range resolution requirement (meters)?",
        expected_format="meters",
    ),
    Question(
        id="radar.t2.mti",
        domain="radar",
        tier=2,
        category="Processing",
        text="MTI / Doppler processing required? (MTI / MTD / pulse-Doppler / none)",
        expected_format="enum",
    ),
    # ========== Environment / Compliance ==========
    Question(
        id="radar.t1.temp_range",
        domain="radar",
        tier=1,
        category="Environmental",
        text="Operating temperature range? (commercial / industrial / military -55 to +125 / space)",
        expected_format="enum",
    ),
    Question(
        id="radar.t1.standards",
        domain="radar",
        tier=1,
        category="Compliance",
        text="Required compliance standards? (MIL-STD-461 / 810 / 704, DO-254 / 160, ITAR)",
        expected_format="list",
    ),
]
