from __future__ import annotations

import math
from typing import Callable

from .models import WormState

WIGGLE_HZ = 6.0
SEGMENT_COUNT = 13


def worm_segments(
    x: float,
    y: float,
    facing_radians: float,
    phase: float,
    body_length_px: float,
    count: int = SEGMENT_COUNT,
) -> list[tuple[float, float]]:
    """Return head-to-tail centers for a small sinusoidal worm body."""
    if body_length_px <= 0.0 or count <= 1:
        return [(float(x), float(y))]
    forward_x, forward_y = math.cos(facing_radians), math.sin(facing_radians)
    lateral_x, lateral_y = -forward_y, forward_x
    points: list[tuple[float, float]] = []
    for index in range(count):
        fraction = index / (count - 1)
        along = -fraction * body_length_px
        amplitude = (0.25 + 0.75 * fraction) * body_length_px * 0.16
        lateral = math.sin(phase + fraction * math.tau * 1.8) * amplitude
        points.append((
            x + forward_x * along + lateral_x * lateral,
            y + forward_y * along + lateral_y * lateral,
        ))
    return points


class WormRenderer:
    """Convert worm state into renderer-independent geometry and colour."""

    def __init__(
        self,
        body_length_mm: float = 1.0,
        dpi: float = 96.0,
        color_fn: Callable[[WormState], tuple[int, int, int]] | None = None,
    ):
        self.body_length_px = max(2.0, float(body_length_mm) * float(dpi) / 25.4)
        self.color_fn = color_fn or self.default_color

    @staticmethod
    def default_color(worm: WormState) -> tuple[int, int, int]:
        energy = max(0.0, min(1.0, worm.energy))
        stress = max(0.0, min(1.0, worm.stress))
        return (
            max(0, min(255, int(45 + 175 * energy + 35 * stress))),
            max(0, min(255, int(35 + 190 * energy * (1.0 - 0.45 * stress)))),
            max(0, min(255, int(35 + 95 * energy * (1.0 - 0.55 * stress)))),
        )

    def geometry(self, worm: WormState) -> list[tuple[float, float]]:
        if len(worm.body_points) >= 2:
            return [(float(point[0]), float(point[1])) for point in worm.body_points]
        return worm_segments(
            worm.screen_x,
            worm.screen_y,
            worm.facing_radians,
            worm.body_phase,
            self.body_length_px,
        )

    def color_hex(self, worm: WormState) -> str:
        red, green, blue = self.color_fn(worm)
        return f"#{red:02x}{green:02x}{blue:02x}"
