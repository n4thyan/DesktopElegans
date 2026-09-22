from __future__ import annotations
from .models import HostState, SensoryFrame, WormState


class DigitalSensoryMapper:
    """Map abstract habitat + internal state onto named C. elegans sensory neurons.

    The mapping is an engineered interface into c302, not a claim that digital
    resource metrics are biologically equivalent to these sensory modalities.
    Strengths are normalized to 0..1 and later converted into NeuroML current
    pulses by :class:`C302Brain`.
    """

    def __init__(
        self,
        threshold: float = 0.20,
        strength_quantum: float = 0.10,
        hunger_gain: float = 0.35,
        stress_gain: float = 0.45,
        proprioception_gain: float = 1.0,
    ):
        self.threshold = float(threshold)
        self.strength_quantum = max(0.01, min(1.0, float(strength_quantum)))
        self.hunger_gain = max(0.0, float(hunger_gain))
        self.stress_gain = max(0.0, float(stress_gain))
        self.proprioception_gain = max(0.0, float(proprioception_gain))

    def _q(self, value: float) -> float:
        value = max(0.0, min(1.0, float(value)))
        q = round(value / self.strength_quantum) * self.strength_quantum
        return round(max(0.0, min(1.0, q)), 6)

    def sense(self, state: HostState, organism: WormState | None = None) -> SensoryFrame:
        state = state.clamp()
        energy = 1.0 if organism is None else max(0.0, min(1.0, float(organism.energy)))
        stress = 0.0 if organism is None else max(0.0, min(1.0, float(organism.stress)))
        hunger = 1.0 - energy

        base_resource = (state.cpu_free + state.memory_free) / 2.0
        resource = min(1.0, base_resource * (1.0 + hunger * self.hunger_gain))
        base_aversive = 1.0 if state.blocked else max(0.0, 1.0 - state.network_quality)
        aversive = min(1.0, max(base_aversive, stress * self.stress_gain))
        peer = state.peer_signal
        cursor_proximity = state.signals.get("cursor_proximity", 0.0)
        window_presence = state.signals.get("window_presence", 0.0)
        foreground_overlap = state.signals.get("foreground_overlap", 0.0)
        monitor_position = state.signals.get("monitor_position", 0.0)
        resource_proximity = state.signals.get("resource_proximity", 0.0)
        resource_left = state.signals.get("resource_left", 0.0)
        resource_right = state.signals.get("resource_right", 0.0)
        peer_proximity = max(state.peer_signal, state.signals.get("peer_proximity", 0.0))
        peer_left = state.signals.get("peer_left", 0.0)
        peer_right = state.signals.get("peer_right", 0.0)
        context_novelty = state.signals.get("context_novelty", 0.0)
        resource = max(resource, resource_proximity)
        novelty = max(state.novelty, context_novelty, (window_presence + foreground_overlap) * 0.5)
        peer = max(peer, peer_proximity)
        aversive = max(aversive, cursor_proximity)
        proprioception_raw = 0.0 if organism is None else max(0.0, min(1.0, float(organism.proprioception)))
        proprioception = min(1.0, proprioception_raw * self.proprioception_gain)
        body_contact = 0.0 if organism is None else max(0.0, min(1.0, float(organism.body_contact)))

        strengths: dict[str, float] = {}

        def add(neurons: tuple[str, ...], strength: float) -> None:
            strength = self._q(strength)
            if strength < self.threshold:
                return
            for neuron in neurons:
                strengths[neuron] = max(strengths.get(neuron, 0.0), strength)

        # Attractive/resource-like channels, including bounded left/right cues.
        add(("AWAL", "AWAR"), resource)
        add(("AWAL",), resource_left)
        add(("AWAR",), resource_right)
        add(("ASEL", "ASER"), (resource + state.network_quality) / 2.0)
        # Novelty/exploration channel.
        add(("AWCL", "AWCR"), novelty)
        # Social/peer cue channels, still interpreted only by c302.
        add(("ADFL", "ADFR"), peer)
        add(("AWCL",), peer_left)
        add(("AWCR",), peer_right)
        # Strong aversive channel.
        add(("ASHL", "ASHR"), aversive)
        # Closed-loop body feedback. DVA is used as an engineered stretch/
        # curvature channel; ALM/PLM carry contact from the flexible body.
        add(("DVA",), proprioception)
        add(("ALML", "ALMR", "PLML", "PLMR"), body_contact)

        return SensoryFrame(
            stimulated_neurons=list(strengths),
            channels={
                "resource": round(resource, 6),
                "base_resource": round(base_resource, 6),
                "novelty": round(novelty, 6),
                "peer": round(peer, 6),
                "aversive": round(aversive, 6),
                "base_aversive": round(base_aversive, 6),
                "hunger": round(hunger, 6),
                "stress": round(stress, 6),
                "proprioception": round(proprioception, 6),
                "proprioception_raw": round(proprioception_raw, 6),
                "body_contact": round(body_contact, 6),
                "cursor_proximity": round(cursor_proximity, 6),
                "window_presence": round(window_presence, 6),
                "foreground_overlap": round(foreground_overlap, 6),
                "monitor_position": round(monitor_position, 6),
                "resource_proximity": round(resource_proximity, 6),
                "resource_left": round(resource_left, 6),
                "resource_right": round(resource_right, 6),
                "peer_proximity": round(peer_proximity, 6),
                "peer_left": round(peer_left, 6),
                "peer_right": round(peer_right, 6),
                "context_novelty": round(context_novelty, 6),
            },
            neuron_strengths=strengths,
        )

    def map(self, state: HostState, organism: WormState | None = None) -> list[str]:
        return self.sense(state, organism).stimulated_neurons
