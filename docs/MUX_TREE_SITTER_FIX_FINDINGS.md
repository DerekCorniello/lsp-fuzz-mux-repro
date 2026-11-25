# Tree-sitter Mux parse blowup: fixes and validation

## What failed

At the original `tree-sitter-mux` revision `e7a0308`, malformed input made of repeated `a<` prefixes sent Tree-sitter error recovery through many competing comparison and generic-type parses. A fresh parse of 10,000 prefixes used about 2.27 GB; 20,000 used about 4.48 GB. The original LSPFuzz campaign also grew to roughly 16 GB while reparsing a mutated Mux document. Its backtrace is in `results/full/rep-1/freeze-bt.txt`.

A later one-hour campaign with the grammar change still hit a 4 GB memory limit after roughly 12 minutes. The killed process was `lsp-fuzz-cli`, not the Mux server. That event did not leave the exact in-flight document, so its cause remains unproven. See `results/validation-grammar-fix-1h/full/rep-1/fuzzer.log` and the corresponding user journal.

## Grammar change

In `tree-sitter-mux/grammar.js`, `comparison_expression` now uses one optional comparison operator instead of a repeated operator sequence. Mux comparisons are not meant to chain, so `a < b < c` is no longer accepted as one expression. The grammar also declares a `call_target`/`type_path` conflict. Tree-sitter requires that conflict to generate the parser after the comparison change; removing it produces an unresolved conflict at a generic-looking `<` after `in`.

The change preserves the tested ambiguity cases. `graph.Graph<string> g` and `map<string, set<int>> values` parse as typed declarations, `identity<int>(1)` parses as a generic call, and `a < b` parses as a comparison. At the top level, `a < b > c` still parses as a generic typed declaration, not as two chained comparisons. This spelling is inherently ambiguous without surrounding context.

`src/parser.c` and `src/grammar.json` were regenerated. Their repro copies are identical to the current generated files:

- `upstream-lsp-fuzz/crates/tree-sitter-mux/src/parser.c`
- `upstream-lsp-fuzz/crates/lsp-fuzz-grammars/res/grammar/mux.json`

The generated parser reports ABI 15. The existing 46 corpus cases remain unchanged; five new cases cover generic declarations, a generic call, a single comparison, and the top-level ambiguity. `tree-sitter test` passes 51/51. Sample checks pass. `scripts/test-grammar-stress.sh` checks 10,000, 20,000, and 50,000 `a<` prefixes under a 1 GB test limit and a 10-second timeout; all pass. These limits are test guards, not grammar or product limits.

## Separate incremental-edit bug in LSPFuzz

`upstream-lsp-fuzz/crates/lsp-fuzz/src/text_document/mod.rs` built a Tree-sitter `InputEdit` with `new_end_position` measured from the replaced node's **old end**. It must be measured from the edit's **start** plus the replacement text. A captured mutation started at column 31, inserted 15 bytes, and reported column 59 instead of 46. The corrected calculation handles both same-line and multiline replacements. A regression test covers both cases, and all 59 `lsp-fuzz` library tests pass.

This is a definite correctness bug in the fuzzer's incremental parse path. We have not shown that it caused the earlier 4 GB event. The grammar fix is independently proven for the synthetic `a<` blowup.

## Capped fuzz validation

All runs used the full Mux campaign with seed 0 and a 2 GB systemd memory cap. Temporary capture hooks wrote the current fresh and incremental documents, the previous document, and the last `InputEdit` before parsing.

| Run | Fuzzer edit code | Duration | Executions | Memory peak | Result |
| --- | --- | ---: | ---: | ---: | --- |
| `results/diagnostic-parse-capture-v4` | Original | 20 min | ~131,000 | 711.7 MB | No OOM |
| `results/diagnostic-edit-fixed` | Corrected | 20 min | ~134,000 | 766.5 MB | No OOM |
| `results/validation-grammar-edit-1h` | Corrected | 1h 00m | 361,916 | About 1 GB | Clean timeout; no OOM |

The one-hour run completed 1h 00m and ended by its planned timeout (`run.json` status 124). The log reached 59m 58s of fuzzer time. Final statistics were 2,587 corpus entries, 361,916 executions, 61,252 edges, and zero objectives. The cgroup journal reported a peak of about 1 GB, well under the 2 GB cap. The journal and log showed no OOM, kill, panic, or output-truncation warnings. This is strong evidence that the grammar fix holds for a full hour. It is not proof of a six-hour run, and the earlier 4 GB event still has no captured input. The 20-minute run with the original edit code also completed without reproducing that event.

The capture hooks are diagnostic code, not part of the fix. At the time of this note they remain in the local fuzzer source and release binary after the one-hour run; remove them before committing or using the build for an ordinary campaign. Keep the coordinate correction and its test.

## Next step

Remove the temporary capture hooks, rebuild the release fuzzer, then run a private six-hour pilot under the same 2 GB cgroup cap. Check the service, journal, and fuzzer progress every 15 minutes. Keep the pilot results private.

If memory spikes during the pilot, keep its logs and journal, then do a separate capped diagnostic run with input capture restored. Replay the captured incremental edit against the saved previous document and compare it with a fresh parse. That will distinguish another grammar ambiguity from an incremental-parse or fuzzer issue.

The compiler's handling of chained comparisons is a separate issue recorded in `docs/MUX_CHAINED_COMPARISON.md`.
