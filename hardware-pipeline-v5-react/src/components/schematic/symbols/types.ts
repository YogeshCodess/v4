// Shared types for schematic symbol library.
// Every symbol is rendered with 1 grid unit = GRID px in SVG space.
// Components expose a `getPinAnchor(pinName)` helper so the net router can find pins.

export const GRID = 40; // px per grid unit

export interface PinSpec {
  name: string;
  num?: string;
  side: 'left' | 'right' | 'top' | 'bottom';
}

export interface ComponentData {
  ref: string;
  type:
    | 'resistor' | 'capacitor' | 'capacitor_polar' | 'inductor'
    | 'diode' | 'diode_zener' | 'diode_tvs' | 'diode_led'
    | 'ic' | 'ground' | 'vcc' | 'connector' | 'net_label';
  value?: string;
  part_number?: string;
  x: number;   // grid column
  y: number;   // grid row
  rot?: 0 | 90 | 180 | 270;
  pins?: PinSpec[];
}

export interface NetEndpoint { ref: string; pin: string; }
export interface Waypoint { x: number; y: number; }
export interface NetData {
  name: string;
  type?: 'signal' | 'power' | 'ground' | 'clock' | 'differential' | 'analog';
  endpoints: NetEndpoint[];
  waypoints?: Waypoint[];
}

export interface SheetData {
  id: string;
  title: string;
  components: ComponentData[];
  nets: NetData[];
}

export interface SchematicData {
  sheets: SheetData[];
  auto_synthesized?: boolean;
}

// Pin anchor returns grid coordinates for a given component + pin name.
// Returned (x,y) is in grid units — multiply by GRID for SVG px.
export type PinAnchor = { x: number; y: number };

// Net type colour palette — matches v5 design system
export const NET_COLORS: Record<string, string> = {
  power:        '#f59e0b',
  ground:       '#64748b',
  clock:        '#8b5cf6',
  differential: '#3b82f6',
  analog:       '#10b981',
  signal:       '#00c6a7',
  default:      '#94a3b8',
};
