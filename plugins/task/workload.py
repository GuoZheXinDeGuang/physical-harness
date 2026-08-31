"""Task-planning workload: the strict form of the OS loop's Plan -> Act -> Verify edge.

Structural sibling of ``plugins/rsi/workload.py``: every dependency is resolved
through the kernel as consumer "task", so the audit trail accounts the whole
loop and swapping any provider is a mount-plan edit, not a code edit here.

Two layers, never merged: the planner's output is a skill-call graph -- the
inter-skill CONTROLLER (which skills, what order). Each node dispatches as ONE
``governed_rollout`` whose StageSpec chain is the intra-skill SCORER; stages'
"scorer never controller" invariant (harness/stages.py) is what licenses their
privileged reads, so the plan is never collapsed into a stage chain.

The replan loop is this workload's OWN while (zos plan_turn's shape, harness
side). ``kernel.note`` is evidence, never the mechanism. ``max_replans`` /
``max_actuations`` are model-independent floors, workload config on purpose:
the "seven actions on one y" incident class stays fenced no matter which
planner is mounted behind the seam.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from harness import opstream
from harness.config import sha_json
from harness.features import privilege_cost
from harness.kernel import Kernel
from harness.registry import load_provider
from harness.spec import EpisodeSpec
from plugins.task.validate import NODE_KINDS, validate_plan

#: The kernel consumer name every capability this workload resolves is
#: accounted under (harness/kernel.py per-resolution audit trail).
CONSUMER = "task"

#: skill name -> EpisodeSpec kwargs for the node that runs it: the EXECUTION
#: half of the hand-declared Stack vocabulary (planner_stack.CATALOGUE is the
#: symbolic half the planner sees). ``stages`` is a "module:factory" ref
#: resolved at dispatch through harness.registry -- plugins never import each
#: other (tests/test_boundaries.py), and the ref string is the same sanctioned
#: crossing episode specs already use for env/policy providers. percept_noise
#: 0.012 is the Stack policy's calibrated operating point (scripts/stack_campaign.py).
SKILL_SPECS: dict[str, dict[str, Any]] = {
    "stack": {"task": "stack", "percept_noise": 0.012, "terminal_label": True,
              "stages": "plugins.embodiment_robosuite.env:stack_stages"},
    # One skill, several scenes: "pick" resolves its embodiment task from the
    # node's object arg (the first arg threading, arrived with the second
    # skill provider exactly as round 83 predicted).
    "pick": {"task_by_object": {"can": "pickcan", "milk": "pickmilk"},
             "percept_noise": 0.012,  # the phase-3 operating point; omitting it
             # silently falls to EpisodeSpec's 0.020 default -- the probe ran at
             # 0.012 and the first real closed loop failed on the drift (round 86)
             "terminal_label": True,
             "stages": "plugins.embodiment_robosuite.env:pick_stages"},
    # The geometric-grasp card's follow-up (skill_geometric_grasp/planner.py
    # docstring): its single grasp node driven by the generic loop. Names its
    # task directly like "stack" (one scene, the grasp IS the Lift task under
    # the card-mounted lift_geometric_provider) -- not task_by_object, the node's
    # object arg is card vocabulary the catalogue only types. Mirrors the card's
    # own [claim] verbatim (task="lift", pick_stages, percept_noise 0.012,
    # terminal_label): the pick grasp stage (finger_gap>0.01) plus lifted() on
    # the terminal label ARE the real lift criteria, no separate success path.
    "grasp": {"task": "lift", "percept_noise": 0.012, "terminal_label": True,
              "stages": "plugins.embodiment_robosuite.env:pick_stages"},
}


def _governed_rollout(spec: EpisodeSpec, bundle) -> dict:
    """One node, one governed rollout under ``bundle`` (``None`` runs ungoverned).

    Reached by ref string, not import, for the same boundary reason as
    SKILL_SPECS above. Module-level so tests can monkeypatch the dispatch
    without touching the loop.
    """
    return importlib.import_module("plugins.rsi.governed").governed_rollout(spec, bundle)


def assemble_bundle(records, task):
    """Reassemble a task's mounted SkillRecords into the governance Bundle whose
    evidence they earned -- the SAME inversion + growth the campaign
    rebuild/rescore/probe paths use (``rule_from_canonical`` + ``Bundle.append``),
    never a second assembly implementation.

    A record's ``preconditions`` IS a rule's canonical trigger and its
    ``recovery`` IS the canonical recovery (plugins/rsi/workload.py), so the only
    adaptation is a chain-position ``rule_id`` (g1..gN over ``records`` order --
    ``skills()`` returns a content-digest sort, a deterministic total order; the
    id only needs determinism+uniqueness because governed_rollout keys firing
    state by list index, not id). Budgets are the MINIMUM admitting every rule's
    declared reads, derived from the harness's own ``privilege_cost`` (the exact
    function the episode's ``assert_privilege_budget`` measures against): the
    critic budget SUMS distinct trigger features (one accumulating per-step view),
    the action budget is the MAX recovery percept privilege (a fresh view per
    fire, no cross-rule accumulation).

    Only records whose held-out blind-twin judgement was ESTABLISHED may steer a
    real run (charter: rules govern via the same governed_rollout that earned
    their evidence); everything else is excluded. Zero matching records ->
    ``(None, [])``, byte-identical to a bare ``governed_rollout(spec, None)``.

    Reached by importlib, not import: plugins.task may not static-import
    plugins.rsi (tests/test_boundaries.py) -- the same dodge ``_governed_rollout``
    uses. Returns ``(bundle_or_None, [content_digest, ...])`` where the digests
    ARE the skills-root filename stems / boot-seal ``skills_manifest`` entries, so
    an auditor re-derives the identical bundle from exactly those records.
    """
    matching = [r for r in records
                if r.get("task") == task
                and r.get("heldout_judgement_established") is True]
    if not matching:
        return None, []
    rebuild = importlib.import_module("plugins.rsi.rebuild")
    Bundle = importlib.import_module("plugins.rsi.governed").Bundle
    rules = [rebuild.rule_from_canonical(
                {"rule_id": f"g{i + 1}", "trigger": rec["preconditions"],
                 "recovery": rec["recovery"]})
             for i, rec in enumerate(matching)]
    critic_budget = privilege_cost(sorted({r.trigger.feature for r in rules}))
    action_budget = max((r.recovery.percept_privilege for r in rules), default=0)
    bundle = Bundle(rules=(), critic_budget=critic_budget, action_budget=action_budget)
    for rule in rules:
        bundle = bundle.append(rule)
    return bundle, [sha_json(rec) for rec in matching]


def _dispatch(node: Mapping, *, seed: int, env_ref: str, policy_ref: str,
              skills) -> dict:
    """Build the node's EpisodeSpec and run it, stage scorer aboard, under the
    governance bundle its task's mounted skills assemble to.

    A binding either names its embodiment task directly ("stack": the policy
    is cubeA-on-cubeB by construction) or carries ``task_by_object`` mapping
    the node's object arg to a task ("pick"): the catalogue only types the
    arg, so an object with no scene binding fails loudly HERE, before any
    actuation. ``skills`` is the mounted SkillRecord set (``graph.skill``);
    ``assemble_bundle`` selects the ones this node's task earned governance with,
    or ``None`` when none match -- the ungoverned path, unchanged from before.
    """
    binding = SKILL_SPECS.get(node["skill"])
    if binding is None:
        raise ValueError(f"skill {node['skill']!r} validated against the "
                         "catalogue but has no execution binding in SKILL_SPECS")
    kwargs = dict(binding)
    task_by_object = kwargs.pop("task_by_object", None)
    if task_by_object is not None:
        obj = node["args"].get("object")
        if obj not in task_by_object:
            raise ValueError(
                f"skill {node['skill']!r} has no task binding for object "
                f"{obj!r}; known objects: {sorted(task_by_object)}")
        kwargs["task"] = task_by_object[obj]
    stages = load_provider(kwargs.pop("stages"))
    spec = EpisodeSpec(seed=seed, stages=stages, env_provider=env_ref,
                       policy_provider=policy_ref, **kwargs)
    bundle, digests = assemble_bundle(skills, spec.task)
    result = _governed_rollout(spec, bundle)
    # Seal WHICH rules governed this node and under what budgets: the content
    # digests cross-check against runtime.boot's skills_manifest, bundle_sha pins
    # the assembled chain's identity. Empty/None for a node no established skill
    # matched (honest empty, byte-identical to today).
    result["governance"] = {
        "skills": digests,
        "bundle_sha": bundle.sha() if bundle is not None else None,
        "critic_budget": bundle.critic_budget if bundle is not None else 0,
        "action_budget": bundle.action_budget if bundle is not None else 0,
    }
    return result


# ── generic node-kind machinery (m6-mission-design §2) ───────────────────────
# The base owns the KINDS (generic handlers); a card owns the PREDICATES (the
# machine oracles they resolve). A mission is pure data: nodes carry an optional
# ``kind`` and name a predicate in their ``skill`` slot; the card's PREDICATES
# table maps that name -> a "module:factory" ref whose factory returns the
# callable ``predicate(node, ctx) -> {"success": bool, ...}``. Every handler
# returns the SAME ``{"success": bool, "governance": {...}}`` shape the loop
# already reads, and the truth is ALWAYS a machine predicate, never a model claim.

@dataclass
class EpisodeContext:
    """ONE persistent sim episode threaded through the plan graph (M7): the live
    ``env``, the running ``obs``, the retargetable ``driver``, and a ``cursor``
    (env steps consumed so far vs ``spec.horizon``). Built ONCE when a brief
    declares ``episodic``; ``env.close()`` fires ONCE at mission end (win, abort,
    or horizon), not per sub-goal -- the whole point of M7 over M6's fresh-per-node
    dispatch. The world carries consequences forward: a ``segment`` reads the obs
    the prior segment LEFT (a dropped object stays where it fell), never a reset
    preview. Mutable on purpose -- the loop threads one instance through every node.
    """

    embodiment: Any
    env: Any
    driver: Any
    spec: EpisodeSpec
    obs: Any
    cursor: int = 0
    closed: bool = False

    @property
    def exhausted(self) -> bool:
        """The shared robosuite horizon is spent; driving further is refused."""
        return self.cursor >= self.spec.horizon

    def close(self) -> None:
        """Idempotent single close -- the mission-end teardown, fired once."""
        if not self.closed:
            self.closed = True
            self.env.close()


@dataclass(frozen=True)
class NodeCtx:
    """What a node-kind handler may read. ``manipulate`` uses only the refs;
    ``perceive`` also resets the same-seed env by ref; ``decide``/``verify`` read
    only ``nodes_out`` (accumulated prior sealed results). ``nodes_out`` is the
    loop's live dict passed by reference, so a later node sees earlier facts.

    ``episode`` is the persistent M7 world or ``None``. When set, ``segment`` nodes
    drive it (no make/close) and a card's perceive/verify predicate reads its live
    ``obs`` instead of resetting a fresh env -- the same handle carries every
    sub-goal's consequence forward. ``None`` is today's fresh-per-node path,
    byte-identical (every existing card omits ``episodic``)."""

    seed: int
    env_ref: str
    policy_ref: str
    skills: tuple
    nodes_out: Mapping[str, dict]
    predicates: Mapping[str, str]  # predicate name -> "module:factory" ref
    episode: EpisodeContext | None = None
    #: segment skill -> per-sub-goal spec override (task/task_by_object/stages),
    #: the segment analog of SKILL_SPECS: it routes each sub-goal's object to the
    #: task whose object_key the driver retargets on. Empty -> every segment drives
    #: the ONE episode spec (a single-object mission). Card data, threaded on the brief.
    segment_specs: Mapping[str, Mapping] | None = None


#: A non-manipulate node seals zero privilege: decide/verify read only sealed
#: prior facts, never a raw pose. Perceive overrides this with a measured budget.
_ZERO_PRIV = {"privilege_features": (), "privilege_cost": 0}


def _predicate(node: Mapping, ctx: NodeCtx):
    """Resolve a non-manipulate node's card-authored predicate by ref.

    The ``skill`` slot names the predicate; the card's PREDICATES table (folded
    into the task binding, threaded onto the brief) maps it to a "module:factory"
    ref. Same sanctioned crossing as ``stages`` -- ``load_provider`` calls the
    factory and returns the callable, so plugins never import each other. A node
    whose predicate is undeclared fails loudly HERE, before any oracle runs."""
    ref = ctx.predicates.get(node["skill"])
    if ref is None:
        raise ValueError(
            f"node {node['id']!r} (kind {node.get('kind')!r}) names predicate "
            f"{node['skill']!r} with no ref in the card's PREDICATES table; "
            f"declared predicates: {sorted(ctx.predicates)}")
    return load_provider(ref)


def _manipulate(node: Mapping, ctx: NodeCtx) -> dict:
    """The TODAY handler, byte-identical: a governed rollout under the node's
    task's assembled bundle. Ignores ``ctx.nodes_out``/``predicates``."""
    return _dispatch(node, seed=ctx.seed, env_ref=ctx.env_ref,
                     policy_ref=ctx.policy_ref, skills=ctx.skills)


