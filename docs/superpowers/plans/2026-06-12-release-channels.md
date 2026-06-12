# Release Channels (shout / mumble) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace main-tracking self-update with channel-tag releases (`shout` stable / `mumble` beta), a `release.sh` cut/promote/status script with GitHub Release notes, and a Channel menu.

**Architecture:** Immutable `vX.Y.Z` tags + two floating channel tags. `check_for_updates(channel)`/`apply_update(channel)` compare/reset HEAD against `<channel>^{commit}` instead of `origin/main`. A Channel submenu persists `update_channel` in config.json. `release.sh` shells `git tag`/`gh release`.

**Tech Stack:** Python 3.12, git tags, gh CLI, pytest, bash.

**Spec:** `docs/superpowers/specs/2026-06-12-release-channels-design.md`

---

### Task 1: Channel config key

**Files:**
- Modify: `blurt.py` (constants ~line 65, `DEFAULT_CONFIG`, `load_config`)
- Modify: `tests/test_config.py`

- [ ] **Step 1: Failing tests** — in `tests/test_config.py`, add `"update_channel": "shout"` to the `DEFAULTS` dict, and append:

```python
def test_load_config_valid_channel(config_path):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"update_channel": "mumble"}))
    assert blurt.load_config()["update_channel"] == "mumble"


def test_load_config_unknown_channel_falls_back(config_path):
    import blurt
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"update_channel": "scream"}))
    assert blurt.load_config()["update_channel"] == "shout"
```

- [ ] **Step 2: Verify RED** — `uv run pytest tests/test_config.py -q` → the two new tests fail (`KeyError`/wrong value) and the `cfg == DEFAULTS` tests fail (missing key).

- [ ] **Step 3: Implement** — in `blurt.py` replace the update-constants comment block:

```python
# Self-update follows a release channel: a floating git tag moved by
# release.sh. "shout" = stable (default), "mumble" = beta. The checkout must
# still be on UPDATE_BRANCH for updates to run.
UPDATE_REMOTE = "origin"
UPDATE_BRANCH = "main"
UPDATE_CHANNELS = ("shout", "mumble")
```

Add to `DEFAULT_CONFIG`: `"update_channel": "shout",`
In `load_config`, after the type/clipboard hotkey loop:

```python
    if "update_channel" in raw:
        if raw["update_channel"] in UPDATE_CHANNELS:
            cfg["update_channel"] = raw["update_channel"]
        else:
            print(
                f"[blurt] unknown update_channel {raw['update_channel']!r}; "
                f"using {DEFAULT_CONFIG['update_channel']!r}",
                file=sys.stderr,
            )
```

- [ ] **Step 4: Verify GREEN** — `uv run pytest tests/test_config.py -q` → all pass.
- [ ] **Step 5: Commit** — `git add blurt.py tests/test_config.py && git commit --no-gpg-sign -m "Add update_channel config key"`

---

### Task 2: Channel-aware check_for_updates

**Files:**
- Modify: `blurt.py` (`UpdateCheck`, new `_version_at`, `check_for_updates`)
- Modify: `tests/test_self_update.py`

- [ ] **Step 1: Failing tests** — in `tests/test_self_update.py`:

Add after `_commit`:

```python
def _tag(repo: Path, name: str, ref: str = "HEAD", annotated: bool = False) -> None:
    args = ["tag", "-f"] + (["-a", "-m", name] if annotated else []) + [name, ref]
    _git(repo, *args)
    _git(repo, "push", "--force", "origin", name)
```

In the `local_repo` fixture, after the `push -u origin main` line, add tags so a
release exists at the initial commit:

```python
    _tag(local, "v0.1.0", annotated=True)
    _tag(local, "shout")
    _tag(local, "mumble")
```

Update the existing tests for the new signature/expectations:
- `test_check_for_updates_up_to_date`: call `blurt.check_for_updates("shout", repo=local_repo)`; also assert `result.version == "v0.1.0"` and `result.channel == "shout"`.
- `test_check_for_updates_update_available`: in the pusher clone, after `_commit`, run `_tag(clone, "v0.2.0", annotated=True)` and `_tag(clone, "mumble")`; call with `"mumble"`; assert `status == "update_available"`, `commits_behind == 1`, `version == "v0.2.0"`; then call with `"shout"` and assert `up_to_date` (channels independent).
- `test_check_for_updates_dirty`, `test_check_for_updates_untracked_does_not_block`, `test_check_for_updates_wrong_branch`, `test_check_for_updates_non_repo`: pass `"shout"` as the first argument; expectations unchanged.

Append new tests:

```python
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
```

- [ ] **Step 2: Verify RED** — `uv run pytest tests/test_self_update.py -q` → failures (TypeError on positional arg / missing `_version_at` / missing fields).

