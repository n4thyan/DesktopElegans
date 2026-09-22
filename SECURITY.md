# Security Policy

## Scope

DesktopElegans is designed as a local visual desktop-organism simulation.

Its window-to-window behaviour is implemented by a trusted controller that tracks simulated worms and associates them with eligible Windows host surfaces. It is **not** intended to copy code into host processes or propagate between machines.

The following are intentionally out of scope:

- process injection;
- privilege escalation;
- credential access;
- autonomous persistence;
- email, network or removable-media spreading;
- self-replicating executable processes;
- security-control bypasses.

## Reporting a vulnerability

Please do not publish sensitive exploit details in a public issue.

If GitHub private vulnerability reporting is available for this repository, use that channel. Otherwise, open a minimal issue stating that you have a security report and need a private contact path, without including exploit details.

For ordinary bugs that do not expose a security weakness, use the normal bug-report template.

## Supported version

Security fixes currently target the latest code on the default branch while the project is pre-release.