def _perceive(node: Mapping, ctx: NodeCtx) -> dict:
    """Read the seed-deterministic scene through a card predicate; seal the
    privilege it declares it read. The predicate resets the same-seed task env by
    ref and reads poses; it returns ``{"success", "facts", "privilege": [feature
    names]}``. The base -- not the card -- meters the budget through the harness's
    own ``privilege_cost`` (the exact function the episode's privilege gate
    measures against), so a perceive node pays the SAME accounting a critic pays;
    an undeclared feature raises in ``privilege_cost``."""
    raw = _predicate(node, ctx)(node, ctx)
    features = tuple(raw.get("privilege", ()))
    return {"success": bool(raw["success"]), "facts": raw.get("facts"),
            "governance": {"privilege_features": features,
                           "privilege_cost": privilege_cost(features)}}


def _decide(node: Mapping, ctx: NodeCtx) -> dict:
    """A PURE function of ``ctx.nodes_out`` (prior facts + faults) -> a route.
    Zero privilege, deterministic in seed -> byte-identical replay."""
    raw = _predicate(node, ctx)(node, ctx)
    return {"success": bool(raw["success"]), "decision": raw.get("decision"),
            "governance": dict(_ZERO_PRIV)}


def _verify(node: Mapping, ctx: NodeCtx) -> dict:
    """A predicate over a prior node's sealed result (a privileged stage residual
    thresholded, or a boolean AND of prior successes). Truth = machine predicate
    over sealed residuals; on ``False`` the loop's existing fault->replan fires."""
    raw = _predicate(node, ctx)(node, ctx)
    return {"success": bool(raw["success"]), "governance": dict(_ZERO_PRIV)}


