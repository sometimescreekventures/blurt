# Self-Registering Permissions + One-Command Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** blurt requests its own Accessibility + Input Monitoring grants via macOS TCC prompts, self-restarts when they land, and a new `setup.sh` makes a fresh Mac a single command.

**Architecture:** A new `# --- permissions ---` section in `blurt.py` wraps the TCC APIs (`AXIsProcessTrusted*` from ApplicationServices, `CGPreflightListenEventAccess`/`CGRequestListenEventAccess` from Quartz) behind two boolean functions that degrade to `True` if the APIs are unavailable. `main()` calls `ensure_permissions()`, which — when grants are missing — warns, sets `⚠️`, and spawns a daemon watcher thread that fires the OS prompts and polls until granted, then reuses `restart_daemon()` (exit-nonzero → launchd relaunch). `setup.sh` chains the existing scripts.

**Tech Stack:** Python 3.12, pyobjc (Quartz already present via pynput; ApplicationServices added), bash, pytest.

**Spec:** `docs/superpowers/specs/2026-06-12-self-registering-permissions-design.md`

---

### Task 1: Add the ApplicationServices dependency

**Files:**
- Modify: `pyproject.toml` (dependencies list, ~line 6)

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, extend `dependencies`:

```toml
dependencies = [
    "parakeet-mlx>=0.3.0",
    "sounddevice>=0.4.7",
    "soundfile>=0.12",
    "numpy>=1.26",
    "pynput>=1.7.7",
    "rumps>=0.4.0",
    "pyobjc-framework-applicationservices>=10",
]
```

- [ ] **Step 2: Sync and verify the import works**

