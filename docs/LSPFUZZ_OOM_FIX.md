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

## Second failure: six-hour campaign

The bounded stdout capture was enough for short pilots but not for a full
campaign. Full repetition 1 was OOM-killed after 2h38m with:

- 1,189,457 executions
- 2,933 corpus entries
- 62,741/320,490 edges
- 0 objectives
- `anon-rss: 16526272 kB` (about 16.5 GB)

```text
Out of memory: Killed process 2073298 (lsp-fuzz-cli)
anon-rss: 16526272 kB
```

This is a second, longer-running memory problem. It is not the frozen-execution
signature from the first failure: the fuzzer was making progress when it was
killed. Memory now scales with corpus size, roughly 5.6 MB per corpus entry at
the point of the kill.

The leading suspect is `LspResponseInfo`, which is attached to every corpus
testcase and retained in memory. It contains:

- every published diagnostic range;
- every parsed symbol range;
- full LSP parameter fragments, including completion items, code actions,
  inlay hints, code lenses, workspace symbols, hierarchy items, and document
  links.

With thousands of corpus entries, those retained response objects can exceed
the machine's 23 GB of RAM. The analysis has not yet isolated which field or
response type dominates.

## Operational detection bug

The first six-hour campaign wrapper used a semicolon between the full and
baseline invocations. After full repetition 1 failed with status 137, the
wrapper continued into the baseline run instead of stopping.

The watcher also missed the failure because `scripts/20_run_campaign.sh` writes
`run.json` only after an accepted fuzzer status. An OOM-killed repetition exits
early and leaves no completion record. `scripts/25_watch_campaign.py` now:

- scans the driver log for `error:` and `Killed` lines;
- reports incomplete campaign repetition directories that have a `fuzzer.log`
  but no `run.json`;
- keeps checking RSS, stats age, truncation warnings, and disk space.

The abandoned partial `results/full` and `results/baseline` directories were
removed.

## Recommended next fix

Before another six-hour run, bound the retained per-testcase response metadata.
The likely options are:

1. Cap the number of diagnostics, symbol ranges, and each parameter-fragment
   category when `LspResponseInfo` is created. This is the smallest local
   change, but it trades some generated-operation variety for bounded memory.
2. Store response metadata on disk and load it only for the selected testcase.
   This preserves more data but is a larger change to corpus metadata handling.
3. Run on a machine with substantially more RAM. Extrapolating the observed
   growth suggests six hours would need roughly 64 GB or more, and the growth
   may not be linear.

Option 1 is the practical local fix. Any cap must be documented as a
reproduction setting, applied identically to full and baseline, and monitored.
A one-hour memory test should show a flat retained-memory curve before starting
the six-hour repetitions.

## Upstream PR assessment for the second issue

Yes, this is worth raising upstream separately from the stdout-capture bug.
Retaining full response metadata for every corpus entry is a scaling problem,
not a Mux-specific issue. An upstream issue or PR should make the retained
metadata limit configurable, handle a single oversized response without
unbounded allocation, and add a memory-growth regression test. A hardcoded
count cap is probably too blunt for upstream without maintainer input; the
local experiment can use one as a documented workaround.
