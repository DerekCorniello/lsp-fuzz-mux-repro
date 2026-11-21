#!/usr/bin/env bash
# Check that the local setup is complete and the Mux target responds.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

failures=0

check_file() {
    if [[ -f "$1" ]]; then
        log "ok: $1"
    else
        log "missing: $1"
        failures=$((failures + 1))
    fi
}

check_exec() {
    if [[ -x "$1" ]]; then
        log "ok: $1"
    else
        log "missing executable: $1"
        failures=$((failures + 1))
    fi
}

check_exec "$LSP_FUZZ_BIN"
check_exec "$MUX_AFL_BIN"
check_exec "$MUX_NORMAL_BIN"
check_file "$FRAGMENT_FILE"

if "$LSP_FUZZ_BIN" export-baseline --help >/dev/null 2>&1; then
    log "ok: lsp-fuzz-cli export-baseline"
else
    log "missing: lsp-fuzz-cli export-baseline"
    failures=$((failures + 1))
fi

if [[ "$failures" -gt 0 ]]; then
    echo "error: $failures setup check(s) failed" >&2
    exit 1
fi

if [[ "${VERIFY_SMOKE:-1}" == "1" ]]; then
    python3 "$REPO_ROOT/targets/mux/lsp_smoke.py" \
        --executable "$MUX_NORMAL_BIN" \
        --arg lsp \
        --timeout 30 \
        --output "$RESULTS_DIR/setup-protocol-smoke.json" >/dev/null
    log "direct protocol smoke passed"
fi

log "setup verification passed"
