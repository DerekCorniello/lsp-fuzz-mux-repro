#!/usr/bin/env python3
"""Compare AFL++ edge maps for initialization-only and full LSP transcripts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MAIN_SOURCE = """class Counter {
    int value

    func increment(int amount) returns int {
        self.value = self.value + amount
        return self.value
    }
}

func helper(int x) returns int {
    return x + 1
}

func main() returns void {
    auto counter = Counter.new()
    auto total = counter.increment(helper(41))
    print(total.to_string())
    return
}
"""

LIB_SOURCE = """class Point {
    int x
    int y

    func sum() returns int {
        return self.x + self.y
    }
}
"""


def frame(message: dict) -> bytes:
    body = json.dumps(message, separators=(",", ":")).encode()
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def initialize(workspace_uri: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "processId": None,
            "rootUri": workspace_uri,
            "workspaceFolders": [{"uri": workspace_uri, "name": "probe"}],
            "capabilities": {
                "workspace": {
                    "workspaceEdit": {"documentChanges": True},
                    "didChangeWatchedFiles": {"dynamicRegistration": True},
                },
                "textDocument": {
                    "completion": {"completionItem": {"snippetSupport": True}}
                },
            },
        },
    }


def requests(main_uri: str, lib_uri: str, main_text: str, lib_text: str) -> list[dict]:
    point = lambda line, character: {"line": line, "character": character}
    document = {"uri": main_uri}
    return [
        {
            "jsonrpc": "2.0",
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": main_uri,
                    "languageId": "mux",
                    "version": 1,
                    "text": main_text,
                }
            },
        },
        {
            "jsonrpc": "2.0",
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": lib_uri,
                    "languageId": "mux",
                    "version": 1,
                    "text": lib_text,
                }
            },
        },
        {"jsonrpc": "2.0", "id": 10, "method": "textDocument/formatting", "params": {"textDocument": document, "options": {"tabSize": 4, "insertSpaces": True}}},
        {"jsonrpc": "2.0", "id": 11, "method": "textDocument/documentSymbol", "params": {"textDocument": document}},
        {"jsonrpc": "2.0", "id": 12, "method": "textDocument/definition", "params": {"textDocument": document, "position": point(0, 6)}},
        {"jsonrpc": "2.0", "id": 13, "method": "textDocument/references", "params": {"textDocument": document, "position": point(0, 6), "context": {"includeDeclaration": True}}},
        {"jsonrpc": "2.0", "id": 14, "method": "textDocument/rename", "params": {"textDocument": document, "position": point(0, 6), "newName": "Renamed"}},
        {"jsonrpc": "2.0", "id": 15, "method": "textDocument/hover", "params": {"textDocument": document, "position": point(8, 26)}},
        {"jsonrpc": "2.0", "id": 16, "method": "textDocument/completion", "params": {"textDocument": document, "position": point(8, 26)}},
        {"jsonrpc": "2.0", "id": 17, "method": "textDocument/semanticTokens/full", "params": {"textDocument": document}},
        {"jsonrpc": "2.0", "id": 18, "method": "textDocument/signatureHelp", "params": {"textDocument": document, "position": point(11, 13)}},
        {"jsonrpc": "2.0", "id": 19, "method": "textDocument/codeAction", "params": {"textDocument": document, "range": {"start": point(0, 0), "end": point(1, 0)}, "context": {"diagnostics": []}}},
        {"jsonrpc": "2.0", "id": 20, "method": "textDocument/inlayHint", "params": {"textDocument": document, "range": {"start": point(0, 0), "end": point(20, 0)}}},
        {"jsonrpc": "2.0", "id": 21, "method": "textDocument/foldingRange", "params": {"textDocument": document}},
    ]


def transcript(workspace: Path, full: bool) -> bytes:
    main_path = workspace / "main.mux"
    lib_path = workspace / "lib.mux"
    main_path.write_text(MAIN_SOURCE)
    lib_path.write_text(LIB_SOURCE)
    workspace_uri = workspace.as_uri() + "/"
    messages = [initialize(workspace_uri)]
    messages.append({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    if full:
        messages.extend(
            requests(main_path.as_uri(), lib_path.as_uri(), MAIN_SOURCE, LIB_SOURCE)
        )
    messages.append({"jsonrpc": "2.0", "id": 2, "method": "shutdown"})
    messages.append({"jsonrpc": "2.0", "method": "exit"})
    return b"".join(frame(message) for message in messages)


def run_showmap(showmap: Path, binary: Path, target_args: list[str], data: bytes, output: Path) -> tuple[int, int]:
    proc = subprocess.run(
        [str(showmap), "-q", "-o", str(output), "--", str(binary), *target_args],
        input=data,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    edges = 0
    if output.exists():
        for line in output.read_text().splitlines():
            if ":" in line:
                edges += 1
    return proc.returncode, edges


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--showmap", required=True)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--arg", action="append", default=[])
    parser.add_argument("--output", help="Write JSON report here")
    args = parser.parse_args()

    showmap = Path(args.showmap).resolve()
    binary = Path(args.executable).resolve()
    target_args = args.arg or ["lsp"]
    report: dict = {"executable": str(binary), "target_args": target_args}
    with tempfile.TemporaryDirectory(prefix="mux-coverage-probe-") as temp:
        temp_path = Path(temp)
        workspace = temp_path / "workspace"
        workspace.mkdir()
        init_data = transcript(workspace, full=False)
        full_data = transcript(workspace, full=True)
        init_code, init_edges = run_showmap(showmap, binary, target_args, init_data, temp_path / "init.map")
        full_code, full_edges = run_showmap(showmap, binary, target_args, full_data, temp_path / "full.map")
        report.update(
            {
                "initialize_only": {"exit_code": init_code, "edges": init_edges},
                "initialize_plus_requests": {"exit_code": full_code, "edges": full_edges},
                "edge_delta": full_edges - init_edges,
                "changed": full_edges > init_edges,
            }
        )

    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n")
    return 0 if report["changed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
