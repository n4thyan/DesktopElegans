from __future__ import annotations

import math
import random
import sys
import threading
import time
import unittest

from nematode_mind.models import BrainResult, HostState, WormAction, WormState
from nematode_mind.__main__ import manager_config
from nematode_mind.brain_c302 import C302Brain
from nematode_mind.brain_worker import BrainWorker
from nematode_mind.config import validate_config
from nematode_mind.neuromechanics import (
    BodyPhysicsConfig,
    FlexibleBody,
    MOTOR_NEURONS,
    MUSCLE_NAMES,
    MotorToMuscleDecoder,
    MuscleActivation,
)
from nematode_mind.process_transport import LocalProcessTransport, ProcessTransportConfig
from nematode_mind.renderer import WormRenderer
from nematode_mind.sensors import DigitalSensoryMapper
from nematode_mind.worm_manager import WormManager, WormManagerConfig


class RecordingMotorBrain:
    def __init__(self) -> None:
        self.proprioceptive_inputs: list[float] = []

    def step(self, environment: HostState, organism: WormState | None = None) -> BrainResult:
        self.proprioceptive_inputs.append(organism.proprioception if organism else 0.0)
        motor = {f"DB{i}": 0.95 for i in range(1, 8)}
        motor.update({f"VB{i}": 0.1 if i < 6 else 0.75 for i in range(1, 12)})
        return BrainResult([], {}, 0.9, 0.1, WormAction.MIGRATE, motor_neuron_scores=motor)


class BlockingBrain:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def step(self, environment: HostState, organism: WormState | None = None) -> BrainResult:
        self.entered.set()
        self.release.wait(5.0)
        return BrainResult([], {}, 0.0, 0.0, WormAction.REST)