Run: `uv sync && .venv/bin/python -c "from ApplicationServices import AXIsProcessTrusted; print(type(AXIsProcessTrusted()))"`
Expected: prints `<class 'bool'>` (value depends on the terminal's grants — type is what matters).

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit --no-gpg-sign -m "Add pyobjc-framework-applicationservices dependency"
```

(All commits in this repo use `--no-gpg-sign`; the configured 1Password signing can't run non-interactively.)

---

### Task 2: TCC wrapper functions

**Files:**
- Modify: `blurt.py` (new `# --- permissions ---` section, before `# --- menu bar ---`; new import block after the existing imports)
- Create: `tests/test_permissions.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_permissions.py`:

```python
import threading

import pytest

import blurt


# --- TCC wrappers -------------------------------------------------------------

def test_accessibility_granted_reads_ax(monkeypatch):
    monkeypatch.setattr(blurt, "_TCC_AVAILABLE", True, raising=False)
    monkeypatch.setattr(blurt, "AXIsProcessTrusted", lambda: False, raising=False)
    assert blurt.accessibility_granted() is False
    monkeypatch.setattr(blurt, "AXIsProcessTrusted", lambda: True, raising=False)
    assert blurt.accessibility_granted() is True


def test_accessibility_prompt_uses_options_dict(monkeypatch):
    seen = {}
    monkeypatch.setattr(blurt, "_TCC_AVAILABLE", True, raising=False)
    monkeypatch.setattr(blurt, "kAXTrustedCheckOptionPrompt", "prompt-key", raising=False)
    monkeypatch.setattr(
        blurt, "AXIsProcessTrustedWithOptions",
        lambda opts: seen.update(opts) or False, raising=False,
    )
    assert blurt.accessibility_granted(prompt=True) is False
    assert seen == {"prompt-key": True}


def test_input_monitoring_granted_reads_preflight(monkeypatch):
    monkeypatch.setattr(blurt, "_TCC_AVAILABLE", True, raising=False)
    monkeypatch.setattr(blurt, "CGPreflightListenEventAccess", lambda: False, raising=False)
    assert blurt.input_monitoring_granted() is False


def test_input_monitoring_prompt_uses_request(monkeypatch):
    calls = []
    monkeypatch.setattr(blurt, "_TCC_AVAILABLE", True, raising=False)
    monkeypatch.setattr(
        blurt, "CGRequestListenEventAccess",
        lambda: calls.append(1) or True, raising=False,
    )
    assert blurt.input_monitoring_granted(prompt=True) is True
    assert calls == [1]


def test_checks_assume_granted_without_tcc(monkeypatch):
    monkeypatch.setattr(blurt, "_TCC_AVAILABLE", False, raising=False)
    assert blurt.accessibility_granted() is True
    assert blurt.input_monitoring_granted(prompt=True) is True


def test_checks_assume_granted_when_api_raises(monkeypatch):
    monkeypatch.setattr(blurt, "_TCC_AVAILABLE", True, raising=False)
    def boom():
        raise RuntimeError("tcc broke")
    monkeypatch.setattr(blurt, "AXIsProcessTrusted", boom, raising=False)
    monkeypatch.setattr(blurt, "CGPreflightListenEventAccess", boom, raising=False)
    assert blurt.accessibility_granted() is True
    assert blurt.input_monitoring_granted() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_permissions.py -q`
Expected: FAIL — `AttributeError: <module 'blurt'> has no attribute 'accessibility_granted'`

- [ ] **Step 3: Implement the wrappers**

In `blurt.py`, after the `from pynput.keyboard import ...` import line, add:

```python
# TCC permission APIs. Quartz ships with pynput; ApplicationServices is our
# own dependency. If either is missing (unexpected macOS/pyobjc combo), the
# permission checks below degrade to "assume granted" so startup never blocks.
try:
    from ApplicationServices import (
        AXIsProcessTrusted,
        AXIsProcessTrustedWithOptions,
        kAXTrustedCheckOptionPrompt,
    )
    from Quartz import CGPreflightListenEventAccess, CGRequestListenEventAccess
    _TCC_AVAILABLE = True
except ImportError as _tcc_err:
    print(f"[blurt] TCC APIs unavailable ({_tcc_err}); permission checks disabled", file=sys.stderr)
    _TCC_AVAILABLE = False
```

Before the `# --- menu bar ---` section, add:

```python
# --- permissions --------------------------------------------------------------

def accessibility_granted(prompt: bool = False) -> bool:
    """True if the Accessibility (AX) grant is present.

    prompt=True additionally shows the macOS grant dialog (at most once per
    binary per TCC state) and registers the binary in the settings pane.
    """
    if not _TCC_AVAILABLE:
        return True
    try:
        if prompt:
            return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
        return bool(AXIsProcessTrusted())
    except Exception as e:
        print(f"[blurt] accessibility check failed ({e}); assuming granted", file=sys.stderr)
        return True


def input_monitoring_granted(prompt: bool = False) -> bool:
    """True if the Input Monitoring grant is present. prompt=True as above."""
    if not _TCC_AVAILABLE:
        return True
    try:
        if prompt:
            return bool(CGRequestListenEventAccess())
        return bool(CGPreflightListenEventAccess())
    except Exception as e:
        print(f"[blurt] input monitoring check failed ({e}); assuming granted", file=sys.stderr)
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_permissions.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add blurt.py tests/test_permissions.py
git commit --no-gpg-sign -m "Add TCC permission check/request wrappers"
```

---

### Task 3: Prompt helper + grant watcher

**Files:**
- Modify: `blurt.py` (extend the `# --- permissions ---` section)
- Modify: `tests/test_permissions.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_permissions.py`:

```python
# --- request_missing_permissions ----------------------------------------------

def test_request_only_prompts_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(
        blurt, "accessibility_granted",
        lambda prompt=False: calls.append(("ax", prompt)) or False,
    )
    monkeypatch.setattr(
        blurt, "input_monitoring_granted",
        lambda prompt=False: calls.append(("im", prompt)) or True,
    )
    blurt.request_missing_permissions()
    assert ("ax", True) in calls
    assert ("im", True) not in calls


# --- watcher --------------------------------------------------------------------

def _watch(granted, **kw):
    defaults = dict(
        request=lambda: None,
        has_agent=lambda: True,
        meeting_active=threading.Event(),
        restart=lambda: pytest.fail("restart not expected"),
        poll_sec=0.01,
    )
    defaults.update(kw)
    blurt.watch_for_permission_grants(granted, **defaults)


def test_watcher_requests_then_restarts_when_granted():
    calls = []
    _watch(
        lambda: True,
        request=lambda: calls.append("request"),
        restart=lambda: calls.append("restart"),
    )
    assert calls == ["request", "restart"]


def test_watcher_no_agent_returns_without_restart():
    _watch(lambda: True, has_agent=lambda: False)  # restart would fail the test


def test_watcher_polls_until_granted():
    state = {"n": 0}
    def granted():
        state["n"] += 1
        return state["n"] >= 3
    restarts = []
    _watch(granted, restart=lambda: restarts.append(1))
    assert restarts == [1]
    assert state["n"] == 3


def test_watcher_defers_restart_during_meeting():
    meeting = threading.Event()
    meeting.set()
    state = {"n": 0}
    def granted():
        state["n"] += 1
        if state["n"] >= 2:
            meeting.clear()
        return True
    restarts = []
    _watch(granted, meeting_active=meeting, restart=lambda: restarts.append(1))
    assert restarts == [1]
    assert state["n"] >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_permissions.py -q`
Expected: new tests FAIL — `AttributeError: ... no attribute 'request_missing_permissions'`

- [ ] **Step 3: Implement**

Append to the `# --- permissions ---` section in `blurt.py` (module constant goes with the other constants at the top, next to `CONTINUATION_SEC`):

```python
PERMISSION_POLL_SEC = 5.0
```

```python
def request_missing_permissions() -> None:
    """Fire the OS grant dialog for each permission that is missing."""
    if not accessibility_granted():
        accessibility_granted(prompt=True)
    if not input_monitoring_granted():
        input_monitoring_granted(prompt=True)


def watch_for_permission_grants(
    granted: Callable[[], bool],
    *,
    request: Callable[[], None] = request_missing_permissions,
    has_agent: Callable[[], bool] = has_launchagent,
    meeting_active: threading.Event,
    restart: Callable[[], None] = restart_daemon,
    poll_sec: float = PERMISSION_POLL_SEC,
) -> None:
    """Prompt for missing grants, poll until they land, then restart.

    Runs on a daemon thread (spawned by ensure_permissions) so the OS dialogs
    can never block startup. Once granted: under the LaunchAgent we exit
    non-zero so launchd relaunches us with the grants effective (Input
    Monitoring only applies to a fresh process); interactively we can only
    tell the user to restart. A live meeting recording defers the restart —
    same guard as self-update.
    """
    request()
    while True:
        if granted():
            if not has_agent():
                print("[blurt] permissions granted; restart blurt to pick them up", flush=True)
                return
            if not meeting_active.is_set():
                print("[blurt] permissions granted; restarting", flush=True)
                restart()
                return
        time.sleep(poll_sec)
```

Note `watch_for_permission_grants` must be defined *after* `has_launchagent`
and `restart_daemon` (it references them as defaults) — placing the whole
permissions section just before `# --- menu bar ---` satisfies this.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_permissions.py -q`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add blurt.py tests/test_permissions.py
git commit --no-gpg-sign -m "Add permission prompt helper and grant watcher"
```

---

### Task 4: ensure_permissions + main() wiring

**Files:**
- Modify: `blurt.py` (end of `# --- permissions ---` section; `main()`)
- Modify: `tests/test_permissions.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_permissions.py`:

```python
# --- ensure_permissions ---------------------------------------------------------

def test_ensure_permissions_all_granted_no_watcher(monkeypatch):
    monkeypatch.setattr(blurt, "accessibility_granted", lambda prompt=False: True)
    monkeypatch.setattr(blurt, "input_monitoring_granted", lambda prompt=False: True)
    started = []
    monkeypatch.setattr(
        blurt, "watch_for_permission_grants", lambda *a, **k: started.append(1)
    )
    blurt.STATE.title = "🎙"
    assert blurt.ensure_permissions(threading.Event()) is True
    assert started == []
    assert blurt.STATE.title == "🎙"


def test_ensure_permissions_missing_warns_and_watches(monkeypatch):
    monkeypatch.setattr(blurt, "accessibility_granted", lambda prompt=False: False)
    monkeypatch.setattr(blurt, "input_monitoring_granted", lambda prompt=False: True)
    ran = threading.Event()
    monkeypatch.setattr(
        blurt, "watch_for_permission_grants", lambda *a, **k: ran.set()
    )
    blurt.STATE.title = "🎙"
    assert blurt.ensure_permissions(threading.Event()) is False
    assert ran.wait(2.0), "watcher thread did not start"
    assert blurt.STATE.title == "⚠️"
    blurt.STATE.title = "🎙"  # restore for other tests
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_permissions.py -q`
Expected: the two new tests FAIL — `AttributeError: ... no attribute 'ensure_permissions'`

- [ ] **Step 3: Implement**

Append to the `# --- permissions ---` section:

```python
def ensure_permissions(meeting_active: threading.Event) -> bool:
    """True if all TCC grants are present.

    Otherwise: log what's missing, set the warning icon, and hand off to the
    watcher thread (which prompts and polls). Startup continues either way —
    the pynput listener still starts, matching pre-TCC-check behavior.
    """
    missing = [
        name
        for name, ok in (
            ("Accessibility", accessibility_granted()),
            ("Input Monitoring", input_monitoring_granted()),
        )
        if not ok
    ]
    if not missing:
        return True
    print(
        f"[blurt] missing permissions: {', '.join(missing)} — requesting from macOS. "
        "Approve the dialogs (or toggle blurt's python in System Settings → "
        "Privacy & Security); blurt restarts itself once granted.",
        file=sys.stderr,
    )
    STATE.title = "⚠️"
    threading.Thread(
        target=watch_for_permission_grants,
        args=(lambda: accessibility_granted() and input_monitoring_granted(),),
        kwargs={"meeting_active": meeting_active},
        daemon=True,
    ).start()
    return False
```

In `main()`, immediately after `meeting_active = threading.Event()`:

```python
    ensure_permissions(meeting_active)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_permissions.py -q`
Expected: 13 passed

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: all pass (38 pre-existing + 13 new = 51)

- [ ] **Step 6: Commit**

```bash
git add blurt.py tests/test_permissions.py
git commit --no-gpg-sign -m "Check and request TCC permissions at startup"
```

---

### Task 5: setup.sh

**Files:**
- Create: `setup.sh` (mode 755)

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# One-command setup for a fresh Mac:
#
#   ./setup.sh
#
# Runs install.sh (Xcode CLT, uv, venv, deps), installs + starts the
# LaunchAgent (which also builds Blurt.app), then points you at the
# permission dialogs blurt fires on first launch. Idempotent — re-run it
# any time to repair an install.

set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

"$here/install.sh"
"$here/service.sh" install
"$here/service.sh" start

cat <<'EOF'

────────────────────────────────────────────────────────────────────────
✅ blurt is installed and running.

macOS will now show permission dialogs from blurt's python binary:
  • Accessibility       → approve / toggle on
  • Input Monitoring    → approve / toggle on
blurt restarts itself automatically once both are granted.
  • Microphone          → prompts the first time you dictate; click Allow

Dismissed a dialog? The entries are already registered — toggle them in
System Settings → Privacy & Security → Accessibility / Input Monitoring,
or run ./permissions.sh for a guided walkthrough.
────────────────────────────────────────────────────────────────────────
EOF
```

- [ ] **Step 2: Verify**

Run: `chmod +x setup.sh && bash -n setup.sh && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add setup.sh
git commit --no-gpg-sign -m "Add one-command setup.sh"
```

---

### Task 6: README updates

**Files:**
- Modify: `README.md` (Install, Permissions, Troubleshooting sections)

- [ ] **Step 1: Quick deploy section**

Replace the current quick-deploy code block and its trailing paragraph with:

```markdown
### Quick deploy to a new Mac

​```bash
git clone https://github.com/sometimescreekventures/blurt.git
cd blurt
./setup.sh
​```

`setup.sh` installs the toolchain and the LaunchAgent, then blurt itself asks
macOS for the permissions it needs — approve the **Accessibility** and
**Input Monitoring** dialogs (blurt restarts itself once both are granted),
and click Allow on the **Microphone** popup the first time you dictate.
TCC grants are per-machine and can't be scripted, but two toggles is as small
as macOS lets it get. If you dismissed the dialogs, run `./permissions.sh`
for a guided walkthrough.
```

(Remove the backslash-escapes around the inner code fence — they're only here
to nest it in this plan.)

- [ ] **Step 2: "What each step does" section**

Add at the top of the list:

```markdown
`./setup.sh` runs the three steps below in order — it's all most installs need.
```

(Keep the existing `install.sh` / `service.sh` / `make-app.sh` descriptions.)

- [ ] **Step 3: Permissions section lead**

Replace the section's first paragraph with:

```markdown
You need three TCC permissions granted to the Python interpreter that runs
`blurt.py`. **You normally don't do anything manual here**: on startup blurt
checks its grants and asks macOS for whatever is missing — the binary
self-registers in the right panes, you flip the toggles in the OS dialogs,
and blurt restarts itself. The menu-bar icon shows `⚠️` until grants land.
The rest of this section is fallback material: `./permissions.sh` walks the
drag-and-drop path if the dialogs were dismissed, and the details below help
if something is still stuck.
```

- [ ] **Step 4: Troubleshooting entry**

Add before the "Transcription quality is poor" entry:

```markdown
**blurt stopped working after an update (hotkeys dead, `⚠️` in menu bar).**
A uv Python upgrade can change the interpreter's path, which makes macOS
forget the Accessibility / Input Monitoring grants. blurt detects this at
startup and re-fires the permission dialogs — flip the toggles and it
restarts itself. (`./service.sh logs` shows `missing permissions: …` when
this is the cause.)
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit --no-gpg-sign -m "Document self-registering permissions and setup.sh"
```

---

### Task 7: Verification + PR

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q`
Expected: 51 passed

- [ ] **Step 2: Live smoke test on this (already-granted) Mac**

Run: `./service.sh restart && sleep 5 && ./service.sh logs | tail -8`
Expected: normal startup (`hold the configured hotkey to talk`, `ready.`), **no** `missing permissions:` line, no `⚠️` behavior change — the granted path is a no-op.

- [ ] **Step 3: Push and open PR (do NOT merge — Sean merges)**

```bash
git push -u origin self-registering-permissions
gh pr create --title "Self-registering permissions + one-command setup.sh" --body "..."
```

Body summarizes: TCC self-prompting, watcher self-restart, setup.sh, README; notes the true first-run manual checklist (spec §Testing) is best done on a secondary Mac or after `tccutil reset`.

- [ ] **Step 4: Report**

Hand Sean the PR link and the manual first-run checklist from the spec.
