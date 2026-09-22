from __future__ import annotations
import json
from pathlib import Path

from .models import WormState


class StateStore:
    """Small crash-safe JSON snapshot store for the organism state."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, state: WormState) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def load(self) -> WormState:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("worm state snapshot must contain a JSON object")
        return WormState.from_dict(data)

    def exists(self) -> bool:
        return self.path.exists()
