# Long-Horizon Mission Design — `clear_build` (M5)

Operator directive: the current tasks are short single-step episodes. Compose a
long chain across MULTIPLE skills and packages, then use RSI to optimize the
configuration, the harness, and the success rate.

This is a design doc. No code lands here; §2 and §3c spec the exact (small)
harness edits. Everything is grounded in what the robosuite embodiment can
actually stage — read before proposing objects the sim cannot spawn.

---

## 0. What the sim can actually stage (grounding, verified this session)

* **Four robosuite tasks exist**, and nothing else (`plugins/embodiment_robosuite/env.py` `TASKS`):
  `lift` (Lift), `stack` (Stack: cubeA→cubeB), `pickcan` / `pickmilk`
  (PickPlace, `single_object_mode=2`). There is no bin-packing scene, no
  N-cube tower env, no clutter field. Do not design for objects the sim cannot spawn.
* **Each node runs as ONE INDEPENDENT episode.** `_dispatch` builds a fresh
  `EpisodeSpec` → a fresh `make_env` per node; `scene.snapshot({})` is empty
  until M2's World bridge (`plugins/task/workload.py` L221). So a "chain" here
  is a **symbolic composition of independent single-skill episodes** sequenced
  by the replan loop — NOT one persistent world where objects carry over.
  Design to that truth; the persistent-scene mission waits on M2.
* **SKILL_SPECS bindings that exist** (`plugins/task/workload.py` L45):
  `stack` (task=stack), `pick` (task_by_object: can→pickcan, milk→pickmilk),
  `grasp` (task=lift).
* **Report-grade skills that exist**, both `task="stack"`, `heldout_judgement_established=True`
  (verified on disk in `runs/stack-g1/skills`, `runs/place-g2/skills`):
  * `stack-g1` — 1 record, recovery `regrasp` (grasp-shaped repair).
  * `place-g2` — 2 records, recovery `replace` (place-shaped repair).

The load-bearing fact: **place is the place-shaped recovery WITHIN the stack
task**, so `place-g2`'s prereg `task` is `"stack"`, same as `stack-g1`.
`assemble_bundle(records, task="stack")` (workload L80) keys on `task==spec.task`
+ judgement, so a single **stack node pulls BOTH families' records into one
Bundle** — three rules (1 regrasp + 2 replace), `critic_budget` = sum of distinct
trigger features, `action_budget` = max recovery privilege. This is how the
mission exercises "governance bundles from BOTH skill families" without any new
promotion.

---

## 1. The mission: `clear_build` — a 4-node graph

Narrative: **arm bring-up, clear two clutter objects, then build the tower.**

| node | skill (binding) | embodiment task | governance |
|---|---|---|---|
| `n1` grasp cube | `grasp` (task=lift) | Lift | ungoverned (no lift skill; geo-grasp converged 0 rules, round 97) |
| `n2` pick can | `pick` (task_by_object) | PickPlace can | ungoverned |
| `n3` pick milk | `pick` (task_by_object) | PickPlace milk | ungoverned, `after=[n2]` |
| `n4` stack cubeA→cubeB | `stack` (task=stack) | Stack | **regrasp + replace (both families)**, `after=[n3]` |

Requirements met: **4 nodes**; **3 distinct SKILL_SPECS bindings** (grasp / pick /
stack); **both governance families on `n4`**; **replan routing across nodes** —
`workload.run`'s own `while` preserves finished work (a succeeded node is
skipped, never re-run or re-billed, L245) and folds each fault back into the next
brief with `nodes_done`/`nodes_left`.

**Node-order caveat (drives the power calc).** Governance only touches `n4`, and
`n4` is reached only if `n1..n3` eventually succeed. So the governed−baseline
chain delta = (n4 delta) × `q_pre`, where `q_pre = P(n1..n3 all succeed)`.
Ordering stack LAST maximizes the narrative but dilutes measurable power; the
calibration block measures `q_pre` and the go/no-go gate (§4) is on it. An
alternative order (stack first) removes dilution but breaks the clear-then-build
story — a deliberate trade, decided by the calibration number, not by taste.

---

## 2. Minimal harness to stage it — the "优化 harness 框架" half

Three small pieces. **No new evolvable machinery for Phase 1** — it composes
existing parts.

* **E1 — planner** (`plugins/task/planner_stack.py`, ~18 lines): add a
  `clear_build` branch emitting the 4-node plan; `CATALOGUE +=
  {"grasp": {"object": str}}`; `ORACLES += ("lifted",)`. The workload's verify
  handling is **already oracle-agnostic** — it scores `result["success"]` per
  node regardless of predicate name (workload L269), so mixed predicates
  (`lifted` / `pick_success` / `stack_success`) need only be admitted by
  `validate_plan`. No workload change.
