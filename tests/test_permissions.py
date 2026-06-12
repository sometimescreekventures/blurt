import threading

import pytest

import blurt


# --- TCC wrappers -------------------------------------------------------------

def test_accessibility_granted_reads_ax(monkeypatch):
    monkeypatch.setattr(blurt, "_TCC_AVAILABLE", True, raising=False)
    monkeypatch.setattr(blurt, "AXIsProcessTrusted", lambda: False, raising=False)
    assert blurt.accessibility_granted() is False
    monkeypatch.setattr(blurt, "AXIsProcessTrusted", lambda: True, raising=False)
    assert blurt.accessibility_granted() is True


def test_accessibility_prompt_uses_options_dict(monkeypatch):
    seen = {}
    monkeypatch.setattr(blurt, "_TCC_AVAILABLE", True, raising=False)
    monkeypatch.setattr(blurt, "kAXTrustedCheckOptionPrompt", "prompt-key", raising=False)
    monkeypatch.setattr(
        blurt, "AXIsProcessTrustedWithOptions",
        lambda opts: seen.update(opts) or False, raising=False,
    )
    assert blurt.accessibility_granted(prompt=True) is False
    assert seen == {"prompt-key": True}


def test_input_monitoring_granted_reads_preflight(monkeypatch):
    monkeypatch.setattr(blurt, "_TCC_AVAILABLE", True, raising=False)
    monkeypatch.setattr(blurt, "CGPreflightListenEventAccess", lambda: False, raising=False)
    assert blurt.input_monitoring_granted() is False


def test_input_monitoring_prompt_uses_request(monkeypatch):
    calls = []
    monkeypatch.setattr(blurt, "_TCC_AVAILABLE", True, raising=False)
    monkeypatch.setattr(
        blurt, "CGRequestListenEventAccess",
        lambda: calls.append(1) or True, raising=False,
    )
    assert blurt.input_monitoring_granted(prompt=True) is True
    assert calls == [1]


def test_checks_assume_granted_without_tcc(monkeypatch):
    monkeypatch.setattr(blurt, "_TCC_AVAILABLE", False, raising=False)
    assert blurt.accessibility_granted() is True
    assert blurt.input_monitoring_granted(prompt=True) is True


def test_checks_assume_granted_when_api_raises(monkeypatch):
    monkeypatch.setattr(blurt, "_TCC_AVAILABLE", True, raising=False)
    def boom():
        raise RuntimeError("tcc broke")
    monkeypatch.setattr(blurt, "AXIsProcessTrusted", boom, raising=False)
    monkeypatch.setattr(blurt, "CGPreflightListenEventAccess", boom, raising=False)
    assert blurt.accessibility_granted() is True
    assert blurt.input_monitoring_granted() is True
