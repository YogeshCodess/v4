export type PhaseStatusValue = 'pending' | 'in_progress' | 'completed' | 'failed' | 'draft_pending';

export interface PhaseStatusEntry {
  status: PhaseStatusValue;
  updated_at?: string; // ISO string from backend
  /** Real wall-clock seconds the phase took to run. Set by
   *  `pipeline_service._serialised_flip` via the `extra` dict. Used by
   *  DocumentsView's elapsed counter to display the truth for fast
   *  phases (P8c often completes in 5-30 s) where the frontend timer
   *  may not have started before the phase already finished. */
  duration_seconds?: number;
}

export type StatusesRaw = Record<string, PhaseStatusEntry>;

export interface Project {
  id: number;
  name: string;
  description?: string;
  design_type?: string;
  /** Wizard-selected scope — authoritative on the backend (ProjectDB.design_scope). */
  design_scope?: DesignScope;
  /** "receiver" (default), "transmitter", "transceiver", "power_supply"
   *  or "switch_matrix" — picked at project creation. Drives wizard flow
   *  (which architecture catalogue + tier-1 spec questions appear), which
   *  Round-1 questions the P1 agent asks, and which direction branch of
   *  tools/rf_cascade.py computes the cascade. Authoritative on the
   *  backend (ProjectDB.project_type, validated against
   *  services.project_service.VALID_PROJECT_TYPES). */
  project_type?: ProjectType;
  status?: string;
  output_dir?: string;
  created_at?: string;
  conversation_history?: unknown[];
}

export type ProjectType =
  | 'receiver'
  | 'transmitter'
  | 'transceiver'
  | 'power_supply'
  | 'switch_matrix';

export const PROJECT_TYPE_LABELS: Record<ProjectType, string> = {
  'receiver':      'Receiver',
  'transmitter':   'Transmitter',
  'transceiver':   'Transceiver',
  'power_supply':  'Power Supply',
  'switch_matrix': 'Switch Matrix',
};

export type Statuses = Record<string, PhaseStatusValue>;

export interface SubStep {
  label: string;
  time: string;
  detail: string;
}

export interface PhaseMeta {
  id: string;           // "P1", "P2", "P8a" etc
  code: string;         // "P01", "P02" etc
  num: number;
  name: string;
  tagline: string;
  color: string;
  auto: boolean;
  manual: boolean;
  time: string;
  subSteps: SubStep[];
  metrics: { timeSaved: string; errorReduction: string; confidence: string; costImpact: string };
  inputs: string[];
  outputs: string[];
  tools: string[];
  externalTool?: string;
  /**
   * v20 — which Stage-0 design scopes this phase applies to.
   * Omitted or empty means "applies to all scopes" (default).
   * When a scope is active, phases not listed here are shown greyed-out
   * with a "Not applicable for scope X" label.
   */
  applicableScopes?: DesignScope[];
}

export type CenterTab = 'chat' | 'documents';
export type AppMode = 'landing' | 'pipeline';

/**
 * v20 — Stage 0 design scope. Picked once per project, before the P1 chat
 * begins. Drives which clarification questions are asked, which fallback
 * card bank renders on backend failure, and which pipeline phases are
 * applicable (others are greyed out in the sidebar).
 *
 * - 'full'          : whole receiver chain (default if unset)
 * - 'front-end'     : RF front-end only (LNA + preselector + optional mixer)
 * - 'downconversion': downconversion / IF stage only (mixer + LO + IF filter)
 * - 'dsp'           : baseband / digital only (ADC + FPGA / DSP / interface)
 */
export type DesignScope = 'full' | 'front-end' | 'downconversion' | 'dsp';

export const SCOPE_LABELS: Record<DesignScope, string> = {
  'full': 'Full Receiver System',
  'front-end': 'RF Front-End Only',
  'downconversion': 'Downconversion / IF Stage',
  'dsp': 'Baseband / DSP Only',
};
