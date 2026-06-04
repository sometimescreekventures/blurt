# Meeting Recorder — Design

**Date:** 2026-06-04
**Status:** Approved, ready for planning

## Motivation

Blurt transcribes short push-to-talk utterances. The user also wants to capture long-form audio — a Teams call, a lecture, any meeting — and get a plain-text transcript they can paste into an AI or keep as notes. This is a different shape of problem from dictation: minutes-to-hours of audio, a toggle rather than a hold, and an output document rather than a paste at the cursor.

This first version (v1) captures from the **currently selected input device** (the existing microphone picker) — i.e. "record the room / my mic." It deliberately does *not* implement macOS system-audio capture. Because capture reads from whatever device the picker is set to, a user who later installs a loopback device (e.g. BlackHole) and selects it gets clean system audio with no code change. That upgrade path is the reason mic-source is an acceptable v1.

## Scope

In scope:

- A menu-bar toggle: **"Start Meeting Recording"** ⇄ **"Stop Meeting Recording (mm:ss)"** with a live elapsed timer.
- Long-form capture from the selected input device, streamed to a `.wav` on disk (not held in RAM).
- Live **chunked** transcription (~30 s fixed windows) appended to a `.txt` as the meeting proceeds.
- Output to `~/Documents/blurt-meetings/`: a timestamped `.txt` (with header/footer) and the raw `.wav` kept alongside.
- **Auto-open** the `.txt` in the default editor when recording stops.
- Distinct menu-bar icon `⏺` while a meeting records.
- Mutual exclusion with the mic-using dictation hotkeys; the clipboard-type hotkey remains usable.
- Graceful finalize on device loss, disk error, and app quit (SIGTERM).

Out of scope (deliberate):

- **System-audio / loopback capture** (BlackHole, ScreenCaptureKit, Core Audio process taps). v1 uses the selected input device only.
- **VAD-based segmentation** on silence boundaries. v1 uses fixed 30 s windows; word-boundary artifacts at chunk edges are accepted. Noted as a future refinement.
- Speaker diarization ("who said what").
- Summarization or any LLM post-processing. The transcript is raw text; the user takes it elsewhere.
- A hotkey trigger. v1 is menu-only (meetings are not time-critical to start, and it costs no hotkey).
- Pause/resume mid-meeting. Stop finalizes; there is no resume.
- Any new `config.json` key. Meeting recording reuses the selected microphone device.

## User-visible design

### Menu structure

```
🎙 blurt
├── Microphone ▸ …
├── Hotkey ▸ …
├── Type-mode Hotkey ▸ …
├── Clipboard Hotkey ▸ …
├── ─────────
├── Start Meeting Recording          ← toggles label + behavior
├── ─────────
├── Version: <sha> …
├── Check for Updates
└── Quit blurt
```

While recording, the item reads **"Stop Meeting Recording (12:34)"**, its label updated once per second by the existing `_tick` timer (or a dedicated 1 s timer). The menu-bar title shows `⏺`.

### Lifecycle

- **Start** (menu click): if the mic is unavailable (`disabled` state) the click is a no-op with a log line. Otherwise: create `MEETING_DIR` if missing; open the streaming `.wav` writer and the `.txt`; write the header; start capture + worker threads; set `STATE.title = "⏺"`; flip the menu label.
- **During**: capture thread enqueues blocks; worker writes WAV, accumulates 30 s chunks, VAD-skips silent chunks, transcribes voiced chunks, appends cleaned text to the `.txt`.
- **Stop** (menu click): signal threads to finish; flush and transcribe the final partial chunk; close the WAV; append the footer; auto-open the `.txt`; reset `STATE.title` (to `🎙`, or `⚠️` if the mic went missing); flip the menu label back.

### Output files

Folder: `~/Documents/blurt-meetings/` (constant `MEETING_DIR`).

Filenames use the local start time: `YYYY-MM-DD-HH-MM-meeting.txt` and `.wav`. If a file with that name already exists (two recordings in the same minute), append `-2`, `-3`, … to disambiguate.

`.txt` shape:

```
Meeting — 2026-06-04 14:30

<chunk 1 text> <chunk 2 text> <chunk 3 text> …

— ended 15:17, duration 00:47:12 —
```

The body is appended chunk-by-chunk (space-separated), so the file is continuously crash-safe. The footer is written only on a clean stop.

## Architecture

### Components

A new `MeetingRecorder` class encapsulates the feature. It owns no menu state; the `MenuApp` drives it (`start()` / `stop()`), mirroring how `MenuApp` already drives the dictation `Hotkey`.

