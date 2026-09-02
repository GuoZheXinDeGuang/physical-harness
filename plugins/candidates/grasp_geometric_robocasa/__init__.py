"""grasp_geometric_robocasa: the first code candidate (overnight item 5).

Executor key ``geometric`` for the robocasa ``grasp_meat`` skill, a code-as-policy
StepExecutor: live object pose -> hover -> descend -> close -> lift, read off the
env the stage driver hands it (``bind(env, target=)``). A
harness.skill_executor.InprocExecutor (``handshake`` / ``reset`` / ``act(obs)`` /
``done`` / ``diagnostics``) mounted like any policy provider (``provider(**params)``
-> ``make_driver(spec)``). The robocasa primitives (eef / object pose readers,
the OSC arm action) are reached by ref at act time, never imported
(tests/test_boundaries.py: cards never import each other).
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from typing import Any

import numpy as np

from harness.skill_executor import InprocExecutor, normalize_handshake

REF = "plugins.candidates.grasp_geometric_robocasa:provider"
#: the embodiment card's live-state primitives, resolved by ref at act time
PRIMITIVES_REF = "plugins.embodiment_robocasa.drivers"
_MANIFEST = Path(__file__).with_name("manifest.toml")


def manifest_tunables() -> dict[str, float]:
    """The card's own ``[tunables]`` table: the executor's default knobs."""
    return dict(tomllib.loads(_MANIFEST.read_text()).get("tunables", {}))


class GeometricGraspExecutor(InprocExecutor):
    """One segment's executor: hover above the live object, descend with open
    fingers, close in place, lift gently. ``done`` is relational (the object
    rose with the hand), never a gripper latch."""

    def __init__(self, tunables: dict[str, float], target: str | None = None) -> None:
        self.t = tunables
        self.target = target
        self._env: Any = None
        self.reset()

    def handshake(self) -> dict:
        """The sealed shape; the knobs this executor ran under ride in ``meta``."""
        return normalize_handshake("inproc", REF, {"tunables": dict(self.t)})

    def bind(self, env, target: str | None = None) -> None:
        """The live world + the object to pick (the stage's target when it has one)."""
        self._env = env
        self.target = target or self.target
        if self.target is None:
            raise ValueError("geometric executor: no target object bound")

    def reset(self) -> None:
        self.phase = "hover"
        self.k = 0
        self._close_k = 0
        self._z0: float | None = None
        self._lift_z: float | None = None

    def _pose(self):
        P = importlib.import_module(PRIMITIVES_REF)
        return P, P._obj_pos(self._env, self.target), P._eef(self._env)

    def act(self, obs) -> np.ndarray:
        P, obj, eef = self._pose()
        t = self.t
        if self._z0 is None:
            self._z0 = float(obj[2])
        self.k += 1
        if self.phase == "hover":
            goal = obj + np.array([0.0, 0.0, t["hover"]])
            if (np.linalg.norm((goal - eef)[:2]) < t["xy_tol"]
                    and abs(goal[2] - eef[2]) < t["z_tol"]):
                self.phase = "descend"
            return P._arm_action(self._env, goal, P.GRIP_OPEN)
        if self.phase == "descend":
            goal = np.array([obj[0], obj[1], obj[2] - t["descend_below"]])
            if (np.linalg.norm((goal - eef)[:2]) < t["xy_tol"]
                    and eef[2] - goal[2] < t["z_tol"]):
                self.phase = "close"
            return P._arm_action(self._env, goal, P.GRIP_OPEN)
        if self.phase == "close":  # hold the eef where it is; only the fingers move
            self._close_k += 1
            if self._close_k >= t["close_ticks"]:
                self.phase = "lift"
                self._lift_z = float(eef[2]) + t["lift_dz"]
            return P._arm_action(self._env, eef, P.GRIP_CLOSE)
        a = P._arm_action(self._env, np.array([eef[0], eef[1], self._lift_z]), P.GRIP_CLOSE)
        a[0:3] = np.clip(a[0:3], -t["lift_cap"], t["lift_cap"])  # gentle: keep the enclosure
        return a

    def done(self) -> bool:
        if self._env is None or self._z0 is None:
            return False
        _, obj, _ = self._pose()
        return self.phase == "lift" and float(obj[2]) > self._z0 + self.t["secure_dz"]

    def diagnostics(self) -> dict:
        d = {"phase": self.phase, "k": self.k, "target": self.target}
        if self._env is not None and self._z0 is not None:
            _, obj, eef = self._pose()
            d["obj_rise"] = float(obj[2]) - self._z0
            d["eef_to_obj"] = float(np.linalg.norm(obj - eef))
        return d


class GeometricGraspPolicies:
    """``PolicyFactory`` surface the workload mounts once per episode; a fresh
    executor per segment (``make_driver``)."""

    def __init__(self, params: dict[str, Any]) -> None:
        self.tunables = {**manifest_tunables(), **(params.get("tunables") or {})}
        self.target = params.get("target")

    def make_driver(self, spec: Any) -> GeometricGraspExecutor:
        return GeometricGraspExecutor(self.tunables, self.target)


def provider(**params: Any) -> GeometricGraspPolicies:
    return GeometricGraspPolicies(params)


if __name__ == "__main__":
    # Base-importable self-check on a fake primitives module: the phase ladder
    # hover -> descend -> close -> lift, relational done, handshake shape.
    import sys
    import types

    fake = types.ModuleType("fake_prims")
    fake.GRIP_OPEN, fake.GRIP_CLOSE = -1.0, 1.0
    world = {"obj": np.array([0.5, 0.0, 1.0]), "eef": np.array([0.5, 0.0, 1.3])}
    fake._obj_pos = lambda env, name: world["obj"].copy()
    fake._eef = lambda env: world["eef"].copy()

    def _arm_action(env, goal, grip):
        held = grip > 0 and abs(world["eef"][2] - world["obj"][2]) < 0.05  # fingers around it
        world["eef"] += np.clip(np.asarray(goal) - world["eef"], -0.05, 0.05)  # P step
        if held:
            world["obj"][2] = world["eef"][2]  # enclosed: the object rides the hand
        a = np.zeros(12); a[6] = grip
        return a
    fake._arm_action = _arm_action
    sys.modules["fake_prims"] = fake
    PRIMITIVES_REF = "fake_prims"

    ex = provider().make_driver(None)
    assert ex.handshake()["transport"] == "inproc" and ex.handshake()["ref"] == REF
    ex.bind(object(), target="meat")
    seen = []
    for _ in range(80):
        if ex.done():
            break
        ex.act({})
        seen.append(ex.phase)
    assert ex.done(), ex.diagnostics()
    assert [p for i, p in enumerate(seen) if i == 0 or seen[i - 1] != p] == \
        ["hover", "descend", "close", "lift"], seen
    assert ex.diagnostics()["obj_rise"] > ex.t["secure_dz"]
    print("plugins/candidates/grasp_geometric_robocasa self-check OK")
