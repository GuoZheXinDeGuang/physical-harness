#!/usr/bin/env python3
"""Rung-0 smoke MCP server: one `echo` tool, stdlib only, stdio transport.

Its only job is to prove the dsh cockpit can register a harness MCP tool
(`mcp__physical-harness__echo`) before rung 1 stands up the real read-only
server. Zero dependencies on purpose -- the `mcp` SDK is deferred to rung 1,
where the design actually scopes it. Speaks newline-delimited JSON-RPC 2.0,
the MCP stdio framing.

    python3 profiles/dsh/echo_server.py            # run as an MCP stdio server
    python3 profiles/dsh/echo_server.py --selftest # assert the dispatch works
"""

from __future__ import annotations

import json
import sys

TOOL = {
    "name": "echo",
    "description": "Echo back the given text -- rung-0 registration smoke test.",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "text to echo"}},
        "required": ["text"],
    },
}


def handle(msg: dict) -> dict | None:
    """Dispatch one JSON-RPC message; return a response dict, or None for notifications."""
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        # Echo the client's requested protocol version back for max compatibility.
        proto = (msg.get("params") or {}).get("protocolVersion", "2024-11-05")
        return _ok(mid, {
            "protocolVersion": proto,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "physical-harness", "version": "0.0.1"},
        })
    if method == "tools/list":
        return _ok(mid, {"tools": [TOOL]})
    if method == "tools/call":
        params = msg.get("params") or {}
        if params.get("name") != "echo":
            return _err(mid, -32602, f"unknown tool {params.get('name')!r}")
        text = (params.get("arguments") or {}).get("text", "")
        return _ok(mid, {"content": [{"type": "text", "text": str(text)}]})
    if method == "ping":
        return _ok(mid, {})
    if method is not None and mid is None:
        return None  # notification (e.g. notifications/initialized): no reply
    return _err(mid, -32601, f"method not found: {method}")


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0


def _selftest() -> int:
    assert handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18"}})["result"][
        "protocolVersion"] == "2025-06-18"
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == ["echo"]
    call = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": "echo", "arguments": {"text": "pong"}}})
    assert call["result"]["content"][0]["text"] == "pong"
    assert "error" in handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": "nope", "arguments": {}}})
    print("selftest ok")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv[1:] else serve())