- [ ] **Step 3: Implement** — in `blurt.py`:

`UpdateCheck` gains fields (after `commits_behind`):

```python
    version: str = ""  # vX.Y.Z at the channel commit, "" if untagged
    channel: str = ""
```

After `_git`, add:

```python
def _version_at(commit: str, *, repo: Path | None = None) -> str:
    """Highest v* tag pointing at `commit`, or '' if none."""
    try:
        tags = _git(
            ["tag", "--points-at", commit, "--list", "v*", "--sort=-v:refname"],
            repo=repo, timeout=5.0,
        )
    except (RuntimeError, subprocess.TimeoutExpired):
        return ""
    return tags.splitlines()[0] if tags else ""
```

Rewrite `check_for_updates`:

```python
def check_for_updates(channel: str = UPDATE_CHANNELS[0], repo: Path | None = None) -> UpdateCheck:
    """Fetch tags and compare local HEAD to the channel's floating tag."""
    repo = repo or REPO_ROOT
    try:
        branch = _git(["symbolic-ref", "--short", "HEAD"], repo=repo, timeout=2.0)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return UpdateCheck(status="check_failed", channel=channel, error=str(e))
    if branch != UPDATE_BRANCH:
        return UpdateCheck(
            status="wrong_branch", channel=channel,
            error=f"on {branch}, expected {UPDATE_BRANCH}",
        )
    try:
        dirty = _git(["status", "--porcelain", "--untracked-files=no"], repo=repo, timeout=5.0)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return UpdateCheck(status="check_failed", channel=channel, error=str(e))
    try:
        _git(["fetch", UPDATE_REMOTE, "--tags", "--force"], repo=repo, timeout=15.0)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return UpdateCheck(status="check_failed", channel=channel, error=str(e))
    try:
        local_sha = _git(["rev-parse", "--short", "HEAD"], repo=repo, timeout=2.0)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return UpdateCheck(status="check_failed", channel=channel, error=str(e))
    try:
        remote_sha = _git(["rev-parse", "--short", f"{channel}^{{commit}}"], repo=repo, timeout=2.0)
    except (RuntimeError, subprocess.TimeoutExpired):
        return UpdateCheck(
            status="check_failed", channel=channel, local_sha=local_sha,
            error=f"channel tag {channel!r} not found — cut a release first",
        )
    version = _version_at(remote_sha, repo=repo)
    try:
        behind = int(
            _git(["rev-list", "--count", f"HEAD..{channel}^{{commit}}"], repo=repo, timeout=5.0)
        )
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as e:
        return UpdateCheck(status="check_failed", channel=channel, error=str(e))

    common = dict(
        local_sha=local_sha, remote_sha=remote_sha,
        commits_behind=behind, version=version, channel=channel,
    )
    if dirty:
        return UpdateCheck(status="dirty", **common)
    if remote_sha == local_sha:
        return UpdateCheck(status="up_to_date", **common)
    return UpdateCheck(status="update_available", **common)
```

- [ ] **Step 4: Verify GREEN** — `uv run pytest tests/test_self_update.py -q` → all pass.
- [ ] **Step 5: Commit** — `git add blurt.py tests/test_self_update.py && git commit --no-gpg-sign -m "Retarget update check to release channel tags"`

---

### Task 3: Channel-aware apply_update + version display

**Files:**
- Modify: `blurt.py` (`apply_update`, `current_version`)
- Modify: `tests/test_self_update.py`

- [ ] **Step 1: Failing tests** — append:

```python
def test_apply_update_wrong_branch(local_repo):
    import blurt
    _git(local_repo, "checkout", "-b", "feature-bar")
    assert blurt.apply_update("shout").status == "wrong_branch"


def test_apply_update_missing_channel_tag_is_fetch_failed(local_repo):
    import blurt
    _git(local_repo, "push", "origin", ":refs/tags/shout")
    _git(local_repo, "tag", "-d", "shout")
    assert blurt.apply_update("shout").status == "fetch_failed"
```

(Both rely on the fixture's `monkeypatch.setattr(blurt, "REPO_ROOT", local)` and
return before `uv sync` / restart.)

- [ ] **Step 2: Verify RED** — `uv run pytest tests/test_self_update.py -q` → TypeError (apply takes no args) / wrong status.

- [ ] **Step 3: Implement** — `apply_update` signature becomes
`def apply_update(channel: str = UPDATE_CHANNELS[0]) -> ApplyResult:` and its
fetch/reset block becomes:

```python
    try:
        _git(["fetch", UPDATE_REMOTE, "--tags", "--force"], timeout=30.0)
        target = _git(["rev-parse", f"{channel}^{{commit}}"], timeout=5.0)
        _git(["reset", "--hard", target], timeout=10.0)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return ApplyResult(status="fetch_failed", error=str(e))
```

