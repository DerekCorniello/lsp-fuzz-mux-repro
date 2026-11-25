#!/usr/bin/env bash
set -euo pipefail
cd /home/derekcorn/code/advswe/lsp-fuzz-mux-repro
root="$PWD/results/validation-parse-timeout-6h-rep3"
mkdir -p "$root/capture"
export RESULTS_DIR="$root"
export LSPFUZZ_PARSE_CAPTURE_DIR="$root/capture"
export CAMPAIGN_HOURS=6
export CAMPAIGN_REPETITIONS=1
export CAMPAIGN_SEED=2
export CAMPAIGN_WRAP_SECONDS=22200
exec >"$root/campaign-6h.log" 2>&1
exec scripts/20_run_campaign.sh full
