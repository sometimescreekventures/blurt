# blurt

Push-to-talk dictation for macOS, running fully locally on Apple Silicon.

Hold **Right Option (⌥)**, speak, release — text pastes at your cursor.

- **Fast.** ~60–360 ms end-to-end for 1–10 s utterances on an M-series Mac.
- **Local.** No cloud, no API keys, no network traffic after model download.
- **Small.** A single ~1,400-line Python file, one background daemon, one menu-bar icon.

Built on [parakeet-mlx](https://github.com/senstella/parakeet-mlx) (Apple-Silicon port of NVIDIA's Parakeet-TDT) with [MLX](https://github.com/ml-explore/mlx), [sounddevice](https://python-sounddevice.readthedocs.io/) for mic capture, [pynput](https://pynput.readthedocs.io/) for the global hotkey, and [rumps](https://github.com/jaredks/rumps) for the menu bar.

---

## Requirements

- macOS on Apple Silicon (M1 or later). Verified on macOS 26 / M5.
- A microphone.
- ~2 GB free disk for Xcode Command Line Tools, Python 3.12, model weights.

All other dependencies are installed by `./install.sh`.

## Install

### Quick deploy to a new Mac

```bash
git clone https://github.com/sometimescreekventures/blurt.git
cd blurt
./setup.sh
```

`setup.sh` installs the toolchain and the LaunchAgent, then blurt itself asks macOS for the permissions it needs — approve the **Accessibility** and **Input Monitoring** dialogs (blurt restarts itself once both are granted), and click Allow on the **Microphone** popup the first time you dictate. TCC grants are per-machine and can't be scripted, but two toggles is as small as macOS lets it get. If you dismissed the dialogs, run `./permissions.sh` for a guided walkthrough.

On first launch the daemon downloads ~600 MB of Parakeet weights from Hugging Face (one-time). After that, cold-start is ~10 s.

### What each step does

`./setup.sh` runs the steps below in order — it's all most installs need.

`./install.sh`:

1. Installs Xcode Command Line Tools (prompts for GUI install dialog if missing).
2. Installs [uv](https://github.com/astral-sh/uv) if missing.
3. Creates a Python 3.12 virtualenv at `.venv/`.
4. `uv sync`s the locked dependencies from `uv.lock`.

`./service.sh install && ./service.sh start` renders a LaunchAgent plist into `~/Library/LaunchAgents/local.blurt.plist` and bootstraps it so blurt runs at login. Install also builds a thin `Blurt.app` launcher (custom icon) into `~/Applications` so you can restart the LaunchAgent by clicking an icon.

`./permissions.sh` is the manual fallback for permissions: it resolves the real Python binary path, reveals it in Finder, and opens the Accessibility + Input Monitoring settings panes one at a time so you can drag-and-drop the binary into each. Normally blurt's own startup prompts make this unnecessary.

`./make-app.sh` rebuilds the `Blurt.app` launcher on its own — it runs automatically during `./service.sh install`, so you only need it directly if the repo moves. The bundle uses the custom icon in `Resources/Blurt.icns` and `exec`s `service.sh restart`.

### Running without a LaunchAgent

If you just want to try it first:

```bash
uv run python blurt.py
```

macOS will prompt for **Microphone** permission the first time you hold the hotkey — approve it. The terminal will show `This process is not trusted!` until you grant **Accessibility** and **Input Monitoring** — see [Permissions](#permissions).

### Managing the service

```bash
./service.sh restart     # after pulling code changes by hand
./service.sh logs        # tail stdout + stderr
./service.sh status      # launchd state
./service.sh uninstall   # remove the LaunchAgent
```

For routine updates you don't need the terminal at all — see [Updating](#updating).

### Updating

blurt updates from **release channels** — floating git tags moved by `release.sh`:

- 🗣️ **`shout`** — stable. The default channel; what every Mac should run.
- 🤫 **`mumble`** — beta. Releases land here first and soak before promotion.

Pick your channel from the menu-bar **Channel** submenu (persisted per machine). The **Version:** line shows the release the daemon is running and the channel (e.g. `Version: v0.2.0 (2026-06-12) · shout`), and **Check for Updates** compares your machine to the channel's tag. The daemon also checks once in the background at startup, so after a release the menu usually already reads **Update to v0.2.0 (N commits behind)** — click it to update. Switching channels fires a check immediately; if the other channel points at a *different* (even older) release, the menu offers **Switch to vX.Y.Z** — so moving from beta back to stable is a clean, deliberate downgrade.

Clicking the update label runs: `git fetch --tags` → `git reset --hard <channel tag>` → `uv sync` → exit with a non-zero status, which makes launchd (via `KeepAlive {SuccessfulExit: false}` in the plist) relaunch the daemon on the released code within a second or two. There is no periodic polling after startup; the manual click is the refresh path.

The update item refuses to run when it could lose work or state:

- **Local changes** in tracked files (`Update unavailable: local changes`) — commit, stash, or revert first. Untracked files don't block, since `git reset --hard` preserves them.
- **Not on `main`** (`Update unavailable: on <branch>`) — the checkout must be on `main` (its ref is what gets moved to the release).
- **No LaunchAgent** (`Update requires LaunchAgent install`) — when running `uv run python blurt.py` interactively there's nothing to relaunch the process, so the item is disabled.
- **Meeting recording in progress** — stop the recording first; the restart would discard it.
- **No release cut yet** (`channel tag 'shout' not found — cut a release first`) — run `./release.sh` once.

If `uv sync` fails, the label shows `Update failed — see logs` and the daemon keeps running the old code (but the checkout is already on the released commit — run `uv sync` from Terminal to recover). The remote, required branch, and channel names are the `UPDATE_REMOTE` / `UPDATE_BRANCH` / `UPDATE_CHANNELS` constants in `blurt.py`.

### Cutting a release

```bash
./release.sh            # cut a beta: tag vX.Y.Z, GitHub pre-release, move 🤫 mumble
./release.sh --minor    # same, but bump the minor (or --major)
./release.sh promote    # graduate: move 🗣️ shout to mumble's release, mark it latest
./release.sh status     # where the channels point + unreleased commits on main
```

Every release is an immutable `vX.Y.Z` tag plus a GitHub Release with notes auto-generated from the merged PRs (`gh release create --generate-notes`). Cutting requires a clean `main` matching `origin/main`. Promotion never rebuilds anything — it re-points `shout` at the exact commit that soaked on `mumble` and flips the GitHub Release from pre-release to latest. The flow is: merge PRs → `./release.sh` → run the beta on a Mumble machine for a while → `./release.sh promote`.

## Permissions

You need three TCC permissions granted to the Python interpreter that runs `blurt.py`. **You normally don't do anything manual here**: on startup blurt checks its grants and asks macOS for whatever is missing — the binary self-registers in the right panes, you flip the toggles in the OS dialogs, and blurt restarts itself. The menu-bar icon shows `⚠️` until the grants land. The rest of this section is fallback material: `./permissions.sh` walks the drag-and-drop path if the dialogs were dismissed, and the details below help if something is still stuck.

**Which Python?** `./service.sh install` prints the real path — typically something like:

```
/Users/<you>/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin/python3.12
```

This is the uv-managed Python that `.venv/bin/python` symlinks to. macOS TCC resolves through the symlink, so grant the target.

### Accessibility + Input Monitoring

macOS 14+ doesn't let you add raw command-line binaries via the `+` button in the permission picker — it greys them out. **Drag-and-drop the Python binary onto the list area instead**:

1. In Finder, press `⌘⇧G`, paste the path above, press Enter.
2. Open **System Settings → Privacy & Security → Accessibility**.
3. Drag the `python3.12` file from the Finder window onto the Accessibility list area.
4. Toggle the switch on.
5. Repeat in **Privacy & Security → Input Monitoring**.

### Microphone

Triggered by an OS dialog on first use. Click Allow.

### Alternative: grant Terminal.app (manual runs only)

If you're just running `uv run python blurt.py` interactively from Terminal, you can grant those two permissions to **Terminal.app** instead — child processes inherit them. This does **not** work when running via LaunchAgent, because `launchd` spawns the process, not Terminal.

## Usage

1. Menu bar shows `🎙` when idle.
2. Hold **Right Option (⌥)** — menu bar turns `🔴`, Tink sound plays, mic starts capturing.
3. Speak.
4. Release Right Option — Pop sound plays, menu bar shows `✨` briefly while transcribing.
5. Text pastes at your cursor.

Icon legend: `🎙` idle · `🔴` recording · `✨` transcribing · `⌨️` typing out the clipboard · `⏺` meeting recording · `⚠️` error (mic missing or stream failure — check logs).

The menu also has a **Microphone** submenu (pick a specific input device or System Default; **Refresh devices** rescans after plugging one in), the three hotkey submenus, the meeting-recorder toggle, and the version / update items described in [Updating](#updating).

### Three hotkeys

blurt has three independently configurable hold-to-fire hotkeys, each selectable from its own menu-bar submenu (no two may share a key):

| Hotkey | Default | What it does |
| ------ | ------- | ------------ |
| **Dictate → paste** | Right Option (`⌥`) | Transcribe speech, deliver via clipboard + ⌘V. The original, fastest path. |
| **Dictate → type** | Right Command (`⌘`) | Transcribe speech, deliver by synthesizing keystrokes. Use in VDI/remote clients where ⌘V is broken or mangled. |
| **Type clipboard** | Left Control (`⌃`) | Type whatever is on the clipboard out as keystrokes — no microphone. For pasting prepared text into a VDI that blocks paste but allows typing. Hold to start; **press again to abort** a long type. Menu bar shows `⌨️` while typing. |

The two dictate hotkeys are interchangeable per utterance — hold whichever one suits the destination field. The keystroke paths (type and clipboard) both honor `TYPE_KEY_DELAY` for clients that drop fast input.

### Meeting recording

For long-form audio (a call, a lecture), use the **Start Meeting Recording** menu-bar item. It records from the currently selected input device, transcribes in ~30 s chunks as it goes, and saves a timestamped transcript you can paste into an AI or keep as notes.

- Click **Start Meeting Recording** — the menu-bar icon shows `⏺` and the item becomes **Stop Meeting Recording (mm:ss)** with a live timer.
- Click **Stop** — the transcript `.txt` opens automatically in your default editor. Both the transcript and the raw `.wav` are saved to `~/Documents/blurt-meetings/` (filename `YYYY-MM-DD-HH-MM-meeting.{txt,wav}`).
- While a meeting is recording, the two **dictation** hotkeys are disabled (they share the mic); the **clipboard-type** hotkey still works.

**Capturing the other participants.** By default this records your selected microphone, so it captures *you* clearly but remote voices only as faint speaker bleed. To capture everyone, install a virtual loopback device (e.g. [BlackHole](https://github.com/ExistentialAudio/BlackHole)), set up an aggregate/multi-output device so meeting audio plays to your speakers *and* into the loopback, then select that loopback in blurt's **Microphone** menu — no other changes needed.

Knobs: `MEETING_DIR` and `MEETING_CHUNK_SEC` at the top of `blurt.py`. Chunks are fixed-length windows; very long words straddling a 30 s boundary may transcribe slightly worse (acceptable tradeoff for simplicity).

### Continuation spacing

If you dictate two utterances within 15 s of each other, the second gets a leading space — so `"Hello."` + `"How are you?"` becomes `"Hello. How are you?"` rather than `"Hello.How are you?"`. After 15 s of idle, the next paste has no leading space (assumes you've moved to a new context). Tune via `CONTINUATION_SEC` in `blurt.py`.

### Cleanup

Two layers of hallucination defense:

1. **VAD gate (primary).** Before transcribing, compute RMS per 100 ms frame. If fewer than `VAD_MIN_VOICED_FRAMES` exceed `VAD_RMS_THRESHOLD`, skip transcription entirely — Parakeet never runs, nothing gets pasted. Catches the common case of an accidental hold with no speech. The log shows `max_rms` and `voiced` counts so you can tune the threshold to your environment.
2. **Regex cleanup (fallback).** If speech was detected but the transcript still contains hallucinated backchannels (`"Mm-hmm."`, `"Uh-huh."`) or training-corpus artifacts (`"Thank you for watching."`, `"Thank you very much."`) at the leading or trailing edges, `cleanup()` strips them. Mid-utterance content is left alone. Edit `_BACKCHANNEL` / `_TRAIL_THANKS` in `blurt.py` to tune.

## Configuration

All knobs are constants at the top of `blurt.py`:

| Constant                  | Default                                | Notes                                                       |
| ------------------------- | -------------------------------------- | ----------------------------------------------------------- |
| `MODEL_ID`                | `mlx-community/parakeet-tdt-0.6b-v2`   | Any parakeet-mlx model. Larger = slower + more accurate.    |
| `SAMPLE_RATE`             | `16_000`                               | What the model expects; don't change.                       |
| `MIN_HOLD_SEC`            | `0.2`                                  | Taps shorter than this are ignored (accidental press).       |
| `MIN_AUDIO_SEC`           | `0.15`                                 | Audio shorter than this is skipped.                          |
| `CONTINUATION_SEC`        | `15.0`                                 | Window for adding leading space on back-to-back dictations.  |
| `VAD_RMS_THRESHOLD`       | `0.01`                                 | Per-100ms-frame RMS; below this = silence.                   |
| `VAD_MIN_VOICED_FRAMES`   | `2`                                    | Require N voiced frames before transcribing.                 |
| `CLIPBOARD_RESTORE_DELAY` | `0.8`                                  | How long to wait before restoring the clipboard after paste. |
| `TYPE_KEY_DELAY`          | `0.0`                                  | Per-character delay (s) for the keystroke paths. Bump to `0.005`–`0.01` if a slow VDI drops characters. |
| `SOUND_START` / `SOUND_STOP` | `Tink.aiff` / `Pop.aiff`            | Built-in system sounds. See `/System/Library/Sounds/`.       |
| `SOUND_VOLUME`            | `0.3`                                  | `afplay -v` argument, 0.0–1.0.                               |
| `MEETING_DIR`             | `~/Documents/blurt-meetings`           | Where meeting transcripts + WAVs are saved.                  |
| `MEETING_CHUNK_SEC`       | `30.0`                                 | Meeting transcription chunk length.                          |
| `UPDATE_REMOTE` / `UPDATE_BRANCH` | `origin` / `main`              | Remote + required local branch for updates.                  |
| `UPDATE_CHANNELS`         | `("shout", "mumble")`                  | The floating release-channel tags (stable, beta).            |

The microphone, all three hotkeys, and the release channel are chosen from menu-bar submenus and persisted to `~/Library/Application Support/blurt/config.json` (keys `microphone`, `hotkey`, `type_hotkey`, `clipboard_hotkey`, `update_channel`). Hotkey defaults are Right Option / Right Command / Left Control; the channel defaults to `shout`. The picker offers single modifiers and F13–F19; no two hotkeys may share a key.

## Architecture

One process, a handful of threads:

- **Main thread** — `rumps.App` run loop. Owns the menu bar icon and menu labels; a 0.1 s timer drains cross-thread UI updates from a queue.
- **pynput listener thread** — CGEventTap; fires `on_press` / `on_release` for the three hotkeys.
- **Audio thread** — `sounddevice.InputStream` callbacks push 50 ms PCM blocks into the active recording's frame buffer (or, for meetings, onto a queue).
- **Transcription worker (one per utterance)** — spawned on release; runs Parakeet, runs cleanup, pastes or types.
- **Meeting worker (while a meeting is recording)** — drains the audio queue, streams to the WAV, transcribes ~30 s chunks, appends to the transcript.
- **Update worker (per check/apply)** — runs the git/uv steps off the UI thread; results marshal back via the UI queue.

A single `_mlx_lock` serializes all MLX compute — Metal command buffers aren't safe to encode from multiple threads concurrently (we hit `A command encoder is already encoding` asserts without it).

### Why not use parakeet-mlx's `transcribe_stream`?

Tried; in our setting (short 1–10 s push-to-talk utterances, 50 ms chunks) it was ~2× slower than batch and sometimes returned empty results because the default right-context drop window (256 encoder frames ≈ 20 s) prevented token finalization on short audio. Batch-on-release with the direct `get_logmel` → `model.generate` path is both simpler and faster for this use case.

### Why not `.app` bundle?

Could be. Keeping it a single `.py` file under a LaunchAgent is easier to hack on and re-grant permissions for. An `.app` bundle would give a nicer TCC story (add to Accessibility via `+` picker instead of drag-and-drop) but doubles the project's surface area.

## Troubleshooting

**Menu bar icon is missing.** Check `./service.sh status` — if `not loaded`, run `./service.sh start`. If loaded but no icon, check `./service.sh logs` for Python errors.

**`This process is not trusted!` in logs.** Accessibility or Input Monitoring isn't granted to the running Python binary. See [Permissions](#permissions).

**Hotkey doesn't fire.** Permissions (see above). Also: if your keyboard has a non-standard layout (e.g. ANSI → JIS remap), pynput may report a different key. Run with debug logging — add `print(key)` to `on_press` — to verify.

**MLX assertion `A command encoder is already encoding`.** Should be fixed by `_mlx_lock`. If it recurs, you've likely hit a new concurrency path — file an issue with `./service.sh logs` output.

**Transcription is empty for all utterances.** Usually means audio isn't reaching the model. Check System Settings → Privacy & Security → Microphone, ensure the Python binary is allowed. Also check the system default input device has non-zero level.

**Paste lands in the wrong place.** Keep focus in the target text field until you hear the Pop sound. Paste happens ~100–400 ms after release.

**blurt stopped working after an update (hotkeys dead, `⚠️` in menu bar).** A uv Python upgrade can change the interpreter's path, which makes macOS forget the Accessibility / Input Monitoring grants. blurt detects this at startup and re-fires the permission dialogs — flip the toggles and it restarts itself. (`./service.sh logs` shows `missing permissions: …` when this is the cause.)

**Menu-bar update says `Update failed — see logs`.** Run `./service.sh logs`. If the error is `uv sync` related, the checkout is already on the new commit — run `uv sync` from the repo and then `./service.sh restart`. A `fetch` failure usually means no network; the daemon stays on the old code and you can just retry.

**Transcription quality is poor for specific terms.** Parakeet doesn't support prompt biasing out of the box. Workaround: keep a post-processing dictionary of common mis-transcriptions → corrections, applied in `cleanup()`.

## License

MIT — see [LICENSE](LICENSE).

## Credits

- [parakeet-mlx](https://github.com/senstella/parakeet-mlx) — @senstella's Apple-Silicon port of Parakeet.
- [Parakeet-TDT](https://huggingface.co/nvidia/parakeet-tdt-0.6b) — NVIDIA's speech model family.
- [rumps](https://github.com/jaredks/rumps) — macOS menu bar apps in pure Python.