(docstring: "Fetch tags, reset to the channel tag's commit, uv sync, restart.")

`current_version` body: after computing `sha` and `date`, return the release
name when HEAD is a release:

```python
        label = _version_at("HEAD") or sha
        return (label, date)
```

- [ ] **Step 4: Verify GREEN** — `uv run pytest tests/test_self_update.py -q`, then full `uv run pytest -q`.
- [ ] **Step 5: Commit** — `git add blurt.py tests/test_self_update.py && git commit --no-gpg-sign -m "Apply updates from the channel tag; show release in version"`

---

### Task 4: Channel submenu + MenuApp wiring

**Files:**
- Modify: `blurt.py` (`MenuApp`, `main()`)

No unit tests (rumps UI); covered by the smoke test in Task 7.

- [ ] **Step 1: Implement.**

`MenuApp` class attribute next to `_SYSTEM_DEFAULT_LABEL`:

```python
    _CHANNEL_LABELS = {"shout": "🗣️ Shout (stable)", "mumble": "🤫 Mumble (beta)"}
```

`__init__` signature: `def __init__(self, hotkey: "Hotkey", recorder: "MeetingRecorder", channel: str) -> None:` with `self._channel = channel` set before menu construction. Build the submenu after `self._clipboard_hotkey_menu`:

```python
        self._channel_menu = rumps.MenuItem("Channel")
        self._build_channel_menu()
```

Version line uses a helper; replace the two `version_label` lines with:

```python
        self._version_item = rumps.MenuItem(self._version_text())  # no callback → disabled
```

New methods (next to the hotkey submenu builders):

```python
    def _version_text(self) -> str:
        label, date = current_version()
        text = f"Version: {label}" + (f" ({date})" if date else "")
        return f"{text} · {self._channel}"

    def _build_channel_menu(self) -> None:
        for channel in UPDATE_CHANNELS:
            item = rumps.MenuItem(self._CHANNEL_LABELS[channel], callback=self._on_channel_pick)
            item.state = 1 if channel == self._channel else 0
            self._channel_menu.add(item)

    def _on_channel_pick(self, sender) -> None:
        label = str(sender.title)
        match = next((ch for ch, lbl in self._CHANNEL_LABELS.items() if lbl == label), None)
        if match is None or match == self._channel:
            return
        self._channel = match
        save_config(self._current_config(update_channel=match))
        for item in self._channel_menu.values():
            if isinstance(item, rumps.MenuItem):
                item.state = 1 if str(item.title) == label else 0
        self._version_item.title = self._version_text()
        # Show immediately what this channel offers (may be a downgrade).
        self._on_check_updates(None)
```

Wire the rest:
- `self.menu` list: insert `self._channel_menu` after `self._clipboard_hotkey_menu`.
- `_current_config`: add `"update_channel": self._channel,`.
- `_check_updates_worker`: `result = check_for_updates(self._channel)`.
- `_apply_update_worker`: `result = apply_update(self._channel)`.
- `_render_check_result` update_available branch:

```python
        elif result.status == "update_available":
            name = result.version or result.remote_sha
            if result.commits_behind:
                label = (
                    f"Update to {name} ({result.commits_behind} commit"
                    f"{'s' if result.commits_behind != 1 else ''} behind)"
                )
            else:
                label = f"Switch to {name}"
            self._set_update_label(label, self._on_apply_update)
```

- `main()`: `app = MenuApp(hotkey=hk, recorder=recorder, channel=cfg["update_channel"])`.

- [ ] **Step 2: Sanity** — `uv run python -c "import ast; ast.parse(open('blurt.py').read())"` then full `uv run pytest -q` (all green).
- [ ] **Step 3: Commit** — `git add blurt.py && git commit --no-gpg-sign -m "Add Channel submenu; thread channel through update flow"`

---

### Task 5: release.sh

**Files:**
- Create: `release.sh` (mode 755) — full content in the spec's Components section; implement exactly the cut/promote/status flows:

