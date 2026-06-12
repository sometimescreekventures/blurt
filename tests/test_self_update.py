import os
import subprocess
from pathlib import Path

import pytest

# Ignore the user's global/system git config (commit signing, hooks, etc.) so
# fixture commits work in any environment — e.g. 1Password-backed signing
# can't run non-interactively and would fail every commit.
_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True,
        env=_GIT_ENV,
    )
    return proc.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    (repo / "marker.txt").write_text(message)
    _git(repo, "add", "marker.txt")
    _git(repo, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _tag(repo: Path, name: str, ref: str = "HEAD", annotated: bool = False) -> None:
    args = ["tag", "-f"] + (["-a", "-m", name] if annotated else []) + [name, ref]
    _git(repo, *args)
    _git(repo, "push", "--force", "origin", name)


@pytest.fixture
def local_repo(tmp_path: Path, monkeypatch):
    """Build a local repo + bare remote at `origin`, with the local on `main`,
    one commit shared, and a v0.1.0 release with both channel tags on it.
    Tests mutate the remote then poke check_for_updates."""
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True, env=_GIT_ENV
    )
    subprocess.run(
        ["git", "init", "-b", "main", str(local)], check=True, capture_output=True, env=_GIT_ENV
    )
    _commit(local, "initial")
    _git(local, "remote", "add", "origin", str(remote))
    _git(local, "push", "-u", "origin", "main")
    _tag(local, "v0.1.0", annotated=True)
    _tag(local, "shout")
    _tag(local, "mumble")

    import blurt
    monkeypatch.setattr(blurt, "REPO_ROOT", local)
    return local


def test_check_for_updates_up_to_date(local_repo):
    import blurt
    result = blurt.check_for_updates("shout", repo=local_repo)
    assert result.status == "up_to_date"
    assert result.commits_behind == 0
    assert result.version == "v0.1.0"
    assert result.channel == "shout"


def test_check_for_updates_update_available_per_channel(local_repo, tmp_path):
    import blurt
    # Release a new beta from a second clone: commit, tag v0.2.0, move mumble.
    clone = tmp_path / "pusher"
    subprocess.run(
        ["git", "clone", _git(local_repo, "remote", "get-url", "origin"), str(clone)],
        check=True, capture_output=True, env=_GIT_ENV,
    )
    _commit(clone, "remote update")
    _git(clone, "push", "origin", "main")
    _tag(clone, "v0.2.0", annotated=True)
    _tag(clone, "mumble")

    result = blurt.check_for_updates("mumble", repo=local_repo)
    assert result.status == "update_available"
    assert result.commits_behind == 1
    assert result.version == "v0.2.0"
    assert result.local_sha != result.remote_sha

    # shout hasn't moved — the stable channel is still current.
    assert blurt.check_for_updates("shout", repo=local_repo).status == "up_to_date"


def test_check_for_updates_dirty(local_repo):
    import blurt
    (local_repo / "marker.txt").write_text("hand-edited")
    result = blurt.check_for_updates("shout", repo=local_repo)
    assert result.status == "dirty"


def test_check_for_updates_untracked_does_not_block(local_repo):
    """git reset --hard preserves untracked files, so they should not block update."""
    import blurt
    (local_repo / "scratch.txt").write_text("not committed, not ignored")
    result = blurt.check_for_updates("shout", repo=local_repo)
    assert result.status == "up_to_date"


def test_check_for_updates_wrong_branch(local_repo):
    import blurt
    _git(local_repo, "checkout", "-b", "feature-foo")
    result = blurt.check_for_updates("shout", repo=local_repo)
    assert result.status == "wrong_branch"
    assert "feature-foo" in result.error


def test_check_for_updates_non_repo(tmp_path, monkeypatch):
    import blurt
    monkeypatch.setattr(blurt, "REPO_ROOT", tmp_path)
    result = blurt.check_for_updates("shout", repo=tmp_path)
    assert result.status == "check_failed"


def test_check_for_updates_missing_channel_tag(local_repo):
    import blurt
    _git(local_repo, "push", "origin", ":refs/tags/mumble")
    _git(local_repo, "tag", "-d", "mumble")
    result = blurt.check_for_updates("mumble", repo=local_repo)
    assert result.status == "check_failed"
    assert "cut a release" in result.error


def test_check_for_updates_downgrade_offered(local_repo):
    """HEAD ahead of the channel tag is still an update (behind == 0)."""
    import blurt
    _commit(local_repo, "local work beyond the release")
    _git(local_repo, "push", "origin", "main")
    result = blurt.check_for_updates("shout", repo=local_repo)
    assert result.status == "update_available"
    assert result.commits_behind == 0
    assert result.version == "v0.1.0"


def test_version_at_picks_highest(local_repo):
    import blurt
    _tag(local_repo, "v0.2.0", annotated=True)
    assert blurt._version_at("HEAD", repo=local_repo) == "v0.2.0"


def test_apply_update_wrong_branch(local_repo):
    import blurt
    _git(local_repo, "checkout", "-b", "feature-bar")
    assert blurt.apply_update("shout").status == "wrong_branch"


def test_apply_update_missing_channel_tag_is_fetch_failed(local_repo):
    import blurt
    _git(local_repo, "push", "origin", ":refs/tags/shout")
    _git(local_repo, "tag", "-d", "shout")
    assert blurt.apply_update("shout").status == "fetch_failed"


# --- uv resolution ------------------------------------------------------------

def test_uv_binary_prefers_path(monkeypatch):
    """When uv is on PATH, use whatever shutil.which finds."""
    import blurt
    monkeypatch.setattr(blurt.shutil, "which", lambda name: "/opt/somewhere/bin/uv")
    assert blurt._uv_binary() == "/opt/somewhere/bin/uv"


def test_uv_binary_falls_back_to_local_bin(monkeypatch, tmp_path):
    """launchd's default PATH lacks ~/.local/bin, where the astral.sh installer
    puts uv — the resolver must fall back there explicitly."""
    import blurt
    monkeypatch.setattr(blurt.shutil, "which", lambda name: None)
    monkeypatch.setattr(blurt.Path, "home", staticmethod(lambda: tmp_path))
    uv = tmp_path / ".local" / "bin" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("#!/bin/sh\n")
    assert blurt._uv_binary() == str(uv)


def test_uv_binary_missing_raises(monkeypatch, tmp_path):
    import blurt
    monkeypatch.setattr(blurt.shutil, "which", lambda name: None)
    monkeypatch.setattr(blurt.Path, "home", staticmethod(lambda: tmp_path))
    with pytest.raises(FileNotFoundError):
        blurt._uv_binary()


# --- restart mechanism ------------------------------------------------------

def test_restart_daemon_exits_nonzero():
    """The daemon must exit with a non-zero status so the LaunchAgent's
    KeepAlive(SuccessfulExit=false) relaunches it with the freshly-synced code.
    A clean exit (0) would NOT relaunch — that was the original bug."""
    import blurt
    codes = []
    blurt.restart_daemon(_exit=lambda code: codes.append(code))
    assert codes == [1]


def test_restart_daemon_spawns_no_helper(monkeypatch, tmp_path):
    """The detached helper approach was unreliable (launchd reaps the job's
    descendants). The fix must not spawn any subprocess."""
    import blurt
    popened = []
    monkeypatch.setattr(blurt.subprocess, "Popen", lambda *a, **k: popened.append(a))
    helper = tmp_path / "blurt-restart.sh"
    blurt.restart_daemon(_exit=lambda code: None)
    assert popened == []
    assert not helper.exists()
