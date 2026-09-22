from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path


class EventLogger:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: dict) -> None:
        event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")
