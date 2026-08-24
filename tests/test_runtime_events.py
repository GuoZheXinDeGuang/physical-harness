"""The operational event stream (harness.opstream + board.store.read_runtime_events).

Three layers, one file: the writer's never-fail contract (emitting can lose an
event but can never raise into a task), the reader's cursor semantics (after_seq
/ last_seq, truncation-as-reboot), and face equivalence (storecli dispatch ==
MCP tool == board.store, byte-identical). Plus the wiring proof: a drained
runtime with the test_runtime_drain fake rollout leaves a feed whose kinds tell
the whole story boot -> task_claimed -> plan_built -> node -> plan_complete ->
task_done. The feed is NEVER a chain row: the sealed chain still verifies and
carries none of these kinds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_read_session import _session

from board import mcp_server as ms
from board import store as bs
from board import storecli
from harness import opstream
from harness.events import SessionLog
from plugins.task import workload
from scripts import harness_runtime as runtime


@pytest.fixture(autouse=True)
def _disarm():
    """opstream is a module-level singleton (one resident runtime per process);
    disarm after each test so an armed tmp path never leaks across tests."""
    yield
    opstream._path = None
    opstream._seq = 0


def _read(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_arm_truncates_and_emit_appends(tmp_path):
    feed = tmp_path / "runtime_events.jsonl"
    feed.write_text("stale line from a previous boot\n")
    opstream.arm(feed)
    assert feed.read_text() == "", "arm truncates: the feed is per-boot"
    opstream.emit("boot", pid=1)
    opstream.emit("task_claimed", brief="b.json", task="stack", seed=90901)
    rows = _read(feed)
    assert [r["kind"] for r in rows] == ["boot", "task_claimed"]
    assert [r["seq"] for r in rows] == [1, 2]
    assert all("ts" in r for r in rows)
    assert rows[1]["task"] == "stack" and rows[1]["seed"] == 90901


def test_emit_never_raises(tmp_path):
    # unarmed: a plain no-op (campaign subprocesses, library users)
    opstream._path = None
    opstream.emit("node_start", node="n1")
    # arm failure (unwritable destination) leaves the stream unarmed, no raise
    opstream.arm(tmp_path / "no-such-dir" / "runtime_events.jsonl")
    assert opstream._path is None
    opstream.emit("node_start", node="n1")
    # armed, then the destination dir vanishes: emit swallows the OSError
    d = tmp_path / "sess"
    d.mkdir()
    opstream.arm(d / "runtime_events.jsonl")
    (d / "runtime_events.jsonl").unlink()
    d.rmdir()
    opstream.emit("node_start", node="n1")


def test_read_absent_file_is_empty_feed(tmp_path):
    sd = _session(tmp_path)
    assert bs.read_runtime_events(sd) == {"events": [], "last_seq": 0}


def test_cursor_semantics_and_truncation_signal(tmp_path):
    sd = _session(tmp_path)
    opstream.arm(sd / "runtime_events.jsonl")
    for kind in ("boot", "task_claimed", "plan_built"):
        opstream.emit(kind)

    full = bs.read_runtime_events(sd)
    assert [e["seq"] for e in full["events"]] == [1, 2, 3]
    assert full["last_seq"] == 3

    tail = bs.read_runtime_events(sd, after_seq=2)
    assert [e["kind"] for e in tail["events"]] == ["plan_built"]
    assert tail["last_seq"] == 3
    assert bs.read_runtime_events(sd, after_seq=3)["events"] == []

    # reboot: arm truncates, seq restarts; a poller holding cursor 3 sees
    # last_seq < after_seq and resets to 0 -- the documented contract.
    opstream.arm(sd / "runtime_events.jsonl")
    opstream.emit("boot")
    r = bs.read_runtime_events(sd, after_seq=3)
    assert r["events"] == [] and r["last_seq"] == 1
    assert r["last_seq"] < 3, "the poller's truncation signal"

    # a half-written trailing line is skipped, picked up whole next poll
    with open(sd / "runtime_events.jsonl", "a") as f:
        f.write('{"seq": 2, "kind": "task_cl')
    assert bs.read_runtime_events(sd)["last_seq"] == 1


def test_faces_are_byte_identical(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    sd = _session(runs)
    opstream.arm(sd / "runtime_events.jsonl")
    opstream.emit("boot", pid=7)
    opstream.emit("node_start", node="stack-0", skill="stack", actuation=1)
    ms.configure(runs)

    def _same(a, b):
        return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    direct = bs.read_runtime_events(sd, 1)
    assert direct["events"], "non-trivial fixture: identity is not identity-of-empty"
    assert _same(ms.runtime_events("session-main", 1), direct)
    assert _same(storecli.dispatch("runtime_events", "session-main", runs,
                                   tmp_path / "S.md", tmp_path / "p.md", after=1),
                 direct)
    # the shared safe_child guard fronts this fn on both faces too
    assert ms.runtime_events("../session-main") == {"error": "unknown session"}
    with pytest.raises(ValueError):
        storecli.dispatch("runtime_events", "../session-main", runs,
                          tmp_path / "S.md", tmp_path / "p.md")


def _ok_rollout(spec):
    return {"success": True, "steps": 10, "stages": [
        {"name": "grasp", "success": True}, {"name": "place", "success": True}]}


def _drop(inbox: Path, name: str, brief: dict) -> None:
    tmp = inbox / (name + ".tmp")
    tmp.write_text(json.dumps(brief))
    os.replace(tmp, inbox / name)


def test_drained_runtime_writes_the_feed_not_the_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(workload, "_governed_rollout", _ok_rollout)
    session = tmp_path / "session-main"
    inbox = session / "inbox"
    inbox.mkdir(parents=True)
    _drop(inbox, "a-stack.json", {"kind": "task", "task": "stack", "seed": 90901})
    _drop(inbox, "b-bad.json", {"kind": "task", "task": "nonsense", "seed": 90902})

    rt = runtime.main(session, drain=True)

    kinds = [e["kind"] for e in bs.read_runtime_events(session)["events"]]
    assert kinds[0] == "boot"
    # the well-formed brief: claimed -> full plan graph -> node lifecycle -> done
    for k in ("task_claimed", "plan_built", "node_start", "actuation_start",
              "actuation_end", "node_verified", "plan_complete", "task_done"):
        assert k in kinds, f"missing {k} in {kinds}"
    # the unknown-task brief still emits its claim and its failure
    assert "task_failed" in kinds
    plan = next(e for e in bs.read_runtime_events(session)["events"]
                if e["kind"] == "plan_built")
    assert plan["nodes"] and plan["nodes"][0]["skill"] == "stack"

    # operational feed only: the sealed chain has none of these kinds and verifies
    chain_kinds = {r["kind"] for r in rt.log.rows()}
    assert not chain_kinds & {"boot", "task_claimed", "plan_built", "node_start",
                              "node_verified", "task_done", "task_failed"}
    assert SessionLog.load(session / "session-log").verify()
