"""Task-planning workload: the strict form of the OS loop's Plan -> Act -> Verify edge.

Structural sibling of ``plugins/rsi/workload.py``: every dependency is resolved
through the kernel as consumer "task", so the audit trail accounts the whole
loop and swapping any provider is a mount-plan edit, not a code edit here.

Two layers, never merged: the planner's output is a skill-call graph -- the
inter-skill CONTROLLER (which skills, what order). Each node dispatches as ONE
``governed_rollout`` whose StageSpec chain is the intra-skill SCORER; stages'
"scorer never controller" invariant (harness/stages.py) is what licenses their
privileged reads, so the plan is never collapsed into a stage chain.

The replan loop is this workload's OWN while (zos plan_turn's shape, harness
side). ``kernel.note`` is evidence, never the mechanism. ``max_replans`` /
``max_actuations`` are model-independent floors, workload config on purpose:
the "seven actions on one y" incident class stays fenced no matter which
planner is mounted behind the seam.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any

from harness import opstream
from harness.kernel import Kernel
from harness.registry import load_provider
from harness.spec import EpisodeSpec
from plugins.task.validate import validate_plan

#: The kernel consumer name every capability this workload resolves is
#: accounted under (harness/kernel.py per-resolution audit trail).
CONSUMER = "task"

#: skill name -> EpisodeSpec kwargs for the node that runs it: the EXECUTION
#: half of the hand-declared Stack vocabulary (planner_stack.CATALOGUE is the
#: symbolic half the planner sees). ``stages`` is a "module:factory" ref
#: resolved at dispatch through harness.registry -- plugins never import each
#: other (tests/test_boundaries.py), and the ref string is the same sanctioned
#: crossing episode specs already use for env/policy providers. percept_noise
#: 0.012 is the Stack policy's calibrated operating point (scripts/stack_campaign.py).
SKILL_SPECS: dict[str, dict[str, Any]] = {
    "stack": {"task": "stack", "percept_noise": 0.012, "terminal_label": True,
              "stages": "plugins.embodiment_robosuite.env:stack_stages"},
    # One skill, several scenes: "pick" resolves its embodiment task from the
    # node's object arg (the first arg threading, arrived with the second
    # skill provider exactly as round 83 predicted).
    "pick": {"task_by_object": {"can": "pickcan", "milk": "pickmilk"},
             "percept_noise": 0.012,  # the phase-3 operating point; omitting it
             # silently falls to EpisodeSpec's 0.020 default -- the probe ran at
             # 0.012 and the first real closed loop failed on the drift (round 86)
             "terminal_label": True,
             "stages": "plugins.embodiment_robosuite.env:pick_stages"},
}


def _governed_rollout(spec: EpisodeSpec) -> dict:
    """One node, one plain governed rollout (no critic bundle).

    Reached by ref string, not import, for the same boundary reason as
    SKILL_SPECS above. Module-level so tests can monkeypatch the dispatch
    without touching the loop.
    """
    return importlib.import_module("plugins.rsi.governed").governed_rollout(spec, None)


def _dispatch(node: Mapping, *, seed: int, env_ref: str, policy_ref: str) -> dict:
    """Build the node's EpisodeSpec and run it, stage scorer aboard.

    A binding either names its embodiment task directly ("stack": the policy
    is cubeA-on-cubeB by construction) or carries ``task_by_object`` mapping
    the node's object arg to a task ("pick"): the catalogue only types the
    arg, so an object with no scene binding fails loudly HERE, before any
    actuation.
    """
    binding = SKILL_SPECS.get(node["skill"])
    if binding is None:
        raise ValueError(f"skill {node['skill']!r} validated against the "
                         "catalogue but has no execution binding in SKILL_SPECS")
    kwargs = dict(binding)
    task_by_object = kwargs.pop("task_by_object", None)
    if task_by_object is not None:
        obj = node["args"].get("object")
        if obj not in task_by_object:
            raise ValueError(
                f"skill {node['skill']!r} has no task binding for object "
                f"{obj!r}; known objects: {sorted(task_by_object)}")
        kwargs["task"] = task_by_object[obj]
    stages = load_provider(kwargs.pop("stages"))
    spec = EpisodeSpec(seed=seed, stages=stages, env_provider=env_ref,
                       policy_provider=policy_ref, **kwargs)
    return _governed_rollout(spec)


def run(brief: Mapping, kernel: Kernel, *, seed: int,
        max_replans: int = 3, max_actuations: int = 3) -> dict[str, Any]:
    """Run one plan -> validate -> act -> verify -> replan loop through the kernel.

    ``brief`` carries task/catalogue/oracles (planner-facing vocabulary,
    authored on the skill side); the workload stamps scene and remaining budget
    onto it each attempt, and folds every refusal back in -- an invalid plan as
    the validator's own words, a failed node as a Fault-shaped dict preserving
    failed/done/left (stage level) and nodes_done/nodes_left (node level) so
    the planner can keep finished work -- and the loop itself keeps it: a node
    already succeeded is skipped, never re-run or re-billed. Returns
    ``{success, goal, replans, actuations, nodes, faults}`` and closes with one
    ``task.plan_complete`` ledger note, win or lose.
    """
    planner = kernel.resolve("task.planner", consumer=CONSUMER)
    scene = kernel.resolve("graph.scene", consumer=CONSUMER)
    # Accounted even while the catalogue is hand-declared: graph.skill is where
    # the measured-skill enrichment join lands when a multi-skill choice needs it.
    kernel.resolve("graph.skill", consumer=CONSUMER)
    kernel.resolve("embodiment.env", consumer=CONSUMER)
    kernel.resolve("policy.driver", consumer=CONSUMER)
    # Intra-node fan-out lives behind exec.rollouts; the plan loop never does.
    kernel.resolve("exec.rollouts", consumer=CONSUMER)

    # Same refusal as plugins/rsi/workload.py: refs travel on specs bare, so
    # Mount params would silently vanish between kernel and rollout.
    for cap in ("embodiment.env", "policy.driver"):
        params = kernel.provider_params(cap)
        if params:
            raise ValueError(
                f"{cap} was mounted with params {params}, which cannot travel "
                "on an EpisodeSpec ref; use a parameter-free provider ref")
    env_ref = kernel.provider_ref("embodiment.env")
    policy_ref = kernel.provider_ref("policy.driver")
    catalogue = brief["catalogue"]
    oracles = brief["oracles"]

    plan: Mapping = {}
    nodes_out: dict[str, dict] = {}
    faults: list[dict] = []
    actuations = 0
    replans = 0
    success = False
    while True:
        # Pre-episode there is no obs; the empty snapshot is the honest scene
        # until M2's World bridge feeds a live one.
        brief = {**brief, "scene": scene.snapshot({}),
                 "budget": max_actuations - actuations}
        plan = planner.plan(brief)
        ok, msg = validate_plan(plan, catalogue, oracles)
        # Operational feed (harness.opstream; never chain evidence): the FULL
        # node graph, the moment it exists, so the execution-graph panel draws
        # the plan while the first node is still running.
        nodes = list(plan.get("nodes") or []) if ok else []
        opstream.emit("plan_built", replan=replans, valid=ok, msg=None if ok else msg,
                      goal=plan.get("goal") if isinstance(plan, Mapping) else None,
                      nodes=[{"id": n.get("id"), "skill": n.get("skill"),
                              "args": dict(n.get("args") or {})} for n in nodes],
                      verify=[dict(v) for v in (plan.get("verify") or [])] if ok else [])
        fault: dict | None = None
        if not ok:
            fault = {"kind": "invalid_plan", "msg": msg}
        else:
            # nodes_out accumulates ACROSS replans: a node that already
            # succeeded is finished work, skipped without re-running or
            # re-billing -- a model-independent floor, like max_actuations.
            for node in plan["nodes"]:
                prior = nodes_out.get(node["id"])
                if prior is not None and prior["success"]:
                    continue
                # The model-independent floor, enforced BEFORE dispatch: no
                # planner, however eloquent, can mint extra actuations.
                if actuations >= max_actuations:
                    fault = {"kind": "budget", "node": node["id"],
                             "msg": (f"max_actuations={max_actuations} reached "
                                     f"before dispatching node {node['id']!r}")}
                    break
                actuations += 1
                opstream.emit("node_start", node=node["id"], skill=node["skill"],
                              actuation=actuations)
                opstream.emit("actuation_start", node=node["id"], actuation=actuations)
                result = _dispatch(node, seed=seed, env_ref=env_ref,
                                   policy_ref=policy_ref)
                opstream.emit("actuation_end", node=node["id"], actuation=actuations,
                              success=bool(result.get("success")),
                              steps=result.get("steps"))
                stages = result.get("stages", [])
                done = [s["name"] for s in stages if s["success"]]
                left = [s["name"] for s in stages if not s["success"]]
                # At the 1-node loop the declared oracle IS the terminal
                # boolean the rollout already scored (terminal_label); a
                # second oracle dialect arrives with a second skill provider.
                bad_preds = [v["predicate"] for v in plan["verify"]
                             if v["after"] == node["id"] and not result["success"]]
                nodes_out[node["id"]] = {"success": bool(result["success"]),
                                         "steps": result.get("steps"),
                                         "stages": stages}
                if left or bad_preds:
                    fault = {"kind": "node_failure", "node": node["id"],
                             "failed": left + bad_preds, "done": done, "left": left,
                             "msg": (f"node {node['id']!r} failed: stages {left}, "
                                     f"predicates {bad_preds}; done {done}")}
                    opstream.emit("node_failed", node=node["id"],
                                  failed=left + bad_preds, done=done)
                    break
                opstream.emit("node_verified", node=node["id"], stages=done)
            if fault is not None:
                # Node-id-level attribution alongside the stage-level
                # done/left above: the planner keeps finished NODES too.
                nodes_done = [nid for nid, n in nodes_out.items() if n["success"]]
                fault["nodes_done"] = nodes_done
                fault["nodes_left"] = [n["id"] for n in plan["nodes"]
                                       if n["id"] not in nodes_done]
        if fault is None:
            success = True
            break
        faults.append(fault)
        if fault["kind"] == "budget" or replans >= max_replans:
            break
        replans += 1
        opstream.emit("replan", replan=replans, fault_kind=fault["kind"],
                      node=fault.get("node"), msg=fault.get("msg"))
        brief = {**brief, "fault": fault}

    goal = plan.get("goal") if isinstance(plan, Mapping) else None
    out = {"success": success, "goal": goal, "replans": replans,
           "actuations": actuations, "nodes": nodes_out, "faults": faults}
    opstream.emit("plan_complete", success=success, goal=goal,
                  replans=replans, actuations=actuations)
    kernel.note("task.plan_complete", {
        "success": success, "goal": goal, "replans": replans,
        "actuations": actuations, "faults": faults,
        "nodes": {nid: {"success": n["success"],
                        "stages": [{"name": s["name"], "success": s["success"]}
                                   for s in n["stages"]]}
                  for nid, n in nodes_out.items()},
    })
    return out
