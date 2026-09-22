from __future__ import annotations

import copy
import ctypes
import math
import os
import threading
from dataclasses import dataclass
from typing import Dict

from .desktop_environment import desktop_dpi
from .ecology import Resource
from .host_surfaces import HostSurface, WindowsHostSurfaceManager
from .models import WormState
from .renderer import WormRenderer


@dataclass
class _HostWindow:
    host_id: str
    toplevel: object
    canvas: object
    hwnd: int
    original_wndproc: int
    wndproc_callback: object


class DesktopOverlay:
    """Per-host transparent windows that participate in normal Win32 z-order.

    No overlay is topmost. Each host window is placed immediately above its
    inhabited Win32 surface and below every unrelated window already above that
    host. It is no-activate and returns HTTRANSPARENT for every pixel.
    """

    def __init__(
        self,
        body_length_mm: float = 1.0,
        visible: bool = True,
        fps: int = 60,
        title: str = "nematode-wormmind-desktop",
        debug_enabled: bool | None = None,
        debug_scale: int = 1,
        show_coordinates: bool = True,
        show_sensory_state: bool = False,
        arcade_growth: bool = False,
        max_visual_growth: float = 2.0,
        surface_manager=None,
    ):
        self._title = title
        self._visible = bool(visible)
        self._fps = max(15, min(144, int(fps)))
        self._body_length_mm = max(0.25, float(body_length_mm))
        self._debug_enabled = int(debug_scale) != 1 if debug_enabled is None else bool(debug_enabled)
        self._debug_scale = max(1, min(100, int(debug_scale))) if self._debug_enabled else 1
        self._show_coordinates = bool(show_coordinates)
        self._show_sensory_state = bool(show_sensory_state)
        self._arcade_growth = bool(arcade_growth)
        self._max_visual_growth = max(1.0, min(4.0, float(max_visual_growth)))
        self.surface_manager = surface_manager
        self._owner_thread: int | None = None
        self._root = None
        self._renderer: WormRenderer | None = None
        self._tk = None
        self._closed = False
        self._windows: dict[str, _HostWindow] = {}
        # Compatibility origin used by geometry-only tests.
        self._virtual_left = 0
        self._virtual_top = 0

    def _assert_owner(self) -> None:
        if self._owner_thread is not None and threading.get_ident() != self._owner_thread:
            raise RuntimeError("desktop overlay must be used from the thread that started it")

    def start(self) -> None:
        if not self._visible:
            return
        if os.name != "nt":
            raise RuntimeError("the transparent host overlay requires Windows")
        if self._root is not None:
            return
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("desktop overlay must be started on the process main thread")

        import tkinter as tk

        self._owner_thread = threading.get_ident()
        root = tk.Tk()
        try:
            root.withdraw()
            self._tk = tk
            self._root = root
            self._renderer = WormRenderer(body_length_mm=self._body_length_mm, dpi=desktop_dpi())
            self.surface_manager = self.surface_manager or WindowsHostSurfaceManager()
            self._closed = False
            root.update_idletasks()
            root.update()
        except Exception:
            try:
                root.destroy()
            finally:
                self._root = None
                self._owner_thread = None
            raise

    def stop(self) -> None:
        if self._root is None:
            return
        self._assert_owner()
        root = self._root
        for record in list(self._windows.values()):
            self._destroy_host_window(record)
        self._windows.clear()
        self._root = None
        self._renderer = None
        self._tk = None
        self._closed = True
        try:
            root.destroy()
        finally:
            try:
                root.update_idletasks()
            except Exception:
                pass

    def _create_host_window(self, surface: HostSurface) -> _HostWindow:
        if self._root is None or self._tk is None:
            raise RuntimeError("overlay is not started")
        tk = self._tk
        transparent = "#010203"
        top = tk.Toplevel(self._root)
        try:
            top.withdraw()
            top.title(f"{self._title}:{surface.host_id}")
            top.overrideredirect(True)
            top.wm_attributes("-transparentcolor", transparent)
            top.geometry(f"{max(1, surface.width)}x{max(1, surface.height)}+0+0")
            top.configure(bg=transparent)
            canvas = tk.Canvas(
                top,
                width=max(1, surface.width),
                height=max(1, surface.height),
                bg=transparent,
                highlightthickness=0,
                borderwidth=0,
            )
            canvas.pack(fill="both", expand=True)
            top.update_idletasks()
            hwnd, original, callback = self._make_click_through(
                top.winfo_id(), surface.hwnd, own_host=surface.is_desktop
            )
            record = _HostWindow(surface.host_id, top, canvas, hwnd, original, callback)
            self.surface_manager.register_overlay(hwnd)
            return record
        except Exception:
            top.destroy()
            raise

    def _destroy_host_window(self, record: _HostWindow) -> None:
        self._restore_wndproc(record)
        if self.surface_manager is not None:
            self.surface_manager.unregister_overlay(record.hwnd)
        try:
            record.toplevel.destroy()
        except Exception:
            pass

    def update_worms(self, worms: Dict[str, WormState], resources: Dict[str, Resource] | None = None) -> None:
        if not self._visible or self._root is None or self._closed:
            return
        self._assert_owner()
        renderer = self._renderer
        tk = self._tk
        if renderer is None or tk is None or self.surface_manager is None:
            return

        catalog = self.surface_manager.refresh()
        worm_groups: dict[str, list[WormState]] = {}
        for worm in worms.values():
            if not worm.alive:
                continue
            surface = catalog.for_id(worm.host_id) or catalog.for_hwnd(worm.host_hwnd) or catalog.desktop
            worm_groups.setdefault(surface.host_id, []).append(copy.deepcopy(worm))

        resource_groups: dict[str, list[Resource]] = {}
        for item in (resources or {}).values():
            surface = catalog.surface_at(item.x, item.y)
            resource_groups.setdefault(surface.host_id, []).append(item)

        active_ids = set(worm_groups) | set(resource_groups)
        self._reconcile_host_windows(catalog, active_ids)

        for host_id in active_ids:
            surface = catalog.for_id(host_id)
            if surface is None or not surface.visible or surface.minimized or surface.width <= 0 or surface.height <= 0:
                continue
            record = self._windows.get(host_id)
            if record is None:
                record = self._create_host_window(surface)
                self._windows[host_id] = record
            self._place_host_window(record, surface)
            self._draw_surface(
                record,
                surface,
                worm_groups.get(host_id, []),
                resource_groups.get(host_id, []),
                renderer,
                tk,
            )

        try:
            self._root.update_idletasks()
            self._root.update()
        except tk.TclError:
            self._closed = True

    def _reconcile_host_windows(self, catalog, active_ids: set[str]) -> None:
        """Hide dormant renderers and destroy HWND resources for closed hosts."""

        for host_id, record in list(self._windows.items()):
            surface = catalog.for_id(host_id)
            if surface is None:
                self._windows.pop(host_id, None)
                self._destroy_host_window(record)
            elif host_id not in active_ids or not surface.visible or surface.minimized:
                record.toplevel.withdraw()

    def _draw_surface(
        self,
        record: _HostWindow,
        surface: HostSurface,
        worms: list[WormState],
        resources: list[Resource],
        renderer: WormRenderer,
        tk,
    ) -> None:
        canvas = record.canvas
        canvas.configure(width=surface.width, height=surface.height)
        canvas.delete("all")
        for item in resources:
            x = float(item.x) - surface.left
            y = float(item.y) - surface.top
            radius = max(1.0, float(item.radius_px))
            canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill="#6bd96b", outline="#9bf59b", tags="resource",
            )
        for worm in worms:
            points = self._canvas_points(
                renderer.geometry(worm),
                visual_growth=self._visual_growth(worm),
                origin=(surface.left, surface.top),
            )
            if not points:
                continue
            flattened = [coordinate for point in points for coordinate in point]
            color = renderer.color_hex(worm)
            canvas.create_line(
                *flattened,
                fill=color,
                width=max(2, min(6, self._debug_scale)),
                smooth=True,
                splinesteps=12,
                capstyle=tk.ROUND,
                joinstyle=tk.ROUND,
                tags="worm",
            )
            head_x, head_y = points[0]
            head_radius = 1.5 if self._debug_scale == 1 else 4.0
            canvas.create_oval(
                head_x - head_radius, head_y - head_radius,
                head_x + head_radius, head_y + head_radius,
                fill=color, outline=color, tags="worm",
            )
            labels: list[str] = []
            if self._debug_enabled and self._show_coordinates:
                labels.append(
                    f"{worm.worm_id} host={surface.host_id} process={surface.process_name or 'desktop'}\n"
                    f"global=({worm.screen_x:.1f},{worm.screen_y:.1f}) "
                    f"local=({worm.host_local_x:.1f},{worm.host_local_y:.1f})\n"
                    f"monitor={surface.monitor} bounds={surface.rect}"
                )
            if self._debug_enabled and self._show_sensory_state:
                channels = worm.metadata.get("sensory_channels", {})
                labels.append(
                    f"E={worm.energy:.2f} food={float(channels.get('resource_proximity', 0.0)):.2f} "
                    f"peer={float(channels.get('peer_proximity', 0.0)):.2f}"
                )
            if labels:
                canvas.create_text(
                    head_x + 8, head_y + 8, text="\n".join(labels),
                    fill=color, anchor="nw", tags="worm",
                )

    def _visual_growth(self, worm: WormState) -> float:
        if not self._arcade_growth:
            return 1.0
        growth = 1.0 + max(0.0, math.log2(max(1.0, float(worm.ecology_mass)))) * 0.15
        return min(self._max_visual_growth, growth)

    def _canvas_points(
        self,
        points: list[tuple[float, float]],
        visual_growth: float = 1.0,
        origin: tuple[float, float] | None = None,
    ) -> list[tuple[float, float]]:
        if not points:
            return []
        head_x, head_y = points[0]
        scale = float(self._debug_scale) * max(1.0, min(self._max_visual_growth, float(visual_growth)))
        origin_x, origin_y = origin or (self._virtual_left, self._virtual_top)
        return [
            (
                head_x + (x - head_x) * scale - origin_x,
                head_y + (y - head_y) * scale - origin_y,
            )
            for x, y in points
        ]

    def _make_click_through(
        self, child_hwnd: int, host_hwnd: int, own_host: bool = False
    ) -> tuple[int, int, object]:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        long_ptr = ctypes.c_ssize_t
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_long.restype = long_ptr
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, long_ptr]
        set_long.restype = long_ptr
        hwnd = int(user32.GetParent(child_hwnd) or child_hwnd)

        gwl_exstyle = -20
        gwlp_wndproc = -4
        gwlp_hwndparent = -8
        ws_ex_transparent = 0x00000020
        ws_ex_toolwindow = 0x00000080
        ws_ex_layered = 0x00080000
        ws_ex_noactivate = 0x08000000
        style = int(get_long(hwnd, gwl_exstyle))
        set_long(hwnd, gwl_exstyle, style | ws_ex_transparent | ws_ex_toolwindow | ws_ex_layered | ws_ex_noactivate)
        # Do not make the renderer an owned popup of the host. Explorer can
        # defer/ignore SC_CLOSE while a foreign owned popup exists. Lifecycle
        # and z-order are reconciled explicitly by the surface manager instead.
        # The desktop shell is the exception: ownership keeps an HWND_BOTTOM
        # fallback above the wallpaper/icons while still below applications.
        if own_host:
            set_long(hwnd, gwlp_hwndparent, int(host_hwnd))
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)

        wm_nchittest = 0x0084
        wm_mouseactivate = 0x0021
        httransparent = -1
        ma_noactivate = 3
        wndproc_type = ctypes.WINFUNCTYPE(
            long_ptr, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )
        original = int(get_long(hwnd, gwlp_wndproc))
        user32.CallWindowProcW.argtypes = [
            ctypes.c_void_p, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
        ]
        user32.CallWindowProcW.restype = long_ptr

        @wndproc_type
        def wndproc(window, message, wparam, lparam):
            if message == wm_nchittest:
                return httransparent
            if message == wm_mouseactivate:
                return ma_noactivate
            return user32.CallWindowProcW(ctypes.c_void_p(original), window, message, wparam, lparam)

        ctypes.set_last_error(0)
        callback_address = ctypes.cast(wndproc, ctypes.c_void_p).value
        previous = int(set_long(hwnd, gwlp_wndproc, callback_address))
        if previous == 0 and ctypes.get_last_error() != 0:
            raise ctypes.WinError(ctypes.get_last_error())

        lwa_colorkey = 0x00000001
        if not user32.SetLayeredWindowAttributes(hwnd, 0x00030201, 255, lwa_colorkey):
            raise OSError("failed to apply host overlay transparency colour key")
        return hwnd, original, wndproc

    def _place_host_window(self, record: _HostWindow, surface: HostSurface) -> None:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        anchor = int(self.surface_manager.insertion_anchor(surface.hwnd) or 0)
        swp_noactivate = 0x0010
        swp_showwindow = 0x0040
        flags = swp_noactivate | swp_showwindow
        if user32.SetWindowPos(
            record.hwnd, wintypes.HWND(anchor),
            int(surface.left), int(surface.top), int(surface.width), int(surface.height),
            flags,
        ):
            return
        # UIPI can reject relative placement against an unrelated elevated
        # anchor. Ownership still keeps this popup above its host, while
        # HWND_BOTTOM keeps it below unrelated normal windows.
        hwnd_bottom = 1
        if not user32.SetWindowPos(
            record.hwnd, wintypes.HWND(hwnd_bottom),
            int(surface.left), int(surface.top), int(surface.width), int(surface.height),
            flags,
        ):
            raise ctypes.WinError()

    @staticmethod
    def _restore_wndproc(record: _HostWindow) -> None:
        try:
            user32 = ctypes.windll.user32
            set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            set_long.restype = ctypes.c_ssize_t
            set_long(record.hwnd, -4, record.original_wndproc)
        except Exception:
            pass
