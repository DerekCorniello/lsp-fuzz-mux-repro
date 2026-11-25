# LSPFuzz parse OOM findings

## Short answer

This is not user error and not a hardware problem. LSPFuzz mutates Mux into malformed documents that make tree-sitter's error recovery allocate gigabytes of RAM. The Mux grammar fix removed the first known trigger, but it did not remove all of them.

The captured inputs are small. One repro is 12 bytes. Another is 61 bytes. Both could allocate past a 1 GB cgroup limit before the parse timeout was added.

A parse timeout in LSPFuzz now bounds each parse to 500 ms. A 1-hour validation run under a 2 GiB cap finished clean with two timeouts and no OOM.

## What I changed

All of these changes are in the vendored LSPFuzz source, not in the Mux compiler or language. Nothing is committed.

1. Parse input tracking in `text_document/mod.rs`.
   The fuzzer can set `LSPFUZZ_PARSE_CAPTURE_DIR`. It writes a ring of the last roughly 2,000 parse inputs with length, RSS, and timestamp. The ring keeps disk use near 20 MB instead of millions of files.

2. A fresh-reparse guard in `TextDocument::edit`.
   `should_reparse_fresh()` discards the old syntax tree when an edit replaces more than 64 bytes or removes more than four lines. The captured OOM edit replaced 122 bytes and removed ten lines, so this avoids that particular incremental parse path.

3. tree-sitter runtime upgrade from 0.25.10 to 0.26.13.
   The 12-byte repro OOMs on 0.25.10 and parses fine on 0.26.13. The 61-byte repro still needs the timeout.

4. A parse timeout in `Metadata::generate` and `TextDocument::update_metadata`.
   `parse_limited()` uses `parse_with_options` with a 500 ms budget. When the budget expires, LSPFuzz substitutes an empty parse tree and keeps going. The captured input is already in the ring. This turns a runaway parse into a skipped mutation.

5. Regression tests.
   The fuzzer library passes 60 tests, including the fresh-reparse guard.

6. A repro file at `inputs/minimal-tree-sitter-oom.mux`.

## What the runs showed

A 1-hour run with the earlier grammar fix and a 2 GB cap finished with about 1 GB peak memory. That looked good.

A 6-hour run without capture hooks hit the 6 GB cgroup cap at 18 minutes 47 seconds. That run produced no capture, so it did not identify the input.

A 1-hour diagnostic run with capture hooks and a 3 GB cap finished with about 1.9 GB peak memory. The hooks changed timing enough that the OOM did not reproduce.

A 6-hour run with capture hooks and a 6 GB cap OOMed at 57 minutes 39 seconds. The capture ring held the last parse inputs. The final input was 1,062 bytes, and the preceding edit replaced 12 bytes with 166 bytes. That edit crosses the fresh-reparse guard, so the fatal path was a fresh parse.

A 1-hour validation run with tree-sitter 0.26.13 plus the fresh-reparse guard still OOMed at 37 minutes 38 seconds under a 3 GB cap. The runtime upgrade and reparse guard alone are not enough.

The final 1-hour validation with the parse timeout passed. It ran from 02:06:12Z to 03:06:18Z, reached 59m57s of fuzzer time, and exited with status 0. Final stats were 2,324 corpus, 333,511 executions, 62,539 edges, and 0 objectives. Journal memory peak was 1.9 GiB. The parse timeout fired twice. No OOM, kill, or failed result occurred.

Note that systemd's `2G` means 2 GiB, or 2,147,483,648 bytes. The observed 2.12 GB decimal peak was still below that cap.

## Minimal repros

The first repro is 10 newlines followed by `:`. Similar tokens such as `::`, `,`, and `}` also trigger it.

The second repro is in `inputs/minimal-tree-sitter-oom.mux`:

```text
(w: " +to(){}
//ts: sis,n, locwing, e functinested s+ obal_L)
```

That file is 61 bytes. Before the timeout, a fresh parse of it grew past 1 GB. With the timeout, both repros complete under a 1 GB cap.

## Why the paper does not show this

The paper did not fuzz Mux. Its main subjects use mature grammars for C, C++, Solidity, Ruby, SystemVerilog, and similar languages. Those grammars have had years of parser testing. Mux grammar is small, permissive about whitespace, and has many GLR conflicts around expressions, types, and declarations.

The paper also ran on a machine with 128 cores and 1 TB of RAM, with 24-hour runs repeated ten times. If a subject parser briefly allocated tens of gigabytes, that machine could absorb it. This laptop has 23 GiB, and the validation campaign used cgroup caps of 2 GiB to 6 GiB. The same spike that would be a rounding error in the paper kills this run.

There is also a measurement difference. The paper reports fuzzing outcomes over completed 24-hour campaigns. Our machine has not completed a 6-hour campaign yet, so it has exposed the failure before the paper's metrics would have been collected.

## Current status

The 1-hour validation passed with the parse timeout under a 2 GiB cap. The 6-hour validation then passed under a 14 GiB cap.

The 6-hour run started at 14:16:02Z on 2026-11-24 and ended at 20:16:11Z with status 0. Final stats were 3,229 corpus, 2,117,663 executions, 64,841 edges, and 0 objectives. There were 305 parse timeouts and no target crashes. Journal memory peak was 4.7 GiB, well under the 14 GiB cap.

Rate stayed near 98 executions/sec for the whole six hours. Corpus growth fell from about 20 per minute early on to about 9 per minute by the end. The naive projection from the midpoint was about 2.12M executions, close to the final 2.12M. Edge growth flattened as coverage saturated, so a linear edge projection overstates the final count.

The parse timeout is now the important part of the fix. It turns each runaway parse into a skipped mutation, and the 6-hour run showed that this scales over a full campaign.

No commits were made.
