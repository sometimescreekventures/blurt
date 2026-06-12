#!/usr/bin/env bash
# Uninstall blurt from this machine — the mirror image of setup.sh.
#
#   ./uninstall.sh          remove the LaunchAgent, Blurt.app, config, logs
#   ./uninstall.sh --full   also remove the model weights cache and this
#                           checkout itself, and open System Settings so you
#                           can remove the TCC permission entries
#
# TCC grants (Accessibility / Input Monitoring / Microphone) attach to the
# uv-managed python binary and cannot be removed by scripts — both modes
# print the exact entry to remove by hand. uv itself and its pythons are
# left alone (shared with other projects).

set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
full=0
case "${1:-}" in
    --full) full=1 ;;
    "") ;;
    *) sed -n '2,12p' "$0"; exit 1 ;;
esac

say() { printf '\033[1;35m[uninstall]\033[0m %s\n' "$*"; }

# 1. LaunchAgent (also stops the daemon; handles legacy labels; ok if absent)
if [[ -x "$here/service.sh" ]]; then
    "$here/service.sh" uninstall || true
fi

# 2. App bundle
rm -rf "$HOME/Applications/Blurt.app"
say "removed ~/Applications/Blurt.app"

# 3. Config + logs
rm -rf "$HOME/Library/Application Support/blurt"
rm -f "$HOME/Library/Logs/blurt.out.log" "$HOME/Library/Logs/blurt.err.log"
say "removed config and logs"

# 4. TCC guidance — resolve the binary while the venv still exists
venv_python="$here/.venv/bin/python"
real_python="$(readlink -f "$venv_python" 2>/dev/null || echo "python3.12 (venv already gone — look for python3.12 entries)")"
cat <<EOF

macOS doesn't let scripts remove TCC grants. To finish, open
System Settings → Privacy & Security and remove (select, then "–")
the python entry under each of:
  • Accessibility
  • Input Monitoring
  • Microphone
The entry to remove is:
  $real_python

EOF

if [[ $full -eq 0 ]]; then
    say "done. (--full also removes the model cache and this checkout)"
    exit 0
fi

open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" || true

rm -rf "$HOME/.cache/huggingface/hub/models--mlx-community--parakeet-tdt-0.6b-v2"
say "removed Parakeet weights cache"

say "removing checkout $here — done."
cd /
rm -rf "$here"