- **Capture thread** — a `sounddevice.InputStream` opened on the selected device (`MenuApp` passes `hotkey.device`), 16 kHz mono float32, callback pushes each block into `self._queue` (a `queue.Queue`). This is the same device-selection path dictation uses.
- **Worker thread** — `self._worker()` loops:
  1. Drain blocks from `self._queue` (with a timeout so it notices stop).
  2. Append each block to the streaming `soundfile.SoundFile` WAV writer.
  3. Accumulate blocks into a `chunk_buf`; when it reaches `MEETING_CHUNK_SEC` worth of samples, run `_flush_chunk()`.
  4. On stop signal, exit the loop and run a final `_flush_chunk()` for the remainder.
- **`_flush_chunk(samples)`** — runs `has_speech`; if silent, returns. Otherwise `_transcribe_array` + `cleanup`, and appends the text to the `.txt` (opened in append mode, flushed per write).

### Data flow

```
InputStream callback ──blocks──▶ queue ──▶ worker
                                            ├──▶ soundfile WAV (streamed to disk)
                                            └──▶ chunk_buf ──30s──▶ _flush_chunk
                                                                      ├─ has_speech? ─ no ─▶ drop
                                                                      └─ yes ─▶ transcribe ─▶ append .txt
```

Only the current chunk (~30 s ≈ 480k float32 samples ≈ 1.9 MB) and the queue backlog live in RAM. The WAV grows on disk (~115 MB/hour at 16 kHz mono int16). Parakeet runs ~30× realtime on Apple Silicon, so a 30 s chunk transcribes in ~1 s — the worker keeps pace with the meeting and the queue stays shallow.

### WAV format

`soundfile.SoundFile(path, mode="w", samplerate=16000, channels=1, subtype="PCM_16")`. Capture blocks are float32 in [-1, 1]; `soundfile` converts to int16 on write. Keeping the WAV lets the user re-transcribe later with a better tool if v1 quality disappoints — the stated reason for trying mic-source first.

### Concurrency and mutual exclusion

- Meeting recording and dictation both need the mic; two `InputStream`s on one device is unreliable. Rule: **while `MeetingRecorder` is active, `Hotkey.on_press` ignores the paste and type (dictation) keys**; the **clipboard-type key still works** (no mic). Symmetrically, **starting a meeting is refused while a dictation recording is in progress** (the menu click logs and returns).
- The `MenuApp` holds the `MeetingRecorder`; `Hotkey` gets a reference (or a shared flag) so its `on_press` can check "is a meeting recording active?" cheaply. A simple shared `threading.Event` (`meeting_active`) set by `MeetingRecorder.start()` and cleared by `stop()`, readable by `Hotkey`, is the cleanest coupling — no back-reference to `MenuApp` needed.
- Transcription serializes on the existing `_mlx_lock` per `_transcribe_array` call, so meeting chunks and any clipboard/dictation MLX use never encode Metal concurrently. The lock is acquired per chunk (not held across the meeting), so other MLX work can interleave between chunks.

### Timestamps

`blurt.py` is normal Python, so `datetime.now()` provides start time, filename, header, and duration. (The Date/Math restriction applies only to Workflow JS scripts, not to this code.)

## Code shape

All edits in `blurt.py`, consistent with the single-file ethos the README celebrates. Meeting recording is self-contained enough to split into a `meeting.py` module later if the file keeps growing; v1 keeps it inline. Estimated +130 to +170 lines.

### Dependency declaration

`soundfile` is currently only a *transitive* dependency (pulled in under parakeet-mlx). Because this feature imports it directly, add `soundfile` to the `dependencies` list in `pyproject.toml` so it cannot disappear if a parent drops it. It is already present in the resolved `uv.lock`, so `uv sync` makes no actual install change — this only records the intent.

### New imports / constants

- `import soundfile as sf` and `from datetime import datetime` at the top.
- `MEETING_DIR = Path.home() / "Documents" / "blurt-meetings"`
- `MEETING_CHUNK_SEC = 30.0`

### New class `MeetingRecorder`

```python
class MeetingRecorder:
    def __init__(self, meeting_active: threading.Event) -> None: ...
    def is_active(self) -> bool: ...
    def start(self, device: str | None) -> bool:    # returns False if it couldn't start
    def stop(self) -> Path | None:                  # returns the .txt path (for auto-open)
    def elapsed(self) -> float:                      # seconds, for the menu timer
    # internals: _cb, _worker, _flush_chunk, _open_outputs, _finalize
```

- `start` opens outputs, starts the `InputStream` and worker thread, sets `meeting_active`, records `_start_dt`. Returns False (and logs) on device-open or file-open failure, leaving no partial state.
- `stop` clears `meeting_active`, signals the worker to drain-and-exit, joins it (bounded timeout), writes the footer, closes the WAV, returns the `.txt` path.
- Robust `_finalize` is idempotent and used by both `stop` and the error/quit paths.

### `Hotkey` changes

