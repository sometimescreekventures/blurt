#!/usr/bin/env python3
"""blurt — push-to-talk dictation for macOS.

Hold Right Option (⌥) → speak → release.
Audio is transcribed locally via Parakeet-MLX and pasted at the cursor.
"""
from __future__ import annotations

import functools
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import rumps
import sounddevice as sd
import soundfile as sf
from pynput import keyboard
from pynput.keyboard import Controller as KBController, Key

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

SAMPLE_RATE = 16_000
CHANNELS = 1
BLOCK_SEC = 0.05
MIN_HOLD_SEC = 0.2
MIN_AUDIO_SEC = 0.15
CLIPBOARD_RESTORE_DELAY = 0.8
CONTINUATION_SEC = 15.0  # if within this since last paste, prepend a space
PERMISSION_POLL_SEC = 5.0  # how often to re-check TCC grants while missing

# Per-character delay for type-mode delivery. Default 0 (let pynput burst as
# fast as macOS allows). Bump to 0.005–0.01 if a slow VDI drops characters.
TYPE_KEY_DELAY = 0.0

# Voice-activity gate: skip transcription when audio is essentially silence.
# Tune by watching max_rms values in the logs for your environment.
VAD_FRAME_SEC = 0.1
VAD_RMS_THRESHOLD = 0.01
VAD_MIN_VOICED_FRAMES = 2

MODEL_ID = "mlx-community/parakeet-tdt-0.6b-v2"

# Meeting recorder: long-form capture from the selected input device, chunked
# transcription, written to a timestamped document.
MEETING_DIR = Path.home() / "Documents" / "blurt-meetings"
MEETING_CHUNK_SEC = 30.0

SOUND_START = "/System/Library/Sounds/Tink.aiff"
SOUND_STOP = "/System/Library/Sounds/Pop.aiff"
SOUND_VOLUME = "0.3"

CONFIG_PATH = Path.home() / "Library" / "Application Support" / "blurt" / "config.json"
REPO_ROOT = Path(__file__).resolve().parent

# Self-update tracks this remote / branch via the menu-bar "Check for Updates" item.
UPDATE_REMOTE = "origin"
UPDATE_BRANCH = "main"

# Ordered list of (menu label, pynput Key attribute name).
# Single source of truth for the Hotkey submenu and for config validation.
HOTKEY_CHOICES: list[tuple[str, str]] = [
    ("Right Option", "alt_r"),
    ("Left Option", "alt_l"),
    ("Right Command", "cmd_r"),
    ("Left Command", "cmd_l"),
    ("Left Control", "ctrl_l"),
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
DEFAULT_CONFIG: dict = {
    "microphone": None,
    "hotkey": "alt_r",
    "type_hotkey": "cmd_r",
    "clipboard_hotkey": "ctrl_l",
}


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
    for field in ("type_hotkey", "clipboard_hotkey"):
        if field in raw:
            if isinstance(raw[field], str) and raw[field] in _HOTKEY_ATTRS:
                cfg[field] = raw[field]
            else:
                print(
                    f"[blurt] unknown {field} {raw[field]!r} in config; "
                    f"using {DEFAULT_CONFIG[field]!r}",
                    file=sys.stderr,
                )
    # Resolve collisions in priority order: hotkey wins, then type_hotkey,
    # then clipboard_hotkey. HOTKEY_CHOICES has far more entries than the three
    # keys, so a non-colliding fallback always exists.
    used: set[str] = {cfg["hotkey"]}
    for field in ("type_hotkey", "clipboard_hotkey"):
        if cfg[field] in used:
            fallback = next(
                (attr for _, attr in HOTKEY_CHOICES if attr not in used),
                DEFAULT_CONFIG[field],
            )
            print(
                f"[blurt] {field} collides with an already-bound key {cfg[field]!r}; "
                f"falling back to {fallback!r}",
                file=sys.stderr,
            )
            cfg[field] = fallback
        used.add(cfg[field])
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


@dataclass
class State:
    title: str = "🎙"
    last_text: str = ""


STATE = State()
_model = None
_mx = None
_get_logmel = None
_model_lock = threading.Lock()
# Serialize MLX compute; Metal command buffers aren't safe to encode from
# multiple threads concurrently (seen as AGXG17XFamilyCommandBuffer asserts).
_mlx_lock = threading.Lock()
_kb = KBController()
_last_paste_ts = 0.0


# --- model loading ----------------------------------------------------------

def load_model():
    """Lazy-load Parakeet + its mel front-end; warm with 0.5s silence."""
    global _model, _mx, _get_logmel
    with _model_lock:
        if _model is None:
            print(f"[blurt] loading {MODEL_ID} ...", flush=True)
            import mlx.core as mx
            from parakeet_mlx import from_pretrained
            from parakeet_mlx.audio import get_logmel
            _mx = mx
            _get_logmel = get_logmel
            m = from_pretrained(MODEL_ID)
            _transcribe_array(m, np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.float32))
            _model = m
            print("[blurt] ready.", flush=True)
            STATE.title = "🎙"
    return _model


def _transcribe_array(model, audio: np.ndarray):
    # Must be float32: get_logmel's mx.view(complex, dtype) + [::2]/[1::2]
    # split only works when real dtype matches FFT's internal real precision.
    with _mlx_lock:
        x = _mx.array(audio, dtype=_mx.float32)
        mel = _get_logmel(x, model.preprocessor_config)
        return model.generate(mel)[0]