* **E2 — governance mount**: a `graph.skill` root that surfaces BOTH
  `stack-g1` and `place-g2` skill records (copy/symlink the three JSONs under one
  `skills/` root), so `assemble_bundle` sees all three. Reuses the
  `place_campaign._mount` pattern verbatim; a mount-plan edit, not code.
* **E3 — chain battery** (`scripts/chain_battery.py`, new): run `workload.run`
  over a seed block under 3 mount configs {none, stack-g1 only, stack-g1+place-g2},
  aggregating chain-success + a per-node first-death histogram read straight from
  the `task.plan_complete` note (per-node success + stage done/left +
  `fault.nodes_done`/`nodes_left` are **already emitted** — no workload change).
  Composes `eval_battery` + `workload.run`; not deep machinery.

---

## 3. What RSI optimizes — measurable claims

### (a) Chain success rate, end-to-end — 3 arms
* **baseline** = no skills mounted (`n4` ungoverned).
* **governed** = stack-g1 + place-g2 mounted (`n4` governed by existing rules).
* **evolved** = + any rule a dev campaign promotes on this mission (Phase 2).

Claim shape: chain rate = ∏ node rates; `delta(governed − baseline) =
(n4 delta) × q_pre`. Report all three arms on ≥2 held-out blocks, **paired
same-seed McNemar on the chain boolean**. Because `n1..n3` are ungoverned and
deterministic in seed, a same-seed pair differs between arms IFF all pre-nodes
succeed AND `n4` flips — which is exactly the diluted discordance §4 sizes for.

### (b) Per-node / per-stage attribution — where chains die
`task.plan_complete` already carries per-node success, stage-level `done`/`left`,
and `fault.nodes_done`/`nodes_left`. Aggregate over the block → a **first-death
histogram** (which node kills the chain) plus `stage_attribution` inside `n4`
(grasp-stage vs place-stage residual, `campaign.stage_attribution`). No
extension. This answers the load-bearing question BEFORE any evolution: *are
chains even dying at the node governance touches?*

### (c) A NEW evolvable surface this mission opens
* **Surface A — replan-policy params** (`max_replans`, `max_actuations`):
  reachable NOW as a pure config sweep (see (d)); no evolvable machinery.
