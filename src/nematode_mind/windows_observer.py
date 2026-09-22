from __future__ import annotations

import ctypes
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class DesktopObservation:
    """Bounded live Windows context plus non-content window metadata."""

    signals: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class WindowsDesktopObserver:
    """Read public Win32 desktop/window metadata without intercepting input.

    This observes cursor geometry, monitor placement, foreground/under-point
    windows, class names and top-level captions. It never reads files, browser
    page contents, controls, keystrokes, clipboard data, or application memory.
    """

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("WindowsDesktopObserver requires Windows")
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self._own_pid = os.getpid()

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _window_text(self, hwnd: int) -> str:
        length = min(512, max(0, int(self.user32.GetWindowTextLengthW(hwnd))))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value[:512]

    def _window_class(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value[:255]

    def _window_pid(self, hwnd: int) -> int:
        pid = ctypes.c_ulong()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def _process_name(self, pid: int) -> str:
        process_query_limited_information = 0x1000
        handle = self.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            query = getattr(self.kernel32, "QueryFullProcessImageNameW", None)
            if query and query(handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).name[:255]
            return ""
        finally:
            self.kernel32.CloseHandle(handle)

    def _window_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        from ctypes import wintypes

        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    def _window_at(self, x: float, y: float) -> int:
        """Return the first usable top-level window under a virtual point.

        EnumWindows is z-ordered. Transparent/no-activate windows and this
        process (the worm overlay) are skipped, so observation sees through the
        overlay without changing or activating anything underneath.
        """
        from ctypes import wintypes

        found: list[int] = []
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        get_long = getattr(self.user32, "GetWindowLongPtrW", self.user32.GetWindowLongW)
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_long.restype = ctypes.c_ssize_t
        ws_ex_transparent = 0x00000020
        ws_ex_noactivate = 0x08000000

        @enum_proc
        def callback(hwnd, _lparam):
            hwnd = int(hwnd)
            if not self.user32.IsWindowVisible(hwnd) or self.user32.IsIconic(hwnd):
                return True
            if self._window_pid(hwnd) == self._own_pid:
                return True
            style = int(get_long(hwnd, -20))
            if style & (ws_ex_transparent | ws_ex_noactivate):
                return True
            rect = self._window_rect(hwnd)
            if rect and rect[0] <= x < rect[2] and rect[1] <= y < rect[3]:
                found.append(hwnd)
                return False
            return True

        self.user32.EnumWindows(callback, 0)
        if found:
            return found[0]
        return int(self.user32.GetShellWindow() or self.user32.GetDesktopWindow())

    def _monitors(self) -> list[tuple[int, tuple[int, int, int, int]]]:
        from ctypes import wintypes

        monitors: list[tuple[int, tuple[int, int, int, int]]] = []
        monitor_proc = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )

        @monitor_proc
        def callback(hmonitor, _hdc, rect, _lparam):
            value = rect.contents
            monitors.append((
                int(hmonitor),
                (int(value.left), int(value.top), int(value.right), int(value.bottom)),
            ))
            return True

        self.user32.EnumDisplayMonitors(0, None, callback, 0)
        monitors.sort(key=lambda item: (item[1][0], item[1][1]))
        return monitors

    def observe(self, x: float, y: float) -> DesktopObservation:
        from ctypes import wintypes

        cursor = wintypes.POINT()
        self.user32.GetCursorPos(ctypes.byref(cursor))
        distance = math.hypot(float(cursor.x) - x, float(cursor.y) - y)
        cursor_proximity = self._clamp(1.0 - distance / 240.0)

        monitors = self._monitors()
        monitor_index = 0
        monitor_rect = None
        for index, (_handle, rect) in enumerate(monitors):
            if rect[0] <= x < rect[2] and rect[1] <= y < rect[3]:
                monitor_index = index
                monitor_rect = rect
                break
        if monitor_rect is None and monitors:
            monitor_rect = monitors[0][1]
        monitor_position = 0.0 if len(monitors) <= 1 else monitor_index / (len(monitors) - 1)

        surface = self._window_at(x, y)
        foreground = int(self.user32.GetForegroundWindow() or 0)
        shell = int(self.user32.GetShellWindow() or 0)
        desktop = int(self.user32.GetDesktopWindow() or 0)
        surface_root = int(self.user32.GetAncestor(surface, 2) or surface)  # GA_ROOT
        foreground_root = int(self.user32.GetAncestor(foreground, 2) or foreground) if foreground else 0
        foreground_overlap = 1.0 if surface_root and surface_root == foreground_root else 0.0
        surface_kind = "desktop" if surface_root in {shell, desktop} else "window"

        surface_pid = self._window_pid(surface_root) if surface_root else 0
        foreground_pid = self._window_pid(foreground_root) if foreground_root else 0
        signals = {
            "cursor_proximity": cursor_proximity,
            "monitor_position": self._clamp(monitor_position),
            "monitor_count": self._clamp(len(monitors) / 8.0),
            "window_presence": 0.0 if surface_kind == "desktop" else 1.0,
            "foreground_overlap": foreground_overlap,
        }
        metadata = {
            "monitor_index": monitor_index,
            "monitor_count": len(monitors),
            "monitor_rect": list(monitor_rect) if monitor_rect else None,
            "cursor_x": int(cursor.x),
            "cursor_y": int(cursor.y),
            "surface_kind": surface_kind,
            "surface_hwnd": surface_root,
            "surface_class": self._window_class(surface_root) if surface_root else "",
            "surface_process": self._process_name(surface_pid) if surface_pid else "",
            "surface_title": self._window_text(surface_root) if surface_root else "",
            "foreground_hwnd": foreground_root,
            "foreground_class": self._window_class(foreground_root) if foreground_root else "",
            "foreground_process": self._process_name(foreground_pid) if foreground_pid else "",
            "foreground_title": self._window_text(foreground_root) if foreground_root else "",
        }
        return DesktopObservation(signals=signals, metadata=metadata)
