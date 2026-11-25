# Mux grammar parse-memory blowup

## Summary

A six-hour full-mode run against `mux lsp` was OOM-killed after the fuzzer
process grew to roughly 16 GB in about two minutes. The failure was not in the
Mux server and was not the target-output or response-metadata problems already
documented in `docs/LSPFUZZ_OOM_FIX.md`.

A gdb backtrace taken during the memory climb shows the allocation happening
inside tree-sitter while the fuzzer reparses a mutated Mux document:

```text
GrammarBasedMutation::splice
  -> TextDocument::edit
     -> TextDocument::update_metadata
        -> tree_sitter::Parser::parse
           -> ts_parser_parse
              -> ts_parser__handle_error
                 -> ts_parser__condense_stack
                    -> ts_parser__reduce
```

The fuzzer document limit is only 100 KB
(`crates/lsp-fuzz/src/text_document/mutations/mod.rs`,
`MAX_DOCUMENT_SIZE = 100_000`). A 100 KB invalid document should not need
16 GB. The evidence points to a superlinear parse path caused by the Mux
grammar's ambiguity between comparison expressions and generic type arguments.

This document records the issue and the plan to fix the grammar. No grammar fix
has been applied yet.

## Observed failure

Run:

```sh
RESULTS_DIR="$PWD/results" \
MUX_BUILD_PROFILE=release \
CAMPAIGN_HOURS=6 \
CAMPAIGN_WRAP_SECONDS=21900 \
CAMPAIGN_REPETITIONS=1 \
CAMPAIGN_SEED=0 \
setsid nohup ./scripts/20_run_campaign.sh full
```

The run was healthy for about an hour:

- ~900 MB RSS
- ~2,500 corpus entries
- ~420,000 executions

Then the corpus and execution counters stopped changing while RSS grew:

| Time | RSS | Available system RAM | Corpus | Executions |
|---|---:|---:|---:|---:|
| 21:03:56 | 2.0 GB | 14.2 GB | 2,576 | 423,680 |
| 21:04:11 | 3.6 GB | 12.5 GB | 2,576 | 423,680 |
| 21:04:26 | 5.1 GB | 11.0 GB | 2,576 | 423,680 |
| 21:04:41 | 6.5 GB | 10.3 GB | 2,576 | 423,680 |

The fuzzer continued climbing to roughly 16 GB and took the laptop down. The
partial run is in `results/full/rep-1/`; its `run.json` was never written and it
must not be counted as a completed repetition.

The captured gdb output is preserved at:

```text
results/full/rep-1/freeze-bt.txt
```

## Root cause

The Mux grammar uses `<` and `>` for two different purposes:

- comparison expressions, such as `a < b > c`;
- generic type arguments, such as `Graph<string>`.

The grammar intentionally permits both interpretations in overlapping
contexts. Its `conflicts` list includes combinations of `primary_expression`,
`type_path`, `type_arg`, `member_object`, and `call_target`. It also uses
`prec.dynamic(1)` on `typed_declaration` to resolve the runtime ambiguity
between a generic declaration and a comparison chain.

A malformed or incomplete generic sequence keeps several GLR interpretations
alive. During error recovery, tree-sitter retains and condenses those parse
stacks instead of collapsing to one interpretation. The result is memory growth
that is much worse than linear in the input size.

The key grammar locations are:

- `conflicts` around `grammar.js:14`;
- `typed_declaration` dynamic precedence around `grammar.js:138`;
- comparison `<`/`>` around `grammar.js:335`;
- `type_path` and `type_args` around `grammar.js:558`.

The fuzzer's grammar-based mutator can generate incomplete generic syntax. The
existing deterministic cap still allows a document to contain tens of thousands
of unmatched `<` tokens, which is enough to reach the observed memory range.

## Reproduction without the fuzzer

Using the upstream grammar at revision
`e7a030806b2a1b996078a3101a4655debd4ac51a`, a standalone fresh parse of
unmatched generic prefixes shows the superlinear behavior:

| Input | Size | Peak RSS | Result |
|---|---:|---:|---|
| `a<` repeated 10,000 times | 20 KB | 2.27 GB | completed |
| `a<` repeated 20,000 times | 40 KB | 4.48 GB | completed |
| `a<` repeated 50,000 times | 100 KB | exceeded 2 GB test cap | capped |

The input cap for a fuzzer document is 100 KB, so the pathological range is
reachable by normal mutation. The test used only the grammar and
`tree-sitter parse`; the Mux server was not involved.

The reproduction was run under a local memory limit so a failed parse could not
repeat the laptop crash. Commands used:

```sh
tree-sitter generate
ulimit -v 2000000
timeout -s KILL 5 tree-sitter parse /tmp/path/to/stress.mux
```

## Why the paper did not hit this

The paper's experiments used:

- a dual AMD EPYC 7773X machine with 128 cores and 1 TB RAM;
- 24-hour runs, repeated ten times;
- C/C++, Solidity, Ruby, and SystemVerilog servers;
- mature tree-sitter grammars for those languages.

A roughly 16 GB spike is only about 1.6% of a 1 TB machine. Even several
concurrent processes would fit. The paper does not report per-process memory
usage, OOM handling, or a memory limit, so it is not evidence that their
grammars cannot produce pathological parses; it is evidence that their hardware
had enough headroom to absorb them.

