# LSPFuzz on the Mux language server

Reproduction scaffolding for running the ASE 2025 LSPFuzz paper artifact
against `mux lsp`. The vendored fuzzer is pinned to the paper snapshot and
extended with a Mux language definition. The Mux compiler is built from a
separate checkout with a small AFL++ shared-memory input harness; the main Mux
checkout is not modified.

The reproduction has two goals:

1. Show that the existing Mux stdio JSON-RPC language server can be driven by
   the paper's context-aware LSP fuzzer with real AFL++ edge coverage.
2. Produce comparable LSPFuzz and two-dimensional-baseline measurements on the
   same machine, toolchain, target revision, and seed policy.

## Pinned revisions

| Component | Revision |
|---|---|
| LSPFuzz paper artifact | `a1d4c65ad6f0b2160fcfb774250295e2270b0b86` |
| Mux compiler | `e0f4d43bd6e51bb48d428bf8ca174415294d08e9` |
| Mux runtime (Cargo pin) | `44c164514e2b4b75db10c683dba8c73c58906603` |
| Mux examples | `34698ff5af8279a71148dd87388a74df66449ef7` |
| Mux Tree-sitter grammar | `e7a030806b2a1b996078a3101a4655debd4ac51a` |
| AFL++ | `d798677805de81b50aa8f45beb0342ae1617b9d8` |
| cargo-afl | `0.18.2` |
| LLVM | 22.1.8 (`/usr/bin/llvm-config-22`) |

All pins live in [`config/pins.env`](config/pins.env). Build outputs also record
the exact Rust compiler versions and binary hashes in
`build/target-metadata.json`.

## Mux integration

The vendored tree at [`upstream-lsp-fuzz/`](upstream-lsp-fuzz/) is the paper
snapshot plus these changes:

- `crates/tree-sitter-mux/` - the grammar JSON is copied to
  `crates/lsp-fuzz-grammars/res/grammar/mux.json`; `parser.c`,
  `tree_sitter/parser.h`, and `queries/highlights.scm` are copied from the
  pinned `tree-sitter-mux` revision. The new crate exposes
  `tree_sitter_mux::LANGUAGE` and `tree_sitter_mux::HIGHLIGHT_QUERY`.
- `crates/lsp-fuzz-grammars/` - registers `Language::Mux` with extension `mux`,
  LSP language ID `mux`, the Mux grammar, and the Mux highlight query.
- `crates/lsp-fuzz-cli/` - adds `export-baseline`, which decodes the
  two-dimensional baseline's `TwoDimBaselineInput` into a workspace and a
  replayable `transcript.lsp`. The upstream `reproduce-baseline` path requires
  ASAN logs, which a Rust build without ASAN does not produce.
- Dependency pins - `lsp-types` is pinned to the exact commit
  `00b517e1936150cd8ab7a02aeb602c2a5fe66a46` and the fuzzer's direct
  `fluent-uri` dependency is moved to `0.4`, matching that `lsp-types` commit.
  The generated `Cargo.lock` is tracked so the snapshot builds reproducibly.

### Target harness

`mux lsp` already speaks LSP over stdin/stdout. LSPFuzz delivers test cases
through AFL++ shared memory and requires the target to advertise shared-memory
fuzzing. [`targets/mux/afl-shmem.patch`](targets/mux/afl-shmem.patch) applies a
feature-gated patch to the isolated Mux checkout:

- defines `__afl_sharedmem_fuzzing` so the AFL++ runtime advertises
  `FS_NEW_OPT_SHDMEM_FUZZ`;
- when `__afl_fuzz_ptr` is available, copies the current test case into a
  `memfd` and points file descriptor 0 at it before `lsp-server` starts.

Without the feature, or outside a forkserver with shared-memory fuzzing, the
Mux server keeps reading ordinary stdin. The ordinary and instrumented builds
therefore share one source revision and differ only by the harness feature and
AFL++ instrumentation.

Target configuration used by every script:

```text
executable:  build/mux-afl-target/<profile>/mux
arguments:   lsp
input:       LSP JSON-RPC framing from AFL++ shared-memory fuzzing
output:      stdout (captured by LSPFuzz)
coverage:    AFL++ PCGUARD instrumentation
```

## Prerequisites

- Linux x86-64, Rust stable 1.93.1, Rust nightly, LLVM 22 development files,
  `clang`, `make`, `git`, `rsync`, `python3`.
- Network access on first build for crates.io and the Mux runtime git
  dependency.
- About 10 GB free disk for both Mux builds and the fuzzer.

## Build

Run from the repository root:

```bash
# 1. Build pinned AFL++ and install cargo-afl 0.18.2.
scripts/00_setup_tools.sh

# 2. Build the vendored LSPFuzz with the Mux language.
scripts/01_build_lspfuzz.sh

# 3. Clone the pinned Mux compiler into build/, apply the harness patch, and
#    build both the instrumented and ordinary binaries.
MUX_BUILD_PROFILE=debug scripts/02_build_target.sh

# 4. Copy Mux compiler tests and examples into inputs/mux-sources/ and mine a
#    context-fragment database.
scripts/03_collect_fragments.sh
```

Set `MUX_BUILD_PROFILE=release` for campaign runs. Re-run
`scripts/02_build_target.sh` with the same value; scripted paths pick the
profile up from `scripts/env.sh`.

## Validation

Check that all artifacts exist and the ordinary server answers a full protocol
smoke test:

```bash
scripts/40_verify_setup.sh
```