def _episode_spec(brief: Mapping, *, seed: int, env_ref: str, policy_ref: str) -> EpisodeSpec:
    """Build the ONE persistent episode's spec from the card's ``episode`` block on
    the brief (kwargs a card authors: task, percept_noise, terminal_label, horizon,
    and a ``stages`` REF resolved the same sanctioned way ``_dispatch`` resolves a
    node's stages). Absent -> a bare same-seed spec. The mission stays pure data:
    the base never names a task, the card's episode block does."""
    kwargs = dict(brief.get("episode") or {})
    stages_ref = kwargs.pop("stages", None)
    stages = load_provider(stages_ref) if stages_ref else None
    return EpisodeSpec(seed=seed, stages=stages, env_provider=env_ref,
                       policy_provider=policy_ref, **kwargs)


def _segment_governance(bundle, digests, entered: int, exited: int) -> dict:
    """The per-sub-goal governance seal: the SAME shape ``_dispatch`` seals for a
    fresh rollout (which established skills governed under what budgets), PLUS the
    sub-goal's env-step span off the shared cursor so an auditor reconstructs the
    whole persistent timeline from one note (m7 §2e). One seal per sub-goal."""
    return {
        "skills": digests,
        "bundle_sha": bundle.sha() if bundle is not None else None,
        "critic_budget": bundle.critic_budget if bundle is not None else 0,
        "action_budget": bundle.action_budget if bundle is not None else 0,
        "entered_env_step": entered,
        "exited_env_step": exited,
    }


