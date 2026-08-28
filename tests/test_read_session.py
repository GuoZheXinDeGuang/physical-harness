"""Unit tests for the board's runtime-session read layer (board/store.py).

No server, no simulator: a fixture session dir is built with the real
harness.events.SessionLog writer (the same one scripts/harness_runtime.py uses),
so read_session/discover_sessions are exercised against the exact on-disk shape
the runtime produces -- a chained rows.jsonl carrying note data inline.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
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


def test_runtime_status_present_absent_and_partial(tmp_path):
    sd = _session(tmp_path)  # a booted session, but no runtime_status.json yet
    assert bs.read_runtime_status(sd) is None  # absent -> null
    status = {"pid": 4321, "render": True, "mode": "execution",
              "boot_ts": 1.5, "display": ":1"}
    (sd / "runtime_status.json").write_text(json.dumps(status))
    # present -> every boot-written field verbatim, plus the two derived ones
    assert bs.read_runtime_status(sd) == dict(
        status, alive=False, heartbeat_age_s=None)
    (sd / "runtime_status.json").write_text('{"pid": 4321, "rend')  # mid-write
    assert bs.read_runtime_status(sd) is None  # partial -> null, next poll recovers


def _status(sd: Path, **fields) -> None:
    (sd / "runtime_status.json").write_text(json.dumps(
        {"pid": os.getpid(), "mode": "execution", "boot_ts": 1.5, **fields}))


def test_a_status_file_cannot_claim_a_dead_runtime(tmp_path):
    """The incident, three times over: runtime_status.json named a pid that had
    been gone for days and every reader reported "runtime up", so operator
    briefs queued into an inbox nothing was serving. ``alive`` is now decided
    against /proc, so the corpse reads dead."""
    sd = _session(tmp_path)
    _status(sd, pid=_dead_pid())
    assert bs.read_runtime_status(sd)["alive"] is False
    # ...and a session with no live runtime is visible in the FIRST call an
    # agent makes, without drilling into runtime_status at all.
    assert [s["runtime_alive"] for s in bs.discover_sessions(tmp_path)] == [False]


def test_alive_needs_a_real_runtime_cmdline_not_a_substring(tmp_path):
    """This process is alive and its pid is real, but it is pytest, not a
    harness_runtime on this session -- the recycled-pid case, and the reason the
    check is structural (argv[0] is a python FILE, a later arg names
    harness_runtime.py, another resolves to THIS session dir). A grep whose
    command line happens to carry both strings would pass a substring scan."""
    sd = _session(tmp_path)
    _status(sd)                                   # our own, very-much-alive pid
    assert bs.read_runtime_status(sd)["alive"] is False
    assert bs.runtime_python(sd, os.getpid()) is None
    assert bs.runtime_python(sd, "not-a-pid") is None


def test_heartbeat_age_is_the_second_axis(tmp_path):
    """alive says the process exists; heartbeat_age_s says how long since it
    last stamped. Absent (a file older than heartbeats) reads as null, never 0 --
    'never beat' must not look like 'beat just now'."""
    sd = _session(tmp_path)
    _status(sd)
    assert bs.read_runtime_status(sd)["heartbeat_age_s"] is None
    _status(sd, heartbeat_ts=time.time() - 120)
    assert 119 <= bs.read_runtime_status(sd)["heartbeat_age_s"] <= 130


def test_a_runtime_that_announced_its_exit_is_not_alive(tmp_path):
    """Clean shutdown stamps stopped_ts. It is belt-and-braces (a kill -9 never
    writes it) but it must never be ignored when present."""
    sd = _session(tmp_path)
    _status(sd, stopped_ts=time.time())
    assert bs.read_runtime_status(sd)["alive"] is False


def _dead_pid() -> int:
    """A pid that has certainly exited and been reaped -- a real corpse, not a
    number picked out of the air."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def test_partial_trailing_line_is_skipped(tmp_path):
    sd = _session(tmp_path)
    rows_path = sd / "session-log" / "rows.jsonl"
    with rows_path.open("a") as fh:
        fh.write('{"seq": 9, "kind": "task.plan_complete", "data": {"succ')  # mid-write
    d = bs.read_session(sd)
    assert d["skipped"] == 1
    assert d["kinds"]["task.plan_complete"] == 2  # the whole rows still parse