class NeuromechanicsTests(unittest.TestCase):
    def test_c302_brain_keeps_canonical_package_across_multiple_epochs(self) -> None:
        brain = C302Brain(parameter_set="A")
        canonical, _pynml, _writers = brain._imports()
        original = sys.modules.get("c302")
        sys.modules["c302"] = object()  # type: ignore[assignment]
        try:
            reused, _pynml, _writers = brain._imports()
        finally:
            if original is None:
                sys.modules.pop("c302", None)
            else:
                sys.modules["c302"] = original
        self.assertIs(reused, canonical)
        self.assertTrue(callable(getattr(reused, "generate", None)))

    def test_worker_stop_retains_live_thread_and_clears_pending(self) -> None:
        brain = BlockingBrain()
        worker = BrainWorker(brain)
        worm = WormState("w", "desktop")
        worker.start()
        self.assertTrue(worker.submit(worm, HostState("tile", 0.5, 0.5, 0.5)))
        self.assertTrue(brain.entered.wait(1.0))
        self.assertFalse(worker.stop(timeout=0.01))
        self.assertTrue(worker.is_alive)
        self.assertFalse(worker.pending(worm.worm_id))
        brain.release.set()
        deadline = time.monotonic() + 1.0
        while worker.is_alive and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(worker.stop(timeout=1.0))
        self.assertFalse(worker.is_alive)

    def test_config_rejects_ambiguous_and_inconsistent_values(self) -> None:
        invalid = (
            {"habitat": {"nodes": []}},
            {"reproduction": {"enabled": "false"}},
            {"transport": {"process_enabled": "false"}},
            {"organism": {"death_energy": -1}},
            {"desktop": {"initial_worms": 3}, "reproduction": {"max_worms": 2}},
            {"brain": {"duration_ms": float("nan")}},
            {"physics": {"muscle_gain": float("inf")}},
            {"transport": {"startup_timeout_sec": float("nan")}},
            {"visual_debug": {"enabled": "false"}},
            {"visual_debug": {"scale": 0}},
            {"ecology": {"predation_enabled": "false"}},
            {"ecology": {"predation_size_ratio": 1.0}},
            {"resources": {"max_count": 2, "initial_count": 3}},
            {"resources": {"nutrition_min": 0.5, "nutrition_max": 0.1}},
            {"replication": {"sandbox_enabled": "false"}},
            {"replication": {"max_living_worms": 0}},
            {"replication": {"max_sandbox_disk_mb": float("inf")}},
            {"replication": {"process_enabled": False}, "transport": {"process_enabled": True}},
        )
        for cfg in invalid:
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                validate_config(cfg)

    def test_physics_body_length_uses_effective_desktop_dpi(self) -> None:
        manager = WormManager(WormManagerConfig(body_length_mm=1.0, desktop_dpi=144.0))
        self.assertTrue(math.isclose(manager.body.config.length_px, 144.0 / 25.4, rel_tol=1e-6))

    def test_desktop_manager_receives_full_brain_interface_config(self) -> None:
        cfg = manager_config({
            "brain": {
                "baseline_gain": 3.0,
                "stimulus": {"min_pa": 2.0, "max_pa": 6.0, "delay_ms": 12.0, "duration_ms": 50.0},
                "sensory": {
                    "threshold": 0.3,
                    "quantum": 0.05,
                    "hunger_gain": 0.4,
                    "stress_gain": 0.6,
                    "proprioception_gain": 25.0,
                },
            },
            "visual_debug": {"enabled": True, "scale": 20, "show_coordinates": False},
            "ecology": {"predation_enabled": True, "max_visual_growth": 1.5},
            "resources": {"max_count": 7, "initial_count": 3},
            "replication": {
                "max_living_worms": 5,
                "max_generations": 2,
                "max_child_processes": 1,
                "process_enabled": True,
                "sandbox_enabled": False,
            },
        })
        self.assertEqual(cfg.brain_baseline_gain, 3.0)
        self.assertEqual(cfg.brain_stimulus_duration_ms, 50.0)
        self.assertEqual(cfg.brain_sensory_quantum, 0.05)
        self.assertEqual(cfg.brain_proprioception_gain, 25.0)
        self.assertTrue(cfg.visual_debug_enabled)
        self.assertEqual(cfg.visual_debug_scale, 20)
        self.assertFalse(cfg.visual_debug_show_coordinates)
        self.assertTrue(cfg.ecology_predation_enabled)
        self.assertEqual(cfg.resources_max_count, 7)
        self.assertEqual(cfg.replication_max_living_worms, 5)
        self.assertEqual(cfg.process_transport_max_children, 1)

    def test_manager_closes_motor_body_feedback_loop(self) -> None:
        brain = RecordingMotorBrain()
        manager = WormManager(WormManagerConfig(
            initial_worms=1,
            brain_interval_sec=0.05,
            reproduction_enabled=False,
        ), rng=random.Random(3))
        manager.setup(brain=brain, dimensions=(800, 600))
        worm = next(iter(manager.worms.values()))
        starting_head = (worm.screen_x, worm.screen_y)
        manager.start()
        try:
            deadline = time.monotonic() + 3.0
            while manager.status()["brain_ticks"] < 2 and time.monotonic() < deadline:
                manager.update()
                time.sleep(0.01)
            self.assertGreaterEqual(manager.status()["brain_ticks"], 2)
            self.assertNotEqual(starting_head, (worm.screen_x, worm.screen_y))
            self.assertTrue(any(value > 0 for value in worm.muscle_dorsal))
            self.assertGreater(worm.proprioception, 0.0)
            self.assertGreater(brain.proprioceptive_inputs[-1], 0.0)
        finally:
            manager.stop()

    def test_named_motor_neurons_drive_segmented_muscles_and_body(self) -> None:
        decoder = MotorToMuscleDecoder(segments=13)
        scores = {f"DB{i}": 0.9 for i in range(1, 8)}
        scores.update({f"VB{i}": 0.55 if i % 2 else 0.15 for i in range(1, 12)})
        activation = decoder.decode(scores)
        self.assertEqual(len(activation.dorsal), 13)
        self.assertEqual(len(activation.ventral), 13)
        self.assertGreater(activation.forward_drive, activation.reverse_drive)

        c302_muscles = {f"MDL{i:02d}": 0.8 for i in range(1, 25)}
        c302_muscles.update({f"MDR{i:02d}": 0.6 for i in range(1, 25)})
        c302_muscles.update({f"MVL{i:02d}": 0.2 for i in range(1, 25)})
        c302_muscles.update({f"MVR{i:02d}": 0.2 for i in range(1, 25)})
        activation = decoder.decode(scores, c302_muscles)
        self.assertTrue(all(dorsal > ventral for dorsal, ventral in zip(activation.dorsal, activation.ventral)))

        worm = WormState("w", "desktop", screen_x=100.0, screen_y=100.0)
        body = FlexibleBody(BodyPhysicsConfig(segments=13, length_px=24.0))
        body.initialise(worm)
        before = [tuple(point) for point in worm.body_points]
        result = body.step(worm, activation, dt=0.1, bounds=(0, 0, 400, 300))
        self.assertNotEqual(before, [tuple(point) for point in worm.body_points])
        self.assertEqual(worm.muscle_dorsal, list(activation.dorsal))
        self.assertTrue(0.0 <= worm.proprioception <= 1.0)
        self.assertGreater(result["forward_drive"], result["reverse_drive"])

    def test_c302_rejects_partial_motor_and_muscle_trace_sets(self) -> None:
        brain = C302Brain(parameter_set="A")
        motor_results = {
            brain._result_key(name): [0.0, 0.01]
            for name in MOTOR_NEURONS[:-1]
        }
        with self.assertRaisesRegex(RuntimeError, "Incomplete locomotor"):
            brain._extract_motor_scores(motor_results)

        muscle_results = {
            f"{name}/0/generic_muscle_iaf_cell/v": [0.0, 0.01]
            for name in MUSCLE_NAMES[:-1]
        }
        with self.assertRaisesRegex(RuntimeError, "Incomplete body-wall"):
            brain._extract_muscle_scores(muscle_results)

    def test_body_continues_between_neural_results(self) -> None:
        manager = WormManager(WormManagerConfig(
            initial_worms=1,
            reproduction_enabled=False,
            brain_interval_sec=10.0,
        ), rng=random.Random(13))
        manager.setup(brain=RecordingMotorBrain(), dimensions=(640, 480))
        worm = next(iter(manager.worms.values()))
        manager._activations[worm.worm_id] = MuscleActivation(
            dorsal=(0.9,) * manager.cfg.physics_segments,
            ventral=(0.1,) * manager.cfg.physics_segments,
            forward_drive=0.9,
            reverse_drive=0.0,
        )
        manager._next_brain_at[worm.worm_id] = float("inf")
        manager.update(now=100.0)
        after_first_frame = [point[:] for point in worm.body_points]
        manager.update(now=100.2)
        self.assertNotEqual(after_first_frame, worm.body_points)

    def test_physics_geometry_replaces_renderer_only_sine_animation(self) -> None:
        worm = WormState("w", "desktop", body_points=[[10.0, 20.0], [9.0, 21.0], [8.0, 21.5]])
        self.assertEqual(WormRenderer().geometry(worm), [(10.0, 20.0), (9.0, 21.0), (8.0, 21.5)])

    def test_body_feedback_is_mapped_back_to_c302_sensory_neurons(self) -> None:
        worm = WormState("w", "desktop", proprioception=0.8, body_contact=1.0)
        habitat = HostState("tile", 0.5, 0.5, 0.5)
        frame = DigitalSensoryMapper(threshold=0.2, strength_quantum=0.1).sense(habitat, worm)
        self.assertIn("DVA", frame.stimulated_neurons)
        self.assertIn("ALML", frame.stimulated_neurons)
        self.assertIn("PLMR", frame.stimulated_neurons)
        self.assertTrue(math.isclose(frame.channels["proprioception"], 0.8))
        self.assertEqual(frame.channels["body_contact"], 1.0)

        # The configured desktop gain makes ordinary small curvatures cross the
        # c302 stimulus threshold while preserving the raw measurement.
        worm.proprioception = 0.01
        amplified = DigitalSensoryMapper(
            threshold=0.2,
            strength_quantum=0.1,
            proprioception_gain=20.0,
        ).sense(habitat, worm)
        self.assertIn("DVA", amplified.stimulated_neurons)
        self.assertEqual(amplified.channels["proprioception_raw"], 0.01)
        self.assertEqual(amplified.channels["proprioception"], 0.2)

    def test_live_desktop_signals_are_bounded_and_reach_existing_sensory_paths(self) -> None:
        state = HostState(
            "desktop",
            0.5,
            0.5,
            1.0,
            novelty=0.0,
            signals={
                "cursor_proximity": 0.8,
                "window_presence": 1.0,
                "foreground_overlap": 1.0,
                "monitor_position": 0.0,
            },
            context={"surface_kind": "window", "surface_class": "generic"},
        )
        frame = DigitalSensoryMapper(threshold=0.2, strength_quantum=0.1).sense(state)
        self.assertEqual(frame.channels["cursor_proximity"], 0.8)
        self.assertEqual(frame.channels["window_presence"], 1.0)
        self.assertIn("ASHL", frame.stimulated_neurons)
        self.assertIn("AWCL", frame.stimulated_neurons)

    def test_process_transport_is_bounded_local_and_round_trips_state(self) -> None:
        transport = LocalProcessTransport(ProcessTransportConfig(
            enabled=True,
            max_children=1,
            max_generation=1,
            startup_timeout_sec=5.0,
            shutdown_timeout_sec=2.0,
        ))
        child = WormState("child-1", "desktop", parent_id="parent", generation=1, energy=0.4)
        too_many = WormState("child-2", "desktop", parent_id="parent", generation=1)
        too_deep = WormState("child-3", "desktop", parent_id="child-1", generation=2)
        try:
            self.assertTrue(transport.spawn(child))
            self.assertEqual(transport.child_count, 1)
            child.energy = 0.73
            transport.sync_snapshot(child)
            snapshot = transport.snapshot(child.worm_id)
            self.assertEqual(snapshot.worm_id, child.worm_id)
            self.assertEqual(snapshot.parent_id, "parent")
            self.assertAlmostEqual(snapshot.energy, 0.73)
            self.assertFalse(transport.spawn(too_many))
            self.assertFalse(transport.spawn(too_deep))
        finally:
            transport.stop()
        self.assertEqual(transport.child_count, 0)

    def test_process_transport_startup_send_failure_is_reaped(self) -> None:
        transport = LocalProcessTransport(ProcessTransportConfig(
            enabled=True,
            max_children=1,
            max_generation=1,
            startup_timeout_sec=2.0,
            shutdown_timeout_sec=1.0,
        ))

        def broken_send(_child, _payload) -> None:
            raise BrokenPipeError("simulated broken startup pipe")

        transport._send = broken_send  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "startup transport"):
            transport.spawn(WormState("broken", "desktop", generation=1))
        self.assertEqual(transport.child_count, 0)

    def test_manager_process_reproduction_is_opt_in_and_supervised(self) -> None:
        manager = WormManager(WormManagerConfig(
            initial_worms=1,
            brain_interval_sec=0.01,
            reproduction_enabled=True,
            reproduction_max_worms=2,
            reproduction_min_energy=0.9,
            reproduction_parent_energy_cost=0.2,
            reproduction_offspring_energy=0.2,
            process_transport_enabled=True,
            process_transport_max_children=1,
            process_transport_max_generation=1,
        ), rng=random.Random(7))
        manager.setup(brain=RecordingMotorBrain(), dimensions=(640, 480))
        manager.start()
        try:
            deadline = time.monotonic() + 5.0
            while manager.status()["process_children"] < 1 and time.monotonic() < deadline:
                manager.update()
                time.sleep(0.01)
            self.assertEqual(manager.status()["alive_worms"], 2)
            self.assertEqual(manager.status()["process_children"], 1)
            children = [worm for worm in manager.alive_worms() if worm.parent_id is not None]
            self.assertEqual(len(children), 1)
            self.assertTrue(manager.transport.has_child(children[0].worm_id))
        finally:
            manager.stop()
        self.assertEqual(manager.status()["process_children"], 0)

    def test_transport_startup_failure_rolls_back_parent_and_environment(self) -> None:
        manager = WormManager(WormManagerConfig(
            initial_worms=1,
            reproduction_enabled=True,
            reproduction_max_worms=2,
            reproduction_min_energy=0.9,
            reproduction_parent_energy_cost=0.3,
            reproduction_cooldown_ticks=7,
            process_transport_enabled=True,
            process_transport_max_children=1,
        ), rng=random.Random(9))
        manager.setup(brain=RecordingMotorBrain(), dimensions=(640, 480))
        parent = next(iter(manager.worms.values()))
        before = (parent.energy, parent.reproduction_cooldown, manager.environment.snapshot()["worm_count"])

        def fail(_child: WormState) -> bool:
            raise RuntimeError("simulated child startup failure")

        manager.transport.spawn = fail  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "startup failure"):
            manager._maybe_reproduce(parent)
        after = (parent.energy, parent.reproduction_cooldown, manager.environment.snapshot()["worm_count"])
        self.assertEqual(after, before)

    def test_low_energy_transport_failure_restores_exact_parent_state(self) -> None:
        manager = WormManager(WormManagerConfig(
            initial_worms=1,
            reproduction_enabled=True,
            reproduction_max_worms=2,
            reproduction_min_energy=0.0,
            reproduction_parent_energy_cost=0.3,
            reproduction_cooldown_ticks=7,
            process_transport_enabled=True,
            process_transport_max_children=1,
        ), rng=random.Random(17))
        manager.setup(brain=RecordingMotorBrain(), dimensions=(640, 480))
        parent = next(iter(manager.worms.values()))
        parent.energy = 0.1
        parent.reproduction_cooldown = 0
        before = (parent.energy, parent.reproduction_cooldown, manager.environment.snapshot()["worm_count"])
        manager.transport.spawn = lambda _child: False  # type: ignore[method-assign]
        self.assertIsNone(manager._maybe_reproduce(parent))
        after = (parent.energy, parent.reproduction_cooldown, manager.environment.snapshot()["worm_count"])
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
