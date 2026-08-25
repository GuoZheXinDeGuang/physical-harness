# M6 — Heterogeneous ≥10-step mission: `inventory_build`

First-principles design. **No code lands in this doc** — §2 specs the exact (small)
base delta, everything else is a card + measurement. Sibling of
`docs/long-horizon-design.md` (M5 `clear_build`); reuse its discipline verbatim.

The operator's binding constraints (charter `docs/overnight-goal-20260825.md`):
≥10 steps; NOT manipulation-only; complex process graph; per-step execution with
replan-on-failure; RSI creates/composes small skills; **never hardcode the harness
for one pipeline — generic machinery only**.

---

## 0. Reality check: what robosuite can actually stage (verified this session)

Grounded in `plugins/embodiment_robosuite/env.py` and `plugins/task/workload.py`.
Design lives inside these facts; do not invent scenes the sim cannot spawn.

* **Four tasks, nothing else** (`env.py` L22 `TASKS`): `lift` (Lift, cube),
  `stack` (Stack, cubeA→cubeB), `pickcan` / `pickmilk` (PickPlace,
  `single_object_mode=2`). No clutter field, no N-object bin, no packing scene.
* **Cameras exist but are a separate factory** (`env.py` L136 `camera_make_env`):
  offscreen agentview RGB + normalized depth, obs gains `agentview_image` /
  `agentview_depth`, every other key unchanged. Already used by the geometric-grasp
  card. A perceive node *may* read it, but does not need to.
* **Observation channels**: privileged ground-truth poses (`cube_pos`,
  `cubeA_pos`, `cubeB_pos`, `Can_pos`, `Milk_pos`), observable `robot0_gripper_qpos`.
  The privilege split is structural (`harness/features.py`) — a perceive node that
  reads a pose pays privilege budget through `privilege_cost`, same accounting the
  manipulation critics pay.
* **THE load-bearing truth — no persistent world.** Each node runs as ONE
  independent episode: `_dispatch` builds a fresh `EpisodeSpec` → a fresh
  `make_env` per node; `scene.snapshot({})` is empty until M2's World bridge
  (`workload.py` L221-224). Objects do **not** carry over between nodes. A "chain"
  is a **symbolic composition of independent, seed-deterministic single-skill
  episodes** sequenced by the replan loop — NOT one mutable world.

**Consequence for a heterogeneous mission.** We cannot honestly stage "survey a
persistent clutter field → mutate it → the world remembers." An honest perceive /
decide / verify node in *this* sim reads only:
1. the **seed-deterministic env** a manipulation node will run (a perceive node
   resets the same-seed task env and reads its poses — an honest preview of the
   scene the arm acts in), or
2. **prior nodes' sealed results** (a verify node reads a manipulation node's
   privileged stage residual; a decide node routes on accumulated facts).

Neither needs a mutable persistent world. Both exist NOW. This is the whole design
envelope — M6 is heterogeneous by adding perceive/decide/verify **node kinds** over
the four staged tasks, not by pretending the world persists.

---

## 1. The mission: `inventory_build` — an 11-node heterogeneous graph

Narrative: **survey the workspace → classify what's present → decide a build order
from geometry → grasp/build/clear with verify gates between → final integrity
check → machine report.** 11 nodes; **4 node KINDS** (perceive, decide, verify are
the 3 beyond manipulate); replan edges on every verify failure.

| # | node id | kind | binds / predicate | honest oracle (machine, never LLM) | replan edge |
|---|---|---|---|---|---|
| 1 | `survey` | **perceive** | reads same-seed Lift+Stack+PickPlace poses via `OnboardPercept.object_estimate` (privilege-budgeted) | every expected object pose extractable & within table bounds | → re-survey |
| 2 | `classify` | **perceive** | geometry → {cube, can, milk} type map | classification == the known object→type map for that task | → re-classify |
| 3 | `plan-order` | **decide** | pure fn of survey facts → build order | chosen order == machine-optimal order over facts (deterministic) | fold fault → re-decide |
| 4 | `grasp-cube` | manipulate | `grasp` (task=lift) | `lifted` (terminal label) | → replan |
| 5 | `verify-grasp` | **verify** | predicate over #4 sealed result | #4 grasp-stage residual passes | fail → replan #4 |
| 6 | `build-stack` | manipulate | `stack` (task=stack) — **governed node** | `stack_success` | → replan |
| 7 | `verify-integrity` | **verify** | predicate over #6 sealed `stack_xy/z_residual` | residual < authored tol | fail → replan #6 |
| 8 | `pick-can` | manipulate | `pick` (can→pickcan) | `pick_success` | → replan |
| 9 | `pick-milk` | manipulate | `pick` (milk→pickmilk), `after=[pick-can]` | `pick_success` | → replan |
| 10 | `verify-cleared` | **verify** | predicate over #8,#9 sealed results | both picks succeeded | fail → replan |
| 11 | `report` | **decide** | assemble facts+results → structured dict | every report field cross-checks a sealed node result | — |

