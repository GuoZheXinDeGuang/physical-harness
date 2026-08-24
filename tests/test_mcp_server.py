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
import os
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


# --- run_task: submit + wait + read, one call ------------------------------
#
# The fake runtime is a monkeypatched time.sleep: on each poll tick it does the
# real runtime's move+append (inbox -> done|failed, seal one chain row) for the
# one pending brief. So run_task's real submit/poll/read loop runs synchronously
# with no threads and no MuJoCo -- the runtime is faked, the tool under test is not.


def _run_task_session(tmp_path: Path) -> Path:
    """A fake session: one pre-existing chain row (so baseline is nonzero and the
    tool must skip it) + an empty inbox. Returns the session dir."""
    session = tmp_path / "session-main"
    (session / "inbox").mkdir(parents=True)
    SessionLog(session / "session-log").append("runtime.boot", {"mode": "execution"})
    ms.configure(tmp_path, inbox=session / "inbox")
    return session


def _fake_runtime(session: Path, dest: str, kind: str, data: dict):
    """Seal one chain row then os.replace the one pending brief inbox -> dest,
    exactly the move+append scripts/harness_runtime.py:_process does."""
    briefs = list((session / "inbox").glob("*.json"))
    if not briefs:
        return
    SessionLog.load(session / "session-log").append(kind, data)
    (session / dest).mkdir(exist_ok=True)
    os.replace(briefs[0], session / dest / briefs[0].name)


def test_run_task_done_reads_plan_complete(tmp_path, monkeypatch):
    session = _run_task_session(tmp_path)
    nodes = {"stack-0": {"success": True, "stages": []}}
    monkeypatch.setattr(ms.time, "sleep", lambda _: _fake_runtime(
        session, "done", "task.plan_complete",
        {"success": True, "goal": "stacked", "faults": [], "nodes": nodes}))

    res = ms.run_task("stack", 90000)

    assert res["status"] == "done"
    assert res["success"] is True          # copied verbatim, not re-decided
    assert res["nodes"] == nodes
    assert res["chain_seq"] == 1           # after the seq-0 boot row
    assert "failure" not in res            # no faults -> no failure key
    assert res["brief_id"].startswith("brief-") and "elapsed_s" in res


def test_run_task_done_carries_faults_as_failure(tmp_path, monkeypatch):
    session = _run_task_session(tmp_path)
    faults = [{"kind": "budget", "msg": "out of actuations"}]
    monkeypatch.setattr(ms.time, "sleep", lambda _: _fake_runtime(
        session, "done", "task.plan_complete",
        {"success": False, "faults": faults, "nodes": {}}))

    res = ms.run_task("clear_table", 90003)

    assert res["status"] == "done" and res["success"] is False
    assert res["failure"] == faults        # goal missed -> faults surface verbatim


def test_run_task_failed_reads_task_error(tmp_path, monkeypatch):
    session = _run_task_session(tmp_path)

    def fake_sleep(_):
        briefs = list((session / "inbox").glob("*.json"))
        if briefs:
            _fake_runtime(session, "failed", "runtime.task_error",
                          {"brief": briefs[0].name, "task": "stack",
                           "error": "ValueError('unknown brief keys')"})
    monkeypatch.setattr(ms.time, "sleep", fake_sleep)

    res = ms.run_task("stack", 90001)

    assert res["status"] == "failed"
    assert "unknown brief keys" in res["error"]
    assert res["chain_seq"] == 1
    assert "success" not in res and "nodes" not in res


def test_run_task_timeout_leaves_brief_pending(tmp_path, monkeypatch):
    session = _run_task_session(tmp_path)
    monkeypatch.setattr(ms.time, "sleep", lambda _: None)  # runtime never claims it

    res = ms.run_task("stack", 90002, timeout_s=0.05)

    assert res["status"] == "timeout"
    assert "guidance" in res and res["brief_id"].startswith("brief-")
    assert (session / "inbox" / res["brief_id"]).exists()  # still queued, keeps running
