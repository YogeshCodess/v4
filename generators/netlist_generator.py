"""Netlist Generator - Pre-PCB netlist generation with NetworkX."""

import json
import logging
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)


class NetlistGenerator:
    """Generate netlist from component connectivity specification."""

    def generate(self, project_name: str, components: List[Dict], connections: List[Dict], metadata: Dict = None) -> Dict:
        nodes = []
        edges = []

        for comp in components:
            nodes.append({
                "id": comp.get("id", f"U{len(nodes)+1}"),
                "name": comp.get("name", "Component"),
                "type": comp.get("type", "IC"),
                "pins": comp.get("pins", []),
                "properties": comp.get("properties", {}),
            })

        for conn in connections:
            edges.append({
                "source": conn.get("source"),
                "source_pin": conn.get("source_pin"),
                "target": conn.get("target"),
                "target_pin": conn.get("target_pin"),
                "signal": conn.get("signal", "NET"),
                "type": conn.get("type", "wire"),
            })

        return {
            "project": project_name,
            "version": "1.0",
            "nodes": nodes,
            "edges": edges,
            "metadata": metadata or {},
        }

    @staticmethod
    def _clean_label(text) -> str:
        """Scrub characters that break Mermaid's flowchart parser from a label.

        Removes/replaces: parens, brackets, braces, pipes, quotes, angle brackets,
        newlines, tabs, #, @, and collapses 2+ dashes (which Mermaid reads as arrows).
        """
        import re as _re
        if text is None:
            return ""
        s = str(text)
        s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
        s = s.replace("(", " ").replace(")", " ")
        s = s.replace("{", " ").replace("}", " ")
        s = s.replace("[", " ").replace("]", " ")
        s = s.replace("|", "/").replace('"', " ").replace("'", " ")
        s = s.replace("<", " ").replace(">", " ")
        s = s.replace("#", " ").replace("@", " ")
        s = _re.sub(r"[\u2013\u2014]", "-", s)      # em/en dash → hyphen
        s = _re.sub(r"-{2,}", " ", s)               # 2+ dashes → space
        s = _re.sub(r"^[-=]+|[-=]+$", "", s)         # trim dash/equals at ends
        s = _re.sub(r"\s{2,}", " ", s).strip()
        return s

    def to_mermaid(self, netlist: Dict) -> str:
        lines = ["graph TB"]
        for node in netlist.get("nodes", []):
            raw_id = str(node.get("id", "N") or "N")
            # Node IDs must be alphanumeric — replace anything non-wordchar with _
            import re as _re
            nid = _re.sub(r"[^\w]", "_", raw_id) or "N"
            nname = self._clean_label(node.get("name", ""))
            ntype = self._clean_label(node.get("type", ""))
            label = f"{nname} {ntype}".strip() if ntype else (nname or nid)
            if not label:
                label = nid
            lines.append(f"    {nid}[{label}]")
        for edge in netlist.get("edges", []):
            import re as _re
            src = _re.sub(r"[^\w]", "_", str(edge.get("source", "") or ""))
            tgt = _re.sub(r"[^\w]", "_", str(edge.get("target", "") or ""))
            sig = self._clean_label(edge.get("signal", ""))
            if not src or not tgt:
                continue
            if sig:
                lines.append(f"    {src} -->|{sig}| {tgt}")
            else:
                lines.append(f"    {src} --> {tgt}")
        return "\n".join(lines)

    def save(self, netlist: Dict, output_dir: Path, project_name: str) -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        json_path = output_dir / "netlist.json"
        json_path.write_text(json.dumps(netlist, indent=2), encoding="utf-8")
        
        md_path = output_dir / "netlist_visual.md"
        md_path.write_text(f"```mermaid\n{self.to_mermaid(netlist)}\n```\n", encoding="utf-8")
        
        return json_path
