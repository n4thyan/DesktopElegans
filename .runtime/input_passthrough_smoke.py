from __future__ import annotations

import ctypes
import json
import re
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from nematode_mind.host_surfaces import WindowsHostSurfaceManager, bind_worm_to_surface, sync_worm_to_surface
from nematode_mind.models import WormState
from nematode_mind.overlay import DesktopOverlay

ROOT = Path(__file__).resolve().parents[1]
user32 = ctypes.windll.user32
ULONG_PTR = wintypes.WPARAM


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", INPUTUNION)]


def send_inputs(*events: INPUT) -> None:
    values = (INPUT * len(events))(*events)
    sent = int(user32.SendInput(len(values), values, ctypes.sizeof(INPUT)))
    if sent != len(values):
        raise ctypes.WinError()


def mouse_event(flags: int, data: int = 0) -> INPUT:
    return INPUT(type=0, mi=MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0))


def key_event(character: str, key_up: bool = False) -> INPUT:
    return INPUT(type=1, ki=KEYBDINPUT(0, ord(character), 0x0004 | (0x0002 if key_up else 0), 0, 0))


def title(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def rect(hwnd: int) -> tuple[int, int, int, int]:
    value = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(value)):
        raise ctypes.WinError()
    return value.left, value.top, value.right, value.bottom


def overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def z_order() -> list[int]:
    values: list[int] = []
    hwnd = int(user32.GetTopWindow(0) or 0)
    while hwnd:
        values.append(hwnd)
        hwnd = int(user32.GetWindow(hwnd, 2) or 0)  # GW_HWNDNEXT
    return values


def window_pid(hwnd: int) -> int:
    value = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(value))
    return int(value.value)


