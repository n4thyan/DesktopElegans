from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass
from typing import Dict, Tuple

from .brain_worker import Brain, BrainWorker
from .colony_sandbox import ColonySandbox, ColonySandboxConfig
from .desktop_environment import DesktopEnvironment, desktop_dpi, desktop_geometry
from .ecology import DesktopEcology, EcologyConfig
from .host_surfaces import (
    HostSurfaceCatalog,
    WindowsHostSurfaceManager,
    bind_worm_to_surface,
    sync_worm_to_surface,
    update_worm_local_coordinates,
)
from .models import WormAction, WormState
from .neuromechanics import BodyPhysicsConfig, FlexibleBody, MotorToMuscleDecoder, MuscleActivation
from .organism import MetabolismConfig, WormMetabolism
from .process_transport import LocalProcessTransport, ProcessTransportConfig


@dataclass
class WormManagerConfig:
    body_length_mm: float = 1.0
    render_fps: int = 60
    initial_worms: int = 1
    brain_parameter_set: str = "A"
    brain_duration_ms: float = 150.0
    brain_dt_ms: float = 0.05
    brain_decision_mode: str = "raw"
    brain_baseline_gain: float = 2.0
    brain_work_dir: str = ".runtime/c302"
    brain_interval_sec: float = 0.5
    brain_shutdown_timeout_sec: float = 120.0
    brain_cache_size: int = 4096
    brain_stimulus_min_pa: float = 1.5
    brain_stimulus_max_pa: float = 5.0
    brain_stimulus_delay_ms: float = 10.0
    brain_stimulus_duration_ms: float | None = None
    brain_sensory_threshold: float = 0.20
    brain_sensory_quantum: float = 0.10
    brain_hunger_gain: float = 0.35
    brain_stress_gain: float = 0.45
    brain_proprioception_gain: float = 20.0
    desktop_region_tile_px: int = 200
    desktop_workspace_factor: float = 0.85
    desktop_dpi: float | None = None
    organism_initial_energy: float = 1.0
    organism_rest_gain: float = 0.025
    organism_explore_cost: float = 0.025
    organism_migrate_cost: float = 0.07
    organism_retreat_cost: float = 0.045
    organism_resource_intake_gain: float = 0.015
    organism_rich_habitat_bonus: float = 0.02
    organism_stress_gain_blocked: float = 0.18
    organism_stress_recovery: float = 0.05
    organism_stress_energy_penalty: float = 0.015
    organism_death_energy: float = 0.0
    reproduction_enabled: bool = True
    reproduction_max_worms: int = 12
    reproduction_min_energy: float = 0.90
    reproduction_offspring_energy: float = 0.22
    reproduction_parent_energy_cost: float = 0.30
    reproduction_cooldown_ticks: int = 20
    reproduction_separation_px: float = 16.0
    physics_segments: int = 13
    physics_stiffness: float = 0.72
    physics_damping: float = 0.82
    physics_muscle_gain: float = 1.8
    physics_propulsion_gain: float = 2.8
    physics_substeps: int = 4
    process_transport_enabled: bool = False
    process_transport_max_children: int = 2
    process_transport_max_generation: int = 1
    process_transport_startup_timeout_sec: float = 5.0
    process_transport_shutdown_timeout_sec: float = 3.0
    ecology_enabled: bool = True
    ecology_arcade_growth: bool = False
    ecology_max_visual_growth: float = 2.0
    ecology_predation_enabled: bool = False
    ecology_predation_size_ratio: float = 1.75
    ecology_basal_energy_use_per_sec: float = 0.002
    ecology_energy_capacity: float = 2.0
    ecology_mass_gain_fraction: float = 0.25
    resources_max_count: int = 24
    resources_initial_count: int = 16
    resources_regeneration_interval_sec: float = 15.0
    resources_nutrition_min: float = 0.10
    resources_nutrition_max: float = 0.20
    resources_radius_px: float = 2.0
    resources_sensory_radius_px: float = 120.0
    replication_max_living_worms: int = 12
    replication_max_generations: int = 3
    sandbox_enabled: bool = False
    sandbox_root: str = "~/Desktop/WormHabitat"
    sandbox_max_disk_bytes: int = 16 * 1024 * 1024
    sandbox_max_state_bytes: int = 64 * 1024
    sandbox_cleanup_timeout_sec: float = 5.0
    sandbox_per_worm_directories: bool = True
    visual_debug_enabled: bool = False
    visual_debug_scale: int = 20
    visual_debug_show_coordinates: bool = True
    visual_debug_show_sensory_state: bool = False