The Mux subject is different: it uses the smaller `tree-sitter-mux` grammar and
a newer tree-sitter version. This failure is specific to the Mux grammar parse
path, not a general property of LSPFuzz or the Mux server.

## What this is not

These existing fixes are still valid, but they do not address this failure:

- target stdout capture is capped at 1 MiB;
- retained response metadata is capped for diagnostics, symbol ranges, and
  parameter fragments;
- input shared memory is reduced to 64 MiB.

The failing backtrace is in the fuzzer's mutator before target execution. No
LSP response had been produced when the memory grew.

## Confidence

**85%** that the `<` ambiguity between comparison expressions and generic type
arguments is the trigger.

The remaining uncertainty:

- **10%** that incremental reparsing with the old `tree_sitter::Tree` makes the
  grammar ambiguity worse than a fresh parse;
- **5%** that a separate tree-sitter `0.25.10` parser defect is responsible
  independently of the Mux grammar.

The confidence is high because the standalone grammar reproduces the same
superlinear memory behavior without LSPFuzz, Mux, target output, or response
metadata.

## Fix plan

### Phase 1: Capture the exact fuzzer input

Before changing the grammar, capture the real failing document so the fix is
tested against the actual trigger rather than only the synthetic `a<` chain.

1. Add temporary instrumentation around `TextDocument::update_metadata` to
   write the current document and its byte length to a bounded file when the
   document is large or contains many unmatched angle brackets.
2. Run the instrumented fuzzer under a hard cgroup limit:
   `systemd-run --user --scope -p MemoryMax=4G ...`.
3. Preserve the document that reproduces the blowup and add it under a
   regression corpus.
4. Confirm whether the blowup occurs with:
   - fresh parsing (`parse(content, None)`);
   - incremental parsing (`parse(content, Some(&old_tree))`);
   - the current tree-sitter `0.25.10`;
   - the tree-sitter version used by the paper snapshot.

Acceptance: a minimal Mux document reproduces the memory growth under a 1 GB
test limit, and its exact token pattern is understood.

### Phase 2: Make the grammar unambiguous

The preferred fix is in `tree-sitter-mux`, not in LSPFuzz.

1. Build a grammar test branch from the pinned revision
   `e7a030806b2a1b996078a3101a4655debd4ac51a`.
2. Inspect the conflicts involving `type_path`, `type_arg`,
   `primary_expression`, `member_object`, and `call_target`.
3. Try a grammar variant that separates generic type arguments from general
   expressions instead of resolving the overlap with GLR conflicts:
   - use a dedicated type-expression production in type positions;
   - remove `type_path` from expression/member-object alternatives where it is
     not needed;
   - adjust precedence on comparison, generic, and typed-declaration rules;
   - require a context-sensitive token or external scanner if Mux's syntax
     requires `<` adjacency to disambiguate generics.
4. Verify that valid Mux generic declarations still parse identically:
   `Graph<string>`, nested generics, type paths, generic methods, and the
   `graph.Graph<string> g` case that motivated `prec.dynamic`.
5. Regenerate `src/parser.c` with `tree-sitter generate` and keep the generated
   parser and node types consistent with the vendored crate.

Acceptance: the stress input parses with bounded memory and time, and valid Mux
grammar tests show no regressions.

### Phase 3: Add a parser safety bound

The grammar fix may be difficult to make complete because the language itself
has a real ambiguity. Add a defensive bound in the fuzzer regardless:

1. In `TextDocument::update_metadata`, reject or truncate a mutation before
   calling tree-sitter if the document exceeds a conservative count of
   ambiguous constructs, such as unmatched `<` tokens or maximum nesting.
2. Keep the existing `MAX_DOCUMENT_SIZE` guard.
3. Do not silently accept a partial parse; log the rejection so campaign
   statistics show how often it occurs.
4. Document the bound as a reproduction setting and apply it identically to
   full and baseline modes.

This is a safety net, not the primary grammar fix.

### Phase 4: Isolate future fuzz runs from the laptop

1. Run every campaign under a cgroup memory limit, for example:
   `systemd-run --user --scope -p MemoryMax=4G ...`.
2. Add a watchdog that reports RSS and `MemAvailable`, and stops the fuzzer
   before system OOM. The previous watcher attached gdb but did not enforce a
   limit; that is not sufficient.
3. Keep a separate `MemoryMax` for the target if target memory is measured.
4. Never start a six-hour run without the limit and watchdog in place.

### Phase 5: Validate before resuming the campaign

Run in this order:

1. Standalone grammar stress test under a 1 GB memory limit.
2. All collected Mux fragments and compiler tests through the parser.
3. LSPFuzz full mode for 15 minutes under a 4 GB cgroup limit.
4. LSPFuzz full mode for 1 hour under the same limit.
5. Only then resume the six-hour repetitions.

Acceptance criteria:

- no parse operation exceeds the bound on the regression input;
- no OOM or unbounded RSS growth in the 1-hour run;
- valid Mux syntax still parses;
- full and baseline use the same grammar, document limits, and memory limits.

## Upstream follow-up

The parser behavior is worth reporting to `tree-sitter-mux` with the minimal
unmatched-generic reproducer. If the grammar cannot be made unambiguous without
rejecting valid Mux code, the issue should also be raised upstream against
tree-sitter because an invalid input should not cause unbounded error-recovery
memory growth.
