from __future__ import annotations
import argparse
import json
from pathlib import Path

from .brain_c302 import C302Brain
from .config import load_config
from .doctor import print_checks
from .event_log import EventLogger
from .lab import LabEnvironment
from .organism import MetabolismConfig, WormMetabolism
from .runner import WormRunner
from .state_store import StateStore
from .telemetry import summarize_log


def build_brain(cfg: dict) -> C302Brain:
    b = cfg.get("brain", {})
    s = b.get("stimulus", {})
    sensory = b.get("sensory", {})
    return C302Brain(
        parameter_set=b.get("parameter_set", "A"),
        duration_ms=b.get("duration_ms", 150),
        dt_ms=b.get("dt_ms", 0.05),
        work_dir=b.get("work_dir", ".runtime/c302"),
        stimulus_min_pa=s.get("min_pa", 1.5),
        stimulus_max_pa=s.get("max_pa", 5.0),
        stimulus_delay_ms=s.get("delay_ms", 10.0),
        stimulus_duration_ms=s.get("duration_ms"),
        sensory_threshold=sensory.get("threshold", 0.20),
        sensory_quantum=sensory.get("quantum", 0.10),
        decision_mode=b.get("decision_mode", "raw"),
        baseline_gain=b.get("baseline_gain", 2.0),
        hunger_gain=sensory.get("hunger_gain", 0.35),
        stress_gain=sensory.get("stress_gain", 0.45),
        proprioception_gain=sensory.get("proprioception_gain", 20.0),
        result_cache_size=b.get("result_cache_size", 4096),
    )


def build_metabolism(cfg: dict) -> WormMetabolism:
    o = cfg.get("organism", {})
    return WormMetabolism(MetabolismConfig(
        rest_gain=float(o.get("rest_gain", 0.025)),
        explore_cost=float(o.get("explore_cost", 0.025)),
        migrate_cost=float(o.get("migrate_cost", 0.07)),
        retreat_cost=float(o.get("retreat_cost", 0.045)),
        rich_habitat_bonus=float(o.get("rich_habitat_bonus", 0.02)),
        stress_gain_blocked=float(o.get("stress_gain_blocked", 0.18)),
        stress_recovery=float(o.get("stress_recovery", 0.05)),
        stress_energy_penalty=float(o.get("stress_energy_penalty", 0.015)),
        resource_intake_gain=float(o.get("resource_intake_gain", 0.015)),
    ))


def _state_store(cfg: dict) -> StateStore:
    return StateStore(cfg.get("state", {}).get("path", ".runtime/worm-state.json"))


def _environment(cfg: dict) -> LabEnvironment:
    return LabEnvironment.from_config(cfg)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="OpenWorm c302 digital nematode controller")
    parser.add_argument(
        "command",
        choices=["demo", "probe", "probe-all", "calibrate", "neural-check", "doctor", "validate", "state", "summary", "cache", "clear-cache", "clear-result-cache"],
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--ticks", type=int, default=5)
    parser.add_argument("--start-host", default="alpha")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--decision-mode", choices=["raw", "baseline_delta"], default=None)
    args = parser.parse_args(argv)

    if args.command == "doctor":
        raise SystemExit(0 if print_checks() else 1)

    cfg = load_config(args.config)
    if args.decision_mode is not None:
        cfg.setdefault("brain", {})["decision_mode"] = args.decision_mode
    if args.command == "validate":
        env = _environment(cfg)
        print(f"config OK: {args.config}; habitat nodes={', '.join(env.nodes)}")
        return

    if args.command == "summary":
        log_path = cfg.get("logging", {}).get("path", "logs/worm.jsonl")
        print(json.dumps(summarize_log(log_path), indent=2, sort_keys=True))
        return

    if args.command == "state":
        store = _state_store(cfg)
        if not store.exists():
            print("no saved worm state")
            return
        print(json.dumps(store.load().to_dict(), indent=2, sort_keys=True))
        return

    brain = build_brain(cfg)
    if args.command == "neural-check":
        result = brain.neural_check()
        print(json.dumps(result, indent=2, sort_keys=True))
        raise SystemExit(0 if result["ok"] else 1)
    if args.command == "cache":
        print(json.dumps(brain.cache_info(), indent=2, sort_keys=True))
        return
    if args.command == "clear-cache":
        print(f"removed {brain.clear_cache()} cached c302 files")
        return
    if args.command == "clear-result-cache":
        print(f"removed {brain.clear_result_cache()} in-memory c302 results")
        return
    if args.command == "calibrate":
        print(json.dumps(brain.calibrate(), indent=2, sort_keys=True))
        return

    env = _environment(cfg)

    if args.command in {"probe", "probe-all"}:
        hosts = [args.start_host] if args.command == "probe" else list(env.nodes)
        for host in hosts:
            if host not in env.nodes:
                raise SystemExit(f"unknown host: {host}; choose from {', '.join(env.nodes)}")
            result = brain.step(env.nodes[host].state.copy())
            print(json.dumps({
                "host": host,
                "action": result.action.value,
                "confidence": result.confidence,
                "decision_margin": result.decision_margin,
                "forward_score": result.forward_score,
                "reverse_score": result.reverse_score,
                "stimulated_neurons": result.stimulated_neurons,
                "neuron_strengths": result.neuron_strengths,
                "neuron_scores": result.neuron_scores,
                "metadata": result.metadata,
            }, indent=2, sort_keys=True))
        return

    if args.start_host not in env.nodes:
        raise SystemExit(f"unknown start host: {args.start_host}; choose from {', '.join(env.nodes)}")

    o = cfg.get("organism", {})
    logger = EventLogger(cfg.get("logging", {}).get("path", "logs/worm.jsonl"))
    state_store = _state_store(cfg)
    initial_state = state_store.load() if args.resume and state_store.exists() else None

    final = WormRunner(
        brain,
        env,
        logger,
        build_metabolism(cfg),
        state_store=state_store,
    ).run(
        ticks=args.ticks,
        start_host=args.start_host,
        initial_energy=float(o.get("initial_energy", 1.0)),
        state=initial_state,
        quiet=args.quiet,
    )
    if args.quiet:
        print(json.dumps(final.to_dict(), sort_keys=True))


if __name__ == "__main__":
    main()
