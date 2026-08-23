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
- ``percept.model`` lives in ``plugins.embodiment_robosuite.percept`` (L1 rung 1). It
  arrives at L1, not here.

The task/policy matrix
-----------------------
GOAL.md acceptance #2 also asks for a pure-config proof over a 3-task x
2-policy matrix ("用 3 任务 x 2 策略的纯配置矩阵证明"). Task and policy are
NOT capability mounts, though: they are ``plugins.rsi.campaign.Preregistration``
fields, threaded down into episode specs by ``plugins.rsi.campaign._specs``, not
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
from harness.manifest import discover

#: The 3-task x 2-policy pure-config matrix GOAL.md acceptance #2 asks for.
TASKS: tuple[str, ...] = ("lift", "stack", "pickcan")
POLICIES: tuple[str, ...] = ("scripted", "runs/bc_h256.npz")


#: The one mount the base OWNS rather than a card: the local-pool execution
#: fabric lives in ``harness`` (no plugin), so it is not folded from a manifest.
_BASE_OWNED: tuple[Mount, ...] = (Mount("exec.rollouts", "harness.executor:provider"),)


def base_profile() -> Profile:
    """The L0 reference mounts: a FOLD over every installed plugin manifest.

    Each card declares its ``cap -> ref (+params)`` in ``plugins/*/manifest.toml``
    (parsed as data, never imported), and ``discover()`` unions them -- so a new
    embodiment or policy is a dropped-in manifest, not an edit here. The base
    contributes only ``exec.rollouts`` (harness-owned). ``resolve_plan`` sorts by
    capability, so the plan sha is a pure function of the installed manifest set:
    a changed set is a different experiment identity (charter "配置变=另一个实验身份").
    ``embodiment.ground_truth`` stays off the base (privileged; a campaign mounts
    it only where it needs oracle state) -- no manifest declares it.
    """
    return Profile("base", discover().mounts + _BASE_OWNED)


def fakes_profile() -> Profile:
    """Zero-plugin boot: every non-privileged capability mounted to a base fake.

    This is the "开机" profile (W1) -- with ``plugins/`` empty the kernel still
    has a provider for each seam, so a governed episode runs end to end on
    ``harness.fakes`` alone. ``embodiment.ground_truth`` stays unmounted for the
    same reason ``base_profile`` omits it (privileged, mounted only where oracle
    state is needed); ``exec.rollouts`` reuses the base-owned local pool.
    """
    return Profile("fakes", (
        Mount("embodiment.env", "harness.fakes:env_provider"),
        Mount("policy.driver", "harness.fakes:policy_provider"),
        Mount("percept.model", "harness.fakes:percept_provider"),
        Mount("reasoner.proposer", "harness.fakes:reasoner_provider"),
        Mount("task.planner", "harness.fakes:task_planner_provider"),
        Mount("graph.skill", "harness.fakes:skill_graph_provider"),
        Mount("graph.scene", "harness.fakes:scene_graph_provider"),
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


def bundle(name: str):
    """Build an installed card's named overlay bundle from its manifest.

    A bundle is an alternate mount set a card owns (a second robot, a real-world
    scene bridge), declared in ``plugins/*/manifest.toml`` under ``[bundles.<name>]``
    and folded by ``discover``. ``resolve_plan(base_profile(), bundles=(bundle("sawyer"),))``
    is the whole story of "a second robot": the plan sha moves (the mount IS
    experiment identity), every other capability keeps its provider. The wiring
    lives in the card, not here -- so a new robot's bundle is a manifest edit, not
    a code edit, exactly like adding a task is a dropped-in plugin dir.
    """
    from harness.config import Bundle

    reg = discover()
    if name not in reg.bundles:
        raise KeyError(
            f"no bundle {name!r} among installed manifests "
            f"(have {sorted(reg.bundles)}) -- declare it under [bundles.{name}]")
    return Bundle(name, reg.bundles[name])