Requirements met: **11 nodes**; **3 kinds beyond manipulate** (perceive ×2,
decide ×2, verify ×3); **complex graph** (a decide node routes the order the later
manipulation nodes execute; verify nodes gate and reroute); **replan-on-failure** —
a verify-node failure is a `node_failure` fault folded into the next brief by the
**existing** loop (`workload.py` L275-300), so replan-on-verify is free, no new
routing code. Governance lands on `build-stack` (both families, exactly M5's mount).

**Node order.** Governed `build-stack` is reached only after grasp; to avoid `q_pre`
dilution (M5 §1 lesson, calibration-decided) the calibration block measures per-kind
first-death and the go/no-go (§4) reorders if the governed node isn't where chains
die. Same lever as M5 `planner@v2`, decided on the number not on the narrative.

---

## 2. THE ARCHITECTURE — a generic node-kind registry (the whole point)

Today **every** node is a manipulation rollout: `_dispatch` → `_governed_rollout`
(`workload.py` L128-169). The base has exactly one node kind, welded in. Making M6
heterogeneous by special-casing perceive/decide/verify *for this mission* is exactly
the "hardcode the harness for one pipeline" the charter forbids. So the base grows
**generic node kinds** — first-class citizens ANY mission reuses — and the mission
stays pure data in a card.

### 2a. The registry (base delta, ~70–100 LOC + tests, lands ONCE)

A node gains an **optional** `kind` field, default `"manipulate"`. A base table maps
kind → handler `(node, ctx) → {success, ...}`:

```
NODE_KINDS = {
    "manipulate": _dispatch,   # TODAY's handler, unchanged, byte-identical
    "perceive":   _perceive,   # new
    "decide":     _decide,     # new
    "verify":     _verify,     # new
}
```

`ctx` bundles what a handler may read: `{seed, env_ref, policy_ref, percept_ref,
skills, nodes_out}` — `nodes_out` is the accumulated prior-node results the loop
already keeps (`workload.py` L215, L243-274). The per-node loop routes on
`node.get("kind", "manipulate")` → handler, passing `ctx`. `_dispatch` (manipulate)
is unchanged; it just ignores `ctx.nodes_out`.

**Honest oracle per kind — the hard line.** Every handler returns `{"success":
bool}` scored by the SAME `result["success"]` the loop already reads (`workload.py`
L269-271), and the truth is always a **machine predicate**, never a model claim:
* **`_perceive`**: resolves the node's card-authored predicate by ref (the `stages`
  boundary dodge, `load_provider("module:attr")` — no cross-plugin import), which
  resets the same-seed task env (`load_provider(env_ref).make_env(spec).reset()`)
  and reads poses through `OnboardPercept` at the spec's `percept_noise`. It reads
  privileged pose ONLY through `privilege_cost` accounting — the node seals its
  privilege budget into `result["governance"]` exactly as manipulate seals its
  bundle budgets (`workload.py` L163-168). Cost: one env reset (~env-make time), no
  policy rollout.
* **`_decide`**: resolves a **pure** predicate over `ctx.nodes_out` (prior facts +
  faults) → `{success, decision}`. Zero privilege (reads only sealed prior facts).
  Deterministic in seed → byte-identical replay.
* **`_verify`**: resolves a predicate over a prior node's sealed result (privileged
  stage residual thresholded, or a boolean AND of prior successes) → `{success}`.
  Truth = machine predicate over sealed residuals. On `False`, the loop's existing
  fault→replan fires — no new routing.

### 2b. Why this is DATA-driven and card-declared (not hardcoded)

* **Base owns the kinds** (generic handlers); **the card owns the predicates**. A
  perceive/decide/verify node's `skill` slot names its predicate; the card's
  `CATALOGUE` types its args; `validate_plan` **already** checks skill∈catalogue +
  arg types (`validate.py` L45-59). The card publishes a `PREDICATES` table
  (predicate name → `module:attr` ref) and declares it in the manifest task-binding
  beside `catalogue`/`oracles` — imported by ref, folded by `discover()`
  (`manifest.py` L134), no base edit, base_profile sha (b905a51) unmoved.
