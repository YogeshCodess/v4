import type { PhaseMeta, DesignScope } from '../types';

export const PHASES: PhaseMeta[] = [
  {
    id: 'P1', code: 'P01', num: 1,
    name: 'Requirements & Component Selection',
    tagline: 'Natural language → verified BOM',
    color: '#00c6a7', auto: true, manual: false, time: '~4 min',
    subSteps: [
      { label: 'Decoding your design intent', time: '12s', detail: 'Extracting domain, voltage, current, form-factor from natural language' },
      { label: 'Classifying hardware domain', time: '5s', detail: 'RF / Motor / Power / Digital / Mixed-signal domain identified' },
      { label: 'Scouting 500K+ components', time: '48s', detail: 'Searching DigiKey, Mouser, Arrow for best-match parts' },
      { label: 'Scoring & ranking candidates', time: '20s', detail: 'Evaluating availability, lifecycle, cost, specs match, RoHS compliance' },
      { label: 'Assembling BOM with alternates', time: '15s', detail: '2-3 alternatives per critical component, footprint verified' },
      { label: 'Drafting block diagram', time: '30s', detail: 'Connectivity map generated from selected components' },
      { label: 'Finalizing requirements', time: '50s', detail: 'Cross-checking all specs before locking down the design' },
    ],
    metrics: { timeSaved: '2 weeks → 4 min', errorReduction: '72%', confidence: '99%', costImpact: 'Rs 8.2L/yr' },
    inputs: ['Engineer natural language description', 'Design type (RF / Digital)', 'Voltage / current requirements'],
    outputs: ['Requirements document (.md)', 'Block diagram (.md)', 'Architecture overview (.md)', 'Component recommendations BOM (.md)', 'Power calculation budget (.md)'],
    tools: ['Claude AI', 'Component search (ChromaDB, optional)'],
    applicableScopes: ['full', 'front-end', 'downconversion', 'dsp'],
  },
  {
    id: 'P2', code: 'P02', num: 2,
    name: 'HRS Document Generation',
    tagline: '50-100 page specification in minutes',
    color: '#3b82f6', auto: true, manual: false, time: '~4 min',
    subSteps: [
      { label: 'Ingesting P1 requirements', time: '3s', detail: 'Pulling in structured requirements, BOM, and block diagram' },
      { label: 'Loading IEEE 29148 template', time: '5s', detail: 'Domain-specific template schema selected for your design type' },
      { label: 'Computing power budget', time: '18s', detail: 'Efficiency curves, thermal derating, rail margins calculated' },
      { label: 'Building interface tables', time: '22s', detail: 'Connectors, signals, pin assignments auto-populated' },
      { label: 'Writing 8 spec sections', time: '120s', detail: 'Overview, electrical, mechanical, thermal, test — each section in parallel' },
      { label: 'Rendering Mermaid diagrams', time: '30s', detail: 'Block diagrams, power tree, interface topology rendered inline' },
      { label: 'Compiling final document', time: '12s', detail: 'Markdown assembled with cross-references and section numbering' },
    ],
    metrics: { timeSaved: '3 weeks → 4 min', errorReduction: '68%', confidence: '96%', costImpact: 'Rs 11.4L/yr' },
    inputs: ['Requirements from P1', 'Block diagram from P1', 'Architecture from P1', 'BOM from P1'],
    outputs: ['HRS document (.md)', 'HRS document (.docx)'],
    tools: ['Claude AI', 'python-docx'],
    applicableScopes: ['full', 'front-end', 'downconversion', 'dsp'],
  },
  {
    id: 'P3', code: 'P03', num: 3,
    name: 'Compliance Validation',
    tagline: 'Multi-standard real-time checking',
    color: '#f59e0b', auto: true, manual: false, time: '~4 min',
    subSteps: [
      { label: 'Loading BOM + HRS data', time: '4s', detail: 'Component list and spec parameters extracted for validation' },
      { label: 'Scanning restricted substances', time: '35s', detail: 'RoHS / REACH database cross-check on every component' },
      { label: 'Running EMC pre-compliance', time: '45s', detail: 'IEC 61000: conducted & radiated emissions estimated' },
      { label: 'Mapping safety standards', time: '30s', detail: 'ISO 26262, IEC 61508, MIL-STD-461 rules applied per domain' },
      { label: 'Building compliance matrix', time: '20s', detail: 'PASS / WARN / FAIL scored per standard with evidence' },
      { label: 'Estimating cost impact', time: '15s', detail: 'Non-compliance remediation costs and lead-time impact' },
      { label: 'Exporting compliance report', time: '10s', detail: 'Full report assembled with certificate readiness score' },
    ],
    metrics: { timeSaved: '1 week → 4 min', errorReduction: '91%', confidence: '97%', costImpact: 'Rs 6.8L/yr' },
    inputs: ['BOM / component recommendations from P1', 'Requirements from P1', 'Design type'],
    outputs: ['Compliance report (.md)', 'Compliance matrix (.csv)'],
    tools: ['Claude AI', 'RoHS/REACH rules (built-in)'],
    applicableScopes: ['full', 'front-end', 'downconversion', 'dsp'],
  },
  {
    id: 'P4', code: 'P04', num: 4,
    name: 'Logical Netlist Generation',
    tagline: 'Pre-PCB netlists — the paradigm shift',
    color: '#8b5cf6', auto: true, manual: false, time: '~4 min',
    subSteps: [
      { label: 'Parsing block diagram', time: '8s', detail: 'Converting P1 block diagram into a graph model' },
      { label: 'Resolving component pinouts', time: '22s', detail: 'Each block mapped to real component pins from datasheets' },
      { label: 'Weaving connectivity graph', time: '30s', detail: 'Net connections derived from interface definitions in HRS' },
      { label: 'Classifying net types', time: '15s', detail: 'Power, ground, differential pairs, high-speed nets tagged' },
      { label: 'Running electrical rules check', time: '35s', detail: 'Validating: no floating pins, correct power domains, no shorts' },
      { label: 'Synthesizing schematic', time: '18s', detail: 'Auto-layout: IC placement, power rails, ground symbols, net routing' },
      { label: 'Generating netlist visual', time: '12s', detail: 'Mermaid diagram + DRC report assembled for review' },
    ],
    metrics: { timeSaved: 'Eliminates post-layout rework', errorReduction: '85%', confidence: '95%', costImpact: 'Rs 9.1L/yr' },
    inputs: ['Requirements from P1', 'BOM from P1', 'HRS from P2'],
    outputs: ['Netlist JSON (.json)', 'Netlist visual (.md)', 'Netlist validation report (.json)'],
    tools: ['Claude AI', 'NetworkX (graph validation)'],
    applicableScopes: ['full', 'front-end', 'downconversion', 'dsp'],
  },
  {
    id: 'P5', code: 'P05', num: 5,
    name: 'PCB Layout',
    tagline: 'Engineer-driven EDA tool',
    color: '#475569', auto: false, manual: true, time: 'Days-Weeks',
    externalTool: 'Altium Designer / KiCad / OrCAD',
    subSteps: [
      { label: 'Import validated netlist (P4)', time: '5 min', detail: 'Zero connectivity ambiguity — netlist pre-validated by AI' },
      { label: 'Define layer stackup', time: '2 hrs', detail: '6-layer: signal, ground, power, signal, ground, signal' },
      { label: 'Component placement', time: '1-2 days', detail: 'Manual placement following mechanical constraints' },
      { label: 'Route critical signals', time: '2-3 days', detail: 'Differential pairs, high-speed, RF traces routed manually' },
      { label: 'DRC / ERC check', time: '2 hrs', detail: 'Design rule check in EDA tool, resolve all violations' },
      { label: 'Gerber export', time: '30 min', detail: 'Manufacturing files: Gerber, drill, BOM, assembly drawing' },
    ],
    metrics: { timeSaved: 'N/A (manual)', errorReduction: '85% fewer netlist errors', confidence: 'N/A', costImpact: 'Reduced re-spins' },
    inputs: ['KiCad netlist from P4', 'Mechanical constraints', 'PCB stackup spec'],
    outputs: ['PCB layout file', 'Gerber files', 'Assembly drawing', 'Drill file'],
    tools: ['Altium Designer', 'KiCad', 'OrCAD'],
    applicableScopes: ['full', 'front-end', 'downconversion', 'dsp'],
  },
  {
    id: 'P6', code: 'P06', num: 6,
    name: 'GLR Specification',
    tagline: 'Glue logic requirements for FPGA/CPLD',
    color: '#00c6a7', auto: true, manual: false, time: '~4 min',
    subSteps: [
      { label: 'Ingesting P4 netlist', time: '5s', detail: 'Connectivity graph parsed, FPGA/CPLD boundary nodes identified' },
      { label: 'Mapping FPGA I/O boundaries', time: '20s', detail: 'Logic cells, I/O banks, clock domains traced' },
      { label: 'Deriving glue logic specs', time: '35s', detail: 'Level shifting, bus arbitration, state machine requirements extracted' },
      { label: 'Generating RTL constraints', time: '40s', detail: 'Timing constraints, I/O standards, pin assignments formulated' },
      { label: 'Writing GLR specification', time: '80s', detail: 'Truth tables, state diagrams, timing waveforms, 9 functional sections' },
      { label: 'Assembling final document', time: '10s', detail: 'Scope, pinout table, RTM, and all sections compiled' },
    ],
    metrics: { timeSaved: '1 week → 4 min', errorReduction: '78%', confidence: '95%', costImpact: 'Rs 5.2L/yr' },
    inputs: ['Requirements from P1', 'Netlist JSON from P4'],
    outputs: ['GLR specification (.md) — scope, pinout table (35+ signals), functional specs (9 sections), RTM'],
    tools: ['Claude AI'],
    applicableScopes: ['full', 'front-end', 'downconversion', 'dsp'],
  },
  {
    id: 'P7a', code: 'P07a', num: 7,
    name: 'Register Map & Programming Sequence',
    tagline: 'AI-generated RDT + PSQ from GLR + RTL',
    color: '#f59e0b', auto: true, manual: false, time: '~2 min',
    subSteps: [
      { label: 'Parsing GLR + FPGA design', time: '5s', detail: 'GLR spec and FPGA RTL outputs ingested for register extraction' },
      { label: 'Discovering register blocks', time: '20s', detail: 'Address, width, access type, reset values catalogued' },
      { label: 'Building Register Description Table', time: '60s', detail: 'Bit-field breakdown: name, bits, R/W/RO/RC access, per-bit description' },
      { label: 'Crafting Programming Sequence', time: '45s', detail: 'Ordered init steps: power-on, clock, peripherals with rationale' },
      { label: 'Validating dependencies', time: '15s', detail: 'Unlock sequences, self-clearing bits, poll conditions verified' },
      { label: 'Exporting RDT + PSQ', time: '10s', detail: 'Register table and programming sequence documents assembled' },
    ],
    metrics: { timeSaved: '2 days → 2 min', errorReduction: '90%', confidence: '95%', costImpact: 'Rs 3.4L/yr' },
    inputs: ['GLR specification from P6', 'Netlist from P4', 'HRS from P2'],
    outputs: ['Register Description Table (.md)', 'Programming Sequence (.md)', 'register_map.json', 'programming_sequence.json'],
    tools: ['Claude AI', 'GLR parser', 'Register map validator'],
    applicableScopes: ['full', 'front-end', 'downconversion', 'dsp'],
  },
  {
    id: 'P7', code: 'P07', num: 7,
    name: 'FPGA RTL Design',
    tagline: 'AI-generated Verilog + testbench + constraints',
    color: '#10b981', auto: true, manual: false, time: '~4 min',
    subSteps: [
      { label: 'Loading GLR + register map + netlist', time: '5s', detail: 'GLR spec, P7a register_map.json, netlist, and HRS ingested - the RTL register file mirrors P7a exactly' },
      { label: 'Designing top-level architecture', time: '30s', detail: 'Module hierarchy, clock domains, reset strategy planned' },
      { label: 'Generating Verilog/VHDL RTL', time: '90s', detail: 'Synthesisable RTL: project register file (case statement per P7a address), FSMs from GLR, peripheral ports from BOM' },
      { label: 'Building SystemVerilog testbench', time: '45s', detail: 'Self-checking TB: clock gen, reset, register R/W verification' },
      { label: 'Writing Vivado XDC constraints', time: '15s', detail: 'Timing, I/O delays, false paths, pin assignments' },
      { label: 'Compiling design report', time: '10s', detail: 'Port table, FSM diagrams, resource estimates, Vivado commands' },
    ],
    metrics: { timeSaved: '2 weeks → 4 min', errorReduction: '82%', confidence: '95%', costImpact: 'Rs 7.5L/yr' },
    inputs: ['GLR specification from P6', 'Register Map from P7a', 'Netlist from P4', 'HRS from P2'],
    outputs: ['fpga_top.v (Verilog RTL)', 'fpga_testbench.v (SystemVerilog TB)', 'fpga_coverage.sv (SV covergroups)', 'constraints.xdc (Vivado)', 'rtl/*.v component controllers (uart/spi/i2c/pll/adc/flash/gpio/eeprom)', 'FPGA design report (.md)'],
    tools: ['Claude AI', 'Verilog-2001', 'Vivado XDC'],
    applicableScopes: ['full', 'front-end', 'downconversion', 'dsp'],
  },
  {
    id: 'P8a', code: 'P08a', num: 8,
    name: 'SRS Document',
    tagline: 'Software Requirements Specification',
    color: '#00c6a7', auto: true, manual: false, time: '~4 min',
    subSteps: [
      { label: 'Pulling hardware specs from P1-P4', time: '5s', detail: 'Interfaces, signals, and protocols gathered across phases' },
      { label: 'Defining software interfaces', time: '25s', detail: 'Driver APIs, HAL layer, communication protocols specified' },
      { label: 'Writing functional requirements', time: '90s', detail: 'All SW requirements authored with unique IDs and rationale' },
      { label: 'Writing non-functional requirements', time: '40s', detail: 'Performance, memory, RTOS, safety-level constraints defined' },
      { label: 'Linking traceability matrix', time: '20s', detail: 'HW-to-SW requirement cross-references validated' },
      { label: 'Compiling SRS document', time: '10s', detail: 'IEEE 830 compliant SRS assembled with revision history' },
    ],
    metrics: { timeSaved: '2 weeks → 4 min', errorReduction: '74%', confidence: '95%', costImpact: 'Rs 7.6L/yr' },
    inputs: ['Requirements from P1', 'HRS from P2', 'GLR specification from P6'],
    outputs: ['SRS document (.md)', 'SRS document (.docx)'],
    tools: ['Claude AI', 'python-docx'],
    applicableScopes: ['full', 'front-end', 'downconversion', 'dsp'],
  },
  {
    id: 'P8b', code: 'P08b', num: 9,
    name: 'SDD Document',
    tagline: 'Software Design Document',
    color: '#3b82f6', auto: true, manual: false, time: '~4 min',
    subSteps: [
      { label: 'Ingesting SRS from P8a', time: '5s', detail: 'All software requirements parsed and structured' },
      { label: 'Architecting software layers', time: '60s', detail: 'HAL, drivers, middleware, application layers designed' },
      { label: 'Specifying module interfaces', time: '35s', detail: 'Function signatures, data structures, return codes defined' },
      { label: 'Authoring design descriptions', time: '80s', detail: 'Each module detailed with flowcharts and pseudocode' },
      { label: 'Rendering architecture diagrams', time: '25s', detail: 'Class, sequence, and state machine diagrams generated' },
      { label: 'Compiling SDD document', time: '10s', detail: 'IEEE 1016 compliant design document assembled' },
    ],
    metrics: { timeSaved: '2 weeks → 4 min', errorReduction: '70%', confidence: '95%', costImpact: 'Rs 8.9L/yr' },
    inputs: ['SRS from P8a', 'HRS from P2', 'GLR specification from P6'],
    outputs: ['SDD document (.md)', 'SDD document (.docx)'],
    tools: ['Claude AI', 'python-docx', 'Mermaid (embedded diagrams)'],
    applicableScopes: ['full', 'front-end', 'downconversion', 'dsp'],
  },
  {
    id: 'P8c', code: 'P08c', num: 10,
    name: 'Code Generation + Review',
    tagline: 'C/C++ drivers, Qt GUI, MISRA-C analysis, CI/CD',
    color: '#8b5cf6', auto: true, manual: false, time: '~4 min',
    subSteps: [
      { label: 'Generating C/C++ device drivers', time: '30s', detail: 'HAL register layer, interrupt handlers, DMA, error codes' },
      { label: 'Running Cppcheck analysis', time: '45s', detail: 'Static analysis: null pointers, memory leaks, MISRA rule mapping' },
      { label: 'Measuring code complexity', time: '25s', detail: 'Lizard cyclomatic complexity per function, Rule 15.5 check' },
      { label: 'Building Qt GUI application', time: '60s', detail: 'Qt 5.14.2 panels: Dashboard, Control, Log, Settings, Serial worker' },
      { label: 'Generating CI/CD workflow', time: '12s', detail: 'GitHub Actions YAML assembled and syntax-validated' },
      { label: 'Deep MISRA-C 2023 review', time: '50s', detail: 'Mapping findings to rule numbers, before/after fixes, CWE codes' },
      { label: 'Committing artefacts', time: '12s', detail: 'Git commit + GitHub PR with full review summary' },
    ],
    metrics: { timeSaved: '3 days → 4 min', errorReduction: '83%', confidence: '95%', costImpact: 'Rs 4.8L/yr' },
    inputs: ['Firmware source files (.c/.h)', 'SDD from P8b', 'SRS from P8a', 'Register map from P7a'],
    outputs: [
      'C/C++ driver source files',
      'Qt 5.14.2 GUI (.pro, .ui files, MainWindow, DashboardPanel, ControlPanel, LogPanel, SettingsPanel, SerialWorker)',
      'GitHub Actions CI/CD workflow (.yml)',
      'Code review report (.md)',
      'Git commit + GitHub PR URL',
    ],
    tools: ['Cppcheck', 'Lizard', 'cpplint', 'Claude AI (MISRA-C 2023)', 'Qt 5.14.2 / QMake', 'gitpython', 'PyGithub'],
    applicableScopes: ['full', 'front-end', 'downconversion', 'dsp'],
  },
];

