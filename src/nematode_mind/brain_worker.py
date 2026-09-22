from __future__ import annotations

import copy
import queue
import threading
from dataclasses import dataclass
from typing import Protocol

from .models import BrainResult, HostState, WormState


class Brain(Protocol):
    def step(self, environment: HostState, organism: WormState | None = None) -> BrainResult: ...


@dataclass(frozen=True)
class BrainOutcome:
    worm_id: str
    result: BrainResult | None = None
    error: BaseException | None = None


@dataclass(frozen=True)
class _BrainRequest:
    worm_id: str
    habitat: HostState
    worm: WormState


class BrainWorker:
    """Serialize slow c302 calls on one background worker thread."""

    def __init__(self, brain: Brain):
        self.brain = brain
        self._requests: queue.Queue[_BrainRequest | None] = queue.Queue()
        self._outcomes: queue.Queue[BrainOutcome] = queue.Queue()
        self._pending: set[str] = set()
        self._pending_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        while True:
            try:
                self._requests.get_nowait()
            except queue.Empty:
                break
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="c302-brain-worker")
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> bool:
        self._stop.set()
        self._requests.put(None)
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))
        stopped = thread is None or not thread.is_alive()
        if stopped:
            self._thread = None
        with self._pending_lock:
            self._pending.clear()
        return stopped

    @property
    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def submit(self, worm: WormState, habitat: HostState) -> bool:
        """Queue one request unless that worm already has one in flight."""
        with self._pending_lock:
            if worm.worm_id in self._pending:
                return False
            self._pending.add(worm.worm_id)
        self._requests.put(_BrainRequest(
            worm_id=worm.worm_id,
            habitat=copy.deepcopy(habitat),
            worm=copy.deepcopy(worm),
        ))
        return True

    def drain(self) -> list[BrainOutcome]:
        outcomes: list[BrainOutcome] = []
        while True:
            try:
                outcome = self._outcomes.get_nowait()
            except queue.Empty:
                break
            with self._pending_lock:
                self._pending.discard(outcome.worm_id)
            outcomes.append(outcome)
        return outcomes

    def pending(self, worm_id: str) -> bool:
        with self._pending_lock:
            return worm_id in self._pending

    def _run(self) -> None:
        while not self._stop.is_set():
            request = self._requests.get()
            if request is None:
                break
            try:
                result = self.brain.step(request.habitat, request.worm)
                outcome = BrainOutcome(request.worm_id, result=result)
            except BaseException as exc:
                outcome = BrainOutcome(request.worm_id, error=exc)
            self._outcomes.put(outcome)
