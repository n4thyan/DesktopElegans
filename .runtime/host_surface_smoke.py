from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PIL import ImageGrab

from nematode_mind.host_surfaces import WindowsHostSurfaceManager, bind_worm_to_surface, sync_worm_to_surface
from nematode_mind.models import WormState
from nematode_mind.overlay import DesktopOverlay

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".runtime" / "host-smoke"
OUT.mkdir(parents=True, exist_ok=True)
user32 = ctypes.windll.user32


def capture(name: str) -> str:
    path = OUT / f"{name}.png"
    ImageGrab.grab(all_screens=True).save(path)
    return str(path)


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError()
    return rect.left, rect.top, rect.right, rect.bottom


def set_rect(hwnd: int, rect: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = rect
    if not user32.SetWindowPos(hwnd, 0, left, top, right - left, bottom - top, 0x0014):
        raise ctypes.WinError()


def z_order() -> list[int]:
    rows: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd, _lparam):
        rows.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return rows


def title(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def send_real_input_through_overlay(host_hwnd: int, x: int, y: int, text: str) -> dict:
    """Use the real Windows input queue, then restore user focus/cursor."""
    previous_foreground = int(user32.GetForegroundWindow() or 0)
    previous_cursor = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(previous_cursor))
    user32.SetCursorPos(int(x), int(y))
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
    time.sleep(0.15)
    for character in text:
        encoded = int(user32.VkKeyScanW(ord(character)))
        if encoded == -1:
            raise RuntimeError(f"cannot synthesize test character: {character!r}")
        virtual_key = encoded & 0xFF
        modifiers = (encoded >> 8) & 0xFF
        if modifiers & 1:
            user32.keybd_event(0x10, 0, 0, 0)  # VK_SHIFT down
        user32.keybd_event(virtual_key, 0, 0, 0)
        user32.keybd_event(virtual_key, 0, 0x0002, 0)
        if modifiers & 1:
            user32.keybd_event(0x10, 0, 0x0002, 0)  # VK_SHIFT up
    user32.mouse_event(0x0800, 0, 0, -120, 0)  # MOUSEEVENTF_WHEEL
    time.sleep(0.2)
    foreground_after = int(user32.GetForegroundWindow() or 0)
    user32.SetCursorPos(previous_cursor.x, previous_cursor.y)
    if previous_foreground:
        user32.SetForegroundWindow(previous_foreground)
    return {
        "previous_foreground": previous_foreground,
        "foreground_after_input": foreground_after,
        "input_point": [int(x), int(y)],
    }


surfaces = WindowsHostSurfaceManager()
catalog = surfaces.refresh()
chrome = next(
    (surface for surface in catalog.surfaces if surface.process_name.lower() == "chrome.exe" and not surface.minimized),
    None,
)
if chrome is None:
    raise RuntimeError("no visible Chrome top-level window is available for the requested smoke test")

chrome_original = window_rect(chrome.hwnd)
input_process: subprocess.Popen | None = None
input_surface = None
overlay = DesktopOverlay(
    debug_enabled=True,
    debug_scale=20,
    show_coordinates=True,
    surface_manager=surfaces,
)
result: dict = {"chrome_hwnd": chrome.hwnd, "chrome_original_rect": chrome_original, "screenshots": {}}

