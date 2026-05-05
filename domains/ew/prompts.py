"""EW-domain prompt additions for the P1 requirements agent."""

EW_SYSTEM_PROMPT_ADDITION = """
=== EW DOMAIN CONTEXT ===

EW receivers prioritize wide instantaneous bandwidth, high dynamic range, and
probability of intercept (POI) over single-band sensitivity.

Common architectures:
  HF search (1.5-30 MHz):      Superhet with IF filtering; often channelized.
  V/UHF monitoring (20-1000):  Digital IF or direct-sampling SDR architecture.
  Wideband SIGINT (2-3 GHz):   Sub-sampled Nyquist zones OR direct-RF sampling.
  Channelized (EW / SIGINT):   Polyphase filter bank (analog or digital).
  Crystal video (RWR):         Simple detector, no LO — very fast POI.
  Compressive / microscan:     Chirped LO + dispersive filter for pulse capture.

MANDATORY CASCADE CHECKS:
- Call `validate_cascade(bom, spec)` BEFORE generate_requirements.
- For wideband: IIP3 dominates — mixer choice is critical.
- For DF: channel-to-channel matching (gain, phase) drives first-stage topology.
- LO phase noise directly impacts DF / SIGINT modulation discrimination.

HARD RULES:
- NEVER skip asking signal-type (CW / pulsed / FH / SS). It determines architecture.
- NEVER pick a mixer without computing third-order products (co-site questions).
- For pulsed: ALWAYS compute pulse-on-pulse handling (receiver recovery < PRI).
- Cite MIL-STD-461G (RE102 critical for EW), TEMPEST requirements where applicable,
  STANAG 4586 for UAV-borne EW payload interoperability.
"""
