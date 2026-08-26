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

from board import cards as bc
from board import store as bs
from board import vault as bv


def _read(path: Path) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def dispatch(fn: str, name: str | None, runs: Path, status: Path, progress: Path,
             after: int = 0, relation: str | None = None, after_ts: float = 0.0,
             wait_ms: int = 0):
    """Return the same object the matching board/mcp_server.py tool returns.

    Raises KeyError for an unknown fn and ValueError for a rejected name, so
    main() can map both to an ``{"error": ...}`` line with a nonzero exit while
    every valid call is a bare board.store passthrough.
    """
    if fn == "list_stores":
        return bs.list_stores(runs)
    if fn == "cards":
        return bc.list_cards()
    if fn == "vault":
        return bv.build_graph(runs)
    if fn == "vault_node":
        return bv.node(bv.build_graph(runs), name or "")
    if fn == "vault_neighbors":
        return bv.neighbors(bv.build_graph(runs), name or "", relation)
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
    # Session-addressed reads default to session-main when no name is given (the
    # resident runtime), so a caller can omit it; an explicit name still routes
    # to that session, and a ``../`` name is rejected by the shared guard.
    if fn == "session":
        path = bs.safe_child(runs, name or "session-main", bs.is_session)
        if path is None:
            raise ValueError("unknown session")
        return bs.read_session(path)
    if fn == "runtime_status":
        path = bs.safe_child(runs, name or "session-main", bs.is_session)
        if path is None:
            raise ValueError("unknown session")
        return bs.read_runtime_status(path)
    if fn == "runtime_frame":
        path = bs.safe_child(runs, name or "session-main", bs.is_session)
        if path is None:
            raise ValueError("unknown session")
        return bs.read_runtime_frame(path, after_ts, wait_ms)
    if fn == "runtime_events":
        path = bs.safe_child(runs, name or "session-main", bs.is_session)
        if path is None:
            raise ValueError("unknown session")
        return bs.read_runtime_events(path, after)
    if fn == "session_progress":
        path = bs.safe_child(runs, name or "session-main", bs.is_session)
        if path is None:
            raise ValueError("unknown session")
        return bs.session_progress(path)
    raise KeyError(fn)


def serve(stdin, stdout, runs: Path, status: Path, progress: Path) -> int:
    """Resident line-JSON loop over the SAME dispatch (``storecli serve``).

    One request object per line ({"fn", "name"?, "after"?, "relation"?,
    "after_ts"?, "wait_ms"?}), one JSON reply line per request, strictly in
    order, flushed. The ph-station bridge keeps one of these alive for the
    取景窗 long poll: the ~60ms interpreter+import spawn cost was the measured
    browser fps ceiling, and this moves it off the per-frame path. Errors map
    to the same ``{"error": ...}`` dicts as one-shot mode and NEVER end the
    loop; EOF does.
    """
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            result = dispatch(req.get("fn", ""), req.get("name"), runs, status, progress,
                              int(req.get("after", 0)), req.get("relation"),
                              float(req.get("after_ts", 0.0)), int(req.get("wait_ms", 0)))
        except KeyError:
            result = {"error": f"unknown fn: {req.get('fn', '')}"}
        except Exception as exc:  # bad JSON / rejected name / anything: reply, keep serving
            result = {"error": str(exc)}
        print(json.dumps(result), file=stdout, flush=True)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("fn", help="serve|list_stores|store|heldout|sessions|session|session_progress|runtime_status|runtime_frame|runtime_events|ledger|rounds|cards|vault|vault_node|vault_neighbors")
    parser.add_argument("name", nargs="?", default=None, help="store/session name, or vault node id for vault_node/vault_neighbors")
    parser.add_argument("--relation", default=None, help="vault_neighbors: restrict adjacency to one rel")
    parser.add_argument("--runs", type=Path, default=Path("runs"), help="campaign runs directory (default: runs)")
    parser.add_argument("--status", type=Path, default=None, help="STATUS.md for the ledger (default: <runs>/../STATUS.md)")
    parser.add_argument("--progress", type=Path, default=None, help="progress.md for the rounds feed (default: <runs>/../progress.md)")
    parser.add_argument("--after", type=int, default=0, help="runtime_events cursor: return only events with seq > AFTER")
    parser.add_argument("--after-ts", type=float, default=0.0, help="runtime_frame cursor: the ts last displayed; unchanged file -> short {unchanged} reply")
    parser.add_argument("--wait-ms", type=int, default=0, help="runtime_frame long poll: block up to WAIT_MS for the frame to change past --after-ts before answering (capped board-side)")
    args = parser.parse_args(argv)
    runs = args.runs.resolve()
    status = args.status.resolve() if args.status else runs.parent / "STATUS.md"
    progress = args.progress.resolve() if args.progress else runs.parent / "progress.md"
    if args.fn == "serve":
        return serve(sys.stdin, sys.stdout, runs, status, progress)
    try:
        result = dispatch(args.fn, args.name, runs, status, progress, args.after, args.relation,
                          args.after_ts, args.wait_ms)
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
