"""A from-scratch ``harness.contracts.TaskPlanner`` for ``kitchen_thaw`` (M7 on
RoboCasa) plus the card's PREDICATES -- the machine oracles its perceive/decide/
verify nodes read off the ONE persistent MicrowaveThawingFridge episode's LIVE
state (docs/sim-adaptation.md §3).

Sibling of ``plugins/clear_workspace/planner.py`` (the robosuite M7 card), one
file, two halves:

* **The symbolic layer** -- CATALOGUE / ORACLES / a deterministic planner emitting
  a 15-node persistent kitchen mission:
    survey -> plan -> nav-fridge -> at-fridge -> grasp -> grasped ->
    nav-micro -> at-micro -> place -> inside -> close -> closed -> press -> on ->
    report
  The six behaviour nodes are the ``segment`` KIND (drive the SHARED live env for
  one sub-goal, no reset), each re-tasked by SEGMENT_SPECS to the stage driver that
  runs it; every ``verify`` reads a robocasa live-state predicate on the world as
  it now is. Unlike clear_workspace this chain is LINEAR (one meat, one appliance
  path), so a verify failure re-drives the SAME segment in the SAME world -- the
  base loop's fault->replan, bounded by ``max_replans`` (~2 retries) -- rather than
  dropping an object. The planner is a pure function of the task (no fault
  adaptation needed: the base loop skips finished nodes and re-runs the failed one).

* **The predicate layer** -- PREDICATES maps each kindful skill name to a zero-arg
  factory ref. Every VERIFY truth is a MACHINE predicate WRAPPING robocasa's own
  free oracle (``plugins.embodiment_robocasa.predicates``, reached by ref via
  ``load_provider`` -- never a sibling import; tests/test_boundaries.py) evaluated
  on the LIVE persistent env (``ctx.episode.env``). survey reads the live meat pose
  + archives the ``get_ep_meta`` scene fingerprint (into runtime_events and its
  sealed facts); report cross-checks the sealed segment outcomes against the same
  live oracle.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import numpy as np

from harness import opstream
from harness.registry import load_provider

# ── card constants ────────────────────────────────────────────────────────────
#: The robocasa card names the frozen food "meat" (MicrowaveThawingFridge's own
#: _check_success reads that name); its pose lands in obs under "meat_pos".
_POS_KEY = "meat_pos"

#: The linear appliance path: (segment id, segment skill, verify id, verify skill).
#: Each segment skill is re-tasked by SEGMENT_SPECS to a kitchen_driver stage; each
#: verify skill resolves (PREDICATES) to a robocasa live-state predicate wrapper.
_CHAIN: tuple[tuple[str, str, str, str], ...] = (
    ("nav-fridge", "nav_fridge", "at-fridge", "v_at_fridge"),
    ("grasp",      "grasp",      "grasped",   "v_grasped"),
    ("nav-micro",  "nav_micro",  "at-micro",  "v_carry"),
    ("place",      "place",      "inside",    "v_inside"),
    ("close",      "close",      "closed",    "v_closed"),
    ("press",      "press",      "on",        "v_on"),
)
_SEG_IDS: tuple[str, ...] = tuple(seg for seg, *_ in _CHAIN)


# ── symbolic layer: card vocabulary the validator types (plugins.task.validate) ──

#: skill name -> {arg name: required python type}. Every node is arg-free: a
#: segment's behaviour is fixed by its skill (routed through SEGMENT_SPECS), not by
#: a runtime arg, and the verifies name their object implicitly (the one meat).
CATALOGUE: dict[str, dict[str, type]] = {
    "survey": {}, "plan": {},
    "nav_fridge": {}, "v_at_fridge": {},
    "grasp": {}, "v_grasped": {},
    "nav_micro": {}, "v_carry": {},
    "place": {}, "v_inside": {},
    "close": {}, "v_closed": {},
    "press": {}, "v_on": {},
    "report": {},
}

#: Verify predicates a plan's ``verify`` LIST may name (symbolic labels the loop
#: folds into a failed node's attribution; the kindful verify NODES carry the real
#: machine oracle). ``staged`` gates each segment, ``reported`` gates report so the
#: list is never empty (validate rejects an unverified plan).
ORACLES: tuple[str, ...] = ("staged", "reported")

#: kindful skill name -> zero-arg factory ref (load_provider resolves it at the
#: doctor's Tier A, so a dead predicate reddens at mount, not mid-mission). The
#: segment skills are NOT here -- they carry no predicate, they drive.
PREDICATES: dict[str, str] = {
    "survey":      "plugins.mission_kitchen_thaw.planner:survey",
    "plan":        "plugins.mission_kitchen_thaw.planner:plan_ready",
    "v_at_fridge": "plugins.mission_kitchen_thaw.planner:v_at_fridge",
    "v_grasped":   "plugins.mission_kitchen_thaw.planner:v_grasped",
    "v_carry":     "plugins.mission_kitchen_thaw.planner:v_carry",
    "v_inside":    "plugins.mission_kitchen_thaw.planner:v_inside",
    "v_closed":    "plugins.mission_kitchen_thaw.planner:v_closed",
    "v_on":        "plugins.mission_kitchen_thaw.planner:v_on",
    "report":      "plugins.mission_kitchen_thaw.planner:report",
}

#: The ONE persistent episode's spec block (workload._episode_spec kwargs): the
#: MicrowaveThawingFridge kitchen scene, driven at the phase-3 operating point.
#: horizon well above the nominal so six segments + retries fit under robocasa's own
#: hard ceiling. percept_provider names the robocasa onboard percept so a future
#: governed recovery re-reads the right sensor (no bundle fires today, so it is
#: never touched -- declared for correctness, not for this E2E).
EPISODE: dict[str, Any] = {
    "task": "kitchen_thaw",
    "percept_noise": 0.012,
    "percept_provider": "plugins.embodiment_robocasa.percept:provider",
    "horizon": 2000,
}

#: Each segment skill -> the sub-goal task the kitchen_driver dispatches on (its
#: stage driver). This is the ONLY channel the behaviour reaches the driver: the
#: base re-tasks the persistent spec per node (ep.spec.child(task=...)) and the
#: composite driver's enter_segment reads spec.task. No stages overlay (these
#: sub-goals are scored by the driver's own done(), not a StageSpec chain).
SEGMENT_SPECS: dict[str, dict[str, Any]] = {
    "nav_fridge": {"task": "nav_fridge"},
    "grasp":      {"task": "grasp_meat"},
    "nav_micro":  {"task": "nav_micro"},
    "place":      {"task": "place_meat"},
    "close":      {"task": "close_door"},
    "press":      {"task": "press_start"},
}


def _emit_plan() -> Mapping:
    """The fixed 15-node kitchen mission. Round-tripped through sorted JSON so the
    emitted mapping is exactly its canonical byte form (determinism + replay)."""
    nodes: list[dict] = [
        {"id": "survey", "skill": "survey", "kind": "perceive", "args": {}, "after": []},
        {"id": "plan", "skill": "plan", "kind": "decide", "args": {}, "after": ["survey"]},
    ]
    verify_list: list[dict] = []
    prev = "plan"
    for seg_id, seg_skill, ver_id, ver_skill in _CHAIN:
        nodes.append({"id": seg_id, "skill": seg_skill, "kind": "segment",
                      "args": {}, "after": [prev]})
        nodes.append({"id": ver_id, "skill": ver_skill, "kind": "verify",
                      "args": {}, "after": [seg_id]})
        verify_list.append({"after": seg_id, "predicate": "staged"})
        prev = ver_id
    nodes.append({"id": "report", "skill": "report", "kind": "decide",
                  "args": {}, "after": [prev]})
    verify_list.append({"after": "report", "predicate": "reported"})
    return json.loads(json.dumps({
        "goal": "in ONE persistent kitchen episode, take the frozen meat from the "
                "fridge to the microwave and start it thawing -- survey, then for "
                "each appliance sub-goal drive a segment and verify the live state, "
                "retrying a failed sub-goal in the SAME world, closing with a "
                "machine report",
        "nodes": nodes,
        "verify": verify_list,
    }, sort_keys=True))


class KitchenThawPlanner:
    """Layer 1 ``harness.contracts.TaskPlanner``: a deterministic emitter of the
    fixed linear kitchen graph. The in-episode retry is the BASE loop re-running a
    failed node in the persistent world (bounded by max_replans), so this planner
    is a pure function of the task -- byte-identical every plan and replan."""

    def plan(self, brief: Mapping) -> Mapping:
        task = brief.get("task")
        if task != "kitchen_thaw":
            raise ValueError(
                f"KitchenThawPlanner only plans 'kitchen_thaw', got {task!r}")
        return _emit_plan()

    @property
    def identity(self) -> str:
        return "kitchen_thaw_planner@v1"


def provider(**params: Any) -> KitchenThawPlanner:
    return KitchenThawPlanner(**params)


# ── predicate layer: machine oracles over the LIVE persistent episode ─────────
# Every predicate reads ctx.episode -- the ONE MicrowaveThawingFridge world threaded
# through the graph. The verifies WRAP robocasa's own free oracle (reached by ref,
# never sibling import): the truth is always the persistent fixture/contact state as
# it currently is, not a reset preview.


def _episode(ctx):
    """The live persistent world, or a loud refusal. A kitchen_thaw run is always
    episodic, so ``None`` here is a wiring bug (the binding must declare episodic)."""
    ep = getattr(ctx, "episode", None)
    if ep is None:
        raise ValueError(
            "kitchen_thaw predicate reached with no persistent episode; the binding "
            "must declare episodic=true (workload threads ctx.episode)")
    return ep


def _wrap(robocasa_ref: str):
    """A VERIFY predicate: resolve a robocasa ``pred(env)->bool`` primitive by ref
    (once, at factory time) and adapt it to the base's ``(node, ctx)->{"success"}``
    contract, evaluated on the live persistent env."""
    prim = load_provider(robocasa_ref)

    def pred(node: Mapping, ctx) -> dict:
        return {"success": bool(prim(_episode(ctx).env))}

    return pred


# The kindful factories PREDICATES resolves to (load_provider calls each zero-arg).
def v_at_fridge():
    # after nav-fridge: the fridge is open and reachable -- ready to grasp.
    return _wrap("plugins.embodiment_robocasa.predicates:fridge_is_open")


def v_grasped():
    return _wrap("plugins.embodiment_robocasa.predicates:obj_grasped")


def v_carry():
    # after nav-micro: still holding the meat after the transport leg (a drop fails).
    return _wrap("plugins.embodiment_robocasa.predicates:obj_grasped")


def v_inside():
    return _wrap("plugins.embodiment_robocasa.predicates:obj_in_microwave")


def v_closed():
    return _wrap("plugins.embodiment_robocasa.predicates:microwave_closed")


def v_on():
    return _wrap("plugins.embodiment_robocasa.predicates:microwave_on")


def survey():
    """PERCEIVE: read the LIVE meat pose off the persistent obs and archive the
    ``get_ep_meta`` scene fingerprint (layout/style/lang) -- the deterministic
    scene identity for the episode seal. Emits it on the operational stream
    (durable in runtime_events.jsonl, no-op outside the runtime) AND seals it in
    the node's facts. Success = the meat pose is readable (a NaN/absurd read
    fails). Seals the privileged pose channel it read; the base meters it via
    privilege_cost, the same budget a critic pays."""
    def pred(node: Mapping, ctx) -> dict:
        ep = _episode(ctx)
        env, obs = ep.env, ep.obs
        meat = [float(v) for v in np.asarray(obs[_POS_KEY])[:3]]
        try:
            meta = env.get_ep_meta()
        except Exception:  # noqa: BLE001 -- a scene with no meta still surveys the pose
            meta = {}

        def _plain(v):
            # robocasa layout_id/style_id are numpy int64 -- coerce to plain python
            # so the scene fingerprint is JSON-serialisable (opstream.emit's
            # json.dumps SILENTLY swallows a non-serialisable value, dropping the
            # whole event: the fingerprint must survive the seal, not vanish).
            if v is None:
                return None
            if isinstance(v, (bool, str)):
                return v
            try:
                return int(v)
            except (TypeError, ValueError):
                return str(v)

        scene = {"lang": (str(meta["lang"]) if meta.get("lang") is not None else None),
                 "layout_id": _plain(getattr(env, "layout_id", meta.get("layout_id"))),
                 "style_id": _plain(getattr(env, "style_id", meta.get("style_id")))}
        opstream.emit("scene_meta", **{k: v for k, v in scene.items() if v is not None})
        ok = all(np.isfinite(c) and abs(c) < 100 for c in meat)
        return {"success": bool(ok), "facts": {"scene": scene, "meat_pos": meat},
                "privilege": ["privileged.object_z"]}
    return pred


def plan_ready():
    """DECIDE: a pure fn of the survey facts -> the fixed appliance sequence.
    Success = survey read a meat pose. Informational (the node order is fixed by the
    planner); records the sequence + scene the report cross-checks."""
    def pred(node: Mapping, ctx) -> dict:
        survey_out = ctx.nodes_out.get("survey") or {}
        facts = survey_out.get("facts") or {}
        ready = bool(survey_out.get("success") and "meat_pos" in facts)
        return {"success": ready,
                "decision": {"sequence": list(_SEG_IDS), "scene": facts.get("scene")}}
    return pred


#: The live checks the report reads directly (name -> robocasa primitive ref).
_LIVE_CHECKS: dict[str, str] = {
    "grasped": "plugins.embodiment_robocasa.predicates:obj_grasped",
    "in_microwave": "plugins.embodiment_robocasa.predicates:obj_in_microwave",
    "microwave_closed": "plugins.embodiment_robocasa.predicates:microwave_closed",
    "microwave_on": "plugins.embodiment_robocasa.predicates:microwave_on",
}


def report():
    """DECIDE: assemble the mission record and CROSS-CHECK it against the live
    oracle. Pairs each segment's sealed success with the current fixture/contact
    state; ``thawed`` is the headline (microwave on). Success = the record is a
    faithful account (every live check readable); an honest partial (mission aborted
    before report) simply never reaches this node, so a False mission is sealed by
    the abort, not a crashing report."""
    def pred(node: Mapping, ctx) -> dict:
        ep = _episode(ctx)
        out = ctx.nodes_out
        live: dict[str, Any] = {}
        ok = True
        for name, ref in _LIVE_CHECKS.items():
            try:
                live[name] = bool(load_provider(ref)(ep.env))
            except Exception:  # noqa: BLE001 -- an unreadable check breaks the report
                live[name] = None
                ok = False
        segments = {sid: bool((out.get(sid) or {}).get("success")) for sid in _SEG_IDS}
        decision = {"live": live, "segments": segments,
                    "thawed": bool(live.get("microwave_on"))}
        return {"success": bool(ok), "decision": decision}
    return pred


if __name__ == "__main__":
    # No cross-plugin import (this card may not import plugins.task.validate or a
    # sibling card -- tests/test_boundaries.py): structural shape + predicate wiring
    # are asserted here on fakes; the real validate_plan lives in
    # tests/test_mission_kitchen_thaw.py and the live E2E in the runtime.
    from dataclasses import dataclass
    from typing import Any as _Any

    plan = KitchenThawPlanner().plan({"task": "kitchen_thaw"})
    assert set(plan) == {"goal", "nodes", "verify"} and plan["goal"]
    ids = [n["id"] for n in plan["nodes"]]
    assert ids == ["survey", "plan",
                   "nav-fridge", "at-fridge", "grasp", "grasped",
                   "nav-micro", "at-micro", "place", "inside",
                   "close", "closed", "press", "on", "report"], ids
    assert len(ids) == 15, len(ids)
    kinds = [n.get("kind", "manipulate") for n in plan["nodes"]]
    assert kinds.count("segment") == 6 and kinds.count("verify") == 6
    assert kinds.count("perceive") == 1 and kinds.count("decide") == 2
    assert "manipulate" not in kinds  # a wholly kindful mission
    # every node names a catalogued skill; after edges are topological (earlier ids);
    # every non-segment kindful node names a declared predicate.
    seen: list[str] = []
    for n in plan["nodes"]:
        assert n["skill"] in CATALOGUE, n
        assert all(a in seen for a in n["after"]), n["id"]
        seen.append(n["id"])
        kind = n.get("kind", "manipulate")
        if kind in ("perceive", "decide", "verify"):
            assert n["skill"] in PREDICATES, n
    # every segment skill re-tasks through SEGMENT_SPECS
    for _, seg_skill, _, _ in _CHAIN:
        assert seg_skill in SEGMENT_SPECS, seg_skill
    # determinism: same brief -> byte-identical plan (and replan)
    assert json.dumps(plan, sort_keys=True) == \
        json.dumps(KitchenThawPlanner().plan({"task": "kitchen_thaw"}), sort_keys=True)

    # every PREDICATES ref resolves to a callable (base-clean: the robocasa
    # predicate module is importable, robocasa lazy inside its own methods)
    for ref in PREDICATES.values():
        assert callable(load_provider(ref))

    # the verify wrappers score off a fake live episode; the robocasa primitive is
    # faked by monkeypatching load_provider's target -- here we test the ADAPTER
    # shape directly on a fake predicate + ctx.
    @dataclass
    class _Ep:
        env: _Any
        obs: dict

    @dataclass
    class _Ctx:
        episode: _Any
        nodes_out: dict

    class _Env:
        def __init__(self, on: bool) -> None:
            self._on = on

        def get_ep_meta(self):
            return {"lang": "thaw the meat", "layout_id": 3, "style_id": 5}
        # a bare fridge/microwave stand-in so the wrapped robocasa preds are not
        # exercised here (that needs the sim) -- survey/plan/report are what we can
        # score on a fake, so those are the asserted paths.
        layout_id = 3
        style_id = 5

    obs = {_POS_KEY: [0.1, -0.2, 0.9]}
    ctx = _Ctx(_Ep(_Env(on=False), obs), {})
    sv = survey()({}, ctx)
    assert sv["success"] and sv["facts"]["scene"]["layout_id"] == 3
    assert sv["facts"]["meat_pos"] == [0.1, -0.2, 0.9]
    ctx.nodes_out["survey"] = sv
    pl = plan_ready()({}, ctx)
    assert pl["success"] and pl["decision"]["sequence"] == list(_SEG_IDS)

    # a NaN meat pose fails the survey honestly
    ctx_bad = _Ctx(_Ep(_Env(on=False), {_POS_KEY: [float("nan"), 0.0, 0.9]}), {})
    assert survey()({}, ctx_bad)["success"] is False

    # no-episode predicate refuses loudly
    try:
        survey()({}, _Ctx(None, {}))
    except ValueError:
        pass
    else:
        raise AssertionError("no-episode predicate must refuse loudly")

    # wrong task fails loudly
    try:
        KitchenThawPlanner().plan({"task": "stack"})
    except ValueError:
        pass
    else:
        raise AssertionError("wrong task must fail loudly")
    print("plugins/mission_kitchen_thaw/planner.py self-check OK")
