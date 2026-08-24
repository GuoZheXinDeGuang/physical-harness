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
import time
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
def session_progress(name: str) -> dict:
    """One session's mission-progress aggregate over its task.plan_complete rows
    (task tallies, total replans/faults, stage pass-rate, latest task tree)."""
    path = bs.safe_child(_Cfg.runs, name, bs.is_session)
    return bs.session_progress(path) if path else {"error": "unknown session"}


@mcp.tool()
def runtime_status(name: str) -> dict | None:
    """One runtime session's LIVE status (pid/render/mode/boot_ts/display), or null
    when it has not booted since the file existed. Live state, not sealed evidence."""
    path = bs.safe_child(_Cfg.runs, name, bs.is_session)
    return bs.read_runtime_status(path) if path else {"error": "unknown session"}


@mcp.tool()
def runtime_events(name: str, after_seq: int = 0) -> dict:
    """One runtime session's OPERATIONAL event feed (runtime_events.jsonl):
    events with seq > after_seq plus last_seq. last_seq < after_seq means the
    runtime re-booted (feed truncated); reset the cursor to 0 and re-read.
    Live progress (task_claimed/plan_built/node/stage/replan), never chain
    evidence."""
    path = bs.safe_child(_Cfg.runs, name, bs.is_session)
    return bs.read_runtime_events(path, after_seq) if path else {"error": "unknown session"}


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


def _outcome(status: str, brief_id: str, session_dir: Path, baseline: int,
             elapsed: float) -> dict:
    """Copy THIS brief's sealed chain row into a compact result. The runtime is
    the SOLE authority on success -- fields are copied verbatim, nothing here
    interprets them. failed/ carries a runtime.task_error whose ``brief`` matches
    (unambiguous); done/ carries a task.plan_complete, which carries no brief_id.
    ponytail: attribute the done row as the last plan_complete after our baseline
    seq -- exact for serial briefs (the interactive MCP case). Add a brief_id to
    plan_complete and match it if concurrent submitters ever misattribute."""
    new = [r for r in bs.chain_rows(session_dir) if r.get("seq", -1) > baseline]
    if status == "failed":
        row = next((r for r in reversed(new)
                    if r["kind"] == "runtime.task_error"
                    and r["data"].get("brief") == brief_id), None)
        out = {"status": "failed", "brief_id": brief_id, "elapsed_s": elapsed}
        if row is not None:
            out["error"] = row["data"].get("error")
            out["chain_seq"] = row["seq"]
        return out
    row = next((r for r in reversed(new) if r["kind"] == "task.plan_complete"), None)
    out = {"status": "done", "brief_id": brief_id, "elapsed_s": elapsed}
    if row is not None:
        d = row["data"]
        out["success"] = d.get("success")
        out["nodes"] = d.get("nodes")
        out["chain_seq"] = row["seq"]
        if d.get("faults"):
            out["failure"] = d["faults"]
    return out


@mcp.tool()
def run_task(task: str, seed: int, max_replans: int | None = None,
             max_actuations: int | None = None, timeout_s: float = 120) -> dict:
    """Submit a task brief and BLOCK until the resident runtime finishes it.

    Kills the submit -> bash-poll -> read-session ceremony: one call drops the
    brief (the SAME atomic path submit_brief uses -- no second implementation),
    watches the runtime's own inbox/processing -> done/|failed/ protocol
    (read-only, ~0.5s poll), then returns the sealed chain row for THIS brief.

    The runtime stays the SOLE authority: this does NO _BRIEF_KEYS validation
    (an injected key still hard-fails in the runtime, surfacing here as
    status:failed) and copies task.plan_complete / runtime.task_error fields
    verbatim -- it never decides success itself. Returns
    ``{status: done|failed|timeout, brief_id, ...}``: done carries
    success/nodes (+failure faults when the goal was missed), failed carries
    error, timeout carries guidance (the brief keeps running; poll the session
    later with session()).
    """
    brief = {"kind": "task", "task": task, "seed": seed}
    if max_replans is not None:
        brief["max_replans"] = max_replans
    if max_actuations is not None:
        brief["max_actuations"] = max_actuations
    session_dir = _Cfg.inbox.parent
    baseline = max((r.get("seq", -1) for r in bs.chain_rows(session_dir)), default=-1)
    brief_id = submit_brief(brief)["submitted"]

    done_dir, failed_dir = session_dir / "done", session_dir / "failed"
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        if (done_dir / brief_id).exists():
            return _outcome("done", brief_id, session_dir, baseline,
                            round(time.monotonic() - start, 3))
        if (failed_dir / brief_id).exists():
            return _outcome("failed", brief_id, session_dir, baseline,
                            round(time.monotonic() - start, 3))
        time.sleep(0.5)
    return {"status": "timeout", "brief_id": brief_id,
            "elapsed_s": round(time.monotonic() - start, 3),
            "guidance": f"brief still running; poll session({session_dir.name!r}) later"}


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
