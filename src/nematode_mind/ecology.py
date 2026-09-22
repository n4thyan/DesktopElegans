from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Iterable

from .models import WormState


@dataclass(frozen=True)
class Resource:
    resource_id: str
    x: float
    y: float
    nutrition: float
    mass: float
    radius_px: float


@dataclass(frozen=True)
class EcologyConfig:
    enabled: bool = True
    max_resources: int = 24
    initial_resources: int = 16
    regeneration_interval_sec: float = 15.0
    nutrition_min: float = 0.10
    nutrition_max: float = 0.20
    resource_radius_px: float = 2.0
    sensory_radius_px: float = 120.0
    basal_energy_use_per_sec: float = 0.002
    energy_capacity: float = 2.0
    mass_gain_fraction: float = 0.25
    predation_enabled: bool = False
    predation_size_ratio: float = 1.75


class DesktopEcology:
    """Bounded resources and organism interactions in desktop coordinates.

    This layer only emits sensory signals and resolves physical overlap. It never
    chooses a heading or movement; c302 and the flexible body remain responsible
    for locomotion.
    """

    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        config: EcologyConfig | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.bounds = tuple(float(value) for value in bounds)
        if len(self.bounds) != 4 or self.bounds[2] <= self.bounds[0] or self.bounds[3] <= self.bounds[1]:
            raise ValueError("ecology bounds must have positive width and height")
        self.config = config or EcologyConfig()
        self.rng = rng or random.Random()
        self.resources: dict[str, Resource] = {}
        self._next_resource_id = 1
        self._regeneration_elapsed = 0.0
        if self.config.enabled:
            self._fill(min(self.config.initial_resources, self.config.max_resources))

    def _new_resource(self) -> Resource:
        left, top, right, bottom = self.bounds
        radius = max(0.0, self.config.resource_radius_px)
        margin_x = min(radius, (right - left) * 0.5)
        margin_y = min(radius, (bottom - top) * 0.5)
        lo_x, hi_x = left + margin_x, right - margin_x
        lo_y, hi_y = top + margin_y, bottom - margin_y
        resource = Resource(
            resource_id=f"r{self._next_resource_id:06d}",
            x=self.rng.uniform(lo_x, hi_x),
            y=self.rng.uniform(lo_y, hi_y),
            nutrition=self.rng.uniform(self.config.nutrition_min, self.config.nutrition_max),
            mass=self.rng.uniform(self.config.nutrition_min, self.config.nutrition_max),
            radius_px=self.config.resource_radius_px,
        )
        self._next_resource_id += 1
        return resource

    def _fill(self, target: int) -> None:
        target = max(0, min(int(target), self.config.max_resources))
        while len(self.resources) < target:
            item = self._new_resource()
            self.resources[item.resource_id] = item

    @staticmethod
    def _points(worm: WormState) -> list[tuple[float, float]]:
        if worm.body_points:
            return [(float(point[0]), float(point[1])) for point in worm.body_points]
        return [(float(worm.screen_x), float(worm.screen_y))]

    @classmethod
    def _worms_touch(cls, first: WormState, second: WormState, radius: float = 2.0) -> bool:
        limit = max(0.0, float(radius)) * 2.0
        return any(
            math.dist(a, b) <= limit
            for a in cls._points(first)
            for b in cls._points(second)
        )

    def tick(
        self,
        elapsed_sec: float,
        worms: Iterable[WormState],
        *,
        finalize_starvation: bool = True,
    ) -> None:
        if not self.config.enabled:
            return
        elapsed = max(0.0, min(60.0, float(elapsed_sec)))
        living = [worm for worm in worms if worm.alive]
        for worm in living:
            worm.energy = max(0.0, worm.energy - self.config.basal_energy_use_per_sec * elapsed)
        if finalize_starvation:
            self.finalize_starvation(living)
        if len(self.resources) >= self.config.max_resources:
            self._regeneration_elapsed = 0.0
            return
        self._regeneration_elapsed += elapsed
        interval = self.config.regeneration_interval_sec
        while self._regeneration_elapsed >= interval and len(self.resources) < self.config.max_resources:
            self._regeneration_elapsed -= interval
            self._fill(len(self.resources) + 1)

    @staticmethod
    def finalize_starvation(worms: Iterable[WormState]) -> None:
        for worm in worms:
            if worm.alive and worm.energy <= 0.0:
                worm.alive = False
                worm.metadata.setdefault("death_reason", "starvation")

    def consume_resources(self, worm: WormState) -> list[Resource]:
        if not self.config.enabled or not worm.alive:
            return []
        points = self._points(worm)
        consumed: list[Resource] = []
        for resource_id, item in list(self.resources.items()):
            if any(math.dist(point, (item.x, item.y)) <= item.radius_px + 1.5 for point in points):
                consumed.append(self.resources.pop(resource_id))
        for item in consumed:
            worm.energy = min(self.config.energy_capacity, worm.energy + item.nutrition)
            worm.ecology_mass += item.mass * self.config.mass_gain_fraction
            worm.metadata["resources_eaten"] = int(worm.metadata.get("resources_eaten", 0)) + 1
        return consumed

    def sensory_signals(self, worm: WormState, worms: Iterable[WormState]) -> dict[str, float]:
        radius = max(1.0, self.config.sensory_radius_px)
        resource = self._nearest_point(worm, ((item.x, item.y) for item in self.resources.values()), radius)
        peers = (
            (peer.screen_x, peer.screen_y)
            for peer in worms
            if peer.alive and peer.worm_id != worm.worm_id
        )
        peer = self._nearest_point(worm, peers, radius)
        resource_proximity, resource_left, resource_right = resource
        peer_proximity, peer_left, peer_right = peer
        return {
            "resource_proximity": resource_proximity,
            "resource_left": resource_left,
            "resource_right": resource_right,
            "peer_proximity": peer_proximity,
            "peer_left": peer_left,
            "peer_right": peer_right,
        }

    @staticmethod
    def _nearest_point(
        worm: WormState,
        points: Iterable[tuple[float, float]],
        radius: float,
    ) -> tuple[float, float, float]:
        nearest: tuple[float, float] | None = None
        nearest_distance = float("inf")
        for point in points:
            distance = math.dist((worm.screen_x, worm.screen_y), point)
            if distance < nearest_distance:
                nearest, nearest_distance = point, distance
        if nearest is None or nearest_distance >= radius:
            return 0.0, 0.0, 0.0
        proximity = max(0.0, min(1.0, 1.0 - nearest_distance / radius))
        bearing = math.atan2(nearest[1] - worm.screen_y, nearest[0] - worm.screen_x)
        relative = math.atan2(math.sin(bearing - worm.facing_radians), math.cos(bearing - worm.facing_radians))
        lateral = math.sin(relative)
        left = proximity * max(0.0, -lateral)
        right = proximity * max(0.0, lateral)
        return proximity, left, right

    def resolve_predation(self, worms: Iterable[WormState]) -> list[tuple[str, str]]:
        if not self.config.enabled or not self.config.predation_enabled:
            return []
        living = [worm for worm in worms if worm.alive]
        events: list[tuple[str, str]] = []
        for index, first in enumerate(living):
            if not first.alive:
                continue
            for second in living[index + 1 :]:
                if not first.alive:
                    break
                if not second.alive or not self._worms_touch(first, second):
                    continue
                predator, victim = (first, second) if first.ecology_mass >= second.ecology_mass else (second, first)
                if not predator.alive or not victim.alive:
                    continue
                if predator.ecology_mass < victim.ecology_mass * self.config.predation_size_ratio:
                    continue
                victim.alive = False
                victim.metadata["death_reason"] = "predation"
                victim.metadata["predator_id"] = predator.worm_id
                predator.energy = min(self.config.energy_capacity, predator.energy + victim.energy)
                predator.ecology_mass += victim.ecology_mass
                events.append((predator.worm_id, victim.worm_id))
        return events
