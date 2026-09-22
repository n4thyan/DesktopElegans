from __future__ import annotations

import argparse
import json
import sys
import time

from .worm_manager import WormManager, WormManagerConfig


CORE_COMMANDS = {
    "demo", "probe", "probe-all", "calibrate", "neural-check", "doctor",
    "validate", "state", "summary", "cache", "clear-cache", "clear-result-cache",
}


def manager_config(config: dict) -> WormManagerConfig:
    brain = config.get("brain", {})
    stimulus = brain.get("stimulus", {})
    sensory = brain.get("sensory", {})
    organism = config.get("organism", {})
    desktop = config.get("desktop", {})
    reproduction = config.get("reproduction", {})
    physics = config.get("physics", {})
    transport = config.get("transport", {})
    visual_debug = config.get("visual_debug", {})
    ecology = config.get("ecology", {})
    resources = config.get("resources", {})
    replication = config.get("replication", {})
    process_enabled = bool(replication.get("process_enabled", transport.get("process_enabled", False)))
    max_child_processes = int(replication.get("max_child_processes", transport.get("max_children", 2)))
    max_generations = int(replication.get("max_generations", 3))
    return WormManagerConfig(
        body_length_mm=float(desktop.get("body_length_mm", 1.0)),
        render_fps=int(desktop.get("render_fps", 60)),
        initial_worms=int(desktop.get("initial_worms", 1)),
        brain_parameter_set=str(brain.get("parameter_set", "A")),
        brain_duration_ms=float(brain.get("duration_ms", 150.0)),
        brain_dt_ms=float(brain.get("dt_ms", 0.05)),
        brain_decision_mode=str(brain.get("decision_mode", "raw")),
        brain_baseline_gain=float(brain.get("baseline_gain", 2.0)),
        brain_work_dir=str(brain.get("work_dir", ".runtime/c302")),
        brain_interval_sec=float(brain.get("interval_sec", 0.5)),
        brain_shutdown_timeout_sec=float(brain.get("shutdown_timeout_sec", 120.0)),
        brain_cache_size=int(brain.get("result_cache_size", 4096)),
        brain_stimulus_min_pa=float(stimulus.get("min_pa", 1.5)),
        brain_stimulus_max_pa=float(stimulus.get("max_pa", 5.0)),
        brain_stimulus_delay_ms=float(stimulus.get("delay_ms", 10.0)),
        brain_stimulus_duration_ms=(
            None if stimulus.get("duration_ms") is None else float(stimulus["duration_ms"])
        ),
        brain_sensory_threshold=float(sensory.get("threshold", 0.20)),
        brain_sensory_quantum=float(sensory.get("quantum", 0.10)),
        brain_hunger_gain=float(sensory.get("hunger_gain", 0.35)),
        brain_stress_gain=float(sensory.get("stress_gain", 0.45)),
        brain_proprioception_gain=float(sensory.get("proprioception_gain", 20.0)),
        desktop_region_tile_px=int(desktop.get("region_tile_px", 200)),
        desktop_workspace_factor=float(desktop.get("workspace_factor", 0.85)),
        organism_initial_energy=float(organism.get("initial_energy", 1.0)),
        organism_rest_gain=float(organism.get("rest_gain", 0.025)),
        organism_explore_cost=float(organism.get("explore_cost", 0.025)),
        organism_migrate_cost=float(organism.get("migrate_cost", 0.07)),
        organism_retreat_cost=float(organism.get("retreat_cost", 0.045)),
        organism_resource_intake_gain=float(organism.get("resource_intake_gain", 0.015)),
        organism_rich_habitat_bonus=float(organism.get("rich_habitat_bonus", 0.02)),
        organism_stress_gain_blocked=float(organism.get("stress_gain_blocked", 0.18)),
        organism_stress_recovery=float(organism.get("stress_recovery", 0.05)),
        organism_stress_energy_penalty=float(organism.get("stress_energy_penalty", 0.015)),
        organism_death_energy=float(organism.get("death_energy", 0.0)),
        reproduction_enabled=bool(reproduction.get("enabled", True)),
        reproduction_max_worms=int(reproduction.get("max_worms", 12)),
        reproduction_min_energy=float(reproduction.get("min_energy", 0.90)),
        reproduction_offspring_energy=float(reproduction.get("offspring_energy", 0.22)),
        reproduction_parent_energy_cost=float(reproduction.get("parent_energy_cost", 0.30)),
        reproduction_cooldown_ticks=int(reproduction.get("cooldown_brain_ticks", 20)),
        reproduction_separation_px=float(reproduction.get("separation_px", 16.0)),
        physics_segments=int(physics.get("segments", 13)),
        physics_stiffness=float(physics.get("stiffness", 0.72)),
        physics_damping=float(physics.get("damping", 0.82)),
        physics_muscle_gain=float(physics.get("muscle_gain", 1.8)),
        physics_propulsion_gain=float(physics.get("propulsion_gain", 2.8)),
        physics_substeps=int(physics.get("substeps", 4)),
        process_transport_enabled=process_enabled,
        process_transport_max_children=max_child_processes,
        process_transport_max_generation=min(int(transport.get("max_generation", max_generations)), max_generations),
        process_transport_startup_timeout_sec=float(transport.get("startup_timeout_sec", 5.0)),
        process_transport_shutdown_timeout_sec=float(transport.get("shutdown_timeout_sec", 3.0)),
        ecology_enabled=bool(ecology.get("enabled", True)),
        ecology_arcade_growth=bool(ecology.get("arcade_growth", False)),
        ecology_max_visual_growth=float(ecology.get("max_visual_growth", 2.0)),
        ecology_predation_enabled=bool(ecology.get("predation_enabled", False)),
        ecology_predation_size_ratio=float(ecology.get("predation_size_ratio", 1.75)),
        ecology_basal_energy_use_per_sec=float(ecology.get("basal_energy_use_per_sec", 0.002)),
        ecology_energy_capacity=float(ecology.get("energy_capacity", 2.0)),
        ecology_mass_gain_fraction=float(ecology.get("mass_gain_fraction", 0.25)),
        resources_max_count=int(resources.get("max_count", 24)),
        resources_initial_count=int(resources.get("initial_count", 16)),
        resources_regeneration_interval_sec=float(resources.get("regeneration_interval_sec", 15.0)),
        resources_nutrition_min=float(resources.get("nutrition_min", 0.10)),
        resources_nutrition_max=float(resources.get("nutrition_max", 0.20)),
        resources_radius_px=float(resources.get("radius_px", 2.0)),
        resources_sensory_radius_px=float(resources.get("sensory_radius_px", 120.0)),
        replication_max_living_worms=int(replication.get("max_living_worms", 12)),
        replication_max_generations=max_generations,
        sandbox_enabled=bool(replication.get("sandbox_enabled", False)),
        sandbox_root=str(replication.get("sandbox_root", "~/Desktop/WormHabitat")),
        sandbox_max_disk_bytes=int(float(replication.get("max_sandbox_disk_mb", 16.0)) * 1024 * 1024),
        sandbox_max_state_bytes=int(float(replication.get("max_per_worm_state_kb", 64.0)) * 1024),
        sandbox_cleanup_timeout_sec=float(replication.get("cleanup_timeout_sec", 5.0)),
        sandbox_per_worm_directories=bool(replication.get("per_worm_state_directories", True)),
        visual_debug_enabled=bool(visual_debug.get("enabled", False)),
        visual_debug_scale=int(visual_debug.get("scale", 20)),
        visual_debug_show_coordinates=bool(visual_debug.get("show_coordinates", True)),
        visual_debug_show_sensory_state=bool(visual_debug.get("show_sensory_state", False)),
    )