# --- post-processing --------------------------------------------------------

# Parakeet hallucinates short backchannels during silence and often appends
# "Thank you." / "Thanks." (training-data artifact from narration corpora).
_BACKCHANNEL = r"(?:mm+-?h+m+|uh-?huh|ah-?ha|aha|uh|um|ah)"
_TRAIL_THANKS = r"thank(?:s|\s+you)(?:\s+(?:very|so)\s+much)?(?:\s+for\s+(?:watching|listening))?"
_CLEAN_LEAD = re.compile(rf"^(?:\s*{_BACKCHANNEL}[\.,!?]?\s*)+", re.IGNORECASE)
_CLEAN_TRAIL = re.compile(
    rf"(?:\s*(?:{_BACKCHANNEL}|{_TRAIL_THANKS})[\.,!?]?\s*)+$",
    re.IGNORECASE,
)


def cleanup(text: str) -> str:
    if not text:
        return ""
    text = _CLEAN_LEAD.sub("", text)
    text = _CLEAN_TRAIL.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def has_speech(audio: np.ndarray) -> tuple[bool, float, int]:
    """Return (speech_detected, max_frame_rms, voiced_frame_count)."""
    frame_samples = int(SAMPLE_RATE * VAD_FRAME_SEC)
    if audio.size < frame_samples:
        return False, 0.0, 0
    n_frames = audio.size // frame_samples
    frames = audio[: n_frames * frame_samples].reshape(n_frames, frame_samples)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    voiced = int((rms >= VAD_RMS_THRESHOLD).sum())
    return voiced >= VAD_MIN_VOICED_FRAMES, float(rms.max()), voiced


# --- audio cues (non-blocking) ---------------------------------------------

