#!/usr/bin/env bash
# Shared environment for all reproduction scripts. Source this file.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO_ROOT

# shellcheck source=../config/pins.env
source "$REPO_ROOT/config/pins.env"

export UPSTREAM_DIR="$REPO_ROOT/upstream-lsp-fuzz"
export LSP_FUZZ_BIN="$UPSTREAM_DIR/target/release/lsp-fuzz-cli"
export TOOLS_DIR="$REPO_ROOT/.tools"
export AFL_DIR="${AFL_DIR:-$TOOLS_DIR/AFLplusplus}"
export BUILD_DIR="${BUILD_DIR:-$REPO_ROOT/build}"
export RESULTS_DIR="${RESULTS_DIR:-$REPO_ROOT/results}"
export INPUTS_DIR="$REPO_ROOT/inputs"
export MUX_SOURCE_DIR="$BUILD_DIR/mux-compiler"
export MUX_AFL_TARGET_DIR="${MUX_AFL_TARGET_DIR:-$BUILD_DIR/mux-afl-target}"
export MUX_NORMAL_TARGET_DIR="${MUX_NORMAL_TARGET_DIR:-$BUILD_DIR/mux-normal-target}"
export MUX_BUILD_PROFILE="${MUX_BUILD_PROFILE:-debug}"
export MUX_AFL_BIN="$MUX_AFL_TARGET_DIR/$MUX_BUILD_PROFILE/mux"
export MUX_NORMAL_BIN="$MUX_NORMAL_TARGET_DIR/$MUX_BUILD_PROFILE/mux"
export FRAGMENT_FILE="${FRAGMENT_FILE:-$INPUTS_DIR/mux-fragments.cbor.zst}"
export MUX_SOURCES_DIR="${MUX_SOURCES_DIR:-$INPUTS_DIR/mux-sources}"

# Campaign defaults (six hours per repetition, three repetitions).
export CAMPAIGN_HOURS="${CAMPAIGN_HOURS:-6}"
export CAMPAIGN_REPETITIONS="${CAMPAIGN_REPETITIONS:-3}"
export CAMPAIGN_EXEC_TIMEOUT_MS="${CAMPAIGN_EXEC_TIMEOUT_MS:-1200}"
export CAMPAIGN_SEED="${CAMPAIGN_SEED:-0}"
export CAMPAIGN_SEEDS="${CAMPAIGN_SEEDS:-32}"

# AFL++ / cargo-afl environment.
export PATH="$AFL_DIR:$PATH"
export AFL_PATH="${AFL_PATH:-$AFL_DIR}"
export LLVM_CONFIG="${LLVM_CONFIG:-$LLVM_CONFIG_BIN}"
export LLVM_SYS_221_PREFIX="${LLVM_SYS_221_PREFIX:-/usr/lib/llvm22}"
export AFL_LLVM_INSTRUMENT="${AFL_LLVM_INSTRUMENT:-PCGUARD}"
export AFL_NO_UI="${AFL_NO_UI:-1}"

mkdir -p "$BUILD_DIR" "$RESULTS_DIR" "$INPUTS_DIR"

log() {
    printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}
