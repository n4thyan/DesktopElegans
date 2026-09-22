from __future__ import annotations

import math
import os
import random
import time
import unittest

from nematode_mind.desktop_environment import DesktopEnvironment
from nematode_mind.models import BrainResult, HostState, WormAction, WormState
from nematode_mind.ecology import Resource
from nematode_mind.overlay import DesktopOverlay
from nematode_mind.renderer import WormRenderer, worm_segments
from nematode_mind.worm_manager import WormManager, WormManagerConfig
from nematode_mind.windows_observer import WindowsDesktopObserver


class SlowRestBrain:
    def step(self, environment: HostState, organism: WormState | None = None) -> BrainResult:
        time.sleep(0.06)
        return BrainResult(
            stimulated_neurons=[],
            neuron_scores={},
            forward_score=0.0,
            reverse_score=0.0,
            action=WormAction.REST,
        )


class DesktopTests(unittest.TestCase):
    def test_renderer_produces_one_mm_geometry(self) -> None:
        points = worm_segments(100, 100, 0.0, 0.0, 96.0 / 25.4)
        self.assertEqual(len(points), 13)
        self.assertEqual(points[0], (100.0, 100.0))
        renderer = WormRenderer(body_length_mm=1.0, dpi=96.0)
        self.assertTrue(math.isclose(renderer.body_length_px, 96.0 / 25.4, rel_tol=1e-6))

    def test_environment_is_bounded_and_edge_is_blocked(self) -> None:
        environment = DesktopEnvironment(800, 600, rng=random.Random(1))
        environment.register_position("w", -500, 900)
        x, y = environment.position("w")
        left, top, right, bottom = environment.workspace_bounds
        self.assertTrue(left <= x < right)
        self.assertTrue(top <= y < bottom)
        self.assertTrue(environment.host_state_at(-1, -1).blocked)

    def test_virtual_desktop_negative_origin_matches_overlay_canvas(self) -> None:
        environment = DesktopEnvironment(
            3840,
            1080,
            screen_left=-1920,
            screen_top=0,
            workspace_factor=1.0,
            rng=random.Random(1),
        )
        self.assertEqual(environment.workspace_bounds, (-1920, 0, 1920, 1080))
        environment.register_position("left", -1500, 500)
        self.assertEqual(environment.position("left"), (-1500.0, 500.0))

        overlay = DesktopOverlay(debug_scale=10)
        overlay._virtual_left = -1920
        points = overlay._canvas_points([(-1500.0, 500.0), (-1504.0, 500.0)])
        self.assertEqual(points, [(420.0, 500.0), (380.0, 500.0)])

    def test_normal_overlay_scale_preserves_biological_geometry(self) -> None:
        overlay = DesktopOverlay(debug_scale=1)
        overlay._virtual_left = -1920
        points = overlay._canvas_points([(100.0, 100.0), (96.0, 100.0)])
        self.assertEqual(points, [(2020.0, 100.0), (2016.0, 100.0)])

    def test_multiple_initial_worms_are_distributed_across_virtual_workspace(self) -> None:
        manager = WormManager(
            WormManagerConfig(initial_worms=3, resources_initial_count=0),
            rng=random.Random(3),
        )
        manager.setup(
            brain=SlowRestBrain(), dimensions=(3840, 1080), origin=(-1920, 0)
        )
        positions = [(worm.screen_x, worm.screen_y) for worm in manager.worms.values()]
        self.assertEqual(len(positions), 3)
        self.assertEqual(len(set(positions)), 3)
        self.assertLess(positions[0][0], -500)
        self.assertLess(abs(positions[1][0]), 100)
        self.assertGreater(positions[2][0], 500)

    @unittest.skipUnless(os.name == "nt", "Windows metadata integration")
    def test_windows_observer_returns_bounded_generic_context(self) -> None:
        observation = WindowsDesktopObserver().observe(-960, 540)
        for value in observation.signals.values():
            self.assertTrue(0.0 <= value <= 1.0)
        self.assertGreaterEqual(observation.metadata["monitor_count"], 1)
        self.assertIn(observation.metadata["surface_kind"], {"desktop", "window"})
        self.assertIn("surface_class", observation.metadata)
        self.assertIn("surface_process", observation.metadata)

    def test_retreat_moves_toward_previous_position(self) -> None:
        environment = DesktopEnvironment(800, 600, rng=random.Random(4))
        worm = WormState("w", "desktop", screen_x=400, screen_y=300, facing_radians=0.0)
        environment.register_position(worm.worm_id, worm.screen_x, worm.screen_y)
        origin = environment.position(worm.worm_id)
        environment.apply_motion(worm, WormAction.EXPLORE)
        explored = environment.position(worm.worm_id)
        environment.apply_motion(worm, WormAction.RETREAT)
        retreated = environment.position(worm.worm_id)
        self.assertLess(math.dist(retreated, origin), math.dist(explored, origin))

    def test_food_contact_enables_bounded_reproduction_and_lineage(self) -> None:
        cfg = WormManagerConfig(
            initial_worms=1,
            organism_initial_energy=0.6,
            ecology_basal_energy_use_per_sec=0.0,
            resources_initial_count=0,
            resources_max_count=1,
            reproduction_enabled=True,
            reproduction_max_worms=2,
            reproduction_min_energy=0.9,
            reproduction_offspring_energy=0.2,
            reproduction_parent_energy_cost=0.3,
            reproduction_cooldown_ticks=0,
            replication_max_living_worms=2,
            replication_max_generations=1,
        )
        manager = WormManager(cfg, rng=random.Random(8))
        manager.setup(brain=SlowRestBrain(), dimensions=(800, 600))
        parent = next(iter(manager.worms.values()))
        parent.body_points = [[parent.screen_x, parent.screen_y], [parent.screen_x - 2.0, parent.screen_y]]
        manager.ecology.resources["demo-food"] = Resource(
            "demo-food", parent.screen_x, parent.screen_y, 0.4, 0.2, 2.0
        )
        before = parent.energy
        manager._next_brain_at[parent.worm_id] = float("inf")
        manager.update(now=10.0)
        self.assertGreater(parent.energy, before)
        self.assertNotIn("demo-food", manager.ecology.resources)

        child = manager._maybe_reproduce(parent)
        self.assertIsNotNone(child)
        self.assertEqual(child.parent_id, parent.worm_id)
        self.assertEqual(child.generation, 1)
        self.assertEqual(len(manager.alive_worms()), 2)
        self.assertIsNone(manager._maybe_reproduce(parent))
        self.assertIsNone(manager._maybe_reproduce(child))

    def test_brain_runs_off_thread_and_reproduction_stays_in_process(self) -> None:
        cfg = WormManagerConfig(
            initial_worms=1,
            brain_interval_sec=0.01,
            reproduction_enabled=True,
            reproduction_max_worms=2,
            reproduction_min_energy=0.9,
            reproduction_offspring_energy=0.2,
            reproduction_parent_energy_cost=0.25,
            reproduction_cooldown_ticks=5,
        )
        manager = WormManager(cfg, rng=random.Random(2))
        manager.setup(brain=SlowRestBrain(), dimensions=(800, 600))
        manager.start()
        try:
            started = time.monotonic()
            manager.update()
            self.assertLess(time.monotonic() - started, 0.04)

            deadline = time.monotonic() + 2.0
            while manager.status()["brain_ticks"] < 1 and time.monotonic() < deadline:
                manager.update()
                time.sleep(0.01)
            self.assertGreaterEqual(manager.status()["brain_ticks"], 1)
            self.assertEqual(len(manager.worms), 2)
            child = max(manager.worms.values(), key=lambda worm: worm.generation)
            self.assertEqual(child.generation, 1)
            self.assertEqual(child.parent_id, "w0001")
        finally:
            manager.stop()


if __name__ == "__main__":
    unittest.main()
