"""The pure-config matrix proof: GOAL.md acceptance #2.

"挂载即配置" (mounting IS configuration) needs a concrete witness that
swapping task/policy is a config edit, not a code edit. This file is that
witness, entirely at the config layer:

(a) ``base_profile()`` resolves and mounts on a fresh kernel without error,
    and every mounted capability resolves.
(b) the resolved plan's sha is deterministic across repeated resolutions, and
    moves when a patch overrides a mount's params.
(c) ``matrix_plans()`` covers the full 3x2 grid, shares one ``MountPlan``
    across every cell, and each cell's overrides are accepted by
    ``plugins.rsi.campaign.Preregistration`` with a distinct resulting sha.
(d) a skill published through the mounted ``graph.skill`` capability round
    trips and is content-addressed.

No environment is ever built and no rollout ever runs -- construction only.
``plugins.embodiment_robosuite`` imports ``governor.env``, but that module
imports ``robosuite`` lazily inside ``_default_make_env`` (verified: importing
the plugin alone does not pull ``robosuite`` into ``sys.modules``), so mounting
it here stays fast; this file simply never calls anything that reaches that
import.
"""

from __future__ import annotations

from harness import Kernel, Mount, Patch, resolve_plan
from harness.config import MountPlan
from harness.definitions import CAPABILITIES
from profiles import POLICIES, TASKS, base_profile, matrix_plans

CONSUMER = "matrix-test"


def _fresh_kernel() -> Kernel:
    return Kernel(CAPABILITIES)


# --- (a) base_profile mounts and every mounted capability resolves ---------

def test_base_profile_mounts_cleanly_and_every_capability_resolves():
    plan = resolve_plan(base_profile())
    k = _fresh_kernel()
    k.mount(plan)  # construction only -- no episode, no environment build

    mounted = {m.capability for m in plan.mounts}
    assert mounted == {
        "embodiment.env", "policy.driver", "percept.model",
        "reasoner.proposer", "graph.skill", "graph.scene", "exec.rollouts",
    }
    for capability in mounted:
        provider = k.resolve(capability, consumer=CONSUMER)
        assert provider is not None

    resolved = {r.capability for r in k.resolutions()}
    assert resolved == mounted
    assert all(r.consumer == CONSUMER for r in k.resolutions())
    assert not any(r.privileged for r in k.resolutions()), \
        "the base profile mounts no privileged capability"


# --- (b) plan sha: deterministic, and moved by a patch ----------------------

def test_plan_sha_is_deterministic_across_resolutions():
    a = resolve_plan(base_profile())
    b = resolve_plan(base_profile())
    assert a.sha() == b.sha()
    assert a.canonical() == b.canonical()
    assert a.sources == b.sources


def test_plan_sha_moves_under_a_patch_overriding_exec_rollouts_params():
    baseline = resolve_plan(base_profile())
    patch = Patch("exec-tag", override=(
        Mount("exec.rollouts", "harness.executor:provider", {"tag": "patched"}),
    ))
    patched = resolve_plan(base_profile(), patches=(patch,))

    assert patched.sha() != baseline.sha()
    # every other mount is untouched by the patch
    baseline_by_cap = {m.capability: m for m in baseline.mounts}
    patched_by_cap = {m.capability: m for m in patched.mounts}
    for capability, mount in baseline_by_cap.items():
        if capability == "exec.rollouts":
            continue
        assert mount == patched_by_cap[capability]
    assert patched_by_cap["exec.rollouts"].params == {"tag": "patched"}


# --- (c) matrix_plans: full grid, one shared plan, valid Preregistration ---

def test_matrix_plans_covers_the_full_grid_with_one_shared_plan():
    plans = matrix_plans()

    expected_keys = {(task, policy) for task in TASKS for policy in POLICIES}
    assert set(plans) == expected_keys
    assert len(plans) == 6

    plan_shas = {plan.sha() for plan, _ in plans.values()}
    assert plan_shas == {resolve_plan(base_profile()).sha()}, \
        "L0's proof requires one shared mount plan across the whole matrix"
    for plan, _overrides in plans.values():
        assert isinstance(plan, MountPlan)


def test_matrix_plan_overrides_are_compatible_with_preregistration():
    from plugins.rsi.campaign import Preregistration

    plans = matrix_plans()
    shas = set()
    for (task, policy), (_plan, overrides) in plans.items():
        assert overrides == {"task": task, "policy": policy}
        prereg = Preregistration(
            dev=(1, 2), heldout=(3, 4),
            percept_noise=0.02, critic_budget=0, action_budget=0,
            recovery_sensor_sd=0.02, max_generations=1,
            **overrides,
        )
        assert prereg.task == task
        assert prereg.policy == policy
        shas.add(prereg.sha())

    assert len(shas) == 6, "each (task, policy) cell must yield a distinct prereg sha"


# --- (d) publish/skills round-trip through the mounted graph.skill ---------

def test_skill_publish_round_trips_through_the_mounted_graph():
    plan = resolve_plan(base_profile())
    k = _fresh_kernel()
    k.mount(plan)

    graph = k.resolve("graph.skill", consumer=CONSUMER)
    assert graph.skills() == ()

    record = {"trigger": "observable.finger_gap < 0.002", "effect": {"delta": 0.32}}
    digest = graph.publish(record)

    assert graph.skills() == (record,)
    assert digest == graph.publish(record), "content addressing broke: same record, new digest"
    assert len(graph.skills()) == 1, "publishing an identical record must not duplicate it"
