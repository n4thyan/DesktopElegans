from __future__ import annotations
from dataclasses import dataclass
from statistics import mean
from typing import Mapping, Sequence

from .models import WormAction

FORWARD_NEURONS = ("AVBL", "AVBR", "PVCL", "PVCR")
REVERSE_NEURONS = ("AVAL", "AVAR", "AVDL", "AVDR", "AVEL", "AVER")
OUTPUT_NEURONS = FORWARD_NEURONS + REVERSE_NEURONS


@dataclass(frozen=True)
class DecisionThresholds:
    reverse_margin: float = 0.08
    migrate_forward: float = 0.58
    explore_margin: float = 0.02


class LocomotionInterpreter:
    """Decode c302 membrane-potential traces into a small action vocabulary."""

    def __init__(self, thresholds: DecisionThresholds | None = None):
        self.thresholds = thresholds or DecisionThresholds()

    @staticmethod
    def trace_activity(trace: Sequence[float] | None) -> float:
        if trace is None:
            return 0.0
        try:
            count = len(trace)
        except TypeError:
            trace = list(trace)
            count = len(trace)
        if count == 0:
            return 0.0

        # pyNeuroML returns membrane potential in volts. Focus on the final 25%
        # while retaining peak depolarisation, so a brief active event survives
        # averaging. Works with Python lists and numpy arrays.
        start = max(0, int(count * 0.75))
        tail_mv = [float(v) * 1000.0 for v in trace[start:]]
        if not tail_mv:
            return 0.0

        avg_mv = mean(tail_mv)
        peak_mv = max(tail_mv)
        avg_score = max(0.0, min(1.0, (avg_mv + 70.0) / 50.0))
        peak_score = max(0.0, min(1.0, (peak_mv + 70.0) / 50.0))
        return max(0.0, min(1.0, (0.7 * avg_score) + (0.3 * peak_score)))

    @staticmethod
    def baseline_center(raw: Mapping[str, float], baseline: Mapping[str, float], gain: float = 2.0) -> dict[str, float]:
        """Center baseline activity at 0.5 and amplify stimulus-driven deltas.

        This is an engineered decoder, not a biological claim. It exists because
        c302 parameter sets can have different resting voltages/activity scales.
        """
        return {
            n: max(0.0, min(1.0, 0.5 + (float(raw.get(n, 0.0)) - float(baseline.get(n, 0.0))) * gain))
            for n in OUTPUT_NEURONS
        }

    def decide(self, scores: Mapping[str, float]) -> tuple[WormAction, float, float]:
        forward = mean(float(scores.get(n, 0.0)) for n in FORWARD_NEURONS)
        reverse = mean(float(scores.get(n, 0.0)) for n in REVERSE_NEURONS)
        t = self.thresholds

        if reverse > forward + t.reverse_margin:
            action = WormAction.RETREAT
        elif forward >= t.migrate_forward:
            action = WormAction.MIGRATE
        elif forward > reverse + t.explore_margin:
            action = WormAction.EXPLORE
        else:
            action = WormAction.REST
        return action, forward, reverse

    @staticmethod
    def confidence(forward: float, reverse: float, action: WormAction) -> tuple[float, float]:
        margin = abs(float(forward) - float(reverse))
        if action == WormAction.REST:
            # REST is most convincing when neither channel is strongly dominant.
            conf = max(0.0, min(1.0, 1.0 - margin * 3.0))
        else:
            conf = max(0.0, min(1.0, margin * 3.0))
        return conf, margin