- Accept the shared `meeting_active: threading.Event` (constructor arg).
- In `on_press`, the paste/type branch early-returns when `meeting_active.is_set()`. The clipboard branch is unaffected. (One added condition; no structural change.)

### `MenuApp` changes

- Construct the `MeetingRecorder` (sharing the `meeting_active` Event with `Hotkey`).
- New menu item `self._meeting_item` inserted after the hotkey submenus with a separator on each side.
- `_on_meeting_toggle(sender)`:
  - If active → `path = recorder.stop()`; relabel to "Start Meeting Recording"; `subprocess.Popen(["open", str(path)])` to auto-open; reset title.
  - Else → refuse if a dictation is recording (`hotkey._recording`) or mic disabled, with a log; otherwise `recorder.start(hotkey.device)`, relabel, set title `⏺`.
- `_tick` (existing 0.1 s timer) updates the meeting item's label with `mm:ss` while active, and keeps the menu-bar title in sync (it already mirrors `STATE.title`).

### `main` changes

- Create `meeting_active = threading.Event()`; pass it to both `Hotkey` and `MeetingRecorder` (via `MenuApp`).
- Extend the `sigterm` handler to call `recorder.stop()` if active, so a quit/restart during a meeting still finalizes the transcript and WAV.

## Error handling

- **Device open fails on start** → `start` returns False; menu stays "Start…"; log line; no files created.
- **Device disappears mid-meeting** (`InputStream` callback error / read failure) → worker catches, triggers `_finalize`, menu resets, title → `⚠️`, the partial `.txt` is auto-opened. The user re-selects a device from the existing Microphone menu as today.
- **Disk write failure** (WAV or txt) → `_finalize` with whatever was written; log; menu resets.
- **Transcription error on a chunk** → log and skip that chunk; recording continues (one bad chunk shouldn't end the meeting). The WAV still has the audio for later re-transcription.
- **Quit while recording** → `sigterm` finalizes before exit.
- **Worker falls behind** (shouldn't, given ~30× realtime) → the queue grows; bounded only by RAM. Acceptable for v1; a backlog warning log fires if the queue exceeds a generous threshold.

## Testing strategy

Consistent with prior specs: pure/seam-testable logic gets unit tests; the OS-boundary pieces (live `InputStream`, menu clicks, auto-open) get a manual checklist.

### Unit tests (`tests/test_meeting.py`)

- **Output paths**: a filename helper produces `YYYY-MM-DD-HH-MM-meeting.{txt,wav}` from a fixed `datetime`, and disambiguates with `-2` when the target exists (use `tmp_path` + a monkeypatched `MEETING_DIR`).
- **Chunk flushing**: feed `_flush_chunk` a synthetic silent array → asserts nothing appended (via a `has_speech` path); feed a voiced array with `_transcribe_array` monkeypatched to a stub → asserts the stub's text is appended to the `.txt`.
- **`.txt` assembly**: header written on open, chunks appended space-separated, footer with a computed duration written on finalize — verified by reading the file back.
- **Mutual exclusion**: with `meeting_active` set, a `Hotkey.on_press` of the paste key does not start a recording; the clipboard key path is unaffected. (Stub the `Recorder` so no real stream opens.)

### Manual verification checklist

1. Click "Start Meeting Recording" with a valid mic → menu bar shows `⏺`, item reads "Stop Meeting Recording (00:0X)" and ticks up.
2. Speak/play audio for ~90 s, click Stop → `.txt` opens automatically; contains a header, the transcript, and a footer with a plausible duration; `.wav` exists alongside and plays back.
3. Put a Teams/YouTube video on the speakers (mic-source), record 60 s, Stop → transcript roughly matches the spoken content (quality caveat acknowledged for mic-source).
4. While a meeting is recording, hold Right Option (dictation) → ignored, meeting unaffected; hold Left Control (clipboard type) with text on the clipboard → still types.
5. Start a meeting, then unplug/disable the selected mic → recording finalizes, partial `.txt` opens, title shows `⚠️`.
6. Start a meeting, then Quit blurt (menu) → on next launch the `.txt` from before is complete through the last flushed chunk and has a footer (or is at least readable through the last chunk).
7. Two recordings started within the same minute → second file gets a `-2` suffix; neither overwrites the other.

## Open questions

None. All decisions settled in the brainstorming exchange preceding this spec:

- Audio source: selected input device (mic) for v1; loopback is a no-code upgrade via the existing picker.
- Trigger: menu-bar toggle (not a hotkey, not hold).
- Output: timestamped `.txt` + `.wav` in `~/Documents/blurt-meetings/`; auto-open the `.txt` on stop; keep the audio.
- Transcription: live, fixed 30 s chunks; VAD segmentation deferred.
- Icon: `⏺` while recording.
- Mutual exclusion: dictation hotkeys disabled during a meeting; clipboard-type stays enabled.
- Keep the feature inline in `blurt.py` for now.
