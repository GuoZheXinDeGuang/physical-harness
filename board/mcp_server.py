#!/usr/bin/env python3
"""Read-only MCP server over the RSI board's parse layer (board/store.py).

The dsh cockpit (round-95 adoption) is an MCP client; this is the harness-side
MCP *server* it connects to for live reads. Every tool is a one-call passthrough
into board.store -- the SAME pure parse layer scripts/rsi_board.py serves over
HTTP -- so the two surfaces return byte-identical dicts and rsi_board can be
retired (rung 4) without losing a view. Zero writes: runs/ is sealed evidence.

Name-addressed reads (store/heldout/session) go through board.store.safe_child,
the one audited traversal guard, so a ``../`` name can never escape runs_dir.

    .venv/bin/python board/mcp_server.py --runs runs/    # stdio MCP server
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server import MCPServer

from board import cards as bc
from board import store as bs
from scripts.brief_drop import drop


class _Cfg:
    """Server config: the runs/ tree, the two markdown feeds, and the resident
    runtime's session inbox that submit_brief drops into. Set once by configure()
    (main, or a test); the tools read it. Read defaults mirror rsi_board."""

    runs = Path("runs").resolve()
    status = runs.parent / "STATUS.md"
    progress = runs.parent / "progress.md"
    inbox = runs / "session-main" / "inbox"


def configure(runs, status=None, progress=None, inbox=None) -> None:
    _Cfg.runs = Path(runs).resolve()
    _Cfg.status = Path(status).resolve() if status else _Cfg.runs.parent / "STATUS.md"
    _Cfg.progress = Path(progress).resolve() if progress else _Cfg.runs.parent / "progress.md"
    _Cfg.inbox = Path(inbox).resolve() if inbox else _Cfg.runs / "session-main" / "inbox"


def _read(path: Path) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


mcp = MCPServer("physical-harness")


@mcp.tool()
def list_stores() -> list[dict]:
    """Every campaign store under runs/, newest first (summary cards)."""
    return bs.list_stores(_Cfg.runs)


@mcp.tool()
def store(name: str) -> dict:
    """Full structured view of one campaign store by name."""
    path = bs.safe_child(_Cfg.runs, name, bs.is_store)
    return bs.store_detail(path) if path else {"error": "unknown store"}


@mcp.tool()
def heldout(name: str) -> dict:
    """Multi-block held-out comparison for a campaign (its block + rescores)."""
    path = bs.safe_child(_Cfg.runs, name, bs.is_store)
    return bs.heldout_blocks(_Cfg.runs, name) if path else {"error": "unknown store"}


@mcp.tool()
def sessions() -> list[dict]:
    """Every runtime session under runs/, newest first (with chain badges)."""
    return bs.discover_sessions(_Cfg.runs)


@mcp.tool()
def session(name: str) -> dict:
    """One runtime session: note payloads by kind plus chain_ok (hash-chain verify)."""
    path = bs.safe_child(_Cfg.runs, name, bs.is_session)
    return bs.read_session(path) if path else {"error": "unknown session"}


@mcp.tool()
def runtime_status(name: str) -> dict | None:
    """One runtime session's LIVE status (pid/render/mode/boot_ts/display), or null
    when it has not booted since the file existed. Live state, not sealed evidence."""
    path = bs.safe_child(_Cfg.runs, name, bs.is_session)
    return bs.read_runtime_status(path) if path else {"error": "unknown session"}


@mcp.tool()
def ledger() -> list[dict]:
    """Seed-block burn map parsed from STATUS.md's budget section."""
    return bs.parse_ledger(_read(_Cfg.status))


@mcp.tool()
def rounds() -> list[dict]:
    """progress.md round sections, latest first."""
    return bs.parse_rounds(_read(_Cfg.progress))


@mcp.tool()
def list_cards() -> list[dict]:
    """Every installed 机箱 card (plugins/*/manifest.toml), manifest read as data."""
    return bc.list_cards()


@mcp.tool()
def submit_brief(brief: dict) -> dict:
    """Drop a brief into the resident runtime's session inbox for it to claim.

    A brief is a pure selector+budgets, e.g.
    ``{"kind":"task","task":"stack","seed":90000}``. This tool does NO
    validation and names NO provider: it only performs the shared atomic drop
    (brief_drop.drop -- temp write + os.replace) so the runtime never claims a
    half-written brief. The resident runtime re-validates ``_BRIEF_KEYS``
    server-side on claim and stays the SOLE authority, so an injected extra key
    rides through unchanged and hard-fails to failed/ there -- the MCP seam puts
    the LLM in front of the inbox but not in front of the guard.
    """
    _Cfg.inbox.mkdir(parents=True, exist_ok=True)
    name = f"brief-{uuid.uuid4().hex}.json"
    drop(_Cfg.inbox, name, json.dumps(brief))
    return {"submitted": name, "inbox": str(_Cfg.inbox)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--runs", type=Path, default=Path("runs"),
                        help="campaign runs directory (default: runs)")
    parser.add_argument("--status", type=Path, default=None,
                        help="STATUS.md for the seed ledger (default: <runs>/../STATUS.md)")
    parser.add_argument("--progress", type=Path, default=None,
                        help="progress.md for the rounds feed (default: <runs>/../progress.md)")
    parser.add_argument("--session", default="session-main",
                        help="resident runtime session whose inbox submit_brief drops into")
    args = parser.parse_args(argv)
    runs = args.runs.resolve()
    if not runs.is_dir():
        parser.error(f"runs directory not found: {runs}")
    configure(runs, args.status, args.progress, inbox=runs / args.session / "inbox")
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
