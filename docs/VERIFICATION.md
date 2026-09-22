# Core Milestone Verification

This document records the verified Windows behaviour for the current DesktopElegans core milestone.

## Final result

```text
49 / 49 tests PASS
canonical sequence exit code: 0
config validation: PASS
input passthrough: PASS
Explorer lifecycle: PASS
Chrome / desktop surface: PASS
post-c302 visible wriggling: CONFIRMED
remaining core bugs found: NONE
```

## Canonical verification commands

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests .runtime
$env:PYTHONWARNINGS="error::ResourceWarning"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m nematode_mind validate --config config.yaml
.\.venv\Scripts\python.exe .runtime\input_passthrough_smoke.py
.\.venv\Scripts\python.exe .runtime\explorer_host_smoke.py
.\.venv\Scripts\python.exe .runtime\host_surface_smoke.py
```

## Input passthrough

Verified:

```text
mouse click: PASS
keyboard input: PASS
mouse wheel: PASS
renderer activated: NO
renderer topmost: NO
transparent input style: YES
no-activate style: YES
HTTRANSPARENT: YES
MA_NOACTIVATE: YES
```

## Explorer lifecycle

Verified against a real File Explorer host:

- renderer placed relative to the Explorer host;
- movement between monitors;
- host-local position preservation;
- minimise hides renderer;
- restore shows renderer;
- close detected;
- same worm identity preserved;
- desktop fallback after host close;
- stale renderer removed;
- renderer remains non-topmost.

## Chrome / desktop surfaces

Verified:

- host-relative coordinates survive window movement;
- negative-X monitor movement;
- minimise / restore;
- ordinary foreground windows cover host worms;
- Chrome covers a desktop worm at the same desktop location;
- transparent/no-activate behaviour;
- non-topmost rendering.

## Multi-worm run

A three-worm live run confirmed distinct worms distributed across the multi-monitor workspace.

Foreground Terminal/Chrome regions correctly occluded worms beneath them instead of the organism being painted globally over everything.

## c302 / jNeuroML

The final live verification confirmed visible post-epoch changes in worm:

- body curvature;
- orientation;
- screen position.

All three worms remained alive during the verified multi-worm run.

## Scale

The production visual target is approximately 1 mm.

At 96 DPI:

```text
96 / 25.4 ≈ 3.78 px per mm
```

Debug scale is a render-only multiplier and should not change the simulation's physical geometry.

## Shutdown

Final verification found no lingering project renderer windows or DesktopElegans Python/Java processes after shutdown.

## Production launch

```powershell
.\run.ps1
```
