# Microphone and Hotkey Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add menu-bar submenus for selecting the microphone input device and the push-to-talk hotkey, persisted to a JSON config file, with a disabled/warning state when the configured microphone is unavailable.

**Architecture:** All changes live in `blurt.py` to preserve the single-file ethos stated in the README. Config loading/saving is a pair of pure helpers that round-trip a `dict` to `~/Library/Application Support/blurt/config.json` using an atomic write-then-rename. The `Recorder` gains a `device` argument that flows straight through to `sd.InputStream`. The `Hotkey` class grows a mutable `trigger_key` attribute (replacing the hardcoded `Key.alt_r` comparison) and a `disabled` flag that `on_press` honors. The `MenuApp` builds two submenus and routes clicks to callbacks that mutate the shared `Hotkey` instance and persist the new config.

**Tech Stack:** Python 3.11+, `sounddevice` (audio capture + device enumeration), `pynput` (hotkey), `rumps` (menu bar), `pytest` (new dev dep for the pure helpers), stdlib `json` + `pathlib`.

**Spec reference:** `docs/superpowers/specs/2026-04-23-mic-and-hotkey-picker-design.md`

---

## File Structure

- **Modify:** `blurt.py` — all runtime changes (config helpers, `Recorder.start` signature, `Hotkey` attributes, `MenuApp` submenus and callbacks, `main` wiring).
- **Create:** `tests/test_config.py` — unit tests for `load_config` / `save_config`.
- **Create:** `tests/__init__.py` — empty; marks the tests package.
- **Modify:** `pyproject.toml` — add `pytest` to a `dev` dependency group so `uv sync --group dev` installs it.

The tests only cover the pure config helpers — no tests for menu building, device enumeration, or the pynput listener, since those are OS-boundary code consistent with the rest of the file.

---

## Task 1: Add pytest dev dependency and scaffold the tests directory

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/__init__.py`

- [ ] **Step 1: Edit `pyproject.toml` to add a dev dependency group**

Replace the file contents with:

```toml
[project]
name = "blurt"
version = "0.1.0"
description = "Push-to-talk local dictation for macOS (Parakeet-MLX)"
requires-python = ">=3.11"
dependencies = [
    "parakeet-mlx>=0.3.0",
    "sounddevice>=0.4.7",
    "numpy>=1.26",
    "pynput>=1.7.7",
    "rumps>=0.4.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
]

[tool.uv]
package = false
```

- [ ] **Step 2: Install the dev group**

Run: `uv sync --group dev`
Expected: `uv.lock` regenerates, pytest installs into `.venv`. Final line should be `Installed N packages` or `Audited N packages` with no errors.

- [ ] **Step 3: Create an empty tests package marker**

Create `tests/__init__.py` with content:

```python
```

(Empty file — zero bytes. This lets pytest treat `tests/` as a package and keeps imports clean if we ever add shared fixtures.)

- [ ] **Step 4: Verify pytest runs against an empty collection**

Run: `uv run pytest tests/ -v`
Expected: pytest prints `no tests ran` (exit code 5). This confirms the runner is wired.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/__init__.py
git commit -m "Add pytest dev dependency and tests package scaffold"
```

---

## Task 2: Write failing tests for `load_config`

**Files:**
- Create: `tests/test_config.py`

The helper we're specifying in this task and the next has this contract:

- `load_config() -> dict` reads `CONFIG_PATH`, validates it, returns a dict with keys `microphone` (str or None) and `hotkey` (str). Unknown/malformed input falls back to `DEFAULT_CONFIG = {"microphone": None, "hotkey": "alt_r"}`. Unknown hotkey values are replaced with the default; a warning is printed to stderr but the function still returns.
- `save_config(cfg: dict) -> None` writes atomically (temp file + `os.replace`), creating the parent directory if missing. Write failures print a warning to stderr but do not raise.

Both functions read/write `blurt.CONFIG_PATH`. Tests monkeypatch it to a `tmp_path` location.

`HOTKEY_CHOICES` is the authoritative list of `(label, attr_name)` tuples used by the validator. Task 4 defines its contents; for now tests only need `"alt_r"` to be valid and `"bogus_key"` to be invalid.

