from __future__ import annotations
import math
from pathlib import Path
import yaml


def load_config(path: str) -> dict:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise ValueError("config root must be a mapping")
    validate_config(cfg)
    return cfg


def _finite(value, name: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _unit_interval(value, name: str) -> float:
    value = _finite(value, name)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


def _boolean(mapping: dict, key: str, name: str, default: bool) -> bool:
    value = mapping.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def validate_config(cfg: dict) -> None:
    b = cfg.get("brain", {})
    if not isinstance(b, dict):
        raise ValueError("brain config must be a mapping")
    duration = _finite(b.get("duration_ms", 150), "brain.duration_ms")
    dt = _finite(b.get("dt_ms", 0.05), "brain.dt_ms")
    if duration <= 0 or dt <= 0 or dt >= duration:
        raise ValueError("brain duration_ms/dt_ms are invalid")
    mode = str(b.get("decision_mode", "raw"))
    if mode not in {"raw", "baseline_delta"}:
        raise ValueError("brain.decision_mode must be raw or baseline_delta")
    if _finite(b.get("baseline_gain", 2.0), "brain.baseline_gain") <= 0:
        raise ValueError("brain.baseline_gain must be > 0")
    if _finite(b.get("interval_sec", 0.5), "brain.interval_sec") <= 0:
        raise ValueError("brain.interval_sec must be positive")
    if _finite(b.get("shutdown_timeout_sec", 120.0), "brain.shutdown_timeout_sec") <= 0:
        raise ValueError("brain.shutdown_timeout_sec must be positive")
    if int(b.get("result_cache_size", 4096)) < 1:
        raise ValueError("brain.result_cache_size must be >= 1")

    stim = b.get("stimulus", {})
    if not isinstance(stim, dict):
        raise ValueError("brain.stimulus must be a mapping")
    imin = _finite(stim.get("min_pa", 1.5), "brain.stimulus.min_pa")
    imax = _finite(stim.get("max_pa", 5.0), "brain.stimulus.max_pa")
    if imin < 0 or imax < imin:
        raise ValueError("brain.stimulus current range is invalid")
    delay = _finite(stim.get("delay_ms", 10.0), "brain.stimulus.delay_ms")
    pulse = stim.get("duration_ms")
    pulse_value = None if pulse is None else _finite(pulse, "brain.stimulus.duration_ms")
    if delay < 0 or (pulse_value is not None and pulse_value <= 0):
        raise ValueError("brain.stimulus delay/duration is invalid")
    if pulse_value is not None and delay + pulse_value > duration:
        raise ValueError("brain.stimulus pulse must fit inside brain.duration_ms")

    sensory = b.get("sensory", {})
    if not isinstance(sensory, dict):
        raise ValueError("brain.sensory must be a mapping")
    _unit_interval(sensory.get("threshold", 0.2), "brain.sensory.threshold")
    quantum = _finite(sensory.get("quantum", 0.1), "brain.sensory.quantum")
    if _finite(sensory.get("hunger_gain", 0.35), "brain.sensory.hunger_gain") < 0 or _finite(sensory.get("stress_gain", 0.45), "brain.sensory.stress_gain") < 0:
        raise ValueError("brain.sensory hunger_gain/stress_gain must be >= 0")
    if _finite(sensory.get("proprioception_gain", 20.0), "brain.sensory.proprioception_gain") < 0:
        raise ValueError("brain.sensory.proprioception_gain must be >= 0")
    if not 0.0 < quantum <= 1.0:
        raise ValueError("brain.sensory.quantum must be > 0 and <= 1")

    o = cfg.get("organism", {})
    if not isinstance(o, dict):
        raise ValueError("organism config must be a mapping")
    _unit_interval(o.get("initial_energy", 1.0), "organism.initial_energy")
    _unit_interval(o.get("death_energy", 0.0), "organism.death_energy")
    for key in (
        "rest_gain", "explore_cost", "migrate_cost", "retreat_cost",
        "rich_habitat_bonus", "stress_gain_blocked", "stress_recovery",
        "stress_energy_penalty", "resource_intake_gain",
    ):
        if _finite(o.get(key, 0.0), f"organism.{key}") < 0:
            raise ValueError(f"organism.{key} must be >= 0")

    habitat = cfg.get("habitat", {})
    if not isinstance(habitat, dict):
        raise ValueError("habitat config must be a mapping")
    nodes = habitat.get("nodes", {})
    if not isinstance(nodes, dict):
        raise ValueError("habitat.nodes must be a mapping")
    if "dynamic_novelty" in habitat and not isinstance(habitat["dynamic_novelty"], bool):
        raise ValueError("habitat.dynamic_novelty must be a boolean")
    if isinstance(nodes, dict):
        known = {str(k) for k in nodes}
        for node_id, raw in nodes.items():
            if not isinstance(raw, dict):
                raise ValueError(f"habitat.nodes.{node_id} must be a mapping")
            for key in ("cpu_free", "memory_free", "network_quality", "peer_signal", "novelty"):
                _unit_interval(raw.get(key, 0.5 if key in {"cpu_free", "memory_free", "network_quality"} else 0.0), f"habitat.nodes.{node_id}.{key}")
            neighbors = raw.get("neighbors", [])
            if not isinstance(neighbors, list):
                raise ValueError(f"habitat.nodes.{node_id}.neighbors must be a list")
            unknown = [str(n) for n in neighbors if str(n) not in known]
            if unknown:
                raise ValueError(f"habitat.nodes.{node_id} has unknown neighbors: {unknown}")

    desktop = cfg.get("desktop", {})
    if not isinstance(desktop, dict):
        raise ValueError("desktop config must be a mapping")
    if int(desktop.get("initial_worms", 1)) < 1:
        raise ValueError("desktop.initial_worms must be >= 1")
    if _finite(desktop.get("body_length_mm", 1.0), "desktop.body_length_mm") <= 0:
        raise ValueError("desktop.body_length_mm must be > 0")
    if not 15 <= int(desktop.get("render_fps", 60)) <= 144:
        raise ValueError("desktop.render_fps must be between 15 and 144")
    if int(desktop.get("region_tile_px", 200)) < 50:
        raise ValueError("desktop.region_tile_px must be >= 50")
    if not 0.5 <= _finite(desktop.get("workspace_factor", 0.85), "desktop.workspace_factor") <= 1.0:
        raise ValueError("desktop.workspace_factor must be between 0.5 and 1.0")

    reproduction = cfg.get("reproduction", {})
    if not isinstance(reproduction, dict):
        raise ValueError("reproduction config must be a mapping")
    if int(reproduction.get("max_worms", 12)) < 1:
        raise ValueError("reproduction.max_worms must be >= 1")
    if "enabled" in reproduction and not isinstance(reproduction["enabled"], bool):
        raise ValueError("reproduction.enabled must be a boolean")
    if int(desktop.get("initial_worms", 1)) > int(reproduction.get("max_worms", 12)):
        raise ValueError("desktop.initial_worms must not exceed reproduction.max_worms")
    for key, default in (
        ("min_energy", 0.90),
        ("offspring_energy", 0.22),
        ("parent_energy_cost", 0.30),
    ):
        _unit_interval(reproduction.get(key, default), f"reproduction.{key}")
    if int(reproduction.get("cooldown_brain_ticks", 20)) < 0:
        raise ValueError("reproduction.cooldown_brain_ticks must be >= 0")
    if _finite(reproduction.get("separation_px", 16.0), "reproduction.separation_px") < 0:
        raise ValueError("reproduction.separation_px must be >= 0")

    physics = cfg.get("physics", {})
    if not isinstance(physics, dict):
        raise ValueError("physics config must be a mapping")
    if not 3 <= int(physics.get("segments", 13)) <= 64:
        raise ValueError("physics.segments must be between 3 and 64")
    for key, default in (("stiffness", 0.72), ("damping", 0.82)):
        value = _finite(physics.get(key, default), f"physics.{key}")
        if not 0.0 < value <= 1.0:
            raise ValueError(f"physics.{key} must be > 0 and <= 1")
    for key, default in (("muscle_gain", 1.8), ("propulsion_gain", 2.8)):
        if _finite(physics.get(key, default), f"physics.{key}") <= 0:
            raise ValueError(f"physics.{key} must be > 0")
    if not 1 <= int(physics.get("substeps", 4)) <= 16:
        raise ValueError("physics.substeps must be between 1 and 16")

    visual_debug = cfg.get("visual_debug", {})
    if not isinstance(visual_debug, dict):
        raise ValueError("visual_debug config must be a mapping")
    _boolean(visual_debug, "enabled", "visual_debug.enabled", False)
    _boolean(visual_debug, "show_coordinates", "visual_debug.show_coordinates", True)
    _boolean(visual_debug, "show_sensory_state", "visual_debug.show_sensory_state", False)
    if not 1 <= int(visual_debug.get("scale", 20)) <= 100:
        raise ValueError("visual_debug.scale must be between 1 and 100")

    ecology = cfg.get("ecology", {})
    if not isinstance(ecology, dict):
        raise ValueError("ecology config must be a mapping")
    _boolean(ecology, "enabled", "ecology.enabled", True)
    _boolean(ecology, "arcade_growth", "ecology.arcade_growth", False)
    _boolean(ecology, "predation_enabled", "ecology.predation_enabled", False)
    if not 1.0 <= _finite(ecology.get("max_visual_growth", 2.0), "ecology.max_visual_growth") <= 4.0:
        raise ValueError("ecology.max_visual_growth must be between 1 and 4")
    if _finite(ecology.get("predation_size_ratio", 1.75), "ecology.predation_size_ratio") <= 1.0:
        raise ValueError("ecology.predation_size_ratio must be > 1")
    if _finite(ecology.get("basal_energy_use_per_sec", 0.002), "ecology.basal_energy_use_per_sec") < 0:
        raise ValueError("ecology.basal_energy_use_per_sec must be >= 0")
    if _finite(ecology.get("energy_capacity", 2.0), "ecology.energy_capacity") <= 0:
        raise ValueError("ecology.energy_capacity must be > 0")
    if _finite(ecology.get("mass_gain_fraction", 0.25), "ecology.mass_gain_fraction") < 0:
        raise ValueError("ecology.mass_gain_fraction must be >= 0")

    resources = cfg.get("resources", {})
    if not isinstance(resources, dict):
        raise ValueError("resources config must be a mapping")
    maximum = int(resources.get("max_count", 24))
    initial = int(resources.get("initial_count", 16))
    if maximum < 0 or initial < 0 or initial > maximum:
        raise ValueError("resources counts must satisfy 0 <= initial_count <= max_count")
    if _finite(resources.get("regeneration_interval_sec", 15.0), "resources.regeneration_interval_sec") <= 0:
        raise ValueError("resources.regeneration_interval_sec must be > 0")
    nutrition_min = _finite(resources.get("nutrition_min", 0.10), "resources.nutrition_min")
    nutrition_max = _finite(resources.get("nutrition_max", 0.20), "resources.nutrition_max")
    if nutrition_min < 0 or nutrition_max < nutrition_min:
        raise ValueError("resources nutrition range is invalid")
    for key, default in (("radius_px", 2.0), ("sensory_radius_px", 120.0)):
        if _finite(resources.get(key, default), f"resources.{key}") <= 0:
            raise ValueError(f"resources.{key} must be > 0")

    replication = cfg.get("replication", {})
    if not isinstance(replication, dict):
        raise ValueError("replication config must be a mapping")
    _boolean(replication, "sandbox_enabled", "replication.sandbox_enabled", False)
    _boolean(replication, "process_enabled", "replication.process_enabled", False)
    _boolean(replication, "per_worm_state_directories", "replication.per_worm_state_directories", True)
    if int(replication.get("max_living_worms", 12)) < 1:
        raise ValueError("replication.max_living_worms must be >= 1")
    if int(replication.get("max_generations", 3)) < 0:
        raise ValueError("replication.max_generations must be >= 0")
    if int(replication.get("max_child_processes", 2)) < 0:
        raise ValueError("replication.max_child_processes must be >= 0")
    if int(desktop.get("initial_worms", 1)) > int(replication.get("max_living_worms", 12)):
        raise ValueError("desktop.initial_worms must not exceed replication.max_living_worms")
    sandbox_root = replication.get("sandbox_root", "~/Desktop/WormHabitat")
    if not isinstance(sandbox_root, str) or not sandbox_root.strip():
        raise ValueError("replication.sandbox_root must be a non-empty string")
    if _finite(replication.get("max_sandbox_disk_mb", 16.0), "replication.max_sandbox_disk_mb") <= 0:
        raise ValueError("replication.max_sandbox_disk_mb must be > 0")
    if _finite(replication.get("max_per_worm_state_kb", 64.0), "replication.max_per_worm_state_kb") <= 0:
        raise ValueError("replication.max_per_worm_state_kb must be > 0")
    if _finite(replication.get("cleanup_timeout_sec", 5.0), "replication.cleanup_timeout_sec") <= 0:
        raise ValueError("replication.cleanup_timeout_sec must be > 0")

    transport = cfg.get("transport", {})
    if not isinstance(transport, dict):
        raise ValueError("transport config must be a mapping")
    if int(transport.get("max_children", 2)) < 0:
        raise ValueError("transport.max_children must be >= 0")
    if int(transport.get("max_generation", 1)) < 0:
        raise ValueError("transport.max_generation must be >= 0")
    if "process_enabled" in transport and not isinstance(transport["process_enabled"], bool):
        raise ValueError("transport.process_enabled must be a boolean")
    if (
        "process_enabled" in replication
        and "process_enabled" in transport
        and replication["process_enabled"] != transport["process_enabled"]
    ):
        raise ValueError("replication.process_enabled conflicts with transport.process_enabled")
    for key, default in (("startup_timeout_sec", 5.0), ("shutdown_timeout_sec", 3.0)):
        if _finite(transport.get(key, default), f"transport.{key}") <= 0:
            raise ValueError(f"transport.{key} must be > 0")

    for section, key, default in (
        ("logging", "path", "logs/worm.jsonl"),
        ("state", "path", ".runtime/worm-state.json"),
    ):
        value = cfg.get(section, {}).get(key, default) if isinstance(cfg.get(section, {}), dict) else None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{section}.{key} must be a non-empty string")
