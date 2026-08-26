"""Shared stage machinery for the three composite missions (recycle_cans /
pack_lunch / steam_prep) -- everything they need beyond ``drivers.py``, WITHOUT
touching it: an object-addressed navigate, a point-targeted place family, and the
generic composite ``policy.driver`` adapter each mission's driver file arms with
its own stage table.

Same discipline as ``drivers.py``: closed-loop P controllers over LIVE privileged
state, deterministic given the seeded scene, robocasa imported lazily inside
methods so every module here stays base-importable. The composite adapter is a
verbatim sibling of ``kitchen_driver.KitchenThawDriver`` (the M7
episodic-segment protocol: enter_segment / act / exhausted / segment_success),
parameterised by stage table instead of copied per mission.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from plugins.embodiment_robocasa import drivers as D


class NavToObjectDriver(D.NavigateDriver):
    """NavigateDriver whose dock is computed NEAR A NAMED OBJECT on the fixture
    (``compute_robot_base_placement_pose(..., ref_object=...)``): a long counter
    has one generic dock but the can/tupperware sits at a specific run of it.
    Everything else -- empty and carry legs both -- is the parent verbatim."""

    def __init__(self, fixture_name: str, obj_name: str, carry: bool = False):
        super().__init__(fixture_name, carry=carry)
        self.obj_name = obj_name

    def _target(self, env):
        if self._goal is None:
            from robocasa.utils.env_utils import compute_robot_base_placement_pose

            fx = D._fixture(env, self.fixture_name)
            pos, ori = compute_robot_base_placement_pose(
                env, fx, ref_object=self.obj_name)
            self._goal = (np.asarray(pos[:2], float), float(ori[2]))
        return self._goal


class PointPlaceDriver:
    """Place the held object at a WORLD POINT: over -> lower -> release ->
    retreat, the ``drivers.PlaceDriver`` phase chain re-targeted from a fixture's
    interior centroid to ``_drop_point(env)`` (subclass-supplied). done() is the
    subclass's own live truth."""

    OVER_DZ = 0.12
    RELEASE_TICKS = 6

    def __init__(self, obj_name: str):
        self.obj_name = obj_name
        self.phase = "over"
        self._ticks = 0

    # -- subclass surface ------------------------------------------------------
    def _drop_point(self, env) -> np.ndarray:
        raise NotImplementedError

    def done(self, env) -> bool:
        raise NotImplementedError

    # -- the shared phase chain ------------------------------------------------
    def act(self, env, obs):
        c = np.asarray(self._drop_point(env), float)
        eef = D._eef(env)
        if self.phase == "over":
            goal = np.array([c[0], c[1], c[2] + self.OVER_DZ])
            if np.linalg.norm((eef - goal)[:2]) < 0.03:
                self.phase = "lower"
            return D._arm_action(env, goal, D.GRIP_CLOSE)
        if self.phase == "lower":
            if eef[2] - c[2] < 0.04:
                self.phase = "release"
            return D._arm_action(env, c, D.GRIP_CLOSE)
        if self.phase == "release":
            self._ticks += 1
            if self._ticks > self.RELEASE_TICKS:
                self.phase = "retreat"
            return D._arm_action(env, np.array([c[0], c[1], c[2] + 0.02]),
                                 D.GRIP_OPEN)
        # retreat: up and clear, gripper open
        return D._arm_action(env, np.array([eef[0], eef[1], c[2] + 0.25]),
                             D.GRIP_OPEN)


class ReceptaclePlaceDriver(PointPlaceDriver):
    """Place into a receptacle OBJECT (tupperware/pot) -- target its LIVE body
    pose (a nudged receptacle is followed), drop from just above its rim."""

    RIM_DZ = 0.10  # release height above the receptacle centre

    def __init__(self, obj_name: str, receptacle: str):
        super().__init__(obj_name)
        self.receptacle = receptacle

    def _drop_point(self, env) -> np.ndarray:
        p = D._obj_pos(env, self.receptacle)
        return np.array([p[0], p[1], p[2] + self.RIM_DZ])

    def done(self, env) -> bool:
        import robocasa.utils.object_utils as OU

        return bool(OU.check_obj_in_receptacle(env, self.obj_name, self.receptacle)
                    and OU.gripper_obj_far(env, obj_name=self.obj_name))