- [ ] **Step 1: Create the test file with all cases**

Create `tests/test_config.py`:

```python
import json
from pathlib import Path

import pytest


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch):
    """Point blurt.CONFIG_PATH at a throwaway location for each test."""
    import blurt
    path = tmp_path / "Application Support" / "blurt" / "config.json"
    monkeypatch.setattr(blurt, "CONFIG_PATH", path)
    return path


def test_load_config_missing_file_returns_defaults(config_path):
    import blurt
    assert not config_path.exists()
    cfg = blurt.load_config()
    assert cfg == {"microphone": None, "hotkey": "alt_r"}


def test_load_config_valid_file(config_path):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"microphone": "C922", "hotkey": "f13"}))
    cfg = blurt.load_config()
    assert cfg == {"microphone": "C922", "hotkey": "f13"}


def test_load_config_malformed_json_returns_defaults(config_path, capsys):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{not valid json")
    cfg = blurt.load_config()
    assert cfg == {"microphone": None, "hotkey": "alt_r"}
    assert "config" in capsys.readouterr().err.lower()


def test_load_config_unknown_hotkey_falls_back(config_path, capsys):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"microphone": None, "hotkey": "bogus_key"}))
    cfg = blurt.load_config()
    assert cfg["hotkey"] == "alt_r"
    assert cfg["microphone"] is None
    assert "hotkey" in capsys.readouterr().err.lower()


def test_load_config_partial_file_merges_defaults(config_path):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"microphone": "MyMic"}))
    cfg = blurt.load_config()
    assert cfg == {"microphone": "MyMic", "hotkey": "alt_r"}


def test_load_config_does_not_overwrite_malformed_file(config_path):
    import blurt
    config_path.parent.mkdir(parents=True)
    original = "{not valid json"
    config_path.write_text(original)
    blurt.load_config()
    assert config_path.read_text() == original
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: All six tests fail with `AttributeError: module 'blurt' has no attribute 'load_config'` (or `CONFIG_PATH`). This confirms the tests exercise real names that don't yet exist.

---

## Task 3: Implement `load_config` and `save_config` to make Task 2's tests pass

**Files:**
- Modify: `blurt.py` — add imports and helpers near the top of the module, after the existing constants block.

- [ ] **Step 1: Add imports**

At the top of `blurt.py`, the current import block ends at line 21 (`from pynput.keyboard import Controller as KBController, Key`). Add two lines immediately after it:

```python
import json
import os
from pathlib import Path
```

(Matches the existing stdlib-first ordering.)

- [ ] **Step 2: Add the `HOTKEY_CHOICES`, `CONFIG_PATH`, and `DEFAULT_CONFIG` constants**

Find the block ending with `SOUND_VOLUME = "0.3"` (around line 41 of the original file) and add this block immediately after it:

```python
CONFIG_PATH = Path.home() / "Library" / "Application Support" / "blurt" / "config.json"

# Ordered list of (menu label, pynput Key attribute name).
# Single source of truth for the Hotkey submenu and for config validation.
HOTKEY_CHOICES: list[tuple[str, str]] = [
    ("Right Option", "alt_r"),
    ("Left Option", "alt_l"),
    ("Right Command", "cmd_r"),
    ("Left Command", "cmd_l"),
    ("Right Control", "ctrl_r"),
    ("Right Shift", "shift_r"),
    ("F13", "f13"),
    ("F14", "f14"),
    ("F15", "f15"),
    ("F16", "f16"),
    ("F17", "f17"),
    ("F18", "f18"),
    ("F19", "f19"),
]
_HOTKEY_ATTRS = {attr for _, attr in HOTKEY_CHOICES}
DEFAULT_CONFIG: dict = {"microphone": None, "hotkey": "alt_r"}
```

- [ ] **Step 3: Add `load_config` and `save_config`**

Immediately after the constants above, add:

```python
def load_config() -> dict:
    """Load config from CONFIG_PATH, merging with defaults.

    Missing file → defaults. Malformed JSON → defaults + warning, file untouched.
    Unknown hotkey value → defaults['hotkey'] + warning.
    """
    cfg = dict(DEFAULT_CONFIG)
    if not CONFIG_PATH.exists():
        return cfg
    try:
        raw = json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"[blurt] config unreadable ({e}); using defaults", file=sys.stderr)
        return cfg
    if not isinstance(raw, dict):
        print(f"[blurt] config is not a JSON object; using defaults", file=sys.stderr)
        return cfg
    if "microphone" in raw and (raw["microphone"] is None or isinstance(raw["microphone"], str)):
        cfg["microphone"] = raw["microphone"]
    if "hotkey" in raw:
        if isinstance(raw["hotkey"], str) and raw["hotkey"] in _HOTKEY_ATTRS:
            cfg["hotkey"] = raw["hotkey"]
        else:
            print(
                f"[blurt] unknown hotkey {raw['hotkey']!r} in config; using {DEFAULT_CONFIG['hotkey']!r}",
                file=sys.stderr,
            )
    return cfg


