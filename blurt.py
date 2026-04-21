#!/usr/bin/env python3
"""blurt — push-to-talk dictation for macOS.

Hold Right Option (⌥) → speak → release.
Audio is transcribed locally via Parakeet-MLX and pasted at the cursor.
"""
from __future__ import annotations

import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

import numpy as np
import rumps
import sounddevice as sd
from pynput import keyboard
from pynput.keyboard import Controller as KBController, Key

SAMPLE_RATE = 16_000
CHANNELS = 1
BLOCK_SEC = 0.05
MIN_HOLD_SEC = 0.2
MIN_AUDIO_SEC = 0.15
CLIPBOARD_RESTORE_DELAY = 0.8
CONTINUATION_SEC = 15.0  # if within this since last paste, prepend a space

# Voice-activity gate: skip transcription when audio is essentially silence.
# Tune by watching max_rms values in the logs for your environment.
VAD_FRAME_SEC = 0.1
VAD_RMS_THRESHOLD = 0.01
VAD_MIN_VOICED_FRAMES = 2

MODEL_ID = "mlx-community/parakeet-tdt-0.6b-v2"

SOUND_START = "/System/Library/Sounds/Tink.aiff"
SOUND_STOP = "/System/Library/Sounds/Pop.aiff"
SOUND_VOLUME = "0.3"


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


# --- clipboard + paste ------------------------------------------------------

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
    def __init__(self) -> None:
        self._recording = False
        self._rec = Recorder()
        self._lock = threading.Lock()

    def on_press(self, key):
        if key != Key.alt_r:
            return
        with self._lock:
            if self._recording:
                return
            try:
                self._rec.start()
                self._recording = True
                STATE.title = "🔴"
                play_start()
            except Exception as e:
                print(f"[blurt] record start: {e}", file=sys.stderr)
                STATE.title = "⚠️"

    def on_release(self, key):
        if key != Key.alt_r:
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


# --- menu bar ---------------------------------------------------------------

class MenuApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("blurt", title="🎙", quit_button=None)
        self.menu = [rumps.MenuItem("Quit blurt", callback=self._quit)]

    @rumps.timer(0.1)
    def _tick(self, _):
        if self.title != STATE.title:
            self.title = STATE.title

    def _quit(self, _):
        rumps.quit_application()


# --- main -------------------------------------------------------------------

def main() -> int:
    hk = Hotkey()
    threading.Thread(target=load_model, daemon=True).start()
    listener = keyboard.Listener(on_press=hk.on_press, on_release=hk.on_release)
    listener.start()
    print("[blurt] hold Right Option to talk. ⌘-click menu bar to quit.", flush=True)

    def sigterm(*_):
        listener.stop()
        rumps.quit_application()

    signal.signal(signal.SIGINT, sigterm)
    signal.signal(signal.SIGTERM, sigterm)

    MenuApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
