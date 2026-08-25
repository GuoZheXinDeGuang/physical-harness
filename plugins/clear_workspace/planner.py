"""A from-scratch ``harness.contracts.TaskPlanner`` for ``clear_workspace`` (M7)
plus the card's PREDICATES -- the machine oracles its perceive/decide/verify
nodes read off the ONE persistent episode's LIVE state.

Sibling of ``plugins/inventory_build/planner.py`` (M6), one file, two halves:

* **The symbolic layer** -- CATALOGUE / ORACLES / a deterministic, FAULT-ADAPTIVE
  planner. The graph is a ≥12-node persistent mission over the four robosuite
  mode-0 objects (milk, bread, cereal, can), all in ONE PickPlace episode:
  survey -> plan-order -> [clear-X -> verify-X]x4 -> sweep -> report. The four
  ``clear-X`` nodes are the new ``segment`` KIND (drive the SHARED live env for
  one sub-goal, no reset); ``verify-X`` reads that object's LIVE ``not_in_bin``.

  UNLIKE M6's pure table, this planner ADAPTS to faults within one run: a
  ``verify-X`` failure (the segment lifted the object but the frozen policy can
  not carry it to its bin) is UNRECOVERABLE by re-lifting, so the object is
  dropped from the next plan -- the "replan over the REMAINING sub-goals in the
  same context" of docs/m7-persistent-mission.md §2c. A ``clear-X`` failure (the
  grasp slipped) IS recoverable: the base loop re-dispatches it in the SAME world
  (in-episode retry) until ``_MAX_SEG_RETRIES``, then the object is skipped too.
  The skip set is derived PURELY from the fault stream (instance state the loop's
  own replan threads one fault at a time), so a given seed + fault sequence
  replays byte-identically -- determinism, adaptive, no reset.

* **The predicate layer** -- PREDICATES maps each kindful skill name to a
  "module:factory" ref. Every truth is a MACHINE predicate over the LIVE
  persistent obs / env handle (``ctx.episode``), never a model claim: survey/
  sweep read the current ``{Name}_pos``; verify-X reads ``env.not_in_bin`` on the
  world as it now is; report cross-checks the sealed segment results against that
  same live oracle. The base ``plugins.task.workload`` handlers resolve and score
  them; predicates reach nothing by sibling import (tests/test_boundaries.py).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import numpy as np

# ── card constants: the four mode-0 objects, canonical (object_to_id) order ───
#: lower-case object name (robosuite ``object_to_id`` key) -> its obs pose key.
_OBJECTS: tuple[str, ...] = ("milk", "bread", "cereal", "can")
#: A ``clear-X`` grasp that slips is re-driven in the SAME world this many times
#: before the object is skipped (docs/m7-persistent-mission.md §2c retry budget).
#: A ``verify-X`` failure skips immediately -- re-lifting can not place it.
_MAX_SEG_RETRIES = 2


def _pos_key(obj: str) -> str:
    """The obs key holding this object's live pose (``milk`` -> ``Milk_pos``)."""
    return f"{obj.capitalize()}_pos"


# ── symbolic layer: card vocabulary the validator types (plugins.task.validate) ──

#: skill name -> {arg name: required python type}. ``clear`` is the SEGMENT skill
#: (one binding, four objects -- the pick pattern, routed by its ``object`` arg
#: through SEGMENT_SPECS); ``verify_placed`` is the one live-state verify predicate
#: reused for every object, parameterised by its ``object`` arg.
CATALOGUE: dict[str, dict[str, type]] = {
    "survey": {},
    "plan_order": {},
    "clear": {"object": str},
    "verify_placed": {"object": str},
    "sweep": {},
    "report": {},
}

#: Verify predicates a plan's ``verify`` LIST may name. ``lifted`` gates each
#: segment's terminal (grasp+lift -- the pick_stages docstring's terminal half);
#: ``reported`` gates the final report node so the verify list is never empty even
#: when every object was skipped. Kindful nodes self-gate on their own machine
#: ``result["success"]`` and need no verify-list entry.
ORACLES: tuple[str, ...] = ("lifted", "reported")

#: kindful skill name -> "module:factory" ref of its machine oracle. Threaded onto
#: the brief by harness_runtime; every ref load_provider-resolves at the doctor's
#: Tier A, so a dead predicate reddens at mount, not mid-brief.
PREDICATES: dict[str, str] = {
    "survey": "plugins.clear_workspace.planner:survey",
    "plan_order": "plugins.clear_workspace.planner:plan_order",
    "verify_placed": "plugins.clear_workspace.planner:verify_placed",
    "sweep": "plugins.clear_workspace.planner:sweep",
    "report": "plugins.clear_workspace.planner:report",
}

