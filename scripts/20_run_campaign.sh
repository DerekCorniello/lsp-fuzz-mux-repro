#!/usr/bin/env bash
# Run a controlled LSPFuzz campaign: full, baseline-2d, or a named ablation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Campaign tooling uses release binaries unless explicitly overridden.
export MUX_BUILD_PROFILE="${MUX_BUILD_PROFILE:-release}"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

MODE="${1:-full}"
case "$MODE" in
    full|baseline|no-context-awareness|no-server-feedback) ;;
    *)
        echo "usage: $0 [full|baseline|no-context-awareness|no-server-feedback]" >&2
        exit 2
        ;;
esac

if [[ ! -x "$LSP_FUZZ_BIN" ]]; then
    echo "error: LSPFuzz is not built; run scripts/01_build_lspfuzz.sh" >&2
    exit 1
fi
if [[ ! -x "$MUX_AFL_BIN" ]]; then
    echo "error: instrumented target is not built; run scripts/02_build_target.sh" >&2
    exit 1
fi
if [[ "$MODE" != "baseline" && ! -f "$FRAGMENT_FILE" ]]; then
    echo "error: fragment database is missing; run scripts/03_collect_fragments.sh" >&2
    exit 1
fi

campaign_root="$RESULTS_DIR/$MODE"
mkdir -p "$campaign_root"

common_args=(
    --lsp-executable "$MUX_AFL_BIN"
    --target-args lsp
    --exec-timeout "$CAMPAIGN_EXEC_TIMEOUT_MS"
    --generate-seeds "$CAMPAIGN_SEEDS"
    --no-asan
    --time-budget "$CAMPAIGN_HOURS"
)

for repetition in $(seq 1 "$CAMPAIGN_REPETITIONS"); do
    run_dir="$campaign_root/rep-$repetition"
    if [[ -e "$run_dir/run.json" ]]; then
        log "skipping $run_dir (already complete; remove it to rerun)"
        continue
    fi
    mkdir -p "$run_dir"
    seed=$((CAMPAIGN_SEED + repetition - 1))
    start_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    case "$MODE" in
        full)
            fuzz_args=(fuzz "${common_args[@]}" --language-fragments "Mux=$FRAGMENT_FILE")
            ;;
        baseline)
            fuzz_args=(baseline-2d "${common_args[@]}" --language Mux)
            ;;
        no-context-awareness)
            fuzz_args=(
                fuzz
                "${common_args[@]}"
                --language-fragments "Mux=$FRAGMENT_FILE"
                --ablation-mode no-context-awareness
            )
            ;;
        no-server-feedback)
            fuzz_args=(
                fuzz
                "${common_args[@]}"
                --language-fragments "Mux=$FRAGMENT_FILE"
                --ablation-mode no-server-feedback
            )
            ;;
    esac
    fuzz_args+=(--state "$run_dir/state")

    log "starting $MODE repetition $repetition/$CAMPAIGN_REPETITIONS (seed=$seed, budget=${CAMPAIGN_HOURS}h)"
    set +e
    if [[ -n "${CAMPAIGN_WRAP_SECONDS:-}" ]]; then
        timeout --signal=INT --kill-after=120 "$CAMPAIGN_WRAP_SECONDS" \
            "$LSP_FUZZ_BIN" \
            --random-seed "$seed" \
            --default-log-level info \
            "${fuzz_args[@]}" \
            >"$run_dir/fuzzer.log" 2>&1
    else
        "$LSP_FUZZ_BIN" \
            --random-seed "$seed" \
            --default-log-level info \
            "${fuzz_args[@]}" \
            >"$run_dir/fuzzer.log" 2>&1
    fi
    status=$?
    set -e

    if [[ "$status" -ne 0 && "$status" -ne 124 && "$status" -ne 130 ]]; then
        log "error: fuzzer exited early with status $status"
        tail -40 "$run_dir/fuzzer.log" >&2
        exit 1
    fi

    end_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    python3 - "$run_dir" "$MODE" "$repetition" "$seed" "$status" "$start_utc" "$end_utc" <<'PY'
import json
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
mode, repetition, seed, status, start_utc, end_utc = sys.argv[2:]
stats = run_dir / "state" / "stats"
last = None
if stats.exists() and stats.read_text().strip():
    last = stats.read_text().strip().splitlines()[-1]
run = {
    "mode": mode,
    "repetition": int(repetition),
    "seed": int(seed),
    "status": int(status),
    "start_utc": start_utc,
    "end_utc": end_utc,
    "last_stat": last,
    "stats_file": str(stats),
    "state_dir": str(run_dir / "state"),
}
(run_dir / "run.json").write_text(json.dumps(run, indent=2) + "\n")
PY

    log "finished $MODE repetition $repetition with status $status"
done

log "campaign complete: $campaign_root"
