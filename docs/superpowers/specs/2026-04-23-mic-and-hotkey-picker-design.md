# Microphone and Hotkey Pickers — Design

**Date:** 2026-04-23
**Status:** Approved, ready for planning

## Motivation

Today `blurt.py` calls `sd.InputStream(...)` without a `device` argument, so capture always uses the macOS system default input. A user with a webcam plugged in wants to dictate from the webcam mic without changing the system-wide default (which they still want set to the built-in mic for calls). The push-to-talk hotkey is likewise hardcoded to `Key.alt_r`. Both should be user-selectable from the menu bar and persist across restarts.

## Scope

In scope:

- A "Microphone" submenu that lists input devices, marks the active one, and switches on click.
- A "Hotkey" submenu with a curated list of single modifier keys and F13–F19, same interaction model.
- A JSON config file at `~/Library/Application Support/blurt/config.json` that persists both selections.
- A "device missing" state: if the saved microphone isn't currently available (or disappears mid-session), the menu bar shows `⚠️` and recording is disabled until the user picks a device that exists.

Out of scope (deliberate):

- Sample-rate handling beyond what `sounddevice` already does (it resamples device-native rates down to our requested 16 kHz).
- Per-device gain or VAD threshold tuning.
- A "record any key" hotkey capture dialog.
- An `.app` bundle or any packaging changes.

## User-visible design

### Menu structure

```
🎙 blurt
├── Microphone          ▸   System Default ✓
│                           MacBook Pro Microphone
│                           C922 Pro Stream Webcam
│                           ───
│                           Refresh devices
├── Hotkey              ▸   Right Option ✓
│                           Left Option
│                           Right Command
│                           Left Command
│                           Right Control
│                           Right Shift
│                           F13
│                           F14
│                           F15
│                           F16
│                           F17
│                           F18
│                           F19
└── Quit blurt
```

### Microphone submenu

- Built by querying `sounddevice.query_devices()` and filtering to entries with `max_input_channels > 0`.
- The first entry is always "System Default" (stored in config as `null`, passed to `InputStream` as `device=None`).
- Subsequent entries are the current input devices in `sounddevice`'s order, labeled by device name.
- A checkmark (rumps `state=1`) sits next to the active selection.
- Clicking an entry: writes the selection to config, updates the checkmarks, and is effective on the next hold — no process restart, no re-prompting for mic permission.
- The last entry is "Refresh devices", which re-queries `sounddevice` and rebuilds the submenu. This is needed because rumps menus are not regenerated on open, and because plugging/unplugging a USB audio device during a session would otherwise leave the list stale.

### Hotkey submenu

- Fixed list, in this order:
  1. Right Option (`alt_r`) — default
  2. Left Option (`alt_l`)
  3. Right Command (`cmd_r`)
  4. Left Command (`cmd_l`)
  5. Right Control (`ctrl_r`)
  6. Right Shift (`shift_r`)
  7. F13 through F19 (`f13` … `f19`)
- Checkmark pattern identical to the microphone submenu.
- Clicking mutates `Hotkey.trigger_key`. The `pynput` listener keeps running and does not need to be restarted; the press/release handlers just compare against the new attribute.
- Caps Lock and Fn are intentionally excluded: Caps Lock is OS-latched and does not fire clean press/release events through pynput on macOS, and Fn is filtered by the OS before pynput sees it.

### Device-missing state (option B from brainstorming)

- On startup, after loading config, the app enumerates input devices. If `config.microphone` is non-null and does not match any current device name, the app enters a disabled state: menu bar shows `⚠️`, `on_press` logs a message and does not start a recording.
- Picking any entry in the Microphone submenu (including "System Default") exits the disabled state, sets `STATE.title` back to `🎙`, and the next hold records normally.
- Mid-session disappearance: if `InputStream.start()` raises (for example, because the user unplugged the webcam while it was selected), the handler catches the exception, enters the same disabled state, and logs. The user resolves it the same way — pick a valid device from the menu, or Refresh + pick.

## Persistence

### Location

`~/Library/Application Support/blurt/config.json`

This is macOS-native and keeps user data separate from the source checkout (important because the app typically runs as a LaunchAgent from the checkout directory). The directory is created on first write if missing.

### Shape

```json
{
  "microphone": null,
  "hotkey": "alt_r"
}
```

