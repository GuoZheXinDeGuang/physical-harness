#!/usr/bin/env python3
"""CLI face over the RSI board's parse layer (board/store.py).

The charter's "MCP 与 CLI 是同一函数的两个调用面": board/mcp_server.py serves the
LLM/chat over MCP; this serves the ph-station cockpit's read panels over stdout.
Both are one-call passthroughs into the SAME board.store functions, so the panel
renders the byte-identical dict the LLM gets -- no second statistics layer, no
reinterpretation. The fork host bridge (packages/host/dsh-ph-board) execFiles
this and JSON.parses stdout verbatim.

Name-addressed reads (store/heldout/session) go through board.store.safe_child,
the one audited traversal guard, so a ``../`` name can never escape runs_dir.

    python -m board.storecli list_stores --runs runs/     # -> JSON on stdout
    python -m board.storecli store stack-g1 --runs runs/   # name-addressed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from board import store as bs


def _read(path: Path) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def dispatch(fn: str, name: str | None, runs: Path, status: Path, progress: Path):
    """Return the same object the matching board/mcp_server.py tool returns.

    Raises KeyError for an unknown fn and ValueError for a rejected name, so
    main() can map both to an ``{"error": ...}`` line with a nonzero exit while
    every valid call is a bare board.store passthrough.
    """
    if fn == "list_stores":
        return bs.list_stores(runs)
    if fn == "sessions":
        return bs.discover_sessions(runs)
    if fn == "ledger":
        return bs.parse_ledger(_read(status))
    if fn == "rounds":
        return bs.parse_rounds(_read(progress))
    if fn == "store":
        path = bs.safe_child(runs, name or "", bs.is_store)
        if path is None:
            raise ValueError("unknown store")
        return bs.store_detail(path)
    if fn == "heldout":
        path = bs.safe_child(runs, name or "", bs.is_store)
        if path is None:
            raise ValueError("unknown store")
        return bs.heldout_blocks(runs, name)
    if fn == "session":
        path = bs.safe_child(runs, name or "", bs.is_session)
        if path is None:
            raise ValueError("unknown session")
        return bs.read_session(path)
    raise KeyError(fn)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("fn", help="list_stores|store|heldout|sessions|session|ledger|rounds")
    parser.add_argument("name", nargs="?", default=None, help="store/session name for the name-addressed fns")
    parser.add_argument("--runs", type=Path, default=Path("runs"), help="campaign runs directory (default: runs)")
    parser.add_argument("--status", type=Path, default=None, help="STATUS.md for the ledger (default: <runs>/../STATUS.md)")
    parser.add_argument("--progress", type=Path, default=None, help="progress.md for the rounds feed (default: <runs>/../progress.md)")
    args = parser.parse_args(argv)
    runs = args.runs.resolve()
    status = args.status.resolve() if args.status else runs.parent / "STATUS.md"
    progress = args.progress.resolve() if args.progress else runs.parent / "progress.md"
    try:
        result = dispatch(args.fn, args.name, runs, status, progress)
    except KeyError:
        print(json.dumps({"error": f"unknown fn: {args.fn}"}))
        return 2
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 3
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
