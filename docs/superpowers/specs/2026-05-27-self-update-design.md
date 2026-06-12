# Self-Update from Menu Bar — Design

**Date:** 2026-05-27
**Status:** Approved, ready for planning

## Motivation

The same git checkout runs on multiple Macs. Today, propagating a change means SSH or Terminal on each one: `git pull && uv sync && ./service.sh restart`. We want a single menu-bar click instead — "Check for Updates" → "Update to abc1234" → the daemon picks up the new code automatically.

Scope is intentionally minimal: track `main` only (no release tagging, no `.app` bundle, no Sparkle). The user is the only developer; the cost of release ceremony is not worth paying. If that changes, we can layer GitHub Releases on top later by swapping the comparison target.

## Scope

In scope:

- Menu-bar items showing the current version and a manual "Check for Updates" / "Update to…" action.
- A background check at startup so the user sees "Update available" without first clicking.
- The update flow itself: refuse-if-dirty, `git fetch`, `git reset --hard origin/main`, `uv sync`, restart the LaunchAgent.
- Surface success / failure / "no LaunchAgent" states clearly in the menu.

Out of scope (deliberate):

- GitHub Releases, tagged versions, release notes in the dropdown.
- Periodic background polling beyond the startup check. (Polling can spam logs and the user already gets a manual check button.)
- A "discard local changes and update" confirmation. v1 refuses dirty checkouts; the user uses Terminal to resolve.
- Self-healing rollback if `uv sync` fails. v1 leaves the user on the new SHA and surfaces "see logs"; manual recovery is `uv sync` from Terminal or `git reset --hard <old SHA>`.
- Updating outside the LaunchAgent path (e.g., when the user is running `uv run python blurt.py` interactively). v1 detects this and disables the update item.
- A "Restart blurt" menu item. (Useful but independent; could follow as its own ~5-line addition.)

## User-visible design

### Menu structure

```
🎙 blurt
├── Microphone ▸ …
├── Hotkey ▸ …
├── Type-mode Hotkey ▸ …
├── ─────────
├── Version: 3d899dd (2026-05-27)            ← disabled label
├── Check for Updates                         ← becomes the action label
└── Quit blurt
```

The version line is always disabled (no callback) and reflects the SHA the daemon is *currently running*, captured at startup. It does not change until the daemon restarts.

### "Check for Updates" label state machine

The single menu item under the version line transitions through these labels:

| State                            | Label                                          | Click action               |
| -------------------------------- | ---------------------------------------------- | -------------------------- |
| Idle (startup, before bg check)  | `Check for Updates`                            | Run a check now            |
| Checking (network in flight)     | `Checking…`                                    | Disabled                   |
| Up to date                       | `Up to date ✓` (reverts to `Check for Updates` after 3 s) | Run a check now            |
| Update available                 | `Update to abc1234 (5 commits behind)`         | Run `apply_update()`       |
| Updating                         | `Updating…`                                    | Disabled                   |
| Local changes present            | `Update unavailable: local changes` (disabled, greyed) | n/a                        |
| Check failed (network / git)     | `Check failed — see logs`                      | Run a check now (retry)    |
| Update failed (uv sync)          | `Update failed — see logs`                     | Run `apply_update()` again |
| LaunchAgent missing              | `Update requires LaunchAgent install` (disabled, greyed) | n/a |

The menu-bar icon does **not** change for any of this. Recording / transcription state is the only thing that drives the icon (`🎙 / 🔴 / ✨ / ⚠️`). An update in progress is signalled solely by the menu label so we don't fight the recording state machine.

### Background check at startup

After the model warm-up thread is spawned, a second daemon thread runs `check_for_updates()`. It blocks until network completes (usually < 1 s). The result populates the menu label. If the user opens the menu before the check finishes, they see `Check for Updates`; once it lands, the label updates in place.

No periodic re-check during the session. Manual click is the only refresh path after startup.

### Interaction with recording

While `Hotkey._recording` is true (the user is holding the hotkey), the update menu item is disabled. We don't want to interrupt a dictation, and `apply_update()` would otherwise race with the transcription worker. The check action is also disabled while recording, for symmetry.

## Update flow

```
apply_update():
    1. assert not Hotkey._recording               (defensive; menu already greys)
    2. assert git status --porcelain is empty     (refuse if dirty)
    3. label = "Updating…"
    4. git fetch origin main                       (network)
    5. git reset --hard origin/main                (no merge; clean replacement)
    6. uv sync                                     (idempotent; usually fast)
    7. restart_daemon()                            (see below)
```

If step 4 or 5 errors, set label to `Update failed — see logs`, return. Working tree state is whatever git left it in (typically: pre-fetch SHA, no working changes — `reset --hard` either succeeds entirely or fails before mutating). If step 6 errors, the working tree is on the new SHA but the venv is mid-sync; set label to `Update failed — see logs` and skip the restart. The user can rerun `uv sync` from Terminal or `git reset --hard <old SHA>` to roll back manually.

