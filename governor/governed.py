"""Governed rollout: the frozen policy under a critic-recovery bundle.

Ownership
---------
This module holds the raw observation and is the only place that does. The
critic and the recovery each receive a :class:`FeatureView` built under their
own budget, and both invariants are asserted before every dispatch. That is the
structural answer to the leak recorded in docs/headline-finding.md, where a
hand-written recovery read ``obs["cube_pos"]`` directly and inflated a +13.3%
(n.s.) result into a reported +50%.

The percept model IS the ablation ladder
----------------------------------------
A recovery that re-approaches must know where the object is. Modelling that as
``estimate.cube_pos = true + N(0, sensor_sd)`` puts the sim-to-real question
inside the harness instead of beside it: ``sensor_sd=0`` is ground truth and is
declared PRIVILEGED, while a positive ``sensor_sd`` is what an onboard sensor
would actually deliver. Sweeping it produces the transfer curve directly, and no
separate "ablation mode" can drift out of sync with the thing being measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from governor.env import PHASE_HEIGHT, EpisodeSpec, FrozenPolicy, make_env, phase_at
from governor.invariant import (
    assert_privilege_budget, assert_view_reconstructable, record_view,
)
from governor.percept import FeatureView, PrivilegePolicy, project
from governor.search import Trigger

#: A perfect percept is ground truth, so it costs privilege; a noisy one does not.
PRIVILEGED_SENSOR_SD = 0.0


@dataclass(frozen=True, slots=True)
class RecoverySpec:
    """A bounded, scripted repair. Phases and durations only -- no free code."""

    name: str = "reapproach"
    #: (phase, duration) pairs replayed after a trigger fires.
    program: tuple[tuple[str, int], ...] = (
        ("descend", 18),   # lower and open: grip=-1 releases whatever is (not) held
        ("above", 15),     # re-stage above the re-estimated pose
        ("descend", 25),
        ("close", 14),
        ("lift", 40),
    )
    #: Std-dev of the percept the recovery re-reads. 0.0 == ground truth == privileged.
    sensor_sd: float = 0.020
    max_invocations: int = 1

    @property
    def percept_privilege(self) -> int:
        return 1 if self.sensor_sd <= PRIVILEGED_SENSOR_SD else 0


@dataclass(frozen=True, slots=True)
class Bundle:
    """One critic-recovery pair, the unit the evolution loop promotes."""

    trigger: Trigger
    recovery: RecoverySpec = field(default_factory=RecoverySpec)
    critic_budget: int = 0
    action_budget: int = 0

    def declared_privilege(self) -> int:
        return self.trigger.privilege + self.recovery.percept_privilege


def _percept_cube(obs, spec: EpisodeSpec, sensor_sd: float, draw: int) -> np.ndarray:
    """Onboard estimate of the cube pose. Deterministic in (seed, draw)."""
    true = np.asarray(obs["cube_pos"]).copy()
    if sensor_sd <= PRIVILEGED_SENSOR_SD:
        return true
    rng = np.random.RandomState((spec.seed * 104729 + 3 + draw * 7907) % (2**31 - 1))
    return true + np.array([rng.normal(0, sensor_sd), rng.normal(0, sensor_sd), 0.0])


def governed_rollout(spec: EpisodeSpec, bundle: Bundle | None) -> dict:
    """Run one episode, optionally under a critic-recovery bundle.

    Passing ``bundle=None`` runs the frozen policy alone; both arms of a paired
    gate therefore go through this identical code path, so the only difference
    between them is the governance itself.
    """
    policy_names = PrivilegePolicy(critic_budget=bundle.critic_budget if bundle else 0).critic_names()
    env = make_env(spec)
    obs = env.reset()
    policy = FrozenPolicy(spec)
    policy.observe_once(obs)

    schedule = list(spec.schedule)
    history: dict[str, list[float]] = {}
    consec = 0
    fired_at: int | None = None
    invocations = 0
    privilege_used = 0
    t = 0
    queue: list[tuple[str, int]] = list(schedule)

    while queue:
        phase, dur = queue.pop(0)
        for _ in range(dur):
            obs, _r, _done, _info = env.step(policy.act(obs, phase))
            t += 1
            view = project(obs, policy_names, step=t, episode=f"s{spec.seed}")
            logged = record_view(view)              # what the durable log will claim
            for name in view.snapshot():
                history.setdefault(name, []).append(view.snapshot()[name])

            if bundle is None or invocations >= bundle.recovery.max_invocations:
                continue

            # --- critic dispatch, behind both invariants ---------------------
            assert_view_reconstructable(view, logged)
            trig = bundle.trigger
            armed = t >= trig.arm_after
            if armed:
                value = view[trig.feature]           # attested read
                hit = value < trig.threshold if trig.op == "lt" else value > trig.threshold
                consec = consec + 1 if hit else 0
            else:
                consec = 0
            assert_privilege_budget(view, bundle.critic_budget, role="critic")
            privilege_used = max(privilege_used, view.privilege_used())

            if armed and consec >= trig.dwell:
                fired_at = fired_at if fired_at is not None else t
                invocations += 1
                # --- recovery dispatch, its own budget ----------------------
                act_view = project(obs, PrivilegePolicy(critic_budget=bundle.action_budget).critic_names(),
                                   step=t, episode=f"s{spec.seed}")
                assert_view_reconstructable(act_view, record_view(act_view))
                assert_privilege_budget(act_view, bundle.action_budget, role="recovery")
                # the recovery re-reads the object pose through the percept model;
                # ground truth here is declared privileged by RecoverySpec.
                policy.target = _percept_cube(obs, spec, bundle.recovery.sensor_sd, invocations)
                queue = list(bundle.recovery.program) + queue
                consec = 0
                break

    success = bool(env._check_success())
    env.close()
    return {
        "seed": spec.seed,
        "success": success,
        "steps": t,
        "fired_at": fired_at,
        "invocations": invocations,
        "critic_privilege_used": privilege_used,
        "declared_privilege": bundle.declared_privilege() if bundle else 0,
        "trace": {k: np.asarray(v) for k, v in history.items()},
    }
