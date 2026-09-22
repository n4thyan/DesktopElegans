from __future__ import annotations
import json
from collections import Counter
from pathlib import Path
from statistics import mean


def summarize_log(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "events": 0}
    events = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    if not events:
        return {"path": str(p), "events": 0}

    actions = Counter(str(e.get("action", "unknown")) for e in events)
    hosts = Counter(str(e.get("host", "unknown")) for e in events)
    confidence = [float(e.get("confidence", 0.0)) for e in events]
    energy = [float((e.get("organism") or {}).get("energy", 0.0)) for e in events]
    stress = [float((e.get("organism") or {}).get("stress", 0.0)) for e in events]
    return {
        "path": str(p),
        "events": len(events),
        "actions": dict(actions),
        "hosts_observed": dict(hosts),
        "mean_confidence": round(mean(confidence), 6),
        "final_energy": energy[-1] if energy else None,
        "final_stress": stress[-1] if stress else None,
        "first_tick": events[0].get("tick"),
        "last_tick": events[-1].get("tick"),
    }