### `restart_daemon` mechanism

> **Revised 2026-06-04 (bugfix).** The original detached-helper approach below
> was unreliable in practice and has been replaced. See "Why the helper
> approach failed."

```python
def restart_daemon(_exit=os._exit):
    print("[blurt] update applied; exiting for launchd to relaunch", flush=True)
    sys.stdout.flush(); sys.stderr.flush()
    _exit(1)  # non-zero → KeepAlive(SuccessfulExit=false) relaunches us
```

The LaunchAgent plist sets `KeepAlive = {SuccessfulExit: false}` (see [blurt.plist.template](../../../blurt.plist.template)): launchd relaunches the job whenever it exits **un**-successfully. So the canonical self-restart is to die with a non-zero status and let launchd revive the job — which then runs the freshly-synced code. Verified empirically: a non-zero/abnormal exit relaunches the job in ~0.5 s. `os._exit` (not `sys.exit`) is used because `restart_daemon` runs on a background worker thread; it terminates the whole process immediately. The `_exit` parameter is injected only so unit tests can assert the exit code without killing the test runner.

Normal quit (the "Quit blurt" menu item) and `launchctl bootout` still exit 0, so launchd does **not** relaunch in those cases — only the deliberate update path exits non-zero.

#### Why the helper approach failed

The original code spawned a detached `/tmp/blurt-restart.sh` (`sleep 2; service.sh start`) with `start_new_session=True`, then exited the daemon **cleanly (exit 0)**. Two compounding bugs:

1. A clean exit (0) tells `KeepAlive{SuccessfulExit:false}` **not** to relaunch — so the restart depended entirely on the helper.
2. launchd reaps a job's descendant processes when the job's main process exits — including `setsid`'d ones. `start_new_session=True` was not enough to escape this, so the helper was usually killed during its `sleep 2` before it could run `service.sh start`.

Net effect: the git reset + `uv sync` landed, but nothing relaunched the daemon, so the user had to relaunch manually (which then showed the new version). The non-zero-exit approach removes the helper, the detached subprocess, and the race entirely. Net downtime: ~0.5–1 s.

### LaunchAgent-presence detection

```python
def has_launchagent() -> bool:
    try:
        subprocess.check_output(
            ["launchctl", "print", f"gui/{os.getuid()}/local.blurt"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return True
    except subprocess.CalledProcessError:
        return False
    except (OSError, subprocess.TimeoutExpired):
        return False
```

Called once at startup, cached. If `False`, the update menu item is initialized in the disabled "Update requires LaunchAgent install" state and the background check is skipped.

## Code shape

All in `blurt.py`. New section `# --- self-update --------------------`, located after the existing menu/recorder sections. Estimated +110 to +140 lines, zero removed.

### Module-level constants

```python
# git ref to track when the user runs "Update to latest" from the menu.
UPDATE_REMOTE = "origin"
UPDATE_BRANCH = "main"
```

### New free functions

- `current_version() -> tuple[str, str]` — returns `(short_sha, iso_date)` via `git rev-parse --short HEAD` and `git show -s --format=%cs HEAD`. Cached on first call (e.g. via `functools.lru_cache`); the daemon's SHA can't change without a restart, so the cache is correct for the process lifetime.
- `check_for_updates() -> UpdateCheck` where `UpdateCheck` is a small dataclass with fields `status: Literal["up_to_date", "update_available", "dirty", "check_failed"]`, `local_sha: str`, `remote_sha: str | None`, `commits_behind: int`, `error: str | None`.
- `apply_update() -> ApplyResult` (analogous dataclass: `status: Literal["restarting", "dirty", "fetch_failed", "uv_failed"]`, `error: str | None`).
- `restart_daemon() -> None` — the detached-helper pattern above.
- `has_launchagent() -> bool` — the detection helper.

All four shell out via `subprocess.run([...], cwd=REPO_ROOT, capture_output=True, text=True, timeout=N)`. `REPO_ROOT = Path(__file__).resolve().parent`.

### `MenuApp` changes

- New attributes: `self._version_item`, `self._update_item`, `self._update_lock = threading.Lock()`.
- `_build_update_menu()` constructs the two menu items, with the version label disabled and the update item starting in `Check for Updates` (or the LaunchAgent-missing greyed state).
- `_on_check(sender)` runs `check_for_updates()` in a background thread, marshals the result back to the rumps run loop via `rumps.Timer` / `rumps.notification` (or simpler: a 0-interval `rumps.Timer` that polls a queue — same pattern used elsewhere in rumps apps).
- `_on_update(sender)` runs `apply_update()` in a background thread under `self._update_lock`. If it returns `restarting`, the helper takes over from here.
- `_refresh_update_label(state)` is the single place that sets `_update_item.title` and `_update_item.set_callback(...)`. Easier to reason about state transitions when one function owns the label.

