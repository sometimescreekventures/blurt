# Blurt.app Bundle Built by `service.sh install` — Design

**Date:** 2026-06-12
**Status:** Approved

## Motivation

Quick deploy is currently four steps, with `./make-app.sh` (which drops the
custom-icon `Blurt.app` launcher into `~/Applications`) as an optional trailing
step that's easy to forget. The app bundle should appear as part of installing
the service, not as a separate step.

## Design

- `service.sh install`, after bootstrapping and enabling the LaunchAgent, also
  runs `make-app.sh` so `~/Applications/Blurt.app` (with `Resources/Blurt.icns`)
  is created automatically.
- The bundle logic stays in `make-app.sh`; `service.sh` calls it. One source of
  truth, and `./make-app.sh` remains runnable standalone for its documented
  "re-run if the repo moves" case.
- App creation is a convenience: if `make-app.sh` fails, `service.sh install`
  prints a warning and still succeeds (consistent with `make-app.sh`'s existing
  warn-and-continue handling of a missing icon).
- The install completion message mentions the app; the README quick-deploy
  drops the `./make-app.sh` step (3 steps instead of 4) and the
  "What each step does" section notes that `service.sh install` builds the app.

Out of scope: bundling Python into the .app, changing what the launcher does
(`exec service.sh restart`), or an all-in-one setup script.

## Verification

No test harness exists for the shell scripts; verify manually:

1. `rm -rf ~/Applications/Blurt.app && ./service.sh install` → install output
   mentions the app; `~/Applications/Blurt.app` exists with
   `Contents/Resources/Blurt.icns` and the service is loaded.
2. Temporarily break `make-app.sh` (e.g. rename it) → `./service.sh install`
   warns but exits 0 and the agent is still installed. Restore.
3. `./make-app.sh` standalone still works.