def save_config(cfg: dict) -> None:
    """Write config atomically. Failures log and return; never raise."""
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2))
        os.replace(tmp, CONFIG_PATH)
    except OSError as e:
        print(f"[blurt] config save failed: {e}", file=sys.stderr)
```

- [ ] **Step 4: Run Task 2's tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: All six tests PASS.

- [ ] **Step 5: Add round-trip and atomic-write tests**

Append these tests to `tests/test_config.py`:

```python
def test_save_config_round_trip(config_path):
    import blurt
    blurt.save_config({"microphone": "MyMic", "hotkey": "f14"})
    assert blurt.load_config() == {"microphone": "MyMic", "hotkey": "f14"}


def test_save_config_creates_parent_dir(config_path):
    import blurt
    assert not config_path.parent.exists()
    blurt.save_config({"microphone": None, "hotkey": "alt_r"})
    assert config_path.exists()


def test_save_config_write_failure_does_not_raise(tmp_path, monkeypatch, capsys):
    import blurt
    # Point CONFIG_PATH at a child of a file — mkdir will fail.
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setattr(blurt, "CONFIG_PATH", blocker / "sub" / "config.json")
    blurt.save_config({"microphone": None, "hotkey": "alt_r"})
    assert "config save failed" in capsys.readouterr().err.lower()
```

- [ ] **Step 6: Run the full test file**

Run: `uv run pytest tests/test_config.py -v`
Expected: All nine tests PASS.

- [ ] **Step 7: Commit**

```bash
git add blurt.py tests/test_config.py
git commit -m "Add config load/save helpers with validation and atomic write"
```

---

## Task 4: Make `Recorder.start` accept a device argument

**Files:**
- Modify: `blurt.py` — the `Recorder` class.

- [ ] **Step 1: Change the `start` signature and pass `device` through**

Find the `Recorder.start` method (around line 192 of the original file). Replace:

```python
    def start(self) -> None:
        self._frames = []
        self._start_ts = time.monotonic()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            callback=self._cb,
            blocksize=int(SAMPLE_RATE * BLOCK_SEC),
        )
        self._stream.start()
```

with:

```python
    def start(self, device: str | None = None) -> None:
        self._frames = []
        self._start_ts = time.monotonic()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            callback=self._cb,
            blocksize=int(SAMPLE_RATE * BLOCK_SEC),
            device=device,
        )
        self._stream.start()
```

`device=None` is `sounddevice`'s existing "system default" sentinel, so passing it when no override is configured preserves today's behavior exactly.

- [ ] **Step 2: Verify the file still imports**

Run: `uv run python -c "import blurt"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add blurt.py
git commit -m "Let Recorder.start accept a sounddevice device argument"
```

---

## Task 5: Add `list_input_devices` helper

**Files:**
- Modify: `blurt.py` — add helper near the other top-level helpers.

- [ ] **Step 1: Add the function**

Immediately after the `save_config` function added in Task 3, add:

```python
def list_input_devices() -> list[str]:
    """Return input device names in sounddevice's reported order.

    Duplicates (two identical USB devices) are preserved; lookup at
    stream-open time resolves to the first name match.
    """
    try:
        devices = sd.query_devices()
    except Exception as e:
        print(f"[blurt] device query failed: {e}", file=sys.stderr)
        return []
    return [
        d["name"]
        for d in devices
        if d.get("max_input_channels", 0) > 0
    ]
