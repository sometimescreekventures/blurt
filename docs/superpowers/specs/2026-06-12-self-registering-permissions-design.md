# Self-Registering Permissions + One-Command Setup — Design

**Date:** 2026-06-12
**Status:** Approved

## Motivation

First-run setup is four commands, and the worst of them is the TCC dance:
reveal the uv-managed python binary in Finder, then drag-and-drop it onto the
Accessibility and Input Monitoring panes (macOS 14+ greys raw binaries out of
the `+` picker). Worse, when a uv Python upgrade changes the binary path,
macOS silently forgets both grants and the daemon just stops working.

The fix: blurt asks macOS for the permissions itself. Calling
`AXIsProcessTrustedWithOptions(prompt=True)` and `CGRequestListenEventAccess()`
at startup makes macOS show its own dialogs and pre-register the binary in
both panes — granting becomes "flip two toggles," exactly like the existing
microphone prompt. A new `setup.sh` collapses install to one command.

## Scope

In scope:

- Permission check/request wrappers in `blurt.py` for Accessibility and
  Input Monitoring.
- Startup integration: prompt for missing grants, show `⚠️`, watch for the
  grants to land, then self-restart (LaunchAgent path) to pick them up.
- `setup.sh`: one command for a fresh Mac (`install.sh` → `service.sh install`
  → `service.sh start` → tell the user to approve the dialogs).
- README: quick deploy becomes `git clone … && ./setup.sh`; `permissions.sh`
  demoted to a manual fallback; troubleshooting entry for the
  permissions-lost-after-update case.
- New dependency: `pyobjc-framework-ApplicationServices` (small; Quartz is
  already present via pynput).

Out of scope (deliberate):

- Packaging blurt as a real `.app` bundle (standing decision in the README;
  the OS dialogs will say "python3.12," which is cosmetic).
- Changing `permissions.sh` (it remains as-is, as the fallback).
- Scripting TCC grants directly (impossible without MDM).
- Microphone handling (already prompt-driven).

## Components

### Permission wrappers (`# --- permissions ---` section in blurt.py)

```python
def accessibility_granted(prompt: bool = False) -> bool
def input_monitoring_granted(prompt: bool = False) -> bool
```

- Accessibility: `AXIsProcessTrusted()`; with `prompt=True`,
  `AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})` from
  `ApplicationServices`.
- Input Monitoring: `CGPreflightListenEventAccess()`; with `prompt=True`,
  `CGRequestListenEventAccess()` from `Quartz`.
- `prompt=True` shows the OS dialog (macOS shows it at most once per binary
  per TCC state) and registers the binary in the pane either way.
- If the APIs can't be imported or raise (older/newer macOS oddities), the
  wrappers log a warning and return `True` — blurt degrades to today's
  behavior instead of blocking startup.

### Startup integration (`main()`)

1. Check both permissions with `prompt=False`.
2. Both granted → run exactly as today.
3. Anything missing → call the missing one(s) with `prompt=True`, log what's
   missing and what to do, set `STATE.title = "⚠️"`, and start the watcher
   thread. The pynput listener still starts regardless (unchanged).

### Watcher thread

A daemon thread, started only when something was missing:

- Polls both checks (`prompt=False`) every 5 s.
- When both become granted:
  - LaunchAgent installed and no meeting recording active → log
    "permissions granted; restarting" and call `restart_daemon()` (the
    existing exit-nonzero → launchd relaunch path). A fresh process is
    required for Input Monitoring to take effect on the event tap.
  - Meeting recording active → keep polling; restart on a later tick once
    the meeting ends (same guard as self-update).
  - No LaunchAgent (interactive run) → log "restart blurt to pick up
    permissions" and exit the thread.
- The watcher takes injected check/restart/launchagent functions so the
  gating logic is unit-testable without macOS APIs.

### setup.sh

```bash
git clone https://github.com/sometimescreekventures/blurt.git
cd blurt && ./setup.sh
```

Runs `./install.sh`, then `./service.sh install`, then `./service.sh start`,
then prints: approve the two permission dialogs that just appeared (blurt
restarts itself once you do), the mic prompt comes on first dictation, and
`./permissions.sh` exists as a manual fallback. Idempotent — also serves as
the "fix my install" command. Fails fast (`set -euo pipefail`) if any step
fails.

### README

- Quick deploy: clone + `./setup.sh` (+ optional `./permissions.sh` note).
- "What each step does" gains setup.sh; permissions.sh described as fallback.
- Permissions section: lead with "blurt asks for these itself on first
  launch — approve the dialogs"; keep the drag-and-drop reference material
  for the fallback.
- Troubleshooting: "blurt stopped working after an update" → grants were
  lost with the python path; the daemon re-prompts at startup, flip the
  toggles and it restarts itself.

## Edge cases

- **User dismisses a dialog without granting.** The pane entry still exists;
  the user can toggle it later (or run `permissions.sh`). The `⚠️` icon and
  the watcher persist until granted; the dialog won't re-show on subsequent
  launches (macOS behavior), which is fine because the entry is registered.
- **Repeated daemon restarts while ungranted.** Each start re-calls
  `prompt=True` for missing grants; macOS suppresses duplicate dialogs, so
  no prompt spam.
- **Stale pane entries from old python paths.** Harmless; macOS keeps them
  disabled. Not cleaned up.
- **`⚠️` overload.** The icon already means "mic missing / stream failure";
  logs disambiguate. Acceptable for v1.
- **Interactive run under Terminal with inherited grants.** Checks pass
  (Terminal's grants cover child processes), so no prompts — matches the
  documented Terminal workflow.

## Testing

Unit (TDD, mocking the macOS API boundary):

- Wrappers return the API result; `prompt` selects the prompting variant;
  import/availability failure → `True` + warning.
- Watcher: restarts when both checks flip to granted and a LaunchAgent
  exists; defers while a meeting is active; logs-and-exits without a
  LaunchAgent; never restarts while still ungranted.
- `main()` gating: prompts fired only for missing permissions (factored so
  this is testable without rumps).

Manual checklist (on a secondary Mac, or after `tccutil reset Accessibility`
+ `tccutil reset ListenEvent`):

1. Fresh clone → `./setup.sh` → two dialogs appear; toggle both on → daemon
   restarts itself within ~5 s; dictation works end-to-end.
2. Dismiss the dialogs instead → `⚠️` persists; entries visible in both
   panes; toggling them later triggers the self-restart.
3. Granted state → restart daemon → no dialogs, no `⚠️`, normal startup.
4. Interactive `uv run python blurt.py` with Terminal granted → no prompts.
