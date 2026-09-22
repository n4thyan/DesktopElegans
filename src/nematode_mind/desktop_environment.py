from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass
from typing import Dict, Iterable, Tuple

from .models import HostState, WormAction, WormState


@dataclass
class DesktopRegion:
    """One synthetic habitat tile in desktop coordinates.

    Tiles never inspect files, windows, processes, network state, or user data.
    They give the c302 sensory bridge stable spatial variation while the worms
    move over the visible desktop.
    """

    region_id: str
    left: int
    top: int
    right: int
    bottom: int
    cpu_free: float = 0.5
    memory_free: float = 0.5
    network_quality: float = 0.5
    blocked: bool = False
    peer_signal: float = 0.0
    novelty: float = 0.2

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def as_host_state(self) -> HostState:
        return HostState(
            host_id=self.region_id,
            cpu_free=self.cpu_free,
            memory_free=self.memory_free,
            network_quality=self.network_quality,
            blocked=self.blocked,
            peer_signal=self.peer_signal,
            novelty=self.novelty,
        ).clamp()


class DesktopEnvironment:
    """A local habitat in Windows virtual-desktop coordinates."""

    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        screen_left: int = 0,
        screen_top: int = 0,
        region_tile_pixels: int = 200,
        workspace_factor: float = 0.85,
        rng: random.Random | None = None,
        observer=None,
    ):
        self.screen_width = max(1, int(screen_width))
        self.screen_height = max(1, int(screen_height))
        self.screen_left = int(screen_left)
        self.screen_top = int(screen_top)
        self.region_tile = max(50, int(region_tile_pixels))
        self.workspace_factor = max(0.5, min(1.0, float(workspace_factor)))
        self.rng = rng or random.Random()
        self.observer = observer
        if self.observer is None and os.name == "nt":
            try:
                from .windows_observer import WindowsDesktopObserver

                self.observer = WindowsDesktopObserver()
            except Exception:
                self.observer = None
        self.regions: Dict[str, DesktopRegion] = {}
        self._worm_positions: Dict[str, Tuple[float, float]] = {}
        self._previous_positions: Dict[str, Tuple[float, float]] = {}
        self._context_signatures: Dict[str, tuple] = {}
        self._workspace_bounds = (
            self.screen_left,
            self.screen_top,
            self.screen_left + self.screen_width,
            self.screen_top + self.screen_height,
        )
        self._rebuild_region_grid()

    @property
    def workspace_bounds(self) -> Tuple[int, int, int, int]:
        return self._workspace_bounds

    @property
    def screen_bounds(self) -> Tuple[int, int, int, int]:
        return (
            self.screen_left,
            self.screen_top,
            self.screen_left + self.screen_width,
            self.screen_top + self.screen_height,
        )

    def _rebuild_region_grid(self) -> None:
        self.regions.clear()
        width = max(1, int(self.screen_width * self.workspace_factor))
        height = max(1, int(self.screen_height * self.workspace_factor))
        left0 = self.screen_left + (self.screen_width - width) // 2
        top0 = self.screen_top + (self.screen_height - height) // 2
        right_edge = left0 + width
        bottom_edge = top0 + height
        self._workspace_bounds = (left0, top0, right_edge, bottom_edge)

        rows = max(1, math.ceil(height / self.region_tile))
        cols = max(1, math.ceil(width / self.region_tile))
        for row in range(rows):
            for col in range(cols):
                left = left0 + col * self.region_tile
                top = top0 + row * self.region_tile
                right = min(left + self.region_tile, right_edge)
                bottom = min(top + self.region_tile, bottom_edge)
                region_id = f"r{row:03d}c{col:03d}"
                wave = (math.sin(row * 1.7 + col * 0.9) + 1.0) * 0.5
                self.regions[region_id] = DesktopRegion(
                    region_id=region_id,
                    left=left,
                    top=top,
                    right=right,
                    bottom=bottom,
                    cpu_free=0.35 + 0.45 * wave,
                    memory_free=0.75 - 0.35 * wave,
                    network_quality=0.45 + 0.30 * (1.0 - wave),
                    novelty=0.2,
                )

    def region_at(self, x: float, y: float) -> DesktopRegion | None:
        for region in self.regions.values():
            if region.contains(x, y):
                return region
        return None

    def register_position(self, worm_id: str, x: float, y: float) -> None:
        position = self._clamp_position(x, y)
        self._worm_positions[worm_id] = position
        self._previous_positions[worm_id] = position

    def unregister(self, worm_id: str) -> None:
        self._worm_positions.pop(worm_id, None)
        self._previous_positions.pop(worm_id, None)
        self._context_signatures.pop(worm_id, None)

    def position(self, worm_id: str) -> Tuple[float, float]:
        return self._worm_positions.get(worm_id, self.workspace_center())

    def workspace_center(self) -> Tuple[float, float]:
        left, top, right, bottom = self._workspace_bounds
        return ((left + right) * 0.5, (top + bottom) * 0.5)

    def _clamp_position(self, x: float, y: float) -> Tuple[float, float]:
        left, top, right, bottom = self._workspace_bounds
        return (
            max(float(left), min(float(right - 1), float(x))),
            max(float(top), min(float(bottom - 1), float(y))),
        )

    def move_worm(self, worm_id: str, dx: float, dy: float) -> Tuple[float, float]:
        old = self.position(worm_id)
        self._previous_positions[worm_id] = old
        new = self._clamp_position(old[0] + dx, old[1] + dy)
        self._worm_positions[worm_id] = new
        return new

    def set_position(self, worm_id: str, x: float, y: float) -> Tuple[float, float]:
        """Synchronise an externally integrated body head with habitat space."""
        old = self.position(worm_id)
        self._previous_positions[worm_id] = old
        new = self._clamp_position(x, y)
        self._worm_positions[worm_id] = new
        return new

    def set_host_position(self, worm_id: str, x: float, y: float) -> Tuple[float, float]:
        """Synchronise a host-bound body across the complete virtual desktop."""
        old = self.position(worm_id)
        self._previous_positions[worm_id] = old
        left, top, right, bottom = self.screen_bounds
        new = (
            max(float(left), min(float(right - 1), float(x))),
            max(float(top), min(float(bottom - 1), float(y))),
        )
        self._worm_positions[worm_id] = new
        return new

    def host_state_at(self, x: float, y: float, worm: WormState | None = None) -> HostState:
        region = self.region_at(x, y)
        if region is None:
            state = HostState(
                host_id="desktop-edge",
                cpu_free=0.2,
                memory_free=0.2,
                network_quality=0.2,
                blocked=True,
                novelty=0.1,
            )
        else:
            state = region.as_host_state()
        if worm is not None:
            visits = worm.visited_hosts.get(state.host_id, 0)
            state = state.copy(novelty=max(state.novelty, 1.0 / (1.0 + visits)))
        if self.observer is not None:
            try:
                observation = self.observer.observe(x, y)
                signals = dict(observation.signals)
                metadata = dict(observation.metadata)
                if worm is not None:
                    signature = (
                        metadata.get("monitor_index"),
                        metadata.get("surface_kind"),
                        metadata.get("surface_hwnd"),
                        metadata.get("foreground_hwnd"),
                    )
                    previous = self._context_signatures.get(worm.worm_id)
                    signals["context_novelty"] = 1.0 if previous is not None and previous != signature else 0.0
                    self._context_signatures[worm.worm_id] = signature
                state = state.copy(signals=signals, context=metadata)
                if worm is not None:
                    worm.metadata["desktop_context"] = metadata
                    worm.metadata["desktop_signals"] = signals
            except Exception as exc:
                # Desktop observation must never make the overlay or brain loop
                # block/fail. Keep the synthetic habitat available and expose a
                # bounded diagnostic rather than touching underlying windows.
                if worm is not None:
                    worm.metadata["desktop_observation_error"] = type(exc).__name__
        return state

    def apply_motion(self, worm: WormState, action: WormAction) -> dict:
        before = self.position(worm.worm_id)
        step = 0.0
        angle = worm.facing_radians

        if action == WormAction.MIGRATE:
            step = self.region_tile * 0.40
            angle += self.rng.uniform(-0.65, 0.65)
        elif action == WormAction.EXPLORE:
            step = self.region_tile * 0.12
            angle += self.rng.uniform(-1.2, 1.2)
        elif action == WormAction.RETREAT:
            step = self.region_tile * 0.20
            previous = self._previous_positions.get(worm.worm_id, before)
            dx, dy = previous[0] - before[0], previous[1] - before[1]
            angle = math.atan2(dy, dx) if math.hypot(dx, dy) > 0.01 else angle + math.pi

        if step > 0.0:
            after = self.move_worm(worm.worm_id, math.cos(angle) * step, math.sin(angle) * step)
            actual_dx, actual_dy = after[0] - before[0], after[1] - before[1]
            if math.hypot(actual_dx, actual_dy) > 0.01:
                worm.facing_radians = math.atan2(actual_dy, actual_dx)
        else:
            after = before

        worm.screen_x, worm.screen_y = after
        return {"from": before, "to": after, "step": math.dist(before, after), "action": action.value}

    def spawn_child(
        self,
        parent: WormState,
        child_id: str,
        offspring_energy: float,
        parent_energy_cost: float,
        cooldown_ticks: int,
        separation_pixels: float,
    ) -> WormState | None:
        if not parent.alive or parent.reproduction_cooldown > 0:
            return None
        angle = self.rng.uniform(0.0, math.tau)
        child_x, child_y = self._clamp_position(
            parent.screen_x + math.cos(angle) * separation_pixels,
            parent.screen_y + math.sin(angle) * separation_pixels,
        )
        child = WormState(
            worm_id=child_id,
            host_id=parent.host_id,
            previous_host_id=parent.host_id,
            energy=max(0.01, min(1.0, float(offspring_energy))),
            parent_id=parent.worm_id,
            generation=parent.generation + 1,
            screen_x=child_x,
            screen_y=child_y,
            facing_radians=angle,
            body_phase=self.rng.uniform(0.0, math.tau),
            reproduction_cooldown=max(0, int(cooldown_ticks)),
        )
        parent.energy = max(0.0, parent.energy - max(0.0, parent_energy_cost))
        parent.reproduction_cooldown = max(0, int(cooldown_ticks))
        self.register_position(child.worm_id, child_x, child_y)
        return child

    @staticmethod
    def tick_cooldowns(worms: Iterable[WormState]) -> None:
        for worm in worms:
            worm.reproduction_cooldown = max(0, worm.reproduction_cooldown - 1)

    def snapshot(self) -> dict:
        return {
            "screen_width": self.screen_width,
            "screen_height": self.screen_height,
            "screen_left": self.screen_left,
            "screen_top": self.screen_top,
            "workspace_bounds": self.workspace_bounds,
            "region_count": len(self.regions),
            "worm_count": len(self._worm_positions),
        }


def desktop_geometry() -> Tuple[int, int, int, int]:
    """Return Windows virtual-screen ``(left, top, width, height)``."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        return (
            int(user32.GetSystemMetrics(76)),
            int(user32.GetSystemMetrics(77)),
            max(1, int(user32.GetSystemMetrics(78))),
            max(1, int(user32.GetSystemMetrics(79))),
        )
    except Exception:
        return 0, 0, 1920, 1080


def desktop_dimensions() -> Tuple[int, int]:
    """Return virtual-desktop dimensions for compatibility."""
    _, _, width, height = desktop_geometry()
    return width, height


def desktop_dpi() -> float:
    """Return effective system DPI using the same awareness as the overlay."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        try:
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass
        get_dpi = getattr(user32, "GetDpiForSystem", None)
        dpi = float(get_dpi()) if get_dpi else 96.0
        return dpi if 50.0 <= dpi <= 600.0 else 96.0
    except Exception:
        return 96.0
