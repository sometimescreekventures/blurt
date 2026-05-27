import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    (repo / "marker.txt").write_text(message)
    _git(repo, "add", "marker.txt")
    _git(repo, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def local_repo(tmp_path: Path, monkeypatch):
    """Build a local repo + bare remote at `origin`, with the local on `main` and
    one commit shared. Returns the local repo path. Tests mutate the remote then
    poke check_for_updates."""
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(local)], check=True, capture_output=True)
    _commit(local, "initial")
    _git(local, "remote", "add", "origin", str(remote))
    _git(local, "push", "-u", "origin", "main")

    import blurt
    monkeypatch.setattr(blurt, "REPO_ROOT", local)
    return local


def test_check_for_updates_up_to_date(local_repo):
    import blurt
    result = blurt.check_for_updates(repo=local_repo)
    assert result.status == "up_to_date"
    assert result.commits_behind == 0


def test_check_for_updates_update_available(local_repo, tmp_path):
    import blurt
    # Push a new commit to origin by cloning, committing, pushing back.
    clone = tmp_path / "pusher"
    subprocess.run(
        ["git", "clone", _git(local_repo, "remote", "get-url", "origin"), str(clone)],
        check=True, capture_output=True,
    )
    _commit(clone, "remote update")
    _git(clone, "push", "origin", "main")

    result = blurt.check_for_updates(repo=local_repo)
    assert result.status == "update_available"
    assert result.commits_behind == 1
    assert result.local_sha != result.remote_sha


def test_check_for_updates_dirty(local_repo):
    import blurt
    (local_repo / "marker.txt").write_text("hand-edited")
    result = blurt.check_for_updates(repo=local_repo)
    assert result.status == "dirty"


def test_check_for_updates_wrong_branch(local_repo):
    import blurt
    _git(local_repo, "checkout", "-b", "feature-foo")
    result = blurt.check_for_updates(repo=local_repo)
    assert result.status == "wrong_branch"
    assert "feature-foo" in result.error


def test_check_for_updates_non_repo(tmp_path, monkeypatch):
    import blurt
    monkeypatch.setattr(blurt, "REPO_ROOT", tmp_path)
    result = blurt.check_for_updates(repo=tmp_path)
    assert result.status == "check_failed"
