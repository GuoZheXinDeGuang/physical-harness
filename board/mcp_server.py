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
from board import vault as bv
from scripts.brief_drop import drop


#: The default routing session -- the resident runtime cockpit always brings up.
#: submit_brief/run_task and the session-addressed reads fall back to it, so the
#: pre-routing single-runtime behavior is byte-identical when no session is named.
_DEFAULT_SESSION = "session-main"


class _Cfg:
    """Server config: the runs/ tree, the two markdown feeds, the default routing
    session, and its inbox that submit_brief drops into. Set once by configure()
    (main, or a test); the tools read it. Read defaults mirror rsi_board."""

    runs = Path("runs").resolve()
    status = runs.parent / "STATUS.md"
    progress = runs.parent / "progress.md"
    session = _DEFAULT_SESSION
    inbox = runs / _DEFAULT_SESSION / "inbox"


def configure(runs, status=None, progress=None, inbox=None,
              session=_DEFAULT_SESSION) -> None:
    _Cfg.runs = Path(runs).resolve()
    _Cfg.status = Path(status).resolve() if status else _Cfg.runs.parent / "STATUS.md"
    _Cfg.progress = Path(progress).resolve() if progress else _Cfg.runs.parent / "progress.md"
    _Cfg.session = session
    _Cfg.inbox = Path(inbox).resolve() if inbox else _Cfg.runs / session / "inbox"


def _route_inbox(session: str) -> Path | None:
    """The inbox a per-call ``session`` routes into, or ``None`` for an unknown
    one. The server's default session returns the configured inbox verbatim (zero
    behavior change plus the configure(inbox=) override tests rely on, and no
    is_session gate so a first submit can precede the runtime's first boot); any
    OTHER session is validated against runs/ (a real booted session, ``../``
    rejected) through the shared board.store guard and routed to <session>/inbox."""
    if session == _Cfg.session:
        return _Cfg.inbox
    return bs.session_inbox(_Cfg.runs, session)


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
def session(name: str = _DEFAULT_SESSION) -> dict:
    """One runtime session: note payloads by kind plus chain_ok (hash-chain verify).
    ``name`` defaults to the resident session-main; pass another to read a second
    runtime's session (a ``../`` name is rejected by the shared guard)."""
    path = bs.safe_child(_Cfg.runs, name, bs.is_session)
    return bs.read_session(path) if path else {"error": "unknown session"}


@mcp.tool()
def session_progress(name: str = _DEFAULT_SESSION) -> dict:
    """One session's mission-progress aggregate over its task.plan_complete rows
    (task tallies, total replans/faults, stage pass-rate, latest task tree).
    ``name`` defaults to session-main."""
    path = bs.safe_child(_Cfg.runs, name, bs.is_session)
    return bs.session_progress(path) if path else {"error": "unknown session"}


@mcp.tool()
def runtime_status(name: str = _DEFAULT_SESSION) -> dict | None:
    """One runtime session's LIVE status (pid/render/mode/boot_ts/display), or null
    when it has not booted since the file existed. Live state, not sealed evidence.
    ``name`` defaults to session-main."""
    path = bs.safe_child(_Cfg.runs, name, bs.is_session)
    return bs.read_runtime_status(path) if path else {"error": "unknown session"}


@mcp.tool()
def runtime_frame(name: str = _DEFAULT_SESSION) -> dict:
    """One runtime session's LIVE viewport frame (runs/<session>/frame.jpg,
    dumped offscreen by the frames overlay while a task runs): {jpeg_b64, ts,
    age_s}, or {"error": "no frame"} when none has been dumped. Live state,
    never chain evidence. ``name`` defaults to session-main."""
    path = bs.safe_child(_Cfg.runs, name, bs.is_session)
    return bs.read_runtime_frame(path) if path else {"error": "unknown session"}


@mcp.tool()
def runtime_events(name: str = _DEFAULT_SESSION, after_seq: int = 0) -> dict:
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
def vault() -> dict:
    """The Skill Vault: the whole typed wiki graph (skill/package/capability nodes
    + the 9-relation edge vocabulary), a deterministic fold over sealed runs/ +
    manifests. Read it before planning: which tasks have a *promoted* skill."""
    return bv.build_graph(_Cfg.runs)


@mcp.tool()
def vault_node(id: str) -> dict:
    """One vault node as a wiki page: the node plus its ``out`` edges and
    ``backlinks`` (in-edges). Unknown id -> {"error": "unknown node"}."""
    return bv.node(bv.build_graph(_Cfg.runs), id)


@mcp.tool()
def vault_neighbors(id: str, relation: str | None = None) -> dict:
    """Adjacency (both directions) for one vault node, optionally one ``rel``."""
    return bv.neighbors(bv.build_graph(_Cfg.runs), id, relation)


@mcp.tool()
def submit_brief(brief: dict, session: str = _DEFAULT_SESSION) -> dict:
    """Drop a brief into a runtime session's inbox for it to claim.

    A brief is a pure selector+budgets, e.g.
    ``{"kind":"task","task":"stack","seed":90000}``. This tool does NO brief
    validation and names NO provider: it only performs the shared atomic drop
    (brief_drop.drop -- temp write + os.replace) so the runtime never claims a
    half-written brief. The resident runtime re-validates ``_BRIEF_KEYS``
    server-side on claim and stays the SOLE authority, so an injected extra key
    rides through unchanged and hard-fails to failed/ there -- the MCP seam puts
    the LLM in front of the inbox but not in front of the guard.

    ``session`` only ROUTES (which runtime's inbox); it never touches the brief.
    It defaults to the resident session-main, so single-runtime behavior is
    unchanged. A non-default session is validated against runs/ (a real booted
    session, ``../`` rejected); an unknown one returns ``{"error": ...}``.
    """
    inbox = _route_inbox(session)
    if inbox is None:
        return {"error": f"unknown session {session!r}"}
    inbox.mkdir(parents=True, exist_ok=True)
    name = f"brief-{uuid.uuid4().hex}.json"
    drop(inbox, name, json.dumps(brief))
    return {"submitted": name, "inbox": str(inbox)}


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
             max_actuations: int | None = None, timeout_s: float = 120,
             session: str = _DEFAULT_SESSION) -> dict:
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

    ``session`` routes to a second runtime (default session-main); an unknown one
    returns ``{"status": "error"}`` before any submit.
    """
    inbox = _route_inbox(session)
    if inbox is None:
        return {"status": "error", "error": f"unknown session {session!r}"}
    brief = {"kind": "task", "task": task, "seed": seed}
    if max_replans is not None:
        brief["max_replans"] = max_replans
    if max_actuations is not None:
        brief["max_actuations"] = max_actuations
    session_dir = inbox.parent
    baseline = max((r.get("seq", -1) for r in bs.chain_rows(session_dir)), default=-1)
    brief_id = submit_brief(brief, session=session)["submitted"]

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
    parser.add_argument("--session", default=_DEFAULT_SESSION,
                        help="default runtime session for submit_brief/run_task "
                             "when no per-call session is named (routes still "
                             "reach any other session by name)")
    args = parser.parse_args(argv)
    runs = args.runs.resolve()
    if not runs.is_dir():
        parser.error(f"runs directory not found: {runs}")
    configure(runs, args.status, args.progress, session=args.session)
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
