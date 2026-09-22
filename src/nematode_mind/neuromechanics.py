from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean
from typing import Mapping, Sequence

from .models import WormState

# Segmentally ordered locomotor motor neurons. B-class neurons dominate forward
# locomotion; A-class neurons dominate reverse locomotion. D-class neurons are
# inhibitory and reduce activation on the opposing body wall.
DORSAL_FORWARD = ("DB1", "DB2", "DB3", "DB4", "DB5", "DB6", "DB7")
VENTRAL_FORWARD = ("VB1", "VB2", "VB3", "VB4", "VB5", "VB6", "VB7", "VB8", "VB9", "VB10", "VB11")
DORSAL_REVERSE = ("DA1", "DA2", "DA3", "DA4", "DA5", "DA6", "DA7", "DA8", "DA9")
VENTRAL_REVERSE = ("VA1", "VA2", "VA3", "VA4", "VA5", "VA6", "VA7", "VA8", "VA9", "VA10", "VA11", "VA12")
DORSAL_INHIBITORY = ("DD1", "DD2", "DD3", "DD4", "DD5", "DD6")
VENTRAL_INHIBITORY = ("VD1", "VD2", "VD3", "VD4", "VD5", "VD6", "VD7", "VD8", "VD9", "VD10", "VD11", "VD12", "VD13")
MOTOR_NEURONS = tuple(dict.fromkeys(
    DORSAL_FORWARD + VENTRAL_FORWARD + DORSAL_REVERSE + VENTRAL_REVERSE
    + DORSAL_INHIBITORY + VENTRAL_INHIBITORY
))
MUSCLE_NAMES = tuple(
    [f"{quadrant}{index:02d}" for quadrant in ("MDL", "MDR", "MVR") for index in range(1, 25)]
    + [f"MVL{index:02d}" for index in range(1, 24)]
)


@dataclass(frozen=True)
class MuscleActivation:
    dorsal: tuple[float, ...]
    ventral: tuple[float, ...]
    forward_drive: float
    reverse_drive: float


@dataclass(frozen=True)
class BodyPhysicsConfig:
    segments: int = 13
    length_px: float = 12.0
    stiffness: float = 0.72
    damping: float = 0.82
    muscle_gain: float = 1.8
    propulsion_gain: float = 2.8
    substeps: int = 4


class MotorToMuscleDecoder:
    """Project named c302 motor-neuron activity onto body-wall muscle bands."""

    def __init__(self, segments: int = 13):
        self.segments = max(3, int(segments))

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _project(self, scores: Mapping[str, float], neurons: Sequence[str]) -> list[float]:
        values = [self._clamp(scores.get(name, 0.0)) for name in neurons]
        if len(values) == 1:
            return values * self.segments
        projected: list[float] = []
        for segment in range(self.segments):
            position = segment * (len(values) - 1) / (self.segments - 1)
            left = int(math.floor(position))
            right = min(len(values) - 1, left + 1)
            fraction = position - left
            projected.append(values[left] * (1.0 - fraction) + values[right] * fraction)
        return projected

    def _project_muscles(self, scores: Mapping[str, float], prefixes: tuple[str, str]) -> list[float]:
        longitudinal: list[float] = []
        for index in range(1, 25):
            values = [
                self._clamp(scores[name])
                for prefix in prefixes
                if (name := f"{prefix}{index:02d}") in scores
            ]
            longitudinal.append(mean(values) if values else 0.0)
        indexed = {str(index): value for index, value in enumerate(longitudinal)}
        return self._project(indexed, tuple(str(index) for index in range(24)))

    def decode(
        self,
        scores: Mapping[str, float],
        muscle_scores: Mapping[str, float] | None = None,
    ) -> MuscleActivation:
        df = self._project(scores, DORSAL_FORWARD)
        vf = self._project(scores, VENTRAL_FORWARD)
        dr = self._project(scores, DORSAL_REVERSE)
        vr = self._project(scores, VENTRAL_REVERSE)
        di = self._project(scores, DORSAL_INHIBITORY)
        vi = self._project(scores, VENTRAL_INHIBITORY)

        if muscle_scores:
            dorsal = tuple(self._project_muscles(muscle_scores, ("MDL", "MDR")))
            ventral = tuple(self._project_muscles(muscle_scores, ("MVL", "MVR")))
        else:
            # Compatibility path for test brains and snapshots without c302 muscle
            # traces. Real C302Brain results provide muscle_scores directly.
            dorsal = tuple(self._clamp(max(df[i], dr[i]) - 0.55 * vi[i]) for i in range(self.segments))
            ventral = tuple(self._clamp(max(vf[i], vr[i]) - 0.55 * di[i]) for i in range(self.segments))
        forward = mean(df + vf)
        reverse = mean(dr + vr)
        return MuscleActivation(dorsal, ventral, forward, reverse)


