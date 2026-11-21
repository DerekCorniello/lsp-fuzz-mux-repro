#!/usr/bin/env python3
"""Direct LSP protocol smoke test for `mux lsp`.

This deliberately does not use LSPFuzz. It drives the server over stdio with
LSP JSON-RPC framing, exercises every request type advertised by the server,
and verifies a clean shutdown/exit sequence.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
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


class LspClient:
    def __init__(self, executable: Path, args: list[str], cwd: Path):
        self.proc = subprocess.Popen(
            [str(executable), *args],
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        assert self.proc.stderr is not None
        self.buffer = b""
        self.next_id = 1
        self.notifications: list[dict] = []
        self.responses: dict[int, dict] = {}

    def send(self, message: dict) -> None:
        body = json.dumps(message, separators=(",", ":")).encode()
        payload = f"Content-Length: {len(body)}\r\n\r\n".encode() + body
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> int:
        request_id = self.next_id
        self.next_id += 1
        self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params if params is not None else {},
            }
        )
        return request_id

    def notify(self, method: str, params: dict | None = None) -> None:
        self.send(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params if params is not None else {},
            }
        )

    def _read_exact(self, count: int, deadline: float) -> bytes | None:
        while len(self.buffer) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if not ready:
                return None
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                return None
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def receive(self, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while True:
            separator = self.buffer.find(b"\r\n\r\n")
            if separator < 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
                if not ready:
                    return None
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    return None
                self.buffer += chunk
                continue
            header = self.buffer[:separator].decode("ascii", errors="replace")
            length = None
            for line in header.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
            if length is None:
                raise RuntimeError(f"missing Content-Length in {header!r}")
            body_start = separator + 4
            body = self._read_exact_header_body(body_start, length, deadline)
            if body is None:
                return None
            self.buffer = self.buffer[body_start + length :]
            message = json.loads(body)
            if "id" in message and "method" not in message:
                self.responses[int(message["id"])] = message
            else:
                self.notifications.append(message)
            return message

    def _read_exact_header_body(
        self, body_start: int, length: int, deadline: float
    ) -> bytes | None:
        while len(self.buffer) < body_start + length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if not ready:
                return None
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                return None
            self.buffer += chunk
        return self.buffer[body_start : body_start + length]

    def wait_for_response(self, request_id: int, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while request_id not in self.responses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if self.receive(remaining) is None:
                return None
        return self.responses[request_id]

    def drain(self, quiet_seconds: float = 0.25, total_timeout: float = 10.0) -> None:
        deadline = time.monotonic() + total_timeout
        while time.monotonic() < deadline:
            if self.receive(quiet_seconds) is None:
                return

    def stop(self) -> int:
        if self.proc.poll() is not None:
            return self.proc.returncode
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            return self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            return self.proc.wait(timeout=5)


def point(line: int, character: int) -> dict:
    return {"line": line, "character": character}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True)
    parser.add_argument("--arg", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--output", help="Write a JSON report here")
    args = parser.parse_args()

    executable = Path(args.executable).resolve()
    if not executable.exists():
        print(f"error: executable not found: {executable}", file=sys.stderr)
        return 2

    report: dict = {
        "executable": str(executable),
        "arguments": args.arg,
        "requests": {},
        "notifications": 0,
        "diagnostics": 0,
        "errors": [],
    }

    workspace = Path(tempfile.mkdtemp(prefix="mux-lsp-smoke-"))
    main_path = workspace / "main.mux"
    lib_path = workspace / "lib.mux"
    main_path.write_text(MAIN_SOURCE)
    lib_path.write_text(LIB_SOURCE)
    main_uri = main_path.as_uri()
    lib_uri = lib_path.as_uri()
    workspace_uri = workspace.as_uri() + "/"

    client = LspClient(executable, args.arg or ["lsp"], workspace)
    try:
        init_id = client.request(
            "initialize",
            {
                "processId": os.getpid(),
                "clientInfo": {"name": "mux-lsp-smoke"},
                "rootUri": workspace_uri,
                "workspaceFolders": [{"uri": workspace_uri, "name": "smoke"}],
                "capabilities": {
                    "workspace": {
                        "workspaceEdit": {"documentChanges": True},
                        "didChangeWatchedFiles": {"dynamicRegistration": True},
                    },
                    "textDocument": {
                        "completion": {"completionItem": {"snippetSupport": True}},
                        "publishDiagnostics": {},
                    },
                },
            },
        )
        init_response = client.wait_for_response(init_id, args.timeout)
        if init_response is None:
            report["errors"].append("no initialize response")
            return finish(client, report, args, 1)
        if "error" in init_response:
            report["errors"].append(f"initialize failed: {init_response['error']}")
            return finish(client, report, args, 1)
        capabilities = init_response.get("result", {}).get("capabilities", {})
        report["capabilities"] = capabilities

        client.notify("initialized", {})

        client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": main_uri,
                    "languageId": "mux",
                    "version": 1,
                    "text": MAIN_SOURCE,
                }
            },
        )
        client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": lib_uri,
                    "languageId": "mux",
                    "version": 1,
                    "text": LIB_SOURCE,
                }
            },
        )

        # Give the analysis worker time to publish diagnostics for both files.
        client.drain(quiet_seconds=0.5, total_timeout=10.0)
        report["diagnostics"] = sum(
            1
            for message in client.notifications
            if message.get("method") == "textDocument/publishDiagnostics"
        )

        text_document = {"uri": main_uri}
        helper_position = point(11, 13)
        increment_position = point(8, 26)
        counter_position = point(0, 6)

        requests: list[tuple[str, dict]] = [
            ("textDocument/formatting", {"textDocument": text_document, "options": {"tabSize": 4, "insertSpaces": True}}),
            ("textDocument/documentSymbol", {"textDocument": text_document}),
            ("textDocument/definition", {"textDocument": text_document, "position": counter_position}),
            ("textDocument/references", {"textDocument": text_document, "position": counter_position, "context": {"includeDeclaration": True}}),
            ("textDocument/rename", {"textDocument": text_document, "position": counter_position, "newName": "RenamedCounter"}),
            ("textDocument/hover", {"textDocument": text_document, "position": increment_position}),
            ("textDocument/completion", {"textDocument": text_document, "position": increment_position}),
            ("textDocument/semanticTokens/full", {"textDocument": text_document}),
            ("textDocument/signatureHelp", {"textDocument": text_document, "position": helper_position}),
            ("textDocument/codeAction", {"textDocument": text_document, "range": {"start": point(0, 0), "end": point(1, 0)}, "context": {"diagnostics": []}}),
            ("textDocument/inlayHint", {"textDocument": text_document, "range": {"start": point(0, 0), "end": point(20, 0)}}),
            ("textDocument/foldingRange", {"textDocument": text_document}),
        ]

        pending: dict[int, str] = {}
        for method, params in requests:
            request_id = client.request(method, params)
            pending[request_id] = method
            report["requests"][method] = {"id": request_id, "status": "sent"}

        deadline = time.monotonic() + args.timeout
        while pending and time.monotonic() < deadline:
            message = client.receive(deadline - time.monotonic())
            if message is None:
                break
            message_id = message.get("id")
            if message_id in pending:
                method = pending.pop(int(message_id))
                report["requests"][method] = {
                    "id": int(message_id),
                    "status": "error" if "error" in message else "ok",
                    "response": message,
                }

        for request_id, method in pending.items():
            report["requests"][method] = {
                "id": request_id,
                "status": "no-response",
            }
            report["errors"].append(f"no response for {method}")

        report["notifications"] = len(client.notifications)

        shutdown_id = client.request("shutdown", None)
        shutdown_response = client.wait_for_response(shutdown_id, args.timeout)
        if shutdown_response is None:
            report["errors"].append("no shutdown response")
            return finish(client, report, args, 1)
        client.notify("exit")

        try:
            return_code = client.proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            report["errors"].append("server did not exit after exit notification")
            return_code = client.stop()
        report["exit_code"] = return_code
        report["stderr"] = client.proc.stderr.read().decode(errors="replace")
        if return_code != 0:
            report["errors"].append(f"server exited with {return_code}")

        status = 0 if not report["errors"] else 1
        return finish(client, report, args, status)
    finally:
        if client.proc.poll() is None:
            client.stop()


def finish(client: LspClient, report: dict, args, status: int) -> int:
    report["ok"] = status == 0
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