def _afplay(path: str) -> None:
    try:
        subprocess.Popen(
            ["afplay", "-v", SOUND_VOLUME, path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[blurt] afplay: {e}", file=sys.stderr)


def play_start() -> None: _afplay(SOUND_START)
def play_stop() -> None:  _afplay(SOUND_STOP)


# --- delivery ---------------------------------------------------------------

def pbpaste() -> str:
    try:
        return subprocess.check_output(["pbpaste"], timeout=1).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def pbcopy(text: str) -> None:
    p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
    p.communicate(text.encode("utf-8"), timeout=2)


def paste_text(text: str) -> None:
    global _last_paste_ts
    if not text:
        return
    # If we pasted recently, assume the user is continuing in the same field
    # and insert a separator so sentences don't run together.
    now = time.monotonic()
    if now - _last_paste_ts < CONTINUATION_SEC and text[:1].isalnum():
        text = " " + text

    prior = pbpaste()
    pbcopy(text)
    time.sleep(0.05)
    _kb.press(Key.cmd); _kb.press("v"); _kb.release("v"); _kb.release(Key.cmd)
    _last_paste_ts = time.monotonic()

    def restore():
        time.sleep(CLIPBOARD_RESTORE_DELAY)
        pbcopy(prior)
    threading.Thread(target=restore, daemon=True).start()


def _emit_keystrokes(text: str, abort: "threading.Event | None" = None) -> int:
    """Type text via synthesized keystrokes. Returns the number of chars emitted.

    Iterates per character (even when TYPE_KEY_DELAY == 0) so the abort flag,
    when supplied, is honored — it is checked *before* each keystroke, keeping
    the modifier-combo window during an abort to at most one in-flight char.
    """
    n = 0
    for ch in text:
        if abort is not None and abort.is_set():
            break
        _kb.type(ch)
        n += 1
        if TYPE_KEY_DELAY > 0.0:
            time.sleep(TYPE_KEY_DELAY)
    return n


def type_text(text: str) -> None:
    """Deliver text via synthesized keystrokes (for VDIs that mangle ⌘V)."""
    global _last_paste_ts
    if not text:
        return
    now = time.monotonic()
    if now - _last_paste_ts < CONTINUATION_SEC and text[:1].isalnum():
        text = " " + text

    _emit_keystrokes(text)
    _last_paste_ts = time.monotonic()


def type_clipboard(abort: "threading.Event") -> None:
    """Type the current clipboard contents out verbatim as keystrokes.

    No continuation-space munging and no _last_paste_ts update — this is a
    deliberate bulk entry, not a dictation continuation. Honors `abort`.
    """
    text = pbpaste()
    if not text.strip():
        print("[blurt] clipboard empty; nothing to type", flush=True)
        return
    n = _emit_keystrokes(text, abort=abort)
    if abort.is_set():
        print(f"[blurt] clipboard type aborted after {n} chars", flush=True)
    else:
        print(f"[blurt] clipboard typed {n} chars", flush=True)


# --- audio capture ----------------------------------------------------------

class Recorder:
    def __init__(self) -> None:
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._start_ts: float = 0.0

    def _cb(self, indata, frames, time_info, status):
        if status:
            print(f"[blurt] audio status: {status}", file=sys.stderr)
        self._frames.append(indata.copy().reshape(-1))

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

    def stop(self) -> tuple[np.ndarray, float]:
        duration = time.monotonic() - self._start_ts
        s = self._stream
        self._stream = None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception as e:
                print(f"[blurt] stream close: {e}", file=sys.stderr)
        audio = np.concatenate(self._frames) if self._frames else np.zeros(0, dtype=np.float32)
        return audio, duration


# --- hotkey -----------------------------------------------------------------

class Hotkey:
    def __init__(
        self,
        trigger_key: Key,
        type_trigger_key: Key,
        clipboard_trigger_key: Key,
        device: str | None,
        meeting_active: "threading.Event | None" = None,
    ) -> None:
        self._recording = False
        self._rec = Recorder()
        self._lock = threading.Lock()
        self.trigger_key = trigger_key
        self.type_trigger_key = type_trigger_key
        self.clipboard_trigger_key = clipboard_trigger_key
        # Which delivery path the current recording is bound to; set in on_press,
        # read in on_release/_work, cleared after dispatch.
        self._active_mode: str | None = None
        # Clipboard-typing state (a separate, audio-free path).
        self._clip_pending = False
        self._clip_press_ts = 0.0
        self._clip_typing = False
        self._clip_abort: "threading.Event | None" = None
        # Set while a meeting recording owns the mic; dictation paths defer to it.
        self._meeting_active = meeting_active or threading.Event()
        self.device = device
        self.disabled = False

    def on_press(self, key):
        # Clipboard hotkey: re-press while typing aborts; otherwise it's a
        # hold-to-fire that types the clipboard on release. No mic, no model,
        # and it works even when dictation is disabled (missing mic).
        if key == self.clipboard_trigger_key:
            if self._clip_typing:
                if self._clip_abort is not None:
                    self._clip_abort.set()
                return
            with self._lock:
                if self._recording:
                    return  # dictation in progress; ignore
                self._clip_pending = True
                self._clip_press_ts = time.monotonic()
            return

        if key == self.trigger_key:
            mode = "paste"
        elif key == self.type_trigger_key:
            mode = "type"
        else:
            return
        # A meeting recording owns the mic; dictation defers to it. (The
        # clipboard path above is unaffected — it needs no microphone.)
        if self._meeting_active.is_set():
            return
        # Reading trigger_key/disabled/device here without a lock is intentional:
        # the UI thread may mutate them between reads, but a stale stream-open
        # will fail and the except path below flips self.disabled = True.
        if self.disabled:
            return
        with self._lock:
            if self._recording or self._clip_typing:
                return
            try:
                self._rec.start(self.device)
                self._recording = True
                self._active_mode = mode
                STATE.title = "🔴"
                play_start()
            except Exception as e:
                print(f"[blurt] record start: {e}", file=sys.stderr)
                STATE.title = "⚠️"
                self.disabled = True

    def on_release(self, key):
        # Clipboard hotkey release: fire if it was a genuine hold.
        if key == self.clipboard_trigger_key:
            with self._lock:
                if not self._clip_pending:
                    return  # e.g. this release followed an abort re-press
                self._clip_pending = False
                held = time.monotonic() - self._clip_press_ts
                if held < MIN_HOLD_SEC:
                    return
                if self._recording or self._clip_typing:
                    return
                self._clip_typing = True
                self._clip_abort = threading.Event()
            STATE.title = "⌨️"
            threading.Thread(target=self._clip_work, daemon=True).start()
            return

        # Only react to release of whichever key started the active recording.
        active_key = (
            self.trigger_key if self._active_mode == "paste"
            else self.type_trigger_key if self._active_mode == "type"
            else None
        )
        if key != active_key:
            return
        mode = self._active_mode
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            self._active_mode = None
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
        threading.Thread(target=self._work, args=(audio, duration, mode), daemon=True).start()

    def _work(self, audio: np.ndarray, duration: float, mode: str | None) -> None:
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
                if mode == "type":
                    type_text(text)
                else:
                    paste_text(text)
        except Exception as e:
            print(f"[blurt] transcribe/paste: {e}", file=sys.stderr)
            STATE.title = "⚠️"
            return
        STATE.title = "🎙"

    def _clip_work(self) -> None:
        try:
            type_clipboard(self._clip_abort)
        except Exception as e:
            print(f"[blurt] clipboard type: {e}", file=sys.stderr)
        finally:
            with self._lock:
                self._clip_typing = False
                self._clip_abort = None
            # Restore the resting icon, preserving the mic-missing warning.
            STATE.title = "⚠️" if self.disabled else "🎙"


# --- meeting recorder -------------------------------------------------------

def _fmt_hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def meeting_paths(start_dt: datetime, directory: Path = MEETING_DIR) -> tuple[Path, Path]:
    """Return (txt_path, wav_path) sharing a timestamped stem.

    Disambiguates with -2, -3, … if either file already exists, so two
    recordings in the same minute never clobber each other.
    """
    base = f"{start_dt.strftime('%Y-%m-%d-%H-%M')}-meeting"
    candidate, n = base, 2
    while (directory / f"{candidate}.txt").exists() or (directory / f"{candidate}.wav").exists():
        candidate, n = f"{base}-{n}", n + 1
    return directory / f"{candidate}.txt", directory / f"{candidate}.wav"


class MeetingRecorder:
    """Long-form capture from the selected input device → chunked transcript.

    Capture runs on the sounddevice callback thread (push blocks to a queue);
    a worker thread streams those blocks to a WAV and transcribes ~30s chunks,
    appending text to the .txt as it goes. Both files grow incrementally so a
    crash leaves a usable WAV + partial transcript behind.
    """

    def __init__(self, meeting_active: threading.Event) -> None:
        self._meeting_active = meeting_active
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._worker: threading.Thread | None = None
        self._wav: sf.SoundFile | None = None
        self._txt_path: Path | None = None
        self._chunk: list[np.ndarray] = []
        self._chunk_samples = 0
        self._start_dt: datetime | None = None
        self._start_mono = 0.0
        self._done = False
        self.last_output_path: Path | None = None

    def is_active(self) -> bool:
        return self._meeting_active.is_set()

    def elapsed(self) -> float:
        return time.monotonic() - self._start_mono if self.is_active() else 0.0

    def start(self, device: str | None) -> bool:
        """Open outputs + stream and begin recording. False on failure (no state left)."""
        if self.is_active():
            return False
        try:
            MEETING_DIR.mkdir(parents=True, exist_ok=True)
            self._start_dt = datetime.now()
            txt_path, wav_path = meeting_paths(self._start_dt)
            self._txt_path = txt_path
            txt_path.write_text(f"Meeting — {self._start_dt.strftime('%Y-%m-%d %H:%M')}\n\n")
            self._wav = sf.SoundFile(
                str(wav_path), mode="w", samplerate=SAMPLE_RATE,
                channels=CHANNELS, subtype="PCM_16",
            )
            self._stop.clear()
            self._done = False
            self._chunk, self._chunk_samples = [], 0
            self._queue = queue.Queue()
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32",
                callback=self._cb, blocksize=int(SAMPLE_RATE * BLOCK_SEC),
                device=device, finished_callback=self._on_stream_finished,
            )
            self._stream.start()
        except Exception as e:
            print(f"[blurt] meeting start failed: {e}", file=sys.stderr)
            self._cleanup_outputs()
            return False

        self._start_mono = time.monotonic()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        self._meeting_active.set()
        STATE.title = "⏺"
        print(f"[blurt] meeting recording → {self._txt_path}", flush=True)
        return True

    def _cb(self, indata, frames, time_info, status):
        if status:
            print(f"[blurt] meeting audio status: {status}", file=sys.stderr)
        self._queue.put(indata.copy().reshape(-1))

    def _on_stream_finished(self) -> None:
        # Fires on normal stop (self._stop set → ignore) or on device removal,
        # where we must finalize ourselves so the menu/timer reconcile.
        if self._stop.is_set():
            return
        print("[blurt] meeting input ended unexpectedly; finalizing", file=sys.stderr)
        threading.Thread(target=self.stop, daemon=True).start()

    def _run(self) -> None:
        chunk_target = int(MEETING_CHUNK_SEC * SAMPLE_RATE)
        while True:
            try:
                block = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._stop.is_set():
                    break
                continue
            try:
                if self._wav is not None:
                    self._wav.write(block)
            except Exception as e:
                print(f"[blurt] meeting wav write: {e}", file=sys.stderr)
            self._chunk.append(block)
            self._chunk_samples += block.shape[0]
            if self._chunk_samples >= chunk_target:
                self._flush_chunk()
        self._flush_chunk()

    def _flush_chunk(self) -> None:
        if self._chunk_samples == 0:
            return
        audio = np.concatenate(self._chunk)
        self._chunk, self._chunk_samples = [], 0
        speech, _max_rms, _voiced = has_speech(audio)
        if not speech:
            return
        try:
            model = load_model()
            result = _transcribe_array(model, audio)
            text = cleanup(getattr(result, "text", str(result)).strip())
            if text and self._txt_path is not None:
                with open(self._txt_path, "a") as f:
                    f.write(text + " ")
        except Exception as e:
            print(f"[blurt] meeting chunk transcribe: {e}", file=sys.stderr)

    def stop(self) -> Path | None:
        """Finalize: stop capture, drain, write footer, return the .txt path.

        Idempotent and safe to call from the menu or the device-loss path.
        """
        with self._lock:
            if self._done:
                return self.last_output_path
            self._done = True
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                print(f"[blurt] meeting stream close: {e}", file=sys.stderr)
        if self._worker is not None:
            self._worker.join(timeout=120.0)
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception as e:
                print(f"[blurt] meeting wav close: {e}", file=sys.stderr)
        if self._txt_path is not None:
            try:
                end = datetime.now()
                with open(self._txt_path, "a") as f:
                    f.write(
                        f"\n\n— ended {end.strftime('%H:%M')}, "
                        f"duration {_fmt_hms(self.elapsed_at_stop())} —\n"
                    )
            except Exception as e:
                print(f"[blurt] meeting footer write: {e}", file=sys.stderr)
        self.last_output_path = self._txt_path
        self._meeting_active.clear()
        print(f"[blurt] meeting saved → {self._txt_path}", flush=True)
        return self._txt_path

    def elapsed_at_stop(self) -> float:
        return time.monotonic() - self._start_mono if self._start_mono else 0.0

    def _cleanup_outputs(self) -> None:
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None


# --- self-update ------------------------------------------------------------

@dataclass(frozen=True)
class UpdateCheck:
    status: str  # "up_to_date" | "update_available" | "dirty" | "wrong_branch" | "check_failed"
    local_sha: str = ""
    remote_sha: str = ""
    commits_behind: int = 0
    error: str = ""


@dataclass(frozen=True)
class ApplyResult:
    status: str  # "restarting" | "dirty" | "wrong_branch" | "fetch_failed" | "uv_failed"
    error: str = ""


def _git(args: list[str], *, repo: Path | None = None, timeout: float = 10.0) -> str:
    """Run a git command in the repo. Returns stdout (stripped). Raises on non-zero."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo or REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


@functools.lru_cache(maxsize=1)
def current_version() -> tuple[str, str]:
    """Return (short_sha, iso_date) for the currently-running checkout.

    Cached for the process lifetime — the daemon's SHA can't change without a restart.
    """
    try:
        sha = _git(["rev-parse", "--short", "HEAD"], timeout=2.0)
        date = _git(["show", "-s", "--format=%cs", "HEAD"], timeout=2.0)
        return (sha, date)
    except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"[blurt] current_version failed: {e}", file=sys.stderr)
        return ("unknown", "")


def check_for_updates(repo: Path | None = None) -> UpdateCheck:
    """Fetch origin and compare local HEAD to origin/main."""
    repo = repo or REPO_ROOT
    try:
        branch = _git(["symbolic-ref", "--short", "HEAD"], repo=repo, timeout=2.0)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return UpdateCheck(status="check_failed", error=str(e))
    if branch != UPDATE_BRANCH:
        return UpdateCheck(status="wrong_branch", error=f"on {branch}, expected {UPDATE_BRANCH}")
    try:
        dirty = _git(["status", "--porcelain", "--untracked-files=no"], repo=repo, timeout=5.0)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return UpdateCheck(status="check_failed", error=str(e))
    try:
        _git(["fetch", UPDATE_REMOTE, UPDATE_BRANCH], repo=repo, timeout=15.0)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return UpdateCheck(status="check_failed", error=str(e))
    try:
        local_sha = _git(["rev-parse", "--short", "HEAD"], repo=repo, timeout=2.0)
        remote_sha = _git(
            ["rev-parse", "--short", f"{UPDATE_REMOTE}/{UPDATE_BRANCH}"], repo=repo, timeout=2.0
        )
        behind = int(
            _git(
                ["rev-list", "--count", f"HEAD..{UPDATE_REMOTE}/{UPDATE_BRANCH}"],
                repo=repo,
                timeout=5.0,
            )
        )
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as e:
        return UpdateCheck(status="check_failed", error=str(e))

    if dirty:
        return UpdateCheck(
            status="dirty", local_sha=local_sha, remote_sha=remote_sha, commits_behind=behind
        )
    if behind == 0:
        return UpdateCheck(
            status="up_to_date", local_sha=local_sha, remote_sha=remote_sha, commits_behind=0
        )
    return UpdateCheck(
        status="update_available",
        local_sha=local_sha,
        remote_sha=remote_sha,
        commits_behind=behind,
    )


def _uv_binary() -> str:
    """Resolve the uv executable to an absolute path.

    Under the LaunchAgent the daemon inherits launchd's minimal PATH
    (/usr/bin:/bin:/usr/sbin:/sbin), which doesn't include ~/.local/bin —
    where the astral.sh installer used by install.sh puts uv. So a bare
    "uv" exec fails when started by launchd; fall back there explicitly.
    """
    found = shutil.which("uv")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "uv"
    if fallback.exists():
        return str(fallback)
    raise FileNotFoundError("uv not found on PATH or at ~/.local/bin/uv")


def apply_update() -> ApplyResult:
    """Fetch, reset, uv sync, restart. Refuses dirty checkouts and non-main branches."""
    try:
        branch = _git(["symbolic-ref", "--short", "HEAD"], timeout=2.0)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return ApplyResult(status="fetch_failed", error=str(e))
    if branch != UPDATE_BRANCH:
        return ApplyResult(status="wrong_branch", error=f"on {branch}")

    try:
        dirty = _git(["status", "--porcelain", "--untracked-files=no"], timeout=5.0)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return ApplyResult(status="fetch_failed", error=str(e))
    if dirty:
        return ApplyResult(status="dirty")

    try:
        _git(["fetch", UPDATE_REMOTE, UPDATE_BRANCH], timeout=30.0)
        _git(["reset", "--hard", f"{UPDATE_REMOTE}/{UPDATE_BRANCH}"], timeout=10.0)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return ApplyResult(status="fetch_failed", error=str(e))

    try:
        proc = subprocess.run(
            [_uv_binary(), "sync"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=180.0,
        )
        if proc.returncode != 0:
            return ApplyResult(status="uv_failed", error=proc.stderr.strip()[:500])
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return ApplyResult(status="uv_failed", error=str(e))

    restart_daemon()
    return ApplyResult(status="restarting")


def restart_daemon(_exit: "Callable[[int], None]" = os._exit) -> None:
    """Exit non-zero so the LaunchAgent relaunches us with the synced code.

    The LaunchAgent plist sets KeepAlive={SuccessfulExit: false}: launchd
    relaunches the job whenever it exits UN-successfully. So the canonical
    self-restart is simply to die with a non-zero status and let launchd
    revive us.

    The earlier approach — spawn a detached `/tmp/blurt-restart.sh` helper,
    then exit cleanly — was unreliable: a clean exit (0) tells KeepAlive NOT
    to relaunch, and launchd reaps the job's descendants (even setsid'd ones)
    when the daemon exits, so the helper was usually killed before it could run
    `service.sh start`. The update content (git reset + uv sync) had already
    landed, which is why a manual relaunch always "worked."

    `os._exit` is used (not sys.exit) because this runs on a background thread:
    it terminates the whole process immediately with the given status.
    """
    print("[blurt] update applied; exiting for launchd to relaunch", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    _exit(1)


def has_launchagent() -> bool:
    """Check whether the LaunchAgent is registered with launchd for this user."""
    try:
        subprocess.check_output(
            ["launchctl", "print", f"gui/{os.getuid()}/local.blurt"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return True
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return False


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


# --- menu bar ---------------------------------------------------------------

class MenuApp(rumps.App):
    _SYSTEM_DEFAULT_LABEL = "System Default"

    def __init__(self, hotkey: "Hotkey", recorder: "MeetingRecorder") -> None:
        super().__init__("blurt", title="🎙", quit_button=None)
        self.hotkey = hotkey
        self.recorder = recorder
        self._meeting_was_active = False
        self._mic_menu = rumps.MenuItem("Microphone")
        self._hotkey_menu = rumps.MenuItem("Hotkey")
        self._type_hotkey_menu = rumps.MenuItem("Type-mode Hotkey")
        self._clipboard_hotkey_menu = rumps.MenuItem("Clipboard Hotkey")
        self._meeting_item = rumps.MenuItem(
            "Start Meeting Recording", callback=self._on_meeting_toggle
        )
        self._build_mic_menu()
        self._build_hotkey_menu()
        self._build_type_hotkey_menu()
        self._build_clipboard_hotkey_menu()
        self._refresh_hotkey_greyouts()

        sha, date = current_version()
        version_label = f"Version: {sha}" + (f" ({date})" if date else "")
        self._version_item = rumps.MenuItem(version_label)  # no callback → disabled
        self._has_launchagent = has_launchagent()
        if self._has_launchagent:
            self._update_item = rumps.MenuItem(
                "Check for Updates", callback=self._on_check_updates
            )
        else:
            self._update_item = rumps.MenuItem(
                "Update requires LaunchAgent install"
            )  # disabled

        # Cross-thread updates to the update label: the background thread enqueues
        # (title, callback) tuples, the run-loop timer drains them.
        self._update_queue: queue.Queue[tuple[str, Optional[Callable]]] = queue.Queue()
        self._update_in_flight = threading.Lock()

        self.menu = [
            self._mic_menu,
            self._hotkey_menu,
            self._type_hotkey_menu,
            self._clipboard_hotkey_menu,
            None,  # separator
            self._meeting_item,
            None,
            self._version_item,
            self._update_item,
            None,
            rumps.MenuItem("Quit blurt", callback=self._quit),
        ]

    @rumps.timer(0.1)
    def _tick(self, _):
        self._reconcile_meeting()
        if self.title != STATE.title:
            self.title = STATE.title
        try:
            while True:
                title, cb = self._update_queue.get_nowait()
                self._update_item.title = title
                self._update_item.set_callback(cb)
        except queue.Empty:
            pass

    def _reconcile_meeting(self) -> None:
        """Keep the meeting menu label/icon in sync, and auto-open on stop.

        Single source of truth for ending a meeting: whether the user clicked
        Stop or the input device vanished, the falling edge here resets the UI
        and opens the transcript exactly once.
        """
        active = self.recorder.is_active()
        if active:
            mm, ss = divmod(int(self.recorder.elapsed()), 60)
            self._meeting_item.title = f"Stop Meeting Recording ({mm:02d}:{ss:02d})"
            STATE.title = "⏺"
        elif self._meeting_was_active:  # falling edge — meeting just ended
            self._meeting_item.title = "Start Meeting Recording"
            if STATE.title == "⏺":
                STATE.title = "⚠️" if self.hotkey.disabled else "🎙"
            path = self.recorder.last_output_path
            if path is not None:
                subprocess.Popen(
                    ["open", str(path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        self._meeting_was_active = active

    def _on_meeting_toggle(self, _sender) -> None:
        if self.recorder.is_active():
            threading.Thread(target=self.recorder.stop, daemon=True).start()
            return
        if self.hotkey._recording:
            print("[blurt] can't start meeting: dictation in progress", file=sys.stderr)
            return
        if self.hotkey.disabled:
            print("[blurt] can't start meeting: microphone unavailable", file=sys.stderr)
            return
        self.recorder.start(self.hotkey.device)

    def _quit(self, _):
        if self.recorder.is_active():
            self.recorder.stop()
        rumps.quit_application()

    # --- microphone submenu --------------------------------------------------

    def _build_mic_menu(self) -> None:
        """Populate self._mic_menu. Caller must call self._mic_menu.clear() first if rebuilding."""
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
        save_config(self._current_config(microphone=new_device))
        self._refresh_mic_checkmarks()

    def _on_refresh_devices(self, _sender) -> None:
        self._mic_menu.clear()
        self._build_mic_menu()

    def _refresh_mic_checkmarks(self) -> None:
        for item in self._mic_menu.values():
            if not isinstance(item, rumps.MenuItem):
                continue
            title = str(item.title)
            if title == self._SYSTEM_DEFAULT_LABEL:
                item.state = 1 if self.hotkey.device is None else 0
            else:
                item.state = 1 if self.hotkey.device == title else 0

    # --- hotkey submenu ------------------------------------------------------

    def _build_hotkey_menu(self) -> None:
        current = self._current_hotkey_attr()
        for label, attr in HOTKEY_CHOICES:
            item = rumps.MenuItem(label, callback=self._on_hotkey_pick)
            item.state = 1 if attr == current else 0
            self._hotkey_menu.add(item)

    def _on_hotkey_pick(self, sender) -> None:
        label = str(sender.title)
        match = next((attr for lbl, attr in HOTKEY_CHOICES if lbl == label), None)
        if match is None or match in {
            self._current_type_hotkey_attr(),
            self._current_clipboard_hotkey_attr(),
        }:
            # collision guard; the entry should already be greyed
            return
        self.hotkey.trigger_key = getattr(Key, match)
        save_config(self._current_config(hotkey=match))
        for item in self._hotkey_menu.values():
            if isinstance(item, rumps.MenuItem):
                item.state = 1 if str(item.title) == label else 0
        self._refresh_hotkey_greyouts()

    def _current_hotkey_attr(self) -> str:
        name = getattr(self.hotkey.trigger_key, "name", None)
        if name in {attr for _, attr in HOTKEY_CHOICES}:
            return name
        return DEFAULT_CONFIG["hotkey"]

    # --- type-mode hotkey submenu -------------------------------------------

    def _build_type_hotkey_menu(self) -> None:
        current = self._current_type_hotkey_attr()
        for label, attr in HOTKEY_CHOICES:
            item = rumps.MenuItem(label, callback=self._on_type_hotkey_pick)
            item.state = 1 if attr == current else 0
            self._type_hotkey_menu.add(item)

    def _on_type_hotkey_pick(self, sender) -> None:
        label = str(sender.title)
        match = next((attr for lbl, attr in HOTKEY_CHOICES if lbl == label), None)
        if match is None or match in {
            self._current_hotkey_attr(),
            self._current_clipboard_hotkey_attr(),
        }:
            return
        self.hotkey.type_trigger_key = getattr(Key, match)
        save_config(self._current_config(type_hotkey=match))
        for item in self._type_hotkey_menu.values():
            if isinstance(item, rumps.MenuItem):
                item.state = 1 if str(item.title) == label else 0
        self._refresh_hotkey_greyouts()

    def _current_type_hotkey_attr(self) -> str:
        name = getattr(self.hotkey.type_trigger_key, "name", None)
        if name in {attr for _, attr in HOTKEY_CHOICES}:
            return name
        return DEFAULT_CONFIG["type_hotkey"]

    # --- clipboard hotkey submenu -------------------------------------------

    def _build_clipboard_hotkey_menu(self) -> None:
        current = self._current_clipboard_hotkey_attr()
        for label, attr in HOTKEY_CHOICES:
            item = rumps.MenuItem(label, callback=self._on_clipboard_hotkey_pick)
            item.state = 1 if attr == current else 0
            self._clipboard_hotkey_menu.add(item)

    def _on_clipboard_hotkey_pick(self, sender) -> None:
        label = str(sender.title)
        match = next((attr for lbl, attr in HOTKEY_CHOICES if lbl == label), None)
        if match is None or match in {
            self._current_hotkey_attr(),
            self._current_type_hotkey_attr(),
        }:
            return
        self.hotkey.clipboard_trigger_key = getattr(Key, match)
        save_config(self._current_config(clipboard_hotkey=match))
        for item in self._clipboard_hotkey_menu.values():
            if isinstance(item, rumps.MenuItem):
                item.state = 1 if str(item.title) == label else 0
        self._refresh_hotkey_greyouts()

    def _current_clipboard_hotkey_attr(self) -> str:
        name = getattr(self.hotkey.clipboard_trigger_key, "name", None)
        if name in {attr for _, attr in HOTKEY_CHOICES}:
            return name
        return DEFAULT_CONFIG["clipboard_hotkey"]

    def _refresh_hotkey_greyouts(self) -> None:
        """Grey out cross-bound entries in each submenu to prevent collisions.

        In each submenu, an entry is disabled when its key is bound by either
        of the *other two* hotkeys (but never the one this submenu owns).
        """
        own = {
            id(self._hotkey_menu): (self._current_hotkey_attr(), self._on_hotkey_pick),
            id(self._type_hotkey_menu): (
                self._current_type_hotkey_attr(),
                self._on_type_hotkey_pick,
            ),
            id(self._clipboard_hotkey_menu): (
                self._current_clipboard_hotkey_attr(),
                self._on_clipboard_hotkey_pick,
            ),
        }
        all_bound = {
            self._current_hotkey_attr(),
            self._current_type_hotkey_attr(),
            self._current_clipboard_hotkey_attr(),
        }
        for menu in (
            self._hotkey_menu,
            self._type_hotkey_menu,
            self._clipboard_hotkey_menu,
        ):
            own_attr, own_cb = own[id(menu)]
            others = all_bound - {own_attr}
            for item in menu.values():
                if not isinstance(item, rumps.MenuItem):
                    continue
                attr = next((a for lbl, a in HOTKEY_CHOICES if lbl == str(item.title)), None)
                if attr in others:
                    item.set_callback(None)
                else:
                    item.set_callback(own_cb)

    def _current_config(self, **overrides) -> dict:
        cfg = {
            "microphone": self.hotkey.device,
            "hotkey": self._current_hotkey_attr(),
            "type_hotkey": self._current_type_hotkey_attr(),
            "clipboard_hotkey": self._current_clipboard_hotkey_attr(),
        }
        cfg.update(overrides)
        return cfg

    # --- self-update --------------------------------------------------------

    def _set_update_label(self, title: str, callback: Optional[Callable]) -> None:
        """Enqueue a label change; the run-loop timer will apply it on the next tick."""
        self._update_queue.put((title, callback))

    def _on_check_updates(self, _sender) -> None:
        if not self._update_in_flight.acquire(blocking=False):
            return
        self._set_update_label("Checking…", None)
        threading.Thread(target=self._check_updates_worker, daemon=True).start()

    def _check_updates_worker(self) -> None:
        try:
            result = check_for_updates()
            self._render_check_result(result)
        finally:
            self._update_in_flight.release()

    def _render_check_result(self, result: UpdateCheck) -> None:
        if result.status == "up_to_date":
            self._set_update_label("Up to date ✓", self._on_check_updates)
            # Revert to "Check for Updates" after a few seconds so the user can
            # click again without the previous "✓" being misleading.
            threading.Timer(
                3.0,
                lambda: self._set_update_label("Check for Updates", self._on_check_updates),
            ).start()
        elif result.status == "update_available":
            label = (
                f"Update to {result.remote_sha} ({result.commits_behind} commit"
                f"{'s' if result.commits_behind != 1 else ''} behind)"
            )
            self._set_update_label(label, self._on_apply_update)
        elif result.status == "dirty":
            self._set_update_label("Update unavailable: local changes", None)
        elif result.status == "wrong_branch":
            self._set_update_label(f"Update unavailable: {result.error}", None)
        else:  # check_failed
            print(f"[blurt] update check failed: {result.error}", file=sys.stderr)
            self._set_update_label("Check failed — see logs", self._on_check_updates)

    def _on_apply_update(self, _sender) -> None:
        if self.hotkey._recording:
            return
        if self.recorder.is_active():
            # The update exits the process (launchd relaunches it); don't do
            # that mid-meeting or we'd lose the in-progress recording.
            print("[blurt] can't update: meeting recording in progress", file=sys.stderr)
            self._set_update_label("Stop the meeting recording first", self._on_apply_update)
            return
        if not self._update_in_flight.acquire(blocking=False):
            return
        self._set_update_label("Updating…", None)
        threading.Thread(target=self._apply_update_worker, daemon=True).start()

    def _apply_update_worker(self) -> None:
        try:
            result = apply_update()
            if result.status == "restarting":
                # restart_daemon already signalled SIGTERM; nothing more to do.
                return
            if result.status == "dirty":
                self._set_update_label("Update unavailable: local changes", None)
            elif result.status == "wrong_branch":
                self._set_update_label(f"Update unavailable: {result.error}", None)
            elif result.status == "uv_failed":
                print(f"[blurt] uv sync failed: {result.error}", file=sys.stderr)
                self._set_update_label("Update failed — see logs", self._on_apply_update)
            else:  # fetch_failed
                print(f"[blurt] update fetch failed: {result.error}", file=sys.stderr)
                self._set_update_label("Update failed — see logs", self._on_apply_update)
        finally:
            self._update_in_flight.release()

    def startup_update_check(self) -> None:
        """Run a non-blocking update check after launch."""
        if not self._has_launchagent:
            return
        if not self._update_in_flight.acquire(blocking=False):
            return
        threading.Thread(target=self._check_updates_worker, daemon=True).start()


# --- main -------------------------------------------------------------------

def main() -> int:
    cfg = load_config()

    # Resolve saved hotkey attr → Key. load_config guarantees this is valid.
    trigger_key = getattr(Key, cfg["hotkey"])
    type_trigger_key = getattr(Key, cfg["type_hotkey"])
    clipboard_trigger_key = getattr(Key, cfg["clipboard_hotkey"])

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

    meeting_active = threading.Event()
    ensure_permissions(meeting_active)
    hk = Hotkey(
        trigger_key=trigger_key,
        type_trigger_key=type_trigger_key,
        clipboard_trigger_key=clipboard_trigger_key,
        device=device,
        meeting_active=meeting_active,
    )
    hk.disabled = disabled
    recorder = MeetingRecorder(meeting_active)

    threading.Thread(target=load_model, daemon=True).start()
    listener = keyboard.Listener(on_press=hk.on_press, on_release=hk.on_release)
    listener.start()
    print("[blurt] hold the configured hotkey to talk. ⌘-click menu bar to quit.", flush=True)

    def sigterm(*_):
        if recorder.is_active():
            recorder.stop()  # finalize the transcript + wav before exit
        listener.stop()
        rumps.quit_application()

    signal.signal(signal.SIGINT, sigterm)
    signal.signal(signal.SIGTERM, sigterm)

    app = MenuApp(hotkey=hk, recorder=recorder)
    app.startup_update_check()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
