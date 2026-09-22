from __future__ import annotations

import math
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

from nematode_mind.host_surfaces import (
    HostSurface,
    HostSurfaceCatalog,
    bind_worm_to_surface,
    sync_worm_to_surface,
    update_worm_local_coordinates,
)
from nematode_mind.models import WormAction, WormState
from nematode_mind.worm_manager import WormManager, WormManagerConfig
from nematode_mind.overlay import DesktopOverlay


class FakeSurfaceManager:
    def __init__(self, catalog: HostSurfaceCatalog):
        self.current = catalog
        self.overlays: set[int] = set()

    def refresh(self) -> HostSurfaceCatalog:
        return self.current

    def register_overlay(self, hwnd: int) -> None:
        self.overlays.add(hwnd)

    def unregister_overlay(self, hwnd: int) -> None:
        self.overlays.discard(hwnd)

    def insertion_anchor(self, _host_hwnd: int) -> int:
        return 0


class HostSurfaceTests(unittest.TestCase):
    def surfaces(self) -> tuple[HostSurface, HostSurface, HostSurface]:
        desktop = HostSurface(
            host_id="desktop:10", hwnd=10, rect=(-1920, 0, 1920, 1080),
            visible=True, minimized=False, is_desktop=True, z_order=100,
            title="Program Manager", class_name="Progman", process_name="explorer.exe",
        )
        chrome = HostSurface(
            host_id="window:20", hwnd=20, rect=(100, 80, 1100, 780),
            visible=True, minimized=False, is_desktop=False, z_order=5,
            title="Example - Google Chrome", class_name="Chrome_WidgetWin_1", process_name="chrome.exe",
        )
        cover = HostSurface(
            host_id="window:30", hwnd=30, rect=(400, 200, 900, 600),
            visible=True, minimized=False, is_desktop=False, z_order=2,
            title="notes.txt - Notepad", class_name="Notepad", process_name="notepad.exe",
        )
        return desktop, chrome, cover

    def test_catalog_is_generic_and_resolves_topmost_physical_surface(self) -> None:
        desktop, chrome, cover = self.surfaces()
        catalog = HostSurfaceCatalog([desktop, chrome, cover])
        self.assertEqual(catalog.desktop.host_id, desktop.host_id)
        self.assertEqual(catalog.surface_at(150, 100).host_id, chrome.host_id)
        self.assertEqual(catalog.surface_at(500, 300).host_id, cover.host_id)
        self.assertEqual(catalog.surface_at(-1000, 500).host_id, desktop.host_id)
        self.assertEqual(catalog.for_hwnd(20).process_name, "chrome.exe")

    def test_host_binding_follows_move_and_resize_in_client_coordinates(self) -> None:
        _desktop, chrome, _cover = self.surfaces()
        worm = WormState(
            "w1", "desktop:10", screen_x=300.0, screen_y=250.0,
            body_points=[[300.0, 250.0], [296.0, 250.0]],
        )
        bind_worm_to_surface(worm, chrome)
        self.assertEqual(worm.host_id, "window:20")
        self.assertEqual((worm.host_local_x, worm.host_local_y), (200.0, 170.0))

        moved = HostSurface(**{
            **chrome.__dict__,
            "rect": (300, 180, 900, 580),
        })
        self.assertTrue(sync_worm_to_surface(worm, moved))
        self.assertEqual((worm.screen_x, worm.screen_y), (500.0, 350.0))
        self.assertEqual(worm.body_points, [[500.0, 350.0], [496.0, 350.0]])

        worm.screen_x += 7.0
        worm.screen_y -= 3.0
        update_worm_local_coordinates(worm, moved)
        self.assertEqual((worm.host_local_x, worm.host_local_y), (207.0, 167.0))

    def test_minimised_or_hidden_host_hides_without_losing_local_position(self) -> None:
        _desktop, chrome, _cover = self.surfaces()
        worm = WormState("w1", chrome.host_id, screen_x=250.0, screen_y=180.0)
        bind_worm_to_surface(worm, chrome)
        before = (worm.host_local_x, worm.host_local_y, worm.screen_x, worm.screen_y)
        minimized = HostSurface(**{**chrome.__dict__, "visible": False, "minimized": True})
        self.assertFalse(sync_worm_to_surface(worm, minimized))
        self.assertEqual(before, (worm.host_local_x, worm.host_local_y, worm.screen_x, worm.screen_y))

    def test_host_coordinates_are_clamped_and_overlay_translation_is_clipped(self) -> None:
        _desktop, chrome, _cover = self.surfaces()
        worm = WormState("w1", chrome.host_id, screen_x=2000.0, screen_y=-100.0)
        bind_worm_to_surface(worm, chrome)
        self.assertTrue(0.0 <= worm.host_local_x < chrome.width)
        self.assertTrue(0.0 <= worm.host_local_y < chrome.height)

        overlay = DesktopOverlay(debug_enabled=False)
        points = overlay._canvas_points(
            [(chrome.left + 20.0, chrome.top + 30.0), (chrome.left + 10.0, chrome.top + 30.0)],
            origin=(chrome.left, chrome.top),
        )
        self.assertEqual(points, [(20.0, 30.0), (10.0, 30.0)])

    def test_overlay_source_has_no_always_on_top_behavior(self) -> None:
        source = Path(__file__).parents[1].joinpath("src", "nematode_mind", "overlay.py").read_text(encoding="utf-8")
        self.assertNotIn('attributes("-topmost", True)', source)
        self.assertNotIn("HWND_TOPMOST", source)
        self.assertNotIn("hwnd_topmost", source.lower())

    def test_manager_tracks_host_lifecycle_and_real_migration(self) -> None:
        desktop, chrome, cover = self.surfaces()
        fake = FakeSurfaceManager(HostSurfaceCatalog([desktop, chrome, cover]))
        manager = WormManager(WormManagerConfig(initial_worms=1, ecology_enabled=False))
        manager.setup(brain=object(), dimensions=(3840, 1080), origin=(-1920, 0), surface_manager=fake)
        worm = manager.alive_worms()[0]
        self.assertEqual(worm.host_id, desktop.host_id)

        manager.assign_worm_host(worm.worm_id, chrome.hwnd)
        local = (worm.host_local_x, worm.host_local_y)
        moved_chrome = HostSurface(**{**chrome.__dict__, "rect": (250, 140, 950, 640)})
        fake.current = HostSurfaceCatalog([desktop, moved_chrome, cover])
        manager._sync_host_surfaces()
        expected_local = (
            min(local[0], moved_chrome.width - 1.0),
            min(local[1], moved_chrome.height - 1.0),
        )
        self.assertEqual((worm.screen_x, worm.screen_y), (250 + expected_local[0], 140 + expected_local[1]))
        local = (worm.host_local_x, worm.host_local_y)

        minimized = HostSurface(**{**moved_chrome.__dict__, "visible": False, "minimized": True})
        fake.current = HostSurfaceCatalog([desktop, minimized, cover])
        manager._sync_host_surfaces()
        self.assertFalse(worm.metadata["host_visible"])

        restored = HostSurface(**{**moved_chrome.__dict__, "rect": (350, 240, 1050, 740)})
        fake.current = HostSurfaceCatalog([desktop, restored, cover])
        manager._sync_host_surfaces()
        self.assertTrue(worm.metadata["host_visible"])
        self.assertEqual((worm.screen_x, worm.screen_y), (350 + local[0], 240 + local[1]))

        bind_worm_to_surface(worm, desktop)
        worm.screen_x, worm.screen_y = (150.0, 100.0)
        worm.host_local_x, worm.host_local_y = (2070.0, 100.0)
        worm.last_action = WormAction.MIGRATE
        fake.current = HostSurfaceCatalog([desktop, chrome, cover])
        manager.host_catalog = fake.current
        manager._update_host_after_motion(worm)
        self.assertEqual(worm.host_id, chrome.host_id)
        self.assertEqual(worm.previous_host_id, desktop.host_id)

    def test_closed_host_migrates_same_worm_to_desktop_fallback(self) -> None:
        desktop, chrome, cover = self.surfaces()
        fake = FakeSurfaceManager(HostSurfaceCatalog([desktop, chrome, cover]))
        manager = WormManager(WormManagerConfig(initial_worms=1, ecology_enabled=False))
        manager.setup(brain=object(), dimensions=(3840, 1080), origin=(-1920, 0), surface_manager=fake)
        worm = manager.alive_worms()[0]
        identity = id(worm)
        manager.assign_worm_host(worm.worm_id, chrome.hwnd)

        fake.current = HostSurfaceCatalog([desktop, cover])
        manager._sync_host_surfaces()

        self.assertEqual(id(worm), identity)
        self.assertEqual(worm.host_id, desktop.host_id)
        self.assertEqual(worm.previous_host_id, chrome.host_id)
        self.assertEqual(len(manager.alive_worms()), 1)

    def test_multiple_worms_keep_distinct_identity_and_host_geometry(self) -> None:
        desktop, chrome, cover = self.surfaces()
        fake = FakeSurfaceManager(HostSurfaceCatalog([desktop, chrome, cover]))
        manager = WormManager(WormManagerConfig(initial_worms=3, ecology_enabled=False))
        manager.setup(brain=object(), dimensions=(3840, 1080), origin=(-1920, 0), surface_manager=fake)
        desktop_worm, chrome_worm, cover_worm = manager.alive_worms()
        manager.assign_worm_host(chrome_worm.worm_id, chrome.hwnd)
        manager.assign_worm_host(cover_worm.worm_id, cover.hwnd)
        identities = {worm.worm_id: id(worm) for worm in manager.alive_worms()}
        chrome_local = (chrome_worm.host_local_x, chrome_worm.host_local_y)
        cover_local = (cover_worm.host_local_x, cover_worm.host_local_y)

        moved_chrome = HostSurface(**{**chrome.__dict__, "rect": (250, 160, 1250, 860)})
        moved_cover = HostSurface(**{**cover.__dict__, "rect": (-1500, 100, -1000, 500)})
        fake.current = HostSurfaceCatalog([desktop, moved_chrome, moved_cover])
        manager._sync_host_surfaces()

        self.assertEqual({worm.worm_id: id(worm) for worm in manager.alive_worms()}, identities)
        self.assertEqual(desktop_worm.host_id, desktop.host_id)
        self.assertEqual(chrome_worm.host_id, moved_chrome.host_id)
        self.assertEqual(cover_worm.host_id, moved_cover.host_id)
        self.assertEqual(
            (chrome_worm.screen_x, chrome_worm.screen_y),
            (moved_chrome.left + min(chrome_local[0], moved_chrome.width - 1.0), moved_chrome.top + min(chrome_local[1], moved_chrome.height - 1.0)),
        )
        self.assertEqual(
            (cover_worm.screen_x, cover_worm.screen_y),
            (moved_cover.left + min(cover_local[0], moved_cover.width - 1.0), moved_cover.top + min(cover_local[1], moved_cover.height - 1.0)),
        )

    def test_overlay_destroys_closed_host_renderer_and_hides_minimized_renderer(self) -> None:
        desktop, chrome, cover = self.surfaces()
        fake = FakeSurfaceManager(HostSurfaceCatalog([desktop, chrome, cover]))
        overlay = DesktopOverlay(surface_manager=fake)

        class DummyTop:
            def __init__(self) -> None:
                self.withdrawn = False
                self.destroyed = False

            def withdraw(self) -> None:
                self.withdrawn = True

            def destroy(self) -> None:
                self.destroyed = True

        closed_top = DummyTop()
        minimized_top = DummyTop()
        closed_record = SimpleNamespace(hwnd=901, original_wndproc=0, toplevel=closed_top)
        minimized_record = SimpleNamespace(hwnd=902, original_wndproc=0, toplevel=minimized_top)
        overlay._windows = {chrome.host_id: closed_record, cover.host_id: minimized_record}
        fake.overlays.update({901, 902})
        minimized_cover = HostSurface(**{**cover.__dict__, "visible": False, "minimized": True})
        catalog = HostSurfaceCatalog([desktop, minimized_cover])

        overlay._reconcile_host_windows(catalog, {chrome.host_id, cover.host_id})

        self.assertNotIn(chrome.host_id, overlay._windows)
        self.assertTrue(closed_top.destroyed)
        self.assertNotIn(901, fake.overlays)
        self.assertIn(cover.host_id, overlay._windows)
        self.assertTrue(minimized_top.withdrawn)
        self.assertFalse(minimized_top.destroyed)

    @unittest.skipUnless(os.name == "nt", "Windows host catalog integration")
    def test_windows_catalog_exposes_desktop_and_public_top_level_metadata(self) -> None:
        from nematode_mind.host_surfaces import WindowsHostSurfaceManager

        manager = WindowsHostSurfaceManager()
        catalog = manager.refresh()
        self.assertTrue(catalog.desktop.is_desktop)
        self.assertGreater(catalog.desktop.width, 0)
        self.assertGreater(catalog.desktop.height, 0)
        self.assertTrue(all(surface.hwnd > 0 for surface in catalog.surfaces))
        self.assertEqual(len({surface.hwnd for surface in catalog.surfaces}), len(catalog.surfaces))
        self.assertTrue(all(surface.process_name == Path(surface.process_name).name for surface in catalog.surfaces if surface.process_name))
        self.assertTrue(all(not manager._is_cloaked(surface.hwnd) for surface in catalog.surfaces))


if __name__ == "__main__":
    unittest.main()
