"""PlanRecord lane end to end, no simulator: a REAL execution-mode runtime runs
the fakes stack task on twelve seeds (12/12) and a failing twin task (0/3);
``scripts/publish_plans.py`` (the evolution door, a real subprocess) promotes
only the graph that clears the rule into a fresh skills root; a SECOND runtime
booted over that root answers the same task from the library (task.plan.planner
== library, plan_id == the published id) while its skills root stays
byte-for-byte untouched (two-state law: execution never writes it)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from board import store as bs
from harness.protocol import plan_lower_bound
from test_mission_e2e import REPO, SESSION, _CARD, _Env, _Runtime

PUBLISH = REPO / "scripts" / "publish_plans.py"
GOAL = "on(cubeA,cubeB)"


class _FailingEnv(_Env):
    """The stack fakes with a terminal that never scores: every episode 0/1."""

    def success(self, obs, spec, start_z):
        return False

    def terminal_success(self, obs, spec, start_z, env=None):
        return False


def failing_env_provider():
    return _FailingEnv()


CARD = (_CARD.format(task="e2e_stack", planner="planner_provider")
        + _CARD.format(task="e2e_fail", planner="planner_provider").replace(
            "test_mission_e2e:env_provider", "test_plan_library_e2e:failing_env_provider"))


def _plans(rows):
    return [r["data"] for r in rows if r["kind"] == "task.plan"]


def _listing(rt):
    return sorted(p.name for p in (rt.session / "skills").iterdir())


@pytest.fixture(scope="module")
def evidence(tmp_path_factory):
    """Runtime 1: 12 x e2e_stack (all succeed) + 3 x e2e_fail (all fail)."""
    rt = _Runtime(tmp_path_factory.mktemp("runs1"), card=CARD)
    try:
        assert _listing(rt) == []
        shas = set()
        for seed in range(12):
            _, rows = rt.run({"kind": "task", "task": "e2e_stack", "seed": seed})
            plan = _plans(rows)[0]
            assert plan["legal"] and plan["planner"] == {"provider": "test_mission_e2e:planner_provider"}
            shas.add(plan["graph_sha"])
        for seed in range(3):
            _, rows = rt.run({"kind": "task", "task": "e2e_fail", "seed": seed,
                              "max_replans": 0})
            assert [r["data"]["success"] for r in rows if r["kind"] == "task.plan_complete"] == [False]
        assert len(shas) == 1 and _listing(rt) == []
        yield rt, shas.pop()
    finally:
        rt.stop()


@pytest.fixture(scope="module")
def published(evidence, tmp_path_factory):
    """publish_plans over runtime 1's chain into runtime 2's (future) skills root."""
    rt1, sha = evidence
    runs2 = tmp_path_factory.mktemp("runs2")
    root = runs2 / SESSION / "skills"
    base = [sys.executable, str(PUBLISH), SESSION, "--runs", str(rt1.runs),
            "--skills-root", str(root), "--goal", f"e2e_stack={GOAL}", "--goal", f"e2e_fail={GOAL}"]
    refused = subprocess.run(base, cwd=str(REPO), capture_output=True, text=True)
    assert refused.returncode == 3 and "refused" in refused.stdout and not root.exists()
    res = subprocess.run([*base, "--mode", "evolution"], cwd=str(REPO),
                         capture_output=True, text=True, check=True)
    lines = {l["task"]: l for l in map(json.loads, res.stdout.splitlines())}
    return runs2, sha, lines


def test_publish_plans_promotes_only_the_graph_that_clears_the_rule(published):
    runs2, sha, lines = published
    good, bad = lines["e2e_stack"], lines["e2e_fail"]
    assert good["published"] is True and (good["n"], good["k"]) == (12, 12)
    assert good["graph_sha"] == sha and good["lower"] == plan_lower_bound(12, 12) >= 0.8
    assert bad["published"] is False and (bad["n"], bad["k"]) == (3, 0)
    assert bad["lower"] == plan_lower_bound(3, 0) and "rule" in bad["reason"]
    files = sorted((runs2 / SESSION / "skills").glob("*.json"))
    assert [f.stem for f in files] == [good["digest"]]
    rec = json.loads(files[0].read_text())
    assert rec["kind"] == "plan" and rec["id"] == sha and rec["task"] == "e2e_stack"
    assert rec["goal"] == [GOAL] and rec["arm"] == "scripted"
    assert rec["embodiment"] == "test_mission_e2e:env_provider"
    assert rec["rule"] == {"theta": 0.8, "n_min": 10, "lower": good["lower"]}
    assert rec["evidence"]["seed_blocks"] == [] and rec["evidence"]["sessions"] == [SESSION]
    assert "planner" not in rec["graph"] and "rationale" not in rec["graph"]


def test_fresh_runtime_answers_from_the_library_and_never_writes_its_root(published):
    runs2, sha, lines = published
    rt = _Runtime(runs2, card=CARD)
    try:
        before = _listing(rt)
        assert before == [f"{lines['e2e_stack']['digest']}.json"]
        boot = next(r["data"] for r in bs.chain_rows(rt.session)
                    if r["kind"] == "runtime.boot")
        assert boot["mode"] == "execution" and boot["skills_manifest"] == [lines["e2e_stack"]["digest"]]
        _, rows = rt.run({"kind": "task", "task": "e2e_stack", "seed": 99})
        plan = _plans(rows)[0]
        assert plan["legal"] is True, plan["problems"]
        assert plan["planner"] == {"provider": "library", "plan_id": sha}
        assert plan["graph_sha"] == sha and plan["graph"]["planner"]["provider"] == "library"
        assert [r["data"] for r in rows if r["kind"] == "task.verify"] == [
            {"node": "stack-0", "results": {"stack_success": True}}]
        assert [r["data"]["success"] for r in rows if r["kind"] == "task.plan_complete"] == [True]
        # the unpublished twin misses the library and falls through to its card planner
        _, rows = rt.run({"kind": "task", "task": "e2e_fail", "seed": 1, "max_replans": 0})
        assert _plans(rows)[0]["planner"] == {"provider": "test_mission_e2e:planner_provider"}
        assert _listing(rt) == before
        assert not [r for r in rows if r["kind"] == "runtime.task_error"]
    finally:
        rt.stop()
    assert _listing(rt) == before
