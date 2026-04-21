#!/usr/bin/env bash
# One-shot installer for blurt, the push-to-talk dictation daemon.
#
# Steps:
#   1. Ensure Xcode Command Line Tools are installed (triggers GUI dialog).
#   2. Install uv (fast Python package manager) if missing.
#   3. Create a Python 3.12 venv and install deps via `uv sync`.
#   4. Print permission + run instructions.
#
# Re-runnable; each step is idempotent.

set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

say() { printf '\033[1;36m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install]\033[0m %s\n' "$*"; }

# --- 1. Xcode Command Line Tools -----------------------------------------
if ! xcode-select -p >/dev/null 2>&1; then
    say "Xcode Command Line Tools missing — opening install dialog."
    say "Click 'Install' in the dialog. This takes ~5–10 minutes."
    xcode-select --install || true
    say "Waiting for Xcode CLT to finish installing..."
    until xcode-select -p >/dev/null 2>&1; do
        sleep 10
        printf '.'
    done
    printf '\n'
    say "Xcode CLT installed."
else
    say "Xcode CLT present at $(xcode-select -p)."
fi

# --- 2. uv ---------------------------------------------------------------
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    say "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
say "uv: $(uv --version)"

# --- 3. Venv + deps ------------------------------------------------------
say "Creating Python 3.12 venv and installing dependencies..."
uv venv --python 3.12 --allow-existing
uv sync

# --- 4. Done -------------------------------------------------------------
cat <<'EOF'

────────────────────────────────────────────────────────────────────────
✅ Install complete.

To run the daemon:
    uv run python blurt.py

Or install as a LaunchAgent that auto-starts at login:
    ./service.sh install

First-run permissions (macOS will prompt; System Settings → Privacy):
  • Microphone         — required to capture audio
  • Accessibility      — required to detect Right Option + synthesize ⌘V
  • Input Monitoring   — required to observe global key events

The permission prompts target the *python binary in the venv*
(./.venv/bin/python). Grant all three to that binary via drag-and-drop
onto the list area of each pane. See README.md for details.

Menu bar icons:
    🎙  idle / ready
    🔴  recording
    ✨  transcribing
    ⚠️  error (see terminal)

Hold Right Option, speak, release. Text pastes at your cursor.
────────────────────────────────────────────────────────────────────────
EOF
