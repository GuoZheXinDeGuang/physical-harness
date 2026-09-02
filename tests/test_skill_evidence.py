"""board.store.skill_evidence: a pure projection of task.verify seal rows into
per (skill, embodiment, executor) {n, k}, byte-identical on the CLI, MCP, and
library faces. Older rows without an executor key count as scripted."""

from __future__ import annotations

import json

from board import mcp_server as ms
from board import store as bs
from board import storecli
from harness.events import SessionLog


def _chain(runs) -> None:
    log = SessionLog(runs / "session-main" / "session-log")
    g = {"goal": "g", "nodes": [{"id": "n1", "skill": "pick", "args": {}, "after": []},
                                {"id": "n2", "skill": "place_meat", "args": {}, "after": ["n1"]}],
         "verify": []}
    plan = {"replan": 0, "seed": 1, "mission": "m", "embodiment": "kitchen", "arm": "auto",
            "graph": g, "legal": True}
    drv = {"ref": "policy.driver:pi05", "handshake": "abc"}
    log.append("task.plan", plan)
    log.append("task.verify", {"node": "n1", "results": {"pick": True}})             # no key -> scripted
    log.append("task.verify", {"node": "n2", "results": {"place_meat": True},
                               "executor": "pi05", "driver": drv})
    log.append("task.plan_complete", {"success": True})
    log.append("task.plan", {**plan, "seed": 2})
    log.append("task.verify", {"node": "n1", "results": {"pick": False}, "executor": "scripted"})
    log.append("task.verify", {"node": "n2", "results": {"place_meat": False},
                               "executor": "pi05", "driver": drv})
    log.append("task.verify", {"node": "n2", "results": {"place_meat": True},
                               "executor": "scripted"})
    log.append("task.verify", {"node": "ghost", "results": {"x": True}})            # not in graph: dropped
    log.append("task.plan_complete", {"success": False})


def test_counts_per_skill_and_executor(tmp_path):
    _chain(tmp_path)
    rows = bs.skill_evidence(tmp_path / "session-main")
    assert rows == [
        {"skill": "pick", "embodiment": "kitchen", "executor": "scripted", "n": 2, "k": 1},
        {"skill": "place_meat", "embodiment": "kitchen", "executor": "pi05", "n": 2, "k": 1},
        {"skill": "place_meat", "embodiment": "kitchen", "executor": "scripted", "n": 1, "k": 1},
    ]


def test_three_faces_are_byte_identical(tmp_path, capsys):
    runs = tmp_path / "runs"
    runs.mkdir()
    _chain(runs)
    expected = bs.skill_evidence(runs / "session-main")
    assert expected
    ms.configure(runs)
    assert storecli.main(["skill_evidence", "session-main", "--runs", str(runs)]) == 0
    assert capsys.readouterr().out.rstrip("\n") == json.dumps(expected)          # CLI
    assert json.dumps(ms.skill_evidence("session-main")) == json.dumps(expected)  # MCP
    assert ms.skill_evidence("../session-main") == {"error": "unknown session"}
    assert storecli.main(["skill_evidence", "nope", "--runs", str(runs)]) == 3
