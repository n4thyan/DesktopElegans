from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .models import WormState


@dataclass(frozen=True)
class ColonySandboxConfig:
    enabled: bool = False
    root: str = "~/Desktop/WormHabitat"
    max_living_worms: int = 12
    max_generations: int = 3
    max_disk_bytes: int = 16 * 1024 * 1024
    max_state_bytes: int = 64 * 1024
    cleanup_timeout_sec: float = 5.0
    per_worm_directories: bool = True


def safe_sandbox_path(root: str | Path, *parts: str) -> Path:
    base = Path(root).expanduser().resolve()
    if not parts:
        return base
    for part in parts:
        text = str(part)
        windows = PureWindowsPath(text)
        if windows.is_absolute() or windows.drive or any(piece == ".." for piece in windows.parts):
            raise ValueError("sandbox path escape rejected")
    candidate = base.joinpath(*parts).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError("sandbox path escape rejected") from exc
    return candidate


class ColonySandbox:
    """Strictly bounded per-organism state under one marked sandbox root."""

    MARKER = ".worm-habitat"
    MARKER_CONTENT = "nematode-wormmind colony\n"

    def __init__(self, config: ColonySandboxConfig | None = None) -> None:
        self.config = config or ColonySandboxConfig()
        self.root = safe_sandbox_path(self.config.root)
        self._validate_root()
        if self.config.enabled:
            marker = safe_sandbox_path(self.root, self.MARKER)
            marker_size = len(self.MARKER_CONTENT.encode("utf-8"))
            if marker_size > self.config.max_disk_bytes:
                raise ValueError("sandbox disk limit cannot contain ownership marker")
            if self.root.exists():
                if not self.root.is_dir():
                    raise ValueError("replication.sandbox_root must be a directory")
                entries = list(self.root.iterdir())
                if marker.is_file():
                    if marker.read_text(encoding="utf-8") != self.MARKER_CONTENT:
                        raise ValueError("sandbox ownership marker is invalid")
                elif entries:
                    raise ValueError("refusing to adopt an existing non-empty unmarked sandbox root")
                else:
                    marker.write_text(self.MARKER_CONTENT, encoding="utf-8")
            else:
                self.root.mkdir(parents=True, exist_ok=False)
                marker.write_text(self.MARKER_CONTENT, encoding="utf-8")

    def _validate_root(self) -> None:
        home = Path.home().resolve()
        broad_roots = {
            Path(self.root.anchor).resolve(),
            home,
            home.parent,
        }
        broad_roots.update((home / name).resolve() for name in ("Desktop", "Documents", "Downloads"))
        if self.root in broad_roots:
            raise ValueError("replication.sandbox_root is too broad")

    def can_admit(self, worm: WormState, living_count: int) -> bool:
        return (
            int(living_count) < self.config.max_living_worms
            and int(worm.generation) <= self.config.max_generations
        )

    def _state_path(self, worm_id: str) -> Path:
        if not worm_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in worm_id):
            raise ValueError("invalid organism id for sandbox state")
        if self.config.per_worm_directories:
            directory = safe_sandbox_path(self.root, "worms", worm_id)
            return safe_sandbox_path(directory, "state.json")
        return safe_sandbox_path(self.root, "worms", f"{worm_id}.json")

    def _disk_usage(self) -> int:
        if not self.root.exists():
            return 0
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())

    def persist(self, worm: WormState) -> Path:
        if not self.config.enabled:
            raise RuntimeError("colony sandbox persistence is disabled")
        payload = json.dumps(worm.to_dict(), indent=2, sort_keys=True).encode("utf-8") + b"\n"
        if len(payload) > self.config.max_state_bytes:
            raise ValueError("per-worm state size limit exceeded")
        target = self._state_path(worm.worm_id)
        existing = target.stat().st_size if target.exists() else 0
        projected = self._disk_usage() - existing + len(payload)
        if projected > self.config.max_disk_bytes:
            raise ValueError("sandbox disk usage limit exceeded")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = safe_sandbox_path(target.parent, "state.tmp" if self.config.per_worm_directories else f"{worm.worm_id}.tmp")
        temporary.write_bytes(payload)
        temporary.replace(target)
        return target

    def cleanup(self) -> bool:
        if not self.root.exists():
            return True
        marker = safe_sandbox_path(self.root, self.MARKER)
        if not marker.is_file() or marker.read_text(encoding="utf-8") != self.MARKER_CONTENT:
            raise RuntimeError("refusing to clean an unmarked sandbox root")
        deadline = time.monotonic() + max(0.001, self.config.cleanup_timeout_sec)

        def check_deadline() -> None:
            if time.monotonic() > deadline:
                raise TimeoutError("sandbox cleanup exceeded configured timeout")

        # Keep the ownership marker until every generated entry is gone. A
        # timeout therefore leaves a still-identifiable, safely resumable root.
        entries = [path for path in self.root.rglob("*") if path != marker]
        for path in sorted((entry for entry in entries if entry.is_file() or entry.is_symlink()), key=lambda item: len(item.parts), reverse=True):
            check_deadline()
            path.unlink(missing_ok=True)
        for path in sorted((entry for entry in entries if entry.is_dir() and not entry.is_symlink()), key=lambda item: len(item.parts), reverse=True):
            check_deadline()
            path.rmdir()
        check_deadline()
        marker.unlink()
        self.root.rmdir()
        check_deadline()
        return not self.root.exists()
