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

import hashlib
import json
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from governor.env import EpisodeSpec, lifted, make_env, object_key
from governor.policy import RecoveryActor, make_driver
from governor.invariant import (
    assert_privilege_budget, assert_view_reconstructable, record_view,
)
from governor.episode_log import chain_start, chain_step
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
class Rule:
    """One critic-recovery pair. The unit a generation appends."""

    rule_id: str
    trigger: Trigger
    recovery: RecoverySpec = field(default_factory=RecoverySpec)

    def canonical(self) -> dict:
        """Byte-stable form used for parent-freeze verification and hashing."""
        return {
            "rule_id": self.rule_id,
            "trigger": {
                "feature": self.trigger.feature, "op": self.trigger.op,
                "threshold": round(float(self.trigger.threshold), 9),
                "dwell": int(self.trigger.dwell), "arm_after": int(self.trigger.arm_after),
            },
            "recovery": {
                "name": self.recovery.name,
                "program": [list(p) for p in self.recovery.program],
                "sensor_sd": round(float(self.recovery.sensor_sd), 9),
                "max_invocations": int(self.recovery.max_invocations),
            },
        }

    def declared_privilege(self) -> int:
        return self.trigger.privilege + self.recovery.percept_privilege


@dataclass(frozen=True, slots=True)
class Bundle:
    """An ordered chain of rules, grown one rule per generation.

    Zetta constrains each generation to ``append_exactly_one_critic_recovery_pair``
    with ``preserve_parent_rules_byte_for_byte`` (Zetta-Embodiment/zetta/evolution/
    stages.py:1858). The point is attribution: if a generation may rewrite its
    parent, a measured gain cannot be assigned to the one change under test.
    :meth:`assert_atomic_child_of` enforces it here rather than documenting it.
    """

    rules: tuple[Rule, ...] = ()
    critic_budget: int = 0
    action_budget: int = 0
    parent_sha: str | None = None

    def canonical(self) -> dict:
        return {
            "rules": [r.canonical() for r in self.rules],
            "critic_budget": self.critic_budget,
            "action_budget": self.action_budget,
        }

    def sha(self) -> str:
        payload = json.dumps(self.canonical(), separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def declared_privilege(self) -> int:
        """Worst-case privilege across the chain; a bundle is as privileged as its worst rule."""
        return max((r.declared_privilege() for r in self.rules), default=0)

    def append(self, rule: Rule) -> "Bundle":
        """The only sanctioned growth operation."""
        return Bundle(rules=(*self.rules, rule), critic_budget=self.critic_budget,
                      action_budget=self.action_budget, parent_sha=self.sha())

    def assert_atomic_child_of(self, parent: "Bundle") -> None:
        """Reject any generation that did more than append exactly one rule."""
        if len(self.rules) != len(parent.rules) + 1:
            raise ValueError(
                f"generation must append exactly one rule: parent has {len(parent.rules)}, "
                f"child has {len(self.rules)}"
            )
        for i, (a, b) in enumerate(zip(parent.rules, self.rules)):
            if a.canonical() != b.canonical():
                raise ValueError(f"parent rule {i} ({a.rule_id}) was modified; parents are frozen")
        if self.parent_sha != parent.sha():
            raise ValueError("child does not record its parent's hash")


def _percept_object(obs, spec: EpisodeSpec, sensor_sd: float, draw: int) -> np.ndarray:
    """Onboard estimate of the target object's pose. Deterministic in (seed, draw)."""
    true = np.asarray(obs[object_key(spec)]).copy()
    if sensor_sd <= PRIVILEGED_SENSOR_SD:
        return true
    rng = np.random.RandomState((spec.seed * 104729 + 3 + draw * 7907) % (2**31 - 1))
    return true + np.array([rng.normal(0, sensor_sd), rng.normal(0, sensor_sd), 0.0])


def governed_rollout(spec: EpisodeSpec, bundle: Bundle | None) -> dict:
    """Run one episode, optionally under a critic-recovery bundle.

    Control ownership, not schedule splicing
    ----------------------------------------
    The frozen policy owns every step until a critic fires; then a scripted
    :class:`RecoveryActor` owns the next ``len(program)`` steps and hands control
    back. Recovery steps do not advance the policy's own clock, so the policy
    resumes where it was interrupted -- which is what the earlier
    splice-into-the-phase-queue implementation did, expressed in a way that also
    works for a policy with no phases at all.

    ``bundle=None`` runs the policy alone; both arms of a paired gate go through
    this identical code path.
    """
    critic_names = PrivilegePolicy(critic_budget=bundle.critic_budget if bundle else 0).critic_names()
    action_names = PrivilegePolicy(critic_budget=bundle.action_budget if bundle else 0).critic_names()
    env = make_env(spec)
    obs = env.reset()
    driver = make_driver(spec)
    driver.observe_once(obs)
    start_z = float(np.asarray(obs[object_key(spec)])[2])

    rules = list(bundle.rules) if bundle else []
    consec = {r.rule_id: 0 for r in rules}
    used = {r.rule_id: 0 for r in rules}
    fires: list[dict] = []
    history: dict[str, list[float]] = {}
    chain = chain_start()
    privilege_used = 0
    recovery: RecoveryActor | None = None
    t = 0

    while t < spec.horizon:
        if recovery is not None and recovery.done:
            driver.on_handback()
            recovery = None
        if recovery is not None:
            action = recovery.act(obs)
        elif driver.exhausted:
            break
        else:
            action = driver.act(obs)

        obs, _r, done, _info = env.step(action)
        if done:
            t += 1
            break

        view = project(obs, critic_names, step=t, episode=f"s{spec.seed}")
        logged = record_view(view)
        for name, value in view.snapshot().items():
            history.setdefault(name, []).append(value)
        chain = chain_step(chain, logged.digest)
        t += 1
        if not rules:
            continue

        assert_view_reconstructable(view, logged)
        triggered: Rule | None = None
        for rule in rules:
            if used[rule.rule_id] >= rule.recovery.max_invocations:
                consec[rule.rule_id] = 0
                continue
            trig = rule.trigger
            if t - 1 < trig.arm_after:
                consec[rule.rule_id] = 0
                continue
            value = view[trig.feature]
            hit = value < trig.threshold if trig.op == "lt" else value > trig.threshold
            consec[rule.rule_id] = consec[rule.rule_id] + 1 if hit else 0
            if triggered is None and consec[rule.rule_id] >= trig.dwell:
                triggered = rule
        assert_privilege_budget(view, bundle.critic_budget, role="critic")
        privilege_used = max(privilege_used, view.privilege_used())
        if triggered is None:
            continue

        used[triggered.rule_id] += 1
        fires.append({"rule_id": triggered.rule_id, "step": t - 1})
        act_view = project(obs, action_names, step=t - 1, episode=f"s{spec.seed}")
        assert_view_reconstructable(act_view, record_view(act_view))
        assert_privilege_budget(act_view, bundle.action_budget, role="recovery")
        percept = _percept_object(obs, spec, triggered.recovery.sensor_sd, used[triggered.rule_id])
        driver.retarget(percept)
        recovery = RecoveryActor(triggered.recovery.program, percept)
        for k in consec:
            consec[k] = 0

    success = lifted(obs, spec, start_z)
    env.close()
    return {
        "seed": spec.seed,
        "task": spec.task,
        "policy": driver.identity,
        "success": success,
        "steps": t,
        "fires": fires,
        "fired_at": fires[0]["step"] if fires else None,
        "fired_rules": sorted({f["rule_id"] for f in fires}),
        "chain": chain,
        "critic_privilege_used": privilege_used,
        "declared_privilege": bundle.declared_privilege() if bundle else 0,
        "trace": {k: np.asarray(v) for k, v in history.items()},
    }
