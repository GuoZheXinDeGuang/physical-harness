"""Unit tests for the board's runtime-session read layer (board/store.py).

No server, no simulator: a fixture session dir is built with the real
harness.events.SessionLog writer (the same one scripts/harness_runtime.py uses),
so read_session/discover_sessions are exercised against the exact on-disk shape
the runtime produces -- a chained rows.jsonl carrying note data inline.
"""

from __future__ import annotations

import json
from pathlib import Path

from board import store as bs
from harness.events import SessionLog


def _session(runs: Path, name: str = "session-main") -> Path:
    """A session dir with a real, verifiable chain of runtime notes."""
    log = SessionLog(runs / name / "session-log")
    log.append("kernel.mount", {"profile": "base"})
    log.append("task.plan_complete", {
        "success": True, "goal": "cubeA on cubeB", "replans": 0, "actuations": 1,
        "faults": [], "nodes": {"n1": {"success": True,
                                       "stages": [{"name": "grasp", "success": True}]}}})
    log.append("task.plan_complete", {
        "success": False, "goal": "clear table", "replans": 3, "actuations": 3,
        "faults": [{"kind": "node_failure"}], "nodes": {}})
    log.append("runtime.task_error",
               {"brief": "bad.json", "task": "nonsense", "error": "ValueError(...)"})
    return runs / name


def test_read_session_by_kind_and_chain(tmp_path):
    d = bs.read_session(_session(tmp_path))
    assert d["name"] == "session-main"
    assert d["chain_ok"] is True
    assert d["skipped"] == 0
    assert d["kinds"]["task.plan_complete"] == 2
    assert d["kinds"]["runtime.task_error"] == 1
    tasks = d["rows"]["task.plan_complete"]
    assert tasks[0]["success"] is True and tasks[0]["goal"] == "cubeA on cubeB"
    assert tasks[1]["success"] is False and tasks[1]["replans"] == 3


def test_discover_sessions_separate_from_stores(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _session(runs, "session-main")
    # a campaign store (index.jsonl, no session-log) is NOT a session
    (runs / "stack-g1" / "artifacts").mkdir(parents=True)
    (runs / "stack-g1" / "index.jsonl").write_text(
        '{"seq":0,"kind":"preregistration","sha":"x"}\n')
    sessions = bs.discover_sessions(runs)
    assert [s["name"] for s in sessions] == ["session-main"]
    assert sessions[0]["chain_ok"] is True and "rows" not in sessions[0]
    assert bs.is_session(runs / "stack-g1") is False
    assert bs.is_session(runs / "session-main") is True


def test_tampered_chain_is_flagged(tmp_path):
    sd = _session(tmp_path)
    rows_path = sd / "session-log" / "rows.jsonl"
    lines = rows_path.read_text().splitlines()
    row = json.loads(lines[1])          # a task.plan_complete row
    row["data"]["success"] = False      # flip a value; leave stale sha/chain
    lines[1] = json.dumps(row, sort_keys=True, default=str)
    rows_path.write_text("\n".join(lines) + "\n")
    d = bs.read_session(sd)
    assert d["chain_ok"] is False
    # by-kind still parses -- the chain flag is the integrity signal, not the parse
    assert d["kinds"]["task.plan_complete"] == 2


def test_partial_trailing_line_is_skipped(tmp_path):
    sd = _session(tmp_path)
    rows_path = sd / "session-log" / "rows.jsonl"
    with rows_path.open("a") as fh:
        fh.write('{"seq": 9, "kind": "task.plan_complete", "data": {"succ')  # mid-write
    d = bs.read_session(sd)
    assert d["skipped"] == 1
    assert d["kinds"]["task.plan_complete"] == 2  # the whole rows still parse
