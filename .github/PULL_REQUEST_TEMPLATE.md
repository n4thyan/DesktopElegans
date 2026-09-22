## Summary

Describe the change and why it is needed.

## Area

- [ ] Biology / c302
- [ ] Body mechanics
- [ ] Window / host surfaces
- [ ] Rendering / z-order
- [ ] Multi-monitor
- [ ] Input passthrough
- [ ] Performance
- [ ] Packaging
- [ ] Documentation

## Verification

- [ ] Relevant regression tests added or updated
- [ ] Full unit test suite passes
- [ ] Config validation passes
- [ ] Input passthrough smoke passes if applicable
- [ ] Explorer lifecycle smoke passes if applicable
- [ ] Chrome / desktop host smoke passes if applicable
- [ ] Manual Windows behaviour checked where appropriate

## Host-surface invariants

For window/rendering changes:

- [ ] Worm identity remains authoritative and is not duplicated during migration
- [ ] Application renderer is not globally topmost
- [ ] Foreground applications can occlude worms normally
- [ ] Renderer does not steal focus or input
- [ ] Minimise / restore / close lifecycle remains clean
- [ ] Negative-coordinate monitor layouts remain supported

## Safety boundary

- [ ] This change does not add self-propagation, process injection, privilege escalation, credential access, persistence or remote spreading.

## Screenshots / evidence

Add screenshots, logs or before/after evidence if useful.
