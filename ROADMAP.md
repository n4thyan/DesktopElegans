# DesktopElegans Roadmap

Status key: 🟢 complete · 🟡 planned / in progress · 🔵 research · ⚪ optional

## Core desktop organism — 🟢

- 🟢 Windows desktop habitat
- 🟢 Generic Win32 top-level window habitats
- 🟢 File Explorer support
- 🟢 Chrome support
- 🟢 Multi-monitor virtual-desktop coordinates
- 🟢 Negative-coordinate monitor layouts
- 🟢 Move / resize tracking
- 🟢 Minimise / restore lifecycle
- 🟢 Host-close fallback
- 🟢 Normal z-order / foreground occlusion
- 🟢 Click-through / no-focus-steal renderers
- 🟢 Multiple persistent worm identities
- 🟢 c302 / jNeuroML neural path
- 🟢 Approx. 1 mm production visual scale
- 🟢 Final core verification: 49/49 tests passing

## Public release polish — 🟡

- 🟡 Import the verified source tree into this repository
- 🟡 Add a short GIF/video showing desktop → app → monitor transitions
- 🟡 Add a simple first-run setup path
- 🟡 Add Windows CI once dependency installation is represented in the public tree
- 🟡 Produce a tagged pre-release build

## Packaging — 🟡

Goal: reduce setup friction from a development environment to a normal Windows download.

- 🟡 package Python/runtime dependencies
- 🟡 handle Java/jNeuroML requirements cleanly
- 🟡 provide a one-command or one-click launch
- 🟡 document Windows SmartScreen expectations for unsigned development builds

## Biology and behaviour — 🔵

Future biological improvements should remain separable from desktop integration.

- 🔵 improve visible locomotion fidelity
- 🔵 investigate richer sensory mappings from desktop state
- 🔵 expand validated c302-driven behaviours
- ⚪ additional configurable ecology behaviours

## Desktop integration — 🔵

- 🔵 broader application compatibility testing
- 🔵 mixed-DPI monitor validation
- 🔵 long-running performance / renderer-leak testing
- ⚪ optional additional host-surface behaviours

## Explicitly out of scope

DesktopElegans is not a malware project.

The roadmap does not include autonomous executable propagation, process injection, privilege escalation, credential access, persistence, remote spreading or bypassing Windows security boundaries.
