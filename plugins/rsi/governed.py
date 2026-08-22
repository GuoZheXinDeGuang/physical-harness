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

import numpy as np

# Transitional laundering point, same class as the proposer and the motor
# vocabulary: driver construction is the policy.driver capability, reached
# through the governor shell until the shared vocabulary refactor lands.
from governor.policy import make_driver
from harness.percept import (
    PRIVILEGED_SENSOR_SD,
)
from harness.spec import EpisodeSpec


class _LegacyEmbodiment:
    """Contract-shaped facade over governor.env for specs with no provider ref."""

    def make_env(self, spec):
        import governor.env as legacy

        return legacy._default_make_env(spec)

    def object_key(self, spec):
        import governor.env as legacy

        return legacy.object_key(spec)

    def success(self, obs, spec, start_z):
        import governor.env as legacy

        return legacy.lifted(obs, spec, start_z)


def _embodiment(spec: EpisodeSpec):
    """Resolve the embodiment ONCE per rollout: contract-first, legacy fallback."""
    if spec.env_provider:
        from harness.registry import load_provider

        return load_provider(spec.env_provider)
    return _LegacyEmbodiment()
from harness.episode_log import chain_start, chain_step
from harness.invariant import (
    assert_privilege_budget,
    assert_view_reconstructable,
    record_view,
)
from harness.percept import PrivilegePolicy, project
from plugins.rsi.recovery import RecoveryActor
from plugins.rsi.stats.search import Trigger

#: A perfect percept is ground truth, so it costs privilege; a noisy one does not.



