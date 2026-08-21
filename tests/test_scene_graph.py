"""graph.scene goes live (round 82): two providers, one schema, sha discipline.

The load-bearing claims, in test order:

(a) base_profile's graph.scene ref string is byte-identical to before, so
    MountPlan.sha does not move — the behavior behind the unchanged ref goes
    from empty stub to SimSceneGraph.
(b) zos_world_bundle swaps ONLY graph.scene (to WorldSceneGraph) and mints a
    new sha — the sawyer_bundle story, replayed for the real-robot bridge.
(c) SimSceneGraph mirrors features.py semantics: _OBJECT_KEYS resolution,
    planar eef-to-object distance (features.py:67), absent keys omitted never
    faked — all without numpy in the import chain.
(d) WorldSceneGraph ports zos.world.relative's bearing math byte-for-byte
    (goldens produced by zos's own function) and propagates pose staleness.
"""

from __future__ import annotations

import sys

from harness import Kernel, resolve_plan
from harness.definitions import CAPABILITIES
from plugins.graphs import SimSceneGraph, WorldSceneGraph
from profiles import base_profile, zos_world_bundle

CONSUMER = "scene-test"

#: The zos world.py __main__ fixture, serialized the way World.snapshot emits it.
_NOW = 1000.0
WORLD_SNAP = {
    "t": _NOW,
    "pose": {"x": 2.31, "y": -0.88, "yaw": 0.54},
    "places": {"start": {"x": 0.0, "y": 0.0, "yaw": 0.0},
               "ceo_office_door": {"x": 3.1, "y": 8.2, "yaw": 0.0},
               "admin_office": {"x": 1.0, "y": 2.0, "yaw": 0.0}},
    "objects": {
        "bottle_1": {"label": "bottle", "x": 7.62, "y": 2.11, "z": 0.78,
                     "conf": 0.91, "t_seen": _NOW, "source": "vlm"},
        "bottle_2": {"label": "bottle", "x": 10.6, "y": 2.11, "z": 0.10,
                     "conf": 0.60, "t_seen": _NOW, "source": "vlm"}},
    "authority": "IDLE", "t_pose": _NOW - 0.2, "odom_age": 0.2, "t_places": _NOW,
}


# --- (a) sha stability: the base_profile ref string never moved --------------

def test_base_profile_scene_ref_is_byte_identical_and_resolves_sim_graph():
    plan = resolve_plan(base_profile())
    mount = {m.capability: m for m in plan.mounts}["graph.scene"]
    assert mount.provider == "plugins.graphs:scene_graph_provider", \
        "the base_profile ref string is MountPlan.sha identity — it must not move"
    assert mount.params == {}

    k = Kernel(CAPABILITIES)
    k.mount(plan)
    assert isinstance(k.resolve("graph.scene", consumer=CONSUMER), SimSceneGraph)


# --- (b) zos_world_bundle: one mount swapped, new identity ------------------

def test_zos_world_bundle_swaps_only_scene_and_mints_a_new_sha():
    base = resolve_plan(base_profile())
    world = resolve_plan(base_profile(), bundles=(zos_world_bundle(),))
    assert world.sha() != base.sha(), "the mount is experiment identity: sha must move"

    base_by_cap = {m.capability: m for m in base.mounts}
    world_by_cap = {m.capability: m for m in world.mounts}
    assert world_by_cap["graph.scene"].provider == "plugins.graphs:world_scene_graph_provider"
    for capability, mount in base_by_cap.items():
        if capability != "graph.scene":
            assert mount == world_by_cap[capability], "only graph.scene may differ"

    k = Kernel(CAPABILITIES)
    k.mount(world)
    assert isinstance(k.resolve("graph.scene", consumer=CONSUMER), WorldSceneGraph)


# --- (c) SimSceneGraph: features.py semantics, no numpy ---------------------

def test_sim_snapshot_planar_relation_and_absent_keys_omitted():
    sg = SimSceneGraph().snapshot({"robot0_eef_pos": [0.1, 0.0, 1.0],
                                   "cubeA_pos": [0.4, 0.4, 0.83]})
    assert {n["id"] for n in sg["nodes"]} == {"eef", "cubeA"}, \
        "cubeB is absent from the obs: it must be omitted, not faked"
    # hypot(0.3, 0.4) = 0.5 — the features.py:67 planar distance, as the rel string
    assert sg["relations"] == [{"from": "eef", "to": "cubeA", "rel": "0.500m planar"}]

    empty = SimSceneGraph().snapshot({})
    assert empty == {"frame": "world", "t": 0.0, "nodes": [], "relations": []}


def test_graphs_module_never_drags_numpy():
    # zos's light venv imports plugins.graphs; numpy there is a boot failure.
    import pathlib
    import subprocess

    src = ("import sys; import plugins.graphs; "
           "sys.exit(1 if 'numpy' in sys.modules else 0)")
    proc = subprocess.run([sys.executable, "-c", src], capture_output=True, check=False,
                          cwd=str(pathlib.Path(__file__).resolve().parents[1]))
    assert proc.returncode == 0, proc.stderr.decode()


# --- (d) WorldSceneGraph: ported bearings, honest staleness -----------------

def test_world_snapshot_bearings_match_zos_relative_goldens():
    g = WorldSceneGraph().snapshot(WORLD_SNAP)
    rel = {r["to"]: r["rel"] for r in g["relations"]}
    # Goldens produced by zos.world.relative itself on this fixture (2026-08-22).
    assert rel == {
        "start": "2.5m left-rear +128°",
        "ceo_office_door": "9.1m ahead-left +54°",
        "admin_office": "3.2m directly left +84°",
        "bottle_1": "6.1m straight ahead -2°",
        "bottle_2": "8.8m straight ahead -11°",
    }
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["self"]["kind"] == "self" and nodes["self"]["stale"] is False
    assert nodes["ceo_office_door"]["kind"] == "place"
    assert nodes["bottle_1"]["kind"] == "object"
    assert nodes["bottle_1"]["conf"] == 0.91 and nodes["bottle_1"]["pos"][2] == 0.78
    assert g["frame"] == "map"


def test_world_snapshot_stale_pose_flags_self_and_no_pose_stays_honest():
    stale = {n["id"]: n for n in WorldSceneGraph().snapshot(
        {**WORLD_SNAP, "odom_age": 5.0})["nodes"]}
    assert stale["self"]["stale"] is True, "odom_age past ODOM_STALE_S must propagate"
    assert stale["bottle_1"]["stale"] is False, "map-frame facts are not odom-derived"

    blind = WorldSceneGraph().snapshot({**WORLD_SNAP, "pose": None})
    assert all(r["rel"] == "distance unknown (no pose)" for r in blind["relations"]), \
        "no pose means zos's honest words, never an invented bearing"
    bself = {n["id"]: n for n in blind["nodes"]}["self"]
    assert bself["pos"] is None and bself["stale"] is True and bself["conf"] == 0.0