### Background startup check

Added to `main()` right after the model warm-up thread:

```python
threading.Thread(target=_startup_update_check, args=(menu,), daemon=True).start()
```

`_startup_update_check` calls `check_for_updates()` and then `menu._refresh_update_label(...)` via the same marshalling pattern as `_on_check`.

### Daemon mutual exclusion

`apply_update` and `check_for_updates` both acquire `_update_lock` non-blocking. If acquired, run; if not (already in flight), no-op. This means: clicks during a check or update don't queue up.

Recording check: both `apply_update` and `_on_update` early-return with no label change if `Hotkey._recording` is true. (Belt-and-braces; the label is also greyed, so this is for the launchagent or background-thread paths.)

## Edge cases

- **Hand-edited files in the checkout.** `git status --porcelain` catches anything tracked. `.venv/`, `__pycache__/`, `Blurt.iconset/`, `.claude/`, etc. are gitignored (or untracked) and shouldn't trip the dirty check. We use `git status --porcelain` (not `git diff --quiet`) precisely because the former covers staged and unstaged changes equally.

- **Daemon running on a detached HEAD or a non-`main` branch.** `git reset --hard origin/main` works regardless — but the user would lose their non-`main` work. We refuse if `git symbolic-ref --short HEAD` doesn't equal `main`, with label "Update unavailable: not on main." This is rare (the install script clones the default branch) but cheap to guard against.

- **Network offline at startup.** Background check fails fast (default `git fetch` timeout is short, plus we pass `timeout=10` to subprocess). Label becomes `Check failed — see logs`. No effect on dictation.

- **`uv sync` removes the currently-running Python's `.venv`.** Should not happen — `uv sync` rewrites `.venv/lib/...` while the existing `.venv/bin/python` keeps running its already-imported modules. New code is loaded on the next process start (post-restart). This is the same pattern Homebrew uses for in-place upgrades of running processes.

- **User clicks "Update" twice quickly.** `_update_lock` (non-blocking) absorbs the second click as a no-op. Label is also disabled in `Updating…` state.

- **Helper script clashes between updates.** `/tmp/blurt-restart.sh` is overwritten on each update; no concurrent writers because of the lock. If two installs of blurt ever ran on the same Mac, this would clash — out of scope, single-instance assumption matches the rest of the app.

- **Restart fails (LaunchAgent disappears mid-update).** Helper exits non-zero, no daemon to surface it. User notices "menu bar icon gone." Diagnostic via `./service.sh logs` or restarting Terminal-side. We don't add a watchdog for v1.

## Testing strategy

Most of this code shells out to `git`, `uv`, and `launchctl`. Unit-testing those is low-value compared to the manual checklist.

### Unit tests

- `check_for_updates()` against a temporary git repo (created in `tmp_path`): set up two branches with controllable divergence, exercise `up_to_date`, `update_available`, and `dirty` outcomes. `check_failed` exercised by pointing the function at a non-repo directory.
- Dataclass round-tripping in the label-state-transition function (`_refresh_update_label`). Pure logic, table-driven.

### Manual verification checklist

To be executed before declaring the task done:

1. Cold start with no remote changes → menu shows current SHA; background check populates "Up to date ✓" within a couple seconds; clicking the label runs a check and shows the same.
2. `git commit --allow-empty -m "test"` on `main`, push to origin, restart blurt → background check shows "Update to abc1234 (1 commit behind)". Click → "Updating…" → menu bar disappears → returns ~3 s later showing the new SHA in the version line.
3. Touch a tracked file (e.g. add a print to blurt.py without committing), restart, click "Check for Updates" → label is greyed `Update unavailable: local changes`. Stash or revert, click again → returns to normal.
4. Switch to a feature branch (`git checkout -b foo`), restart, click → `Update unavailable: not on main`.
5. Cut network (turn Wi-Fi off), click "Check for Updates" → `Check failed — see logs`. Restore network, click again → recovers.
6. Run `uv run python blurt.py` directly (no LaunchAgent), open the menu → update item is greyed with `Update requires LaunchAgent install`.
7. Try to click "Update" while holding the hotkey to record → menu item is disabled (rumps already prevents the click visually).
8. Simulate uv failure: rename `pyproject.toml` to break sync, push a commit, click update → `Update failed — see logs`. Restore `pyproject.toml` manually, click update again → recovers.

## Open questions

None. All decisions settled in the brainstorming exchange preceding this spec:

- Track `main`, not GitHub Releases.
- Background check at startup; no periodic polling.
- Refuse dirty checkouts; no "discard and update" confirmation.
- No automatic rollback on `uv sync` failure; surface error.
- Detached helper script (`/tmp/blurt-restart.sh`) for the daemon restart.
- Disable the update flow when the LaunchAgent isn't installed.
- Lands in the same PR as the type-mode hotkey spec.
