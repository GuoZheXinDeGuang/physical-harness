# agent-loop-design — a PH-native agent loop + tool-call architecture

Status: **proposal (design only, no implementation)**, 2026-08-24. Grounds a
mission→task-graph agent loop for sim **and** future real hardware in the
`graph-as-policy` paper family, and reconciles every claim with the charter
(`GOAL.md` v4.2, `ARCHITECTURE.md`, `docs/ph-station-design.md`).

Audience: an implementer with zero context. Absolute paths throughout.
Motherboard = `/home/yusenzlabpc/Desktop/physical-harness`.

**The one-line thesis.** PH already implements most of GaP's architecture — a
typed skill-node graph (`plugins/task/validate.py`), a static validator, per-node
governed dispatch (`plugins/task/workload.py`), a content-addressed skill library
with *measured* priors (`plugins/graphs/__init__.py` + sealed
`runs/*/skills/*.json`), a commit gate (`heldout_judgement_established`), and a
hard execution/evolution fence (`scripts/harness_runtime.py` MODE). The papers
mostly **validate what is built**. The deltas are small, named, and each is an
independently verifiable rung: seal at *node* granularity, *un-blind* the operator
agent with read tools, add a *mission* layer above the existing task layer, and
swap the deterministic planner table for an LLM *behind the seam that already
exists* — with the typed validator as the safety net that makes an LLM planner
admissible at all.

---

## 1. Where we are

### 1.1 The current loop (end to end)

```
operator (dsh chat LLM)
  └─ mcp__physical-harness__submit_brief({"kind":"task","task":"stack","seed":90000})
       └─ brief_drop.drop → runs/session-main/inbox/<id>.json      (atomic os.replace)

resident runtime  (scripts/harness_runtime.py, polling that inbox)
  1. claim   os.rename inbox/<id>.json → processing/               (atomic; loser gets FileNotFound)
  2. guard   _BRIEF_KEYS re-validated SERVER-SIDE; unknown key → failed/ + runtime.task_error
  3. resolve task STRING → binding {policy, planner, catalogue, oracles}
             via harness.manifest.discover() union of installed manifests at boot
  4. mount   fresh Kernel(CAPABILITIES, log=shared_log); base_profile + task policy/planner
             + shared skills root (graph.skill re-globs RSI output)
  5. run     plugins/task/workload.run  ── the Plan→Validate→Act→Verify→Replan loop
  6. seal    kernel.note("task.plan_complete", {...}) onto the ONE session chain
  7. file    processing/ → done/ (or failed/)
```

The loop body (`plugins/task/workload.py:105 run`) is already a two-layer
graph-as-policy in miniature:

- **Inter-skill controller** = the plan graph the planner emits:
  `{goal, nodes:[{id, skill, args, after}], verify:[{after, predicate}]}`. This
  is the object GaP calls the policy. `plugins/task/planner_stack.py` emits it
  today from a **hand-written table** (only `stack`, `clear_table`).
- **Static validator** = `plugins/task/validate.py:validate_plan` — type-checks
  every node against a skill `catalogue` (`{skill: {arg: python type}}`), checks
  `after` references earlier ids only (admits exactly topologically-ordered
  DAGs), and refuses an empty `verify` ("an unverified plan is vacuous"). This is
  GaP's pre-dispatch edge/type check, already here.
- **Per-node dispatch** = each node → one `governed_rollout` whose `StageSpec`
  chain is the intra-skill scorer (`stages` invariant: scorer never controller).
- **Machine-checked verify** = the embodiment's terminal oracle predicate
  (`stack_success`, `pick_success`), scored by the rollout, never self-reported.
- **Honest replan** = a failed node folds a `Fault`-shaped dict
  (`failed/done/left` at stage level, `nodes_done/nodes_left` at node level) back
  into the next brief; finished nodes are skipped, never re-billed; bounded by
  `max_replans` / `max_actuations` (model-independent floors).
- **Sealed evidence** = one `task.plan_complete` note per run on the hash-chained
  `SessionLog`; `runtime.boot` row 0 seals `{mode, skills_manifest,
  mount_plan_sha}`; `board.store.read_session` exposes `chain_ok`.

### 1.2 The limits (exactly the three the task names)

