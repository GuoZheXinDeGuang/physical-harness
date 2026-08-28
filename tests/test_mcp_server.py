"""The read-only MCP server (board/mcp_server.py) is a pure passthrough.

Every tool is called directly (the SDK's @tool decorator returns the function
unchanged) and asserted byte-identical to the board.store call it wraps -- the
same parse layer scripts/rsi_board.py serves -- so retiring rsi_board (rung 4)
loses no view. Also: the session tool carries chain_ok, and the shared
board.store.safe_child guard rejects a `../` name.

Fixtures reuse the sibling test modules' builders (real CampaignStore shape and
a real SessionLog chain) so the passthrough is exercised against production
on-disk shapes, not a hand-mocked one.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_read_session import _session
from test_store import _campaign, _mkstore, _paired

from board import mcp_server as ms
from board import store as bs
from harness.events import SessionLog


def _same(a, b) -> bool:
    """Byte-identical over the wire == same json.dumps rsi_board would emit."""
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _fixture(tmp_path: Path) -> Path:
    runs = tmp_path / "runs"
    runs.mkdir()
    _campaign(runs / "stack-g1")
    rescore = {"block": 42200, "n": 200, "judgement": True,
               "paired": _paired(0.60, 0.70, 19, 0, 1e-8, n=200),
               "vs_blind": _paired(0.70, 0.78, 40, 5, 1e-6), "stage_attribution": None}
    _mkstore(runs / "stack-g1-rescore-42200", [("heldout_rescore", rescore)])
    _session(runs, "session-main")
    (tmp_path / "STATUS.md").write_text(
        "**PHASE 3 区块预算:** stack-g1 dev 41000-41580;\n"
        "**已烧:** held-out 42000-42199。\n")
    (tmp_path / "progress.md").write_text(
        "## Round 95 - 2026-08-23 - the cockpit\nbody one\n")
    ms.configure(runs, tmp_path / "STATUS.md", tmp_path / "progress.md")
    return runs


def test_tools_are_byte_identical_passthroughs(tmp_path):
    runs = _fixture(tmp_path)
    assert _same(ms.list_stores(), bs.list_stores(runs))
    assert _same(ms.store("stack-g1"), bs.store_detail(runs / "stack-g1"))
    assert _same(ms.heldout("stack-g1"), bs.heldout_blocks(runs, "stack-g1"))
    assert _same(ms.sessions(), bs.discover_sessions(runs))
    assert _same(ms.session("session-main"), bs.read_session(runs / "session-main"))
    # runtime_status: fixture session has no runtime_status.json, so this is both
    # the face-equivalence proof AND null-when-absent (both sides are null).
    assert _same(ms.runtime_status("session-main"), bs.read_runtime_status(runs / "session-main"))
    assert _same(ms.ledger(), bs.parse_ledger((tmp_path / "STATUS.md").read_text()))
    assert _same(ms.rounds(), bs.parse_rounds((tmp_path / "progress.md").read_text()))
    # non-trivial fixtures, so identity is not identity-of-empty
    assert ms.list_stores() and ms.heldout("stack-g1")["blocks"] and ms.ledger() and ms.rounds()


def test_session_tool_carries_chain_ok(tmp_path):
    _fixture(tmp_path)
    s = ms.session("session-main")
    assert s["chain_ok"] is True and s["name"] == "session-main"


def test_traversal_name_rejected_by_shared_guard(tmp_path):
    _fixture(tmp_path)
    assert ms.store("../etc") == {"error": "unknown store"}
    assert ms.store("..") == {"error": "unknown store"}
    assert ms.session("../session-main") == {"error": "unknown session"}
    assert ms.runtime_status("../session-main") == {"error": "unknown session"}
    # the guard itself: a traversal name never resolves outside runs_dir
    assert bs.safe_child(tmp_path / "runs", "../STATUS.md", lambda p: True) is None


# --- run_task: drop and hand back a HANDLE, never block --------------------
#
# The old tool blocked until done/|failed/ appeared and reported status:timeout
# otherwise -- which is what a long mission always hits while the runtime is
# happily finishing it. It now returns immediately; brief_status is the read.


def _run_task_session(tmp_path: Path) -> Path:
    """A fake session: one pre-existing chain row + an empty inbox."""
    session = tmp_path / "session-main"
    (session / "inbox").mkdir(parents=True)
    SessionLog(session / "session-log").append("runtime.boot", {"mode": "execution"})
    ms.configure(tmp_path, inbox=session / "inbox")
    return session


def test_run_task_returns_a_handle_without_waiting(tmp_path, live_runtime):
    session = _run_task_session(tmp_path)
    live_runtime(session)   # else the handle honestly comes back `stalled`

    res = ms.run_task("stack", 90000, max_replans=2)

    assert res["state"] == "queued" and res["task"] == "stack"
    assert res["queue_position"] == 1 and res["ahead_running_s"] == 0.0
    assert res["brief_id"].startswith("brief-")
    # the brief really landed, whole, with the budgets folded in
    dropped = json.loads((session / "inbox" / res["brief_id"]).read_text())
    assert dropped == {"kind": "task", "task": "stack", "seed": 90000,
                       "max_replans": 2}


def test_run_task_handle_is_a_brief_status_reply(tmp_path):
    session = _run_task_session(tmp_path)
    res = ms.run_task("stack", 90001)
    # one implementation, not two: the handle IS what brief_status answers
    assert _same(res, bs.brief_status(session, res["brief_id"]))


def test_run_task_rejects_unknown_session_before_submitting(tmp_path):
    session = _run_task_session(tmp_path)
    res = ms.run_task("stack", 90002, session="../session-main")
    assert "error" in res
    assert not list((session / "inbox").glob("*.json"))


def test_lifecycle_tools_passthrough_and_reject_unknown_session(tmp_path):
    session = _run_task_session(tmp_path)
    brief_id = ms.run_task("stack", 90003)["brief_id"]
    assert _same(ms.brief_status(brief_id), bs.brief_status(session, brief_id))
    assert ms.brief_status(brief_id, session="../oops") == {
        "error": "unknown session '../oops'"}
    assert ms.cancel_brief(brief_id, session="../oops") == {
        "error": "unknown session '../oops'"}
    # cancel is the one lifecycle write: a marker, nothing else touched
    assert ms.cancel_brief(brief_id)["requested"] is True
    assert (session / "cancel" / brief_id).exists()
    assert (session / "inbox" / brief_id).exists()
