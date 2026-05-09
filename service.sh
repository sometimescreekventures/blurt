#!/usr/bin/env bash
# Control script for the blurt LaunchAgent.
#
#   ./service.sh install    render plist + bootstrap via launchctl
#   ./service.sh start      load + start the agent
#   ./service.sh stop       stop + unload the agent
#   ./service.sh restart    stop then start (with a small settle delay)
#   ./service.sh status     show current state
#   ./service.sh logs       tail stdout and stderr
#   ./service.sh uninstall  stop + remove the installed plist
#
# The source of truth is `blurt.plist.template`. `install` substitutes
# __HOME__ and __REPO__ into a plist at ~/Library/LaunchAgents/.

set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
label="local.blurt"
legacy_labels=("local.dictate" "com.seandenton.dictate")  # for migration
template="$here/blurt.plist.template"
dst_plist="$HOME/Library/LaunchAgents/$label.plist"
uid="$(id -u)"
domain="gui/$uid"

die() { echo "error: $*" >&2; exit 1; }

bootout_legacy() {
    for legacy in "${legacy_labels[@]}"; do
        launchctl bootout "$domain/$legacy" 2>/dev/null || true
        rm -f "$HOME/Library/LaunchAgents/$legacy.plist"
    done
}

render_plist() {
    [[ -f "$template" ]] || die "missing template: $template"
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
    sed -e "s|__HOME__|$HOME|g" -e "s|__REPO__|$here|g" "$template" > "$dst_plist"
}

case "${1:-}" in
    install)
        bootout_legacy
        render_plist
        launchctl bootout "$domain" "$dst_plist" 2>/dev/null || true
        launchctl bootstrap "$domain" "$dst_plist"
        launchctl enable "$domain/$label"
        cat <<EOF
installed as $label.

Next: ./permissions.sh   (walks you through granting Accessibility + Input Monitoring)
Then:  ./service.sh restart
EOF
        ;;
    start)
        [[ -f "$dst_plist" ]] || die "not installed; run: $0 install"
        launchctl bootstrap "$domain" "$dst_plist" 2>/dev/null || true
        launchctl kickstart -k "$domain/$label"
        ;;
    stop)
        launchctl bootout "$domain/$label" 2>/dev/null || true
        ;;
    restart)
        "$0" stop
        sleep 1
        "$0" start
        ;;
    status)
        launchctl print "$domain/$label" 2>/dev/null | head -25 || echo "not loaded"
        ;;
    logs)
        echo "--- stdout ($HOME/Library/Logs/blurt.out.log) ---"
        tail -n 40 "$HOME/Library/Logs/blurt.out.log" 2>/dev/null || echo "(empty)"
        echo ""
        echo "--- stderr ($HOME/Library/Logs/blurt.err.log) ---"
        tail -n 40 "$HOME/Library/Logs/blurt.err.log" 2>/dev/null || echo "(empty)"
        ;;
    uninstall)
        launchctl bootout "$domain/$label" 2>/dev/null || true
        bootout_legacy
        rm -f "$dst_plist"
        echo "uninstalled."
        ;;
    *)
        sed -n '2,13p' "$0"
        exit 1
        ;;
esac
