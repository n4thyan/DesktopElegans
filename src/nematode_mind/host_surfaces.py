from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .desktop_environment import desktop_geometry
from .models import WormState


@dataclass(frozen=True)
class HostSurface:
    """Public geometry/lifecycle metadata for one inhabitable Win32 surface."""

    host_id: str
    hwnd: int
    rect: tuple[int, int, int, int]
    visible: bool
    minimized: bool
    is_desktop: bool
    z_order: int
    title: str = ""
    class_name: str = ""
    process_name: str = ""
    monitor: int = 0

    @property
    def left(self) -> int:
        return self.rect[0]

    @property
    def top(self) -> int:
        return self.rect[1]

    @property
    def right(self) -> int:
        return self.rect[2]

    @property
    def bottom(self) -> int:
        return self.rect[3]

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


class HostSurfaceCatalog:
    """Immutable lookup view ordered from front to back."""

    def __init__(self, surfaces: Iterable[HostSurface]):
        ordered = sorted(surfaces, key=lambda surface: surface.z_order)
        desktops = [surface for surface in ordered if surface.is_desktop]
        if not desktops:
            raise ValueError("host surface catalog requires a desktop surface")
        self.surfaces = tuple(ordered)
        self.desktop = desktops[0]
        self._by_id = {surface.host_id: surface for surface in ordered}
        self._by_hwnd = {surface.hwnd: surface for surface in ordered}

    def for_id(self, host_id: str) -> HostSurface | None:
        return self._by_id.get(str(host_id))

    def for_hwnd(self, hwnd: int) -> HostSurface | None:
        return self._by_hwnd.get(int(hwnd))

    def surface_at(self, x: float, y: float) -> HostSurface:
        for surface in self.surfaces:
            if surface.is_desktop or not surface.visible or surface.minimized:
                continue
            if surface.contains(x, y):
                return surface
        return self.desktop


def _shift_body(worm: WormState, dx: float, dy: float) -> None:
    if not worm.body_points:
        return
    worm.body_points = [
        [float(point[0]) + dx, float(point[1]) + dy]
        for point in worm.body_points
    ]


def bind_worm_to_surface(worm: WormState, surface: HostSurface) -> None:
    """Bind at the current physical point, clamped to the host client bounds."""

    local_x = max(0.0, min(max(0.0, surface.width - 1.0), worm.screen_x - surface.left))
    local_y = max(0.0, min(max(0.0, surface.height - 1.0), worm.screen_y - surface.top))
    target_x = surface.left + local_x
    target_y = surface.top + local_y
    _shift_body(worm, target_x - worm.screen_x, target_y - worm.screen_y)
    worm.previous_host_id = worm.host_id if worm.host_id != surface.host_id else worm.previous_host_id
    worm.host_id = surface.host_id
    worm.host_hwnd = int(surface.hwnd)
    worm.host_local_x = local_x
    worm.host_local_y = local_y
    worm.screen_x = target_x
    worm.screen_y = target_y
    worm.metadata["host_surface"] = {
        "hwnd": int(surface.hwnd),
        "desktop": bool(surface.is_desktop),
        "title": surface.title,
        "class": surface.class_name,
        "process": surface.process_name,
    }


def sync_worm_to_surface(worm: WormState, surface: HostSurface) -> bool:
    """Follow host movement without changing the organism's local body geometry."""

    if not surface.visible or surface.minimized or surface.width <= 0 or surface.height <= 0:
        return False
    local_x = max(0.0, min(max(0.0, surface.width - 1.0), float(worm.host_local_x)))
    local_y = max(0.0, min(max(0.0, surface.height - 1.0), float(worm.host_local_y)))
    target_x = surface.left + local_x
    target_y = surface.top + local_y
    _shift_body(worm, target_x - worm.screen_x, target_y - worm.screen_y)
    worm.screen_x = target_x
    worm.screen_y = target_y
    worm.host_local_x = local_x
    worm.host_local_y = local_y
    worm.host_hwnd = int(surface.hwnd)
    return True


def update_worm_local_coordinates(worm: WormState, surface: HostSurface) -> None:
    worm.host_local_x = max(0.0, min(max(0.0, surface.width - 1.0), worm.screen_x - surface.left))
    worm.host_local_y = max(0.0, min(max(0.0, surface.height - 1.0), worm.screen_y - surface.top))
    worm.host_hwnd = int(surface.hwnd)