try:
    center_x = chrome.left + min(max(140, chrome.width // 2), max(140, chrome.width - 140))
    center_y = chrome.top + min(max(120, chrome.height // 2), max(120, chrome.height - 120))
    desktop_worm = WormState("desktop-worm", catalog.desktop.host_id, screen_x=center_x, screen_y=center_y)
    desktop_worm.body_points = [[center_x, center_y], [center_x - 5, center_y], [center_x - 10, center_y + 1]]
    bind_worm_to_surface(desktop_worm, catalog.desktop)

    overlay.start()

    # Desktop worm visible with Chrome minimized, then obscured when Chrome is restored.
    user32.ShowWindowAsync(chrome.hwnd, 6)  # SW_MINIMIZE
    time.sleep(0.8)
    exposed_catalog = surfaces.refresh(force=True)
    desktop_point = None
    for y in range(chrome.top + 100, chrome.bottom - 100, 80):
        for x in range(chrome.left + 120, chrome.right - 120, 100):
            if exposed_catalog.surface_at(x, y).is_desktop:
                desktop_point = (x, y)
                break
        if desktop_point is not None:
            break
    if desktop_point is None:
        raise RuntimeError("no exposed desktop point inside the Chrome test region")
    desktop_worm.screen_x, desktop_worm.screen_y = desktop_point
    desktop_worm.body_points = [
        [desktop_worm.screen_x, desktop_worm.screen_y],
        [desktop_worm.screen_x - 5, desktop_worm.screen_y],
        [desktop_worm.screen_x - 10, desktop_worm.screen_y + 1],
    ]
    bind_worm_to_surface(desktop_worm, exposed_catalog.desktop)
    overlay.update_worms({desktop_worm.worm_id: desktop_worm})
    result["screenshots"]["desktop_visible"] = capture("01-desktop-worm-visible")

    user32.ShowWindowAsync(chrome.hwnd, 9)  # SW_RESTORE
    set_rect(chrome.hwnd, chrome_original)
    time.sleep(0.8)
    overlay.update_worms({desktop_worm.worm_id: desktop_worm})
    result["screenshots"]["desktop_covered_by_chrome"] = capture("02-desktop-worm-covered-by-chrome")

    # Bind to Chrome and prove stable client-local coordinates across move/resize.
    bind_worm_to_surface(desktop_worm, surfaces.refresh().for_hwnd(chrome.hwnd))
    desktop_worm.host_local_x = min(220.0, max(30.0, chrome.width - 30.0))
    desktop_worm.host_local_y = min(180.0, max(30.0, chrome.height - 30.0))
    sync_worm_to_surface(desktop_worm, surfaces.refresh().for_hwnd(chrome.hwnd))
    desktop_worm.body_points = [
        [desktop_worm.screen_x, desktop_worm.screen_y],
        [desktop_worm.screen_x - 5, desktop_worm.screen_y],
        [desktop_worm.screen_x - 10, desktop_worm.screen_y + 1],
    ]
    overlay.update_worms({desktop_worm.worm_id: desktop_worm})
    before_local = [desktop_worm.host_local_x, desktop_worm.host_local_y]
    before_screen = [desktop_worm.screen_x, desktop_worm.screen_y]
    result["screenshots"]["chrome_hosted_before_move"] = capture("03-chrome-hosted-before-move")

    l, t, r, b = chrome_original
    moved_rect = (l + 70, t + 45, max(l + 470, r - 100), max(t + 370, b - 70))
    set_rect(chrome.hwnd, moved_rect)
    time.sleep(0.8)
    moved_surface = surfaces.refresh().for_hwnd(chrome.hwnd)
    sync_worm_to_surface(desktop_worm, moved_surface)
    overlay.update_worms({desktop_worm.worm_id: desktop_worm})
    result["screenshots"]["chrome_hosted_after_move"] = capture("04-chrome-hosted-after-move")
    result["chrome_follow"] = {
        "local_before": before_local,
        "local_after": [desktop_worm.host_local_x, desktop_worm.host_local_y],
        "screen_before": before_screen,
        "screen_after": [desktop_worm.screen_x, desktop_worm.screen_y],
        "moved_client_rect": list(moved_surface.rect),
    }

    # A separate ordinary top-level window covers Chrome and its hosted worm.
    input_process = subprocess.Popen([sys.executable, str(ROOT / ".runtime" / "input_host.py")])
    input_surface = None
    deadline = time.monotonic() + 10.0
    while input_surface is None and time.monotonic() < deadline:
        time.sleep(0.2)
        for surface in surfaces.refresh().surfaces:
            if surface.process_name.lower().startswith("python") and surface.title.startswith("Worm Input Host"):
                input_surface = surface
                break
    if input_surface is None:
        raise RuntimeError("input host did not create a top-level window")
    worm_x, worm_y = int(desktop_worm.screen_x), int(desktop_worm.screen_y)
    # Toggle through the topmost band and immediately demote again. This is a
    # reliable way to put a cross-process smoke-test window at the front of the
    # ordinary z-order without depending on Windows foreground-lock timing.
    if not user32.SetWindowPos(input_surface.hwnd, -1, worm_x - 300, worm_y - 220, 800, 520, 0x0050):
        raise ctypes.WinError()
    if not user32.SetWindowPos(input_surface.hwnd, -2, 0, 0, 0, 0, 0x0053):
        raise ctypes.WinError()
    time.sleep(0.8)
    overlay.update_worms({desktop_worm.worm_id: desktop_worm})
    order = z_order()
    chrome_overlay = overlay._windows[desktop_worm.host_id]
    result["cover_z_order"] = {
        "cover_index": order.index(input_surface.hwnd),
        "overlay_index": order.index(chrome_overlay.hwnd),
        "chrome_index": order.index(chrome.hwnd),
    }
    exstyle = int(getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)(chrome_overlay.hwnd, -20))
    result["styles"] = {
        "topmost": bool(exstyle & 0x00000008),
        "transparent": bool(exstyle & 0x00000020),
        "noactivate": bool(exstyle & 0x08000000),
    }
    hit_x = int(desktop_worm.screen_x)
    hit_y = int(desktop_worm.screen_y)
    hit_lparam = (hit_x & 0xFFFF) | ((hit_y & 0xFFFF) << 16)
    result["hit_test"] = {
        "wm_nchittest": int(user32.SendMessageW(chrome_overlay.hwnd, 0x0084, 0, hit_lparam)),
        "wm_mouseactivate": int(user32.SendMessageW(chrome_overlay.hwnd, 0x0021, chrome.hwnd, 0)),
    }
    result["screenshots"]["chrome_covered"] = capture("05-chrome-hosted-covered")

    # Minimise/restore lifecycle.
    user32.ShowWindowAsync(chrome.hwnd, 6)
    time.sleep(0.8)
    overlay.update_worms({desktop_worm.worm_id: desktop_worm})
    result["chrome_minimized_overlay_visible"] = bool(user32.IsWindowVisible(chrome_overlay.hwnd))
    result["screenshots"]["chrome_minimized"] = capture("06-chrome-minimized")

    user32.ShowWindowAsync(chrome.hwnd, 9)
    set_rect(chrome.hwnd, moved_rect)
    time.sleep(0.8)
    restored_surface = surfaces.refresh().for_hwnd(chrome.hwnd)
    sync_worm_to_surface(desktop_worm, restored_surface)
    overlay.update_worms({desktop_worm.worm_id: desktop_worm})
    result["chrome_restored"] = {
        "overlay_visible": bool(user32.IsWindowVisible(chrome_overlay.hwnd)),
        "local": [desktop_worm.host_local_x, desktop_worm.host_local_y],
        "screen": [desktop_worm.screen_x, desktop_worm.screen_y],
    }
    result["screenshots"]["chrome_restored"] = capture("07-chrome-restored")

    result["passed"] = (
        result["chrome_follow"]["local_before"] == result["chrome_follow"]["local_after"]
        and result["cover_z_order"]["cover_index"] < result["cover_z_order"]["overlay_index"]
        and result["cover_z_order"]["overlay_index"] < result["cover_z_order"]["chrome_index"]
        and not result["chrome_minimized_overlay_visible"]
        and result["chrome_restored"]["overlay_visible"]
        and not result["styles"]["topmost"]
        and result["styles"]["transparent"]
        and result["styles"]["noactivate"]
        and result["hit_test"] == {"wm_nchittest": -1, "wm_mouseactivate": 3}
    )
finally:
    try:
        overlay.stop()
    except Exception:
        pass
    try:
        user32.ShowWindowAsync(chrome.hwnd, 9)
        set_rect(chrome.hwnd, chrome_original)
    except Exception:
        pass
    if input_process is not None:
        if input_surface is not None and user32.IsWindow(input_surface.hwnd):
            user32.PostMessageW(input_surface.hwnd, 0x0010, 0, 0)
        if input_process.poll() is None:
            input_process.terminate()
        try:
            input_process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            input_process.kill()
            input_process.wait(timeout=3.0)

result_path = OUT / "result.json"
result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps({"event": "DONE", "result": str(result_path)}, sort_keys=True), flush=True)
if not result.get("passed", False):
    raise SystemExit(1)
