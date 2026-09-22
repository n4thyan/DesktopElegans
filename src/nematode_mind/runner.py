from __future__ import annotations
import uuid
from typing import Protocol

from .event_log import EventLogger
from .lab import LabEnvironment
from .models import BrainResult, HostState, WormState
from .organism import WormMetabolism
from .state_store import StateStore


class Brain(Protocol):
    def step(self, environment: HostState, organism: WormState | None = None) -> BrainResult: ...


class WormRunner:
    def __init__(
        self,
        brain: Brain,
        environment: LabEnvironment,
        logger: EventLogger,
        metabolism: WormMetabolism | None = None,
        state_store: StateStore | None = None,
    ):
        self.brain = brain
        self.environment = environment
        self.logger = logger
        self.metabolism = metabolism or WormMetabolism()
        self.state_store = state_store

    def new_state(self, start_host: str = "alpha", initial_energy: float = 1.0) -> WormState:
        if start_host not in self.environment.nodes:
            raise ValueError(f"unknown start host: {start_host}")
        return WormState(
            worm_id=str(uuid.uuid4())[:8],
            host_id=start_host,
            energy=max(0.0, min(1.0, float(initial_energy))),
        )

    def run(
        self,
        ticks: int = 5,
        start_host: str = "alpha",
        initial_energy: float = 1.0,
        state: WormState | None = None,
        quiet: bool = False,
    ) -> WormState:
        worm = state or self.new_state(start_host, initial_energy)
        if worm.host_id not in self.environment.nodes:
            raise ValueError(f"snapshot references unknown host: {worm.host_id}")

        for tick_offset in range(max(0, int(ticks))):
            if not worm.alive:
                break

            tick = worm.age_ticks
            observed = self.environment.observe(worm)
            worm.record_observation(observed)
            result = self.brain.step(observed, worm)
            motion = self.environment.apply_motion(worm, result.action)
            self.metabolism.update(worm, observed, result.action)

            self.logger.write({
                "tick": tick,
                "run_tick": tick_offset,
                "worm_id": worm.worm_id,
                "host": observed.host_id,
                "observation": {
                    "cpu_free": observed.cpu_free,
                    "memory_free": observed.memory_free,
                    "network_quality": observed.network_quality,
                    "blocked": observed.blocked,
                    "peer_signal": observed.peer_signal,
                    "novelty": observed.novelty,
                    "resource_quality": observed.resource_quality,
                },
                "sensory_channels": result.sensory_channels,
                "stimulated_neurons": result.stimulated_neurons,
                "neuron_strengths": result.neuron_strengths,
                "neuron_scores": {k: round(v, 6) for k, v in result.neuron_scores.items()},
                "forward_score": round(result.forward_score, 6),
                "reverse_score": round(result.reverse_score, 6),
                "confidence": round(result.confidence, 6),
                "decision_margin": round(result.decision_margin, 6),
                "action": result.action.value,
                "motion": motion,
                "brain": result.metadata,
                "organism": worm.to_dict(),
            })
            if self.state_store:
                self.state_store.save(worm)

            if not quiet:
                print(
                    f"tick={tick} host={observed.host_id} stim={result.stimulated_neurons} "
                    f"F={result.forward_score:.3f} R={result.reverse_score:.3f} "
                    f"conf={result.confidence:.2f} action={result.action.value} -> {worm.host_id} "
                    f"energy={worm.energy:.3f} stress={worm.stress:.3f}"
                )
        return worm
