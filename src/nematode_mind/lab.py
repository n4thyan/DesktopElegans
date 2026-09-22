from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Mapping

from .models import HostState, WormAction, WormState


@dataclass
class LabNode:
    state: HostState
    neighbors: List[str]


class LabEnvironment:
    """Pure in-memory habitat for exercising the core organism loop.

    Nodes are abstract environments, not computers. No discovery, sockets, file
    transfer, persistence, or propagation behaviour exists in this class.
    """

    def __init__(self, nodes: Dict[str, LabNode], dynamic_novelty: bool = True):
        if not nodes:
            raise ValueError("At least one lab node is required")
        self.nodes = nodes
        self.dynamic_novelty = bool(dynamic_novelty)
        for node_id, node in nodes.items():
            unknown = [n for n in node.neighbors if n not in nodes]
            if unknown:
                raise ValueError(f"node {node_id!r} has unknown neighbors: {unknown}")

    @classmethod
    def from_config(cls, cfg: Mapping) -> "LabEnvironment":
        h = cfg.get("habitat", {}) if isinstance(cfg, Mapping) else {}
        node_cfg = h.get("nodes", {}) if isinstance(h, Mapping) else {}
        if not node_cfg:
            return cls.demo()
        nodes: Dict[str, LabNode] = {}
        for node_id, raw in node_cfg.items():
            raw = raw or {}
            nodes[str(node_id)] = LabNode(
                HostState(
                    host_id=str(node_id),
                    cpu_free=float(raw.get("cpu_free", 0.5)),
                    memory_free=float(raw.get("memory_free", 0.5)),
                    network_quality=float(raw.get("network_quality", 0.5)),
                    blocked=bool(raw.get("blocked", False)),
                    peer_signal=float(raw.get("peer_signal", 0.0)),
                    novelty=float(raw.get("novelty", 0.0)),
                ).clamp(),
                [str(n) for n in raw.get("neighbors", [])],
            )
        return cls(nodes, dynamic_novelty=bool(h.get("dynamic_novelty", True)))

    @classmethod
    def demo(cls) -> "LabEnvironment":
        return cls({
            "alpha": LabNode(HostState("alpha", 0.80, 0.75, 0.90, peer_signal=0.8, novelty=0.2), ["beta"]),
            "beta": LabNode(HostState("beta", 0.55, 0.60, 0.75, peer_signal=0.7, novelty=0.8), ["alpha", "gamma"]),
            "gamma": LabNode(HostState("gamma", 0.20, 0.25, 0.15, blocked=True, peer_signal=0.2, novelty=0.5), ["beta"]),
        })

    def observe(self, worm: WormState) -> HostState:
        base = self.nodes[worm.host_id].state
        if not self.dynamic_novelty:
            return base.copy()
        visits = int(worm.visited_hosts.get(worm.host_id, 0))
        memory_novelty = 1.0 / (1.0 + visits)
        return base.copy(novelty=max(base.novelty, memory_novelty))

    def apply_motion(self, worm: WormState, action: WormAction) -> dict:
        node = self.nodes[worm.host_id]
        before = worm.host_id
        reason = "no-motion"
        candidates = list(node.neighbors)

        # No real transport occurs. This changes only the organism's current node
        # inside the in-memory habitat graph.
        if action == WormAction.RETREAT and worm.previous_host_id in node.neighbors:
            target = worm.previous_host_id
            worm.previous_host_id = worm.host_id
            worm.host_id = target
            reason = "return-to-previous"
        elif action == WormAction.RETREAT:
            reason = "no-previous-neighbor"
        elif action == WormAction.MIGRATE and node.neighbors:
            # Deterministic core behaviour: prefer an unvisited neighbor, then a
            # neighbor other than the habitat just left, then the first neighbor.
            unvisited = [n for n in node.neighbors if worm.visited_hosts.get(n, 0) == 0]
            not_previous = [n for n in node.neighbors if n != worm.previous_host_id]
            target = (unvisited or not_previous or node.neighbors)[0]
            worm.previous_host_id = worm.host_id
            worm.host_id = target
            reason = "novel-neighbor" if target in unvisited else "available-neighbor"
        elif action == WormAction.MIGRATE:
            reason = "no-neighbors"

        return {"from": before, "to": worm.host_id, "reason": reason, "candidates": candidates}
