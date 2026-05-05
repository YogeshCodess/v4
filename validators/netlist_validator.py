"""Netlist Validator - NetworkX-based DRC checks."""

import logging
from typing import Dict

logger = logging.getLogger(__name__)

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False
    logger.warning("NetworkX not available, netlist validation limited")


class NetlistValidator:
    """Validate netlist using NetworkX for design rule checks."""

    def __init__(self):
        self.graph = None
        self.errors = []
        self.warnings = []
        self.info = []

    def validate(self, netlist: Dict) -> Dict:
        """Perform full netlist validation."""
        self.errors = []
        self.warnings = []
        self.info = []

        if not HAS_NETWORKX:
            self._add_warning("NetworkX not available", "Install networkx for full validation")

        # Build graph
        self._build_graph(netlist)

        # Run checks
        self._check_cycles()
        self._check_isolated_nodes()
        self._check_connectivity()
        self._check_pin_compatibility(netlist)
        self._check_power_domains(netlist)

        return {
            "is_valid": len(self.errors) == 0,
            "errors": self.errors,
            "warnings": self.warnings,
            "info": self.info,
            "stats": self._get_stats(netlist),
        }

    def _build_graph(self, netlist: Dict):
        """Build NetworkX graph from netlist."""
        if not HAS_NETWORKX:
            return

        self.graph = nx.DiGraph()

        for node in netlist.get("nodes", []):
            self.graph.add_node(node["id"], **node)

        for edge in netlist.get("edges", []):
            self.graph.add_edge(
                edge["source"],
                edge["target"],
                signal=edge.get("signal", ""),
                **edge
            )

        self._add_info("Graph built", f"Nodes: {len(self.graph.nodes)}, Edges: {len(self.graph.edges)}")

    def _check_cycles(self):
        """Check for feedback loops (may be intentional)."""
        if not HAS_NETWORKX or not self.graph:
            return

        try:
            cycles = list(nx.simple_cycles(self.graph))
            if cycles:
                for cycle in cycles:
                    self._add_warning("Cycle detected", f"Feedback loop: {' -> '.join(cycle)}")
        except Exception as e:
            self._add_warning("Cycle check failed", str(e))

    def _check_isolated_nodes(self):
        """Check for unconnected components."""
        if not HAS_NETWORKX or not self.graph:
            return

        isolated = list(nx.isolates(self.graph))
        if isolated:
            for node in isolated:
                self._add_warning("Isolated node", f"Node {node} has no connections")

    def _check_connectivity(self):
        """Check if graph is weakly connected."""
        if not HAS_NETWORKX or not self.graph:
            return

        if not nx.is_weakly_connected(self.graph):
            components = list(nx.weakly_connected_components(self.graph))
            self._add_warning("Disconnected netlist", f"Netlist has {len(components)} disconnected groups")

    def _check_pin_compatibility(self, netlist: Dict):
        """Check pin voltage/type compatibility."""
        for edge in netlist.get("edges", []):
            # Add placeholder for actual pin compatibility checks
            pass

    def _check_power_domains(self, netlist: Dict):
        """Check power domain consistency."""
        # Add placeholder for power domain checks
        pass

    def _get_stats(self, netlist: Dict) -> Dict:
        """Get netlist statistics."""
        nodes = netlist.get("nodes", [])
        edges = netlist.get("edges", [])

        node_types = {}
        for node in nodes:
            t = node.get("type", "Unknown")
            node_types[t] = node_types.get(t, 0) + 1

        return {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "node_types": node_types,
        }

    def _add_error(self, title: str, message: str):
        self.errors.append({"title": title, "message": message})

    def _add_warning(self, title: str, message: str):
        self.warnings.append({"title": title, "message": message})

    def _add_info(self, title: str, message: str):
        self.info.append({"title": title, "message": message})