class WormManager:
    """Coordinate local worms while c302 runs off the render/control thread."""

    def __init__(self, cfg: WormManagerConfig, rng: random.Random | None = None):
        self.cfg = cfg
        self.rng = rng or random.Random()
        self.environment: DesktopEnvironment | None = None
        self.ecology: DesktopEcology | None = None
        self.brain: Brain | None = None
        self.metabolism: WormMetabolism | None = None
        self.worker: BrainWorker | None = None
        self.overlay = None
        self.host_surface_manager = None
        self.host_catalog: HostSurfaceCatalog | None = None
        self.worms: Dict[str, WormState] = {}
        self._next_id = 1
        self._next_brain_at: Dict[str, float] = {}
        self._submitted_habitat = {}
        self._activations: Dict[str, MuscleActivation] = {}
        self._last_physics_at: float | None = None
        self._brain_ticks = 0
        self._fatal_error: BaseException | None = None
        self.motor_decoder = MotorToMuscleDecoder(self.cfg.physics_segments)
        self.body = FlexibleBody(BodyPhysicsConfig(
            segments=self.cfg.physics_segments,
            length_px=max(2.0, self.cfg.body_length_mm * (self.cfg.desktop_dpi or desktop_dpi()) / 25.4),
            stiffness=self.cfg.physics_stiffness,
            damping=self.cfg.physics_damping,
            muscle_gain=self.cfg.physics_muscle_gain,
            propulsion_gain=self.cfg.physics_propulsion_gain,
            substeps=self.cfg.physics_substeps,
        ))
        self.transport = LocalProcessTransport(ProcessTransportConfig(
            enabled=self.cfg.process_transport_enabled,
            max_children=min(self.cfg.process_transport_max_children, self.cfg.replication_max_living_worms),
            max_generation=min(self.cfg.process_transport_max_generation, self.cfg.replication_max_generations),
            startup_timeout_sec=self.cfg.process_transport_startup_timeout_sec,
            shutdown_timeout_sec=self.cfg.process_transport_shutdown_timeout_sec,
        ))
        self.sandbox = ColonySandbox(ColonySandboxConfig(
            enabled=self.cfg.sandbox_enabled,
            root=self.cfg.sandbox_root,
            max_living_worms=self.cfg.replication_max_living_worms,
            max_generations=self.cfg.replication_max_generations,
            max_disk_bytes=self.cfg.sandbox_max_disk_bytes,
            max_state_bytes=self.cfg.sandbox_max_state_bytes,
            cleanup_timeout_sec=self.cfg.sandbox_cleanup_timeout_sec,
            per_worm_directories=self.cfg.sandbox_per_worm_directories,
        ))

    def setup(
        self,
        brain: Brain | None = None,
        dimensions: Tuple[int, int] | None = None,
        origin: Tuple[int, int] | None = None,
        surface_manager=None,
    ) -> None:
        use_real_desktop = dimensions is None
        if dimensions is None:
            virtual_left, virtual_top, virtual_width, virtual_height = desktop_geometry()
            dimensions = (virtual_width, virtual_height)
            origin = (virtual_left, virtual_top)
        if origin is None:
            origin = (0, 0)
        width, height = dimensions
        self.environment = DesktopEnvironment(
            screen_width=width,
            screen_height=height,
            screen_left=origin[0],
            screen_top=origin[1],
            region_tile_pixels=self.cfg.desktop_region_tile_px,
            workspace_factor=self.cfg.desktop_workspace_factor,
            rng=self.rng,
        )
        self.host_surface_manager = surface_manager
        if self.host_surface_manager is None and use_real_desktop and os.name == "nt":
            self.host_surface_manager = WindowsHostSurfaceManager()
        if self.host_surface_manager is not None:
            self.host_catalog = self.host_surface_manager.refresh()
        self.ecology = DesktopEcology(
            self.environment.workspace_bounds,
            EcologyConfig(
                enabled=self.cfg.ecology_enabled,
                max_resources=self.cfg.resources_max_count,
                initial_resources=self.cfg.resources_initial_count,
                regeneration_interval_sec=self.cfg.resources_regeneration_interval_sec,
                nutrition_min=self.cfg.resources_nutrition_min,
                nutrition_max=self.cfg.resources_nutrition_max,
                resource_radius_px=self.cfg.resources_radius_px,
                sensory_radius_px=self.cfg.resources_sensory_radius_px,
                basal_energy_use_per_sec=self.cfg.ecology_basal_energy_use_per_sec,
                energy_capacity=self.cfg.ecology_energy_capacity,
                mass_gain_fraction=self.cfg.ecology_mass_gain_fraction,
                predation_enabled=self.cfg.ecology_predation_enabled,
                predation_size_ratio=self.cfg.ecology_predation_size_ratio,
            ),
            self.rng,
        )
        if brain is None:
            from .brain_c302 import C302Brain

            brain = C302Brain(
                parameter_set=self.cfg.brain_parameter_set,
                duration_ms=self.cfg.brain_duration_ms,
                dt_ms=self.cfg.brain_dt_ms,
                work_dir=self.cfg.brain_work_dir,
                decision_mode=self.cfg.brain_decision_mode,
                baseline_gain=self.cfg.brain_baseline_gain,
                result_cache_size=self.cfg.brain_cache_size,
                stimulus_min_pa=self.cfg.brain_stimulus_min_pa,
                stimulus_max_pa=self.cfg.brain_stimulus_max_pa,
                stimulus_delay_ms=self.cfg.brain_stimulus_delay_ms,
                stimulus_duration_ms=self.cfg.brain_stimulus_duration_ms,
                sensory_threshold=self.cfg.brain_sensory_threshold,
                sensory_quantum=self.cfg.brain_sensory_quantum,
                hunger_gain=self.cfg.brain_hunger_gain,
                stress_gain=self.cfg.brain_stress_gain,
                proprioception_gain=self.cfg.brain_proprioception_gain,
            )
        self.brain = brain
        self.worker = BrainWorker(brain)
        self.metabolism = WormMetabolism(MetabolismConfig(
            rest_gain=self.cfg.organism_rest_gain,
            explore_cost=self.cfg.organism_explore_cost,
            migrate_cost=self.cfg.organism_migrate_cost,
            retreat_cost=self.cfg.organism_retreat_cost,
            rich_habitat_bonus=self.cfg.organism_rich_habitat_bonus,
            stress_gain_blocked=self.cfg.organism_stress_gain_blocked,
            stress_recovery=self.cfg.organism_stress_recovery,
            stress_energy_penalty=self.cfg.organism_stress_energy_penalty,
            resource_intake_gain=self.cfg.organism_resource_intake_gain,
            death_energy=self.cfg.organism_death_energy,
            energy_capacity=self.cfg.ecology_energy_capacity,
        ))
        initial_count = min(
            max(1, int(self.cfg.initial_worms)), self.cfg.replication_max_living_worms
        )
        left, top, right, bottom = self.environment.workspace_bounds
        for index in range(initial_count):
            if initial_count == 1:
                position = self.environment.workspace_center()
            else:
                x_fraction = (index + 1) / (initial_count + 1)
                y_fraction = 0.35 if index % 2 == 0 else 0.65
                position = (
                    left + (right - left) * x_fraction,
                    top + (bottom - top) * y_fraction,
                )
            self.add_worm(screen_position=position)

    def start(self) -> None:
        if self.worker is None:
            raise RuntimeError("call setup() before start()")
        self.worker.start()

    def stop(self) -> bool:
        if self.overlay is not None:
            self.overlay.stop()
        self.transport.stop()
        if self.worker is not None:
            return self.worker.stop(timeout=self.cfg.brain_shutdown_timeout_sec)
        return True

    def _new_id(self) -> str:
        worm_id = f"w{self._next_id:04d}"
        self._next_id += 1
        return worm_id

    def add_worm(
        self,
        parent: WormState | None = None,
        screen_position: tuple[float, float] | None = None,
    ) -> WormState:
        if self.environment is None:
            raise RuntimeError("call setup() before add_worm()")
        if len(self.alive_worms()) >= self.cfg.replication_max_living_worms:
            raise RuntimeError("maximum living worm limit reached")
        generation = parent.generation + 1 if parent else 0
        if generation > self.cfg.replication_max_generations:
            raise RuntimeError("maximum worm generation reached")
        center_x, center_y = screen_position or self.environment.workspace_center()
        worm = WormState(
            worm_id=self._new_id(),
            host_id="desktop-workspace",
            previous_host_id="desktop-workspace",
            energy=self.cfg.organism_initial_energy,
            parent_id=parent.worm_id if parent else None,
            generation=parent.generation + 1 if parent else 0,
            screen_x=center_x + self.rng.uniform(-80.0, 80.0),
            screen_y=center_y + self.rng.uniform(-60.0, 60.0),
            facing_radians=self.rng.uniform(0.0, math.tau),
            body_phase=self.rng.uniform(0.0, math.tau),
        )
        self.environment.register_position(worm.worm_id, worm.screen_x, worm.screen_y)
        worm.screen_x, worm.screen_y = self.environment.position(worm.worm_id)
        self.body.initialise(worm)
        if self.host_catalog is not None:
            bind_worm_to_surface(worm, self.host_catalog.desktop)
            worm.screen_x, worm.screen_y = self.environment.set_host_position(
                worm.worm_id, worm.screen_x, worm.screen_y
            )
        self._activations[worm.worm_id] = MuscleActivation(
            dorsal=(0.0,) * self.cfg.physics_segments,
            ventral=(0.0,) * self.cfg.physics_segments,
            forward_drive=0.0,
            reverse_drive=0.0,
        )
        self.worms[worm.worm_id] = worm
        self._next_brain_at[worm.worm_id] = 0.0
        if self.cfg.sandbox_enabled:
            self.sandbox.persist(worm)
        return worm

    def alive_worms(self) -> list[WormState]:
        return [worm for worm in self.worms.values() if worm.alive]

    def assign_worm_host(self, worm_id: str, hwnd: int) -> WormState:
        """Attach a worm to any currently inhabitable top-level Win32 client."""
        if self.host_surface_manager is None or self.environment is None:
            raise RuntimeError("Win32 host surfaces are not active")
        self.host_catalog = self.host_surface_manager.refresh()
        surface = self.host_catalog.for_hwnd(int(hwnd))
        if surface is None:
            raise ValueError(f"HWND is not an inhabitable visible surface: {hwnd}")
        worm = self.worms[worm_id]
        bind_worm_to_surface(worm, surface)
        worm.screen_x, worm.screen_y = self.environment.set_host_position(
            worm.worm_id, worm.screen_x, worm.screen_y
        )
        return worm

    def _sync_host_surfaces(self) -> None:
        if self.host_surface_manager is None or self.environment is None:
            return
        self.host_catalog = self.host_surface_manager.refresh()
        for worm in self.alive_worms():
            surface = self.host_catalog.for_id(worm.host_id) or self.host_catalog.for_hwnd(worm.host_hwnd)
            if surface is None:
                surface = self.host_catalog.desktop
                bind_worm_to_surface(worm, surface)
            visible = sync_worm_to_surface(worm, surface)
            worm.metadata["host_visible"] = visible
            if visible:
                worm.screen_x, worm.screen_y = self.environment.set_host_position(
                    worm.worm_id, worm.screen_x, worm.screen_y
                )

    def _host_bounds(self, worm: WormState) -> tuple[int, int, int, int]:
        if self.host_catalog is not None:
            surface = self.host_catalog.for_id(worm.host_id) or self.host_catalog.for_hwnd(worm.host_hwnd)
            if surface is not None:
                return surface.rect
        if self.environment is None:
            return (0, 0, 1, 1)
        return self.environment.workspace_bounds

    def _update_host_after_motion(self, worm: WormState) -> None:
        if self.host_catalog is None or self.environment is None:
            return
        surface = self.host_catalog.for_id(worm.host_id) or self.host_catalog.for_hwnd(worm.host_hwnd)
        if surface is None:
            bind_worm_to_surface(worm, self.host_catalog.desktop)
            surface = self.host_catalog.desktop
        update_worm_local_coordinates(worm, surface)

        # Migration changes the actual inhabited surface, but only after the
        # c302 loop asks to migrate. Merely covering a desktop worm with an app
        # never reassigns it to that app.
        if worm.last_action == WormAction.MIGRATE:
            candidate = self.host_catalog.surface_at(worm.screen_x, worm.screen_y)
            if surface.is_desktop and candidate.host_id != surface.host_id:
                bind_worm_to_surface(worm, candidate)
            elif not surface.is_desktop and worm.body_contact >= 0.5:
                bind_worm_to_surface(worm, self.host_catalog.desktop)

    def update(self, now: float | None = None) -> int:
        if self.worker is None or self.environment is None or self.metabolism is None:
            raise RuntimeError("manager is not set up")
        if self._fatal_error is not None:
            raise RuntimeError(f"c302 brain worker failed: {self._fatal_error}") from self._fatal_error
        current = time.monotonic() if now is None else float(now)
        self._sync_host_surfaces()

        for outcome in self.worker.drain():
            worm = self.worms.get(outcome.worm_id)
            habitat = self._submitted_habitat.pop(outcome.worm_id, None)
            if outcome.error is not None:
                self._fatal_error = outcome.error
                continue
            if worm is None or not worm.alive or outcome.result is None or habitat is None:
                continue
            muscles = self.motor_decoder.decode(
                outcome.result.motor_neuron_scores,
                outcome.result.muscle_scores,
            )
            self._activations[worm.worm_id] = muscles
            worm.metadata["sensory_channels"] = dict(outcome.result.sensory_channels)
            self.metabolism.update(worm, habitat, outcome.result.action)
            self.environment.tick_cooldowns([worm])
            self._brain_ticks += 1
            self._maybe_reproduce(worm)
            if self.cfg.sandbox_enabled:
                self.sandbox.persist(worm)
            if self.transport.has_child(worm.worm_id):
                self.transport.sync_snapshot(worm)
            self._next_brain_at[worm.worm_id] = current + max(0.05, self.cfg.brain_interval_sec)

        # Neural epochs update muscle targets; the body integrates continuously at
        # the manager/render cadence so locomotion does not depend on jNeuroML's
        # wall-clock completion time. Bound catch-up after pauses and split it into
        # stable mechanics steps while still representing intervals above 100 ms.
        if self._last_physics_at is None:
            elapsed = 1.0 / max(15, self.cfg.render_fps)
        else:
            elapsed = max(0.0, min(0.25, current - self._last_physics_at))
        frame_elapsed = elapsed
        self._last_physics_at = current
        while elapsed > 0.0:
            step_dt = min(0.05, elapsed)
            for worm in self.alive_worms():
                if worm.metadata.get("host_visible", True) is False:
                    continue
                activation = self._activations.get(worm.worm_id)
                if activation is None:
                    continue
                self.body.step(worm, activation, step_dt, self._host_bounds(worm))
                if self.host_catalog is not None:
                    worm.screen_x, worm.screen_y = self.environment.set_host_position(
                        worm.worm_id, worm.screen_x, worm.screen_y
                    )
                    self._update_host_after_motion(worm)
                else:
                    worm.screen_x, worm.screen_y = self.environment.set_position(
                        worm.worm_id, worm.screen_x, worm.screen_y
                    )
            elapsed -= step_dt

        if self.ecology is not None:
            living = self.alive_worms()
            # Apply basal cost first, resolve physical food contact, then make
            # the final starvation decision. A touching resource can therefore
            # rescue a worm in the same physical frame.
            self.ecology.tick(frame_elapsed, living, finalize_starvation=False)
            interactable = [
                worm for worm in living
                if worm.metadata.get("host_visible", True) is not False
            ]
            for worm in interactable:
                self.ecology.consume_resources(worm)
            for index, worm in enumerate(interactable):
                for peer in interactable[index + 1 :]:
                    if peer.alive and self.ecology._worms_touch(worm, peer):
                        worm.body_contact = 1.0
                        peer.body_contact = 1.0
            self.ecology.resolve_predation(interactable)
            self.ecology.finalize_starvation(living)
            if self.cfg.sandbox_enabled:
                # Persist every changed participant before dead organisms leave
                # the authoritative in-memory population.
                for worm in living:
                    self.sandbox.persist(worm)

        if self._fatal_error is not None:
            raise RuntimeError(f"c302 brain worker failed: {self._fatal_error}") from self._fatal_error

        for worm in list(self.alive_worms()):
            if current < self._next_brain_at.get(worm.worm_id, 0.0):
                continue
            habitat = self.environment.host_state_at(worm.screen_x, worm.screen_y, worm)
            if self.host_catalog is not None:
                surface = self.host_catalog.for_id(worm.host_id) or self.host_catalog.for_hwnd(worm.host_hwnd)
                if surface is not None:
                    context = dict(habitat.context)
                    context["inhabited_surface"] = {
                        "hwnd": surface.hwnd,
                        "desktop": surface.is_desktop,
                        "visible": surface.visible,
                        "minimized": surface.minimized,
                        "title": surface.title,
                        "class": surface.class_name,
                        "process": surface.process_name,
                    }
                    habitat = habitat.copy(host_id=surface.host_id, context=context)
            if self.ecology is not None:
                ecology_signals = self.ecology.sensory_signals(worm, self.alive_worms())
                signals = dict(habitat.signals)
                signals.update(ecology_signals)
                habitat = habitat.copy(
                    peer_signal=max(habitat.peer_signal, ecology_signals["peer_proximity"]),
                    signals=signals,
                )
                worm.metadata["sensory_channels"] = dict(signals)
            worm.record_observation(habitat)
            if self.worker.submit(worm, habitat):
                self._submitted_habitat[worm.worm_id] = habitat

        for worm_id, worm in list(self.worms.items()):
            if worm.alive:
                continue
            self.environment.unregister(worm_id)
            self._next_brain_at.pop(worm_id, None)
            self._submitted_habitat.pop(worm_id, None)
            self._activations.pop(worm_id, None)
            self.transport.stop_child(worm_id)
            self.worms.pop(worm_id, None)

        self.push_to_overlay()
        return len(self.alive_worms())

    def _maybe_reproduce(self, parent: WormState) -> WormState | None:
        if not self.cfg.reproduction_enabled or self.environment is None:
            return None
        total_population = len(self.alive_worms())
        population_cap = min(self.cfg.reproduction_max_worms, self.cfg.replication_max_living_worms)
        if total_population >= max(1, population_cap):
            return None
        if parent.generation + 1 > self.cfg.replication_max_generations:
            return None
        if parent.energy < self.cfg.reproduction_min_energy or parent.reproduction_cooldown > 0:
            return None
        if self.cfg.process_transport_enabled and not self.transport.can_spawn(parent.generation + 1):
            return None
        original_parent_energy = parent.energy
        original_parent_cooldown = parent.reproduction_cooldown
        child = self.environment.spawn_child(
            parent=parent,
            child_id=self._new_id(),
            offspring_energy=self.cfg.reproduction_offspring_energy,
            parent_energy_cost=self.cfg.reproduction_parent_energy_cost,
            cooldown_ticks=self.cfg.reproduction_cooldown_ticks,
            separation_pixels=self.cfg.reproduction_separation_px,
        )
        if child is None:
            return None
        if self.host_catalog is not None:
            surface = self.host_catalog.for_id(parent.host_id) or self.host_catalog.for_hwnd(parent.host_hwnd)
            if surface is not None:
                # spawn_child preserves physical separation; binding converts it
                # to the same real host's client coordinate system.
                bind_worm_to_surface(child, surface)
                child.screen_x, child.screen_y = self.environment.set_host_position(
                    child.worm_id, child.screen_x, child.screen_y
                )
        if self.cfg.process_transport_enabled:
            try:
                transported = self.transport.spawn(child)
            except Exception:
                self.environment.unregister(child.worm_id)
                parent.energy = original_parent_energy
                parent.reproduction_cooldown = original_parent_cooldown
                raise
            if not transported:
                self.environment.unregister(child.worm_id)
                parent.energy = original_parent_energy
                parent.reproduction_cooldown = original_parent_cooldown
                return None
        # WormManager remains authoritative even when a supervised process is
        # attached. The process mirrors bounded state and never owns lineage,
        # rendering, ecology, or descendant spawning.
        self.worms[child.worm_id] = child
        self._next_brain_at[child.worm_id] = 0.0
        self.body.initialise(child)
        self._activations[child.worm_id] = MuscleActivation(
            dorsal=(0.0,) * self.cfg.physics_segments,
            ventral=(0.0,) * self.cfg.physics_segments,
            forward_drive=0.0,
            reverse_drive=0.0,
        )
        if self.cfg.sandbox_enabled:
            self.sandbox.persist(child)
        if parent.energy <= self.cfg.organism_death_energy:
            parent.alive = False
            parent.metadata.setdefault("death_reason", "reproduction_cost")
        return child

    def push_to_overlay(self) -> None:
        if self.overlay is not None:
            resources = self.ecology.resources if self.ecology is not None else {}
            self.overlay.update_worms(
                {worm.worm_id: worm for worm in self.alive_worms()},
                resources,
            )

    def status(self) -> dict:
        return {
            "alive_worms": len(self.alive_worms()),
            "brain_ticks": self._brain_ticks,
            "pending_brain_jobs": sum(
                1 for worm in self.alive_worms()
                if self.worker is not None and self.worker.pending(worm.worm_id)
            ),
            "process_children": self.transport.child_count,
            "resources": len(self.ecology.resources) if self.ecology is not None else 0,
            "total_energy": round(sum(worm.energy for worm in self.alive_worms()), 6),
            "lineage": {
                worm.worm_id: {"parent_id": worm.parent_id, "generation": worm.generation}
                for worm in self.alive_worms()
            },
            "brain_worker_alive": bool(self.worker and self.worker.is_alive),
        }
