"""board.store.trajectories: a pure projection of protocol-shaped chain rows
(task.plan / task.verify / task.fault / task.plan_complete) into trajectory
samples, byte-identical on the CLI, MCP, and library faces."""

from __future__ import annotations

import json

from board import mcp_server as ms
from board import store as bs
from board import storecli
from harness.events import SessionLog
from harness.protocol import content_id


def _chain(runs) -> None:
    log = SessionLog(runs / "session-main" / "session-log")
    g1 = {"goal": "clear", "nodes": [{"id": "n1", "skill": "pick", "args": {"object": "can"},
                                      "after": []},
                                     {"id": "n2", "skill": "pick", "args": {"object": "milk"},
                                      "after": ["n1"]}],
          "verify": [{"predicate": "pick_success", "after": "n1"},
                     {"predicate": "pick_success", "after": "n2"}]}
    plan = {"replan": 0, "seed": 7, "mission": "clear_table", "sigma0": {"objects": {}},
            "skills": ["pick"], "show_evidence": False, "done": [], "fault": None,
            "graph": g1, "graph_id": content_id(g1), "rationale": "", "legal": True,
            "problems": [], "block": None}
    log.append("task.plan", plan)
    log.append("task.verify", {"node": "n1", "results": {"pick_success": True}})
    log.append("task.verify", {"node": "n2", "results": {"pick_success": False}})
    log.append("task.fault", {"node": "n2", "failed": ["pick_success"],
                              "signature": "node_failure", "msg": "x"})
    log.append("task.plan", {**plan, "replan": 1, "done": ["n1"],
                             "fault": {"kind": "node_failure", "node": "n2"}})
    log.append("task.verify", {"node": "n2", "results": {"pick_success": True}})
    log.append("task.plan_complete", {"success": True, "goal": "clear", "replans": 1,
                                      "actuations": 3, "faults": [{}], "nodes": {}})


def test_one_sample_per_decision_with_L_and_success(tmp_path):
    _chain(tmp_path)
    ts = bs.trajectories(tmp_path / "session-main", role_of_seed=lambda s: "dev")
    assert ts[0]["o"]["role_source"] == "caller"
    assert len(ts) == 2
    assert [t["o"]["L"] for t in ts] == [1, 2]
    assert all(t["o"]["success"] is True and t["o"]["replans"] == 1 for t in ts)
    assert ts[0]["o"]["verify"] == {"n1": {"pick_success": True}, "n2": {"pick_success": False}}
    assert ts[1]["x"]["done"] == ["n1"] and ts[1]["x"]["fault"]["node"] == "n2"
    assert ts[0]["o"]["role"] == "dev" and ts[0]["id"] != ts[1]["id"]
    assert ts[0]["id"] == content_id({"x": ts[0]["x"], "y": ts[0]["y"]})


def test_three_faces_are_byte_identical(tmp_path, capsys):
    runs = tmp_path / "runs"
    runs.mkdir()
    _chain(runs)
    expected = bs.trajectories(runs / "session-main")
    assert expected
    ms.configure(runs)
    code = storecli.main(["trajectories", "session-main", "--runs", str(runs)])
    assert code == 0
    assert capsys.readouterr().out.rstrip("\n") == json.dumps(expected)       # CLI
    assert json.dumps(ms.trajectories("session-main")) == json.dumps(expected)  # MCP
    assert ms.trajectories("../session-main") == {"error": "unknown session"}
    assert expected[0]["o"]["role_source"] == "no_store"


def _burn_heldout(runs, seeds) -> None:
    store = runs / "session-main" / "campaigns" / "c"
    (store / "artifacts").mkdir(parents=True)
    (store / "artifacts" / "abc.json").write_text(json.dumps({"heldout": seeds}))
    (store / "index.jsonl").write_text(json.dumps(
        {"seq": 0, "kind": "preregistration", "sha": "abc", "time": 0}) + "\n")


def test_role_from_burned_heldout_block_and_out_split(tmp_path, capsys):
    runs = tmp_path / "runs"
    _chain(runs)                       # seed 7 on both samples
    _burn_heldout(runs, [7, 8])
    ts = bs.trajectories(runs / "session-main")
    assert [t["o"]["role"] for t in ts] == ["heldout", "heldout"]
    assert ts[0]["o"]["role_source"] == "burned_blocks"
    out = tmp_path / "out"
    code = storecli.main(["trajectories", "session-main", "--runs", str(runs), "--out", str(out)])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"dev": 0, "heldout": 2}
    held = [json.loads(l) for l in (out / "heldout.jsonl").read_text().splitlines()]
    assert held == ts and (out / "dev.jsonl").read_text() == ""
    ms.configure(runs)
    assert json.dumps(ms.trajectories_split("session-main")) == json.dumps(
        bs.split_trajectories(ts))                                            # MCP == lib
    assert ms.trajectories_split("session-main")["heldout"] == held           # MCP == CLI files
    (runs / "session-main" / "campaigns" / "c" / "artifacts" / "abc.json").write_text(
        json.dumps({"heldout": [9]}))
    assert [t["o"]["role"] for t in bs.trajectories(runs / "session-main")] == ["dev", "dev"]
