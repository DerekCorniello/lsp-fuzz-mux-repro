#!/usr/bin/env bash
# Collect Mux source fragments from compiler tests and examples, then mine a
# derivation-fragment database for the LSPFuzz context-aware mutator.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

if [[ ! -x "$LSP_FUZZ_BIN" ]]; then
    echo "error: LSPFuzz is not built; run scripts/01_build_lspfuzz.sh" >&2
    exit 1
fi

if [[ ! -d "$MUX_SOURCE_DIR/test_scripts" ]]; then
    echo "error: missing mux-compiler checkout; run scripts/02_build_target.sh" >&2
    exit 1
fi

mkdir -p "$MUX_SOURCES_DIR/compiler-tests" "$MUX_SOURCES_DIR/examples"

log "copying compiler test sources from $MUX_COMPILER_REV"
rsync -a --delete "$MUX_SOURCE_DIR/test_scripts/" "$MUX_SOURCES_DIR/compiler-tests/"

examples_dir="$BUILD_DIR/mux-examples"
if [[ ! -d "$examples_dir/.git" ]]; then
    log "cloning mux-examples $MUX_EXAMPLES_REV"
    git clone "$MUX_EXAMPLES_REPO" "$examples_dir"
fi
git -C "$examples_dir" fetch --depth 1 origin "$MUX_EXAMPLES_REV"
git -C "$examples_dir" checkout --detach "$MUX_EXAMPLES_REV"

log "copying example sources from $MUX_EXAMPLES_REV"
rsync -a --delete "$examples_dir/examples/" "$MUX_SOURCES_DIR/examples/"

cat > "$MUX_SOURCES_DIR/PROVENANCE.json" <<EOF
{
  "mux_compiler_repo": "$MUX_COMPILER_REPO",
  "mux_compiler_rev": "$MUX_COMPILER_REV",
  "mux_examples_repo": "$MUX_EXAMPLES_REPO",
  "mux_examples_rev": "$MUX_EXAMPLES_REV",
  "tree_sitter_mux_rev": "$TREE_SITTER_MUX_REV",
  "source_files": $(find "$MUX_SOURCES_DIR" -name '*.mux' | wc -l)
}
EOF

log "mining Mux grammar fragments"
"$LSP_FUZZ_BIN" \
    --default-log-level info \
    mine-grammar-fragments \
    --search-directory "$MUX_SOURCES_DIR" \
    --language Mux \
    --output "$FRAGMENT_FILE"

log "fragment database: $FRAGMENT_FILE ($(stat -c '%s' "$FRAGMENT_FILE") bytes)"
