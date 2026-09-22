from __future__ import annotations
import hashlib
import importlib
import json
import os
import shutil
import copy
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Mapping, Sequence

from .decision import LocomotionInterpreter, OUTPUT_NEURONS
from .models import BrainResult, HostState, WormState
from .neuromechanics import MOTOR_NEURONS, MUSCLE_NAMES
from .sensors import DigitalSensoryMapper


class C302Brain:
    """Run short OpenWorm c302/NeuroML simulations and decode locomotion output.

    The step path is intentionally unchanged from the v0.4 core. The only
    addition in the v0.5 desktop layer is an in-memory result cache so that
    worms presenting the same quantized sensory pattern can reuse one neural
    result instead of rerunning jNeuroML unnecessarily. The cache is a pure
    optimisation; correctness does not depend on it.
    """

    MODEL_SCHEMA = 5

    def __init__(
        self,
        parameter_set: str = "A",
        duration_ms: float = 150,
        dt_ms: float = 0.05,
        work_dir: str = ".runtime/c302",
        stimulus_min_pa: float = 1.5,
        stimulus_max_pa: float = 5.0,
        stimulus_delay_ms: float = 10.0,
        stimulus_duration_ms: float | None = None,
        sensory_threshold: float = 0.20,
        sensory_quantum: float = 0.10,
        decision_mode: str = "raw",
        baseline_gain: float = 2.0,
        hunger_gain: float = 0.35,
        stress_gain: float = 0.45,
        proprioception_gain: float = 20.0,
        result_cache_size: int = 4096,
    ):
        self.parameter_set = str(parameter_set)
        self.duration_ms = float(duration_ms)
        self.dt_ms = float(dt_ms)
        self.work_dir = Path(work_dir).resolve()
        self.stimulus_min_pa = float(stimulus_min_pa)
        self.stimulus_max_pa = float(stimulus_max_pa)
        self.stimulus_delay_ms = float(stimulus_delay_ms)
        self.stimulus_duration_ms = (
            max(1.0, self.duration_ms - self.stimulus_delay_ms - 5.0)
            if stimulus_duration_ms is None
            else float(stimulus_duration_ms)
        )
        self.decision_mode = str(decision_mode)
        self.baseline_gain = float(baseline_gain)
        self.mapper = DigitalSensoryMapper(
            threshold=float(sensory_threshold),
            strength_quantum=float(sensory_quantum),
            hunger_gain=float(hunger_gain),
            stress_gain=float(stress_gain),
            proprioception_gain=float(proprioception_gain),
        )
        self.interpreter = LocomotionInterpreter()
        self._cache_max = max(1, int(result_cache_size))
        self._result_cache: OrderedDict[str, BrainResult] = OrderedDict()
        self._cache_lock = threading.RLock()
        self._runtime_imports = None
        self._validate()
        # Capture the canonical c302 package before Tk/Pillow or generated-model
        # imports can alter Python's package binding during a multi-worm run.
        self._imports()

    def _validate(self) -> None:
        if self.duration_ms <= 0 or self.dt_ms <= 0:
            raise ValueError("duration_ms and dt_ms must be > 0")
        if self.dt_ms >= self.duration_ms:
            raise ValueError("dt_ms must be smaller than duration_ms")
        if self.stimulus_min_pa < 0 or self.stimulus_max_pa < self.stimulus_min_pa:
            raise ValueError("stimulus current range is invalid")
        if self.stimulus_delay_ms < 0 or self.stimulus_duration_ms <= 0:
            raise ValueError("stimulus delay/duration is invalid")
        if self.stimulus_delay_ms + self.stimulus_duration_ms > self.duration_ms:
            raise ValueError("stimulus pulse must fit inside simulation duration")
        if self.decision_mode not in {"raw", "baseline_delta"}:
            raise ValueError("decision_mode must be raw or baseline_delta")
        if self.baseline_gain <= 0:
            raise ValueError("baseline_gain must be > 0")

    def _imports(self):
        # Keep the canonical package objects for the lifetime of the brain. Some
        # c302/pyNeuroML import paths can mutate the package binding after an
        # epoch; a second worm must not pick up a shadow module lacking generate().
        if self._runtime_imports is not None:
            return self._runtime_imports
        try:
            import c302
            from pyneuroml import pynml
            import neuroml.writers as writers
        except ImportError as exc:
            raise RuntimeError(
                "OpenWorm c302/pyNeuroML is not installed. Run bootstrap.ps1 first."
            ) from exc
        if not callable(getattr(c302, "generate", None)):
            raise RuntimeError("installed c302 package does not expose generate()")
        self._runtime_imports = (c302, pynml, writers)
        return self._runtime_imports

    def _result_key(self, neuron: str) -> str:
        if self.parameter_set.startswith(("A", "B")):
            return f"{neuron}/0/generic_neuron_iaf_cell/v"
        if self.parameter_set.startswith("D"):
            return f"{neuron}/0/{neuron}/v"
        return f"{neuron}/0/GenericNeuronCell/v"

    def _current_for_strength(self, strength: float) -> float:
        strength = max(0.0, min(1.0, float(strength)))
        return self.stimulus_min_pa + strength * (self.stimulus_max_pa - self.stimulus_min_pa)

    def _stimulus_signature(self, strengths: Mapping[str, float]) -> list[str]:
        return [f"{n}:{float(strengths[n]):.3f}" for n in sorted(strengths)]

    def _reference_for(self, strengths: Mapping[str, float]) -> str:
        signature = "|".join([
            f"schema={self.MODEL_SCHEMA}",
            self.parameter_set,
            f"duration={self.duration_ms}",
            f"dt={self.dt_ms}",
            f"imin={self.stimulus_min_pa}",
            f"imax={self.stimulus_max_pa}",
            f"delay={self.stimulus_delay_ms}",
            f"pulse={self.stimulus_duration_ms}",
            *self._stimulus_signature(strengths),
        ])
        digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]
        return f"DigitalNematode_{digest}"

    def _manifest_path(self, reference: str) -> Path:
        return self.work_dir / f"{reference}.manifest.json"

    def _write_manifest(self, reference: str, strengths: Mapping[str, float]) -> None:
        manifest = {
            "schema": self.MODEL_SCHEMA,
            "reference": reference,
            "parameter_set": self.parameter_set,
            "duration_ms": self.duration_ms,
            "dt_ms": self.dt_ms,
            "stimulus_delay_ms": self.stimulus_delay_ms,
            "stimulus_duration_ms": self.stimulus_duration_ms,
            "stimuli": {
                n: {
                    "strength": float(s),
                    "current_pa": round(self._current_for_strength(s), 6),
                }
                for n, s in sorted(strengths.items())
            },
        }
        self._manifest_path(reference).write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _cache_valid(self, reference: str) -> bool:
        lems = self.work_dir / f"LEMS_{reference}.xml"
        nml = self.work_dir / f"{reference}.net.nml"
        manifest = self._manifest_path(reference)
        if not (lems.exists() and nml.exists() and manifest.exists()):
            return False
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return data.get("schema") == self.MODEL_SCHEMA and data.get("reference") == reference
        except Exception:
            return False

    def _ensure_model(self, c302, writers, reference: str, strengths: Mapping[str, float]) -> Path:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        lems_file = self.work_dir / f"LEMS_{reference}.xml"
        nml_file = self.work_dir / f"{reference}.net.nml"
        if self._cache_valid(reference):
            return lems_file

        ParameterisedModel = getattr(
            importlib.import_module(f"c302.parameters_{self.parameter_set}"),
            "ParameterisedModel",
        )
        params = ParameterisedModel()
        cells_to_plot = list(dict.fromkeys([*OUTPUT_NEURONS, *MOTOR_NEURONS, *strengths.keys()]))

        # Generate the full connectome with generic stimulation disabled. Explicit
        # pulse generators are then attached to the selected sensory neurons.
        nml_doc = c302.generate(
            reference,
            params,
            cells=None,
            cells_to_plot=cells_to_plot,
            cells_to_stimulate=[],
            muscles_to_include=list(MUSCLE_NAMES),
            duration=self.duration_ms,
            dt=self.dt_ms,
            target_directory=str(self.work_dir),
            verbose=False,
        )

        for neuron, strength in sorted(strengths.items()):
            current_pa = self._current_for_strength(strength)
            c302.add_new_input(
                nml_doc,
                neuron,
                f"{self.stimulus_delay_ms:g}ms",
                f"{self.stimulus_duration_ms:g}ms",
                f"{current_pa:g}pA",
                params,
            )

        writers.NeuroMLWriter.write(nml_doc, str(nml_file))
        self._write_manifest(reference, strengths)
        return lems_file

    @staticmethod
    def _trace_from_results(results: Mapping[str, Sequence[float]], expected_key: str, neuron: str):
        trace = results.get(expected_key)
        if trace is not None:
            return trace, expected_key
        candidates = [
            k for k in results.keys()
            if k != "t" and str(k).startswith(f"{neuron}/") and str(k).endswith("/v")
        ]
        if len(candidates) == 1:
            return results[candidates[0]], candidates[0]
        return None, None

    def _run_results(self, c302, pynml, writers, strengths: Mapping[str, float]):
        self._ensure_java_runtime()
        reference = self._reference_for(strengths)
        lems_file = self._ensure_model(c302, writers, reference, strengths)
        old = os.getcwd()
        try:
            os.chdir(self.work_dir)
            results = pynml.run_lems_with_jneuroml(
                lems_file.name,
                nogui=True,
                load_saved_data=True,
                verbose=False,
            )
        except Exception as exc:
            raise RuntimeError(
                f"jNeuroML failed for {reference}: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            os.chdir(old)
        if not isinstance(results, Mapping) or "t" not in results:
            raise RuntimeError(f"jNeuroML returned an unexpected result for {reference}")
        return reference, lems_file, results

    @staticmethod
    def _ensure_java_runtime() -> None:
        if shutil.which("java"):
            return
        project_root = Path(__file__).resolve().parents[2]
        candidates = sorted(
            (project_root / ".runtime" / "tools" / "temurin17").glob(
                "*/bin/java.exe"
            )
        )
        if not candidates:
            return
        java_bin = str(candidates[-1].parent)
        os.environ["PATH"] = java_bin + os.pathsep + os.environ.get("PATH", "")

    def _extract_scores(self, results: Mapping[str, Sequence[float]]) -> tuple[Dict[str, float], dict, list[str]]:
        scores: Dict[str, float] = {}
        resolved_keys: Dict[str, str | None] = {}
        missing: list[str] = []
        for neuron in OUTPUT_NEURONS:
            trace, resolved = self._trace_from_results(results, self._result_key(neuron), neuron)
            resolved_keys[neuron] = resolved
            if trace is None:
                missing.append(neuron)
            scores[neuron] = self.interpreter.trace_activity(trace)
        if len(missing) == len(OUTPUT_NEURONS):
            available = sorted(str(k) for k in results.keys() if k != "t")[:20]
            raise RuntimeError(
                "No locomotion voltage traces were found in c302 output. "
                f"Expected neurons={list(OUTPUT_NEURONS)}; sample keys={available}"
            )
        return scores, resolved_keys, missing

    def _extract_motor_scores(self, results: Mapping[str, Sequence[float]]) -> tuple[Dict[str, float], dict, list[str]]:
        scores: Dict[str, float] = {}
        resolved_keys: Dict[str, str | None] = {}
        missing: list[str] = []
        for neuron in MOTOR_NEURONS:
            trace, resolved = self._trace_from_results(results, self._result_key(neuron), neuron)
            resolved_keys[neuron] = resolved
            if trace is None:
                missing.append(neuron)
            scores[neuron] = self.interpreter.trace_activity(trace)
        if missing:
            available = sorted(str(k) for k in results.keys() if k != "t")[:20]
            raise RuntimeError(
                "Incomplete locomotor motor-neuron output from c302. "
                f"Missing={missing}; expected={list(MOTOR_NEURONS)}; sample keys={available}"
            )
        return scores, resolved_keys, missing

    def _extract_muscle_scores(self, results: Mapping[str, Sequence[float]]) -> tuple[Dict[str, float], list[str]]:
        cell_id = "generic_muscle_iaf_cell" if self.parameter_set.startswith(("A", "B")) else "GenericMuscleCell"
        scores: Dict[str, float] = {}
        missing: list[str] = []
        for muscle in MUSCLE_NAMES:
            key = f"{muscle}/0/{cell_id}/v"
            trace = results.get(key)
            if trace is None:
                missing.append(muscle)
            scores[muscle] = self.interpreter.trace_activity(trace)
        if missing:
            available = sorted(str(k) for k in results.keys() if k != "t")[:20]
            raise RuntimeError(
                "Incomplete body-wall muscle output from c302. "
                f"Missing {len(missing)}/{len(MUSCLE_NAMES)} muscles; "
                f"first missing={missing[:8]}; sample keys={available}"
            )
        return scores, missing

    def calibrate(self) -> dict:
        """Run/cache an unstimulated c302 baseline and return output scores."""
        c302, pynml, writers = self._imports()
        reference, lems_file, results = self._run_results(c302, pynml, writers, {})
        scores, resolved, missing = self._extract_scores(results)
        payload = {
            "reference": reference,
            "lems_file": lems_file.name,
            "scores": scores,
            "missing_output_neurons": missing,
            "resolved_result_keys": resolved,
            "samples": len(results.get("t", [])),
        }
        self.work_dir.mkdir(parents=True, exist_ok=True)
        (self.work_dir / "baseline.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        return payload

    def _baseline_scores(self, c302, pynml, writers) -> dict[str, float]:
        baseline_file = self.work_dir / "baseline.json"
        if baseline_file.exists():
            try:
                payload = json.loads(baseline_file.read_text(encoding="utf-8"))
                if payload.get("reference") == self._reference_for({}):
                    return {str(k): float(v) for k, v in payload.get("scores", {}).items()}
            except Exception:
                pass
        reference, _, results = self._run_results(c302, pynml, writers, {})
        scores, resolved, missing = self._extract_scores(results)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        baseline_file.write_text(json.dumps({
            "reference": reference,
            "scores": scores,
            "resolved_result_keys": resolved,
            "missing_output_neurons": missing,
            "samples": len(results.get("t", [])),
        }, indent=2, sort_keys=True), encoding="utf-8")
        return scores

    def neural_check(self) -> dict:
        """Verify all named interface neurons exist in the installed c302 dataset."""
        c302, _, _ = self._imports()
        cell_names, _ = c302.get_cell_names_and_connection(c302.DEFAULT_DATA_READER)
        _, muscle_names, _ = c302.get_cell_muscle_names_and_connection(c302.DEFAULT_DATA_READER)
        available = set(cell_names)
        sensory_names = {
            "AWAL", "AWAR", "ASEL", "ASER", "AWCL", "AWCR", "ADFL", "ADFR",
            "ASHL", "ASHR", "DVA", "ALML", "ALMR", "PLML", "PLMR",
        }
        required = sorted(sensory_names | set(OUTPUT_NEURONS) | set(MOTOR_NEURONS))
        missing = [n for n in required if n not in available]
        missing_muscles = [name for name in MUSCLE_NAMES if name not in set(muscle_names)]
        return {
            "ok": not missing and not missing_muscles,
            "required_count": len(required),
            "dataset_cell_count": len(cell_names),
            "missing": missing,
            "missing_muscles": missing_muscles,
            "required": required,
            "required_muscle_count": len(MUSCLE_NAMES),
        }

    def clear_cache(self) -> int:
        if not self.work_dir.exists():
            return 0
        count = sum(1 for p in self.work_dir.rglob("*") if p.is_file())
        shutil.rmtree(self.work_dir)
        return count

    def cache_info(self) -> dict:
        manifests = list(self.work_dir.glob("*.manifest.json")) if self.work_dir.exists() else []
        return {
            "work_dir": str(self.work_dir),
            "models": len(manifests),
            "baseline": (self.work_dir / "baseline.json").exists(),
        }

    def clear_result_cache(self) -> int:
        """Drop the in-memory c302 result cache and return the number of entries removed."""
        with self._cache_lock:
            count = len(self._result_cache)
            self._result_cache.clear()
            return count

    def _cache_lookup(self, strengths: Mapping[str, float]) -> BrainResult | None:
        sig = self._reference_for(strengths)
        with self._cache_lock:
            cached = self._result_cache.get(sig)
            if cached is None:
                return None
            self._result_cache.move_to_end(sig)
            result = copy.deepcopy(cached)
        result.metadata["result_cache_hit"] = True
        return result

    def _cache_store(self, strengths: Mapping[str, float], result: BrainResult) -> None:
        sig = self._reference_for(strengths)
        with self._cache_lock:
            self._result_cache[sig] = copy.deepcopy(result)
            self._result_cache.move_to_end(sig)
            while len(self._result_cache) > self._cache_max:
                self._result_cache.popitem(last=False)

    def step(self, environment: HostState, organism: WormState | None = None) -> BrainResult:
        sensory = self.mapper.sense(environment, organism)
        strengths = sensory.neuron_strengths

        # In-memory cache hit: reuse a previous deterministic neural result.
        cached = self._cache_lookup(strengths)
        if cached is not None:
            cached.stimulated_neurons = list(sensory.stimulated_neurons)
            cached.sensory_channels = dict(sensory.channels)
            cached.neuron_strengths = dict(strengths)
            return cached

        c302, pynml, writers = self._imports()
        reference, lems_file, results = self._run_results(c302, pynml, writers, strengths)
        raw_scores, resolved_keys, missing = self._extract_scores(results)
        motor_scores, resolved_motor_keys, missing_motor = self._extract_motor_scores(results)
        muscle_scores, missing_muscles = self._extract_muscle_scores(results)

        baseline_scores: dict[str, float] = {}
        effective_scores = raw_scores
        if self.decision_mode == "baseline_delta":
            baseline_scores = self._baseline_scores(c302, pynml, writers)
            effective_scores = self.interpreter.baseline_center(
                raw_scores, baseline_scores, gain=self.baseline_gain
            )

        action, forward, reverse = self.interpreter.decide(effective_scores)
        confidence, margin = self.interpreter.confidence(forward, reverse, action)
        result = BrainResult(
            stimulated_neurons=sensory.stimulated_neurons,
            neuron_scores=effective_scores,
            forward_score=forward,
            reverse_score=reverse,
            action=action,
            sensory_channels=sensory.channels,
            neuron_strengths=dict(strengths),
            motor_neuron_scores=motor_scores,
            muscle_scores=muscle_scores,
            confidence=confidence,
            decision_margin=margin,
            metadata={
                "model_reference": reference,
                "lems_file": lems_file.name,
                "decision_mode": self.decision_mode,
                "raw_neuron_scores": raw_scores,
                "baseline_scores": baseline_scores,
                "missing_output_neurons": missing,
                "resolved_result_keys": resolved_keys,
                "missing_motor_neurons": missing_motor,
                "resolved_motor_result_keys": resolved_motor_keys,
                "missing_muscles": missing_muscles,
                "samples": len(results.get("t", [])),
                "result_cache_hit": False,
            },
        )
        self._cache_store(strengths, result)
        return result
