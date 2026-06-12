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
