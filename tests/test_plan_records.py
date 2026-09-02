"""PlanRecords: board.store.plan_index (chain -> per-graph evidence, three faces
byte-identical), scripts/publish_plans.py (the evolution door + rule), and
workload.plans_for / skill_record.validate_capability on the plan kind."""

from __future__ import annotations

import json

import pytest

from board import mcp_server as ms
from board import store as bs
from board import storecli
from harness.events import SessionLog
from harness.protocol import graph_sha, plan_lower_bound
from harness.skill_library import load_records
from harness.skill_record import SkillRecordError, validate_capability
from plugins.graphs import InMemorySkillGraph
from plugins.task.workload import plans_for
from scripts import publish_plans

EMB, ARM = "plugins.embodiment_robosuite:provider", "scripted"
G_A = {"goal": "hold the can", "nodes": [{"id": "n1", "skill": "pick", "args": {"object": "can"},
                                          "after": []}],
       "verify": [{"predicate": "pick_success", "after": "n1"}], "rationale": "r", "planner": {"provider": "x"}}
G_B = {**G_A, "nodes": [{"id": "n1", "skill": "pick", "args": {"object": "milk"}, "after": []}]}


def _episode(log, graph, seed, ok):
    log.append("task.plan", {
        "replan": 0, "seed": seed, "mission": "stack", "sigma0": {}, "skills": ["pick"],
        "show_evidence": False, "done": [], "fault": None, "graph": graph,
        "graph_id": "x", "graph_sha": graph_sha(graph), "rationale": "r",
        "planner": {"provider": "x"}, "embodiment": EMB, "arm": ARM,
        "facts": ["present(can)", "present(milk)"], "objects": ["can", "milk"],
        "visible": ["pick"], "legal": True, "problems": [], "block": "b0"})
    log.append("task.verify", {"node": "n1", "results": {"pick_success": ok}})
    log.append("task.plan_complete", {"success": ok, "goal": graph["goal"], "replans": 0,
                                      "actuations": 1, "faults": [], "nodes": {}})


def _chain(runs):
    log = SessionLog(runs / "session-main" / "session-log")
    for i in range(10):
        _episode(log, G_A, i, i < 9)          # 9/10
    for i in range(10):
        _episode(log, G_B, 100 + i, i < 3)    # 3/10
    # an illegal row and a replan-1 row are not episodes
    log.append("task.plan", {"replan": 1, "legal": True, "graph_sha": graph_sha(G_A),
                             "mission": "stack", "embodiment": EMB, "arm": ARM, "seed": 5})
    log.append("task.plan", {"replan": 0, "legal": False, "graph_sha": graph_sha(G_B),
                             "mission": "stack", "embodiment": EMB, "arm": ARM, "seed": 6})


def _records(tmp_path):
    root = tmp_path / "records"
    root.mkdir()
    (root / "pick.json").write_text(json.dumps({
        "id": "pick", "name": "pick", "args": {"object": "str"},
        "requires": ["present(object)"], "ensures": ["holding(object)"]}))
    return root


def test_plan_index_from_fixture_chain(tmp_path, capsys):
    runs = tmp_path / "runs"
    runs.mkdir()
    _chain(runs)
    rows = bs.plan_index(runs / "session-main")
    by = {r["graph_sha"]: r for r in rows}
    assert len(rows) == 2 and set(by) == {graph_sha(G_A), graph_sha(G_B)}
    a, b = by[graph_sha(G_A)], by[graph_sha(G_B)]
    assert (a["n"], a["k"], a["L_mean"]) == (10, 9, 0.9)
    assert (b["n"], b["k"], b["L_mean"]) == (10, 3, 0.3)
    assert a["seeds"] == list(range(10)) and a["blocks"] == ["b0"] and len(a["seqs"]) == 10
    assert (a["task"], a["embodiment"], a["arm"]) == ("stack", EMB, ARM)
    assert "planner" not in a["graph"] and "rationale" not in a["graph"]
    # three faces
    ms.configure(runs)
    assert storecli.main(["plan_index", "session-main", "--runs", str(runs)]) == 0
    assert capsys.readouterr().out.rstrip("\n") == json.dumps(rows)
    assert json.dumps(ms.plan_index("session-main")) == json.dumps(rows)
    assert ms.plan_index("../session-main") == {"error": "unknown session"}