```bash
#!/usr/bin/env bash
# Release management for blurt.
#
#   ./release.sh [--minor|--major]   cut a beta: tag vX.Y.Z, GitHub pre-release
#                                    with generated notes, move 🤫 mumble
#   ./release.sh promote             graduate: move 🗣️ shout to mumble's
#                                    version, mark that release latest
#   ./release.sh status              show channel pointers + unreleased work
#
# Channels are floating git tags (shout = stable, mumble = beta) that the
# menu-bar updater follows. Requires gh (authenticated) and a clean checkout
# on main matching origin/main.

set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

die() { echo "error: $*" >&2; exit 1; }

command -v gh >/dev/null 2>&1 || die "gh CLI required (brew install gh)"

ver_at() {  # highest v* tag at a commit-ish, empty if none
    git tag --points-at "$1^{commit}" --list 'v*' --sort=-v:refname 2>/dev/null | head -1
}

case "${1:-cut}" in
    cut|--patch|--minor|--major)
        bump="${1:-cut}"
        git fetch origin --tags --force --quiet
        [[ "$(git symbolic-ref --short HEAD)" == "main" ]] || die "not on main"
        [[ -z "$(git status --porcelain --untracked-files=no)" ]] || die "dirty checkout"
        [[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] \
            || die "main != origin/main — push or pull first"
        existing="$(ver_at HEAD)"
        [[ -z "$existing" ]] || die "HEAD is already released as $existing"

        last="$(git tag --list 'v*' --sort=-v:refname | head -1)"
        last="${last:-v0.1.0}"
        IFS=. read -r maj min pat <<<"${last#v}"
        case "$bump" in
            --major) new="v$((maj + 1)).0.0" ;;
            --minor) new="v$maj.$((min + 1)).0" ;;
            *)       new="v$maj.$min.$((pat + 1))" ;;
        esac

        git tag -a "$new" -m "blurt $new"
        git push --quiet origin "$new"
        gh release create "$new" --title "blurt $new" --generate-notes --prerelease
        git tag -f mumble "$new" >/dev/null
        git push --quiet --force origin mumble
        echo "🤫 mumble → $new (beta cut; promote with: ./release.sh promote)"
        ;;
    promote)
        git fetch origin --tags --force --quiet
        ver="$(ver_at mumble)"
        [[ -n "$ver" ]] || die "mumble doesn't point at a release — cut one first"
        [[ "$ver" != "$(ver_at shout || true)" ]] || die "shout is already at $ver"
        git tag -f shout "$ver" >/dev/null
        git push --quiet --force origin shout
        gh release edit "$ver" --prerelease=false --latest
        echo "🗣️ shout → $ver"
        ;;
    status)
        git fetch origin --tags --force --quiet
        for ch in shout mumble; do
            if git rev-parse -q --verify "$ch^{commit}" >/dev/null; then
                echo "$ch → $(ver_at "$ch") ($(git rev-parse --short "$ch^{commit}"))"
            else
                echo "$ch → (unset)"
            fi
        done
        if git rev-parse -q --verify "mumble^{commit}" >/dev/null; then
            echo "unreleased on main: $(git rev-list --count mumble..origin/main) commit(s)"
        fi
        ;;
    *)
        sed -n '2,11p' "$0"
        exit 1
        ;;
esac
```

- [ ] **Step 1: Write it; `chmod +x release.sh && bash -n release.sh`** → clean.
- [ ] **Step 2: Dry verification** — `./release.sh status` on this repo (no tags yet) → both channels `(unset)`, exits 0.
- [ ] **Step 3: Commit** — `git add release.sh && git commit --no-gpg-sign -m "Add release.sh: cut betas, promote to stable"`

---

### Task 6: README

**Files:**
- Modify: `README.md` (Updating section, Configuration table)

- [ ] **Step 1: Replace the Updating section** content (keep the heading) with channel-based text: what shout/mumble are, the Channel submenu, the check/apply flow (now `git fetch --tags` → `reset --hard <channel tag>` → `uv sync` → relaunch), the same refusal guards, the `Switch to vX.Y.Z` downgrade behavior on channel switch, and recovery if `uv sync` fails. Add a **Cutting a release** subsection documenting `./release.sh`, `./release.sh promote`, `./release.sh status`, release notes via `--generate-notes`, and the rule that promotion ships exactly the beta-tested commit.
- [ ] **Step 2: Configuration table** — replace the `UPDATE_REMOTE / UPDATE_BRANCH` row notes with "Remote + required local branch for updates; channels are the `UPDATE_CHANNELS` floating tags." Update the config.json keys sentence to include `update_channel`.
- [ ] **Step 3: Commit** — `git add README.md && git commit --no-gpg-sign -m "Document release channels and release.sh"`

---

### Task 7: Verify, push, PR (no merge — Sean merges)

- [ ] **Step 1:** `uv run pytest -q` → all pass (51 prior + 7 new = 58).
- [ ] **Step 2:** Smoke: `./service.sh restart` on this Mac from main afterwards is NOT needed (feature unmerged); instead run `uv run python -c "import blurt; print(blurt.check_for_updates('shout', repo=blurt.REPO_ROOT).status)"` → `wrong_branch` (we're on the feature branch — proves the guard works against the real repo).
- [ ] **Step 3:** Push; `gh pr create` with summary + cutover instructions (run `./release.sh && ./release.sh promote` right after merging). Do not merge.