```

- [ ] **Step 2: Smoke-test it from the REPL**

Run: `uv run python -c "import blurt; print(blurt.list_input_devices())"`
Expected: a list of strings, at least one element (your built-in mic). Exit 0. If it prints `[]`, investigate before proceeding — later tasks assume this works.

- [ ] **Step 3: Commit**

```bash
git add blurt.py
git commit -m "Add list_input_devices helper"
```

---

## Task 6: Give `Hotkey` mutable `trigger_key` and `disabled` attributes

**Files:**
- Modify: `blurt.py` — the `Hotkey` class.

- [ ] **Step 1: Update `Hotkey.__init__` and handlers**

Find the `Hotkey` class (around line 220 of the original file). Replace the whole class body with:

```python
class Hotkey:
    def __init__(self, trigger_key: Key, device: str | None) -> None:
        self._recording = False
        self._rec = Recorder()
        self._lock = threading.Lock()
        self.trigger_key = trigger_key
        self.device = device
        self.disabled = False

    def on_press(self, key):
        if key != self.trigger_key:
            return
        if self.disabled:
            return
        with self._lock:
            if self._recording:
                return
            try:
                self._rec.start(self.device)
                self._recording = True
                STATE.title = "🔴"
                play_start()
            except Exception as e:
                print(f"[blurt] record start: {e}", file=sys.stderr)
                STATE.title = "⚠️"
                self.disabled = True

    def on_release(self, key):
        if key != self.trigger_key:
            return
        with self._lock:
            if not self._recording:
                return
            self._recording = False
        try:
            audio, duration = self._rec.stop()
        except Exception as e:
            print(f"[blurt] record stop: {e}", file=sys.stderr)
            STATE.title = "⚠️"
            return
        play_stop()
        if duration < MIN_HOLD_SEC or audio.size < int(SAMPLE_RATE * MIN_AUDIO_SEC):
            STATE.title = "🎙"
            return
        speech, max_rms, voiced = has_speech(audio)
        if not speech:
            print(
                f"[blurt] {duration:.1f}s skipped (silence; max_rms={max_rms:.4f}, voiced={voiced})",
                flush=True,
            )
            STATE.title = "🎙"
            return
        STATE.title = "✨"
        threading.Thread(target=self._work, args=(audio, duration), daemon=True).start()

    def _work(self, audio: np.ndarray, duration: float) -> None:
        try:
            model = load_model()
            t0 = time.monotonic()
            result = _transcribe_array(model, audio)
            raw = getattr(result, "text", str(result)).strip()
            text = cleanup(raw)
            dt = (time.monotonic() - t0) * 1000
            print(f"[blurt] {duration:.1f}s → {dt:.0f}ms raw={raw!r} clean={text!r}", flush=True)
            if text:
                STATE.last_text = text
                paste_text(text)
        except Exception as e:
            print(f"[blurt] transcribe/paste: {e}", file=sys.stderr)
            STATE.title = "⚠️"
            return
        STATE.title = "🎙"
```

The only behavioral changes vs. the original:
- `__init__` takes `trigger_key` and `device`.
- `on_press` / `on_release` compare against `self.trigger_key`.
- `on_press` short-circuits if `self.disabled`.
- `on_press` passes `self.device` to `self._rec.start(...)`.
- On `start()` failure, `self.disabled = True` is set so subsequent holds are ignored until the menu clears it.

- [ ] **Step 2: Verify the file still imports**

Run: `uv run python -c "import blurt"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add blurt.py
git commit -m "Make Hotkey trigger key and device configurable at runtime"
```

---

## Task 7: Wire config loading and startup device check into `main`

**Files:**
- Modify: `blurt.py` — the `main` function and the `MenuApp` constructor signature.

This task introduces the `MenuApp(hotkey=...)` signature used by callbacks in Task 8. It's shaped the way it is so that Task 8 can add menus without touching `main` again.

- [ ] **Step 1: Update `MenuApp.__init__` to accept a `Hotkey`**

Find the `MenuApp` class (around line 290 of the original file). Replace its `__init__` and `_tick`/`_quit` methods with:

```python
class MenuApp(rumps.App):
    def __init__(self, hotkey: "Hotkey") -> None:
        super().__init__("blurt", title="🎙", quit_button=None)
        self.hotkey = hotkey
        self.menu = [rumps.MenuItem("Quit blurt", callback=self._quit)]

    @rumps.timer(0.1)
    def _tick(self, _):
        if self.title != STATE.title:
            self.title = STATE.title

    def _quit(self, _):
        rumps.quit_application()