export function getPhase(id: string): PhaseMeta | undefined {
  return PHASES.find(p => p.id === id);
}

export function getPhaseByIndex(idx: number): PhaseMeta {
  return PHASES[idx];
}

// Returns true if phase is unlocked given completed phase IDs.
// Manual phases (P5 PCB Layout) are always locked in the UI
// but their AI successors unlock when the nearest prior AI
// phase is complete — manual phases are simply skipped in the dependency chain.
/**
 * v20 — Is this phase applicable for the current design scope?
 * A phase without applicableScopes (or empty) applies to all scopes.
 * When `scope` is undefined (no scope picked yet), all phases are applicable.
 */
export function isPhaseApplicable(phase: PhaseMeta, scope?: DesignScope): boolean {
  if (!scope) return true;
  const scopes = phase.applicableScopes;
  if (!scopes || scopes.length === 0) return true;
  return scopes.includes(scope);
}

export function isUnlocked(phase: PhaseMeta, completedIds: string[]): boolean {
  if (phase.id === 'P1') return true;
  if (phase.manual) return false; // manual phases cannot be "run" — always shown locked
  const idx = PHASES.findIndex(p => p.id === phase.id);
  if (idx <= 0) return true;
  // Walk backwards to find the nearest non-manual predecessor
  for (let i = idx - 1; i >= 0; i--) {
    const prev = PHASES[i];
    if (!prev.manual) {
      return completedIds.includes(prev.id);
    }
    // prev is manual — skip it and keep looking
  }
  return true; // no prior AI phase found
}