class FlexibleBody:
    """Small damped segment-chain model driven by dorsal/ventral muscles.

    The model is intentionally lightweight enough for the desktop overlay, but
    state is dynamic: muscle imbalance bends the chain, distance constraints
    preserve body length, motion emerges from the resulting curvature and
    longitudinal motor drive, and stretch/contact measurements are retained for
    the next c302 sensory frame.
    """

    def __init__(self, config: BodyPhysicsConfig | None = None):
        self.config = config or BodyPhysicsConfig()

    def initialise(self, worm: WormState) -> None:
        count = self.config.segments
        spacing = self.config.length_px / (count - 1)
        fx, fy = math.cos(worm.facing_radians), math.sin(worm.facing_radians)
        worm.body_points = [
            [worm.screen_x - fx * spacing * i, worm.screen_y - fy * spacing * i]
            for i in range(count)
        ]
        worm.body_velocities = [[0.0, 0.0] for _ in range(count)]
        worm.muscle_dorsal = [0.0] * count
        worm.muscle_ventral = [0.0] * count

    def step(
        self,
        worm: WormState,
        activation: MuscleActivation,
        dt: float,
        bounds: tuple[int, int, int, int],
    ) -> dict:
        cfg = self.config
        if len(worm.body_points) != cfg.segments or len(worm.body_velocities) != cfg.segments:
            self.initialise(worm)
        points = [[float(x), float(y)] for x, y in worm.body_points]
        velocities = [[float(x), float(y)] for x, y in worm.body_velocities]
        rest = cfg.length_px / (cfg.segments - 1)
        dt = max(1.0 / 240.0, min(0.1, float(dt)))
        sub_dt = dt / max(1, cfg.substeps)
        contact = 0.0

        for _ in range(max(1, cfg.substeps)):
            for index in range(cfg.segments):
                if index == 0:
                    tx = points[0][0] - points[1][0]
                    ty = points[0][1] - points[1][1]
                else:
                    tx = points[index - 1][0] - points[index][0]
                    ty = points[index - 1][1] - points[index][1]
                length = math.hypot(tx, ty) or 1.0
                tx, ty = tx / length, ty / length
                nx, ny = -ty, tx
                bend = activation.dorsal[index] - activation.ventral[index]
                velocities[index][0] += nx * bend * cfg.muscle_gain * sub_dt
                velocities[index][1] += ny * bend * cfg.muscle_gain * sub_dt

            motor_drive = activation.forward_drive - activation.reverse_drive
            curvature = mean(abs(a - b) for a, b in zip(activation.dorsal, activation.ventral))
            head_tx = points[0][0] - points[1][0]
            head_ty = points[0][1] - points[1][1]
            head_len = math.hypot(head_tx, head_ty) or 1.0
            propulsion = motor_drive * (0.2 + curvature) * cfg.propulsion_gain
            velocities[0][0] += head_tx / head_len * propulsion * sub_dt
            velocities[0][1] += head_ty / head_len * propulsion * sub_dt

            for index in range(cfg.segments):
                velocities[index][0] *= cfg.damping
                velocities[index][1] *= cfg.damping
                points[index][0] += velocities[index][0]
                points[index][1] += velocities[index][1]

            for _constraint in range(3):
                for index in range(1, cfg.segments):
                    dx = points[index][0] - points[index - 1][0]
                    dy = points[index][1] - points[index - 1][1]
                    distance = math.hypot(dx, dy) or rest
                    correction = (distance - rest) / distance * 0.5 * cfg.stiffness
                    cx, cy = dx * correction, dy * correction
                    points[index - 1][0] += cx
                    points[index - 1][1] += cy
                    points[index][0] -= cx
                    points[index][1] -= cy

            left, top, right, bottom = bounds
            for point, velocity in zip(points, velocities):
                before = tuple(point)
                point[0] = max(float(left), min(float(right - 1), point[0]))
                point[1] = max(float(top), min(float(bottom - 1), point[1]))
                if tuple(point) != before:
                    contact = 1.0
                    velocity[0] *= -0.25
                    velocity[1] *= -0.25

        lengths = [math.dist(points[i - 1], points[i]) for i in range(1, len(points))]
        stretch = mean(abs(length - rest) / rest for length in lengths) if lengths else 0.0
        turns: list[float] = []
        for index in range(1, len(points) - 1):
            ax, ay = points[index - 1][0] - points[index][0], points[index - 1][1] - points[index][1]
            bx, by = points[index][0] - points[index + 1][0], points[index][1] - points[index + 1][1]
            denom = (math.hypot(ax, ay) * math.hypot(bx, by)) or 1.0
            turns.append(math.acos(max(-1.0, min(1.0, (ax * bx + ay * by) / denom))))

        worm.body_points = points
        worm.body_velocities = velocities
        worm.muscle_dorsal = list(activation.dorsal)
        worm.muscle_ventral = list(activation.ventral)
        worm.proprioception = max(0.0, min(1.0, mean(turns) / math.pi + stretch)) if turns else stretch
        worm.body_contact = contact
        worm.screen_x, worm.screen_y = points[0]
        dx, dy = points[0][0] - points[1][0], points[0][1] - points[1][1]
        if math.hypot(dx, dy) > 1e-6:
            worm.facing_radians = math.atan2(dy, dx)
        return {
            "head": tuple(points[0]),
            "proprioception": worm.proprioception,
            "contact": contact,
            "forward_drive": activation.forward_drive,
            "reverse_drive": activation.reverse_drive,
        }
