# Uninstaller — Design

**Date:** 2026-06-12
**Status:** Approved

## Motivation

Removing blurt (e.g. to test `setup.sh` from scratch, or to retire a machine)
currently means hand-running five different cleanup steps from the README.
Make it one script, symmetric with `setup.sh`.

## Design

`./uninstall.sh` — default removes everything blurt put *outside* the repo:

1. `./service.sh uninstall` (boots out the LaunchAgent, removes the plist,
   handles legacy labels; tolerated if already gone).
2. `~/Applications/Blurt.app`.
3. `~/Library/Application Support/blurt/` (config).
4. `~/Library/Logs/blurt.{out,err}.log`.
5. Prints TCC guidance: grants attach to the uv-managed python binary and
   cannot be removed by scripts — print the resolved binary path and which
   panes to clean (Accessibility, Input Monitoring, Microphone).

`./uninstall.sh --full` — everything above, plus:

- Opens the Accessibility pane in System Settings (user removes the entries
  by hand; the other panes are named in the printed guidance).
- Removes the Parakeet weights cache
  (`~/.cache/huggingface/hub/models--mlx-community--parakeet-tdt-0.6b-v2`).
- Removes the checkout itself (`cd /` first; the `rm -rf` of the script's own
  directory is the final command).

Out of scope: removing uv / uv-managed pythons (shared with other projects),
Xcode CLT, and any scripted TCC mutation (macOS doesn't allow per-binary
resets for unbundled executables; a global `tccutil reset` would strip every
app's grants and is deliberately not offered).

## Testing

`bash -n`; default-path behaviors reviewed by inspection (each step is an
idempotent `rm -rf`/`rm -f` or an already-tested `service.sh` call). The real
end-to-end test is the planned from-scratch reinstall on this machine:
`./uninstall.sh --full` → manual TCC entry removal → fresh clone → `./setup.sh`.
Not run during development — it would tear down the live install mid-PR.
