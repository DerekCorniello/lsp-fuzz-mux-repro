#!/usr/bin/env bash
# Build the local AFL++ and cargo-afl toolchain.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

log "AFL++ revision: $AFLPP_REV"
log "cargo-afl version: $CARGO_AFL_VERSION"
log "LLVM: $($LLVM_CONFIG --version)"

if ! command -v "$LLVM_CONFIG" >/dev/null 2>&1; then
    echo "error: $LLVM_CONFIG not found; install LLVM 22 development packages" >&2
    exit 1
fi

mkdir -p "$TOOLS_DIR"

if [[ ! -d "$AFL_DIR/.git" ]]; then
    log "cloning AFL++"
    git clone "$AFLPP_REPO" "$AFL_DIR"
fi

log "checking out AFL++ $AFLPP_REV"
git -C "$AFL_DIR" fetch --depth 1 origin "$AFLPP_REV"
git -C "$AFL_DIR" checkout --detach "$AFLPP_REV"

log "building AFL++ (source-only, LLVM mode)"
make -C "$AFL_DIR" source-only LLVM_CONFIG="$LLVM_CONFIG" -j"$(nproc)"

installed_version="$(cargo afl --version 2>/dev/null | awk '{print $2}' || true)"
if [[ "$installed_version" != "$CARGO_AFL_VERSION" ]]; then
    log "installing cargo-afl $CARGO_AFL_VERSION"
    cargo install --locked "cargo-afl@$CARGO_AFL_VERSION"
else
    log "cargo-afl $CARGO_AFL_VERSION already installed"
fi

log "building cargo-afl LLVM runtime for the active Rust toolchain"
cargo afl config --build

log "toolchain ready"
"$AFL_DIR/afl-fuzz" --version | head -1
cargo afl --version
