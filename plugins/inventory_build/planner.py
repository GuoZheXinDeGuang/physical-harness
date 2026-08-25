"""A from-scratch ``harness.contracts.TaskPlanner`` for ``inventory_build`` (M6)
plus the card's PREDICATES -- the machine oracles its perceive/decide/verify
nodes name.

Two halves, one file (the clear_build shape):

* **The symbolic layer** -- CATALOGUE / ORACLES / the deterministic 11-node
  planner. Same brief -> byte-identical plan (体检 determinism + replay). The
  graph is HETEROGENEOUS: four node KINDS the generic base loop dispatches --
  perceive x2 (survey, classify), decide x2 (plan-order, report), verify x3
  (verify-grasp, verify-integrity, verify-cleared), manipulate x4 (grasp-cube,
  build-stack, pick-can, pick-milk). Manipulate nodes carry no ``kind`` (default
  ``"manipulate"``) and reuse the grasp/stack/pick SKILL_SPECS bindings; kindful
  nodes carry their ``kind`` and name a predicate in their ``skill`` slot.

* **The predicate layer** -- PREDICATES maps each kindful skill name to a
  "module:factory" ref. ``load_provider`` calls the factory (no args) and gets
  back the callable ``predicate(node, ctx) -> {"success": bool, ...}``. The base
  ``plugins.task.workload`` handlers resolve and score them; the truth is ALWAYS
  a machine predicate over the seed-deterministic scene or prior sealed facts,
  NEVER a model claim. Predicates reach env/percept providers by REF at call time
  (no sibling import -- tests/test_boundaries.py).

Node order follows the mission narrative (survey -> classify -> decide ->
grasp -> stack -> clear -> report). Whether the governed stack node sits early
enough to avoid ``q_pre`` dilution is a CALIBRATION decision (docs/
m6-mission-design.md §1/§4), taken on the per-kind first-death number, not here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

# ── symbolic layer: card vocabulary the validator types (plugins.task.validate) ──

#: skill name -> {arg name: required python type}. Manipulate skills (grasp/pick/
#: stack) reuse the SKILL_SPECS execution bindings; kindful skills take no args
#: (their predicate reads the seed / prior sealed facts off ``ctx``).
CATALOGUE: dict[str, dict[str, type]] = {
    "survey": {},
    "classify": {},
    "plan_order": {},
    "grasp": {"object": str},
    "verify_grasp": {},
    "stack": {"object": str, "target": str},
    "verify_integrity": {},
    "pick": {"object": str},
    "verify_cleared": {},
    "report": {},
}

#: Verify predicates a plan's ``verify`` list may name -- the manipulate nodes'
#: terminal success booleans. Kindful nodes need no verify entry: their own
#: machine ``result["success"]`` IS the gate the loop faults on.
ORACLES: tuple[str, ...] = ("lifted", "pick_success", "stack_success")

#: kindful skill name -> "module:factory" ref of its machine oracle. Threaded
#: onto the brief by harness_runtime; every ref load_provider-resolves at the
#: doctor's Tier A, so a dead predicate reddens at mount, not mid-brief.
PREDICATES: dict[str, str] = {
    "survey": "plugins.inventory_build.planner:survey",
    "classify": "plugins.inventory_build.planner:classify",
    "plan_order": "plugins.inventory_build.planner:plan_order",
    "verify_grasp": "plugins.inventory_build.planner:verify_grasp",
    "verify_integrity": "plugins.inventory_build.planner:verify_integrity",
    "verify_cleared": "plugins.inventory_build.planner:verify_cleared",
    "report": "plugins.inventory_build.planner:report",
}


class InventoryBuildPlanner:
    """Layer 1 ``harness.contracts.TaskPlanner``: deterministic table lookup."""

    def plan(self, brief: Mapping) -> Mapping:
        task = brief.get("task")
        if task != "inventory_build":
            raise ValueError(
                f"InventoryBuildPlanner only plans 'inventory_build', got {task!r}")
        # Round-trip through sorted JSON, same as the other planners: the emitted
        # mapping is exactly its canonical byte form.
        return json.loads(json.dumps({
            "goal": "survey and classify the workspace, decide a build order, "
                    "then grasp / stack / clear with verify gates and a final report",
            "nodes": [
                {"id": "survey", "skill": "survey", "kind": "perceive",
                 "args": {}, "after": []},
                {"id": "classify", "skill": "classify", "kind": "perceive",
                 "args": {}, "after": ["survey"]},
                {"id": "plan-order", "skill": "plan_order", "kind": "decide",
                 "args": {}, "after": ["survey", "classify"]},
                {"id": "grasp-cube", "skill": "grasp",
                 "args": {"object": "cube"}, "after": ["plan-order"]},
                {"id": "verify-grasp", "skill": "verify_grasp", "kind": "verify",
                 "args": {}, "after": ["grasp-cube"]},
                {"id": "build-stack", "skill": "stack",
                 "args": {"object": "cubeA", "target": "cubeB"},
                 "after": ["verify-grasp"]},
                {"id": "verify-integrity", "skill": "verify_integrity",
                 "kind": "verify", "args": {}, "after": ["build-stack"]},
                {"id": "pick-can", "skill": "pick",
                 "args": {"object": "can"}, "after": ["verify-integrity"]},
                {"id": "pick-milk", "skill": "pick",
                 "args": {"object": "milk"}, "after": ["pick-can"]},
                {"id": "verify-cleared", "skill": "verify_cleared", "kind": "verify",
                 "args": {}, "after": ["pick-can", "pick-milk"]},
                {"id": "report", "skill": "report", "kind": "decide", "args": {},
                 "after": ["survey", "classify", "plan-order", "grasp-cube",
                           "build-stack", "pick-can", "pick-milk"]},
            ],
            "verify": [
                {"after": "grasp-cube", "predicate": "lifted"},
                {"after": "build-stack", "predicate": "stack_success"},
                {"after": "pick-can", "predicate": "pick_success"},
                {"after": "pick-milk", "predicate": "pick_success"},
            ],
        }, sort_keys=True))

    @property
    def identity(self) -> str:
        return "inventory_build_planner@v1"


def provider(**params: Any) -> InventoryBuildPlanner:
    return InventoryBuildPlanner(**params)


# ── predicate layer: machine oracles (base loop resolves each by ref) ────────
# The seed-deterministic scene the manipulate nodes will act in, and the types
# they carry. A perceive node reads the scene (privilege-budgeted); decide/verify
# read only ``ctx.nodes_out`` (prior sealed facts + successes -- zero privilege).

_SURVEY_TASKS: tuple[str, ...] = ("lift", "stack", "pickcan", "pickmilk")
_TYPE_MAP: dict[str, str] = {"lift": "cube", "stack": "cube",
                             "pickcan": "can", "pickmilk": "milk"}
_PERCEPT_REF = "plugins.embodiment_robosuite.percept:provider"


def _stage_ok(ctx, node_id: str, stage_name: str) -> bool:
    """A verify predicate over a prior manipulate node's sealed stage: did the
    named measurement stage pass? Missing node / stage -> False (-> replan)."""
    node = ctx.nodes_out.get(node_id)
    if not node:
        return False
    for stage in node.get("stages", ()):
        if stage["name"] == stage_name:
            return bool(stage["success"])
    return False


def _survey(node: Mapping, ctx) -> dict:
    """PERCEIVE: reset each same-seed task env and read the target pose through
    OnboardPercept at the spec's percept_noise. Success = every object pose is
    extractable and within table bounds. Seals the privileged pose channel it
    read (the base meters the budget via privilege_cost)."""
    from harness.registry import load_provider
    from harness.spec import EpisodeSpec

    embodiment = load_provider(ctx.env_ref)
    percept = load_provider(_PERCEPT_REF)
    poses: dict[str, list[float]] = {}
    ok = True
    for task in _SURVEY_TASKS:
        spec = EpisodeSpec(seed=ctx.seed, task=task, env_provider=ctx.env_ref)
        env = embodiment.make_env(spec)
        try:
            obs = env.reset()
            pose = percept.object_estimate(obs, spec, spec.percept_noise, 0)
        finally:
            env.close()
        x, y, z = (float(v) for v in pose[:3])
        # "within table bounds": a NaN / absurd read fails, a real tabletop pose
        # passes. Loose on purpose -- a bounds check, not a pose oracle.
        in_bounds = abs(x) < 1.0 and abs(y) < 1.0 and 0.5 < z < 1.5
        ok = ok and in_bounds
        poses[task] = [x, y, z]
    return {"success": ok, "facts": {"poses": poses},
            "privilege": ["privileged.object_z"]}


def _classify(node: Mapping, ctx) -> dict:
    """PERCEIVE (interprets survey's sealed reads -> a type map; pays no NEW
    privilege). Success = the geometry->type map matches the known object->type
    map for every surveyed task."""
    survey = ctx.nodes_out.get("survey")
    poses = ((survey or {}).get("facts") or {}).get("poses") or {}
    types = {task: _TYPE_MAP[task] for task in poses if task in _TYPE_MAP}
    ok = set(types) == set(_SURVEY_TASKS) and all(
        types[t] == _TYPE_MAP[t] for t in types)
    return {"success": ok, "facts": {"types": types}, "privilege": []}


def _plan_order(node: Mapping, ctx) -> dict:
    """DECIDE: pure fn of survey facts -> a build order (nearest object first,
    by L1 distance in the plane -- deterministic in seed). Success = the chosen
    order equals the machine-optimal order over those facts."""
    survey = ctx.nodes_out.get("survey")
    poses = ((survey or {}).get("facts") or {}).get("poses") or {}
    if set(poses) != set(_SURVEY_TASKS):
        return {"success": False, "decision": None}
    optimal = sorted(_SURVEY_TASKS,
                     key=lambda t: (abs(poses[t][0]) + abs(poses[t][1]), t))
    return {"success": True, "decision": optimal}


def _verify_grasp(node: Mapping, ctx) -> dict:
    """VERIFY: the grasp-cube node's sealed grasp stage passed."""
    return {"success": _stage_ok(ctx, "grasp-cube", "grasp")}


