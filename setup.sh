#!/usr/bin/env bash
# One-command setup for a fresh Mac:
#
#   ./setup.sh
#
# Runs install.sh (Xcode CLT, uv, venv, deps), installs + starts the
# LaunchAgent (which also builds Blurt.app), then points you at the
# permission dialogs blurt fires on first launch. Idempotent — re-run it
# any time to repair an install.

set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

"$here/install.sh"
"$here/service.sh" install
"$here/service.sh" start

cat <<'EOF'

────────────────────────────────────────────────────────────────────────
✅ blurt is installed and running.

macOS will now show permission dialogs from blurt's python binary:
  • Accessibility       → approve / toggle on
  • Input Monitoring    → approve / toggle on
blurt restarts itself automatically once both are granted.
  • Microphone          → prompts the first time you dictate; click Allow

Dismissed a dialog? The entries are already registered — toggle them in
System Settings → Privacy & Security → Accessibility / Input Monitoring,
or run ./permissions.sh for a guided walkthrough.
────────────────────────────────────────────────────────────────────────
EOF