#: The ONE persistent episode's spec block (workload._episode_spec kwargs): a
#: mode-0 PickPlace staging all four objects, driven at the phase-3 operating
#: point. horizon well above the nominal so four segments + retries + recovery
#: splices fit under robosuite's hard ceiling (docs/m7-persistent-mission.md §0).
EPISODE: dict[str, Any] = {
    "task": "clearall",
    "percept_noise": 0.012,
    "horizon": 1800,
}

#: The SEGMENT skill's per-sub-goal spec override (workload._segment_spec): route
#: the node's ``object`` arg to the per-object task whose object_key the driver
#: retargets on, and score the grasp with pick_stages. terminal_label defaults
#: False -> score_terminal falls to embodiment.success == lifted() (grasp + lift),
#: the honest shared sub-goal for a non-stack task.
SEGMENT_SPECS: dict[str, dict[str, Any]] = {
    "clear": {
        "task_by_object": {
            "milk": "clearmilk", "bread": "clearbread",
            "cereal": "clearcereal", "can": "clearcan",
        },
        "stages": "plugins.embodiment_robosuite.env:pick_stages",
    },
}


def _emit_plan(objects: list[str]) -> Mapping:
    """The mission graph over the still-live ``objects`` (skips already dropped).

    survey -> plan-order -> [clear-X (segment) -> verify-X (verify)] for each ->
    sweep -> report. Always ≥ the four framing nodes; with all four objects it is
    the full 12-node graph. Round-tripped through sorted JSON so the emitted
    mapping is exactly its canonical byte form (体检 determinism + replay)."""
    nodes: list[dict] = [
        {"id": "survey", "skill": "survey", "kind": "perceive",
         "args": {}, "after": []},
        {"id": "plan-order", "skill": "plan_order", "kind": "decide",
         "args": {}, "after": ["survey"]},
    ]
    prev = "plan-order"
    clears: list[str] = []
    verify_list: list[dict] = []
    for obj in objects:
        clear_id, verify_id = f"clear-{obj}", f"verify-{obj}"
        nodes.append({"id": clear_id, "skill": "clear", "kind": "segment",
                      "args": {"object": obj}, "after": [prev]})
        nodes.append({"id": verify_id, "skill": "verify_placed", "kind": "verify",
                      "args": {"object": obj}, "after": [clear_id]})
        verify_list.append({"after": clear_id, "predicate": "lifted"})
        clears.append(clear_id)
        prev = verify_id
    nodes.append({"id": "sweep", "skill": "sweep", "kind": "perceive",
                  "args": {}, "after": [prev]})
    nodes.append({"id": "report", "skill": "report", "kind": "decide",
                  "args": {}, "after": ["survey", "plan-order", *clears]})
    # report is always verified so the list is non-empty even with zero objects
    # left (validate rejects an empty verify list -- an unverified plan is vacuous).
    verify_list.append({"after": "report", "predicate": "reported"})
    return json.loads(json.dumps({
        "goal": "survey a cluttered workspace, decide a clearing order, then in "
                "ONE persistent episode clear each object with a live-state verify "
                "and an in-episode replan on failure, closing with a machine report",
        "nodes": nodes,
        "verify": verify_list,
    }, sort_keys=True))


class ClearWorkspacePlanner:
    """Layer 1 ``harness.contracts.TaskPlanner``: a deterministic, fault-adaptive
    graph emitter. Instance state (``_skip`` / ``_seg_faults``) accumulates from
    the fault stream the loop threads one at a time, so it is a pure function of
    (seed, fault sequence) -- adaptive within a run, byte-identical on replay."""

    def __init__(self) -> None:
        self._skip: set[str] = set()          # objects dropped from the mission
        self._seg_faults: dict[str, int] = {}  # object -> segment (grasp) fault count

    def _absorb_fault(self, fault: Mapping | None) -> None:
        """Route the loop's latest fault into the skip set: a verify-X failure
        drops object X (placement unrecoverable by re-lifting); a clear-X failure
        counts one segment retry and drops X only once the retry budget is spent."""
        if not fault or fault.get("kind") != "node_failure":
            return
        node = fault.get("node") or ""
        for obj in _OBJECTS:
            if node == f"verify-{obj}":
                self._skip.add(obj)
                return
            if node == f"clear-{obj}":
                self._seg_faults[obj] = self._seg_faults.get(obj, 0) + 1
                if self._seg_faults[obj] >= _MAX_SEG_RETRIES:
                    self._skip.add(obj)
                return

    def plan(self, brief: Mapping) -> Mapping:
        task = brief.get("task")
        if task != "clear_workspace":
            raise ValueError(
                f"ClearWorkspacePlanner only plans 'clear_workspace', got {task!r}")
        self._absorb_fault(brief.get("fault"))
        objects = [o for o in _OBJECTS if o not in self._skip]
        return _emit_plan(objects)

    @property
    def identity(self) -> str:
        return "clear_workspace_planner@v1"


