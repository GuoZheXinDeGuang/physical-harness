"""Reference implementations of the layer-2 graph contracts.

InMemorySkillGraph is content-addressed the same way campaign artifacts are, so
a published skill's identity is its measurements, not a serial number. The store
is schema-free except for one record ``kind``: ``"capability"`` records are
validated at publish time against ``harness/skill_record.py`` (the shape a
planner selects an executor by), and everything else is stored as handed over.

Two scene-graph providers share one snapshot schema
(frame/t/nodes[id,label,kind,pos,conf,stale]/relations[from,to,rel]):

- SimSceneGraph reads a robosuite observation (the base_profile context);
- WorldSceneGraph reads a robot ``World.snapshot()`` dict (the real-robot
  context, mounted via the ``robot-world`` bundle -- ``profiles.bundle``). its
  consumer was the now-retired zos cockpit; it is reserved for the future
  actuation:real embodiment card, which supplies the live snapshot.

The caller supplies the raw observation (contracts.SceneGraph.snapshot(obs));
this module is a pure normalizer that imports neither a robot runtime nor numpy,
so it stays light enough to load in a real robot's minimal venv. The bearing
math is PORTED from the retired zos world model (world.py:471 ``relative``;
goldens in the self-check below, salvage in local-archive/docs/retired-from-public/zos-salvage.md §6) and array
values are coerced with float()/list().
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from harness.config import sha_json
from harness.skill_record import CAPABILITY, PLAN, validate_capability


class InMemorySkillGraph:
    def __init__(self, root: str | None = None) -> None:
        self._skills: dict[str, dict] = {}
        self._root = Path(root) if root else None
        if self._root is not None:
            self._root.mkdir(parents=True, exist_ok=True)
            # Read-back is what makes `root` a shared store rather than a write
            # spool: a campaign publishes here, and a LATER process (a downstream
            # evidence reader) mounts the same root and reads the promoted records
            # through skills() — the kernel-accounted seam, so no consumer ever
            # globs this directory itself. Filename stem IS the content digest
            # (publish() named it). A corrupt file raises right here at mount
            # time — loud where mounted, and a downstream reader degrades to
            # an honest empty index on its side.
            for f in sorted(self._root.glob("*.json")):
                self._skills[f.stem] = json.loads(f.read_text())

    def publish(self, record: Mapping) -> str:
        payload = dict(record)
        # Schema by `kind`, so the recovery records (no kind, or kind
        # "grasp_recovery") that already live in this store pass through
        # byte-identically and their digests never move. A malformed capability
        # record is NOT storable: raise here rather than warn and write a row a
        # planner would later read as a measured claim.
        if payload.get("kind") in (CAPABILITY, PLAN):
            validate_capability(payload)
        digest = sha_json(payload)
        self._skills[digest] = payload
        if self._root is not None:
            (self._root / f"{digest}.json").write_text(
                json.dumps(payload, sort_keys=True, indent=1, default=str))
        return digest

    def skills(self) -> tuple[Mapping, ...]:
        return tuple(self._skills[k] for k in sorted(self._skills))


# ── shared bearing math ────────────────────────────────────────────────────

#: Mirrors the retired zos world model's ODOM_STALE_S — a pose older than this
#: renders stale (salvage: local-archive/docs/retired-from-public/zos-salvage.md §6).
_ODOM_STALE_S = 2.0

# Ported from the retired zos world model (world.py:471 `relative`). LLMs cannot do metric
# arithmetic over raw coordinates, so the graph carries derived bearings; the
# raw pos stays on the node because tools need it and it is unforgeable.
_SECTORS = ((22.5, "straight ahead"), (67.5, "ahead-{s}"), (112.5, "directly {s}"),
            (157.5, "{s}-rear"), (180.1, "directly behind"))


def _relation(px: float, py: float, pyaw: float, x: float, y: float) -> str:
    """'5.6m ahead-right +32°' — byte-for-byte the ported `relative` string."""
    dx, dy = x - px, y - py
    dist = math.hypot(dx, dy)
    rel = math.degrees(math.atan2(dy, dx) - pyaw)
    rel = (rel + 180.0) % 360.0 - 180.0  # -> [-180, 180)
    side = "left" if rel > 0 else "right"
    word = next(w for lim, w in _SECTORS if abs(rel) < lim).format(s=side)
    return f"{dist:.1f}m {word} {rel:+.0f}°"


# ── providers ──────────────────────────────────────────────────────────────

#: features.py:52 _OBJECT_KEYS (one per supported task) plus cubeB_pos, the
#: stack partner (features.py:83). Duplicated, not imported: features.py pulls
#: numpy at import and this module must stay light for a real robot's minimal venv.
_SIM_OBJECT_KEYS = ("cube_pos", "cubeA_pos", "cubeB_pos", "Can_pos")


class SimSceneGraph:
    """Robosuite obs -> scene graph. An absent object key is OMITTED, never faked."""

    def snapshot(self, obs: Mapping) -> Mapping:
        nodes: list[dict] = []
        relations: list[dict] = []
        eef = obs.get("robot0_eef_pos")
        epos = [float(v) for v in list(eef)[:3]] if eef is not None else None
        if epos is not None:
            nodes.append({"id": "eef", "label": "eef", "kind": "self",
                          "pos": epos, "conf": 1.0, "stale": False})
        for key in _SIM_OBJECT_KEYS:
            if key not in obs:
                continue
            oid = key[: -len("_pos")]
            pos = [float(v) for v in list(obs[key])[:3]]
            nodes.append({"id": oid, "label": oid, "kind": "object",
                          "pos": pos, "conf": 1.0, "stale": False})
            if epos is not None:
                # planar eef-to-object distance, mirroring features.py:67
                d = math.hypot(pos[0] - epos[0], pos[1] - epos[1])
                relations.append({"from": "eef", "to": oid, "rel": f"{d:.3f}m planar"})
        return {"frame": "world", "t": float(obs.get("t", 0.0)),
                "nodes": nodes, "relations": relations}


class WorldSceneGraph:
    """A robot ``World.snapshot()`` dict -> scene graph, same schema.

    Staleness propagates: odom_age past _ODOM_STALE_S (or a missing pose) flags
    the self node, and every self->x bearing was computed from that pose — a
    consumer must treat them as suspect together. No pose means the honest
    'distance unknown (no pose)' string, exactly as the source world model renders it.
    """

    def snapshot(self, obs: Mapping) -> Mapping:
        pose = obs.get("pose")
        stale = float(obs.get("odom_age", float("inf"))) > _ODOM_STALE_S
        nodes: list[dict] = [{
            "id": "self", "label": "self", "kind": "self",
            "pos": [float(pose["x"]), float(pose["y"])] if pose is not None else None,
            "conf": 1.0 if pose is not None else 0.0,
            "stale": stale or pose is None,
        }]
        relations: list[dict] = []

        def add(nid: str, node: dict, x: float, y: float) -> None:
            nodes.append(node)
            rel = ("distance unknown (no pose)" if pose is None else
                   _relation(float(pose["x"]), float(pose["y"]), float(pose["yaw"]), x, y))
            relations.append({"from": "self", "to": nid, "rel": rel})

        for name, p in (obs.get("places") or {}).items():
            x, y = float(p["x"]), float(p["y"])
            add(name, {"id": name, "label": name, "kind": "place",
                       "pos": [x, y], "conf": 1.0, "stale": False}, x, y)
        for oid, o in (obs.get("objects") or {}).items():
            x, y = float(o["x"]), float(o["y"])
            add(oid, {"id": oid, "label": str(o["label"]), "kind": "object",
                      "pos": [x, y, float(o["z"])], "conf": float(o["conf"]),
                      "stale": False}, x, y)
        return {"frame": "map", "t": float(obs.get("t", 0.0)),
                "nodes": nodes, "relations": relations}


def skill_graph_provider(**params: Any) -> InMemorySkillGraph:
    return InMemorySkillGraph(**params)


def scene_graph_provider() -> SimSceneGraph:
    # NAME AND REF UNCHANGED: base_profile mounts "plugins.graphs:scene_graph_provider"
    # byte-identically, so MountPlan.sha stays put while the behavior behind the
    # ref goes from empty stub to the robosuite scene graph (config.py hashes the
    # ref and params, not the constructed object).
    return SimSceneGraph()


def world_scene_graph_provider() -> WorldSceneGraph:
    return WorldSceneGraph()


# ── self-check ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    now = 1000.0
    # The world.py __main__ fixture (pose 2.31,-0.88,0.54; places; two bottles),
    # serialized the way World.snapshot emits it.
    snap = {
        "t": now,
        "pose": {"x": 2.31, "y": -0.88, "yaw": 0.54},
        "places": {"start": {"x": 0.0, "y": 0.0, "yaw": 0.0},
                   "ceo_office_door": {"x": 3.1, "y": 8.2, "yaw": 0.0},
                   "admin_office": {"x": 1.0, "y": 2.0, "yaw": 0.0}},
        "objects": {
            "bottle_1": {"label": "bottle", "x": 7.62, "y": 2.11, "z": 0.78,
                         "conf": 0.91, "t_seen": now, "source": "vlm"},
            "bottle_2": {"label": "bottle", "x": 10.6, "y": 2.11, "z": 0.10,
                         "conf": 0.60, "t_seen": now, "source": "vlm"}},
        "authority": "idle", "t_pose": now - 0.2, "odom_age": 0.2, "t_places": now,
    }
    g = WorldSceneGraph().snapshot(snap)
    rel = {r["to"]: r["rel"] for r in g["relations"]}
    # Goldens produced by the zos world model's own `relative` on this fixture
    # (2026-08-22, before retirement) — the ported math must reproduce the source
    # strings byte-for-byte.
    assert rel["start"] == "2.5m left-rear +128°", rel
    assert rel["ceo_office_door"] == "9.1m ahead-left +54°", rel
    assert rel["admin_office"] == "3.2m directly left +84°", rel
    assert rel["bottle_1"] == "6.1m straight ahead -2°", rel
    assert rel["bottle_2"] == "8.8m straight ahead -11°", rel
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["self"]["stale"] is False and nodes["self"]["pos"] == [2.31, -0.88]
    assert nodes["ceo_office_door"]["kind"] == "place"
    assert nodes["bottle_1"]["kind"] == "object" and nodes["bottle_1"]["conf"] == 0.91
    assert g["frame"] == "map" and len(g["relations"]) == 5

    # Stale propagation: odom_age past ODOM_STALE_S must flag the self node —
    # a frozen pose that still reads as live is the exact defect the world model guards against.
    stale_nodes = {n["id"]: n for n in WorldSceneGraph().snapshot(
        {**snap, "odom_age": 5.0})["nodes"]}
    assert stale_nodes["self"]["stale"] is True
    assert stale_nodes["bottle_1"]["stale"] is False  # map-frame fact, not odom-derived

    # No pose: honest unknown in the source model's exact words; nothing invented.
    blind = WorldSceneGraph().snapshot({**snap, "pose": None})
    assert all(r["rel"] == "distance unknown (no pose)" for r in blind["relations"])
    bself = {n["id"]: n for n in blind["nodes"]}["self"]
    assert bself["pos"] is None and bself["stale"] is True and bself["conf"] == 0.0

    # Sim: eef->cubeA planar distance mirrors features.py:67 (hypot(0.3, 0.4)=0.5);
    # absent keys (cubeB and friends) are omitted, never faked.
    sg = SimSceneGraph().snapshot({"robot0_eef_pos": [0.1, 0.0, 1.0],
                                   "cubeA_pos": [0.4, 0.4, 0.83]})
    assert {n["id"] for n in sg["nodes"]} == {"eef", "cubeA"}
    assert sg["relations"] == [{"from": "eef", "to": "cubeA", "rel": "0.500m planar"}]
    assert SimSceneGraph().snapshot({}) == {
        "frame": "world", "t": 0.0, "nodes": [], "relations": []}

    # Skill store read-back: publish with a root, a FRESH instance loads it —
    # this is the cross-process half of the graph.skill seam (campaign writes,
    # a later downstream process mounts the same root and reads through skills()).
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        first = InMemorySkillGraph(root=td)
        rec = {"kind": "grasp_recovery", "task": "stack", "generation": 1}
        digest = first.publish(rec)
        reread = InMemorySkillGraph(root=td)
        assert reread.skills() == (rec,), reread.skills()
        assert reread.publish(rec) == digest, "content addressing drifted across the reload"
        assert len(reread.skills()) == 1, "re-publishing a loaded record must not duplicate it"
    assert InMemorySkillGraph().skills() == (), "root-less stays a pure in-memory store"

    # The module must stay light: importable with no numpy in the process.
    import sys
    assert "numpy" not in sys.modules, "graphs.py dragged numpy in — the light venv breaks"
    print("plugins/graphs.py self-check OK")
