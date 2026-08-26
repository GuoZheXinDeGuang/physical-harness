"""Composite ``policy.driver`` for the kitchen_thaw persistent mission (M7).

The base's generic segment runner drives ONE driver per persistent episode through
the obs-only ``PolicyDriver`` protocol (``act(obs)`` / ``exhausted``), retargeting
it by object pose. That fits a homogeneous mission (clear_workspace: grasp+lift
each of N objects). kitchen_thaw is NOT homogeneous: its six sub-goals are six
DIFFERENT behaviours -- navigate-to-fridge, grasp, navigate-to-microwave (loaded),
place-inside, close-door, press-start -- each a stage driver in ``drivers.py`` that
reads the LIVE env (``env.sim``, fixtures, contact) and self-targets, driven by
``act(env, obs)`` / ``done(env)`` rather than obs alone.

So this adapter implements the OTHER episodic-segment protocol the base opts into
(``plugins.task.workload._governed_segment``): ``enter_segment(env, spec)`` binds
the live world and selects the stage driver by ``spec.task`` (the mission's
SEGMENT_SPECS re-tasks each segment node to one of the keys below), then ``act`` /
``exhausted`` relay to it and ``segment_success`` reports the stage's OWN ``done``.
No retarget-by-pose, no fixed lifted()/_check_success terminal -- a nav sub-goal is
"arrived", a close is "door shut", and the stage driver already knows each truth.

``drivers`` is a same-package import (robocasa is lazy inside its methods), so this
module stays base-importable; only driving a real segment drags the simulator in.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from plugins.embodiment_robocasa import drivers as D

#: spec.task (set by the mission card's SEGMENT_SPECS re-task, one per segment node)
#: -> (stage-driver factory, per-segment step cap). The caps mirror the phase-3
#: per-stage smoke budgets (tests/test_robocasa_drivers.py): a stalled nav or an
#: unreachable push spends its cap then hands back an honest failed sub-goal, never
#: the whole horizon. Calibration knobs, not scene values -- tune here if the
#: arm/base geometry or a stage's convergence changes. nav_micro carries loaded:
#: its cap covers the stow prelude + the velocity-capped drive (carry-probe:
#: seed 11 needed >250 capped steps for the same leg the empty base does in ~80).
_STAGES: dict[str, tuple[Any, int]] = {
    "nav_fridge":  (lambda: D.NavigateDriver("fridge"), 250),
    "grasp_meat":  (lambda: D.GraspDriver("meat"), 900),
    "nav_micro":   (lambda: D.NavigateDriver("microwave", carry=True), 450),
    "place_meat":  (lambda: D.PlaceDriver("meat", "microwave"), 300),
    "close_door":  (lambda: D.CloseDoorDriver("microwave"), 250),
    "press_start": (lambda: D.PressStartDriver("microwave"), 200),
}


class KitchenThawDriver:
    """One instance threaded through the whole persistent episode; re-armed per
    sub-goal by ``enter_segment``. Frozen and governable like every harness driver
    (the stage controllers are scripted oracle policies, not learned policies under
    test), so a critic-recovery bundle could mount over it unchanged -- none is
    established for these tasks yet, so segments run ungoverned (bundle=None)."""

    def __init__(self) -> None:
        self._env: Any = None
        self._stage: Any = None
        self._cap: int = 0
        #: policy-owned step clock the base loop reads (``getattr(driver, "k", k)``)
        #: and the segment cap counts against.
        self.k: int = 0

    # --- obs-only PolicyDriver surface the base's open_episode/segment loop calls -
    def observe_once(self, obs) -> np.ndarray:
        """Called once at episode open, before any segment is entered. No target to
        lock (each stage self-targets from the live env), so this is a no-op."""
        return np.zeros(D.ADIM)

    def act(self, obs) -> np.ndarray:
        """Relay one control step to the active stage driver on the bound env."""
        a = self._stage.act(self._env, obs)
        self.k += 1
        return a

    @property
    def exhausted(self) -> bool:
        """The sub-goal is over when its stage predicate holds OR the per-segment
        cap is spent -- checked at the loop top BEFORE the first ``act``, so a
        sub-goal already satisfied at entry (e.g. the robot spawns docked at the
        fridge) consumes zero steps and seals success immediately."""
        if self._stage is None:
            return True
        return self.k >= self._cap or bool(self._stage.done(self._env))

    def retarget(self, target) -> None:  # noqa: D401 -- unused on this protocol
        """No-op: the stage drivers self-target off the live env, never a pose."""

    def on_handback(self) -> None:
        """No-op: no critic-recovery bundle mounts over these sub-goals yet."""

    @property
    def identity(self) -> str:
        return "robocasa_kitchen_thaw@v1"

    # --- the episodic-segment protocol the base opts into (heterogeneous branch) --
    def enter_segment(self, env, spec) -> None:
        """Bind the live persistent world and arm THIS sub-goal's stage driver,
        selected by ``spec.task`` (the mission's per-node re-task). An unknown task
        fails loudly here, before any actuation."""
        task = getattr(spec, "task", None)
        if task not in _STAGES:
            raise ValueError(
                f"kitchen_thaw has no stage driver for sub-goal task {task!r}; "
                f"SEGMENT_SPECS must re-task each segment to one of {sorted(_STAGES)}")
        factory, cap = _STAGES[task]
        self._env = env
        self._stage = factory()
        self._cap = cap
        self.k = 0

    def segment_success(self, env) -> bool:
        """The sub-goal truth: the stage driver's OWN live-state predicate (arrived
        / grasped / placed / closed / on), read on the world as it now is."""
        return bool(self._stage.done(env))


class KitchenThawPolicies:
    """Layer 3 ``harness.contracts.PolicyFactory``: one mount, the composite driver.

    Mounted as ``policy.driver`` by the mission binding; ``open_episode`` builds it
    once via ``make_driver(spec)`` (spec.policy_provider names this ref)."""

    def make_driver(self, spec: Any) -> KitchenThawDriver:
        return KitchenThawDriver()


def provider() -> KitchenThawPolicies:
    return KitchenThawPolicies()


if __name__ == "__main__":
    # No robocasa here (base-importable self-check): the protocol surface + the
    # task->stage dispatch + the zero-step-at-entry seal are asserted on a fake
    # stage. The REAL stage drivers are smoked per-stage in tests/test_robocasa_
    # drivers.py and end-to-end through the runtime E2E (local-archive/robocasa-
    # adapt/phase4.md).
    class _FakeStage:
        def __init__(self) -> None:
            self.steps = 0
            self.done_at = 3

        def act(self, env, obs):
            self.steps += 1
            return np.zeros(D.ADIM)

        def done(self, env):
            return self.steps >= self.done_at

    drv = KitchenThawDriver()
    assert drv.exhausted is True, "no stage armed -> exhausted (never drive a null stage)"
    assert drv.observe_once({}).shape == (D.ADIM,)

    # unknown task fails loudly
    class _Spec:
        task = "not_a_stage"
    try:
        drv.enter_segment(object(), _Spec())
    except ValueError:
        pass
    else:
        raise AssertionError("unknown sub-goal task must fail loudly")

    # arm a fake stage by monkeypatching the dispatch, drive to its done
    _STAGES["_probe"] = (lambda: _FakeStage(), 250)

    class _S2:
        task = "_probe"
    drv.enter_segment(object(), _S2())
    assert drv.k == 0 and not drv.exhausted, "fresh stage: clock 0, not yet done"
    steps = 0
    while not drv.exhausted:
        drv.act({})
        steps += 1
    assert steps == 3 and drv.k == 3, ("stage drives to its done predicate", steps, drv.k)
    assert drv.segment_success(object()) is True
    # cap floor: a never-done stage stops at the cap, seals failure
    class _Stuck(_FakeStage):
        def done(self, env):
            return False

    _STAGES["_stuck"] = (lambda: _Stuck(), 5)

    class _S3:
        task = "_stuck"
    drv.enter_segment(object(), _S3())
    while not drv.exhausted:
        drv.act({})
    assert drv.k == 5 and drv.segment_success(object()) is False, "cap-bounded honest fail"
    del _STAGES["_probe"], _STAGES["_stuck"]
    print("plugins/embodiment_robocasa/kitchen_driver.py self-check OK")