def provider(**params: Any) -> ClearWorkspacePlanner:
    return ClearWorkspacePlanner(**params)


# ── predicate layer: machine oracles over the LIVE persistent episode ─────────
# Every predicate reads ctx.episode -- the ONE world threaded through the graph.
# perceive/decide/verify never reset a fresh env (M6 had to; M7 has the live
# world): the truth is always the persistent state as it currently is.


def _episode(ctx):
    """The live persistent world, or a loud refusal. Every M7 predicate needs it;
    a clear_workspace run is always episodic, so ``None`` here is a wiring bug."""
    ep = getattr(ctx, "episode", None)
    if ep is None:
        raise ValueError(
            "clear_workspace predicate reached with no persistent episode; the "
            "binding must declare episodic=true (workload threads ctx.episode)")
    return ep


def _in_bin(env, obs, obj: str) -> bool:
    """LIVE machine oracle: is ``obj`` currently resting in its target bin? Reads
    the object's live pose off the persistent obs and the simulator's own
    ``not_in_bin`` geometry (pick_place.py) -- the world as it is now, not a
    reset preview. ``in_bin`` = NOT ``not_in_bin``."""
    oid = env.object_to_id[obj]
    pos = np.asarray(obs[_pos_key(obj)])
    return not bool(env.not_in_bin(pos, oid))


def _pose(obs, obj: str) -> list[float]:
    return [float(v) for v in np.asarray(obs[_pos_key(obj)])[:3]]


def _survey(node: Mapping, ctx) -> dict:
    """PERCEIVE: read every object's LIVE pose off the persistent obs. Success =
    all four poses extractable and within table bounds (a NaN/absurd read fails).
    Seals the privileged pose channel it read -- the base meters it via
    privilege_cost, the SAME budget M6's reset-based survey paid for a preview."""
    obs = _episode(ctx).obs
    poses: dict[str, list[float]] = {}
    ok = True
    for obj in _OBJECTS:
        x, y, z = _pose(obs, obj)
        poses[obj] = [x, y, z]
        ok = ok and abs(x) < 1.0 and abs(y) < 1.0 and 0.5 < z < 1.5
    return {"success": ok, "facts": {"poses": poses},
            "privilege": ["privileged.object_z"]}


def _plan_order(node: Mapping, ctx) -> dict:
    """DECIDE: pure fn of survey facts -> a clearing order (nearest-to-origin
    first, L1 in the plane, deterministic). Success = the chosen order equals the
    machine-optimal order over those facts. Informational: node order is fixed
    (the planner emits the canonical object order); this records the geometry the
    report cross-checks, exactly as M6's plan-order does."""
    survey = ctx.nodes_out.get("survey")
    poses = ((survey or {}).get("facts") or {}).get("poses") or {}
    if set(poses) != set(_OBJECTS):
        return {"success": False, "decision": None}
    optimal = sorted(_OBJECTS, key=lambda o: (abs(poses[o][0]) + abs(poses[o][1]), o))
    return {"success": True, "decision": optimal}


def _verify_placed(node: Mapping, ctx) -> dict:
    """VERIFY: the object named in this node's args is resting in its bin RIGHT
    NOW -- ``_in_bin`` on the live persistent env + obs. On False the loop's
    existing fault->replan fires; the planner then drops this object and continues
    the others in the SAME world (docs/m7-persistent-mission.md §2c). This is the
    live-state, consequence-carrying verify that M6 (reset-based) could not have."""
    ep = _episode(ctx)
    obj = (node.get("args") or {}).get("object")
    if obj not in _OBJECTS:
        return {"success": False}
    return {"success": _in_bin(ep.env, ep.obs, obj)}


