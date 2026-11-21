#!/usr/bin/env bash
# Export solution inputs from a campaign and replay them against the ordinary
# Mux build. Writes replay.json per repetition.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

MODE="${1:-full}"
campaign_root="$RESULTS_DIR/$MODE"

if [[ ! -d "$campaign_root" ]]; then
    echo "error: no campaign results at $campaign_root" >&2
    exit 1
fi
if [[ ! -x "$MUX_NORMAL_BIN" ]]; then
    echo "error: ordinary target is not built; run scripts/02_build_target.sh" >&2
    exit 1
fi
if [[ ! -x "$LSP_FUZZ_BIN" ]]; then
    echo "error: LSPFuzz is not built; run scripts/01_build_lspfuzz.sh" >&2
    exit 1
fi

for run_dir in "$campaign_root"/rep-*; do
    [[ -d "$run_dir" ]] || continue
    solutions="$run_dir/state/solutions"
    if [[ ! -d "$solutions" ]] || [[ -z "$(find "$solutions" -maxdepth 1 -type f -print -quit)" ]]; then
        log "no solutions in $run_dir"
        continue
    fi
    if [[ "$MODE" == "baseline" ]]; then
        exported="$run_dir/exported"
        rm -rf "$exported"
        log "exporting baseline solutions from $solutions"
        "$LSP_FUZZ_BIN" export-baseline \
            --solution-dir "$solutions" \
            --output-dir "$exported" \
            --language Mux
        log "replaying $exported against $MUX_NORMAL_BIN"
        python3 "$REPO_ROOT/targets/mux/replay.py" \
            --input "$exported" \
            --executable "$MUX_NORMAL_BIN" \
            --arg lsp \
            --jobs "${REPLAY_JOBS:-8}" \
            --output "$run_dir/replay.json" || true
        continue
    fi
    exported="$run_dir/exported"
    rm -rf "$exported"
    mkdir -p "$exported"
    log "exporting $solutions"
    "$LSP_FUZZ_BIN" export --input "$solutions" --output "$exported"
    log "replaying $exported against $MUX_NORMAL_BIN"
    python3 "$REPO_ROOT/targets/mux/replay.py" \
        --input "$exported" \
        --executable "$MUX_NORMAL_BIN" \
        --arg lsp \
        --jobs "${REPLAY_JOBS:-8}" \
        --output "$run_dir/replay.json" || true
done

log "replay reports written under $campaign_root"
