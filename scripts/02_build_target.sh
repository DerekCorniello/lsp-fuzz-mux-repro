#!/usr/bin/env bash
# Clone the pinned Mux compiler into build/, apply the isolated AFL harness
# patch, and build both the instrumented and ordinary `mux` binaries.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

PATCH_FILE="$REPO_ROOT/targets/mux/afl-shmem.patch"
PROFILE="$MUX_BUILD_PROFILE"

if [[ ! -d "$MUX_SOURCE_DIR/.git" ]]; then
    log "cloning mux-compiler $MUX_COMPILER_REV"
    git clone "$MUX_COMPILER_REPO" "$MUX_SOURCE_DIR"
else
    log "using existing mux-compiler checkout at $MUX_SOURCE_DIR"
fi

current_rev="$(git -C "$MUX_SOURCE_DIR" rev-parse HEAD)"
if [[ "$current_rev" != "$MUX_COMPILER_REV" ]]; then
    log "checking out mux-compiler $MUX_COMPILER_REV (was $current_rev)"
    git -C "$MUX_SOURCE_DIR" fetch --depth 1 origin "$MUX_COMPILER_REV"
    git -C "$MUX_SOURCE_DIR" checkout --detach "$MUX_COMPILER_REV"
fi

if git -C "$MUX_SOURCE_DIR" apply --reverse --check "$PATCH_FILE" >/dev/null 2>&1; then
    log "AFL harness patch already applied"
elif git -C "$MUX_SOURCE_DIR" apply --check "$PATCH_FILE" >/dev/null 2>&1; then
    log "applying AFL harness patch"
    git -C "$MUX_SOURCE_DIR" apply "$PATCH_FILE"
else
    echo "error: cannot apply or detect $PATCH_FILE" >&2
    git -C "$MUX_SOURCE_DIR" status --short >&2
    exit 1
fi

build_args=(-p mux-lang --features afl-shmem-input)
if [[ "$PROFILE" == "release" ]]; then
    build_args+=(--release)
fi

run_cargo() {
    local target_dir="$1"
    shift
    local had_config=0
    if [[ -f "$MUX_SOURCE_DIR/.cargo/config.toml" ]]; then
        had_config=1
    fi
    if (
        cd "$MUX_SOURCE_DIR"
        CARGO_TARGET_DIR="$target_dir" "$@"
    ); then
        return 0
    fi
    if [[ "$had_config" == 0 && -f "$MUX_SOURCE_DIR/.cargo/config.toml" ]]; then
        log "Mux generated .cargo/config.toml on first build; retrying once"
        (
            cd "$MUX_SOURCE_DIR"
            CARGO_TARGET_DIR="$target_dir" "$@"
        )
        return
    fi
    return 1
}

log "building instrumented Mux target ($PROFILE)"
run_cargo "$MUX_AFL_TARGET_DIR" cargo afl build "${build_args[@]}"

normal_args=(-p mux-lang)
if [[ "$PROFILE" == "release" ]]; then
    normal_args+=(--release)
fi

log "building ordinary Mux target ($PROFILE)"
run_cargo "$MUX_NORMAL_TARGET_DIR" cargo build "${normal_args[@]}"

for binary in "$MUX_AFL_BIN" "$MUX_NORMAL_BIN"; do
    if [[ ! -x "$binary" ]]; then
        echo "error: expected binary at $binary" >&2
        exit 1
    fi
done

log "instrumented target: $MUX_AFL_BIN"
log "ordinary target:     $MUX_NORMAL_BIN"

"$AFL_DIR/afl-showmap" -q -o /dev/null -- "$MUX_AFL_BIN" lsp </dev/null >/dev/null 2>&1 || true

cat > "$BUILD_DIR/target-metadata.json" <<EOF
{
  "mux_compiler_repo": "$MUX_COMPILER_REPO",
  "mux_compiler_rev": "$MUX_COMPILER_REV",
  "mux_runtime_rev": "$MUX_RUNTIME_REV",
  "tree_sitter_mux_rev": "$TREE_SITTER_MUX_REV",
  "lsp_fuzz_rev": "$LSP_FUZZ_REV",
  "aflpp_rev": "$AFLPP_REV",
  "cargo_afl_version": "$CARGO_AFL_VERSION",
  "llvm_version": "$($LLVM_CONFIG --version)",
  "rust_stable": "$(rustc --version)",
  "rust_nightly": "$(rustc "+$RUST_NIGHTLY_TOOLCHAIN" --version)",
  "profile": "$PROFILE",
  "instrumented_binary": "$MUX_AFL_BIN",
  "instrumented_sha256": "$(sha256sum "$MUX_AFL_BIN" | awk '{print $1}')",
  "ordinary_binary": "$MUX_NORMAL_BIN",
  "ordinary_sha256": "$(sha256sum "$MUX_NORMAL_BIN" | awk '{print $1}')",
  "harness_patch_sha256": "$(sha256sum "$PATCH_FILE" | awk '{print $1}')"
}
EOF

log "wrote $BUILD_DIR/target-metadata.json"
