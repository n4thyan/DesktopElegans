from __future__ import annotations

import ctypes
import json
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from PIL import ImageGrab

from nematode_mind.host_surfaces import WindowsHostSurfaceManager, bind_worm_to_surface, sync_worm_to_surface
from nematode_mind.models import WormState
from nematode_mind.overlay import DesktopOverlay

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".runtime" / "explorer-smoke"
OUT.mkdir(parents=True, exist_ok=True)
user32 = ctypes.windll.user32


def capture(name: str) -> str:
    path = OUT / f"{name}.png"
    ImageGrab.grab(all_screens=True).save(path)
    return str(path)


def explorer_hwnds(manager: WindowsHostSurfaceManager) -> set[int]:
    return {
        surface.hwnd for surface in manager.refresh(force=True).surfaces
        if not surface.is_desktop and surface.process_name.lower() == "explorer.exe"
    }


def z_order() -> list[int]:
    values: list[int] = []
    hwnd = int(user32.GetTopWindow(0) or 0)
    while hwnd:
        values.append(hwnd)
        hwnd = int(user32.GetWindow(hwnd, 2) or 0)
    return values


manager = WindowsHostSurfaceManager(refresh_interval_sec=0.0)
existing = explorer_hwnds(manager)
previous_foreground = int(user32.GetForegroundWindow() or 0)
subprocess.Popen(["explorer.exe", str(ROOT)])
window = None
overlay = None
result: dict = {"screenshots": {}}
try:
    deadline = time.monotonic() + 12.0
    while window is None and time.monotonic() < deadline:
        time.sleep(0.2)
        for surface in manager.refresh(force=True).surfaces:
            if (
                surface.hwnd not in existing
                and not surface.is_desktop
                and surface.process_name.lower() == "explorer.exe"
                and surface.class_name == "CabinetWClass"
            ):
                window = surface
                break
    if window is None:
        raise RuntimeError("new File Explorer host did not appear")

    worm = WormState("explorer-worm", window.host_id, screen_x=window.left + 220.0, screen_y=window.top + 180.0)
    bind_worm_to_surface(worm, window)
    worm.host_local_x = min(220.0, window.width - 1.0)
    worm.host_local_y = min(180.0, window.height - 1.0)
    sync_worm_to_surface(worm, window)
    worm.body_points = [[worm.screen_x, worm.screen_y], [worm.screen_x - 5, worm.screen_y], [worm.screen_x - 10, worm.screen_y + 1]]
    identity = id(worm)
    overlay = DesktopOverlay(debug_enabled=True, debug_scale=20, show_coordinates=True, surface_manager=manager)
    overlay.start()
    overlay.update_worms({worm.worm_id: worm})
    renderer = overlay._windows[window.host_id]
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    renderer_exstyle = int(get_long(renderer.hwnd, -20))
    initial_order = z_order()
    initial_local = [worm.host_local_x, worm.host_local_y]
    initial_monitor = window.monitor
    result["screenshots"]["initial"] = capture("01-explorer-hosted")

    virtual_left = user32.GetSystemMetrics(76)
    virtual_top = user32.GetSystemMetrics(77)
    target_left = virtual_left + 120 if window.left >= 0 and virtual_left < 0 else max(40, virtual_left + 120)
    target_top = virtual_top + 100
    if not user32.SetWindowPos(window.hwnd, 0, target_left, target_top, 900, 620, 0x0010 | 0x0040):
        raise ctypes.WinError()
    time.sleep(0.8)
    moved = manager.refresh(force=True).for_hwnd(window.hwnd)
    if moved is None:
        raise RuntimeError("Explorer host disappeared after move")
    sync_worm_to_surface(worm, moved)
    overlay.update_worms({worm.worm_id: worm})
    result["screenshots"]["moved"] = capture("02-explorer-moved")

    user32.ShowWindowAsync(window.hwnd, 6)
    time.sleep(0.8)
    minimized = manager.refresh(force=True).for_hwnd(window.hwnd)
    overlay.update_worms({worm.worm_id: worm})
    hidden_when_minimized = not bool(user32.IsWindowVisible(renderer.hwnd))

    user32.ShowWindowAsync(window.hwnd, 9)
    user32.ShowWindow(window.hwnd, 9)
    user32.SetWindowPos(
        window.hwnd, 0, moved.left, moved.top, moved.width, moved.height, 0x0040
    )
    user32.BringWindowToTop(window.hwnd)
    time.sleep(0.8)
    restored = manager.refresh(force=True).for_hwnd(window.hwnd)
    if restored is None:
        raise RuntimeError("Explorer host did not restore")
    sync_worm_to_surface(worm, restored)
    overlay.update_worms({worm.worm_id: worm})
    restored_renderer = overlay._windows.get(window.host_id)
    visible_when_restored = bool(
        restored_renderer is not None and user32.IsWindowVisible(restored_renderer.hwnd)
    )
    result["screenshots"]["restored"] = capture("03-explorer-restored")

    user32.PostMessageW(window.hwnd, 0x0112, 0xF060, 0)  # WM_SYSCOMMAND / SC_CLOSE
    deadline = time.monotonic() + 8.0
    catalog = manager.refresh(force=True)
    close_samples = []
    while catalog.for_hwnd(window.hwnd) is not None and time.monotonic() < deadline:
        close_samples.append({
            "is_window": bool(user32.IsWindow(window.hwnd)),
            "visible": bool(user32.IsWindowVisible(window.hwnd)),
            "minimized": bool(user32.IsIconic(window.hwnd)),
            "catalog_has_host": catalog.for_hwnd(window.hwnd) is not None,
        })
        time.sleep(0.2)
        catalog = manager.refresh(force=True)
    closed = catalog.for_hwnd(window.hwnd) is None
    if closed:
        bind_worm_to_surface(worm, catalog.desktop)
    overlay.update_worms({worm.worm_id: worm})
    old_renderer_removed = window.host_id not in overlay._windows

    result.update({
        "explorer_hwnd": window.hwnd,
        "host_class": window.class_name,
        "host_process": window.process_name,
        "initial_monitor": initial_monitor,
        "moved_monitor": moved.monitor,
        "initial_local": initial_local,
        "moved_local": [worm.host_local_x, worm.host_local_y] if not closed else initial_local,
        "screen_after_move": [moved.left + initial_local[0], moved.top + initial_local[1]],
        "initial_z_order": {"renderer_index": initial_order.index(renderer.hwnd), "host_index": initial_order.index(window.hwnd)},
        "hidden_when_minimized": hidden_when_minimized,
        "visible_when_restored": visible_when_restored,
        "closed": closed,
        "close_samples": close_samples[-5:],
        "same_worm_identity": id(worm) == identity,
        "fallback_host": worm.host_id,
        "fallback_is_desktop": worm.host_id == catalog.desktop.host_id,
        "old_renderer_removed": old_renderer_removed,
        "renderer_topmost": bool(renderer_exstyle & 0x00000008),
    })
    result["passed"] = (
        result["host_class"] == "CabinetWClass"
        and result["host_process"].lower() == "explorer.exe"
        and initial_local == [min(220.0, window.width - 1.0), min(180.0, window.height - 1.0)]
        and hidden_when_minimized
        and visible_when_restored
        and closed
        and result["same_worm_identity"]
        and result["fallback_is_desktop"]
        and old_renderer_removed
        and not result["renderer_topmost"]
    )
finally:
    if overlay is not None:
        overlay.stop()
    if window is not None and user32.IsWindow(window.hwnd):
        user32.PostMessageW(window.hwnd, 0x0112, 0xF060, 0)  # WM_SYSCOMMAND / SC_CLOSE
    if previous_foreground:
        user32.SetForegroundWindow(previous_foreground)

path = OUT / "result.json"
path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(result, indent=2, sort_keys=True))
if not result.get("passed", False):
    raise SystemExit(1)