```

The submenus get added in Task 8; this task just plumbs the `Hotkey` reference in.

- [ ] **Step 2: Replace `main` with a version that loads config, resolves the device, and passes `Hotkey` into `MenuApp`**

Find `def main() -> int:` (around line 306 of the original file). Replace the function body with:

```python
def main() -> int:
    cfg = load_config()

    # Resolve saved hotkey attr → Key. load_config guarantees this is valid.
    trigger_key = getattr(Key, cfg["hotkey"])

    # If a specific microphone was saved but isn't plugged in, start disabled.
    device = cfg["microphone"]
    disabled = False
    if device is not None and device not in list_input_devices():
        print(
            f"[blurt] configured microphone {device!r} not found; "
            "pick one from the menu or plug it back in",
            file=sys.stderr,
        )
        disabled = True
        STATE.title = "⚠️"

    hk = Hotkey(trigger_key=trigger_key, device=device)
    hk.disabled = disabled

    threading.Thread(target=load_model, daemon=True).start()
    listener = keyboard.Listener(on_press=hk.on_press, on_release=hk.on_release)
    listener.start()
    print("[blurt] hold the configured hotkey to talk. ⌘-click menu bar to quit.", flush=True)

    def sigterm(*_):
        listener.stop()
        rumps.quit_application()

    signal.signal(signal.SIGINT, sigterm)
    signal.signal(signal.SIGTERM, sigterm)

    MenuApp(hotkey=hk).run()
    return 0
```

- [ ] **Step 3: Verify the file still imports**

Run: `uv run python -c "import blurt"`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add blurt.py
git commit -m "Load config at startup and pass Hotkey into MenuApp"
```

---

## Task 8: Build the Microphone and Hotkey submenus

**Files:**
- Modify: `blurt.py` — the `MenuApp` class.

- [ ] **Step 1: Replace `MenuApp` with the full menu-building version**

Replace the whole `MenuApp` class (the one from Task 7) with:

```python
class MenuApp(rumps.App):
    _SYSTEM_DEFAULT_LABEL = "System Default"

    def __init__(self, hotkey: "Hotkey") -> None:
        super().__init__("blurt", title="🎙", quit_button=None)
        self.hotkey = hotkey
        self._mic_menu = rumps.MenuItem("Microphone")
        self._hotkey_menu = rumps.MenuItem("Hotkey")
        self._build_mic_menu()
        self._build_hotkey_menu()
        self.menu = [
            self._mic_menu,
            self._hotkey_menu,
            None,  # separator
            rumps.MenuItem("Quit blurt", callback=self._quit),
        ]

    @rumps.timer(0.1)
    def _tick(self, _):
        if self.title != STATE.title:
            self.title = STATE.title

    def _quit(self, _):
        rumps.quit_application()

    # --- microphone submenu --------------------------------------------------

    def _build_mic_menu(self) -> None:
        self._mic_menu.clear()

        default_item = rumps.MenuItem(
            self._SYSTEM_DEFAULT_LABEL,
            callback=self._on_mic_pick,
        )
        default_item.state = 1 if self.hotkey.device is None else 0
        self._mic_menu.add(default_item)

        for name in list_input_devices():
            item = rumps.MenuItem(name, callback=self._on_mic_pick)
            item.state = 1 if self.hotkey.device == name else 0
            self._mic_menu.add(item)

        self._mic_menu.add(rumps.separator)
        self._mic_menu.add(
            rumps.MenuItem("Refresh devices", callback=self._on_refresh_devices)
        )

    def _on_mic_pick(self, sender) -> None:
        label = str(sender.title)
        new_device = None if label == self._SYSTEM_DEFAULT_LABEL else label
        self.hotkey.device = new_device
        # Picking any real entry means the device exists right now,
        # so clear the disabled/warning state.
        self.hotkey.disabled = False
        STATE.title = "🎙"
        save_config({"microphone": new_device, "hotkey": self._current_hotkey_attr()})
        self._refresh_mic_checkmarks()

    def _on_refresh_devices(self, _sender) -> None:
        self._build_mic_menu()

    def _refresh_mic_checkmarks(self) -> None:
        for item in self._mic_menu.values():
            if not isinstance(item, rumps.MenuItem):
                continue
            title = str(item.title)
            if title == self._SYSTEM_DEFAULT_LABEL:
                item.state = 1 if self.hotkey.device is None else 0
            elif title == "Refresh devices":
                item.state = 0
            else:
                item.state = 1 if self.hotkey.device == title else 0

    # --- hotkey submenu ------------------------------------------------------

    def _build_hotkey_menu(self) -> None:
        self._hotkey_menu.clear()
        current = self._current_hotkey_attr()
        for label, attr in HOTKEY_CHOICES:
            item = rumps.MenuItem(label, callback=self._on_hotkey_pick)
            item.state = 1 if attr == current else 0
            self._hotkey_menu.add(item)

    def _on_hotkey_pick(self, sender) -> None:
        label = str(sender.title)
        match = next((attr for lbl, attr in HOTKEY_CHOICES if lbl == label), None)
        if match is None:
            return
        self.hotkey.trigger_key = getattr(Key, match)
        save_config({"microphone": self.hotkey.device, "hotkey": match})
        for item in self._hotkey_menu.values():
            if isinstance(item, rumps.MenuItem):
                item.state = 1 if str(item.title) == label else 0

    def _current_hotkey_attr(self) -> str:
        name = getattr(self.hotkey.trigger_key, "name", None)
        if name in {attr for _, attr in HOTKEY_CHOICES}:
            return name
        return DEFAULT_CONFIG["hotkey"]
```

Notes on the implementation:

