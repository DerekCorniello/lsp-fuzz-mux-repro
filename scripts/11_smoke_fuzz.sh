#!/usr/bin/env bash
# Short LSPFuzz smoke campaign. Demonstrates target startup, Mux document
# generation, handler coverage, clean shutdown, and replayable failures.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

MODE="${1:-full}"
SMOKE_SECONDS="${SMOKE_SECONDS:-120}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
state_dir="$RESULTS_DIR/smoke/${MODE}-${stamp}"
mkdir -p "$state_dir"

if [[ ! -x "$LSP_FUZZ_BIN" ]]; then
    echo "error: LSPFuzz is not built; run scripts/01_build_lspfuzz.sh" >&2
    exit 1
fi
if [[ ! -x "$MUX_AFL_BIN" ]]; then
    echo "error: instrumented target is not built; run scripts/02_build_target.sh" >&2
    exit 1
fi
if [[ "$MODE" == "full" && ! -f "$FRAGMENT_FILE" ]]; then
    echo "error: fragment database is missing; run scripts/03_collect_fragments.sh" >&2
    exit 1
fi

common_args=(
    --state "$state_dir/state"
    --lsp-executable "$MUX_AFL_BIN"
    --target-args lsp
    --exec-timeout "$CAMPAIGN_EXEC_TIMEOUT_MS"
    --generate-seeds 16
    --no-asan
    --time-budget 1
)

case "$MODE" in
    full)
        fuzz_args=(
            fuzz
            "${common_args[@]}"
            --language-fragments "Mux=$FRAGMENT_FILE"
        )
        ;;
    baseline)
        fuzz_args=(
            baseline-2d
            "${common_args[@]}"
            --language Mux
        )
        ;;
    *)
        echo "usage: $0 [full|baseline]" >&2
        exit 2
        ;;
esac

log "running $MODE smoke for ${SMOKE_SECONDS}s"
set +e
timeout --signal=INT --kill-after=90 "$SMOKE_SECONDS" \
    "$LSP_FUZZ_BIN" \
    --random-seed "$CAMPAIGN_SEED" \
    --default-log-level info \
    "${fuzz_args[@]}" \
    >"$state_dir/fuzzer.log" 2>&1
fuzz_status=$?
set -e

if [[ "$fuzz_status" != "0" && "$fuzz_status" != "124" && "$fuzz_status" != "130" ]]; then
    echo "error: fuzzer exited with status $fuzz_status" >&2
    tail -40 "$state_dir/fuzzer.log" >&2
    exit 1
fi

stats_file="$state_dir/state/stats"
if [[ ! -s "$stats_file" ]]; then
    echo "error: no stats written at $stats_file" >&2
    exit 1
fi

last_stat="$(tail -1 "$stats_file")"
IFS=',' read -r corpus_count solutions_count elapsed executions edges <<<"$last_stat"
if [[ "${edges:-0}" -le 0 ]]; then
    echo "error: no Mux coverage recorded (last stat: $last_stat)" >&2
    exit 1
fi

log "last stat: corpus=$corpus_count solutions=$solutions_count elapsed=${elapsed}s executions=$executions edges=$edges"

export_dir="$state_dir/exported"
replay_report=""
replay_report_json="null"
if [[ "$MODE" == "baseline" ]]; then
    if [[ "$solutions_count" -gt 0 ]]; then
        replay_report="$state_dir/replay.json"
        replay_report_json="\"$replay_report\""
        log "exporting $solutions_count baseline solution input(s)"
        "$LSP_FUZZ_BIN" export-baseline \
            --solution-dir "$state_dir/state/solutions" \
            --output-dir "$export_dir" \
            --language Mux
        log "replaying exported baseline transcripts against ordinary target"
        python3 "$REPO_ROOT/targets/mux/replay.py" \
            --input "$export_dir" \
            --executable "$MUX_NORMAL_BIN" \
            --arg lsp \
            --jobs "${SMOKE_REPLAY_JOBS:-8}" \
            --output "$replay_report" || true
    else
        log "no baseline solutions recorded; skipping two-dimensional replay"
    fi
else
    if [[ "$solutions_count" -gt 0 ]]; then
        log "exporting $solutions_count solution input(s)"
        "$LSP_FUZZ_BIN" export \
            --input "$state_dir/state/solutions" \
            --output "$export_dir"
    else
        log "no solutions recorded; exporting corpus inputs for replay"
        mkdir -p "$export_dir"
        "$LSP_FUZZ_BIN" export \
            --input "$state_dir/state/corpus" \
            --output "$export_dir"
    fi

    replay_report="$state_dir/replay.json"
    replay_report_json="\"$replay_report\""
    if [[ -n "$(find "$export_dir" -mindepth 2 -maxdepth 2 -type d -name requests -print -quit 2>/dev/null)" ]]; then
        log "replaying exported inputs against ordinary target"
        python3 "$REPO_ROOT/targets/mux/replay.py" \
            --input "$export_dir" \
            --executable "$MUX_NORMAL_BIN" \
            --arg lsp \
            --jobs "${SMOKE_REPLAY_JOBS:-8}" \
            --output "$replay_report"
    else
        log "no exported inputs to replay"
    fi
fi

cat > "$state_dir/smoke-summary.json" <<EOF
{
  "mode": "$MODE",
  "seconds_requested": $SMOKE_SECONDS,
  "fuzzer_status": $fuzz_status,
  "corpus_count": $corpus_count,
  "solutions_count": $solutions_count,
  "elapsed_seconds": $elapsed,
  "executions": $executions,
  "edges_found": $edges,
  "stats_file": "$stats_file",
  "exported_dir": "$export_dir",
  "replay_report": $replay_report_json,
  "mux_compiler_rev": "$MUX_COMPILER_REV",
  "lsp_fuzz_rev": "$LSP_FUZZ_REV",
  "tree_sitter_mux_rev": "$TREE_SITTER_MUX_REV"
}
EOF

log "smoke summary: $state_dir/smoke-summary.json"