def input_host_hwnds() -> set[int]:
    values: set[int] = set()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd, _lparam):
        if title(int(hwnd)).startswith("Worm Input Host"):
            values.add(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return values


def send_text(value: str) -> None:
    events = []
    for character in value:
        events.extend((key_event(character), key_event(character, key_up=True)))
    send_inputs(*events)


previous_foreground = int(user32.GetForegroundWindow() or 0)
previous_cursor = wintypes.POINT()
user32.GetCursorPos(ctypes.byref(previous_cursor))
existing_input_hosts = input_host_hwnds()
process = subprocess.Popen([sys.executable, str(ROOT / ".runtime" / "input_host.py")])
overlay = None
host = None
result: dict = {}
try:
    surfaces = WindowsHostSurfaceManager()
    deadline = time.monotonic() + 10.0
    host = None
    while host is None and time.monotonic() < deadline:
        time.sleep(0.1)
        for candidate in surfaces.refresh().surfaces:
            if candidate.hwnd not in existing_input_hosts and candidate.title.startswith("Worm Input Host"):
                host = candidate
                break
    if host is None:
        visible = [
            (candidate.hwnd, window_pid(candidate.hwnd), candidate.title, candidate.process_name)
            for candidate in surfaces.refresh().surfaces
            if candidate.title.startswith("Worm Input Host")
        ]
        raise RuntimeError(f"new input host did not appear; launcher_pid={process.pid}; candidates={visible}")

    virtual_left = user32.GetSystemMetrics(76)
    virtual_top = user32.GetSystemMetrics(77)
    virtual_width = user32.GetSystemMetrics(78)
    virtual_height = user32.GetSystemMetrics(79)
    foreground_rect = rect(previous_foreground) if previous_foreground else (0, 0, 0, 0)
    candidates = [
        (virtual_left + 20, virtual_top + 40),
        (virtual_left + virtual_width - 540, virtual_top + 40),
        (virtual_left + 20, virtual_top + virtual_height - 400),
        (virtual_left + virtual_width - 540, virtual_top + virtual_height - 400),
    ]
    left, top = next((x, y) for x, y in candidates if not overlaps((x, y, x + 520, y + 360), foreground_rect))
    user32.SetWindowPos(host.hwnd, 0, left, top, 520, 360, 0x0010 | 0x0040)
    time.sleep(0.2)
    host = surfaces.refresh().for_hwnd(host.hwnd)
    if host is None:
        raise RuntimeError("input host disappeared")

    worm = WormState("input-smoke", host.host_id, screen_x=host.left + 260.0, screen_y=host.top + 180.0)
    bind_worm_to_surface(worm, host)
    worm.host_local_x = 260.0
    worm.host_local_y = 180.0
    sync_worm_to_surface(worm, host)
    worm.body_points = [
        [worm.screen_x, worm.screen_y],
        [worm.screen_x - 5, worm.screen_y],
        [worm.screen_x - 10, worm.screen_y + 1],
    ]
    overlay = DesktopOverlay(
        debug_enabled=True,
        debug_scale=20,
        show_coordinates=True,
        surface_manager=surfaces,
    )
    overlay.start()
    overlay.update_worms({worm.worm_id: worm})
    renderer = overlay._windows[host.host_id]
    order = z_order()
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    exstyle = int(get_long(renderer.hwnd, -20))
    if not user32.SetWindowPos(
        host.hwnd, 0, host.left, host.top, host.width, host.height, 0x0040
    ):
        raise ctypes.WinError()
    user32.BringWindowToTop(host.hwnd)
    user32.SetForegroundWindow(host.hwnd)
    time.sleep(0.3)
    before_click_foreground = int(user32.GetForegroundWindow() or 0)
    click_x = int(host.left + worm.host_local_x)
    click_y = int(host.top + worm.host_local_y)
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    point_target = int(user32.WindowFromPoint(wintypes.POINT(click_x, click_y)) or 0)
    point_target_root = int(user32.GetAncestor(point_target, 2) or point_target)  # GA_ROOT
    user32.SetCursorPos(click_x, click_y)
    send_inputs(mouse_event(0x0002), mouse_event(0x0004))
    time.sleep(0.05)
    foreground_after_click = int(user32.GetForegroundWindow() or 0)
    send_text("worm-input-pass")
    send_inputs(mouse_event(0x0800, -120))

    deadline = time.monotonic() + 5.0
    current_title = title(host.hwnd)
    while time.monotonic() < deadline:
        overlay.update_worms({worm.worm_id: worm})
        current_title = title(host.hwnd)
        if "clicked=1" in current_title and "typed=15" in current_title and "scrolled=1" in current_title:
            break
        time.sleep(0.05)
    after_input_foreground = int(user32.GetForegroundWindow() or 0)
    screen_x = click_x
    screen_y = click_y
    hit_lparam = (screen_x & 0xFFFF) | ((screen_y & 0xFFFF) << 16)
    match = re.search(r"clicked=(\d+) typed=(\d+) scrolled=(\d+)", current_title)
    counts = tuple(int(value) for value in match.groups()) if match else (0, 0, 0)
    result = {
        "host_hwnd": host.hwnd,
        "renderer_hwnd": renderer.hwnd,
        "host_title": current_title,
        "input_point": [click_x, click_y],
        "foreground_before": before_click_foreground,
        "point_target_before_click": {
            "hwnd": point_target,
            "root_hwnd": point_target_root,
            "title": title(point_target_root),
            "is_host": point_target_root == host.hwnd,
            "is_renderer": point_target_root == renderer.hwnd,
        },
        "foreground_after_click": foreground_after_click,
        "foreground_after": after_input_foreground,
        "host_activated_normally": foreground_after_click == host.hwnd,
        "renderer_never_activated": foreground_after_click != renderer.hwnd and after_input_foreground != renderer.hwnd,
        "event_counts": {"clicked": counts[0], "typed": counts[1], "scrolled": counts[2]},
        "z_order": {"renderer_index": order.index(renderer.hwnd), "host_index": order.index(host.hwnd)},
        "styles": {
            "topmost": bool(exstyle & 0x00000008),
            "transparent": bool(exstyle & 0x00000020),
            "noactivate": bool(exstyle & 0x08000000),
        },
        "hit_test": {
            "wm_nchittest": int(user32.SendMessageW(renderer.hwnd, 0x0084, 0, hit_lparam)),
            "wm_mouseactivate": int(user32.SendMessageW(renderer.hwnd, 0x0021, host.hwnd, 0)),
        },
    }
    passed = (
        counts[0] >= 1
        and counts[1] >= len("worm-input-pass")
        and counts[2] >= 1
        and result["host_activated_normally"]
        and result["renderer_never_activated"]
        and result["point_target_before_click"]["is_host"]
        and not result["styles"]["topmost"]
        and result["styles"]["transparent"]
        and result["styles"]["noactivate"]
        and result["hit_test"] == {"wm_nchittest": -1, "wm_mouseactivate": 3}
    )
    result["passed"] = passed
    print(json.dumps(result, indent=2, sort_keys=True))
    if not passed:
        raise SystemExit(1)
finally:
    user32.SetCursorPos(previous_cursor.x, previous_cursor.y)
    if previous_foreground:
        user32.SetForegroundWindow(previous_foreground)
    if overlay is not None:
        overlay.stop()
    if host is not None and user32.IsWindow(host.hwnd):
        user32.PostMessageW(host.hwnd, 0x0010, 0, 0)  # WM_CLOSE
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
