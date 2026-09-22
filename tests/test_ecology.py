from __future__ import annotations

import math
import random
import tempfile
import unittest
from pathlib import Path

from nematode_mind.colony_sandbox import ColonySandbox, ColonySandboxConfig, safe_sandbox_path
from nematode_mind.ecology import DesktopEcology, EcologyConfig, Resource
from nematode_mind.models import HostState, WormAction, WormState
from nematode_mind.organism import MetabolismConfig, WormMetabolism
from nematode_mind.overlay import DesktopOverlay


class EcologyTests(unittest.TestCase):
    def ecology(self, **changes) -> DesktopEcology:
        values = dict(
            enabled=True,
            max_resources=2,
            initial_resources=2,
            regeneration_interval_sec=5.0,
            nutrition_min=0.2,
            nutrition_max=0.2,
            resource_radius_px=3.0,
            sensory_radius_px=100.0,
            basal_energy_use_per_sec=0.1,
            energy_capacity=2.0,
            mass_gain_fraction=0.5,
            predation_enabled=False,
            predation_size_ratio=1.5,
        )
        values.update(changes)
        return DesktopEcology((0, 0, 400, 300), EcologyConfig(**values), random.Random(4))

    def worm(self, worm_id: str = "w1", x: float = 100.0, y: float = 100.0) -> WormState:
        return WormState(
            worm_id,
            "desktop",
            energy=0.5,
            ecology_mass=1.0,
            screen_x=x,
            screen_y=y,
            body_points=[[x, y], [x - 2.0, y]],
        )

    def test_resources_have_explicit_bounded_coordinates_and_regenerate(self) -> None:
        ecology = self.ecology()
        self.assertEqual(len(ecology.resources), 2)
        self.assertTrue(all(0 <= item.x <= 400 and 0 <= item.y <= 300 for item in ecology.resources.values()))
        removed = ecology.resources.pop(next(iter(ecology.resources)))
        self.assertIsInstance(removed, Resource)
        ecology.tick(4.9, [])
        self.assertEqual(len(ecology.resources), 1)
        ecology.tick(0.2, [])
        self.assertEqual(len(ecology.resources), 2)

    def test_consumption_requires_physical_contact_and_transfers_energy_mass(self) -> None:
        ecology = self.ecology(initial_resources=0, max_resources=1)
        worm = self.worm()
        ecology.resources["food"] = Resource("food", 102.0, 100.0, nutrition=0.2, mass=0.1, radius_px=2.0)
        eaten = ecology.consume_resources(worm)
        self.assertEqual([item.resource_id for item in eaten], ["food"])
        self.assertAlmostEqual(worm.energy, 0.7)
        self.assertAlmostEqual(worm.ecology_mass, 1.05)
        self.assertNotIn("food", ecology.resources)

        ecology.resources["far"] = Resource("far", 250.0, 250.0, nutrition=0.2, mass=0.1, radius_px=2.0)
        self.assertEqual(ecology.consume_resources(worm), [])
        self.assertIn("far", ecology.resources)

    def test_basal_energy_use_causes_starvation(self) -> None:
        ecology = self.ecology(initial_resources=0)
        worm = self.worm()
        worm.energy = 0.05
        ecology.tick(1.0, [worm])
        self.assertFalse(worm.alive)
        self.assertEqual(worm.metadata["death_reason"], "starvation")

    def test_food_contact_can_rescue_energy_before_starvation_finalizes(self) -> None:
        ecology = self.ecology(initial_resources=0, max_resources=1)
        worm = self.worm()
        worm.energy = 0.05
        ecology.resources["food"] = Resource("food", 100.0, 100.0, 0.25, 0.1, 2.0)
        ecology.tick(1.0, [worm], finalize_starvation=False)
        self.assertEqual(worm.energy, 0.0)
        self.assertTrue(worm.alive)
        ecology.consume_resources(worm)
        ecology.finalize_starvation([worm])
        self.assertTrue(worm.alive)
        self.assertAlmostEqual(worm.energy, 0.25)

    def test_metabolism_respects_ecology_energy_capacity(self) -> None:
        worm = self.worm()
        worm.energy = 1.8
        metabolism = WormMetabolism(MetabolismConfig(rest_gain=0.0, resource_intake_gain=0.0, energy_capacity=2.0))
        habitat = HostState("desktop", 0.0, 0.0, 0.0)
        metabolism.update(worm, habitat, WormAction.REST)
        self.assertAlmostEqual(worm.energy, 1.8)

    def test_resource_and_peer_proximity_are_bounded_sensory_signals(self) -> None:
        ecology = self.ecology(initial_resources=0)
        worm = self.worm()
        peer = self.worm("w2", 140.0, 100.0)
        ecology.resources["food"] = Resource("food", 120.0, 100.0, 0.2, 0.1, 2.0)
        signals = ecology.sensory_signals(worm, [worm, peer])
        for value in signals.values():
            self.assertTrue(0.0 <= value <= 1.0)
        self.assertGreater(signals["resource_proximity"], 0.0)
        self.assertGreater(signals["peer_proximity"], 0.0)

    def test_predation_is_optional_contact_based_and_requires_size_ratio(self) -> None:
        predator = self.worm("large", 100.0, 100.0)
        predator.ecology_mass = 2.0
        victim = self.worm("small", 101.0, 100.0)
        victim.ecology_mass = 1.0
        victim.energy = 0.4

        disabled = self.ecology(initial_resources=0, predation_enabled=False)
        self.assertEqual(disabled.resolve_predation([predator, victim]), [])
        self.assertTrue(victim.alive)

        enabled = self.ecology(initial_resources=0, predation_enabled=True, predation_size_ratio=1.5)
        events = enabled.resolve_predation([predator, victim])
        self.assertEqual(events, [("large", "small")])
        self.assertFalse(victim.alive)
        self.assertGreater(predator.energy, 0.5)
        self.assertGreater(predator.ecology_mass, 2.0)

        equal_a = self.worm("a", 200.0, 200.0)
        equal_b = self.worm("b", 201.0, 200.0)
        self.assertEqual(enabled.resolve_predation([equal_a, equal_b]), [])
        self.assertTrue(equal_a.alive and equal_b.alive)

    def test_resource_generation_stays_inside_tiny_bounds(self) -> None:
        ecology = DesktopEcology(
            (0, 0, 1, 1),
            EcologyConfig(initial_resources=1, max_resources=1, resource_radius_px=2.0),
            random.Random(7),
        )
        item = next(iter(ecology.resources.values()))
        self.assertTrue(0.0 <= item.x <= 1.0)
        self.assertTrue(0.0 <= item.y <= 1.0)

    def test_predation_never_allows_a_dead_worm_to_consume_again(self) -> None:
        ecology = self.ecology(initial_resources=0, predation_enabled=True, predation_size_ratio=1.5)
        big = self.worm("big", 100.0, 100.0)
        small = self.worm("small", 100.0, 100.0)
        tiny = self.worm("tiny", 100.0, 100.0)
        big.ecology_mass, small.ecology_mass, tiny.ecology_mass = 4.0, 2.0, 1.0
        events = ecology.resolve_predation([small, big, tiny])
        self.assertEqual(events, [("big", "small"), ("big", "tiny")])
        self.assertFalse(small.alive)
        self.assertFalse(tiny.alive)
        self.assertNotIn(("small", "tiny"), events)

    def test_debug_and_arcade_growth_are_render_only(self) -> None:
        physical = [(100.0, 100.0), (96.0, 100.0)]
        overlay = DesktopOverlay(debug_enabled=True, debug_scale=20, arcade_growth=True, max_visual_growth=2.0)
        overlay._virtual_left = 0
        worm = self.worm()
        worm.ecology_mass = 128.0
        rendered = overlay._canvas_points(physical, visual_growth=overlay._visual_growth(worm))
        self.assertEqual(physical, [(100.0, 100.0), (96.0, 100.0)])
        self.assertEqual(rendered, [(100.0, 100.0), (-60.0, 100.0)])


