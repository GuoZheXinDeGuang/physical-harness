"""MCP segment executor: ``provider(command=[...])`` spawns an MCP server on
stdio (newline-delimited JSON-RPC 2.0, the MCP stdio transport) and runs a
sub-goal through its ``run_segment`` tool ``{skill, args, sigma, deadline_s}``
-> ``{ok, diagnostics}``. Handshake = the ``initialize`` serverInfo, sealed in
the harness.skill_executor shape. Zero deps: the client is the ~40 lines below.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from typing import Any

from harness.skill_executor import normalize_handshake

REF = "plugins.executor_mcp_segment:provider"
PROTOCOL = "2025-06-18"


def _plain(o: Any):
    """JSON default: numpy scalars/arrays in sigma, anything else by str."""
    return o.tolist() if hasattr(o, "tolist") else str(o)


class _Rpc:
    def __init__(self, command: list[str]):
        self.command = list(command)
        self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, bufsize=1)
        self.n = 0
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        # Reader thread, not select(): one server flush can carry a notification
        # AND the reply, and readline() buffers the rest past the fd's readiness.
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)  # EOF

    def _send(self, msg: dict) -> None:
        self.proc.stdin.write(json.dumps(msg, default=_plain) + "\n")
        self.proc.stdin.flush()

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def call(self, method: str, params: dict, timeout: float) -> dict:
        self.n += 1
        self._send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:  # server-initiated notifications/requests are skipped, not answered
            try:
                line = self.lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                self.close()
                raise TimeoutError(f"mcp {self.command}: no reply to {method} in {timeout}s")
            if line is None:
                raise RuntimeError(f"mcp {self.command} closed the pipe during {method}")
            msg = json.loads(line)
            if msg.get("id") == self.n:
                break
        if "error" in msg:
            raise RuntimeError(f"mcp {method}: {msg['error']}")
        return msg["result"]

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class SegmentDriver:
    """One per segment (workload mounts a fresh driver per node); the connection
    is the provider's. SegmentExecutor: ``handshake()`` + ``run(spec, deadline_s)``."""

    def __init__(self, rpc: _Rpc, server_info: dict):
        self.rpc, self.server_info = rpc, server_info

    def handshake(self) -> dict:
        return normalize_handshake("mcp", REF, self.server_info)

    def run(self, spec: dict, deadline_s: float) -> dict:
        res = self.rpc.call("tools/call", {"name": "run_segment",
                                           "arguments": {**spec, "deadline_s": deadline_s}},
                            timeout=deadline_s + 5.0)
        out = res.get("structuredContent") or json.loads(res["content"][0]["text"])
        return {"ok": bool(out.get("ok")) and not res.get("isError", False),
                "diagnostics": dict(out.get("diagnostics") or {})}


class Provider:
    def __init__(self, command: list[str], **params: Any):
        self.command, self.params, self.rpc, self.server_info = list(command), params, None, {}

    def make_driver(self, spec: Any) -> SegmentDriver:
        if self.rpc is None:  # connect once per episode, like the pi05 factory
            self.rpc = _Rpc(self.command)
            init = self.rpc.call("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                "clientInfo": {"name": "physical-harness",
                                                               "version": "0"}}, timeout=30.0)
            self.rpc.notify("notifications/initialized")
            self.server_info = dict(init.get("serverInfo") or {})
        return SegmentDriver(self.rpc, self.server_info)

    def close(self) -> None:
        if self.rpc is not None:
            self.rpc.close()
            self.rpc = None


def provider(command: list[str], **params: Any) -> Provider:
    return Provider(command, **params)
