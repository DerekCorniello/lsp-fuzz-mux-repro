# LSPFuzz OOM during the Mux campaign

## Summary

A 15-minute full-mode pilot against `mux lsp` was OOM-killed after roughly
five minutes of fuzzing. The fuzzer stopped making progress while its anonymous
RSS continued to grow, which identified target-output capture as the problem.
The local vendored LSPFuzz tree now bounds stdout capture and the input
shared-memory mapping. A rerun completed the full 15-minute budget with a peak
RSS of 791 MB.

## Observed failure

Command:

```sh
RESULTS_DIR="$PWD/results/pilot" \
MUX_BUILD_PROFILE=release \
CAMPAIGN_HOURS=1 \
CAMPAIGN_WRAP_SECONDS=900 \
CAMPAIGN_REPETITIONS=1 \
CAMPAIGN_SEED=0 \
./scripts/20_run_campaign.sh full
```

The kernel reported:

```text
Out of memory: Killed process ... (lsp-fuzz-cli)
anon-rss: 19108280 kB
```

Sampling `/proc/<pid>/status` showed the important detail: the corpus and
execution counters froze at about 1,282 corpus entries and 27,172 executions,
while RSS grew by roughly 1 GB every 10 seconds. This was not gradual corpus
growth; the fuzzer was stuck processing one execution's output.

## Root cause

`LspOutputObserver::capture_stdout_content` in
`crates/lsp-fuzz/src/execution/responses.rs` read every JSON-RPC message from
the target's captured stdout into an unbounded `Vec<JsonRPCMessage>`. A
pathological Mux response could therefore consume arbitrary memory before the
execution completed. Because the fuzzer had not returned to its main loop,
campaign statistics stopped advancing while the process continued allocating.

The full and baseline CLIs also reserved a 15 GiB shared-memory segment for each
test input. That mapping is mostly virtual and was not the 19 GB of anonymous
RSS, but it adds unnecessary address-space and commit pressure on a 23 GB
machine.

## Local fixes

- `crates/lsp-fuzz/src/execution/responses.rs`
  - Added a 32 MiB per-execution cap on captured target stdout.
  - Reads through a bounded `Take` adapter and logs a warning if the cap is
    reached.
  - The captured-message vector is still cleared before each execution.
- `crates/lsp-fuzz-cli/src/cli/fuzz.rs`
- `crates/lsp-fuzz-cli/src/cli/baseline/two_dim.rs`
- `crates/lsp-fuzz-cli/src/cli/baseline/nautilus.rs`
- `crates/lsp-fuzz-cli/src/cli/baseline/binary.rs`
  - Reduced `INPUT_SHM_SIZE` from 15 GiB to 64 MiB. The largest observed Mux
    corpus is far smaller than this limit.

## Validation

| Run | Result |
|---|---|
| Before fixes, full mode, 900 s budget | OOM-killed at ~5 minutes; 19+ GB RSS |
| After fixes, full mode, 600 s diagnostic | 72,924 executions, 607 MB peak RSS |
| After fixes, full mode, 900 s pilot | 88,011 executions, 791 MB peak RSS, 0 objectives |

The post-fix pilot crossed the previous freeze point and kept making progress.
`run.json` reports status `124`, which is the campaign timeout wrapping the
fuzzer at the requested budget, not a crash. No 32 MiB truncation warning was
logged during either post-fix run, so the smaller shared-memory mapping or the
changed execution timing may have avoided the specific pathological response.
The capture cap remains a necessary safety bound for longer campaigns.

## Remaining limitation

If a target execution produces more than 32 MiB of stdout, the excess is
discarded and does not contribute response feedback. The truncation is logged,
so a long campaign can be checked for it. This did not occur in the 10- or
15-minute pilots, but six-hour runs could encounter it.

## Recommended upstream change

The unbounded capture is worth reporting upstream. A proposed PR should:

1. Make the stdout capture limit configurable, for example with a
   `--max-target-output-bytes` option, and keep a finite default.
2. Bound the reader before JSON-RPC parsing so a single oversized message
   cannot allocate without limit.
3. Record truncation explicitly, either in observer metadata or a dedicated
   statistic, instead of relying only on a log line.
4. Add a regression test using a fake LSP target that emits oversized output,
   asserting that capture stays bounded and the fuzzer continues.
5. Treat the 15 GiB shared-memory size as a separate change. Replace the
   hardcoded value with a configurable input limit or a shared constant rather
   than silently shrinking it for every user.

The Mux-specific grammar and harness changes should not be part of that PR.