1. **Single-task briefs, no mission decomposition.** A brief names one `task`
   STRING (`_BRIEF_KEYS["task"] = {kind, task, seed, max_replans, max_actuations}`).
   There is no first-class object *above* the plan graph — no "clear the room,
   then set the table" mission that decomposes into a graph of *tasks*. The
   planner itself is a lookup table (`planner_stack.StackPlanner.plan`) keyed on
   one task string; it cannot compose or sequence tasks.

2. **The plan graph is not sealed as its own pre-dispatch artifact.**
   `validate_plan` runs *in* the loop and `task.plan_complete` seals at the
   *end*, aggregated per run. GaP's ablation (remove graph validation → 0%
   success) argues the *validated graph* should be the sealed plan artifact,
   *before* the first actuation, and evidence should be **per node** (before/after
   state) so refinement targets the failing stage — not one whole-run row.

3. **The agent is blind to the plan graph mid-flight.** Once the chat LLM drops
   `submit_brief`, it is *out of the loop*. The runtime owns Plan→Verify→Replan
   entirely; the LLM cannot see the plan graph, cannot see per-node before/after
   states, cannot make a routing call. It learns the outcome only *after the
   fact*, through read tools over the sealed chain (`session`, `store`). There is
   no read surface that returns the *live* graph or node states.

Note what is **not** a limit and must not regress: authority discipline (a brief
names no provider/plan; the runtime is the sole authority), the execution/
evolution fence (execution mounts frozen skills, campaigns rejected), and
sealed-chain integrity. The proposal is additive to these, never a loosening.

---

## 2. Proposal (grounded in the papers)

### 2.1 Mission → task-graph as a first-class object