* **The ONLY validate change**: admit the optional `kind` key
  (`_NODE_KEYS | {"kind"}`) and check `kind ∈ NODE_KINDS`. Existing cards carry no
  `kind` → default manipulate → byte-identical plans → sealed card shas unmoved.
* **Doctor-checkable** (`plugin_doctor` Tier-A, `scripts/plugin_doctor.py` L221-238,
  base-clean, no sim): for every node kind referenced, assert it's registered; for
  every non-manipulate node, assert its predicate ref loads and its args type against
  the catalogue. Static, sim-free — symmetric with the existing binding check.

Net base delta: 3 handlers + a 4-entry table + one `validate` clause + one doctor
clause. Missions are pure cards forever after. **This is the deliverable** — M6 is
the first *user* of it; `clear_build` and `stack` keep working untouched.

### 2c. Base-gate note (when it lands)

`workload.py` + `validate.py` + `plugin_doctor.py` are base-touching → refresh the
base-gate snapshot + README **same commit** (`docs/base-gate.md`). The `base_profile`
sha and all sealed store/eval-battery shas do NOT move (no mount changes; new tests
only add count). Parity reruns pin the manipulate path byte-identical.

---

## 3. RSI plan — evolvable surfaces, attribution, campaign, wall-clock

### 3a. Evolvable surfaces

* **Manipulation stages** (grasp / place inside `build-stack`): the M5 surface.
  Honest expectation: **saturated** — geometric grasp hit the 100% ceiling
  (round 97) and place-g2's three rules ate the stack residual (round 101 gen-1
  null). Re-running that campaign on M6 most likely repros the null. Stated up front
  so we don't fish.
* **The genuinely NEW surface M6 opens — the `decide` node.** A decide rule is a
  trigger over `{survey facts, prior faults}` → a route (build order / recovery
  family / defer). This is the inter-node surface M5 §3c gated but could not reach
  (every M5 rule fires INSIDE one rollout); decide nodes make it first-class. But a
  decide node over **correct** deterministic facts is 100% → zero residual → null.
  Residual appears only when facts are **ambiguous** — and the honest lever for that
  already exists: perceive nodes read through `OnboardPercept` at a `percept_noise`
  operating point (`workload.py` L52-56), so a higher sensor_sd yields noisy facts a
  decide node can route wrong on. **The evolvable campaign target is therefore the
  decide node under perceive-noise** — reachable NOW, no new noise machinery.

### 3b. What the chain battery must attribute

Extend `scripts/chain_battery.py`'s first-death histogram (already per-node,
L71-77) to bucket **per node KIND**. This answers the load-bearing question before
any burn: *do chains die at a governable node, and of what kind?*
* die at a verify node with a passing-but-strict tol → the tol is the lever (config, not RSI);
* die at the decide node under noise → the decide rule is the RSI target;
* die at `build-stack` residual → M5 stack finding (likely saturated null);
* die at an ungoverned grasp/pick → pivot to attribution (M5 §4.3 c3), demand that campaign, do NOT evolve.

### 3c. The weakest-stage campaign

Reuse `plugins/rsi/gate.py` `paired_gate` + `blind_twin` + power sizing + held-out-once
**verbatim** — the gate only needs `(unit, bundle) → chain bool`, which `chain_rollout`
already provides (M5 §3c). A decide-rule campaign is the SAME `search_triggers` shape
over a node-level feature space (survey facts + fault kind) instead of a step-level one.
Dev-first; a promotion must clear `fixed≥3 ∧ p<α ∧ > blind twin` or it's a **null (valid
result)**. Held-out burns ONLY on a promotion.

### 3d. Wall-clock (bounded ≤2h of episodes tonight)

Per-node cost (M5 measured, `governed_rollout` single-worker): grasp 0.57s +
pickcan 0.67s + pickmilk 0.62s + stack 1.55s = 3.4s manipulate. Perceive = one env
reset ≈ 0.3–0.6s ×2. Decide/verify = pure fn ≈ sub-ms ×5. → **≈ 5s/chain**
single-worker; at 10 workers ≈ **~30–40 chains/min** (env-make bound).
* Calibration 150 chains ≈ **4–6 min**.
* One dev generation (power prefix ≈200–267, paired + blind ≈ 2×) ≈ 400–530
  chain-runs ≈ **15–25 min**.
* Total dev-first budget (cal + ≤2 gens) ≈ **40–55 min** — well under 2h. Held-out
  (200 ×2) only if a promotion clears the gate ≈ +12 min. All GPU-free, headless,
  **script path** (两态铁律 — never through the execution runtime).

