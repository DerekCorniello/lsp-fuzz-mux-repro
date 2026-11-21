#!/usr/bin/env python3
"""Replay LSPFuzz-exported inputs against an ordinary `mux lsp` binary.

Exported layout (from `lsp-fuzz-cli export`):

    <case>/requests/message_00000 ...
    <case>/workspace/...

Each request file already contains the complete LSP-framed payload (headers and
JSON body). The replay sends them one at a time so a crashing request can be
attributed to the last delivered message.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

METHOD_RE = re.compile(rb'"method"\s*:\s*"([^"]+)"')
ID_RE = re.compile(rb'"id"\s*:\s*(\d+)')


def extract_method(payload: bytes) -> str | None:
    body_start = payload.find(b"\r\n\r\n")
    body = payload[body_start + 4 :] if body_start >= 0 else payload
    match = METHOD_RE.search(body)
    return match.group(1).decode() if match else None


def extract_id(payload: bytes) -> int | None:
    body_start = payload.find(b"\r\n\r\n")
    body = payload[body_start + 4 :] if body_start >= 0 else payload
    match = ID_RE.search(body)
    return int(match.group(1)) if match else None






def split_transcript(data: bytes) -> list[bytes]:
    """Split a concatenated Content-Length framed LSP transcript."""
    messages: list[bytes] = []
    offset = 0
    total = len(data)
    while offset < total:
        header_end = data.find(b"\r\n\r\n", offset)
        if header_end < 0:
            raise ValueError("truncated LSP header")
        headers = data[offset:header_end]
        length = None
        for line in headers.split(b"\r\n"):
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                length = int(value.strip())
                break
        if length is None:
            raise ValueError("missing Content-Length")
        body_start = header_end + 4
        body_end = body_start + length
        if body_end > total:
            raise ValueError("truncated LSP body")
        messages.append(data[offset:body_end])
        offset = body_end
    return messages

def classify_exit(code: int, stderr_pipe) -> str:
    if code < 0:
        return "crash"
    if code == 0:
        return "ok"
    if code not in {1, 101}:
        return "crash"
    stderr = ""
    if stderr_pipe is not None:
        try:
            stderr = stderr_pipe.read().decode(errors="replace")
        except Exception:
            stderr = ""
    if "panicked at" in stderr or "fatal runtime error" in stderr:
        return "crash"
    if code == 1 and "received exit before shutdown" in stderr:
        return "eof-without-shutdown"
    return "nonzero-exit"

def replay_case(
    case_dir: Path,
    executable: Path,
    target_args: list[str],
    message_timeout: float,
    settle_timeout: float,
) -> dict:
    workspace = case_dir / "workspace"
    request_dir = case_dir / "requests"
    transcript_path = case_dir / "transcript.lsp"
    if request_dir.is_dir():
        messages = sorted(request_dir.glob("message_*"))
    elif transcript_path.is_file():
        try:
            messages = split_transcript(transcript_path.read_bytes())
        except ValueError as error:
            return {"status": "invalid", "reason": str(error)}
    else:
        return {"status": "invalid", "reason": "missing requests or transcript"}
    proc = subprocess.Popen(
        [str(executable), *target_args],
        cwd=workspace if workspace.is_dir() else case_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert proc.stdin is not None
    result: dict = {
        "status": "ok",
        "messages": len(messages),
        "last_method": None,
        "last_id": None,
        "exit_code": None,
        "signal": None,
        "stderr_tail": "",
    }
    last_method = None
    last_id = None
    try:
        for index, message_path in enumerate(messages):
            if isinstance(message_path, bytes):
                payload = message_path
            else:
                payload = message_path.read_bytes()
            last_method = extract_method(payload)
            last_id = extract_id(payload)
            result["last_method"] = last_method
            result["last_id"] = last_id
            try:
                proc.stdin.write(payload)
                proc.stdin.flush()
            except BrokenPipeError:
                break

            # Give the server a short window to act on this message. Only the
            # terminal `exit` notification needs the full timeout; otherwise
            # per-message waits would dominate replay time for long transcripts.
            wait_for_exit = last_method == "exit"
            deadline = time.monotonic() + (message_timeout if wait_for_exit else 0.05)
            while time.monotonic() < deadline:
                code = proc.poll()
                if code is not None:
                    result["exit_code"] = code
                    if code < 0:
                        result["signal"] = -code
                    result["failed_message_index"] = index
                    result["status"] = classify_exit(code, proc.stderr)
                    return result
                time.sleep(0.005 if wait_for_exit else 0.01)

        # Close stdin so a server waiting for more input terminates on EOF.
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass

        # Let the server finish queued work and terminate on EOF.
        deadline = time.monotonic() + settle_timeout
        while time.monotonic() < deadline:
            code = proc.poll()
            if code is not None:
                result["exit_code"] = code
                if code < 0:
                    result["signal"] = -code
                result["status"] = classify_exit(code, proc.stderr)
                return result
            time.sleep(0.02)

        result["status"] = "timeout"
        return result
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
        if proc.stderr is not None:
            stderr = proc.stderr.read().decode(errors="replace")
            result["stderr_tail"] = "\n".join(stderr.splitlines()[-20:])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Exported case dir or a directory of case dirs")
    parser.add_argument("--executable", required=True, help="Ordinary (uninstrumented) mux binary")
    parser.add_argument("--arg", action="append", default=[], help="Target argument (repeatable)")
    parser.add_argument("--message-timeout", type=float, default=2.0, help="Max wait for the terminal exit notification")
    parser.add_argument("--settle-timeout", type=float, default=5.0)
    parser.add_argument("--jobs", type=int, default=1, help="Parallel replay workers")
    parser.add_argument("--output", help="Write JSON report here")
    args = parser.parse_args()

    executable = Path(args.executable).resolve()
    input_dir = Path(args.input).resolve()
    args.target_args = args.arg or ["lsp"]

    if (input_dir / "requests").is_dir():
        cases = [input_dir]
    else:
        cases = sorted(
            path
            for path in input_dir.iterdir()
            if (path / "requests").is_dir() or (path / "transcript.lsp").is_file()
        )

    reports = []
    total = len(cases)
    if args.jobs <= 1:
        for index, case in enumerate(cases, start=1):
            report = replay_case(
                case,
                executable,
                args.target_args,
                args.message_timeout,
                args.settle_timeout,
            )
            report["case"] = str(case)
            reports.append(report)
            print(f"[{index}/{total}] {report['status']} {case.name}", file=sys.stderr)
            print(json.dumps(report, sort_keys=True))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {
                pool.submit(
                    replay_case,
                    case,
                    executable,
                    args.target_args,
                    args.message_timeout,
                    args.settle_timeout,
                ): case
                for case in cases
            }
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                case = futures[future]
                report = future.result()
                report["case"] = str(case)
                reports.append(report)
                print(f"[{index}/{total}] {report['status']} {case.name}", file=sys.stderr)
        reports.sort(key=lambda report: report["case"])

    summary = {
        "cases": len(reports),
        "ok": sum(1 for report in reports if report["status"] == "ok"),
        "crash": sum(1 for report in reports if report["status"] == "crash"),
        "timeout": sum(1 for report in reports if report["status"] == "timeout"),
        "other": sum(
            1
            for report in reports
            if report["status"] not in {"ok", "crash", "timeout"}
        ),
        "reports": reports,
    }
    if args.output:
        Path(args.output).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0 if summary["crash"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