- `rumps.MenuItem.clear()` removes all children. We call it at the start of both builders so rebuilding is idempotent.
- Separators in rumps are added via `rumps.separator` when calling `.add()`, which is why `_build_mic_menu` translates `None` into `rumps.separator`.
- Checkmark state uses `item.state = 1` / `0`. rumps doesn't enforce radio-group semantics, so we flip every sibling manually.
- `_current_hotkey_attr` uses `Key.alt_r.name == "alt_r"` (pynput's `Key` enum exposes `.name`) to map the current trigger back to its attr-name string, which is the form we persist.

- [ ] **Step 2: Verify the file still imports**

Run: `uv run python -c "import blurt"`
Expected: no output, exit 0.

- [ ] **Step 3: Sanity-run the full app briefly**

Run: `uv run python blurt.py`
Expected: model loads (~10 s), menu bar icon appears, clicking it shows Microphone ▸ and Hotkey ▸ submenus. Ctrl-C in the terminal to exit.

If the menus do not appear, inspect for rumps API mismatches before moving on — later tasks assume this works.

- [ ] **Step 4: Commit**

```bash
git add blurt.py
git commit -m "Add Microphone and Hotkey submenus with persisted selection"
```

---

## Task 9: Manual verification

**Files:** None. This task is a checklist run against the running app.

The integration-flavored behaviors in this feature (menu clicks, device hot-plug, hotkey swap) are not covered by automated tests. Run each of these and only mark the task complete when every one passes.

- [ ] **Scenario 1: Fresh start, no config file**

Steps:
1. `rm -f "$HOME/Library/Application Support/blurt/config.json"`
2. `uv run python blurt.py`
3. Click menu bar icon → Microphone → confirm "System Default" is checked. Hotkey → confirm "Right Option" is checked.
4. Hold Right Option, speak a short phrase, release. Confirm text pastes.
5. Ctrl-C.

Expected: `~/Library/Application Support/blurt/config.json` still does not exist (we don't write it until a selection change).

- [ ] **Scenario 2: Pick a specific microphone and persist it**

Steps:
1. Plug in the webcam.
2. `uv run python blurt.py`
3. Microphone → click the webcam entry. Confirm its checkmark appears and "System Default" loses its check.
4. Confirm `~/Library/Application Support/blurt/config.json` now contains `"microphone": "<webcam name>"`.
5. Hold Right Option, speak, release. Confirm text pastes (and, if you want to be sure, mute the built-in mic in System Settings → Sound → Input and confirm dictation still works).
6. Ctrl-C, restart the app, confirm the webcam stays checked in the menu.

- [ ] **Scenario 3: Missing device on startup → ⚠️ state**

Steps:
1. With the webcam selected and the app running, Ctrl-C it.
2. Unplug the webcam.
3. `uv run python blurt.py`
4. Confirm menu bar shows `⚠️` once startup completes.
5. Hold Right Option — confirm nothing happens and no error bubbles up (a disabled-state log line is fine).
6. Click Microphone → System Default.
7. Confirm menu bar returns to `🎙`. Hold Right Option, confirm dictation works.

- [ ] **Scenario 4: Device disappears mid-session**

Steps:
1. With the webcam plugged in and selected, start the app and confirm dictation works on the webcam.
2. Unplug the webcam.
3. Hold Right Option. Confirm menu bar goes to `⚠️` and a `record start` error is logged.
4. Click Microphone → System Default. Confirm menu bar returns to `🎙` and dictation works again.

- [ ] **Scenario 5: Hotkey swap**

Steps:
1. Click Hotkey → F13.
2. Confirm F13's check appears, Right Option's clears.
3. Confirm config file now contains `"hotkey": "f13"`.
4. Press and hold Right Option — confirm nothing happens.
5. Press and hold F13 — confirm dictation works (your keyboard must have F13; on a Mac laptop without an external keyboard you may need to swap to Right Command instead for this scenario).
6. Switch back to Right Option in the menu; confirm it takes effect immediately.

- [ ] **Scenario 6: Refresh devices**

Steps:
1. Start the app with the webcam unplugged.
2. Open Microphone submenu, note the webcam is absent.
3. Without closing the app, plug in the webcam.
4. Click Microphone → Refresh devices.
5. Re-open the submenu; confirm the webcam is now listed.

- [ ] **Scenario 7: Hand-edited malformed config**

Steps:
1. `printf '%s' '{not valid json' > "$HOME/Library/Application Support/blurt/config.json"`
2. `uv run python blurt.py`
3. Confirm stderr prints a `config unreadable` warning.
4. Confirm the menu defaults are back (System Default, Right Option).
5. Confirm the config file still contains `{not valid json` (we don't overwrite until a selection change).
6. Pick any entry; confirm the file is now valid JSON.

- [ ] **Final commit (if anything was tweaked during verification)**

If any of the above scenarios exposed a bug and you made a fix, commit it with a message starting `Fix: ...`. Otherwise, no commit needed — verification is a gate, not a code-producing step.

---

## Self-Review Notes

**Spec coverage:**
- Microphone submenu → Task 8.
- Hotkey submenu → Task 8.
- Persistence (`~/Library/Application Support/blurt/config.json`, JSON shape) → Tasks 2–3, used in 7–8.
- Device stored by name, not index → Task 5 + Task 7 startup check + Task 8 callback.
- Missing-device fallback (option B: ⚠️, disabled) → Task 6 (disabled attribute + on_press short-circuit + start-failure handling), Task 7 (startup check), Task 8 (recovery on selection).
- Curated hotkey list with Caps Lock / Fn omitted → Task 3 (`HOTKEY_CHOICES`), Task 8 (submenu).
- Refresh devices entry → Task 8.
- Testing strategy (unit tests for config, manual scenarios for everything else) → Tasks 2–3 (unit), Task 9 (manual).

All spec sections are covered. No placeholders, and no forward references to names not introduced by an earlier task (`HOTKEY_CHOICES`, `CONFIG_PATH`, `DEFAULT_CONFIG`, `load_config`, `save_config`, `list_input_devices`, `Hotkey.trigger_key`, `Hotkey.device`, `Hotkey.disabled`, `MenuApp.hotkey` are all defined before use).
