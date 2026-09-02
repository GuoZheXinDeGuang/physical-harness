"""``harness.contracts.TaskPlanner`` + PREDICATES for ``steam_prep`` (M7 on
RoboCasa, MultistepSteaming): the five-phase steaming prep with TEMPORAL
constraints -- faucet on, vegetable into the running sink, faucet off,
vegetable into the pot, pot onto the chosen burner.

    survey -> plan -> faucet-on -> water-on -> grasp-veg -> veg-grasped ->
    sink-veg -> veg-in-sink -> faucet-off -> water-off -> regrasp-veg ->
    veg-held -> carry-veg -> at-pot -> pot-veg -> veg-in-pot -> grasp-pot ->
    pot-held -> burner-pot -> pot-on-burner -> report      (21 nodes)

GRAPH-FIRST card: the graph, predicates and vault/UI presence are complete; the
driver surface is honestly partial -- there is NO sink-faucet driver yet (the
handle needs a hinge-arc torque the scripted OSC cannot produce, the phase-3
door finding), so the faucet segments run a stub that burns a small cap and the
water-on verify fails: the declared frontier, xfail-marked in
tests/test_robocasa_missions.py as "awaiting sink driver". The temporal
constraint itself needs no harness bookkeeping: MultistepSteaming's own
_check_success accumulates ``water_was_turned_on`` / ``vegetable_was_in_sink``
per step, and the graph's verify ORDER (veg must verify in-sink BETWEEN
water-on and water-off) enforces the sequencing; report seals the env's own
accumulated flags.

SECURE_DZ discipline: every grasp verify requires the object's live z to have
risen above its entry z -- the first veg grasp and the pot grasp read the
surveyed z; the REGRASP (veg now in the sink basin, survey stale) reads the z
the veg-in-sink verify sealed. Never the bare check_obj_grasped latch.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import numpy as np

from harness import opstream
from harness.registry import load_provider
from harness.skill_library import RECORDS, catalogue_of, segment_specs, select

# ── card constants ────────────────────────────────────────────────────────────
VEG, POT = "vegetable1", "pot"
SECURE_DZ = 0.08
_P = "plugins.embodiment_robocasa.predicates"

#: (segment id, segment skill, verify id, verify skill) -- fully linear.
_CHAIN: tuple[tuple[str, str, str, str], ...] = (
    ("faucet-on",   "faucet_on",   "water-on",      "v_water_on"),
    ("grasp-veg",   "grasp_veg",   "veg-grasped",   "v_veg_grasped"),
    ("sink-veg",    "sink_veg",    "veg-in-sink",   "v_veg_in_sink"),
    ("faucet-off",  "faucet_off",  "water-off",     "v_water_off"),
    ("regrasp-veg", "regrasp_veg", "veg-held",      "v_veg_held"),
    ("carry-veg",   "carry_veg",   "at-pot",        "v_at_pot"),
    ("pot-veg",     "pot_veg",     "veg-in-pot",    "v_veg_in_pot"),
    ("grasp-pot",   "grasp_pot",   "pot-held",      "v_pot_held"),
    ("burner-pot",  "burner_pot",  "pot-on-burner", "v_pot_on_burner"),
)
_SEG_IDS: tuple[str, ...] = tuple(seg for seg, *_ in _CHAIN)


# ── symbolic layer ────────────────────────────────────────────────────────────

#: The card's slice of the static skill library (skill-library/records): the
#: symbolic contracts Supported/Covered judge; CATALOGUE is its typed view.
SKILL_RECORDS = select(RECORDS, "robocasa", (
    "survey", "plan", "report",
    *(skill for _, skill, _, _ in _CHAIN),
    *(vskill for _, _, _, vskill in _CHAIN if vskill)))
CATALOGUE: dict[str, dict[str, type]] = catalogue_of(SKILL_RECORDS)
#: sigma0 facts the card declares true at reset (no live predicate binding
#: exists for the library vocabulary yet), the base of every Supported chain.
INITIAL_FACTS: tuple[str, ...] = tuple([f"present({VEG})", f"present({POT})", "gripper_free()"])

ORACLES: tuple[str, ...] = ("staged", "reported")

PREDICATES: dict[str, str] = {
    "survey": "plugins.mission_steam_prep.planner:survey",
    "plan": "plugins.mission_steam_prep.planner:plan_ready",
    "report": "plugins.mission_steam_prep.planner:report",
    **{vskill: f"plugins.mission_steam_prep.planner:{vskill}"
       for _, _, _, vskill in _CHAIN},
}

EPISODE: dict[str, Any] = {
    "task": "steam_prep",
    "percept_noise": 0.012,
    "percept_provider": "plugins.embodiment_robocasa.percept:provider",
    "horizon": 6000,
}

SEGMENT_SPECS: dict[str, dict[str, Any]] = segment_specs(SKILL_RECORDS, "robocasa")


def _emit_plan() -> Mapping:
    nodes: list[dict] = [
        {"id": "survey", "skill": "survey", "kind": "perceive", "args": {},
         "after": []},
        {"id": "plan", "skill": "plan", "kind": "decide", "args": {},
         "after": ["survey"]},
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
        "goal": "in ONE persistent kitchen episode, prep the steaming: turn the "
                "sink on, rinse the vegetable in the running sink, turn the "
                "sink off, move the vegetable to the pot, and set the pot on "
                "the chosen burner -- five phases with temporal constraints, "
                "each segment verified on live state and retried in the SAME "
                "world, closing with a machine report",
        "nodes": nodes,
        "verify": verify_list,
    }, sort_keys=True))


class SteamPrepPlanner:
    """Deterministic emitter of the fixed 21-node five-phase mission graph."""

    def plan(self, brief: Mapping) -> Mapping:
        task = brief.get("task")
        if task != "steam_prep":
            raise ValueError(
                f"SteamPrepPlanner only plans 'steam_prep', got {task!r}")
        return _emit_plan()

    @property
    def identity(self) -> str:
        return "steam_prep_planner@v1"


def provider(**params: Any) -> SteamPrepPlanner:
    return SteamPrepPlanner(**params)


# ── predicate layer ───────────────────────────────────────────────────────────

def _episode(ctx):
    ep = getattr(ctx, "episode", None)
    if ep is None:
        raise ValueError(
            "steam_prep predicate reached with no persistent episode; the "
            "binding must declare episodic=true (workload threads ctx.episode)")
    return ep


def _obj_z(env, name: str) -> float:
    return float(np.asarray(env.sim.data.body_xpos[env.obj_body_id[name]])[2])


def _plain(v):
    if v is None or isinstance(v, (bool, str)):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        return str(v)


def survey():
    """PERCEIVE: vegetable + pot LIVE poses, the episode's chosen burner knob,
    and the scene fingerprint, sealed as facts."""
    def pred(node: Mapping, ctx) -> dict:
        ep = _episode(ctx)
        env, obs = ep.env, ep.obs
        objects = {o: [float(v) for v in np.asarray(obs[f"{o}_pos"])[:3]]
                   for o in (VEG, POT)}
        try:
            meta = env.get_ep_meta()
        except Exception:  # noqa: BLE001 -- a scene with no meta still surveys
            meta = {}
        scene = {"lang": (str(meta["lang"]) if meta.get("lang") is not None else None),
                 "layout_id": _plain(getattr(env, "layout_id", meta.get("layout_id"))),
                 "style_id": _plain(getattr(env, "style_id", meta.get("style_id"))),
                 "knob": _plain(getattr(env, "knob", None))}
        opstream.emit("scene_meta", **{k: v for k, v in scene.items() if v is not None})
        ok = all(np.isfinite(v) and abs(v) < 100
                 for pos in objects.values() for v in pos)
        return {"success": bool(ok),
                "facts": {"scene": scene, "objects": objects},
                "privilege": ["privileged.object_z"]}
    return pred


def plan_ready():
    """DECIDE: the fixed five-phase sequence, ready when the survey read both
    objects. Informational (the order is the planner's); sealed for the report."""
    def pred(node: Mapping, ctx) -> dict:
        sv = ctx.nodes_out.get("survey") or {}
        facts = sv.get("facts") or {}
        ready = bool(sv.get("success")
                     and {VEG, POT} <= set(facts.get("objects") or {}))
        return {"success": ready,
                "decision": {"sequence": list(_SEG_IDS),
                             "scene": facts.get("scene")}}
    return pred


def _wrap(ref: str, params: dict | None = None):
    prim = load_provider(ref, params)

    def factory():
        def pred(node: Mapping, ctx) -> dict:
            return {"success": bool(prim(_episode(ctx).env))}
        return pred
    return factory


v_water_on = _wrap(f"{_P}:sink_water", {"on": True})
v_water_off = _wrap(f"{_P}:sink_water", {"on": False})
v_at_pot = _wrap(f"{_P}:base_near_obj", {"name": POT, "th": 1.5})


def v_veg_grasped():
    """SECURE_DZ vs the surveyed entry z, or the grasp segment's own sealed
    SECURE_DZ success after a disturbed retry (see mission_recycle_cans)."""
    grasped = load_provider(f"{_P}:obj_grasped_any", {"name": VEG})

    def pred(node: Mapping, ctx) -> dict:
        ep = _episode(ctx)
        facts = (ctx.nodes_out.get("survey") or {}).get("facts") or {}
        pos0 = (facts.get("objects") or {}).get(VEG)
        if not pos0:
            return {"success": False}
        risen = _obj_z(ep.env, VEG) > float(pos0[2]) + SECURE_DZ
        seg_secure = bool((ctx.nodes_out.get("grasp-veg") or {}).get("success"))
        return {"success": bool(grasped(ep.env) and (risen or seg_secure))}
    return pred


def v_veg_in_sink():
    """In the sink basin AND released; SEALS the veg's basin z as a fact -- the
    regrasp verify's entry-z reference (the surveyed z is stale by now)."""
    inside = load_provider(f"{_P}:obj_in_fixture",
                           {"name": VEG, "fixture": "sink"})
    released = load_provider(f"{_P}:gripper_far", {"name": VEG})

    def pred(node: Mapping, ctx) -> dict:
        env = _episode(ctx).env
        z = _obj_z(env, VEG)
        return {"success": bool(inside(env) and released(env)),
                "facts": {"veg_z": z}}
    return pred


def v_veg_held():
    """SECURE_DZ for the REGRASP: entry z is the basin z sealed by veg-in-sink,
    not the stale survey."""
    grasped = load_provider(f"{_P}:obj_grasped_any", {"name": VEG})

    def pred(node: Mapping, ctx) -> dict:
        ep = _episode(ctx)
        sealed = (ctx.nodes_out.get("veg-in-sink") or {}).get("facts") or {}
        z0 = sealed.get("veg_z")
        if z0 is None:
            return {"success": False}
        risen = _obj_z(ep.env, VEG) > float(z0) + SECURE_DZ
        seg_secure = bool((ctx.nodes_out.get("regrasp-veg") or {}).get("success"))
        return {"success": bool(grasped(ep.env) and (risen or seg_secure))}
    return pred


def v_veg_in_pot():
    inside = load_provider(f"{_P}:obj_in_receptacle",
                           {"name": VEG, "receptacle": POT})
    released = load_provider(f"{_P}:gripper_far", {"name": VEG})

    def pred(node: Mapping, ctx) -> dict:
        env = _episode(ctx).env
        return {"success": bool(inside(env) and released(env))}
    return pred


def v_pot_held():
    """SECURE_DZ vs the surveyed pot z, or the grasp segment's own sealed
    SECURE_DZ success after a disturbed retry (see mission_recycle_cans)."""
    grasped = load_provider(f"{_P}:obj_grasped_any", {"name": POT})

    def pred(node: Mapping, ctx) -> dict:
        ep = _episode(ctx)
        facts = (ctx.nodes_out.get("survey") or {}).get("facts") or {}
        pos0 = (facts.get("objects") or {}).get(POT)
        if not pos0:
            return {"success": False}
        risen = _obj_z(ep.env, POT) > float(pos0[2]) + SECURE_DZ
        seg_secure = bool((ctx.nodes_out.get("grasp-pot") or {}).get("success"))
        return {"success": bool(grasped(ep.env) and (risen or seg_secure))}
    return pred


v_pot_on_burner = _wrap(f"{_P}:pot_on_chosen_burner", {"name": POT})


def report():
    """DECIDE: cross-check sealed segments against the live oracle AND seal
    MultistepSteaming's own accumulated temporal flags; headline
    ``steam_ready`` is the env's _check_success (all five phases in order)."""
    water = load_provider(f"{_P}:sink_water", {"on": True})
    in_pot = load_provider(f"{_P}:obj_in_receptacle",
                           {"name": VEG, "receptacle": POT})
    on_burner = load_provider(f"{_P}:pot_on_chosen_burner", {"name": POT})

    def pred(node: Mapping, ctx) -> dict:
        ep = _episode(ctx)
        out = ctx.nodes_out
        live: dict[str, Any] = {}
        ok = True
        for name, prim in (("water_on", water), ("veg_in_pot", in_pot),
                           ("pot_on_burner", on_burner)):
            try:
                live[name] = bool(prim(ep.env))
            except Exception:  # noqa: BLE001 -- an unreadable check breaks the report
                live[name] = None
                ok = False
        try:
            steam_ready = bool(ep.env._check_success())
        except Exception:  # noqa: BLE001
            steam_ready, ok = None, False
        # the env's OWN cross-step temporal flags (accumulated per _check_success
        # call) -- the sequencing truth the graph order enforced.
        flags = {f: bool(getattr(ep.env, f))
                 for f in ("water_was_turned_on", "vegetable_was_in_sink")
                 if hasattr(ep.env, f)}
        segments = {sid: bool((out.get(sid) or {}).get("success"))
                    for sid in _SEG_IDS}
        return {"success": bool(ok),
                "decision": {"live": live, "flags": flags,
                             "segments": segments, "steam_ready": steam_ready}}
    return pred


if __name__ == "__main__":
    plan = SteamPrepPlanner().plan({"task": "steam_prep"})
    assert set(plan) == {"goal", "nodes", "verify"} and plan["goal"]
    ids = [n["id"] for n in plan["nodes"]]
    assert len(ids) == 21 and len(set(ids)) == 21, len(ids)
    kinds = [n.get("kind", "manipulate") for n in plan["nodes"]]
    assert kinds.count("segment") == 9 and kinds.count("verify") == 9
    assert kinds.count("perceive") == 1 and kinds.count("decide") == 2
    assert "manipulate" not in kinds
    # the temporal ORDER is structural: veg-in-sink sits strictly between
    # water-on and water-off in the linear chain
    assert ids.index("water-on") < ids.index("veg-in-sink") < ids.index("water-off")
    seen: list[str] = []
    for n in plan["nodes"]:
        assert n["skill"] in CATALOGUE, n
        assert all(a in seen for a in n["after"]), n["id"]
        seen.append(n["id"])
        if n.get("kind") in ("perceive", "decide", "verify"):
            assert n["skill"] in PREDICATES, n
    for _, seg_skill, _, _ in _CHAIN:
        assert seg_skill in SEGMENT_SPECS, seg_skill
    assert json.dumps(plan, sort_keys=True) == json.dumps(
        SteamPrepPlanner().plan({"task": "steam_prep"}), sort_keys=True)
    for ref in PREDICATES.values():
        assert callable(load_provider(ref))

    # regrasp verify reads the sealed basin z, refuses without it
    from dataclasses import dataclass

    @dataclass
    class _Ctx:
        episode: Any
        nodes_out: dict

    @dataclass
    class _Ep:
        env: Any
        obs: dict
    assert v_veg_held()({}, _Ctx(_Ep(None, {}), {}))["success"] is False
    try:
        SteamPrepPlanner().plan({"task": "stack"})
    except ValueError:
        pass
    else:
        raise AssertionError("wrong task must fail loudly")
    print("plugins/mission_steam_prep/planner.py self-check OK")