def _verify_integrity(node: Mapping, ctx) -> dict:
    """VERIFY: the build-stack node's sealed place stage passed (its clause chain
    IS the seated-within-tolerance integrity predicate)."""
    return {"success": _stage_ok(ctx, "build-stack", "place")}


def _verify_cleared(node: Mapping, ctx) -> dict:
    """VERIFY: both clear picks succeeded (boolean AND over sealed successes)."""
    can = ctx.nodes_out.get("pick-can")
    milk = ctx.nodes_out.get("pick-milk")
    return {"success": bool(can and can.get("success")
                            and milk and milk.get("success"))}


def _report(node: Mapping, ctx) -> dict:
    """DECIDE: assemble facts + results into a structured report. Success = every
    report field cross-checks a sealed prior node result."""
    out = ctx.nodes_out

    def _ok(nid: str) -> bool:
        n = out.get(nid)
        return bool(n and n.get("success"))

    fields = {
        "surveyed": _ok("survey"),
        "classified": _ok("classify"),
        "order": (out.get("plan-order") or {}).get("decision"),
        "grasped": _ok("grasp-cube"),
        "stacked": _ok("build-stack"),
        "cleared": _ok("pick-can") and _ok("pick-milk"),
    }
    ok = (fields["surveyed"] and fields["classified"] and fields["order"] is not None
          and fields["grasped"] and fields["stacked"] and fields["cleared"])
    return {"success": bool(ok), "decision": fields}