@dataclass(frozen=True, slots=True)
class RecoverySpec:
    """A bounded, scripted repair. Phases and durations only -- no free code."""

    #: Name of a strategy in plugins.rsi.repertoire. Different SHAPES of repair, which
    #: is the axis that matters -- round 6 searched durations and the gain did not
    #: survive a clean gate.
    name: str = "regrasp"
    #: Explicit program overriding the named strategy; None uses the repertoire.
    program: tuple[tuple[str, int], ...] | None = None
    #: Std-dev of the percept the recovery re-reads. 0.0 == ground truth == privileged.
    sensor_sd: float = 0.020
    max_invocations: int = 1

    @property
    def percept_privilege(self) -> int:
        return 1 if self.sensor_sd <= PRIVILEGED_SENSOR_SD else 0

    def steps(self):
        """Resolve to (phase, duration, dx, dy) steps."""
        from plugins.rsi.repertoire import strategy

        if self.program is not None:
            return tuple((n, d, 0.0, 0.0) for n, d in self.program)
        return strategy(self.name).steps


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
                # The running reducer arrived in round 14 and this form was
                # written before it. Omitting it made two rules that behave
                # differently hash identically, which blinded BOTH freeze
                # checks at once: `parent_sha` is computed from this same form,
                # so it could not act as a backstop.
                "reducer": self.trigger.reducer,
            },
            "recovery": {
                "name": self.recovery.name,
                "strategy": self.recovery.name,
                "program": [list(p) for p in self.recovery.steps()],
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

    def append(self, rule: Rule) -> Bundle:
        """The only sanctioned growth operation."""
        return Bundle(rules=(*self.rules, rule), critic_budget=self.critic_budget,
                      action_budget=self.action_budget, parent_sha=self.sha())

    def assert_atomic_child_of(self, parent: Bundle) -> None:
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


#: Where the onboard percept implementation LIVES now (L1 rung 1): the
#: embodiment plugin. governor keeps only the seam and this named default so
#: direct EpisodeSpec users (tests, demos) keep working unchanged. The rung 1
#: caveat (constant selects behaviour without entering any hash) is closed at
#: L1 rung 3: Preregistration.percept_provider defaults to THIS constant, so
#: the effective ref enters the content hash even on the default path.
DEFAULT_PERCEPT_REF = "plugins.embodiment_robosuite.percept:provider"


def _percept_object(obs, spec: EpisodeSpec, sensor_sd: float, draw: int) -> np.ndarray:
    """Dispatch to the mounted percept provider (spec ref, or the default)."""
    from harness.registry import load_provider

    ref = spec.percept_provider or DEFAULT_PERCEPT_REF
    return load_provider(ref).object_estimate(obs, spec, sensor_sd, draw)


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
    # Names AFTER the env: since round 69's registry inversion, feature
    # registration happens when the embodiment plugin is imported, which in a
    # fresh worker is triggered by make_env loading the provider ref. Reading
    # the catalog before that sees an empty registry and records empty views.
    embodiment = _embodiment(spec)
    env = embodiment.make_env(spec)
    critic_names = PrivilegePolicy(critic_budget=bundle.critic_budget if bundle else 0).critic_names()
    action_names = PrivilegePolicy(critic_budget=bundle.action_budget if bundle else 0).critic_names()
    obs = env.reset()
    driver = make_driver(spec)
    driver.observe_once(obs)
    start_z = float(np.asarray(obs[embodiment.object_key(spec)])[2])

    # --- R2 stage overlay: a SCORER over the policy's schedule, never a
    # controller. Everything is gated behind `stages is not None` so the
    # stageless path stays byte-identical (runs/demo, runs/demo-r1 anchors).
    # The scorer view may read privileged features -- a scorer is not a critic,
    # embodiment.success already reads ground truth -- so its reads are
    # accounted PER STAGE and never merged into `history`, the chain, or
    # assert_privilege_budget: those feed the zero-privilege search.
    stages = spec.stages
    if stages is not None:
        scorer_names = PrivilegePolicy(critic_budget=1).critic_names()
        bounds: list[int] = []
        for s in stages:
            bounds.append(s.budget + (bounds[-1] if bounds else 0))
        stage_results: list[dict] = []
        stage_idx = 0
        stage_entered = 0

        def _score_stage(exited_step: int | None) -> None:
            """Score the current stage on the CURRENT obs and advance."""
            nonlocal stage_idx, stage_entered
            sv = project(obs, scorer_names, step=k, episode=f"s{spec.seed}")
            stage_results.append({
                "name": stages[stage_idx].name,
                "entered_step": stage_entered, "exited_step": exited_step,
                "success": stages[stage_idx].holds(sv), "reached": True,
                "privilege_used": sv.privilege_used(),
            })
            stage_entered = k
            stage_idx += 1

    rules = list(bundle.rules) if bundle else []
    consec = {r.rule_id: 0 for r in rules}
    used = {r.rule_id: 0 for r in rules}
    fires: list[dict] = []
    history: dict[str, list[float]] = {}
    chain = chain_start()
    privilege_used = 0
    recovery: RecoveryActor | None = None
    t = 0            # env steps, for the horizon budget
    k = 0            # POLICY-owned steps: the clock a trigger's arm_after counts in

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

        policy_owned = recovery is None
        obs, _r, done, _info = env.step(action)
        t += 1
        if done:
            break

        # The critic governs the POLICY, so it neither observes nor fires while a
        # scripted recovery owns control, and those steps stay out of the trace.
        # Without this the search reads a mixture: round 16's second generation
        # proposed a trigger armed at t=129, inside a recovery program, on a
        # policy whose own clock is 100 steps long. Trigger `arm_after` therefore
        # counts policy-owned steps, which is also the only clock that means the
        # same thing across episodes of different length.
        if not policy_owned:
            continue

        view = project(obs, critic_names, step=k, episode=f"s{spec.seed}")
        logged = record_view(view)
        for name, value in view.snapshot().items():
            history.setdefault(name, []).append(value)
        chain = chain_step(chain, logged.digest)
        k += 1
        # Stage boundaries live on the DRIVER's schedule clock: on_handback
        # supersedes the interrupted phase, so that clock can jump several
        # cumulative budgets in one hop -- hence a while, scoring every crossed
        # stage on the current (post-recovery) obs, not only the first.
        if stages is not None:
            pk = getattr(driver, "k", k)
            while stage_idx < len(stages) and pk >= bounds[stage_idx]:
                _score_stage(exited_step=k)
        if not rules:
            continue

        assert_view_reconstructable(view, logged)
        triggered: Rule | None = None
        for rule in rules:
            if used[rule.rule_id] >= rule.recovery.max_invocations:
                consec[rule.rule_id] = 0
                continue
            trig = rule.trigger
            if k - 1 < trig.arm_after:
                consec[rule.rule_id] = 0
                continue
            # Instantaneous reading, value-only: the reducer is NOT applied here
            # (search enumerates only what this loop honors, search.RUNTIME_REDUCERS).
            # `crosses` is the one shared op/threshold predicate -- the same test
            # Trigger.fire_step steps offline, so search scores what runtime fires.
            hit = trig.crosses(view[trig.feature])
            consec[rule.rule_id] = consec[rule.rule_id] + 1 if hit else 0
            if triggered is None and consec[rule.rule_id] >= trig.dwell:
                triggered = rule
        assert_privilege_budget(view, bundle.critic_budget, role="critic")
        privilege_used = max(privilege_used, view.privilege_used())
        if triggered is None:
            continue

        used[triggered.rule_id] += 1
        fires.append({"rule_id": triggered.rule_id, "step": k - 1, "env_step": t - 1})
        act_view = project(obs, action_names, step=k - 1, episode=f"s{spec.seed}")
        assert_view_reconstructable(act_view, record_view(act_view))
        assert_privilege_budget(act_view, bundle.action_budget, role="recovery")
        percept = _percept_object(obs, spec, triggered.recovery.sensor_sd, used[triggered.rule_id])
        driver.retarget(percept)
        recovery = RecoveryActor(triggered.recovery.steps(), percept,
                                 height_offset=spec.grasp_height_offset)
        for rid in consec:
            consec[rid] = 0

    if stages is not None:
        # The loop can exit with boundaries still uncrossed on the ledger: an
        # exhaust break lands before the in-loop check, and a hand-back jump
        # can even exhaust the driver outright. Settle crossed stages first,
        # then score the in-progress stage on the final obs, then record the
        # stages the policy never reached.
        pk = getattr(driver, "k", k)
        while stage_idx < len(stages) and pk >= bounds[stage_idx]:
            _score_stage(exited_step=k)
        if stage_idx < len(stages) and pk > (bounds[stage_idx - 1] if stage_idx else 0):
            _score_stage(exited_step=None)
        for s in stages[stage_idx:]:
            stage_results.append({"name": s.name, "entered_step": None, "exited_step": None,
                                  "success": False, "reached": False, "privilege_used": 0})

    # R2 round 79: terminal_label swaps the shared sub-goal for the embodiment's
    # full-task terminal boolean. Scored here, while env is still alive, because
    # some terminal predicates need the live handle (Stack's _check_success reads
    # contact ground truth the obs dict cannot carry). No handle-less fallback:
    # an embodiment asked for a terminal label it cannot produce fails loud rather
    # than mislabelling silently. terminal_label False is the byte-identical
    # legacy path.
    if spec.terminal_label:
        terminal = getattr(embodiment, "terminal_success", None)
        if terminal is None:
            raise ValueError(
                f"spec.terminal_label is set but embodiment {type(embodiment).__name__} "
                "has no terminal_success; refusing to fall back to the sub-goal (round 79)")
        success = bool(terminal(obs, spec, start_z, env=env))
    else:
        success = embodiment.success(obs, spec, start_z)
    env.close()
    result = {
        "seed": spec.seed,
        "task": spec.task,
        "policy": driver.identity,
        "success": success,
        "steps": t,
        "policy_steps": k,
        "fires": fires,
        "fired_at": fires[0]["step"] if fires else None,
        "fired_rules": sorted({f["rule_id"] for f in fires}),
        "chain": chain,
        "critic_privilege_used": privilege_used,
        "declared_privilege": bundle.declared_privilege() if bundle else 0,
        "trace": {k: np.asarray(v) for k, v in history.items()},
    }
    if stages is not None:
        # The ONLY new key, and only on the opt-in path: stages=None emits
        # nothing new anywhere. Per-stage results are measurement output like
        # trace, not hashed identity.
        result["stages"] = stage_results
    return result
