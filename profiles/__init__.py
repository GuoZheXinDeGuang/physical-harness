"""Configuration profiles: the harness config layer's first concrete profile.

``base_profile()`` mounts each L0 reference provider once, so a
``resolve_plan()`` over it is the harness's own "hello world" -- every
capability the L0 milestone requires has a provider, and swapping any of them
is a config edit, not a code edit (GOAL.md acceptance #2, "挂载即配置").

Two capabilities in ``harness.definitions.CAPABILITIES`` are deliberately NOT
mounted here:

- ``embodiment.ground_truth`` is privileged and simulator-only (see
  ARCHITECTURE.md's OS-layer table). A base profile handing it out for free
  would let every consumer draw on the privilege budget; a campaign mounts it
  explicitly, only where it needs oracle state.
- ``percept.model`` has not migrated out of ``governor.governed`` yet (see
  ARCHITECTURE.md, row "感知": "L1 迁入; 现在留在 governor.governed"). It
  arrives at L1, not here.

The task/policy matrix
-----------------------
GOAL.md acceptance #2 also asks for a pure-config proof over a 3-task x
2-policy matrix ("用 3 任务 x 2 策略的纯配置矩阵证明"). Task and policy are
NOT capability mounts, though: they are ``governor.campaign.Preregistration``
fields, threaded down into episode specs by ``governor.campaign._specs``, not
anything ``harness.config.Mount`` binds a provider to.

A ``campaign_patch(task, policy) -> Patch`` would therefore have nothing to
override -- at L0 the mount plan is IDENTICAL across the whole matrix (same
embodiment provider, same policy provider, same executor; only the
*preregistration arguments* differ per cell). Encoding that as a no-op Patch
would be misleading busywork, so ``matrix_plans()`` below returns the honest
shape instead: one shared ``MountPlan`` plus, per ``(task, policy)`` cell, the
``Preregistration`` overrides that actually vary it.

At L1, embodiment variants (a different simulator, a different robot rig)
become real ``Bundle``s layered over this profile -- that is the point where
the mount plan itself starts varying per cell, not before.
"""

from __future__ import annotations

from harness.config import Mount, MountPlan, Profile, resolve_plan

#: The 3-task x 2-policy pure-config matrix GOAL.md acceptance #2 asks for.
TASKS: tuple[str, ...] = ("lift", "stack", "pickcan")
POLICIES: tuple[str, ...] = ("scripted", "runs/bc_h256.npz")


def base_profile() -> Profile:
    """The L0 reference mounts: one provider per non-privileged, migrated capability."""
    return Profile("base", (
        Mount("embodiment.env", "plugins.embodiment_robosuite:provider"),
        Mount("policy.driver", "plugins.policies:provider"),
        Mount("reasoner.proposer", "plugins.reasoner:provider", {"top_k": 3}),
        Mount("graph.skill", "plugins.graphs:skill_graph_provider"),
        Mount("graph.scene", "plugins.graphs:scene_graph_provider"),
        Mount("exec.rollouts", "harness.executor:provider"),
    ))


def matrix_plans() -> dict[tuple[str, str], tuple[MountPlan, dict[str, str]]]:
    """The 3-task x 2-policy pure-config matrix, as (shared plan, prereg overrides).

    Every cell resolves to the SAME ``MountPlan`` (same providers, same
    ``.sha()``) -- that sharing IS the L0 proof: varying task/policy across the
    matrix is a config edit to ``Preregistration``, not a code edit to what is
    mounted. See the module docstring for why this is not a ``Patch``.
    """
    plan = resolve_plan(base_profile())
    return {
        (task, policy): (plan, {"task": task, "policy": policy})
        for task in TASKS
        for policy in POLICIES
    }