def test_publish_plans_applies_rule_and_asserts_evolution(tmp_path, capsys):
    runs = tmp_path / "runs"
    runs.mkdir()
    _chain(runs)
    root, records = tmp_path / "skills", _records(tmp_path)
    base = ["session-main", "--runs", str(runs), "--skills-root", str(root),
            "--records", str(records), "--theta", "0.6", "--n-min", "10",
            "--goal", "stack=holding(can)"]
    assert publish_plans.main(base) == 3                       # execution: refused
    assert "refused" in capsys.readouterr().out and not root.exists()
    assert publish_plans.main(["--mode", "evolution", *base]) == 0
    lines = {l["graph_sha"]: l for l in map(json.loads, capsys.readouterr().out.splitlines())}
    a, b = lines[graph_sha(G_A)], lines[graph_sha(G_B)]
    assert (a["k"], a["lower"], a["published"]) == (9, plan_lower_bound(10, 9), True)
    assert (b["k"], b["lower"], b["published"]) == (3, plan_lower_bound(10, 3), False)
    assert "rule" in b["reason"]
    skills = InMemorySkillGraph(root=str(root)).skills()
    assert len(skills) == 1
    rec = skills[0]
    assert rec["kind"] == "plan" and rec["id"] == graph_sha(G_A)
    assert rec["rule"] == {"theta": 0.6, "n_min": 10, "lower": plan_lower_bound(10, 9)}
    assert rec["evidence"] == {"n": 10, "k": 9, "L_mean": 0.9, "seed_blocks": ["b0"],
                               "sessions": ["session-main"]}
    assert rec["goal"] == ["holding(can)"] and rec["published_from"][0]["session"] == "session-main"
    # plans_for reads it back from the mounted root, best first
    hits = plans_for(skills, "stack", EMB, ARM)
    assert [p.id for p in hits] == [graph_sha(G_A)] and hits[0].graph["nodes"] == G_A["nodes"]
    assert plans_for(skills, "stack", EMB, "vla") == []
    # a goal the graph does not ensure: Covered bites, nothing is published
    root2 = tmp_path / "skills2"
    assert publish_plans.main(["--mode", "evolution", *base[:-1], "stack=holding(milk)",
                               "--skills-root", str(root2)]) == 0
    lines = {l["graph_sha"]: l for l in map(json.loads, capsys.readouterr().out.splitlines())}
    a = lines[graph_sha(G_A)]
    assert not a["published"] and "covered" in a["reason"] and not list(root2.glob("*.json"))


def test_validate_capability_plan_kind(tmp_path):
    records = load_records(_records(tmp_path))
    graph = {k: v for k, v in G_A.items() if k not in ("planner", "rationale")}
    rec = {"kind": "plan", "id": graph_sha(graph), "task": "stack", "goal": ["holding(can)"],
           "graph": graph, "embodiment": EMB, "arm": ARM,
           "evidence": {"n": 10, "k": 9, "L_mean": 0.9, "seed_blocks": [], "sessions": []},
           "rule": {"theta": 0.6, "n_min": 10, "lower": plan_lower_bound(10, 9)},
           "published_from": []}
    validate_capability(rec)
    validate_capability(rec, records, ["present(can)"], ["can"])
    with pytest.raises(SkillRecordError, match="covered"):
        validate_capability({**rec, "goal": ["holding(milk)"]}, records, ["present(can)"], ["can"])
    with pytest.raises(SkillRecordError, match="graph_sha"):
        validate_capability({**rec, "id": "0" * 64})
    with pytest.raises(SkillRecordError, match="rule"):
        validate_capability({**rec, "rule": {**rec["rule"], "theta": 0.9}})
    with pytest.raises(SkillRecordError):
        InMemorySkillGraph().publish({**rec, "evidence": {"n": 0, "k": 0}})


def test_every_record_file_declares_a_class():
    """Every shipped record declares its class explicitly (a lowercase token) --
    the derivation rule is the default for NEW records, but shipped ones may
    override it to fold synonyms into one family (pick -> grasp, navigate ->
    nav, stack/pot_veg -> place, faucet/close/press -> actuate)."""
    from harness.skill_library import CLASS_TOKEN, ROOT, load_records
    for path in sorted(ROOT.glob("*.json")):
        d = json.loads(path.read_text())
        assert CLASS_TOKEN.fullmatch(d.get("class", "")), path.name
    assert all(r.class_ for r in load_records().values())