- `microphone`: either `null` (meaning "use system default, pass `device=None`") or a device name string. We store the name rather than the index because indices shuffle on plug/unplug, while names are stable across a session and across reboots for the same hardware.
- `hotkey`: a `pynput.keyboard.Key` attribute name. Loaded via `getattr(Key, value)`; the value is validated against the known `HOTKEY_CHOICES` list on load. Unknown or missing values fall back to `"alt_r"`.

### Error handling

- File missing → treat as defaults (`null`, `"alt_r"`), write on first selection change.
- File present but malformed JSON → log a warning, use defaults, do not overwrite (so the user has a chance to recover a hand-edited file).
- Unknown `hotkey` value → log a warning, fall back to `"alt_r"`.
- Writes are best-effort: on write failure we log but do not crash. Next selection change will try again.

## Code shape

All changes stay in `blurt.py` — the single-file ethos is called out in the README and should be preserved. Approximate layout of additions:

- **Module-level constants and helpers**
  - `CONFIG_PATH = Path.home() / "Library/Application Support/blurt/config.json"`
  - `HOTKEY_CHOICES: list[tuple[str, str]]` — ordered `(label, Key-attr-name)` pairs, single source of truth for the submenu and for load-time validation.
  - `DEFAULT_CONFIG: dict` — `{"microphone": None, "hotkey": "alt_r"}`.
  - `load_config() -> dict` — reads and validates; returns a merged dict with all expected keys.
  - `save_config(cfg: dict) -> None` — creates parent dir if needed, writes atomically (write-to-temp + rename) so a crash mid-write does not leave an empty file.
  - `list_input_devices() -> list[str]` — queries sounddevice and returns input device names in the order `sounddevice` reports them. Duplicates are preserved in the menu (rare, but happens with two identical USB devices); lookup at stream-open time resolves to the first device whose name matches the saved config value.

- **`Recorder`**
  - `start(device: str | None)` — accepts a device name (or `None` for default), passes it straight to `sd.InputStream(..., device=device)`. Raises on failure; caller handles.

- **`Hotkey`**
  - New attributes: `trigger_key: Key` (initialized from config), `disabled: bool` (initialized from the startup device check).
  - `on_press` / `on_release` compare `key == self.trigger_key` and early-return if `self.disabled`.
  - On `Recorder.start` failure, sets `self.disabled = True` and `STATE.title = "⚠️"`.

- **`MenuApp`**
  - New helper methods: `_build_mic_menu()`, `_build_hotkey_menu()`, `_on_mic_pick(sender)`, `_on_hotkey_pick(sender)`, `_on_refresh_devices(sender)`.
  - Menu construction in `__init__` wires both submenus plus the existing Quit item.
  - Mic/hotkey callbacks update the shared `Hotkey` instance, write config, and refresh checkmarks in place.
  - The `MenuApp` holds a reference to the `Hotkey` instance (passed in from `main`) so callbacks can mutate it.

Rough size: +100 to +120 lines, zero removed.

## Testing strategy

This is a small, interactive macOS menu-bar app. There is no existing test suite, and the interesting behaviors (menu clicks, rumps state, live device enumeration, pynput listener) all live at the boundary with OS-level APIs that are hard to unit-test meaningfully. The existing codebase carries no tests for analogous reasons.

Plan:

- **Unit tests** for the pure helpers: `load_config` (valid / missing / malformed / unknown-hotkey cases) and `save_config` (round-trip, atomic write). These need no OS integration.
- **Manual verification checklist** for the integration-flavored pieces, to be run before we consider the task complete:
  1. Fresh start with no config file → default menu state, Right Option still works, config file gets written on first mic pick.
  2. Plug webcam, pick it from the menu, hold hotkey, confirm transcription is coming from the webcam mic (sanity-check by muting the built-in and speaking).
  3. Unplug webcam mid-session → menu bar goes to ⚠️, hold does nothing, log line appears. Pick System Default → returns to 🎙, works.
  4. Restart app with webcam still unplugged → starts in ⚠️ state, Refresh devices + System Default returns it to working order.
  5. Switch hotkey to F13 → old key no longer fires, F13 does. Switch back, same.
  6. Hand-edit config to unknown hotkey string → app starts with default, logs warning, does not overwrite file until a new selection is made.

## Open questions

None. All decisions settled in the brainstorming exchange preceding this spec:

- Menu submenus for both pickers (vs. config-file-only or record-a-key).
- Config stored in `~/Library/Application Support/blurt/config.json`.
- Device stored by name, not index.
- Missing-device fallback is option B (warning state, disabled recording).
- Curated hotkey list, not free-form capture.