// Document files generated by each phase
export const PHASE_DOCUMENTS: Record<string, string[]> = {
  'P1': ['requirements.md', 'block_diagram.md', 'architecture.md', 'component_recommendations.md', 'power_calculation.md', 'gain_loss_budget.md', 'cascade_analysis.json'],
  'P2': ['HRS_{project_name}.md', 'HRS_{project_name}.docx', 'HRS_{project_name}.pdf'],
  'P3': ['compliance_report.md', 'compliance_matrix.csv'],
  'P4': ['netlist_visual.md', 'drc_report.md'],
  'P5': [], // Manual phase - no AI-generated documents
  'P6': ['GLR_{project_name}.md', 'glr_specification.md'],
  // Trailing-slash entries are PREFIX matches (any file under that
  // directory shows up). Without the slash an entry is an exact-name
  // match. P26 (2026-05-04): switched P7 (rtl/) and P8c (drivers/,
  // qt_gui/, .github/) to prefix mode after the user reported the
  // export ZIP contained drivers and qt_gui panels that the UI was
  // hiding.
  'P7': [
    'fpga_design_report.md',
    'rtl/',
  ],
  'P7a': ['register_description_table.md', 'programming_sequence.md'],
  'P8a': ['SRS_{project_name}.md', 'SRS_{project_name}.docx', 'traceability_matrix.csv'],
  'P8b': ['SDD_{project_name}.md', 'SDD_{project_name}.docx'],
  'P8c': [
    'code_review_report.md',
    'ci_validation_report.md',
    'git_summary.md',
    'drivers/',
    'qt_gui/',
    '.github/',
    'cmake/',
    'Makefile',
  ],
};

