# Contributing to DesktopElegans

Thanks for your interest in DesktopElegans.

The project mixes biological simulation, Win32 window-management behaviour and real-time transparent rendering. Small Windows edge cases can therefore have surprisingly large effects, so changes should be narrow, reproducible and well tested.

## Before opening a pull request

Please:

1. Keep changes focused on one problem or feature.
2. Avoid replacing the host-surface architecture unless there is a demonstrated limitation.
3. Preserve one authoritative simulation identity per worm.
4. Do not make application renderers globally topmost.
5. Do not break click-through / no-activation behaviour.
6. Add a regression test for bugs where practical.
7. Run the canonical verification sequence below.

## Canonical verification

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests .runtime
$env:PYTHONWARNINGS="error::ResourceWarning"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m nematode_mind validate --config config.yaml
.\.venv\Scripts\python.exe .runtime\input_passthrough_smoke.py
.\.venv\Scripts\python.exe .runtime\explorer_host_smoke.py
.\.venv\Scripts\python.exe .runtime\host_surface_smoke.py
```

If your change affects rendering or lifecycle behaviour, also perform a real Windows run.

## Useful manual checks

For host/window changes, verify:

- desktop worms remain below foreground applications;
- an application-hosted worm is covered by unrelated windows above its host;
- mouse, keyboard and wheel input still reach the real host window;
- moving and resizing a host preserves worm-local coordinates;
- minimise hides the renderer without deleting the worm;
- restore brings back the same worm;
- closing a host removes stale renderer resources and falls the worm back safely;
- negative-coordinate monitor layouts still work.

## Code style

Prefer clear, direct Python over clever abstractions.

Win32 constants and behaviours should be documented where their purpose is not obvious. Any workaround for DWM, z-order or focus-lock behaviour should include a short explanation.

## Biological changes

When changing the c302 / jNeuroML path, distinguish between:

- behaviour directly produced by the neural model;
- body / rendering mechanics;
- debug-only visual scaling.

Debug enlargement should never silently alter simulation geometry.

## Safety boundary

DesktopElegans is a local desktop-organism simulation. Contributions that add self-propagation, process injection, privilege escalation, credential access, persistence, remote spreading or other malware behaviour are out of scope.

## Bug reports

A useful bug report includes:

- Windows version;
- monitor layout and DPI/scaling;
- affected application;
- expected behaviour;
- observed behaviour;
- reproduction steps;
- relevant console output;
- whether the problem occurs in debug scale and production scale.

Thanks for helping make the worms more convincing without making Windows less usable.