**The object.** Introduce a layer *above* today's plan graph. A **mission** is an
NL goal + budgets; it decomposes into a **task-graph** whose nodes are *tasks*
(each today's single-task plan loop), edges are dependencies, and each task node
carries its own verify predicate. This is GaP's DAG-of-typed-nodes applied one
level up, and it reuses the exact JSON dialect and validator PH already has —
`validate_plan`'s `{nodes:[{id, skill, args, after}], verify:[...]}` shape is
task-agnostic; a task-graph is the same shape with `skill`→`task`.

**Who owns which half — the charter-critical division of labor** (GaP's
Orchestrator → Skill-Agents → Assembly → Validator, mapped onto PH's authority
model):

| GaP role | PH seam | Who / where | Authority |
|---|---|---|---|
| Orchestrator (partition NL → segments) | `task.orchestrator` **(new provider seam)** | runs **server-side** in the runtime; deterministic-table stand-in first, VLM behind the same seam later (mirrors `task.planner`) | proposes; names only tasks/skills from the installed catalogue, never providers |
| Skill Agents (synthesize local subgraphs) | `task.planner` (exists) per task node | server-side | proposes a plan graph per task |
| Assembly (wire the DAG) | the workload loop (exists) | server-side | orders by `after`; no invention |
| **Static validator** (type-check before run) | `validate_plan` (exists) | server-side, **pre-dispatch** | **refuses** a malformed graph — the load-bearing gate |
| Runtime (execute) | `harness_runtime` + `workload.run` | server-side | the **sole** executor and sole writer of `runs/` |

The LLM (whether the chat operator or a future VLM orchestrator) **proposes**;
the planner/validator **admit or refuse**; the runtime **owns execution**. This
is not new policy — it is the existing authority-laundering defense
(`harness_runtime.py:33-39`: "a brief names NO provider/mount ref … adding a task
is installing a plugin dir, never a brief") extended to the mission layer. A
mission brief names a *mission string + budgets*; server-side decomposition
resolves it against the manifest union. **An LLM never dispatches a node.**

**The graph IS the policy (GaP).** The validated task-graph — nodes, edges,
per-node skill selection, verify predicates — *is* the executable policy for the
mission; there is no separate imperative program. This is why the typed static
validator is safety-critical rather than cosmetic: it is the thing that makes an
*LLM-proposed* policy admissible, because a graph that type-checks and whose
edges connect cannot dispatch a skill that doesn't exist or wire an output to an
incompatible input. Contrast **Code as Policies** (free-form emitted code —
flexible, hard to statically verify): PH deliberately takes GaP's *typed-graph*
seal and confines free-form/third-party-library code to *inside* governed skill
nodes (which are plugins that already passed 体检/验货), never as planner output.

**Where it is sealed (GaP node-granularity + ENPIRE versioned runs).** Two new
chain notes on the existing `SessionLog`, no new hash primitive:

- `task.plan_validated` — sealed **before the first dispatch**: the full
  validated graph (mission + task-graph + each task's plan graph) *and* the
  validator verdict. This makes the validated graph the plan artifact GaP's
  ablation demands, and it is what a mid-flight `plan()` read returns.
- `task.node_complete` — one per node, carrying the node's before/after scene
  snapshot refs + stage seal + verify result. Replaces the single aggregate
  `plan_complete` with per-node evidence so refinement (evolution mode) targets
  the *failing stage*, not the whole mission. The aggregate roll-up can remain as
  a closing `mission.complete` note for cheap board reads.

Every seal already rides content-addressed identity (`mount_plan_sha`,
`prereg_sha`) — ENPIRE's "versioned, comparable sealed runs for honest ablation"
is a property PH already has; we keep the seal schema stable and version-tagged
so two planner/orchestrator versions A/B honestly.

### 2.2 Skill priors / capability boundaries inform decomposition

**The lesson (ASPiRe + SayCan + Voyager).** Prefer a library of specialized,
individually-debuggable skills plus a *context-aware arbitrator* over one
generalist; gate every candidate on *feasibility* (affordance), not just LLM
preference; and make the arbitration weights part of the sealed dispatch
evidence.

**PH already has the prior — it is the SkillRecord.** A sealed record
(`runs/stack-g1/skills/*.json`) carries exactly the arbitration inputs, as
*measured* quantities rather than annotations:

```
preconditions: {feature: "observable.finger_gap", op: "lt", threshold: 0.001, arm_after: 58, ...}
effects:       {dev_gate_vs_parent: {governed_rate: 0.658, base_rate: 0.597, p_value: 4.9e-4, n: 196}}
judgement_dev: {governed_rate: 0.658, base_rate: 0.388, p_value: 7.3e-11, ...}
heldout_judgement_established: true
```

This is precisely the ASPiRe premise ("a library of specialized behavior priors,
each from its own focused dataset") realized in PH's terms: each SkillRecord is a
prior *with a measured operating envelope*. The decomposition should read it:

- **Feasibility gate (SayCan).** A node's skill is admissible only if its
  SkillRecord `preconditions` **match the current scene** (`graph.scene`
  snapshot). The precondition predicate (`finger_gap < 0.001 after arm step 58`)
  IS the affordance check — no separate learned value function needed (that is
  the pre-LLM-RL machinery we skip; see §3). Selection score = LLM usefulness ×
  precondition-match, and **that pair is written into the sealed
  `task.plan_validated` artifact** so the dispatch decision is auditable ("skill
  X chosen: usefulness 0.9, precondition matched at conf 0.8; skill Y deselected:
  precondition `finger_gap` unmet in scene").
- **Arbitration, not hard-select (ASPiRe AWM principle only).** When several
  skills could serve a node, the planner *scores* candidates by
  `judgement_dev.governed_rate` + precondition match and records the weights;
  it does not silently hard-code one. We take the *library + arbitration*
  principle, **not** the KL-divergence blending — a measured-prior lookup +
  argmax-with-recorded-scores is enough (YAGNI; upgrade to soft blending only if
  a concurrent-skill node ever needs it).
- **Commit gate (Voyager).** A skill enters the library the planner draws from
  only after `heldout_judgement_established == true` — PH's four-suite gate
  (paired / blind-twin / held-out / ablation, `GOAL.md` v4.2) IS Voyager's
  self-verification commit gate. Already aligned; the decomposition simply must
  refuse to select an uncommitted skill.

The code already anticipates this join: `plugins/task/workload.py:122` resolves
`graph.skill` even while the catalogue is hand-declared, with the comment "where
the measured-skill enrichment join lands when a multi-skill choice needs it," and
`planner_stack.py` YAGNIs it until "a multi-skill choice needs it." The mission
layer is that need.

**Bound the context (SayPlan).** Never feed the whole scene graph or the whole
skill registry to the orchestrator/planner. Do **semantic subgraph retrieval**:
hand the planner only the task-relevant slice of `graph.scene` +
the SkillRecords whose task/preconditions are relevant. This bounds hallucination
and token cost as missions scale, and it is cheap — the scene graph is already a
small normalized `{nodes, relations}` dict and SkillRecords are keyed by task.

### 2.3 Execute + verify per node, with honest failure routing

Keep `governed_rollout` + oracle predicate as the **machine-checked** verify
(ENPIRE's EN module: "evidence must be an objective check the harness runs, never
the agent's self-report"). The `seed`-parameterized episode is the *reset*
primitive ENPIRE pairs with verify. Neither is new; both are the substrate.

The delta is to make failure routing **explicit and sealed** as one of three
outcomes per node (today the loop only does replan-until-budget-then-break):

| Outcome | Trigger | Action | Seal |
|---|---|---|---|
| **replan** | node fault, budget remains, fault is plan-repairable | fold `Fault` into brief, re-propose (exists) | `task.node_complete{outcome:"replan"}` + the fault |
| **escalate** | fault the planner cannot repair, or budget exhausted with work outstanding | **pause the mission**, surface the graph state to the operator via the chain; await a new brief | `task.escalated{node, fault, nodes_done, nodes_left}` |
| **abort** | structural fault, or operator declines | seal mission failed, keep finished-node evidence | `mission.complete{success:false, ...}` |

`escalate` is what closes the "agent blind mid-flight" loop *without* granting the
agent execution authority: the runtime seals `task.escalated`, the operator agent
reads it via `plan()` (§2.4), and responds with a *new brief* (a corrected
mission, a raised budget, a manual skip) — the agent influences the loop only
through the same governed inbox, never by driving a node. "A single task's
failure never kills the system" (`harness_runtime.py:42`) already holds; this
just names the third exit and seals it.

### 2.4 Tool surface v2 (the minimal MCP operator set)

Design rule (charter "thin faces"): the operator agent **submits and observes**;
it never plans, dispatches, grants a mode, or writes `runs/`. Everything the
agent can do routes through the resident runtime, which stays the sole authority.
This is why there is **no** `dispatch_node` / `run_skill` / `set_mode` tool — such
a tool would be authority laundering. The GaP multi-agent decomposition
(orchestrator → skill-agents → validator) lives *inside* the runtime as
providers, not as operator tools.

Existing tools kept unchanged: `list_stores`, `store`, `heldout`, `sessions`,
`session`, `runtime_status`, `ledger`, `rounds`, `list_cards` (all read-only,
`board/mcp_server.py`) and `submit_brief` (single-task/campaign fast path). New
tools (⋆) fill exactly the mission + un-blinding gap:

| Tool | Args | Returns | Authority boundary |
|---|---|---|---|
| ⋆ `submit_mission` | `{mission: str, budgets?: {max_tasks?, max_replans?, max_actuations?}}` | `{submitted, inbox}` | **Intake only.** Drops a `kind:"mission"` brief via the same atomic `brief_drop.drop`. Names **no** task/plan/provider — the runtime decomposes server-side against the manifest union. `_BRIEF_KEYS["mission"]` re-validated server-side; injected keys hard-fail to `failed/`. Mode enforced server-side (a mission that would need `actuation:real` is refused at boot, not here). |
| `submit_brief` | `{kind, task/campaign, seed, budgets...}` | `{submitted, inbox, warning?}` | Unchanged. Single-task/campaign fast path; same server-side guard. `warning` is a read-only advisory (this task's embodiment cannot mount in this session's interpreter); it never gates the drop and the key is absent when nothing is certain. |
| ⋆ `plan` | `{session: str, mission?: str}` | `{graph, nodes:[{id, state, verify, before_ref, after_ref}], outcome}` | **Read-only.** Reads `task.plan_validated` + `task.node_complete`/`task.escalated` rows off the sealed chain and returns the **live** graph + per-node state. This un-blinds the agent (limit #3) *without* control. No writes. |
| ⋆ `skills` | `{task?: str, query?: str}` | `[{digest, task, preconditions, effects, judgement_dev, heldout_judgement_established}]` | **Read-only.** SkillRecords from `graph.skill` (the priors, §2.2) so the agent can ground a mission in *real* capability before submitting and can explain a failure. Read-only; distinct from `list_cards` (which reads card *manifests*, not measured records). |

`plan` and `skills` are pure passthroughs over `board.store` / `graph.skill`,
same discipline as the existing read tools and the `board/storecli.py` byte-
equivalence test (`docs/ph-station-design.md` §3) — MCP face and CLI face are the
same function, logic stays in Python. `submit_mission` is `submit_brief` with a
`mission` `_BRIEF_KEYS` entry; the runtime's `_process` gains a `kind=="mission"`
branch that invokes the orchestrator. That is the entire operator-facing surface;
no more tools, because any additional verb would either duplicate a read or
launder authority.

### 2.5 How real hardware slots in later

The typed task-graph is **embodiment-agnostic**: nodes name *skills*, not motors;
verify names *oracle predicates*, not sensor drivers. So the same graph, the same
`validate_plan`, and the same seal schema carry to real hardware unchanged. What
changes is *behind the mounts*, and the fence is already built:

- **`actuation:real` card, refused here by construction.** `harness.manifest.discover`
  raises on any manifest declaring `actuation = "real"`
  (`harness/manifest.py:119`), and `harness_runtime` refuses to boot with one
  (`harness_runtime.py:37-39`). A real actuator is "a DIFFERENT authenticated
  runtime, never a brief (nor a card) away."
- **Same graph, certified runtime.** Real HW is a *separate* runtime binary that
  mounts the real embodiment card + the `robot-world` bundle
  (`plugins/graphs/manifest.toml`: swaps `graph.scene` →
  `world_scene_graph_provider`, the `World.snapshot()` bridge already ported from
  the retired zos world model, `docs/zos-salvage.md` §6) + certified policy
  drivers. It runs the *identical* `workload.run` loop and seals the *identical*
  chain — so a mission validated in sim is executable on hardware without
  re-authoring the policy.
- **Seal degradation is honest, not silent.** Per `GOAL.md` acceptance #5, the
  real/GPU track does not promise bit-parity; the seal records which track a
  result came from. `plan()`/`skills()` surface the same evidence for both;
  nothing in the sim runtime or the operator tools reaches a real actuator.

`docs/zos-salvage.md` is the design input for that future card; this proposal
only guarantees the *graph and seal survive the embodiment swap*, which the
embodiment-agnostic node dialect already gives for free.

---

## 3. What we explicitly do NOT adopt (and why)

The papers carry ideas that collide with the charter. Named refusals, so a future
reader does not "helpfully" add them back:

1. **ENPIRE's Evolution editing the harness/skill code *during execution*.**
   ENPIRE's E module has agents edit training/algorithm code from logs in the
   live loop. PH forbids ungoverned self-modification: execution mode mounts a
   **frozen** skills root and campaigns are **rejected** outside evolution mode
   (`GOAL.md` v4.1; `harness_runtime.py:_process`). We adopt the *capability*
   (agents editing skills/harness from sealed logs) but **quarantine it to
   evolution mode**, offline, one-directional, behind the four-suite gate. The
   execution loop never rewrites itself.

2. **LLM-judged or self-reported success (GaP/ENPIRE narration).** Success is a
   machine-run oracle predicate scored by `governed_rollout`, never the model's
   claim (ENPIRE EN, and PH's own "evidence not control flow" rule,
   `workload.py:16`). No `plan`/`skills` tool ever lets the agent assert an
   outcome.

3. **GaP simulation self-learning that writes back into the *live* plan
   mid-mission.** No online 试错 in execution (`GOAL.md` v4.2: "执行真任务只用已入库
   技能与固化配置, 不在线试错"). Rehearsal and graph refinement are evolution-mode
   campaigns; their output is a *sealed SkillRecord* a *later* execution mission
   consumes, never a hot patch to a running plan.

4. **ASPiRe's learned KL-divergence blending / AWM network.** Pre-LLM RL
   machinery. We take the *library + context-aware arbitration* principle only;
   arbitration is a measured-prior lookup (precondition match +
   `judgement_dev.governed_rate`) with recorded scores, not a trained weighting
   net. Add soft blending only if a concurrent-skill node ever demands it.

5. **Voyager's automatic curriculum proposing its *own* tasks in execution.**
   Missions come from the operator. Curriculum/skill-discovery is an
   evolution-mode concern, gated. Execution never invents its own objectives.

6. **SayCan's separate learned affordance value function.** Feasibility is the
   SkillRecord `preconditions` predicate matched against the scene graph — a
   quantity PH *already measures and seals*. A second learned model is redundant
   until preconditions prove insufficient.

7. **Code as Policies' free-form runtime code generation as planner output.** The
   plan is a *typed graph* (the `validate_plan` gate is non-negotiable per GaP's
   0%-without-validation ablation). Third-party libraries live *inside* governed
   skill-node plugins that already passed 体检/验货, never as per-mission
   synthesized code.

---

## 4. Migration rungs (smallest verifiable steps from today's loop)

Each rung is independently landable, composes with the existing R/M ladder
(`GOAL.md`), and has one runnable check. Order is by dependency; no rung loosens
authority, the mode fence, or chain integrity.

- **Rung A — seal at node granularity.** Add `task.plan_validated` (sealed
  *before* first dispatch) and `task.node_complete` (per node, before/after +
  stage seal + verify) to `workload.run`; keep a closing aggregate for board
  reads. No new authority, no new provider. *Check:* on a `clear_table` run the
  chain shows `plan_validated` **before** any `node_complete`, one
  `node_complete` per node, and `chain_ok` still true (`board.store.read_session`).
  Ties to M4 (system layer) + the ph-station 演进 panel.

- **Rung B — `plan()` + `skills()` read tools.** Pure passthroughs over the chain
  + `graph.skill`, added to `board/mcp_server.py` (and `board/storecli.py` for the
  panel face). Un-blinds the agent (limit #3). *Check:* byte-equivalence test
  (`plan()` stdout == `board.store` function == MCP tool), and the agent fetches
  the live graph *while* a task runs. Ties to `docs/ph-station-design.md` data
  plane.

- **Rung C — SkillRecord-as-prior in selection.** Wire the `graph.skill`
  enrichment join the planner already resolves (`workload.py:122`): score
  candidate skills by precondition-match against `graph.scene` +
  `judgement_dev.governed_rate`, and write the scores into `task.plan_validated`.
  Still deterministic-table-first. *Check:* on a scene where a skill's
  precondition is unmet, the planner deselects it and the reason is in the seal
  (SayCan feasibility). Ties to the `planner_stack` YAGNI note.

- **Rung D — mission → task-graph, first class.** Add `submit_mission`, the
  `mission` `_BRIEF_KEYS` entry, a `kind=="mission"` branch in `_process`, and a
  `task.orchestrator` provider (deterministic-table stand-in first, same as
  `task.planner`) that decomposes a mission into a task-graph, each node dispatched
  as today's single-task plan loop. *Check:* a two-task mission runs; one task
  fails → mission routes replan/escalate/abort honestly (§2.3); chain unbroken;
  the injected-fault soak (`GOAL.md` M4#7) stays zero-crash. Ties to M3.

- **Rung E — LLM orchestrator/planner behind the seam.** Swap the deterministic
  table for the real reasoner via the existing reasoner transport (`= R3/M1`,
  qwen38); the typed `validate_plan` + seal schema are **unchanged** — the
  validator is what makes the LLM planner admissible (GaP: refuse a graph that
  doesn't type-check). Feed it only the retrieved subgraph (SayPlan, §2.2).
  *Check:* round-25-style paired comparison (mock table vs deterministic search
  vs real model) on the fixed eval battery, no regression; prompt/response land
  in the content-addressed store.

- **Rung F — (gated, future) `actuation:real` embodiment.** Same graph, same
  validator, same seal; a *separate certified runtime* mounts the real card +
  `robot-world` bundle + certified drivers. *Check:* the sim runtime still refuses
  the real card at boot (already true, `harness/manifest.py:119`); the certified
  runtime boots only with the real card under its own MODE fence; a sim-validated
  mission graph replays on hardware without re-authoring. Ties to the `GOAL.md`
  gate rung + `docs/zos-salvage.md`.
