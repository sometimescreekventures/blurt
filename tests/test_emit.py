import threading


class _RecordingKB:
    """Stand-in for pynput's Controller; records each .type() call."""

    def __init__(self):
        self.calls = []

    def type(self, ch):
        self.calls.append(ch)


def test_emit_keystrokes_types_every_char(monkeypatch):
    import blurt
    kb = _RecordingKB()
    monkeypatch.setattr(blurt, "_kb", kb)
    monkeypatch.setattr(blurt, "TYPE_KEY_DELAY", 0.0)

    n = blurt._emit_keystrokes("hello")
    assert n == 5
    assert "".join(kb.calls) == "hello"


def test_emit_keystrokes_preset_abort_types_nothing(monkeypatch):
    import blurt
    kb = _RecordingKB()
    monkeypatch.setattr(blurt, "_kb", kb)
    monkeypatch.setattr(blurt, "TYPE_KEY_DELAY", 0.0)

    abort = threading.Event()
    abort.set()
    n = blurt._emit_keystrokes("hello", abort=abort)
    assert n == 0
    assert kb.calls == []


def test_emit_keystrokes_abort_mid_stream(monkeypatch):
    """Abort set after 3 chars stops further typing (checked before each char)."""
    import blurt
    abort = threading.Event()

    class AbortAfter3(_RecordingKB):
        def type(self, ch):
            super().type(ch)
            if len(self.calls) == 3:
                abort.set()

    kb = AbortAfter3()
    monkeypatch.setattr(blurt, "_kb", kb)
    monkeypatch.setattr(blurt, "TYPE_KEY_DELAY", 0.0)

    n = blurt._emit_keystrokes("abcdefghij", abort=abort)
    assert n == 3
    assert "".join(kb.calls) == "abc"
