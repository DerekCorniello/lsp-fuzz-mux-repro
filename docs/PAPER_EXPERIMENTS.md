# Paper experiment mapping

Reference: Hengcheng Zhu et al., "LSPFuzz: Hunting Bugs in Language Servers",
ASE 2025, pp. 2209-2221. Replication subject: `mux lsp` at Mux compiler
`e0f4d43bd6e51bb48d428bf8ca174415294d08e9`.

| Paper item | Reproduction mechanism | Status |
|---|---|---|
| Context-aware LSP fuzzing | `scripts/20_run_campaign.sh full` using `Mux` grammar and mined fragments | ready |
| Two-dimensional baseline | `scripts/20_run_campaign.sh baseline` (`baseline-2d --language Mux`) | ready |
| No-context-awareness ablation | `scripts/20_run_campaign.sh no-context-awareness` | ready, run only after full and baseline complete |
| Markdown/grammar source corpus | `scripts/03_collect_fragments.sh` over compiler tests and examples | ready |
| Edge coverage over time | `results/<mode>/rep-*/state/stats`, summarized by `scripts/30_summarize.py` | ready |
| Executions per second | stats CSV, summarized as executions / elapsed seconds | ready |
| Unique failures | LSPFuzz solution corpus per run | ready |
| Time to first failure | `time` field in solution file names | ready |
| Operation-triggered vs code-loading failures | exported request method classification in the summary | ready when solutions exist |
| Minimized inputs | exported LSP input (message sequence plus workspace); byte-level minimization is not part of the pinned snapshot | partial |
| Replay against ordinary build | `scripts/21_export_replay.sh` and `targets/mux/replay.py`; `export-baseline` decodes two-dimensional solutions into LSP transcripts | ready |
| ASAN crash-stack deduplication | not used; Mux is built without ASAN and LSPFuzz runs with `--no-asan` | omitted |
| Original four subject servers | not applicable to a single-language replication | omitted |
| OSS-Fuzz deployment and maintainer-response measurements | out of scope | omitted |
| Multi-machine scaling | out of scope | omitted |

## Measurement controls

- Same machine for all configurations.
- Same Mux compiler, runtime, grammar, and AFL++ revisions in every run.
- Same seed per repetition across full and baseline:
  `seed = CAMPAIGN_SEED + repetition - 1`.
- Same execution timeout (`CAMPAIGN_EXEC_TIMEOUT_MS`), seed count, and power
  schedule defaults.
- Six-hour budget per repetition and three repetitions per configuration.
- LLVM 22.1.8, stable Rust 1.93.1 for Mux, nightly Rust for LSPFuzz.

## Failure classification

The replay tool records the last message delivered before the server exited.
The summarizer maps that method to a class:

- `code-loading`: `textDocument/didOpen`, `didChange`, `didClose`, or a
  `workspace/*` notification.
- `operation-triggered`: any `textDocument/*` request.
- `unknown`: no exported message or no method.

A failure is reported as reproducible only if replaying the exported case
against the ordinary build crashes again. Unreproduced failures are counted but
never claimed as bugs.

## Known limitations

- The pinned Mux server is written in Rust and built without ASAN, so
  LSPFuzz's ASAN stack-hash objective is disabled. Distinct crashes are counted
  by the solution corpus and checked by replay.
- LSPFuzz's `reproduce` subcommand expects ASAN logs; this repository therefore
  uses the exported-message replay path for ordinary builds. The vendored CLI
  gains an `export-baseline` command that turns `TwoDimBaselineInput` values into
  `workspace/` plus `transcript.lsp` for the same replay tool.
- Byte-level test-case minimization is not implemented in the pinned LSPFuzz
  snapshot. Exported cases are message-level reproductions.