---

## 4. Evidence plan

**Blocks** — alloc FRESH from the live ledger; frontier is **50000**
(`scripts/alloc_seeds.py 150 --floor 50000` → 50000-50149 free; M5's "reserve
50000+" is nominal, never burned). Reserve by APPENDING lines to STATUS.md 区块预算
in the exact format (`parse_ledger` + runtime burn-guard enforce; never edit
existing lines).

| role | block | n | gates? |
|---|---|---|---|
| calibration | 50000–50149 | 150 | NEVER (per-kind first-death, base rate, q_pre) |
| dev reservoir | 50150–50449 | 300 | ordered power-scaled prefix per generation |
| held-out #1 | 50450–50649 | 200 | scored ONCE (only on a promotion) |
| held-out #2 | 50650–50849 | 200 | scored ONCE (headline repro) |
| reserve | 50850+ | — | — |

**Calibration criteria (abort / go-no-go — reuse M5 §4 gates):**
1. base rate **0% or 100%** → STOP, no gate can learn (c4). Calibration never gates.
2. base rate **≥0.90** → no residual → honest null, do NOT burn dev/held-out (c2).
3. `q_pre < 0.30` at the governed node → reorder it earlier (the `planner@v2`
   lever) or abort BEFORE dev (c1).
4. per-kind attribution shows chains die at an **ungoverned/deterministic** node
   (not the campaign target kind) → pivot to attribution, demand that node's
   campaign, do NOT run the decide-rule evolution on an unproven problem (c3).
5. gen-1 candidate fails the paired gate (`fixed<3 ∨ p≥α ∨ ≤ blind twin`) → stop,
   report null. Never enlarge held-out to chase p (p-hacking, STATUS.md).

**Prereg fields** (seal into ONE new store BEFORE any burn; reuse
`scripts/prereg_clear_build.py` shape):
* `preregistration` — `Preregistration` for the campaign target (dev 50150–50449,
  heldout 50450–50649, `scale_dev_by_power=True`, `require_judgement=True`,
  `max_generations=2`, provider triple stamped from the mount).
* `chain_battery_plan` — hypotheses (a chain success, b **per-kind** attribution,
  c decide-rule evolution GATED on b), the arms + skill lineage, all blocks + roles +
  n, the paired same-seed McNemar gate on the chain boolean, the §4 go/no-go.
* `node_kinds` — the 11-node graph manifest (id, kind, predicate ref, oracle,
  replan edge) + each perceive node's declared privilege budget, so an auditor
  re-derives the exact graph and its privilege cost.
* `calibration` — the sealed probe read the go/no-go was decided on.

Discipline (unchanged): held-out burns once; paired same-seed McNemar gates;
blind-twin where a rule is claimed; ablation at promotion; **a null is a valid
result**; sealed parity (base_profile b905a51 + sealed store/eval shas) must not move.

---

## 5. Sequencing (lazy path) + honest caveats

1. **Base delta (§2a/2b)** + tests + doctor clause + base-gate snapshot refresh
   (same commit). This is the real, one-time, generic extension. `clear_build`/`stack`
   stay byte-identical (parity reruns prove it).
2. **`inventory_build` card** (`plugins/inventory_build/`): manifest + planner
   (11-node graph) + `PREDICATES` (perceive/decide/verify predicates) + `CATALOGUE`/
   `ORACLES`. Pure data; no base edit; `discover()` folds it.
3. **Calibration** (50000–50149) → per-kind first-death → decide go/no-go (§4).
4. If go: **chain battery** (baseline vs governed) paired McNemar → claim (a)+(b)
   with existing rules, zero new promotion needed — same headline shape as M5.
5. Only if attribution proves a **governable** decide/verify residual under noise:
   the decide-rule dev campaign (§3c) + held-out ONLY on a promotion.

**Honest caveats, stated up front so nobody is surprised at 07:00:**
* M6's headline deliverable is the **architecture** (generic node kinds) +
  heterogeneous ≥10-node execution with replan — NOT a guaranteed new promotion.
* The manipulation surfaces are likely saturated (M5 nulls); the new decide surface
  only yields residual under injected perceive-noise, and may still null. That's a
  valid result — the machinery is the win, and it's reusable by every future mission.
* No persistent world (M2 unlanded) — the mission is an honest symbolic composition,
  not a mutable-world survey. When M2 lands, the SAME node kinds read a live
  `scene.snapshot` instead of a same-seed reset — zero card rewrite.
