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


# --- request_missing_permissions ----------------------------------------------

def test_request_only_prompts_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(
        blurt, "accessibility_granted",
        lambda prompt=False: calls.append(("ax", prompt)) or False,
    )
    monkeypatch.setattr(
        blurt, "input_monitoring_granted",
        lambda prompt=False: calls.append(("im", prompt)) or True,
    )
    blurt.request_missing_permissions()
    assert ("ax", True) in calls
    assert ("im", True) not in calls


# --- watcher --------------------------------------------------------------------

def _watch(granted, **kw):
    defaults = dict(
        request=lambda: None,
        has_agent=lambda: True,
        meeting_active=threading.Event(),
        restart=lambda: pytest.fail("restart not expected"),
        poll_sec=0.01,
    )
    defaults.update(kw)
    blurt.watch_for_permission_grants(granted, **defaults)


def test_watcher_requests_then_restarts_when_granted():
    calls = []
    _watch(
        lambda: True,
        request=lambda: calls.append("request"),
        restart=lambda: calls.append("restart"),
    )
    assert calls == ["request", "restart"]


def test_watcher_no_agent_returns_without_restart():
    _watch(lambda: True, has_agent=lambda: False)  # default restart fails the test


def test_watcher_polls_until_granted():
    state = {"n": 0}
    def granted():
        state["n"] += 1
        return state["n"] >= 3
    restarts = []
    _watch(granted, restart=lambda: restarts.append(1))
    assert restarts == [1]
    assert state["n"] == 3


def test_watcher_defers_restart_during_meeting():
    meeting = threading.Event()
    meeting.set()
    state = {"n": 0}
    def granted():
        state["n"] += 1
        if state["n"] >= 2:
            meeting.clear()
        return True
    restarts = []
    _watch(granted, meeting_active=meeting, restart=lambda: restarts.append(1))
    assert restarts == [1]
    assert state["n"] >= 2


# --- ensure_permissions ---------------------------------------------------------

def test_ensure_permissions_all_granted_no_watcher(monkeypatch):
    monkeypatch.setattr(blurt, "accessibility_granted", lambda prompt=False: True)
    monkeypatch.setattr(blurt, "input_monitoring_granted", lambda prompt=False: True)
    started = []
    monkeypatch.setattr(
        blurt, "watch_for_permission_grants", lambda *a, **k: started.append(1)
    )
    blurt.STATE.title = "🎙"
    assert blurt.ensure_permissions(threading.Event()) is True
    assert started == []
    assert blurt.STATE.title == "🎙"


def test_ensure_permissions_missing_warns_and_watches(monkeypatch):
    monkeypatch.setattr(blurt, "accessibility_granted", lambda prompt=False: False)
    monkeypatch.setattr(blurt, "input_monitoring_granted", lambda prompt=False: True)
    ran = threading.Event()
    monkeypatch.setattr(
        blurt, "watch_for_permission_grants", lambda *a, **k: ran.set()
    )
    blurt.STATE.title = "🎙"
    assert blurt.ensure_permissions(threading.Event()) is False
    assert ran.wait(2.0), "watcher thread did not start"
    assert blurt.STATE.title == "⚠️"
    blurt.STATE.title = "🎙"  # restore for other tests
