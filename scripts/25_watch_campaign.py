#!/usr/bin/env python3
import json
import os
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
CAMPAIGN_LOG = RESULTS / "campaign-6h.log"
STALE_SECONDS = 300
MEMORY_WARN_MB = 4096


def process_lines():
    lines = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        lines[int(entry.name)] = raw.replace(b"\0", b" ").decode(errors="replace")
    return lines


def find_process(lines, needle, exclude=()):
    return [(pid, cmd) for pid, cmd in lines.items() if needle in cmd and pid not in exclude]


def rss_mb(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def newest_stats():
    candidates = list(RESULTS.glob("*/rep-*/state/stats"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def last_json_statuses():
    statuses = []
    for path in RESULTS.glob("*/rep-*/run.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        statuses.append((path, data))
    return statuses


def log_tail(path, lines=8):
    if not path.exists():
        return []
    raw = path.read_text(errors="replace").splitlines()
    return raw[-lines:]


def truncation_count(log_path):
    if not log_path.exists():
        return 0
    size = log_path.stat().st_size
    with log_path.open("rb") as f:
        if size > 2_000_000:
            f.seek(size - 2_000_000)
        return f.read().decode(errors="replace").count("Truncated target output capture")


def main():
    now = time.time()
    lines = process_lines()
    drivers = find_process(lines, "20_run_campaign.sh", exclude=(os.getpid(),))
    fuzzers = [
        (pid, cmd)
        for pid, cmd in find_process(lines, "target/release/lsp-fuzz-cli", exclude=(os.getpid(),))
        if cmd.split()[0].endswith("/lsp-fuzz-cli")
    ]
    alerts = []

    print(f"Campaign status  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"driver:          {'running pid=' + str(drivers[0][0]) if drivers else 'NOT RUNNING'}")
    if not drivers:
        alerts.append("campaign driver is not running")

    if fuzzers:
        pid = fuzzers[0][0]
        mem = rss_mb(pid)
        print(f"fuzzer:          running pid={pid} rss={mem} MB")
        if mem is not None and mem > MEMORY_WARN_MB:
            alerts.append(f"fuzzer RSS is {mem} MB")
    else:
        print("fuzzer:          not running")
        if drivers:
            alerts.append("driver is alive but no fuzzer process is running")

    stats = newest_stats()
    if stats:
        age = int(now - stats.stat().st_mtime)
        last = stats.read_text(errors="replace").strip().splitlines()
        last = last[-1] if last else "(empty)"
        rep = stats.parent.parent.name
        mode = stats.parent.parent.parent.name
        print(f"active:          {mode}/{rep}  stats_age={age}s")
        print(f"latest stats:    {last}")
        if age > STALE_SECONDS:
            alerts.append(f"stats have not advanced for {age}s")
        log_path = stats.parent / "fuzzer.log"
        truncated = truncation_count(log_path)
        if truncated:
            alerts.append(f"{truncated} output-truncation warning(s) in current log")
    else:
        print("active:          no stats file yet")
        if drivers:
            alerts.append("driver is running but no stats file exists yet")

    for path, data in last_json_statuses():
        status = data.get("status")
        if status not in (0, 124, 130):
            alerts.append(f"{path.relative_to(ROOT)} finished with status {status}")
        else:
            print(f"completed:       {path.parent.parent.parent.name}/{path.parent.name} status={status}")

    free = shutil.disk_usage(ROOT).free / (1024**3)
    print(f"disk free:       {free:.1f} GiB")
    if free < 10:
        alerts.append(f"only {free:.1f} GiB free on disk")

    driver_lines = log_tail(CAMPAIGN_LOG, 10000)
    driver_errors = [line for line in driver_lines if "error:" in line or "Killed" in line]
    for line in driver_errors[-3:]:
        alerts.append(f"driver log: {line.strip()}")

    active_run = stats.parent.parent if stats else None
    for mode in ("full", "baseline", "no-context-awareness", "no-server-feedback"):
        for run_dir in sorted((RESULTS / mode).glob("rep-*")):
            if run_dir == active_run or (run_dir / "run.json").exists():
                continue
            if (run_dir / "fuzzer.log").exists():
                alerts.append(f"{run_dir.relative_to(ROOT)} has no run.json (incomplete)")

    print("recent driver log:")
    for line in driver_lines[-6:]:
        print(f"  {line}")

    if alerts:
        print("ALERTS:")
        for alert in alerts:
            print(f"  - {alert}")
    else:
        print("ALERTS:          none")


if __name__ == "__main__":
    main()
