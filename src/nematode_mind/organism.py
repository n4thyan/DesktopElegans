from __future__ import annotations
from dataclasses import dataclass

from .models import HostState, WormAction, WormState


@dataclass(frozen=True)
class MetabolismConfig:
    rest_gain: float = 0.025
    explore_cost: float = 0.025
    migrate_cost: float = 0.07
    retreat_cost: float = 0.045
    rich_habitat_bonus: float = 0.02
    stress_gain_blocked: float = 0.18
    stress_recovery: float = 0.05
    death_energy: float = 0.0
    stress_energy_penalty: float = 0.015
    resource_intake_gain: float = 0.015
    energy_capacity: float = 1.0


class WormMetabolism:
    """Persistent internal state for the digital organism.

    This is intentionally local organism logic: no network discovery, transport,
    persistence, or external propagation code lives here.
    """

    def __init__(self, config: MetabolismConfig | None = None):
        self.config = config or MetabolismConfig()

    def update(self, worm: WormState, habitat: HostState, action: WormAction) -> None:
        cfg = self.config
        costs = {
            WormAction.REST: -cfg.rest_gain,
            WormAction.EXPLORE: cfg.explore_cost,
            WormAction.MIGRATE: cfg.migrate_cost,
            WormAction.RETREAT: cfg.retreat_cost,
        }
        worm.energy -= costs[action]

        if not habitat.blocked:
            intake_factor = 1.0 if action == WormAction.REST else (0.5 if action == WormAction.EXPLORE else 0.0)
            worm.energy += cfg.resource_intake_gain * habitat.resource_quality * intake_factor
            if habitat.resource_quality >= 0.7:
                worm.energy += cfg.rich_habitat_bonus

        if habitat.blocked:
            worm.stress += cfg.stress_gain_blocked
        else:
            worm.stress -= cfg.stress_recovery

        worm.stress = max(0.0, min(1.0, worm.stress))
        worm.energy -= cfg.stress_energy_penalty * worm.stress
        worm.energy = max(0.0, min(max(0.0, cfg.energy_capacity), worm.energy))
        worm.age_ticks += 1
        worm.last_action = action
        worm.record_action(action)
        worm.alive = worm.energy > cfg.death_energy
        if not worm.alive:
            worm.metadata.setdefault("death_reason", "energy_depleted")