def _sweep(node: Mapping, ctx) -> dict:
    """PERCEIVE (integrity sweep): re-read all four LIVE poses one final time and
    partition them into placed vs stragglers (still on the table). OBSERVE-only --
    success = every pose was readable, so the sweep never itself blocks the loop;
    the straggler list is a fact the report consumes. (Sending a straggler back
    through its clear is the in-episode recovery surface the design flags as
    future work, §4 -- not gated here.)"""
    obs = _episode(ctx).obs
    env = _episode(ctx).env
    placed: list[str] = []
    stragglers: list[str] = []
    ok = True
    for obj in _OBJECTS:
        try:
            (placed if _in_bin(env, obs, obj) else stragglers).append(obj)
        except Exception:  # noqa: BLE001 -- an unreadable pose fails the sweep
            ok = False
    return {"success": ok, "facts": {"placed": placed, "stragglers": stragglers},
            "privilege": ["privileged.object_z"]}


def _report(node: Mapping, ctx) -> dict:
    """DECIDE: assemble the mission record and CROSS-CHECK it against the live
    oracle. For each object the report pairs its sealed segment success (did the
    grasp lift it?) with the LIVE ``_in_bin`` reading; success = the record is a
    faithful, complete account of the persistent world (all four objects
    partitioned, placed ⊆ readable). The scientific outcome -- HOW MANY were
    placed -- lives in ``decision['placed']``; an honest zero-placed run still
    produces a TRUE report (accurate record), so the mission completes cleanly
    even when the frozen policy clears nothing (honest null valid)."""
    ep = _episode(ctx)
    out = ctx.nodes_out
    lifted = {obj: bool((out.get(f"clear-{obj}") or {}).get("success"))
              for obj in _OBJECTS}
    placed_live: dict[str, bool] = {}
    ok = True
    for obj in _OBJECTS:
        try:
            placed_live[obj] = _in_bin(ep.env, ep.obs, obj)
        except Exception:  # noqa: BLE001 -- an unreadable pose breaks the report
            ok = False
            placed_live[obj] = False
    placed = [o for o in _OBJECTS if placed_live[o]]
    stragglers = [o for o in _OBJECTS if not placed_live[o]]
    order = (out.get("plan-order") or {}).get("decision")
    decision = {"order": order, "lifted": lifted, "placed": placed,
                "stragglers": stragglers, "cleared": len(placed)}
    # faithful record: every object accounted for, and nothing reported placed
    # that was never even lifted (the live oracle and the sealed segments agree).
    ok = ok and set(placed) | set(stragglers) == set(_OBJECTS) \
        and all(lifted[o] for o in placed)
    return {"success": bool(ok), "decision": decision}


# The predicate FACTORIES the PREDICATES refs resolve to: load_provider calls each
# with no args and gets back the (node, ctx) -> dict callable above.
def survey():
    return _survey


def plan_order():
    return _plan_order


def verify_placed():
    return _verify_placed


def sweep():
    return _sweep


def report():
    return _report