def _governed_segment(episode: EpisodeContext, seg_spec: EpisodeSpec, bundle, *,
                      step_budget: int) -> dict:
    """Drive the PERSISTENT episode ONE sub-goal segment on the SHARED env -- no
    make, no reset, no close. Re-aims the shared driver at this sub-goal's object
    (its live pose off ``episode.obs``) and restarts its grasp schedule, drives the
    extracted ``governed_segment`` loop for ``step_budget`` env steps, advances the
    cursor by what it consumed, then scores the sub-goal terminal on the obs the
    drive LEFT. The arm starts wherever the last sub-goal ended -- no teleport.

    Reached by importlib (plugins.task may not import plugins.rsi --
    tests/test_boundaries.py) and module-level so tests monkeypatch it or the inner
    ``governed_segment`` exactly as they monkeypatch ``_governed_rollout``."""
    gov = importlib.import_module("plugins.rsi.governed")
    ep = episode
    # Heterogeneous episodic driver (M7): a mission whose sub-goals are DIFFERENT
    # behaviours -- navigate, grasp, place, close, press -- not one retargetable
    # grasp over N objects. Such a driver binds the live world + THIS sub-goal's
    # spec itself (``enter_segment``) and reports its OWN stage terminal --
    # arrived / grasped / placed / closed / on (``segment_success``) -- so the base
    # neither retargets by object pose nor scores a fixed lifted()/_check_success
    # terminal (neither of which is the right sub-goal truth for a nav or a close).
    # An obs-only retargetable driver (robosuite clear_workspace's ScriptedDriver)
    # has no ``enter_segment`` -> the pose-retarget path below, byte-identical.
    # ponytail: two branches, one protocol probe; unify only if a THIRD driver
    # shape appears that fits neither.
    if hasattr(ep.driver, "enter_segment"):
        ep.driver.enter_segment(ep.env, seg_spec)
        seg = gov.governed_segment(ep.env, ep.obs, ep.driver, seg_spec, bundle,
                                   step_budget=step_budget)
        ep.obs = seg["obs"]
        ep.cursor += seg["steps"]
        seg["success"] = ep.driver.segment_success(ep.env)
        if hasattr(ep.driver, "segment_diagnostics"):
            seg["diagnostics"] = ep.driver.segment_diagnostics(ep.env)
        return seg
    obj_key = ep.embodiment.object_key(seg_spec)
    ep.driver.retarget(ep.obs[obj_key])
    # ponytail: pokes driver.k to restart the four-phase grasp clock for the next
    # sub-goal (the prior one exhausted it); add a PolicyDriver.restart() if an
    # episodic driver ever lacks .k. on_handback already writes .k (drivers.py).
    if hasattr(ep.driver, "k"):
        ep.driver.k = 0
    start_z = float(ep.obs[obj_key][2])
    seg = gov.governed_segment(ep.env, ep.obs, ep.driver, seg_spec, bundle,
                               step_budget=step_budget)
    ep.obs = seg["obs"]
    ep.cursor += seg["steps"]
    seg["success"] = gov.score_terminal(ep.embodiment, ep.obs, seg_spec, start_z, ep.env)
    return seg


