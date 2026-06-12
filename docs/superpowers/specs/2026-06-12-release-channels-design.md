# Release Channels (shout / mumble) — Design

**Date:** 2026-06-12
**Status:** Approved

## Motivation

The menu-bar updater tracks `origin/main`, so merging a PR instantly ships it
to every Mac that clicks update. We want deliberate, staged releases: cut a
beta, soak it, then promote the exact same bits to stable. Releases get real
version numbers and GitHub Release notes — a productized update pipeline.

## The model

Every release is an **immutable semver tag** (`v0.2.0`, continuing from the
0.1.0 lineage) plus a **GitHub Release** with auto-generated notes. Two
**floating channel tags** mark what each channel should run:

- 🗣️ **`shout`** — stable. The default channel.
- 🤫 **`mumble`** — beta. Early, might be slurred.

Cutting a release moves `mumble`. Promotion moves `shout` to the commit
`mumble` already points at — promotion can never ship bits that weren't
beta-tested, and needs no rebuild or new commit.

The updater changes from "compare HEAD to `origin/main`" to "compare HEAD to
my channel's tag." Everything else about it (dirty/branch guards, `uv sync`,
exit-nonzero relaunch, meeting deferral) stays.

## Scope

In scope:

- `release.sh`: `cut` (default), `promote`, `status` subcommands.
- `check_for_updates` / `apply_update` retargeted to channel tags;
  cross-channel "updates" (including downgrades mumble→shout) supported.
- `update_channel` config key + **Channel** menu submenu (Shout / Mumble);
  switching channels fires an immediate update check.
- Version line shows the release tag and channel (e.g. `Version: v0.2.0 · shout`),
  falling back to the short SHA for untagged dev checkouts.
- README: rewrite the Updating section; document the release workflow.
- Cutover plan for machines still running the main-tracking updater.

Out of scope (deliberate):

- Bumping `pyproject.toml`'s version (unused; tags are the version source of truth).
- More than two channels, channel auth/secrets, or per-machine pinning.
- Release artifacts/binaries — releases are git tags; machines still pull via git.
- Automatic rollback. `release.sh` can re-point a channel tag at an older
  version manually if ever needed (documented, not scripted).

## Components

### release.sh

Requires `gh` (authenticated) and a clean checkout on `main` matching
`origin/main` (fetches first; errors otherwise — never tags unpushed work).

- `./release.sh [--minor|--major]` — **cut a beta**:
  1. Compute next version from the highest existing `v*` tag
     (`git tag --list 'v*' --sort=-v:refname`), starting from `v0.1.0` if none.
     Patch bump by default; `--minor` / `--major` reset lower fields.
  2. `git tag -a vX.Y.Z -m "blurt vX.Y.Z"` at HEAD; push the tag.
  3. `gh release create vX.Y.Z --title "blurt vX.Y.Z" --generate-notes --prerelease`
     (GitHub builds the notes from merged PRs since the previous release).
  4. Move the channel pointer: `git tag -f mumble vX.Y.Z`;
     `git push --force origin mumble`.
  5. Print: `🤫 mumble → vX.Y.Z (beta cut; promote with ./release.sh promote)`.
- `./release.sh promote` — **graduate beta to stable**:
  1. Resolve the version tag `mumble` points at (error if `mumble` missing or
     already equal to `shout` — nothing to promote).
  2. `git tag -f shout <that version>`; `git push --force origin shout`.
  3. `gh release edit vX.Y.Z --prerelease=false --latest`.
  4. Print: `🗣️ shout → vX.Y.Z`.
- `./release.sh status` — show both pointers, their versions, and how many
  commits `main` is ahead of `mumble` (i.e. unreleased work).

### Updater changes (blurt.py)

- Constants: `UPDATE_REMOTE` stays; `UPDATE_BRANCH` (the required local
  branch) stays `main`. New `UPDATE_CHANNELS = ("shout", "mumble")` and
  `DEFAULT_CONFIG["update_channel"] = "shout"`.
- `load_config`: validate `update_channel` against `UPDATE_CHANNELS`;
  unknown values warn and fall back to the default (same pattern as hotkeys).
- `UpdateCheck` gains `version: str = ""` (the `vX.Y.Z` name at the channel
  commit, empty if none) and `channel: str = ""`.
