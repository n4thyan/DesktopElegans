from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import TextIO

from .models import WormState

PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class ProcessTransportConfig:
    enabled: bool = False
    max_children: int = 2
    max_generation: int = 1
    startup_timeout_sec: float = 5.0
    shutdown_timeout_sec: float = 3.0


@dataclass
class ProcessChild:
    worm_id: str
    process: subprocess.Popen[str]
    responses: queue.Queue[dict]
    reader: threading.Thread


class LocalProcessTransport:
    """Bounded JSON-lines transport to supervised local child processes.

    It deliberately has no discovery, sockets, self-copying, shell execution, or
    autonomous descendant spawning. A child receives exactly one validated worm
    snapshot over stdin and remains owned by this transport until shutdown.
    """

    def __init__(self, config: ProcessTransportConfig | None = None):
        self.config = config or ProcessTransportConfig()
        self._children: dict[str, ProcessChild] = {}
        self._lock = threading.RLock()

    @property
    def child_count(self) -> int:
        with self._lock:
            return sum(child.process.poll() is None for child in self._children.values())

    def has_child(self, worm_id: str) -> bool:
        with self._lock:
            child = self._children.get(worm_id)
            return bool(child and child.process.poll() is None)

    def can_spawn(self, generation: int) -> bool:
        if not self.config.enabled or generation > self.config.max_generation:
            return False
        with self._lock:
            self.reap()
            return self.child_count < self.config.max_children

    def spawn(self, worm: WormState) -> bool:
        if not self.can_spawn(worm.generation):
            return False
        with self._lock:
            self.reap()
            if worm.worm_id in self._children or self.child_count >= self.config.max_children:
                return False
            command = [sys.executable, "-m", "nematode_mind", "process-child"]
            env = dict(os.environ)
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=env,
            )
            responses: queue.Queue[dict] = queue.Queue()
            reader = threading.Thread(
                target=self._read_responses,
                args=(process.stdout, responses),
                daemon=True,
                name=f"worm-process-reader-{worm.worm_id}",
            )
            reader.start()
            child = ProcessChild(worm.worm_id, process, responses, reader)
            self._children[worm.worm_id] = child
            try:
                self._send(child, {
                    "protocol": PROTOCOL_VERSION,
                    "command": "init",
                    "worm": worm.to_dict(),
                })
            except (BrokenPipeError, OSError, RuntimeError) as exc:
                self._children.pop(worm.worm_id, None)
                self._terminate(child)
                detail = self._stderr_text(process)
                child.reader.join(timeout=0.5)
                self._close_pipes(process)
                raise RuntimeError(
                    f"process child {worm.worm_id} failed during startup transport: {detail}"
                ) from exc
        try:
            response = responses.get(timeout=max(0.1, self.config.startup_timeout_sec))
        except queue.Empty as exc:
            self._terminate(child)
            self._close_pipes(child.process)
            with self._lock:
                self._children.pop(worm.worm_id, None)
            detail = self._stderr_text(process)
            raise RuntimeError(f"process child {worm.worm_id} did not become ready: {detail}") from exc
        if response.get("event") != "ready" or response.get("worm_id") != worm.worm_id:
            self._terminate(child)
            self._close_pipes(child.process)
            with self._lock:
                self._children.pop(worm.worm_id, None)
            raise RuntimeError(f"process child rejected {worm.worm_id}: {response}")
        return True

    @staticmethod
    def _read_responses(stream: TextIO | None, responses: queue.Queue[dict]) -> None:
        if stream is None:
            return
        for line in stream:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = {"event": "protocol-error", "line": line.rstrip("\r\n")}
            if isinstance(payload, dict):
                responses.put(payload)

    @staticmethod
    def _send(child: ProcessChild, payload: dict) -> None:
        if child.process.stdin is None or child.process.poll() is not None:
            raise RuntimeError(f"process child {child.worm_id} is not running")
        child.process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        child.process.stdin.flush()

    @staticmethod
    def _stderr_text(process: subprocess.Popen[str]) -> str:
        if process.stderr is None or process.poll() is None:
            return "no diagnostic output"
        try:
            return process.stderr.read().strip() or f"exit={process.returncode}"
        except Exception:
            return f"exit={process.returncode}"

    def sync_snapshot(self, worm: WormState) -> None:
        """Mirror parent-authoritative state into its supervised process."""
        with self._lock:
            child = self._children.get(worm.worm_id)
            if child is None or child.process.poll() is not None:
                return
            self._send(child, {
                "protocol": PROTOCOL_VERSION,
                "command": "update",
                "worm": worm.to_dict(),
            })

    def stop_child(self, worm_id: str) -> None:
        with self._lock:
            child = self._children.pop(worm_id, None)
        if child is None:
            return
        if child.process.poll() is None:
            try:
                self._send(child, {"protocol": PROTOCOL_VERSION, "command": "shutdown"})
                child.process.wait(timeout=max(0.1, self.config.shutdown_timeout_sec))
            except (BrokenPipeError, RuntimeError, subprocess.TimeoutExpired):
                self._terminate(child)
        child.reader.join(timeout=0.5)
        self._close_pipes(child.process)

    def snapshot(self, worm_id: str, timeout: float = 2.0) -> WormState:
        with self._lock:
            child = self._children[worm_id]
            try:
                self._send(child, {"protocol": PROTOCOL_VERSION, "command": "snapshot"})
            except (BrokenPipeError, OSError, RuntimeError) as exc:
                self._children.pop(worm_id, None)
                self._terminate(child)
                child.reader.join(timeout=0.5)
                self._close_pipes(child.process)
                raise RuntimeError(f"process child {worm_id} transport failed") from exc
        try:
            response = child.responses.get(timeout=max(0.1, timeout))
        except queue.Empty as exc:
            raise RuntimeError(f"process child {worm_id} did not return a snapshot") from exc
        if response.get("event") != "snapshot":
            raise RuntimeError(f"unexpected process child response: {response}")
        return WormState.from_dict(response["worm"])

    def reap(self) -> None:
        with self._lock:
            stale = [worm_id for worm_id, child in self._children.items() if child.process.poll() is not None]
            for worm_id in stale:
                child = self._children.pop(worm_id)
                child.reader.join(timeout=0.5)
                self._close_pipes(child.process)

    def stop(self) -> None:
        with self._lock:
            children = list(self._children.values())
            self._children.clear()
        for child in children:
            if child.process.poll() is None:
                try:
                    self._send(child, {"protocol": PROTOCOL_VERSION, "command": "shutdown"})
                except (BrokenPipeError, RuntimeError):
                    pass
            try:
                child.process.wait(timeout=max(0.1, self.config.shutdown_timeout_sec))
            except subprocess.TimeoutExpired:
                self._terminate(child)
            child.reader.join(timeout=0.5)
            self._close_pipes(child.process)

    @staticmethod
    def _terminate(child: ProcessChild) -> None:
        if child.process.poll() is None:
            child.process.terminate()
            try:
                child.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                child.process.kill()
                child.process.wait(timeout=1.0)

    @staticmethod
    def _close_pipes(process: subprocess.Popen[str]) -> None:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def process_child_main() -> int:
    """Run the inert, supervised endpoint for :class:`LocalProcessTransport`."""
    worm: WormState | None = None
    for line in sys.stdin:
        try:
            message = json.loads(line)
            if not isinstance(message, dict) or message.get("protocol") != PROTOCOL_VERSION:
                raise ValueError("unsupported process transport protocol")
            command = message.get("command")
            if command == "init":
                if worm is not None:
                    raise ValueError("child was already initialised")
                worm = WormState.from_dict(message["worm"])
                print(json.dumps({"event": "ready", "worm_id": worm.worm_id, "pid": os.getpid()}), flush=True)
            elif command == "update":
                if worm is None:
                    raise ValueError("child is not initialised")
                updated = WormState.from_dict(message["worm"])
                if updated.worm_id != worm.worm_id:
                    raise ValueError("cannot change child organism identity")
                worm = updated
            elif command == "snapshot":
                if worm is None:
                    raise ValueError("child is not initialised")
                print(json.dumps({"event": "snapshot", "worm": worm.to_dict()}), flush=True)
            elif command == "shutdown":
                print(json.dumps({"event": "stopped", "worm_id": worm.worm_id if worm else None}), flush=True)
                return 0
            else:
                raise ValueError(f"unknown command: {command}")
        except Exception as exc:
            print(json.dumps({"event": "error", "error": str(exc)}), flush=True)
            return 2
    return 0
