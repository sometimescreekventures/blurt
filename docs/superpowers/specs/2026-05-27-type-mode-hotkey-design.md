# Type-Mode Hotkey — Design

**Date:** 2026-05-27
**Status:** Approved, ready for planning

## Motivation

The current paste flow (`paste_text` in [blurt.py:241](../../../blurt.py#L241)) copies the transcript to the pasteboard and synthesizes ⌘V. Inside VDI clients (Citrix, VMware Horizon, RDP, etc.) the host's clipboard pass-through is often disabled, broken, or routed through a virtual channel that mangles the keystroke — the user observes a stray control character at the cursor instead of the dictated text.

We want a second, parallel push-to-talk hotkey that delivers the transcript by synthesizing per-character key events instead. This bypasses the clipboard entirely and reaches any focused text field that accepts keyboard input — including a VDI session window.

The choice is made per utterance: hold one key for the normal paste path, hold a different key for the type path. Both modes share VAD, transcription, cleanup, continuation-spacing, and menu-bar feedback.

## Scope

In scope:

- A second hotkey, **`type_hotkey`**, persisted in `config.json` alongside the existing `hotkey`. Default: `cmd_r` (Right Command).
- A new menu submenu, **"Type-mode Hotkey"**, structurally identical to the existing Hotkey submenu, listing the same `HOTKEY_CHOICES`.
- A new function, **`type_text(text)`**, that uses `pynput.keyboard.Controller.type()` to synthesize per-character keystrokes. No clipboard touched.
- A configurable per-character delay constant, **`TYPE_KEY_DELAY`** (default `0.0` seconds), for VDIs that drop input typed too quickly.
- Mutual exclusion between the two hotkey selections — a user cannot bind both to the same key.

Out of scope (deliberate):

- Any change to the existing paste path. `paste_text` and its clipboard save/restore behavior are unchanged.
- ASCII-folding or character substitution for unicode glyphs that some VDIs drop (smart quotes, em-dashes). If we hit this in practice we'll add it later; not v1.
- A global "always type" toggle. The user picked per-utterance.
- Per-utterance prompting / a key-capture dialog. Selection is via the menu, same as the existing hotkey picker.
- Changes to `service.sh`, `install.sh`, the LaunchAgent template, or the `.app` launcher.

## User-visible design

### Menu structure

```
🎙 blurt
├── Microphone           ▸  …
├── Hotkey               ▸  Right Option ✓
│                           Left Option
│                           Right Command       ← greyed (bound to type-mode)
│                           …
├── Type-mode Hotkey     ▸  Right Option        ← greyed (bound to paste)
│                           Left Option
│                           Right Command ✓
│                           Left Command
│                           Right Control
│                           Right Shift
│                           F13 … F19
└── Quit blurt
```

- The Type-mode Hotkey submenu mirrors the existing Hotkey submenu's contents and interaction model.
- The key currently bound in the *other* submenu is rendered disabled (rumps `set_callback(None)`) so the user cannot collide the two bindings. Both submenus update each other's greyed state whenever either selection changes.
- Picking a different key mutates the relevant `Hotkey` attribute in memory and writes config. No restart needed — the same `pynput` listener thread serves both hotkeys; the press/release handlers just compare against the current attribute values.

### Recording and menu-bar feedback

- Both hotkeys go through the same `Hotkey` instance and share its `_recording` / `_lock` state. Only one utterance can be active at a time: if the user is mid-recording with one hotkey and presses the other, the second press is ignored (same as a second press of the same hotkey today).
- The menu bar icon transitions are identical for both modes: `🎙` idle → `🔴` recording → `✨` transcribing → `🎙` idle.
- No new icon for type-mode. The user said they'll know which key they pressed; no need to surface it visually.

### Continuation spacing

`_last_paste_ts` is shared across both delivery paths. Dictating with the paste hotkey then immediately with the type hotkey (or vice versa) still gets the leading-space behavior within `CONTINUATION_SEC`. Conceptually `_last_paste_ts` becomes "last *delivery* timestamp" regardless of mechanism.

## `type_text` mechanics

```python
def type_text(text: str) -> None:
    global _last_paste_ts
    if not text:
        return
    now = time.monotonic()
    if now - _last_paste_ts < CONTINUATION_SEC and text[:1].isalnum():
        text = " " + text
    if TYPE_KEY_DELAY > 0.0:
        for ch in text:
            _kb.type(ch)
            time.sleep(TYPE_KEY_DELAY)
    else:
        _kb.type(text)
    _last_paste_ts = time.monotonic()
```

- `_kb.type(text)` is the pynput convenience method that synthesizes platform-appropriate keystrokes for each character. On macOS it goes through `CGEventCreateKeyboardEvent` with the unicode string set on the event, so most printable characters (including non-ASCII) Just Work for the OS-level text-input pipeline.
- We do not touch the clipboard at all on this path. No `pbcopy`/`pbpaste`, no restore thread.
- The two branches on `TYPE_KEY_DELAY` keep the common case (delay=0, one syscall path) cheap. With a non-zero delay we have to chunk per-character anyway to interleave the sleeps.

### Why a per-character delay knob

Some VDI clients buffer or drop fast key events synthesized at host-OS speed. We can't predict which VDI client a user has, so the knob is there from day one as a constant at the top of `blurt.py`, documented alongside the other tunables. Default is `0.0` (no delay). If a user reports dropped characters, the README's troubleshooting section will gain one line: "bump `TYPE_KEY_DELAY` to `0.005` or `0.01`."

## Persistence

### Shape

`~/Library/Application Support/blurt/config.json`:

```json
{
  "microphone": null,
  "hotkey": "alt_r",
  "type_hotkey": "cmd_r"
}
```

- `type_hotkey`: same validation rules as `hotkey`. A `pynput.keyboard.Key` attribute name, validated against `HOTKEY_CHOICES`.
- Default if missing or unknown: `"cmd_r"`.
- Collision rule at load time: if `hotkey == type_hotkey`, log a warning and override `type_hotkey` to the first entry of `HOTKEY_CHOICES` whose attribute isn't equal to `hotkey`. This is a defensive measure for hand-edited configs — the menu UI itself prevents the user from creating a collision.

### Backwards compatibility

Existing `config.json` files (written before this change) have no `type_hotkey` key. `load_config` fills the default (`cmd_r`) on load. The next config write — triggered by any menu selection — persists it. No migration step required.

## Code shape

All edits in `blurt.py`. Estimated +60 to +80 lines, zero removed. The single-file ethos is preserved.

### New constants

```python
TYPE_KEY_DELAY = 0.0  # per-character delay for type-mode; bump for slow VDIs
DEFAULT_CONFIG = {"microphone": None, "hotkey": "alt_r", "type_hotkey": "cmd_r"}
```

### `load_config` / `save_config`

- `load_config` gains a `type_hotkey` validation block mirroring the existing `hotkey` block, plus the collision check described above.
- `save_config` is unchanged structurally — it already serializes whatever dict it's given.

### `Hotkey` class

- New attribute `type_trigger_key: Key`, initialized from config alongside the existing `trigger_key`.
- `on_press`: branches on `key`. If it matches `trigger_key` *or* `type_trigger_key`, take the lock and start a recording. Stash which one matched on `self._active_mode: Literal["paste", "type"] | None` so `on_release` knows which delivery path to use.
- `on_release`: if `key` matches the active mode's key, stop. The `_work` thread is told which mode to use via an argument.
- `_work(audio, duration, mode)`: calls `paste_text(text)` if `mode == "paste"`, `type_text(text)` if `mode == "type"`. Everything else identical.

### `type_text`

New free function, placed directly below `paste_text` in the "clipboard + paste" section. Section header gets renamed to `"# --- delivery --------------------"` since it now covers both paths.

### `MenuApp`

- New attribute `_type_hotkey_menu = rumps.MenuItem("Type-mode Hotkey")`.
- New helpers: `_build_type_hotkey_menu()`, `_on_type_hotkey_pick(sender)`, plus a small `_refresh_hotkey_greyouts()` that both pick callbacks call to re-disable the cross-bound entry in the other submenu.
- `__init__` inserts `self._type_hotkey_menu` into `self.menu` immediately after `self._hotkey_menu`.

## Testing strategy

Consistent with the existing spec: this is a small macOS menu-bar app with no test suite, and the interesting behaviors live at OS boundaries that are hard to unit-test. We add unit tests where pure functions exist and rely on a manual checklist for the rest.

### Unit tests

- Extend the existing `load_config` tests to cover:
  - Missing `type_hotkey` → defaults to `"cmd_r"`.
  - Unknown `type_hotkey` value → warning + default.
  - Collision (`hotkey == type_hotkey` in file) → `type_hotkey` reassigned to a non-colliding default; warning logged.
- A round-trip test for `type_hotkey` through `save_config` + `load_config`.

### Manual verification checklist

To be executed before declaring the task done:

1. Fresh start with no config → menu shows Right Option ✓ in Hotkey submenu, Right Command ✓ in Type-mode Hotkey submenu. Right Option in the type submenu is greyed; Right Command in the paste submenu is greyed.
2. Hold Right Option, speak, release → text appears via paste (clipboard untouched at the end thanks to restore).
3. Hold Right Command, speak, release → text appears character-by-character at the cursor in a native macOS app (e.g. TextEdit). Clipboard contents from before are unchanged immediately after — confirms we did not touch it.
4. Hold Right Command inside a VDI session window where ⌘V is known to fail → text appears in the VDI text field correctly.
5. Switch Type-mode Hotkey from Right Command to F13 via the menu. Right Command should become re-enabled in the paste submenu; F13 should become greyed. Hold F13 → still types. Hold Right Command → does nothing.
6. Try to pick Right Option in the Type-mode submenu → entry is greyed, no callback fires. (Same in reverse from the paste submenu.)
7. Two dictations within `CONTINUATION_SEC`, one via paste and one via type → second one starts with a leading space.
8. Hand-edit config to set `type_hotkey = "alt_r"` (same as the paste hotkey) and restart → app logs the collision warning and reassigns `type_hotkey` to a non-colliding default. Menu reflects the reassigned value.
9. Bump `TYPE_KEY_DELAY` to `0.02`, restart, hold the type hotkey, speak a sentence → text appears more slowly (visibly one character at a time for long strings) but completely.

## Open questions

None. All decisions settled in the brainstorming exchange preceding this spec:

- Per-utterance delivery selection via two hotkeys (not a session toggle, not always-on).
- Default type-mode hotkey: Right Command.
- Single menu-bar icon (`✨`) for both modes.
- Configurable per-character delay constant in `blurt.py`, default `0.0`.
- Cross-submenu grey-out to prevent collision.
- No unicode-folding / ASCII fallback in v1.