class ColonySandboxTests(unittest.TestCase):
    def test_existing_unmarked_directory_is_never_adopted_or_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Documents"
            root.mkdir()
            user_file = root / "user.txt"
            user_file.write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                ColonySandbox(ColonySandboxConfig(enabled=True, root=str(root)))
            self.assertEqual(user_file.read_text(encoding="utf-8"), "keep")
            self.assertFalse((root / ColonySandbox.MARKER).exists())

    def test_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "WormHabitat"
            with self.assertRaises(ValueError):
                safe_sandbox_path(root, "..", "outside.json")
            with self.assertRaises(ValueError):
                safe_sandbox_path(root, "C:/outside.json")

    def test_state_limits_population_generation_size_disk_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "WormHabitat"
            sandbox = ColonySandbox(ColonySandboxConfig(
                enabled=True,
                root=str(root),
                max_living_worms=2,
                max_generations=1,
                max_disk_bytes=4096,
                max_state_bytes=2048,
                cleanup_timeout_sec=2.0,
                per_worm_directories=True,
            ))
            parent = WormState("w1", "desktop", generation=0)
            child = WormState("w2", "desktop", parent_id="w1", generation=1)
            too_deep = WormState("w3", "desktop", parent_id="w2", generation=2)
            self.assertTrue(sandbox.can_admit(parent, living_count=0))
            self.assertTrue(sandbox.can_admit(child, living_count=1))
            self.assertFalse(sandbox.can_admit(too_deep, living_count=1))
            self.assertFalse(sandbox.can_admit(child, living_count=2))
            path = sandbox.persist(parent)
            self.assertTrue(path.is_file())
            self.assertTrue(str(path.resolve()).startswith(str(root.resolve())))
            self.assertTrue(sandbox.cleanup())
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
