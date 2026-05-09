#!/usr/bin/env bash
# Walk the user through granting macOS TCC permissions to the Python
# binary that runs blurt.
#
#   ./permissions.sh
#
# TCC can't be scripted, so this just opens the right Finder window
# and System Settings panes — the user drags the highlighted Python
# binary onto each pane and toggles it on.

set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
venv_python="$here/.venv/bin/python"

[[ -e "$venv_python" ]] || {
    echo "error: $venv_python does not exist." >&2
    echo "Run ./install.sh first to create the virtualenv." >&2
    exit 1
}

# macOS TCC resolves through symlinks; grant the real target.
real_python="$(readlink -f "$venv_python" 2>/dev/null || echo "$venv_python")"

cat <<EOF
Granting macOS permissions to:
  $real_python

You need to grant two permissions before blurt will work:

  1. Accessibility     (paste text via the keyboard)
  2. Input Monitoring  (listen for the global hotkey)

Microphone is granted automatically via an OS popup the first time
you record, so there's no manual step for it.

macOS doesn't let scripts grant these. This script opens Finder with
the Python binary preselected, then walks you through each settings
pane — drag the highlighted file onto the list area, toggle it on.

EOF

read -rp "Press Enter to begin. " _

echo "Revealing python in Finder..."
open -R "$real_python"
sleep 1

prompt_pane() {
    local label="$1" url="$2"
    cat <<EOF

==> $label
    Drag the highlighted python3.12 from the Finder window onto
    the list area in this pane, then toggle the switch on.

EOF
    open "$url"
    read -rp "Press Enter when done. " _
}

prompt_pane "Accessibility" \
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"

prompt_pane "Input Monitoring" \
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"

cat <<EOF

Done. Run:
  ./service.sh restart
to pick up the new permissions, then hold Right Option and speak to test.
EOF
