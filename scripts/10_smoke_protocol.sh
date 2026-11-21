#!/usr/bin/env bash
# Direct LSP protocol smoke test and coverage delta probe.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

mkdir -p "$RESULTS_DIR/smoke"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
report="$RESULTS_DIR/smoke/protocol-$timestamp.json"
coverage_report="$RESULTS_DIR/smoke/coverage-$timestamp.json"

binary="${MUX_SMOKE_BIN:-$MUX_NORMAL_BIN}"
if [[ ! -x "$binary" ]]; then
    echo "error: target not built: $binary (run scripts/02_build_target.sh)" >&2
    exit 1
fi

log "direct protocol smoke against $binary"
python3 "$REPO_ROOT/targets/mux/lsp_smoke.py" \
    --executable "$binary" \
    --arg lsp \
    --output "$report"

if [[ -x "$MUX_AFL_BIN" ]]; then
    log "coverage delta probe against $MUX_AFL_BIN"
    python3 "$SCRIPT_DIR/coverage_probe.py" \
        --showmap "$AFL_DIR/afl-showmap" \
        --executable "$MUX_AFL_BIN" \
        --arg lsp \
        --output "$coverage_report"
else
    log "instrumented target not built; skipping coverage delta probe"
fi

log "protocol report: $report"
log "coverage report: $coverage_report"
