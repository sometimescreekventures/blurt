import json
from pathlib import Path

import pytest


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch):
    """Point blurt.CONFIG_PATH at a throwaway location for each test."""
    import blurt
    path = tmp_path / "Application Support" / "blurt" / "config.json"
    monkeypatch.setattr(blurt, "CONFIG_PATH", path)
    return path


def test_load_config_missing_file_returns_defaults(config_path):
    import blurt
    assert not config_path.exists()
    cfg = blurt.load_config()
    assert cfg == {"microphone": None, "hotkey": "alt_r", "type_hotkey": "cmd_r"}


def test_load_config_valid_file(config_path):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"microphone": "C922", "hotkey": "f13", "type_hotkey": "f14"})
    )
    cfg = blurt.load_config()
    assert cfg == {"microphone": "C922", "hotkey": "f13", "type_hotkey": "f14"}


def test_load_config_malformed_json_returns_defaults(config_path, capsys):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{not valid json")
    cfg = blurt.load_config()
    assert cfg == {"microphone": None, "hotkey": "alt_r", "type_hotkey": "cmd_r"}
    assert "config" in capsys.readouterr().err.lower()


def test_load_config_unknown_hotkey_falls_back(config_path, capsys):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"microphone": None, "hotkey": "bogus_key"}))
    cfg = blurt.load_config()
    assert cfg["hotkey"] == "alt_r"
    assert cfg["microphone"] is None
    assert "hotkey" in capsys.readouterr().err.lower()


def test_load_config_partial_file_merges_defaults(config_path):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"microphone": "MyMic"}))
    cfg = blurt.load_config()
    assert cfg == {"microphone": "MyMic", "hotkey": "alt_r", "type_hotkey": "cmd_r"}


def test_load_config_unknown_type_hotkey_falls_back(config_path, capsys):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"type_hotkey": "bogus_key"}))
    cfg = blurt.load_config()
    assert cfg["type_hotkey"] == "cmd_r"
    assert "type_hotkey" in capsys.readouterr().err.lower()


def test_load_config_collision_falls_back_type_hotkey(config_path, capsys):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"hotkey": "alt_r", "type_hotkey": "alt_r"}))
    cfg = blurt.load_config()
    assert cfg["hotkey"] == "alt_r"
    assert cfg["type_hotkey"] != "alt_r"
    assert cfg["type_hotkey"] in {attr for _, attr in blurt.HOTKEY_CHOICES}
    err = capsys.readouterr().err.lower()
    assert "bound" in err or "collision" in err or "both" in err


def test_load_config_does_not_overwrite_malformed_file(config_path):
    import blurt
    config_path.parent.mkdir(parents=True)
    original = "{not valid json"
    config_path.write_text(original)
    blurt.load_config()
    assert config_path.read_text() == original


def test_save_config_round_trip(config_path):
    import blurt
    blurt.save_config({"microphone": "MyMic", "hotkey": "f14", "type_hotkey": "f15"})
    assert blurt.load_config() == {
        "microphone": "MyMic", "hotkey": "f14", "type_hotkey": "f15"
    }


def test_save_config_creates_parent_dir(config_path):
    import blurt
    assert not config_path.parent.exists()
    blurt.save_config({"microphone": None, "hotkey": "alt_r"})
    assert config_path.exists()


def test_save_config_write_failure_does_not_raise(tmp_path, monkeypatch, capsys):
    import blurt
    # Point CONFIG_PATH at a child of a file — mkdir will fail.
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setattr(blurt, "CONFIG_PATH", blocker / "sub" / "config.json")
    blurt.save_config({"microphone": None, "hotkey": "alt_r"})
    assert "config save failed" in capsys.readouterr().err.lower()