def _segment_spec(node: Mapping, ep: EpisodeContext, ctx: NodeCtx) -> EpisodeSpec:
    """The per-sub-goal spec: the persistent episode's spec, optionally re-tasked
    to this sub-goal's object so ``object_key`` resolves to the object the driver
    retargets on. Reuses ``_dispatch``'s ``task_by_object`` shape (the pick pattern)
    -- no override -> the ONE episode spec drives every sub-goal (single-object)."""
    binding = (ctx.segment_specs or {}).get(node["skill"])
    if not binding:
        return ep.spec
    kwargs = dict(binding)
    args = dict(node.get("args") or {})
    # Static skill-library grounding: the abstract graph carries semantic args
    # (grasp(object=hot0), place(object=hot0,target=tupperware0)); the
    # embodiment binding turns those into its private sub-task vocabulary.  A
    # model cannot smuggle an unknown object or a wrong object->target pairing
    # through string formatting -- both are checked before a driver is armed.
    allowed_args = kwargs.pop("allowed_args", None)
    if allowed_args:
        for arg, allowed in allowed_args.items():
            if args.get(arg) not in allowed:
                raise ValueError(
                    f"segment {node['id']!r}: {arg}={args.get(arg)!r} is not "
                    f"grounded; allowed values: {list(allowed)!r}")
    target_by_object = kwargs.pop("target_by_object", None)
    if target_by_object is not None:
        obj, target = args.get("object"), args.get("target")
        expected = target_by_object.get(obj)
        if expected is None or target != expected:
            raise ValueError(
                f"segment {node['id']!r}: target {target!r} is invalid for "
                f"object {obj!r}; expected {expected!r}")
    task_template = kwargs.pop("task_template", None)
    if task_template is not None:
        try:
            kwargs["task"] = str(task_template).format(**args)
        except KeyError as exc:
            raise ValueError(
                f"segment {node['id']!r}: binding template {task_template!r} "
                f"needs missing arg {exc.args[0]!r}") from None
    task_by_object = kwargs.pop("task_by_object", None)
    if task_by_object is not None:
        obj = args.get("object")
        if obj not in task_by_object:
            raise ValueError(
                f"segment {node['id']!r}: object {obj!r} has no task binding; "
                f"known objects: {sorted(task_by_object)}")
        kwargs["task"] = task_by_object[obj]
    stages_ref = kwargs.pop("stages", None)
    if stages_ref is not None:
        kwargs["stages"] = load_provider(stages_ref)
    return ep.spec.child(**kwargs)


def _segment(node: Mapping, ctx: NodeCtx) -> dict:
    """SEGMENT (M7): drive the ONE persistent episode for this sub-goal. Unlike
    ``manipulate`` (a throwaway make->reset->drive->close per node), a segment runs
    on the shared live env so the world carries the prior sub-goal's consequence
    forward, and a failed verify downstream replans into the SAME world (the loop's
    existing fault->replan, no reset). Governance mounts PER SEGMENT: this sub-goal's
    task's established skills assemble a bundle exactly as ``_dispatch`` does. The
    seal carries the sub-goal's env-step span off the shared cursor."""
    ep = ctx.episode
    if ep is None:
        raise ValueError(
            f"segment node {node['id']!r} needs a persistent episode; the brief must "
            "declare 'episodic' (no EpisodeContext threaded on this run)")
    seg_spec = _segment_spec(node, ep, ctx)
    bundle, digests = assemble_bundle(ctx.skills, seg_spec.task)
    entered = ep.cursor
    if ep.exhausted:
        # Mission-level abort floor (m7 §2c): the shared horizon is spent. Seal an
        # honest zero-step partial rather than drive a dead env; the loop faults on
        # success=False and bounds the retries via max_replans, never a reset.
        return {"success": False, "steps": 0, "stages": [], "aborted": "horizon",
                "governance": _segment_governance(bundle, digests, entered, entered)}
    seg = _governed_segment(ep, seg_spec, bundle, step_budget=ep.spec.horizon - ep.cursor)
    exited = ep.cursor
    stages = seg.get("stages", [])
    opstream.emit("sub_goal_transition", node=node["id"],
                  object=(node.get("args") or {}).get("object"),
                  success=bool(seg["success"]),
                  entered_env_step=entered, exited_env_step=exited)
    return {"success": bool(seg["success"]), "steps": seg.get("steps"),
            "stages": stages,
            "diagnostics": seg.get("diagnostics", {}),
            "governance": _segment_governance(bundle, digests, entered, exited)}