* **Surface B — inter-node recovery rule (fires BETWEEN nodes)**: genuinely new.
  Every existing `Rule` fires INSIDE one `governed_rollout` (intra-node, keyed on
  a per-step `FeatureView`). An inter-node rule fires on a **node-level fault**
  and changes routing. **Not reachable with the existing campaign machinery** —
  its unit of evidence is a *sequence of node faults* from one `workload.run` +
  the *chain* boolean, not a single-episode step trace. It is **gated on
  Phase-1 attribution** showing chains die where an inter-node route would help
  (the round-21 ServoDescend lesson, verbatim in `STATUS.md` "不要重做": verify the
  problem exists before building the primitive).

  **Extension spec (Phase 2, ~80–120 LOC + tests):**
  1. Generalize `workload.run`'s route decision — the already-localized
     break-vs-replan block (L291–300) — behind an optional `inter_node_bundle`:
     at a fault, an `InterNodeRule` may override the route ∈ {`extra_replan`,
     `alt_recovery` (swap `n4`'s bundle to replace-only / regrasp-only),
     `reorder` (defer the failed node)}.
  2. `InterNodeRule` = trigger over node-level features
     {`fault.kind`, `fault.node`, failed-stage names, `len(nodes_done)`} → action.
     Deterministic search is the SAME shape as `search_triggers`, over a
     node-level feature space instead of a step-level one.
  3. `chain_gate._run(seed, inter_node_bundle) → chain boolean`: a thin adapter
     so `plugins/rsi/gate.py`'s `paired_gate` + `blind_twin` + power sizing +
     held-out-once apply **verbatim**. The gate only needs `(unit, bundle) → bool`;
     `chain_rollout` provides exactly that shape. Everything downstream is reused.

### (d) Configuration optimization as MEASURED sweeps
Grid `max_replans ∈ {0,1,2,3}` × `max_actuations ∈ {4,5,6}` on the dev block;
report chain rate + wall-clock per cell; pick the knee (Pareto: rate vs episode
budget). Pure measurement via `workload.run` kwargs — no new code, no hand-tuning.
This is the honest "优化配置".

---

## 4. Evidence plan

**Method** = `local-archive/docs/round18-power.md`: exact McNemar enumeration
(not normal approximation), per-generation sizing from the PREVIOUS generation's
residual rate (`scale_dev_by_power=True`), seeds drawn as an ordered prefix of a
**preregistered reservoir** — the candidate's own result never sets its own
sample size (not optional stopping).

**Dilution → n.** Paired discordance on the CHAIN = `q_pre × (n4 discordance)`.
`n4` discordance ≈ 0.12 (place headline: 74 discordant / 600). At `q_pre ≈ 0.6`,
chain discordance ≈ 0.07. Exact-McNemar 80% power needs **20 discordant pairs**
at fix_share 0.8 → n ≈ 20/0.07 ≈ **285**; the fix_share-0.7 case needs 49 pairs
→ ≈ 700. Per-generation power-scaling is therefore essential, not optional.

**Blocks** (allocated from the live `STATUS.md` ledger via `scripts/alloc_seeds.py`;
frontier 48900; all disjoint; reserve by appending lines in the exact 区块预算
format — the runtime burn-guard enforces):

| role | block | n | gates? |
|---|---|---|---|
| calibration | 48900–49049 | 150 | NEVER (measures per-node rate, `q_pre`, chain base rate) |
| dev reservoir | 49050–49349 | 300 | ordered, power-scaled prefix per generation |
| held-out #1 | 49350–49549 | 200 | scored ONCE |
| held-out #2 | 49550–49749 | 200 | scored ONCE |
| held-out #3 (headline repro) | 49750–49949 | 200 | scored ONCE |
| reserve (Phase-2 inter-node) | 50000+ | — | — |

`n` per block from the power method: held-out **n=200** (the ≥200 transfer-CI
rule; n=60 forbidden, `STATUS.md`); ≥2 held-out is the floor, #3 makes the
two/three-block headline repro (block noise ±7pp, round 32). Dev sized per-gen by
power (≈150–300 prefix). Held-out burns once each.

**Wall-clock** (measured single-worker THIS session, `governed_rollout`, seed 41000):
grasp/lift 0.57s + pickcan 0.67s + pickmilk 0.62s + stack 1.55s = **3.4s/chain**,
+~1 node-time per replan. At 10 workers, throughput ≈ 212 ep/min ÷ ~5 ep/chain ≈
**42 chains/min**.
* Phase-1 battery: 3 arms × (150 cal + 3×200 held) ≈ 2 250 chain-runs ≈ **54 min**.
* Config sweep: 12 cells × 300 dev ≈ 3 600 chain-runs ≈ **86 min** (subsample cells to trim).
* Phase-2 dev campaign (paired + blind per gen, ~2 gens): ≈ 2 000–3 000 chain-runs ≈ **1–1.5 h**.

All GPU-free, headless, script path (两态铁律 — never through the execution runtime).

**Abort / go-no-go criteria:**
1. Calibration `q_pre < 0.30` → chains rarely reach `n4`; the stack delta is
   undetectable through dilution → ABORT or reorder stack earlier BEFORE burning dev.
2. Calibration chain base rate ≥ 0.90 → no residual to evolve (the geometric-grasp
   100% ceiling, round 97) → report an honest null, do NOT burn dev/held-out.
3. Attribution shows chains die at an UNGOVERNED node (pick/grasp), not `n4` →
   stack governance cannot move the headline; pivot the claim to attribution +
   demand a pick/grasp campaign; do NOT run Phase-2 inter-node (its problem is unproven).
4. Dev gen-1 candidate fails the paired gate (`fixed<3` or `p≥α` or ≤ its blind
   twin) → stop; no fishing. A null is a valid result.
5. held-out `p ≈ 0.09` → do NOT enlarge n to chase significance (p-hacking,
   `STATUS.md`); report inconclusive on that block.

---

## 5. Preregistration & discipline

* Seal a `Preregistration` in the store BEFORE any burn — `task="stack"` for the
  `n4` governed evidence; the chain battery preregisters its 3-arm mount configs
  + blocks alongside.
* Reserve the blocks in `STATUS.md` 区块预算 by APPENDING new lines in the exact
  existing format (machine-read by `parse_ledger` + the runtime burn-guard —
  never edit existing lines).
* Held-out burns once; paired same-seed McNemar gates; blind-twin where a rule is
  claimed; ablation curve at promotion; **a null result is a valid result**.

## 6. Sequencing (lazy path)

1. **E1 + E2 + E3**, then the **calibration block** → decide go/no-go (§4.1–4.2).
2. If go: **Phase-1 battery** (3 arms, held-out) → claims (a) + (b) with rules
   that ALREADY EXIST, plus the **config sweep** (d). This is the whole first
   headline and needs zero evolvable extension.
3. Only if attribution proves chains die at `n4` in a way an inter-node route
   fixes: **Phase-2 extension** (§3c) + a dev campaign on the reserve block.

Phase 1 is a measurement harness over existing pieces; Phase 2 is the one real
harness extension, spec'd precisely and gated on evidence — not built on spec.
