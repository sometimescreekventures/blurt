# Type Clipboard as Keystrokes — Design

**Date:** 2026-06-02
**Status:** Approved, ready for planning

## Motivation

Blurt's type-mode hotkey (shipped in the previous change) synthesizes per-character keystrokes for dictation, so text reaches VDI clients that mangle ⌘V. The same primitive solves a second, non-dictation problem: the user has prepared text (copied from somewhere) that they need to enter into a VDI text field, but the VDI blocks clipboard paste while permitting keyboard input. Reading the whole block aloud to dictate it would be absurd.

This feature adds a dedicated hotkey that takes whatever is on the clipboard and types it out as keystrokes — no microphone, no transcription. It reuses the existing keystroke-emission path.

## Scope

In scope:

- A third hotkey, **`clipboard_hotkey`** (default `ctrl_l`, Left Control), persisted in `config.json` alongside `hotkey` and `type_hotkey`.
- Adding **`Left Control` (`ctrl_l`)** to `HOTKEY_CHOICES` so it is selectable in all three pickers.
- A new **"Clipboard Hotkey"** submenu mirroring the existing two pickers.
- **Three-way** collision prevention: no two of the three hotkeys may be bound to the same key — enforced both at config-load time and via cross-submenu grey-out.
- Hold-to-fire semantics: hold the clipboard hotkey ≥ `MIN_HOLD_SEC`, release → read clipboard via `pbpaste()` and type it verbatim.
- **Abort**: while a clipboard type is in progress, pressing the clipboard hotkey again stops it. The typing loop checks an abort flag *before each keystroke*.
- Menu-bar icon shows `⌨️` while a clipboard type is in progress, returning to `🎙` when done or aborted.

Out of scope (deliberate):

- Any change to the two dictation hotkeys or their delivery paths.
- Clipboard transformation (trimming, case-folding, find/replace). Text is typed verbatim.
- A maximum-length cap or confirmation prompt for very large clipboards. Hold-to-fire + abort are the safety mechanisms; a size cap is YAGNI for a single-user tool.
- Reading any clipboard format other than plain UTF-8 text (`pbpaste` default). Rich text, images, files are ignored.
- Restoring or modifying the clipboard. This feature only reads it.

## Behavior

### Trigger

- The clipboard hotkey is **hold-to-fire**, consistent with the two dictation hotkeys, but it never starts the `Recorder` and never invokes the model.
- On press: record a timestamp, mark clipboard-pending. On release: if held ≥ `MIN_HOLD_SEC`, spawn a worker thread that reads the clipboard and types it. Holds shorter than `MIN_HOLD_SEC` are ignored (accidental brush).
- Mutual exclusion with dictation: if a dictation recording is in progress, a clipboard-hotkey press is ignored. If a clipboard type is in progress, dictation-hotkey presses are ignored. This matches the existing "second hotkey ignored while busy" rule.

### Delivery

- The worker calls `pbpaste()`. Empty/whitespace-only clipboard → no-op with a one-line log (`[blurt] clipboard empty; nothing to type`).
- Text is typed **verbatim** through the keystroke emitter — no continuation-space prepending (that behavior is dictation-specific). Newlines in the clipboard are emitted as Return keys by `pynput`, so multi-line text types correctly.
- `TYPE_KEY_DELAY` is respected exactly as in `type_text`: when > 0, sleep between characters; when 0, burst.
- `_last_paste_ts` is **not** updated by clipboard typing — the continuation-spacing heuristic is about back-to-back dictation, not bulk clipboard entry, and updating it could insert a spurious leading space on a subsequent dictation. (Documented decision; revisit only if it proves surprising in use.)

### Abort

- A `threading.Event` (`_clip_abort`) is created per clipboard-type run.
- The worker loop checks `_clip_abort.is_set()` **before emitting each character**; if set, it stops immediately, logs how many characters were typed, and restores the idle icon.
- Pressing the clipboard hotkey while `_clip_typing` is true sets `_clip_abort` and returns (it does not start a new type).
- **Known minor edge:** because the abort key is a modifier (Left Control), there is a window of at most one in-flight character during which Control is physically held, which could produce a single `Ctrl+<char>` combo in the target app. Checking the flag before each keystroke shrinks this to ≤ 1 character. A stray Ctrl+key is a minor annoyance, not destructive, and is the accepted cost of an abort that needs no second key and causes no focus change. Users who attach an external keyboard with F13–F19 can rebind the clipboard hotkey to an inert F-key for zero combo risk.