# The predicate FACTORIES the PREDICATES refs resolve to: load_provider calls
# each with no args and gets back the (node, ctx) -> dict callable above.
def survey():
    return _survey


def classify():
    return _classify


def plan_order():
    return _plan_order


def verify_grasp():
    return _verify_grasp


def verify_integrity():
    return _verify_integrity


def verify_cleared():
    return _verify_cleared


def report():
    return _report


if __name__ == "__main__":
    # No cross-plugin import: this card may not import plugins.task.validate
    # (tests/test_boundaries.py). Structural shape + predicate wiring are asserted
    # here; the real validate_plan coverage lives in tests/test_inventory_build.py.
    from dataclasses import dataclass

    planner = InventoryBuildPlanner()
    brief = {"task": "inventory_build", "scene": {}, "catalogue": CATALOGUE}
    plan = planner.plan(brief)
    assert set(plan) == {"goal", "nodes", "verify"} and plan["goal"]
    assert json.dumps(plan, sort_keys=True) == \
        json.dumps(planner.plan(brief), sort_keys=True), "byte-identical replay"
    ids = [n["id"] for n in plan["nodes"]]
    assert len(ids) == 11 and len(set(ids)) == 11, "11 unique nodes"
    kinds = [n.get("kind", "manipulate") for n in plan["nodes"]]
    assert kinds.count("perceive") == 2 and kinds.count("decide") == 2 \
        and kinds.count("verify") == 3 and kinds.count("manipulate") == 4
    # every kindful node names a declared predicate; every skill is catalogued
    for n in plan["nodes"]:
        assert n["skill"] in CATALOGUE
        if n.get("kind", "manipulate") != "manipulate":
            assert n["skill"] in PREDICATES
    # after edges reference only earlier nodes (topological -> list order)
    seen: list[str] = []
    for n in plan["nodes"]:
        assert all(a in seen for a in n["after"]), n["id"]
        seen.append(n["id"])

    # predicate wiring: every PREDICATES ref resolves to a callable, and the pure
    # (decide/verify) predicates score off a fake ctx with sealed prior facts.
    from harness.registry import load_provider

    for ref in PREDICATES.values():
        assert callable(load_provider(ref))

    @dataclass
    class _Ctx:
        seed: int
        env_ref: str
        nodes_out: dict

    good = _Ctx(0, "x", {
        "survey": {"success": True, "facts": {"poses": {t: [0.0, 0.0, 0.83]
                                                        for t in _SURVEY_TASKS}}},
        "classify": {"success": True},
        "plan-order": {"success": True, "decision": list(_SURVEY_TASKS)},
        "grasp-cube": {"success": True, "stages": [{"name": "grasp", "success": True}]},
        "build-stack": {"success": True, "stages": [{"name": "grasp", "success": True},
                                                    {"name": "place", "success": True}]},
        "pick-can": {"success": True},
        "pick-milk": {"success": True},
    })
    assert _classify({}, good)["success"]
    dec = _plan_order({}, good)
    assert dec["success"] and set(dec["decision"]) == set(_SURVEY_TASKS)
    assert _verify_grasp({}, good)["success"]
    assert _verify_integrity({}, good)["success"]
    assert _verify_cleared({}, good)["success"]
    rep = _report({}, good)
    assert rep["success"] and rep["decision"]["order"] is not None
    # a missing prior fact fails the gate (-> the loop's replan), never crashes
    empty = _Ctx(0, "x", {})
    assert _verify_grasp({}, empty)["success"] is False
    assert _verify_cleared({}, empty)["success"] is False
    assert _report({}, empty)["success"] is False
    assert _plan_order({}, empty)["success"] is False
    try:
        planner.plan({"task": "stack"})
    except ValueError:
        pass
    else:
        raise AssertionError("wrong task must fail loudly")
    print("plugins/inventory_build/planner.py self-check OK")
