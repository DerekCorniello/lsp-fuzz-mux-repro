#!/usr/bin/env bash
# Build the vendored LSPFuzz checkout (Mux integration included).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

if [[ ! -d "$UPSTREAM_DIR" ]]; then
    echo "error: $UPSTREAM_DIR is missing; restore the vendored LSPFuzz tree" >&2
    exit 1
fi

log "building LSPFuzz with $RUST_NIGHTLY_TOOLCHAIN"
(
    cd "$UPSTREAM_DIR"
    if [[ -f Cargo.lock ]]; then
        cargo "+$RUST_NIGHTLY_TOOLCHAIN" build --release --locked
    else
        cargo "+$RUST_NIGHTLY_TOOLCHAIN" build --release
    fi
)

if [[ ! -x "$LSP_FUZZ_BIN" ]]; then
    echo "error: expected LSPFuzz binary at $LSP_FUZZ_BIN" >&2
    exit 1
fi

log "LSPFuzz built: $LSP_FUZZ_BIN"
"$LSP_FUZZ_BIN" --version