## Persistence

### Shape

`~/Library/Application Support/blurt/config.json`:

```json
{
  "microphone": null,
  "hotkey": "alt_r",
  "type_hotkey": "cmd_r",
  "clipboard_hotkey": "ctrl_l"
}
```

- `clipboard_hotkey`: same validation as the other two — a `pynput.keyboard.Key` attribute name in `HOTKEY_CHOICES`. Default and fallback: `"ctrl_l"`.

### Three-way collision handling at load time

`load_config` already reassigns `type_hotkey` if it collides with `hotkey`. This generalizes to three keys:

- Validate each of the three against `HOTKEY_CHOICES`; unknown values fall back to their defaults with a warning.
- Then resolve collisions in priority order **hotkey > type_hotkey > clipboard_hotkey**: if `type_hotkey` equals `hotkey`, reassign it to the first `HOTKEY_CHOICES` entry not already used; if `clipboard_hotkey` equals either of the (now-resolved) other two, reassign it likewise. Each reassignment logs a warning. Because `HOTKEY_CHOICES` has far more than three entries, a non-colliding fallback always exists.

### Backwards compatibility

Existing configs lack `clipboard_hotkey`; `load_config` fills the default (`ctrl_l`) on load, persisted on the next menu-driven write. No migration step.

## Code shape

All edits in `blurt.py`. Estimated +60 to +80 lines, near-zero removed.

### `HOTKEY_CHOICES`

Add `("Left Control", "ctrl_l")` (placed adjacent to the existing `("Right Control", "ctrl_r")`). This makes Left Control selectable in all three pickers, which is the intended behavior.

### Constants

```python
DEFAULT_CONFIG = {
    "microphone": None,
    "hotkey": "alt_r",
    "type_hotkey": "cmd_r",
    "clipboard_hotkey": "ctrl_l",
}
```

### Keystroke emitter refactor

Extract the raw per-character emission out of `type_text` into a helper so both paths share it without duplicating the `TYPE_KEY_DELAY` branch:

```python
def _emit_keystrokes(text: str, abort: threading.Event | None = None) -> int:
    """Type text via synthesized keystrokes. Returns chars emitted.
    Stops early if `abort` is set (checked before each char)."""
    n = 0
    if TYPE_KEY_DELAY > 0.0:
        for ch in text:
            if abort is not None and abort.is_set():
                break
            _kb.type(ch)
            n += 1
            time.sleep(TYPE_KEY_DELAY)
    else:
        # Burst path still honors abort at a coarse granularity by chunking.
        for ch in text:
            if abort is not None and abort.is_set():
                break
            _kb.type(ch)
            n += 1
    return n
```

`type_text` keeps its continuation-space logic and `_last_paste_ts` update, then calls `_emit_keystrokes(text)` (no abort — dictation outputs are short). The clipboard path calls `_emit_keystrokes(clipboard_text, abort=self._clip_abort)` and does not touch `_last_paste_ts`.

Note: even on the `TYPE_KEY_DELAY == 0` path we now iterate per character so the abort flag is honored. This is a negligible cost (a Python-level loop around the same per-character `_kb.type` calls pynput already makes internally) and keeps abort behavior uniform.

### New free function

```python
def type_clipboard(abort: threading.Event) -> None:
    text = pbpaste()
    if not text.strip():
        print("[blurt] clipboard empty; nothing to type", flush=True)
        return
    n = _emit_keystrokes(text, abort=abort)
    if abort.is_set():
        print(f"[blurt] clipboard type aborted after {n} chars", flush=True)
    else:
        print(f"[blurt] clipboard typed {n} chars", flush=True)
```

### `Hotkey` class

- New attribute `clipboard_trigger_key: Key`, initialized from config.
- New state: `self._clip_typing: bool = False`, `self._clip_abort: threading.Event | None = None`, `self._clip_press_ts: float = 0.0`.
- `on_press` gains a third branch:
  - If `key == clipboard_trigger_key`:
    - If `self._clip_typing`: set `self._clip_abort.set()` and return (abort).
    - Else if a dictation recording is active: return (mutual exclusion).
    - Else: record `self._clip_press_ts = time.monotonic()`, set a `_clip_pending` marker.