// Get documents for the given phase only (not cumulative across prior phases).
// Each phase's Documents tab shows only that phase's output files.
export function getVisibleDocuments(phaseId: string, projectName: string): string[] {
  // Backend agents use project_name.replace(' ', '_') when naming output files.
  // e.g. project "BLDC Motor Controller" → "HRS_BLDC_Motor_Controller.md"
  const safeName = projectName.replace(/ /g, '_');
  const docs = PHASE_DOCUMENTS[phaseId] || [];
  return docs.map(doc => doc.replace('{project_name}', safeName));
}

/** True iff `filename` should appear in the Documents tab for `phaseId`.
 *  Entries in PHASE_DOCUMENTS that end with "/" are treated as directory
 *  prefixes (any descendant matches); other entries are exact-name matches. */
export function isVisibleDocument(
  phaseId: string,
  filename: string,
  projectName: string,
): boolean {
  const entries = getVisibleDocuments(phaseId, projectName);
  for (const e of entries) {
    if (e.endsWith('/')) {
      // Directory prefix: match any file inside (one or more levels deep).
      // Normalise both to forward slashes so Windows paths still match.
      const fname = filename.replace(/\\/g, '/');
      if (fname.startsWith(e) && fname.length > e.length) return true;
    } else if (e === filename) {
      return true;
    }
  }
  return false;
}