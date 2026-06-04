import threading
from datetime import datetime

import numpy as np
import pytest


@pytest.fixture
def meeting_dir(tmp_path, monkeypatch):
    import blurt
    d = tmp_path / "blurt-meetings"
    monkeypatch.setattr(blurt, "MEETING_DIR", d)
    return d


# --- filename helper --------------------------------------------------------

def test_meeting_paths_basic(meeting_dir):
    import blurt
    txt, wav = blurt.meeting_paths(datetime(2026, 6, 4, 14, 30), meeting_dir)
    assert txt.name == "2026-06-04-14-30-meeting.txt"
    assert wav.name == "2026-06-04-14-30-meeting.wav"
    assert txt.parent == meeting_dir


def test_meeting_paths_disambiguates(meeting_dir):
    import blurt
    meeting_dir.mkdir(parents=True)
    dt = datetime(2026, 6, 4, 14, 30)
    txt1, wav1 = blurt.meeting_paths(dt, meeting_dir)
    txt1.write_text("first")  # occupy the base .txt
    txt2, wav2 = blurt.meeting_paths(dt, meeting_dir)
    assert txt2.name == "2026-06-04-14-30-meeting-2.txt"
    assert wav2.name == "2026-06-04-14-30-meeting-2.wav"


def test_meeting_paths_disambiguates_on_wav_collision(meeting_dir):
    """A leftover .wav alone should still bump the stem (both files share it)."""
    import blurt
    meeting_dir.mkdir(parents=True)
    dt = datetime(2026, 6, 4, 14, 30)
    _txt1, wav1 = blurt.meeting_paths(dt, meeting_dir)
    wav1.write_text("audio")
    txt2, _wav2 = blurt.meeting_paths(dt, meeting_dir)
    assert txt2.name == "2026-06-04-14-30-meeting-2.txt"


def test_fmt_hms():
    import blurt
    assert blurt._fmt_hms(0) == "00:00:00"
    assert blurt._fmt_hms(59) == "00:00:59"
    assert blurt._fmt_hms(3723) == "01:02:03"


# --- chunk flushing ---------------------------------------------------------

def _silence(seconds=1.0):
    return np.zeros(int(16000 * seconds), dtype=np.float32)


def _voiced(seconds=1.0):
    # Above VAD_RMS_THRESHOLD across the whole window.
    return (np.ones(int(16000 * seconds), dtype=np.float32) * 0.2)


def test_flush_chunk_skips_silence(meeting_dir, monkeypatch):
    import blurt
    meeting_dir.mkdir(parents=True)
    called = []
    monkeypatch.setattr(blurt, "_transcribe_array", lambda m, a: called.append(a) or "X")

    rec = blurt.MeetingRecorder(threading.Event())
    rec._txt_path = meeting_dir / "m.txt"
    rec._txt_path.write_text("")
    rec._chunk = [_silence()]
    rec._chunk_samples = rec._chunk[0].shape[0]
    rec._flush_chunk()

    assert called == []  # transcription never invoked for silence
    assert rec._txt_path.read_text() == ""


def test_flush_chunk_appends_transcript_for_voiced(meeting_dir, monkeypatch):
    import blurt
    meeting_dir.mkdir(parents=True)
    monkeypatch.setattr(blurt, "load_model", lambda: object())
    monkeypatch.setattr(blurt, "_transcribe_array", lambda m, a: "hello world")

    rec = blurt.MeetingRecorder(threading.Event())
    rec._txt_path = meeting_dir / "m.txt"
    rec._txt_path.write_text("")
    rec._chunk = [_voiced()]
    rec._chunk_samples = rec._chunk[0].shape[0]
    rec._flush_chunk()

    assert rec._txt_path.read_text() == "hello world "
    # chunk buffer reset after flush
    assert rec._chunk_samples == 0
    assert rec._chunk == []


def test_flush_chunk_transcription_error_does_not_raise(meeting_dir, monkeypatch):
    import blurt
    meeting_dir.mkdir(parents=True)

    def boom(model):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(blurt, "load_model", boom)
    rec = blurt.MeetingRecorder(threading.Event())
    rec._txt_path = meeting_dir / "m.txt"
    rec._txt_path.write_text("")
    rec._chunk = [_voiced()]
    rec._chunk_samples = rec._chunk[0].shape[0]
    rec._flush_chunk()  # must not raise
    assert rec._txt_path.read_text() == ""


# --- mutual exclusion -------------------------------------------------------

def test_meeting_active_blocks_dictation(monkeypatch):
    import blurt
    from pynput.keyboard import Key

    meeting_active = threading.Event()
    hk = blurt.Hotkey(Key.alt_r, Key.cmd_r, Key.ctrl_l, None, meeting_active=meeting_active)

    started = []
    monkeypatch.setattr(hk._rec, "start", lambda dev: started.append(dev))

    meeting_active.set()
    hk.on_press(Key.alt_r)  # dictation key while a meeting owns the mic
    assert started == []  # recorder.start never called
    assert hk._recording is False


def test_dictation_works_when_no_meeting(monkeypatch):
    import blurt
    from pynput.keyboard import Key

    meeting_active = threading.Event()  # not set
    hk = blurt.Hotkey(Key.alt_r, Key.cmd_r, Key.ctrl_l, None, meeting_active=meeting_active)
    monkeypatch.setattr(hk._rec, "start", lambda dev: None)

    hk.on_press(Key.alt_r)
    assert hk._recording is True