class CompositeStageDriver:
    """The generic composite ``policy.driver`` for a heterogeneous persistent
    mission: one instance threaded through the whole episode, re-armed per
    sub-goal by ``enter_segment`` (spec.task -> its stage driver via the
    mission's own table). Protocol-identical to ``kitchen_driver.
    KitchenThawDriver`` -- factored here because three missions would otherwise
    carry three verbatim copies."""

    def __init__(self, stages: dict[str, tuple[Any, int]], identity: str) -> None:
        self._stages = stages
        self._identity = identity
        self._env: Any = None
        self._stage: Any = None
        self._cap: int = 0
        self.k: int = 0

    # --- obs-only PolicyDriver surface ---------------------------------------
    def observe_once(self, obs) -> np.ndarray:
        return np.zeros(D.ADIM)

    def act(self, obs) -> np.ndarray:
        a = self._stage.act(self._env, obs)
        self.k += 1
        return a

    @property
    def exhausted(self) -> bool:
        if self._stage is None:
            return True
        return self.k >= self._cap or bool(self._stage.done(self._env))

    def retarget(self, target) -> None:  # noqa: D401 -- stages self-target
        """No-op: the stage drivers self-target off the live env, never a pose."""

    def on_handback(self) -> None:
        """No-op: no critic-recovery bundle mounts over these sub-goals yet."""

    @property
    def identity(self) -> str:
        return self._identity

    # --- the episodic-segment protocol ----------------------------------------
    def enter_segment(self, env, spec) -> None:
        task = getattr(spec, "task", None)
        if task not in self._stages:
            raise ValueError(
                f"{self._identity} has no stage driver for sub-goal task {task!r}; "
                f"SEGMENT_SPECS must re-task each segment to one of "
                f"{sorted(self._stages)}")
        factory, cap = self._stages[task]
        self._env = env
        self._stage = factory()
        self._cap = cap
        self.k = 0

    def segment_success(self, env) -> bool:
        return bool(self._stage.done(env))


class CompositePolicies:
    """Layer 3 ``harness.contracts.PolicyFactory``: one mount, one composite
    driver armed with the owning mission's stage table."""

    def __init__(self, stages: dict[str, tuple[Any, int]], identity: str) -> None:
        self._stages = stages
        self._identity = identity

    def make_driver(self, spec: Any) -> CompositeStageDriver:
        return CompositeStageDriver(self._stages, self._identity)


if __name__ == "__main__":
    # Base-importable self-check: the composite adapter's dispatch + cap floor on
    # a fake stage (the kitchen_driver self-check, against the factored class).
    class _FakeStage:
        def __init__(self, done_at=3):
            self.steps, self.done_at = 0, done_at

        def act(self, env, obs):
            self.steps += 1
            return np.zeros(D.ADIM)

        def done(self, env):
            return self.steps >= self.done_at

    stages = {"probe": (lambda: _FakeStage(), 250),
              "stuck": (lambda: _FakeStage(done_at=10 ** 9), 5)}
    drv = CompositePolicies(stages, "probe@v1").make_driver(object())
    assert drv.exhausted is True and drv.identity == "probe@v1"
    assert drv.observe_once({}).shape == (D.ADIM,)

    class _S:
        task = "probe"
    drv.enter_segment(object(), _S())
    assert drv.k == 0 and not drv.exhausted
    while not drv.exhausted:
        drv.act({})
    assert drv.k == 3 and drv.segment_success(object()) is True

    class _S2:
        task = "stuck"
    drv.enter_segment(object(), _S2())
    while not drv.exhausted:
        drv.act({})
    assert drv.k == 5 and drv.segment_success(object()) is False

    class _S3:
        task = "nope"
    try:
        drv.enter_segment(object(), _S3())
    except ValueError:
        pass
    else:
        raise AssertionError("unknown sub-goal task must fail loudly")

    # PointPlaceDriver phase chain shape on a fake drop point (no sim): the
    # subclass surface is the only robocasa-touching part.
    class _Probe(PointPlaceDriver):
        def _drop_point(self, env):
            return np.array([0.0, 0.0, 1.0])

        def done(self, env):
            return False

    print("plugins/embodiment_robocasa/stage_extras.py self-check OK")
