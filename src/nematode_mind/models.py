from __future__ import annotations
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List


class WormAction(str, Enum):
    REST = "rest"
    EXPLORE = "explore"
    MIGRATE = "migrate"
    RETREAT = "retreat"


@dataclass
class HostState:
    host_id: str
    cpu_free: float
    memory_free: float
    network_quality: float
    blocked: bool = False
    peer_signal: float = 0.0
    novelty: float = 0.0
    signals: Dict[str, float] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)

    def clamp(self) -> "HostState":
        for name in ("cpu_free", "memory_free", "network_quality", "peer_signal", "novelty"):
            setattr(self, name, max(0.0, min(1.0, float(getattr(self, name)))))
        self.blocked = bool(self.blocked)
        self.signals = {
            str(name): max(0.0, min(1.0, float(value)))
            for name, value in self.signals.items()
        }
        return self

    def copy(self, **changes: Any) -> "HostState":
        payload = asdict(self)
        payload.update(changes)
        return HostState(**payload).clamp()

    @property
    def resource_quality(self) -> float:
        return (self.cpu_free + self.memory_free + self.network_quality) / 3.0


@dataclass(frozen=True)
class SensoryFrame:
    stimulated_neurons: List[str]
    channels: Dict[str, float]
    neuron_strengths: Dict[str, float] = field(default_factory=dict)


@dataclass
class BrainResult:
    stimulated_neurons: List[str]
    neuron_scores: Dict[str, float]
    forward_score: float
    reverse_score: float
    action: WormAction
    sensory_channels: Dict[str, float] = field(default_factory=dict)
    neuron_strengths: Dict[str, float] = field(default_factory=dict)
    motor_neuron_scores: Dict[str, float] = field(default_factory=dict)
    muscle_scores: Dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    decision_margin: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WormState:
    worm_id: str
    host_id: str
    previous_host_id: str | None = None
    energy: float = 1.0
    age_ticks: int = 0
    stress: float = 0.0
    last_action: WormAction = WormAction.REST
    alive: bool = True
    visited_hosts: Dict[str, int] = field(default_factory=dict)
    action_counts: Dict[str, int] = field(default_factory=dict)
    best_host_id: str | None = None
    best_resource_quality: float = 0.0
    last_resource_quality: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Local desktop-organism extensions (v0.5).
    parent_id: str | None = None
    generation: int = 0
    screen_x: float = 0.0
    screen_y: float = 0.0
    facing_radians: float = 0.0
    body_phase: float = 0.0
    reproduction_cooldown: int = 0
    body_points: List[List[float]] = field(default_factory=list)
    body_velocities: List[List[float]] = field(default_factory=list)
    muscle_dorsal: List[float] = field(default_factory=list)
    muscle_ventral: List[float] = field(default_factory=list)
    proprioception: float = 0.0
    body_contact: float = 0.0
    # Generic Win32 host-surface binding. Coordinates remain absolute for
    # physics; these local values make organisms follow host move/resize.
    host_hwnd: int = 0
    host_local_x: float = 0.0
    host_local_y: float = 0.0
    # Ecology mass is deliberately separate from physical biological body size.
    ecology_mass: float = 1.0

    SNAPSHOT_VERSION = 5

    def record_observation(self, habitat: HostState) -> None:
        self.visited_hosts[habitat.host_id] = self.visited_hosts.get(habitat.host_id, 0) + 1
        quality = habitat.resource_quality
        self.last_resource_quality = quality
        if self.best_host_id is None or quality > self.best_resource_quality:
            self.best_host_id = habitat.host_id
            self.best_resource_quality = quality

    def record_action(self, action: WormAction) -> None:
        key = action.value
        self.action_counts[key] = self.action_counts.get(key, 0) + 1

    def to_dict(self) -> dict:
        data = asdict(self)
        data["last_action"] = self.last_action.value
        data["snapshot_version"] = self.SNAPSHOT_VERSION
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "WormState":
        payload = dict(data)
        payload.pop("snapshot_version", None)
        payload["last_action"] = WormAction(payload.get("last_action", WormAction.REST.value))
        # Backwards compatibility with v0.2/v0.3 snapshots.
        payload.setdefault("visited_hosts", {})
        payload.setdefault("action_counts", {})
        payload.setdefault("best_host_id", None)
        payload.setdefault("best_resource_quality", 0.0)
        payload.setdefault("last_resource_quality", 0.0)
        payload.setdefault("metadata", {})
        payload.setdefault("body_points", [])
        payload.setdefault("body_velocities", [])
        payload.setdefault("muscle_dorsal", [])
        payload.setdefault("muscle_ventral", [])
        payload.setdefault("proprioception", 0.0)
        payload.setdefault("body_contact", 0.0)
        payload.setdefault("host_hwnd", 0)
        payload.setdefault("host_local_x", 0.0)
        payload.setdefault("host_local_y", 0.0)
        payload.setdefault("ecology_mass", 1.0)
        # The interrupted Hermes build briefly added this field for an unsafe,
        # unfinished file-scanning layer.  Ignore it when opening that build's
        # snapshots so the local-only desktop release stays backwards compatible.
        payload.pop("consumed_files", None)
        return cls(**payload)