- `check_for_updates(channel, repo=None)`:
  1. Branch + dirty guards unchanged.
  2. `git fetch origin --tags --force` (floating tags move; `--force` lets
     the moved tag update locally).
  3. Resolve `<channel>^{commit}` — missing tag →
     `check_failed` with `"channel tag '<channel>' not found — cut a release first"`.
  4. HEAD commit == channel commit → `up_to_date`. Otherwise
     `update_available` with `commits_behind = rev-list --count HEAD..<channel>`
     (0 for a downgrade/cross-history move — still an update) and `version`
     from the highest `v*` tag pointing at the channel commit.
- `apply_update(channel)`: same guards, fetch tags, `git reset --hard` to the
  **resolved channel commit SHA** (not the tag name — immune to the tag moving
  mid-update), `uv sync` via `_uv_binary()`, `restart_daemon()`. Statuses unchanged.
- `current_version()`: returns `(label, date)` where label is the highest
  `v*` tag pointing at HEAD, falling back to the short SHA. Still process-cached.
- Menu label for an available update: `Update to v0.3.0 (2 commits behind)`;
  when `commits_behind == 0` (downgrade/cross-channel): `Switch to v0.2.0`.

### Channel submenu (MenuApp)

- New **Channel** submenu after the Clipboard Hotkey submenu:
  `🗣️ Shout (stable)` and `🤫 Mumble (beta)`, checkmark on the active one.
- `MenuApp.__init__` takes the loaded config's channel; `_current_config()`
  includes `update_channel`.
- Picking a channel: save config, update the version line (re-renders
  `Version: <label> · <channel>`), and fire `_on_check_updates` so the user
  immediately sees what that channel offers.
- All check/apply paths pass the active channel.

### README

- Rewrite the Updating section: channels, what shout/mumble mean, the
  Channel submenu, downgrade-on-switch behavior.
- New "Cutting a release" subsection: `release.sh` cut/promote/status flow.
- Cutover note (below).

## Cutover

Machines running the old main-tracking updater will, on their next update
click, pull main's tip (which includes this feature) and restart into the
channel world — from then on they follow `shout`. Therefore, right after
merging: run `./release.sh && ./release.sh promote` so `shout`/`mumble`
exist before any Mac checks; a missing channel tag reads as
`Update unavailable: channel tag … not found` rather than an error spew.

## Edge cases

- **Channel tag missing** (pre-first-release, or someone deleted it):
  `check_failed` with the explicit cut-a-release message; menu shows it.
- **HEAD ahead of channel** (dev machine that ran from main tip): treated as
  `update_available` with behind=0 → `Switch to vX.Y.Z`. Clicking it
  deliberately moves the machine onto the release train. The dev machine can
  simply not click it.
- **Channel switch mid-"Updating…"**: the in-flight lock already serializes;
  apply resolves its SHA once at start.
- **Promote with nothing new**: `release.sh promote` errors with "shout is
  already at vX.Y.Z".
- **`--generate-notes` with no PRs** (direct commits): GitHub falls back to
  the commit list; still fine.
- **Old `UPDATE_BRANCH` guard**: kept — updating still requires the checkout
  to be on `main`, preserving the existing protection for feature branches.

## Testing

Unit (extend `tests/test_self_update.py`; fixture gains tags + helper to move
channel tags in the bare remote):

- `up_to_date` when HEAD == channel commit.
- `update_available` (+ correct `commits_behind`, `version`) when the channel
  tag moves ahead; channels resolve independently (mumble ≠ shout).
- Downgrade: HEAD ahead of channel → `update_available`, behind == 0.
- Missing channel tag → `check_failed` with the cut-a-release message.
- Dirty and wrong-branch guards unchanged (existing tests adapted to pass a channel).
- `load_config` channel validation (valid, unknown→default).
- Version-at-commit helper picks the highest `v*` tag.

`release.sh`: `bash -n` + manual checklist (real `gh` calls can't run in CI):
cut on this repo after merge, verify tag/release/prerelease state, promote,
verify `--latest` flip and `shout` move, `status` output sane.

Manual end-to-end: after cutover, on a second Mac click update → lands on
`shout` version; switch channel to Mumble → offered the beta; switch back →
offered the stable downgrade.