Direct protocol smoke test, independent of LSPFuzz:

```bash
scripts/10_smoke_protocol.sh
```

This starts `mux lsp`, initializes, opens two Mux documents, sends every
advertised request type, waits for responses and diagnostics, sends
`shutdown`/`exit`, and requires exit code 0. It also compares `afl-showmap`
edge maps for an initialization-only transcript and an
initialization-plus-requests transcript to prove coverage changes after
initialization.

Short LSPFuzz smoke campaign:

```bash
SMOKE_SECONDS=120 scripts/11_smoke_fuzz.sh full
SMOKE_SECONDS=120 scripts/11_smoke_fuzz.sh baseline
```

The script checks that the target starts with `mux lsp`, that a fragment
database is loaded, that nonzero edge coverage is recorded, that the server
stops cleanly, and that at least the available corpus inputs can be exported
and replayed against the ordinary Mux build. A run summary is written to
`results/smoke/<mode>-<timestamp>/smoke-summary.json`.

## Campaigns

Full LSPFuzz, three repetitions, six-hour budget:

```bash
MUX_BUILD_PROFILE=release scripts/02_build_target.sh
scripts/03_collect_fragments.sh
CAMPAIGN_HOURS=6 CAMPAIGN_REPETITIONS=3 scripts/20_run_campaign.sh full
```

Two-dimensional baseline, three repetitions, six-hour budget:

```bash
CAMPAIGN_HOURS=6 CAMPAIGN_REPETITIONS=3 scripts/20_run_campaign.sh baseline
```

The seed for repetition `n` is `CAMPAIGN_SEED + n - 1`, so both configurations
use identical seeds per repetition. Every run stores `fuzzer.log`, `run.json`,
`state/stats`, the corpus, and the solution corpus under
`results/<mode>/rep-<n>/`.

The no-context-awareness ablation is conditional on both campaigns completing:

```bash
CAMPAIGN_HOURS=6 CAMPAIGN_REPETITIONS=3 scripts/20_run_campaign.sh no-context-awareness
```

### Stats format

`state/stats` is CSV with one row per fuzzing cycle:

```text
corpus_count,solutions_count,elapsed_seconds,executions,edges_found
```

## Export, replay, and summary

```bash
scripts/21_export_replay.sh full
scripts/21_export_replay.sh baseline
scripts/30_summarize.py --results results
```

`21_export_replay.sh` exports every solution input, then replays the exported
LSP transcripts one message at a time against the ordinary `mux` binary. Full
solutions use `export`; two-dimensional baseline solutions use `export-baseline`
to decode their code and editor-operation components into LSP messages. Each
repetition gets a `replay.json` with the crashing request, exit status, signal,
and a stderr tail. `30_summarize.py` writes:

- `results/summary.json` - per-run and aggregate machine-readable results;
- `results/summary.md` - tables for the report;
- `results/coverage-over-time.csv` - all coverage samples.

The summarizer reports final and maximum edges, executions per second, corpus
size, solution count, time to first failure, replay counts, and a split between
operation-triggered and code-loading failures. A failure is marked reproducible
only when replay against the ordinary build crashes at the same exported input.

## Paper experiments: reproduced and omitted

Reproduced by this pipeline:

- LSPFuzz with the Mux grammar and AFL++ edge coverage.
- Source-fragment mining from Mux compiler tests and examples.
- Direct LSP protocol smoke test over all advertised Mux request types.
- Full LSPFuzz versus the paper's two-dimensional baseline, with identical
  six-hour budgets and three repetitions.
- Edge coverage over time, executions per second, unique solution inputs, time
  to first failure, and operation-triggered versus code-loading classification.
- Failure export and replay against an ordinary Mux build.
- Conditional no-context-awareness ablation.

Not reproduced or not applicable to this subject:

- The paper's original four subjects and their raw crash/coverage numbers.
  Only relative full-versus-baseline measurements are comparable.
- ClusterFuzzLite/OSS-Fuzz deployment measurements.
- ASAN-specific crash-stack deduplication. The Mux server is a Rust binary built
  without AddressSanitizer here; LSPFuzz's solution corpus and replay-based
  reproduction are used instead. The `--no-asan` flag avoids expecting ASAN
  logs during replay.
- Long-term maintainer response or patch rates.
- Multi-machine scaling and scheduler comparisons from the paper.

## Repository layout

```text
config/pins.env              pinned revisions and toolchain identifiers
upstream-lsp-fuzz/           paper snapshot plus Mux integration
targets/mux/                 harness patch, protocol smoke, replay tool
scripts/                     setup, build, mining, campaigns, summary
inputs/mux-sources/          copied Mux compiler tests and examples
inputs/mux-fragments.cbor.zst
results/                     campaign and smoke outputs (only README.md is tracked)
metadata/                    revision and run metadata
docs/                        proposal and reproduction notes
```

## Troubleshooting

- `LLVM_SYS_221_PREFIX` must point at LLVM 22. The Mux build script also writes
  `.cargo/config.toml` in the isolated checkout on first build and exits once;
  re-run `scripts/02_build_target.sh`.
- If `cargo afl` reports a missing runtime, re-run `cargo afl config --build`
  with the same Rust toolchain used for the target.
- LSPFuzz needs nightly for `feature(variant_count)`. Use
  `scripts/01_build_lspfuzz.sh` rather than a bare `cargo build`.
- A Mux program or server can hang on malformed input. Campaign runs always use
  `--exec-timeout`; ad-hoc runs should be wrapped in `timeout`.