if __name__ == "__main__":
    # No cross-plugin import (this card may not import plugins.task.validate --
    # tests/test_boundaries.py): structural shape + fault-adaptive routing +
    # predicate wiring are asserted here on fakes; the real validate_plan and live
    # smoke coverage live in tests/test_clear_workspace.py + the sim smoke.
    from dataclasses import dataclass, field

    planner = ClearWorkspacePlanner()
    brief = {"task": "clear_workspace", "scene": {}, "catalogue": CATALOGUE}
    plan = planner.plan(brief)
    assert set(plan) == {"goal", "nodes", "verify"} and plan["goal"]
    ids = [n["id"] for n in plan["nodes"]]
    assert ids == ["survey", "plan-order",
                   "clear-milk", "verify-milk", "clear-bread", "verify-bread",
                   "clear-cereal", "verify-cereal", "clear-can", "verify-can",
                   "sweep", "report"], ids
    assert len(ids) == 12, len(ids)
    kinds = [n.get("kind", "manipulate") for n in plan["nodes"]]
    assert kinds.count("segment") == 4 and kinds.count("verify") == 4
    assert kinds.count("perceive") == 2 and kinds.count("decide") == 2
    assert "manipulate" not in kinds  # a wholly kindful mission
    # every kindful/segment node names a catalogued skill; every non-segment
    # kindful node names a declared predicate; after edges are topological.
    seen: list[str] = []
    for n in plan["nodes"]:
        assert n["skill"] in CATALOGUE, n
        assert all(a in seen for a in n["after"]), n["id"]
        seen.append(n["id"])
        kind = n.get("kind", "manipulate")
        if kind in ("perceive", "decide", "verify"):
            assert n["skill"] in PREDICATES, n

    # determinism: same brief -> byte-identical plan
    assert json.dumps(plan, sort_keys=True) == \
        json.dumps(ClearWorkspacePlanner().plan(brief), sort_keys=True)

    # FAULT-ADAPTIVE routing: a verify-X fault drops object X from the next plan
    p2 = ClearWorkspacePlanner()
    p2.plan(brief)
    dropped = p2.plan({**brief, "fault": {"kind": "node_failure", "node": "verify-bread"}})
    d_ids = [n["id"] for n in dropped["nodes"]]
    assert "clear-bread" not in d_ids and "verify-bread" not in d_ids, d_ids
    assert "clear-milk" in d_ids and "clear-can" in d_ids  # the rest survive
    # a clear-X grasp fault is a RETRY first (object stays), a skip only past budget
    p3 = ClearWorkspacePlanner()
    p3.plan(brief)
    after1 = p3.plan({**brief, "fault": {"kind": "node_failure", "node": "clear-can"}})
    assert "clear-can" in [n["id"] for n in after1["nodes"]], "first grasp fault retries"
    after2 = p3.plan({**brief, "fault": {"kind": "node_failure", "node": "clear-can"}})
    assert "clear-can" not in [n["id"] for n in after2["nodes"]], "budget spent -> skip"
    # even with every object skipped the verify list stays non-empty (report gate)
    p4 = ClearWorkspacePlanner()
    for obj in _OBJECTS:
        p4.plan({**brief, "fault": {"kind": "node_failure", "node": f"verify-{obj}"}})
    bare = p4.plan(brief)
    assert [n["id"] for n in bare["nodes"]] == ["survey", "plan-order", "sweep", "report"]
    assert bare["verify"] == [{"after": "report", "predicate": "reported"}]

    # predicate wiring: every PREDICATES ref resolves to a callable
    from harness.registry import load_provider
    for ref in PREDICATES.values():
        assert callable(load_provider(ref))

    # the pure/live predicates score off a fake ctx carrying a live episode + facts
    @dataclass
    class _Bins:
        placed: set = field(default_factory=set)
        object_to_id = {"milk": 0, "bread": 1, "cereal": 2, "can": 3}

        def not_in_bin(self, pos, oid):
            name = ["milk", "bread", "cereal", "can"][oid]
            return name not in self.placed

    @dataclass
    class _Ep:
        env: Any
        obs: dict

    @dataclass
    class _Ctx:
        episode: Any
        nodes_out: dict

    obs = {_pos_key(o): [0.0, 0.0, 0.85] for o in _OBJECTS}
    env = _Bins(placed={"milk"})
    ctx = _Ctx(_Ep(env, obs), {})
    sv = _survey({}, ctx)
    assert sv["success"] and set(sv["facts"]["poses"]) == set(_OBJECTS)
    ctx.nodes_out["survey"] = sv
    po = _plan_order({}, ctx)
    assert po["success"] and set(po["decision"]) == set(_OBJECTS)
    ctx.nodes_out["plan-order"] = po
    # verify-X reads the live bin: milk placed -> True, bread not -> False
    assert _verify_placed({"args": {"object": "milk"}}, ctx)["success"] is True
    assert _verify_placed({"args": {"object": "bread"}}, ctx)["success"] is False
    sw = _sweep({}, ctx)
    assert sw["facts"]["placed"] == ["milk"] and set(sw["facts"]["stragglers"]) == \
        {"bread", "cereal", "can"}
    # report: only milk's segment succeeded + only milk is live-placed -> faithful
    ctx.nodes_out["clear-milk"] = {"success": True}
    rep = _report({}, ctx)
    assert rep["success"] and rep["decision"]["placed"] == ["milk"] \
        and rep["decision"]["cleared"] == 1
    # honest null: nothing placed, nothing lifted -> STILL a faithful (True) report
    ctx2 = _Ctx(_Ep(_Bins(), obs), {"plan-order": po})
    rep0 = _report({}, ctx2)
    assert rep0["success"] and rep0["decision"]["cleared"] == 0
    # a report claiming a placed object that was never lifted is UNfaithful (False)
    ctx3 = _Ctx(_Ep(_Bins(placed={"can"}), obs), {})  # can live-placed, no segment sealed
    assert _report({}, ctx3)["success"] is False

    # wrong task fails loudly; a predicate with no episode refuses loudly
    try:
        planner.plan({"task": "stack"})
    except ValueError:
        pass
    else:
        raise AssertionError("wrong task must fail loudly")
    try:
        _survey({}, _Ctx(None, {}))
    except ValueError:
        pass
    else:
        raise AssertionError("no-episode predicate must refuse loudly")
    print("plugins/clear_workspace/planner.py self-check OK")
