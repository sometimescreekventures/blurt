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


DEFAULTS = {
    "microphone": None,
    "hotkey": "alt_r",
    "type_hotkey": "cmd_r",
    "clipboard_hotkey": "ctrl_l",
    "update_channel": "shout",
}


def test_load_config_missing_file_returns_defaults(config_path):
    import blurt
    assert not config_path.exists()
    cfg = blurt.load_config()
    assert cfg == DEFAULTS


def test_load_config_valid_file(config_path):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "microphone": "C922",
                "hotkey": "f13",
                "type_hotkey": "f14",
                "clipboard_hotkey": "f15",
            }
        )
    )
    cfg = blurt.load_config()
    assert cfg == {
        "microphone": "C922",
        "hotkey": "f13",
        "type_hotkey": "f14",
        "clipboard_hotkey": "f15",
        "update_channel": "shout",
    }


def test_load_config_valid_channel(config_path):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"update_channel": "mumble"}))
    assert blurt.load_config()["update_channel"] == "mumble"


def test_load_config_unknown_channel_falls_back(config_path, capsys):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"update_channel": "scream"}))
    assert blurt.load_config()["update_channel"] == "shout"
    assert "update_channel" in capsys.readouterr().err.lower()


def test_load_config_malformed_json_returns_defaults(config_path, capsys):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{not valid json")
    cfg = blurt.load_config()
    assert cfg == DEFAULTS
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
    assert cfg == {**DEFAULTS, "microphone": "MyMic"}


def test_clipboard_hotkey_in_choices():
    import blurt
    assert "ctrl_l" in {attr for _, attr in blurt.HOTKEY_CHOICES}


def test_load_config_unknown_clipboard_hotkey_falls_back(config_path, capsys):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"clipboard_hotkey": "bogus_key"}))
    cfg = blurt.load_config()
    assert cfg["clipboard_hotkey"] == "ctrl_l"
    assert "clipboard_hotkey" in capsys.readouterr().err.lower()


def test_load_config_three_way_collision_resolves_distinct(config_path, capsys):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {"hotkey": "alt_r", "type_hotkey": "alt_r", "clipboard_hotkey": "alt_r"}
        )
    )
    cfg = blurt.load_config()
    keys = [cfg["hotkey"], cfg["type_hotkey"], cfg["clipboard_hotkey"]]
    assert keys[0] == "alt_r"  # highest priority keeps its binding
    assert len(set(keys)) == 3  # all three distinct
    assert all(k in {attr for _, attr in blurt.HOTKEY_CHOICES} for k in keys)


def test_load_config_clipboard_collides_with_type(config_path, capsys):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {"hotkey": "alt_r", "type_hotkey": "cmd_r", "clipboard_hotkey": "cmd_r"}
        )
    )
    cfg = blurt.load_config()
    assert cfg["clipboard_hotkey"] not in {"alt_r", "cmd_r"}
    assert cfg["type_hotkey"] == "cmd_r"


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
    saved = {
        "microphone": "MyMic",
        "hotkey": "f14",
        "type_hotkey": "f15",
        "clipboard_hotkey": "f16",
        "update_channel": "mumble",
    }
    blurt.save_config(saved)
    assert blurt.load_config() == saved


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