def desktop_main(argv: list[str]) -> int:
    from .config import load_config

    parser = argparse.ArgumentParser(
        prog="nematode-mind desktop",
        description="Run the local c302-driven Windows desktop organism",
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--headless", action="store_true", help="disable the transparent overlay")
    parser.add_argument("--seconds", type=float, default=0.0, help="0 runs until Ctrl+C")
    parser.add_argument("--brain-ticks", type=int, default=0, help="stop after this many completed neural steps")
    parser.add_argument("--initial-worms", type=int)
    parser.add_argument("--no-reproduction", action="store_true")
    parser.add_argument("--process-transport", action="store_true", help="spawn bounded supervised local offspring processes")
    parser.add_argument(
        "--visual-debug-scale",
        type=int,
        choices=(1, 10, 20),
        default=None,
        help="override config and render at 1x/10x/20x without changing physics",
    )
    args = parser.parse_args(argv)

    cfg = manager_config(load_config(args.config))
    if args.initial_worms is not None:
        cfg.initial_worms = max(1, args.initial_worms)
    if args.no_reproduction:
        cfg.reproduction_enabled = False
    if args.process_transport:
        # Two-key opt-in: config must allow process transport and the launch must
        # explicitly request it. The CLI flag alone cannot enable spawning.
        cfg.process_transport_enabled = bool(cfg.process_transport_enabled)
    else:
        cfg.process_transport_enabled = False
    if args.visual_debug_scale is not None:
        cfg.visual_debug_enabled = args.visual_debug_scale > 1
        cfg.visual_debug_scale = args.visual_debug_scale

    manager = WormManager(cfg)
    manager.setup()
    shutdown_clean = True
    try:
        if not args.headless:
            from .overlay import DesktopOverlay

            manager.overlay = DesktopOverlay(
                body_length_mm=cfg.body_length_mm,
                fps=cfg.render_fps,
                debug_enabled=cfg.visual_debug_enabled,
                debug_scale=cfg.visual_debug_scale,
                show_coordinates=cfg.visual_debug_show_coordinates,
                show_sensory_state=cfg.visual_debug_show_sensory_state,
                arcade_growth=cfg.ecology_arcade_growth,
                max_visual_growth=cfg.ecology_max_visual_growth,
                surface_manager=manager.host_surface_manager,
            )
            manager.overlay.start()
        manager.start()

        started = time.monotonic()
        frame_interval = 1.0 / max(15, cfg.render_fps)
        while True:
            count = manager.update()
            status = manager.status()
            if count == 0:
                break
            if args.seconds > 0 and time.monotonic() - started >= args.seconds:
                break
            if args.brain_ticks > 0 and status["brain_ticks"] >= args.brain_ticks:
                break
            time.sleep(frame_interval)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_clean = manager.stop()
    print(json.dumps(manager.status(), indent=2, sort_keys=True))
    return 0 if shutdown_clean else 2


def cleanup_colony_main(argv: list[str]) -> int:
    from .colony_sandbox import ColonySandbox, ColonySandboxConfig
    from .config import load_config

    parser = argparse.ArgumentParser(prog="nematode-mind cleanup-colony")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)
    raw = load_config(args.config)
    replication = raw.get("replication", {})
    sandbox = ColonySandbox(ColonySandboxConfig(
        enabled=False,
        root=str(replication.get("sandbox_root", "~/Desktop/WormHabitat")),
        max_living_worms=int(replication.get("max_living_worms", 12)),
        max_generations=int(replication.get("max_generations", 3)),
        max_disk_bytes=int(float(replication.get("max_sandbox_disk_mb", 16.0)) * 1024 * 1024),
        max_state_bytes=int(float(replication.get("max_per_worm_state_kb", 64.0)) * 1024),
        cleanup_timeout_sec=float(replication.get("cleanup_timeout_sec", 5.0)),
        per_worm_directories=bool(replication.get("per_worm_state_directories", True)),
    ))
    cleaned = sandbox.cleanup()
    print(json.dumps({"sandbox_root": str(sandbox.root), "removed": cleaned}, indent=2))
    return 0 if cleaned else 2


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "cleanup-colony":
        raise SystemExit(cleanup_colony_main(args[1:]))
    if args and args[0] == "process-child":
        from .process_transport import process_child_main

        raise SystemExit(process_child_main())
    if args and args[0] in CORE_COMMANDS:
        from .cli import main as core_main

        core_main(args)
        return
    if args and args[0] == "desktop":
        args = args[1:]
    raise SystemExit(desktop_main(args))


if __name__ == "__main__":
    main()
