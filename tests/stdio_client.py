"""A minimal MCP client that speaks raw JSON-RPC over stdio.

The server tests use this instead of the SDK's client so they check the
actual bytes an editor would exchange with ripple. Run it by hand too:

    python tests/stdio_client.py bench/fixtures/shop tax_for
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading


class StdioClient:
    def __init__(self, argv: list[str], env: dict | None = None, timeout: float = 30):
        self.timeout = timeout
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, **(env or {})},
        )
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.next_id = 0

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.put(line)

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        while True:
            message = json.loads(self.lines.get(timeout=self.timeout))
            if message.get("id") == self.next_id:
                return message

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def _send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def initialize(self) -> dict:
        reply = self.request(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "stdio-client", "version": "0"}},
        )
        self.notify("notifications/initialized")
        return reply

    def close(self) -> int:
        self.proc.stdin.close()
        try:
            return self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait()


if __name__ == "__main__":
    repo, symbol = sys.argv[1], sys.argv[2]
    client = StdioClient([sys.executable, "-m", "ripple", "serve", "--repo", repo])
    client.initialize()
    print(json.dumps(client.request("tools/list")["result"], indent=2))
    call = client.request("tools/call", {"name": "impact", "arguments": {"symbol": symbol}})
    print(json.dumps(call["result"], indent=2))
    print("server exit code:", client.close())