class WindowsHostSurfaceManager:
    """Enumerate generic top-level Win32 client surfaces and their lifecycle."""

    GW_HWNDPREV = 3

    def __init__(self, refresh_interval_sec: float = 0.05) -> None:
        if os.name != "nt":
            raise RuntimeError("WindowsHostSurfaceManager requires Windows")
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self.dwmapi = ctypes.windll.dwmapi
        self.own_pid = os.getpid()
        self._overlay_hwnds: set[int] = set()
        self._last_by_hwnd: dict[int, HostSurface] = {}
        self._catalog: HostSurfaceCatalog | None = None
        self._refresh_interval_sec = max(0.0, float(refresh_interval_sec))
        self._last_refresh_at = 0.0

    def register_overlay(self, hwnd: int) -> None:
        self._overlay_hwnds.add(int(hwnd))

    def unregister_overlay(self, hwnd: int) -> None:
        self._overlay_hwnds.discard(int(hwnd))

    def _pid(self, hwnd: int) -> int:
        pid = ctypes.c_ulong()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def _text(self, hwnd: int) -> str:
        length = min(512, max(0, int(self.user32.GetWindowTextLengthW(hwnd))))
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value[:512]

    def _class(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value[:255]

    def _process_name(self, pid: int) -> str:
        process_query_limited_information = 0x1000
        handle = self.kernel32.OpenProcess(process_query_limited_information, False, int(pid))
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

    def _is_cloaked(self, hwnd: int) -> bool:
        """Return whether DWM is retaining a non-visible shell/virtual window."""

        cloaked = ctypes.c_ulong()
        dwmwa_cloaked = 14
        result = self.dwmapi.DwmGetWindowAttribute(
            int(hwnd), dwmwa_cloaked, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        )
        return int(result) == 0 and bool(cloaked.value)

    def _client_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        from ctypes import wintypes

        rect = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 1 or height <= 1:
            return None
        if int(origin.x) <= -30000 or int(origin.y) <= -30000:
            return None
        return int(origin.x), int(origin.y), int(origin.x + width), int(origin.y + height)

    def refresh(self, force: bool = False) -> HostSurfaceCatalog:
        from ctypes import wintypes

        now = time.monotonic()
        if (
            not force
            and self._catalog is not None
            and now - self._last_refresh_at < self._refresh_interval_sec
        ):
            return self._catalog

        surfaces: list[HostSurface] = []
        seen_hwnds: set[int] = set()
        desktop_hwnd = int(self.user32.GetShellWindow() or self.user32.GetDesktopWindow())
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @enum_proc
        def callback(hwnd, _lparam):
            value = int(hwnd)
            if (
                value == desktop_hwnd
                or value in self._overlay_hwnds
                or self._pid(value) == self.own_pid
                or self._is_cloaked(value)
            ):
                return True
            previous = self._last_by_hwnd.get(value)
            visible = bool(self.user32.IsWindowVisible(value))
            minimized = bool(self.user32.IsIconic(value))
            if not visible and not minimized:
                return True
            # Owned/tool windows are transient UI, not independent habitats.
            if self.user32.GetWindow(value, 4):  # GW_OWNER
                return True
            get_long = getattr(self.user32, "GetWindowLongPtrW", self.user32.GetWindowLongW)
            if int(get_long(value, -20)) & 0x00000080:  # WS_EX_TOOLWINDOW
                return True
            rect = self._client_rect(value)
            if rect is None:
                if previous is None:
                    return True
                rect = previous.rect
            pid = self._pid(value)
            seen_hwnds.add(value)
            surface = HostSurface(
                host_id=f"window:{value}",
                hwnd=value,
                rect=rect,
                visible=visible,
                minimized=minimized,
                is_desktop=False,
                z_order=len(surfaces),
                title=self._text(value),
                class_name=previous.class_name if previous is not None else self._class(value),
                process_name=previous.process_name if previous is not None else self._process_name(pid),
                monitor=int(self.user32.MonitorFromWindow(value, 2) or 0),  # MONITOR_DEFAULTTONEAREST
            )
            surfaces.append(surface)
            if not surface.minimized and surface.visible:
                self._last_by_hwnd[value] = surface
            return True

        self.user32.EnumWindows(callback, 0)
        self._last_by_hwnd = {
            hwnd: surface for hwnd, surface in self._last_by_hwnd.items()
            if hwnd in seen_hwnds
        }
        left, top, width, height = desktop_geometry()
        surfaces.append(HostSurface(
            host_id=f"desktop:{desktop_hwnd}",
            hwnd=desktop_hwnd,
            rect=(left, top, left + width, top + height),
            visible=True,
            minimized=False,
            is_desktop=True,
            z_order=1_000_000,
            title=self._text(desktop_hwnd),
            class_name=self._class(desktop_hwnd),
            process_name=self._process_name(self._pid(desktop_hwnd)),
        ))
        self._catalog = HostSurfaceCatalog(surfaces)
        self._last_refresh_at = now
        return self._catalog

    @property
    def catalog(self) -> HostSurfaceCatalog:
        return self._catalog or self.refresh()

    def insertion_anchor(self, host_hwnd: int) -> int:
        """Return the real window immediately above a host in normal z-order."""

        candidate = int(self.user32.GetWindow(int(host_hwnd), self.GW_HWNDPREV) or 0)
        visited: set[int] = set()
        while candidate and candidate not in visited:
            visited.add(candidate)
            if candidate not in self._overlay_hwnds:
                return candidate
            candidate = int(self.user32.GetWindow(candidate, self.GW_HWNDPREV) or 0)
        return 0
