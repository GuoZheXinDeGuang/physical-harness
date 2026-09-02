"""Fake MCP segment service on stdio (newline-delimited JSON-RPC): answers
``initialize`` and ``tools/call run_segment``; appends every run_segment
argument dict to the JSON list at ``$PH_FAKE_MCP_LOG``; ok=false when
``args.fail`` is true. Chatty like a real server: every reply is preceded by a
``notifications/message`` log line in the SAME flush. Run as a script by the executor_mcp_segment card."""

import json
import os
import sys
from pathlib import Path


def _log(args: dict) -> None:
    path = os.environ.get("PH_FAKE_MCP_LOG")
    if path:
        p = Path(path)
        calls = json.loads(p.read_text()) if p.exists() else []
        p.write_text(json.dumps(calls + [args]))


def main() -> None:
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:  # notification
            continue
        method, params = msg.get("method"), msg.get("params") or {}
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}},
                      "serverInfo": {"name": "ph-fake-mcp-segment", "version": "0"}}
        elif method == "tools/call" and params.get("name") == "run_segment":
            args = params.get("arguments") or {}
            _log(args)
            out = {"ok": not (args.get("args") or {}).get("fail", False),
                   "diagnostics": {"served": "fake", "skill": args.get("skill")}}
            result = {"content": [{"type": "text", "text": json.dumps(out)}],
                      "structuredContent": out, "isError": False}
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                                         "error": {"code": -32601, "message": f"unknown {method}"}}) + "\n")
            sys.stdout.flush()
            continue
        note = {"jsonrpc": "2.0", "method": "notifications/message",
                "params": {"level": "info", "data": f"handled {method}"}}
        sys.stdout.write(json.dumps(note) + "\n"
                         + json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