#: kind -> handler ``(node, ctx) -> {"success", "governance", ...}``. The names
#: are validate.NODE_KINDS verbatim; the assert makes a drifted table a loud
#: import error, never a silent KeyError mid-brief.
_KIND_HANDLERS = {
    "manipulate": _manipulate,
    "segment": _segment,
    "perceive": _perceive,
    "decide": _decide,
    "verify": _verify,
}
assert set(_KIND_HANDLERS) == set(NODE_KINDS), \
    "node-kind handler table drifted from validate.NODE_KINDS"


def run(brief: Mapping, kernel: Kernel, *, seed: int,
        max_replans: int = 3, max_actuations: int = 3,
        segment_retries: int = 0, cancelled=None) -> dict[str, Any]:
    """Run one plan -> validate -> act -> verify -> replan loop through the kernel.

    ``brief`` carries task/catalogue/oracles (planner-facing vocabulary,
    authored on the skill side); the workload stamps scene and remaining budget
    onto it each attempt, and folds every refusal back in -- an invalid plan as
    the validator's own words, a failed node as a Fault-shaped dict preserving
    failed/done/left (stage level) and nodes_done/nodes_left (node level) so
    the planner can keep finished work -- and the loop itself keeps it: a node
    already succeeded is skipped, never re-run or re-billed. Returns
    ``{success, goal, replans, actuations, nodes, faults}`` and closes with one
    ``task.plan_complete`` ledger note, win or lose.

    ``cancelled`` is an optional zero-arg predicate -- the operator's stop probe,
    read at the NODE BOUNDARY and nowhere else. Mid-rollout is not a boundary: a
    persistent M7 episode would tear, and a half-driven segment is not evidence
    of anything. A fired probe folds in as a terminal ``cancelled`` FAULT, so the
    loop breaks to its single exit, the world closes exactly once, and the sealed
    note says in its own faults that a human stopped it -- which is what keeps
    board.store.session_progress from tallying it as a failure. ``None`` (the
    default, and every non-runtime caller) is today's path, byte-identical."""
    planner = kernel.resolve("task.planner", consumer=CONSUMER)
    scene = kernel.resolve("graph.scene", consumer=CONSUMER)
    # Accounted even while the catalogue is hand-declared: graph.skill is where
    # the measured-skill enrichment join lands when a multi-skill choice needs it.
    # The handle is now load-bearing: its records assemble each node's governance.
    skill_graph = kernel.resolve("graph.skill", consumer=CONSUMER)
    kernel.resolve("embodiment.env", consumer=CONSUMER)
    kernel.resolve("policy.driver", consumer=CONSUMER)
    # Intra-node fan-out lives behind exec.rollouts; the plan loop never does.
    kernel.resolve("exec.rollouts", consumer=CONSUMER)

    # Same refusal as plugins/rsi/workload.py: refs travel on specs bare, so
    # Mount params would silently vanish between kernel and rollout.
    for cap in ("embodiment.env", "policy.driver"):
        params = kernel.provider_params(cap)
        if params:
            raise ValueError(
                f"{cap} was mounted with params {params}, which cannot travel "
                "on an EpisodeSpec ref; use a parameter-free provider ref")
    env_ref = kernel.provider_ref("embodiment.env")
    policy_ref = kernel.provider_ref("policy.driver")
    # The mounted sealed SkillRecords, frozen for the session (execution-mode
    # skills-root immutability audit). Read once; assembled PER NODE, since a
    # node's governance is the bundle its own task earned evidence with.
    skills = skill_graph.skills()
    catalogue = brief["catalogue"]
    oracles = brief["oracles"]
    # The card's PREDICATES table (predicate name -> "module:factory" ref),
    # threaded onto the brief from the task binding. Empty for a manipulate-only
    # mission (stack/clear_table): those nodes never touch it.
    predicates = brief.get("predicates") or {}

    plan: Mapping = {}
    nodes_out: dict[str, dict] = {}
    # The workload's OWN ledger of finished work ({id, skill, args} per done
    # node), fed to validate_plan on every replan: the untrusted planner must
    # carry each entry verbatim or the graph is refused (replan stability).
    done_specs: dict[str, dict] = {}
    faults: list[dict] = []
    actuations = 0
    replans = 0
    segment_retry_counts: dict[str, int] = {}
    retry_plan: Mapping | None = None
    success = False
    # M7 opt-in: a brief that declares ``episodic`` opens ONE persistent world
    # here and threads it through every node; ``env.close()`` fires once in the
    # finally below (win, abort, or horizon). Absent -> episode is None, today's
    # fresh-per-node path, byte-identical (every existing card omits it).
    episode: EpisodeContext | None = None
    if brief.get("episodic"):
        gov = importlib.import_module("plugins.rsi.governed")
        espec = _episode_spec(brief, seed=seed, env_ref=env_ref, policy_ref=policy_ref)
        embodiment, env, obs, driver = gov.open_episode(espec)
        episode = EpisodeContext(embodiment, env, driver, espec, obs)
    # Built ONCE: nodes_out is passed by reference, so a decide/verify node
    # reached later in the loop reads the facts earlier nodes have sealed;
    # episode (if any) is the shared persistent world every segment drives.
    ctx = NodeCtx(seed=seed, env_ref=env_ref, policy_ref=policy_ref,
                  skills=skills, nodes_out=nodes_out, predicates=predicates,
                  episode=episode, segment_specs=brief.get("segment_specs"))
    while True:
        # Pre-episode there is no obs; the empty snapshot is the honest scene
        # until M2's World bridge feeds a live one. seed rides the brief so a
        # frozen-graph planner (planner_vlm) can key its per-(task, seed) cache;
        # deterministic planners ignore it.
        brief = {**brief, "scene": scene.snapshot({}), "seed": seed,
                 "budget": max_actuations - actuations}
        # A low-level segment gets a bounded retry in the same world and on the
        # same validated graph.  A controller miss is not evidence that the task
        # decomposition changed, so do not spend a VLM call or a replan on it.
        plan = retry_plan if retry_plan is not None else planner.plan(brief)
        retry_plan = None
        ok, msg = validate_plan(
            plan, catalogue, oracles, done=tuple(done_specs.values()),
            requirements=brief.get("planning_context"))
        # Operational feed (harness.opstream; never chain evidence): the FULL
        # node graph, the moment it exists, so the execution-graph panel draws
        # the plan while the first node is still running.
        nodes = list(plan.get("nodes") or []) if ok else []
        opstream.emit("plan_built", replan=replans, valid=ok, msg=None if ok else msg,
                      goal=plan.get("goal") if isinstance(plan, Mapping) else None,
                      nodes=[{"id": n.get("id"), "skill": n.get("skill"),
                              "args": dict(n.get("args") or {})} for n in nodes],
                      verify=[dict(v) for v in (plan.get("verify") or [])] if ok else [])
        fault: dict | None = None
        if not ok:
            fault = {"kind": "invalid_plan", "msg": msg}
        else:
            # nodes_out accumulates ACROSS replans: a node that already
            # succeeded is finished work, skipped without re-running or
            # re-billing -- a model-independent floor, like max_actuations.
            for node in plan["nodes"]:
                # The one cancellation checkpoint: BEFORE dispatch, so the node
                # that already started finishes and the shared episode is never
                # torn mid-segment.
                if cancelled is not None and cancelled():
                    fault = {"kind": "cancelled", "node": node["id"],
                             "msg": "cancelled by the operator before node "
                                    f"{node['id']!r}"}
                    break
                prior = nodes_out.get(node["id"])
                if prior is not None and prior["success"]:
                    continue
                # The model-independent floor, enforced BEFORE dispatch: no
                # planner, however eloquent, can mint extra actuations.
                if actuations >= max_actuations:
                    fault = {"kind": "budget", "node": node["id"],
                             "msg": (f"max_actuations={max_actuations} reached "
                                     f"before dispatching node {node['id']!r}")}
                    break
                actuations += 1
                # ponytail: every dispatched node (any kind) counts one actuation
                # against the model-independent floor; a heterogeneous mission sets
                # max_actuations to cover its node count. Split into a separate
                # counter only if a pure-fn node exhausting the floor ever bites.
                kind = node.get("kind", "manipulate")
                opstream.emit("node_start", node=node["id"], skill=node["skill"],
                              node_kind=kind, actuation=actuations)
                opstream.emit("actuation_start", node=node["id"], actuation=actuations)
                try:
                    result = _KIND_HANDLERS[kind](node, ctx)
                except ValueError as exc:
                    # A dispatch-time grounding refusal (an arg with no scene
                    # binding, an undeclared predicate): the planner's fault,
                    # not the loop's. With a trusted table planner this was a
                    # wiring bug worth a crash; behind an untrusted VLM it is
                    # exactly the boundary's job -- fold the refusal (which
                    # names the known bindings) back into the next brief so a
                    # replan can ground itself. Non-ValueError still crashes:
                    # an env/driver bug is not the planner's to repair.
                    fault = {"kind": "node_failure", "node": node["id"],
                             "failed": [node["skill"]], "done": [], "left": [],
                             "msg": f"node {node['id']!r} refused at dispatch: {exc}"}
                    opstream.emit("node_failed", node=node["id"],
                                  failed=[node["skill"]], done=[])
                    break
                opstream.emit("actuation_end", node=node["id"], actuation=actuations,
                              success=bool(result.get("success")),
                              steps=result.get("steps"),
                              diagnostics=result.get("diagnostics", {}))
                stages = result.get("stages", [])
                done = [s["name"] for s in stages if s["success"]]
                left = [s["name"] for s in stages if not s["success"]]
                # At the 1-node loop the declared oracle IS the terminal
                # boolean the rollout already scored (terminal_label); a
                # second oracle dialect arrives with a second skill provider.
                bad_preds = [v["predicate"] for v in plan["verify"]
                             if v["after"] == node["id"] and not result["success"]]
                entry = {"success": bool(result["success"]),
                         "steps": result.get("steps"),
                         "stages": stages,
                         "governance": result["governance"]}
                # A perceive/decide node's payload (facts / chosen route) is sealed
                # so a later decide/verify node -- reading ctx.nodes_out -- routes on
                # it. Manipulate nodes carry neither; the keys stay absent.
                for extra in ("facts", "decision", "diagnostics"):
                    if extra in result:
                        entry[extra] = result[extra]
                nodes_out[node["id"]] = entry
                if entry["success"]:
                    done_specs[node["id"]] = {"id": node["id"],
                                              "skill": node["skill"],
                                              "args": dict(node["args"])}
                # A node faults when its own oracle says False, whatever its kind:
                # a manipulate node surfaces a failed terminal STAGE in `left`; a
                # perceive/decide/verify node has no stages, so its own
                # result["success"] IS the signal. `failed` names the offender for
                # the fold-back brief -- the failed stages+predicates, or the
                # predicate/skill itself when a kindful node has neither.
                if left or bad_preds or not result["success"]:
                    failed = left + bad_preds or [node["skill"]]
                    fault = {"kind": "node_failure", "node": node["id"],
                             "failed": failed, "done": done, "left": left,
                             "msg": (f"node {node['id']!r} failed: stages {left}, "
                                     f"predicates {bad_preds}; done {done}")}
                    opstream.emit("node_failed", node=node["id"],
                                  failed=left + bad_preds, done=done)
                    break
                opstream.emit("node_verified", node=node["id"], stages=done)
            if fault is not None:
                # Node-id-level attribution alongside the stage-level
                # done/left above: the planner keeps finished NODES too.
                nodes_done = [nid for nid, n in nodes_out.items() if n["success"]]
                fault["nodes_done"] = nodes_done
                fault["nodes_left"] = [n["id"] for n in plan["nodes"]
                                       if n["id"] not in nodes_done]
        if fault is None:
            success = True
            break
        faults.append(fault)
        # budget and cancelled are TERMINAL faults: replanning around either
        # would be the loop arguing with a floor the operator (or the budget)
        # already set.
        if fault["kind"] in ("budget", "cancelled"):
            break
        failed_node = fault.get("node")
        failed_spec = next(
            (n for n in plan.get("nodes", ()) if n.get("id") == failed_node), None)
        used = segment_retry_counts.get(str(failed_node), 0)
        if (fault["kind"] == "node_failure" and failed_spec is not None
                and failed_spec.get("kind", "manipulate") == "segment"
                and used < segment_retries):
            segment_retry_counts[str(failed_node)] = used + 1
            retry_plan = plan
            opstream.emit("node_retry", node=failed_node, retry=used + 1,
                          max_retries=segment_retries,
                          msg="retrying failed segment on the same validated graph")
            continue
        if replans >= max_replans:
            break
        replans += 1
        opstream.emit("replan", replan=replans, fault_kind=fault["kind"],
                      node=fault.get("node"), msg=fault.get("msg"))
        brief = {**brief, "fault": fault}

    # ONE close for the ONE persistent world, at the loop's single exit (every
    # path -- win, budget, exhausted replans -- breaks to here). No try/finally
    # around the loop: harness_runtime owns crash-safety (its docstring), and
    # wrapping would force a 100-line re-indent for a sim-env leak the GC reaps.
    # ponytail: single-exit close; add try/finally if run() ever grows a raising
    # path the runtime does not already contain.
    if episode is not None:
        episode.close()
    goal = plan.get("goal") if isinstance(plan, Mapping) else None
    out = {"success": success, "goal": goal, "replans": replans,
           "actuations": actuations, "nodes": nodes_out, "faults": faults}
    opstream.emit("plan_complete", success=success, goal=goal,
                  replans=replans, actuations=actuations)
    kernel.note("task.plan_complete", {
        "success": success, "goal": goal, "replans": replans,
        "actuations": actuations, "faults": faults,
        "nodes": {nid: {"success": n["success"],
                        "stages": [{"name": s["name"], "success": s["success"]}
                                   for s in n["stages"]],
                        "diagnostics": n.get("diagnostics", {}),
                        "governance": n["governance"]}
                  for nid, n in nodes_out.items()},
    })
    return out
