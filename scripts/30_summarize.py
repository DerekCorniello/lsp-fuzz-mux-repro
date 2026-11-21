#!/usr/bin/env python3
"""Summarize LSPFuzz campaign stats and replay reports into machine-readable tables."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def read_stats(path: Path) -> list[dict]:
    samples = []
    if not path.exists():
        return samples
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) != 5:
            continue
        corpus, solutions, time_s, executions, edges = map(int, parts)
        samples.append(
            {
                "corpus": corpus,
                "solutions": solutions,
                "time_s": time_s,
                "executions": executions,
                "edges": edges,
            }
        )
    return samples


def classify_method(method: str | None) -> str:
    if method is None:
        return "unknown"
    if method.startswith("textDocument/did") or method.startswith("workspace/"):
        return "code-loading"
    if method.startswith("textDocument/"):
        return "operation-triggered"
    return "unknown"


def replay_reports(run_dir: Path) -> list[dict]:
    path = run_dir / "replay.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict) and "reports" in data:
        return data["reports"]
    return data if isinstance(data, list) else []


def summarize_run(run_dir: Path) -> dict:
    run_meta_path = run_dir / "run.json"
    run_meta = json.loads(run_meta_path.read_text()) if run_meta_path.exists() else {}
    stats = read_stats(run_dir / "state" / "stats")
    final = stats[-1] if stats else None
    first_failure = None
    solution_files = []
    solutions_dir = run_dir / "state" / "solutions"
    if solutions_dir.is_dir():
        for path in sorted(solutions_dir.iterdir()):
            if not path.is_file():
                continue
            solution_files.append(path.name)
            # File names are id_<id>_time_<seconds>_exec_<executions>.
            parts = path.name.split("_")
            if "time" in parts:
                index = parts.index("time")
                if index + 1 < len(parts):
                    try:
                        value = int(parts[index + 1])
                    except ValueError:
                        continue
                    first_failure = value if first_failure is None else min(first_failure, value)

    reports = replay_reports(run_dir)
    classifications = [classify_method(report.get("last_method")) for report in reports]
    reproducible = [report for report in reports if report.get("status") == "crash"]

    elapsed = final["time_s"] if final else 0
    executions = final["executions"] if final else 0
    return {
        "mode": run_meta.get("mode", run_dir.parent.name),
        "repetition": run_meta.get("repetition"),
        "seed": run_meta.get("seed"),
        "fuzzer_status": run_meta.get("status"),
        "start_utc": run_meta.get("start_utc"),
        "end_utc": run_meta.get("end_utc"),
        "samples": len(stats),
        "final_edges": final["edges"] if final else 0,
        "max_edges": max((sample["edges"] for sample in stats), default=0),
        "executions": executions,
        "elapsed_seconds": elapsed,
        "executions_per_second": (executions / elapsed) if elapsed else 0,
        "corpus_count": final["corpus"] if final else 0,
        "solutions_count": final["solutions"] if final else 0,
        "time_to_first_failure_s": first_failure,
        "solution_files": solution_files,
        "replayed_cases": len(reports),
        "reproducible_crashes": len(reproducible),
        "operation_triggered": classifications.count("operation-triggered"),
        "code_loading": classifications.count("code-loading"),
        "unknown_failures": classifications.count("unknown"),
        "coverage_series": stats,
        "replay_reports": reports,
        "stats_file": str(run_dir / "state" / "stats"),
    }


def aggregate(runs: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for run in runs:
        groups.setdefault(run["mode"], []).append(run)
    result = {}
    for mode, mode_runs in groups.items():
        def values(key: str) -> list[float]:
            return [float(run[key]) for run in mode_runs]

        def mean(key: str) -> float:
            data = values(key)
            return statistics.fmean(data) if data else 0.0

        def stdev(key: str) -> float:
            data = values(key)
            return statistics.stdev(data) if len(data) > 1 else 0.0

        result[mode] = {
            "repetitions": len(mode_runs),
            "final_edges_mean": mean("final_edges"),
            "final_edges_stdev": stdev("final_edges"),
            "exec_per_second_mean": mean("executions_per_second"),
            "solutions_mean": mean("solutions_count"),
            "reproducible_crashes_mean": mean("reproducible_crashes"),
            "time_to_first_failure_s": [
                run["time_to_first_failure_s"]
                for run in mode_runs
                if run["time_to_first_failure_s"] is not None
            ],
            "operation_triggered": sum(run["operation_triggered"] for run in mode_runs),
            "code_loading": sum(run["code_loading"] for run in mode_runs),
        }
    return result


def render_markdown(summary: dict) -> str:
    lines = [
        "# LSPFuzz Mux campaign summary",
        "",
        "| mode | reps | final edges (mean +/- sd) | exec/s | solutions | reproducible crashes | op-triggered | code-loading |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode, data in sorted(summary["aggregate"].items()):
        lines.append(
            "| {mode} | {reps} | {edges:.1f} +/- {sd:.1f} | {eps:.1f} | {sol:.1f} | {crashes:.1f} | {op} | {load} |".format(
                mode=mode,
                reps=data["repetitions"],
                edges=data["final_edges_mean"],
                sd=data["final_edges_stdev"],
                eps=data["exec_per_second_mean"],
                sol=data["solutions_mean"],
                crashes=data["reproducible_crashes_mean"],
                op=data["operation_triggered"],
                load=data["code_loading"],
            )
        )
    lines.extend(
        [
            "",
            "## Per-run detail",
            "",
            "| mode | rep | seed | edges | exec/s | corpus | solutions | first failure (s) | replayed | reproducible |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for run in summary["runs"]:
        lines.append(
            "| {mode} | {rep} | {seed} | {edges} | {eps:.1f} | {corpus} | {solutions} | {first} | {replayed} | {repro} |".format(
                mode=run["mode"],
                rep=run["repetition"],
                seed=run["seed"],
                edges=run["final_edges"],
                eps=run["executions_per_second"],
                corpus=run["corpus_count"],
                solutions=run["solutions_count"],
                first=run["time_to_first_failure_s"],
                replayed=run["replayed_cases"],
                repro=run["reproducible_crashes"],
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="results")
    parser.add_argument("--output-json", default="results/summary.json")
    parser.add_argument("--output-markdown", default="results/summary.md")
    parser.add_argument("--output-csv", default="results/coverage-over-time.csv")
    args = parser.parse_args()

    results_dir = Path(args.results)
    run_dirs = sorted(
        path
        for path in results_dir.glob("*/rep-*")
        if path.is_dir() and (path / "state").is_dir()
    )
    runs = [summarize_run(run_dir) for run_dir in run_dirs]
    summary = {"runs": runs, "aggregate": aggregate(runs)}

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2) + "\n")
    Path(args.output_markdown).write_text(render_markdown(summary))

    csv_lines = ["mode,repetition,time_s,executions,edges,corpus,solutions"]
    for run in runs:
        for sample in run["coverage_series"]:
            csv_lines.append(
                f"{run['mode']},{run['repetition']},{sample['time_s']},{sample['executions']},{sample['edges']},{sample['corpus']},{sample['solutions']}"
            )
    Path(args.output_csv).write_text("\n".join(csv_lines) + "\n")

    print(f"wrote {output_json}")
    print(f"wrote {args.output_markdown}")
    print(f"wrote {args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
