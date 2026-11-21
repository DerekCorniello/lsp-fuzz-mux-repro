# Results layout

Campaign and smoke outputs are git-ignored so raw runs stay local. The scripts
regenerate everything from the pinned revisions.

```text
results/
  smoke/
    protocol-<timestamp>.json      direct LSP smoke report
    coverage-<timestamp>.json      afl-showmap initialization-vs-requests delta
    <mode>-<timestamp>/
      fuzzer.log
      state/stats                  corpus,solutions,time,executions,edges
      state/corpus/
      state/solutions/
      exported/<case>/workspace/
      exported/<case>/requests/message_*
      replay.json
      smoke-summary.json
  full/
    rep-1/ ... rep-3/
      fuzzer.log
      run.json
      state/stats
      state/corpus/
      state/solutions/
      exported/
      replay.json
  baseline/
    rep-1/ ... rep-3/
      exported/<case>/workspace/main.mux
      exported/<case>/transcript.lsp
  no-context-awareness/
    rep-1/ ... rep-3/
  summary.json
  summary.md
  coverage-over-time.csv
```

`summary.md` contains the tables intended for the report. `summary.json` is the
machine-readable source. `coverage-over-time.csv` has one row per fuzzing cycle
for plotting.