- `on_release` gains a matching branch: if the released key is the clipboard key and `_clip_pending`, compute hold duration; if ≥ `MIN_HOLD_SEC`, start the clipboard worker thread (`_clip_typing = True`, `_clip_abort = threading.Event()`, `STATE.title = "⌨️"`), which calls `type_clipboard` and on completion clears `_clip_typing` and resets `STATE.title = "🎙"`.
- The existing paste/type branches early-return if `_clip_typing` is true (mutual exclusion the other direction).

State transitions are guarded by the existing `self._lock` where they touch shared recording/clip state, consistent with the current code.

### `MenuApp`

- New attribute `self._clipboard_hotkey_menu = rumps.MenuItem("Clipboard Hotkey")`.
- New helpers `_build_clipboard_hotkey_menu()` and `_on_clipboard_hotkey_pick(sender)`, mirroring the existing hotkey pick handlers.
- `_current_clipboard_hotkey_attr()` mirrors the other two accessors.
- `_refresh_hotkey_greyouts()` generalized to three submenus: for each submenu, grey out any entry whose attr is bound by *either of the other two* hotkeys.
- `_current_config()` includes `clipboard_hotkey`.
- `__init__` inserts the new submenu after `self._type_hotkey_menu`.

### `main`

Resolve `clipboard_trigger_key = getattr(Key, cfg["clipboard_hotkey"])` and pass it to the `Hotkey` constructor.

## Testing strategy

Consistent with prior specs: pure functions get unit tests; OS-boundary behavior gets a manual checklist.

### Unit tests (`tests/test_config.py`)

- Missing `clipboard_hotkey` → defaults to `"ctrl_l"`.
- Unknown `clipboard_hotkey` value → warning + default.
- Three-way collision: a config binding all three to the same key resolves to three distinct keys with warnings.
- Two-way collision involving clipboard (`clipboard_hotkey == type_hotkey`, distinct from `hotkey`) → clipboard reassigned.
- `ctrl_l` is present in `HOTKEY_CHOICES`.
- Round-trip of all four keys through `save_config` + `load_config`.

### Unit test for the emitter (new file `tests/test_emit.py`)

- `_emit_keystrokes` honors the abort flag: with a pre-set `threading.Event`, emitting returns 0 and types nothing. (Patch `blurt._kb` with a recording stub to count `.type` calls without real keyboard output.)
- `_emit_keystrokes` with no abort types every character: the stub records one `.type` call per character and the function returns `len(text)`.

### Manual verification checklist

1. Fresh start, no config → menu shows three pickers; Clipboard Hotkey submenu has Left Control checked; Left Control is greyed in the other two submenus; Right Option / Right Command greyed appropriately across all three.
2. Copy a multi-line block in TextEdit, click into an empty document, hold Left Control ~0.5s, release → the block types out verbatim including line breaks; clipboard unchanged.
3. In the VDI text field that blocks paste, copy text on the host, focus the VDI field, hold Left Control, release → text appears.
4. Set `TYPE_KEY_DELAY = 0.02`, copy a long paragraph, start typing, then press Left Control again mid-stream → typing stops promptly; log shows "aborted after N chars".
5. Empty the clipboard (copy an empty selection), hold Left Control → no-op, log shows "clipboard empty".
6. Try to bind two hotkeys to the same key via the menus → the conflicting entry is greyed and unclickable in the other submenus.
7. Hand-edit config to bind all three to `alt_r`, restart → load resolves to three distinct keys, warnings logged, menu reflects the resolved bindings.
8. While a dictation is recording (hold Right Option), tap Left Control → ignored, dictation unaffected. While a clipboard type is running, hold Right Option → ignored.

## Open questions

None. All decisions settled in the brainstorming exchange preceding this spec:

- Dedicated third hotkey (not a menu item, not a mode toggle on the existing type hotkey).
- Hold-to-fire (not tap-to-fire) for accidental-dump safety.
- Default `Left Control` — the only Control key physically present on a Mac built-in keyboard; added to `HOTKEY_CHOICES`.
- Abort by re-pressing the same hotkey; flag checked before each keystroke; one-char modifier-combo window accepted and documented.
- `⌨️` icon while typing.
- Clipboard typed verbatim; no continuation-space, no clipboard restore, no size cap.
