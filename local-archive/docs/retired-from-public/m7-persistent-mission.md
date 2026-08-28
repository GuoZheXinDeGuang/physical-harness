# M7 — Persistent-episode mission: `clear_workspace`

First-principles design. **No code lands in this doc** — §2 specs the exact base
delta, everything else is a card + measurement. Sibling of
`docs/m6-mission-design.md`; reuse its discipline verbatim.

The operator's binding constraint (verbatim): *"长程任务不是让你一次执行几个不同的
小任务，而是我需要一个多步骤在一起的任务，一个大任务里面分很多小步骤"*. M6's
`inventory_build` dispatches each of its 11 nodes as its OWN independent env episode
(a fresh `make_env` + `reset` per node — `workload.py` L129-170, L226, and
`governed.py` L235-240) and reads as several small tasks glued together. **M7 is ONE
task = ONE persistent sim episode = MANY sub-steps inside**: one env instance threaded
through the plan graph, sub-goals executed sequentially in the same world, per-step
machine-oracle verify on the LIVE state, failure → in-episode replan/retry (no reset),
the world carrying consequences forward — a dropped object stays where it fell.

---

## 0. Reality check — what robosuite can actually stage (verified this session)

Grounded in the installed package (`.venv/.../robosuite/environments/manipulation/`,
robosuite **1.5.2**) and the current harness (`plugins/embodiment_robosuite/env.py`,
`plugins/rsi/governed.py`). Design lives inside these facts.

* **PickPlace `single_object_mode=0` IS the native single-episode, multi-object,
  multi-goal, single-arm task** (`pick_place.py` L194 default `single_object_mode=0`,
  L204 `horizon=1000`, L218 `object_to_id={milk, bread, cereal, can}`). ONE reset
  spawns **all four** objects; the task is "each object into its own bin". The current
  harness only ever uses `single_object_mode=2` (`env.py` L26-28: `pickcan`/`pickmilk`
  spawn ONE object) — that is exactly the "one small task" shape M7 replaces.
* **The live per-sub-goal oracle already exists, on the persistent state.**
  `objects_in_bins` is a per-object bool array recomputed every step;
  `reward = np.sum(self.objects_in_bins)` (`pick_place.py` L303); `_check_success`
  (L737) = all four in bins; **`not_in_bin(obj_pos, bin_id)`** (L407) is a machine
  predicate over the LIVE sim pose, per object. This is the honest per-sub-goal verify
  — not a reset-based success flag, but a read of the world as it currently is.
* **Per-object live observables**: in mode 0 the obs dict carries `{Name}_pos` /
  `{Name}_quat` for **every** object (`Milk_pos`, `Bread_pos`, `Cereal_pos`,
  `Can_pos` — `pick_place.py` L619, L685), plus `robot0_eef_pos` / `robot0_gripper_qpos`.
  A dropped can's `Can_pos` reflects where it landed on the very next read — consequences
  persist for free, in the sim's own state.
* **NutAssembly mode 0** (`nut_assembly.py` L172, L182 `horizon=1000`) is the same shape
  — `objects_on_pegs` per-nut, `reward=np.sum` — a second multi-goal scene if we ever
  want a second persistent mission. No card needed tonight; noted for reuse.
* **Horizon**: robosuite terminates hard at the horizon and refuses further steps
  (`spec.py` L38-41, default 900). Four sub-goals × ~100 policy steps + retries + recovery
  splices → M7 must set `spec.horizon` well above the nominal (≈1600-2000, §4). We pass
  `horizon=spec.horizon` into `suite.make` (`env.py` L129), so it is honored.

**The load-bearing point.** The machinery to keep ONE env alive across MANY sub-steps
**already exists** — `stack_stages()` (`env.py` L67-96) scores an 8-phase grasp→place
sequence *inside one reset* via the stage overlay (`governed.py` L252-401). M7 is not new
physics; it is the SAME single-reset drive, generalized from one hard-wired object pair to
a graph of retargetable sub-goals over the four mode-0 objects.

---

## 1. THE MISSION — `clear_workspace`, one episode, ≥12 sub-steps

Narrative: **survey the cluttered workspace → decide a clearing order → for each object:
grasp, lift, transport, release into its bin, verify on the LIVE state → on a drop,
replan against the world as it now is → final integrity sweep → machine report.** ONE
PickPlace mode-0 episode; ONE `reset`; the arm never teleports and the world never rewinds.

| # | node id | kind | binds / predicate | honest oracle (machine, LIVE state) | replan edge |
|---|---|---|---|---|---|
| 1 | `survey` | **perceive** | reads the LIVE persistent obs — all 4 `{Name}_pos` + bin geometry (privilege-budgeted) | every object pose extractable & on the table | → re-survey |
| 2 | `plan-order` | **decide** | pure fn of survey facts → clearing order (nearest-bin first, deterministic) | order == machine-optimal over facts | fold fault → re-decide |
| 3 | `clear-milk` | **segment** | drive persistent env: retarget→`Milk`, grasp+lift+transport+release | segment stages pass (grasp `finger_gap`, lifted, released) | → replan #3 |
| 4 | `verify-milk` | **verify** | `not_in_bin(Milk_pos, bin) == False` on LIVE state | Milk currently resting in its bin | fail → in-episode retry #3 |
| 5 | `clear-cereal` | **segment** | retarget→`Cereal`, same drive on the SAME world | segment stages pass | → replan #5 |
| 6 | `verify-cereal` | **verify** | `not_in_bin(Cereal_pos, bin)` LIVE | in bin | fail → retry #5 |
| 7 | `clear-bread` | **segment** | retarget→`Bread`, SAME world (Milk+Cereal already placed) | segment stages pass | → replan #7 |
| 8 | `verify-bread` | **verify** | `not_in_bin(Bread_pos, bin)` LIVE | in bin | fail → retry #7 |
| 9 | `clear-can` | **segment** | retarget→`Can`, SAME world | segment stages pass | → replan #9 |
| 10 | `verify-can` | **verify** | `not_in_bin(Can_pos, bin)` LIVE | in bin | fail → retry #9 |
| 11 | `sweep` | **perceive** | re-read all 4 LIVE poses — anything still on the table? | 0 objects off-bin, OR names the stragglers | straggler → replan its clear |
| 12 | `report` | **decide** | assemble LIVE `objects_in_bins` + sealed segment outcomes → structured dict | every field cross-checks a sealed segment result AND the live oracle | — |

**Why this is genuinely big, not glued-together.** Each `clear-X` **segment** is itself
grasp→lift→transport→release (four scored stages), so the graph's 12 nodes unfold to
**~20+ sub-steps inside one world**. The verifies read the *consequence* of the prior
segment on the live sim, not a fresh preview. A failed verify does NOT re-plan a new
world — it retries the SAME object where it now sits (`Can_pos` after the drop), the
distinguishing property M6 could not have. `sweep` (#11) closes the loop: it reads the
whole persistent table one more time and can send a straggler back through its clear.

---

## 2. THE ARCHITECTURE — a generic persistent-episode context (the whole point)

Today every node — including M6's — runs in its own throwaway world: `_dispatch` builds a
fresh `EpisodeSpec` → `governed_rollout` → `make_env` → `reset` → drive to horizon →
**`env.close()`** (`governed.py` L216-420), per node. Threading persistence *for this
mission* by special-casing `clear_workspace` is exactly the "hardcode the harness for one
pipeline" the charter forbids. So the base grows a **generic persistent-episode context**
— a first-class citizen ANY mission reuses — and the mission stays pure data in a card.

### 2a. The one honest new thing: a persistent `EpisodeContext`

Everything else already exists; the ONLY missing primitive is *"keep `(env, obs, driver,
step-cursor)` alive across nodes instead of rebuilding it per node."* Concretely:

* **`run()` builds the context ONCE** when the brief declares `episodic: true` (a mission
  opt-in; absent → today's fresh-per-node path, byte-identical). It holds the live
  `env = embodiment.make_env(spec)`, the running `obs`, the retargetable `driver`, and a
  `step_cursor` (env steps consumed so far vs `spec.horizon`). It is threaded on `NodeCtx`
  (`workload.py` L182-195) beside `nodes_out`, and `env.close()` fires exactly ONCE at
  mission end (win, abort, or horizon).
* **The segment runner is `governed_rollout`'s inner loop, extracted — not duplicated.**
  `governed_rollout` today = *make env → reset → [drive N steps under a bundle, scoring
  stages, firing critic-recovery] → score terminal → close*. M7 needs the bracketed middle
  to run on an **already-open** env for a **bounded** step span and then hand the env back
  still alive. So factor that middle into `governed_segment(ctx, node, bundle, stages,
  step_budget) -> {success, stages, fires, governance, steps}` and have **both** callers
  use it: `governed_rollout` (make+reset → `governed_segment` → terminal+close, unchanged
  behavior, parity-pinned) and the new persistent `_segment` handler (no make, no close).
  This is the lazy delta — extract the shared body once, zero new physics.
  <!-- ponytail: the critic-firing + stage-scoring loop is copied nowhere; one function,
       two callers. If a second embodiment ever needs a different drive loop, split then. -->
* **Driver hand-off between sub-goals** is already a supported operation:
  `driver.retarget(target)` (`drivers.py` L132) re-aims the frozen four-phase policy at a
  new object, and resetting its phase clock (`driver.k = 0`, the field `on_handback`
  already manipulates — `drivers.py` L121, L135-150) restarts the grasp schedule for the
  next sub-goal. The segment for `clear-can` calls `observe_once` on the LIVE obs (locking
  `obj["Can_pos"]` — `drivers.py` L72-79, threaded by the node's `object` arg exactly as
  M6's `task_by_object` threads it, `workload.py` L147-154), then drives its schedule on the
  shared env. No teleport: the arm starts each sub-goal from wherever the last one left it.

### 2b. perceive / decide / verify read the LIVE state (not a reset preview)

M6's `_perceive` resets a *second* same-seed env to preview poses (`workload.py` L226-238,
honest only because M6 has no persistent world). Under an `EpisodeContext` the handlers
read `ctx.live_obs` / the live `env` handle directly:

* **`_perceive`** (survey, sweep): reads the current `{Name}_pos` off `ctx.live_obs`
  through the card predicate, still metered by `privilege_cost` (`workload.py` L226-238) —
  a live pose costs the SAME privilege a preview did. Truth = the world as it is now.
* **`_verify`** (verify-X): the card predicate evaluates `not_in_bin(obj_pos, bin_id)` on
  `ctx.live_obs` — the persistent oracle from §0. On `False` the loop's existing
  fault→replan fires (`workload.py` L398-424); **but under a context the replan re-enters
  the SAME world** (the retry re-targets the object where it fell), which is the whole M7
  distinction from M6's reset-based replan.
* **`_decide`** (plan-order, report): unchanged — a pure fn of `ctx.nodes_out` + live facts.

### 2c. In-episode replan policy (the new control question)

The existing loop already keeps finished nodes and folds a fault into the next `plan()`
(`workload.py` L326-424). Persistence adds three *policy* knobs, all model-independent
floors like `max_actuations`, all read on the LIVE state:

* **per-sub-goal retry budget** (`max_retries`, default ~2): a failed verify re-drives the
  SAME object in the SAME world; the world carrying the consequence forward is the point.
* **replan routing when a sub-goal is unrecoverable**: retries exhausted → the planner
  replans over the REMAINING sub-goals *in the same context* (skip this object, continue the
  others; `sweep` will name it a straggler). Never a reset.
* **mission-level abort criteria**: `step_cursor ≥ horizon` (hard robosuite ceiling), OR
  *K* consecutive unrecoverable sub-goals, OR a safety predicate (an object knocked off the
  table — `{Name}_pos.z` below table). Abort seals an honest partial, never a crash.

### 2d. Governance mounts per sub-goal segment (reuse `assemble_bundle`)

`assemble_bundle(skills, task)` (`workload.py` L81-126) already yields the Bundle a task's
mounted SkillRecords earned. M7 mounts it **per segment**: each `clear-X` node assembles the
bundle for its manipulation task and passes it into `governed_segment`, which runs the
identical critic-firing loop (`governed.py` L339-386) bounded to that segment's span. The
segment seals `governance = {skills: digests, bundle_sha, critic_budget, action_budget}`
exactly as `_dispatch` does today (`workload.py` L163-169) — one seal per sub-goal, not one
per mission. Zero new governance code; the bundle machinery is per-segment by construction.

### 2e. Sealing + runtime_events (the UI already consumes it)

* **`plan_complete`** (`workload.py` L431-439) already seals per-node outcomes + governance;
  extend each entry with the sub-goal's **step span** (`entered_env_step`, `exited_env_step`
  off the shared cursor) and its LIVE oracle reading, so an auditor reconstructs the whole
  persistent timeline from one note. `nodes_out` accumulation is unchanged.
* **`runtime_events`** (`harness/opstream.py`; the panel consumes `node_start`,
  `actuation_start/end`, `stage_transition`, `replan`, `plan_complete`) gains one event kind:
  `sub_goal_transition` (object, verify result, retry index) at segment cadence — the same
  no-op-outside-runtime emit the stage machine already uses (`governed.py` L275-277). The UI
  draws sub-goal progress with no new consumer.

### 2f. Base-gate + parity note (when it lands)

`workload.py` + `governed.py` (the extract) + one `env.py` `TASKS` entry (a mode-0 staging,
`{"clearall": {"env": "PickPlace", "kwargs": {"single_object_mode": 0}}}` — `object_key`
becomes per-sub-goal, supplied by the segment's `object` arg, not a fixed spec key) are
base-touching → refresh the base-gate snapshot + README **same commit** (`docs/base-gate.md`,
current 505/6/28 isolated). `base_profile` sha (**b905a51**) and all sealed store/eval shas
do NOT move (no mount changes; new tests only add count). Parity reruns pin the
fresh-per-node path (`episodic` absent) byte-identical: `stack`, `pick`, `clear_build`,
`inventory_build` all keep working untouched — **M7 is the first *user* of the context, not
its only possible one.**

---

## 3. Evidence plan

**Blocks** — alloc FRESH from the live ledger; frontier is **51500**
(`scripts/alloc_seeds.py 150 --floor 51500` → 51500-51649 free; 50850-51499 already burned
by the grasp-cube campaign, STATUS.md round 108). Reserve by APPENDING lines to STATUS.md
区块预算 in the exact format (`parse_ledger` + runtime burn-guard enforce; never edit
existing lines). **Scratch task briefs (seed <542k, outside evidence blocks) do NOT burn the
ledger** (round 107) — smoke the mission on a scratch seed first.

| role | block | n | gates? |
|---|---|---|---|
| calibration | 51500–51649 | 150 | NEVER (base rate, per-sub-goal first-death, **wall-clock**, horizon-exhaust rate) |
| dev reservoir | 51650–51949 | 300 | ordered power-scaled prefix, ONLY if calibration says go |
| held-out #1 | 51950–52149 | 200 | scored ONCE (only on a promotion) |
| reserve | 52150+ | — | future persistent-recovery surface |

**Wall-clock — MEASURE ONE EARLY (these episodes are LONG).** M5 measured single
`governed_rollout` ≈ stack 1.55s (one env-make + ~100 steps). A persistent 4-segment mission
is **one** env-make (the dominant cost — M6 §3d: env-make ≈ 0.3-0.6s) amortized over ~400-800
steps + retries → estimate **≈ 6-10s/episode single-worker**, cheaper *per sub-goal* than
M6's fresh-per-node but longer *per episode*. This is an estimate; the **first calibration
episode reports the real number** and it gates everything below. At 10 workers, 150-episode
calibration ≈ **2-4 min** if the estimate holds — but do not plan a campaign on the estimate;
plan it on the measured first episode.

**Calibration criteria (abort / go-no-go — reuse M6 §4 gates):**
1. base rate **0% or 100%** → STOP, no gate can learn. Calibration never gates.
2. base rate **≥0.90** → no residual → honest null, do NOT burn dev/held-out.
3. **horizon-exhaust** is the dominant death → raise `spec.horizon` (config, not RSI) and
   re-calibrate; a mission that dies on the clock measures the budget, not the policy.
4. per-sub-goal first-death shows chains die at an **ungoverned** segment (not a governable
   drop/recovery) → attribution pivot (M6 c3), demand that segment's campaign, do NOT evolve.
5. measured wall-clock puts cal + 1 dev gen **> ~2h** → calibration only tonight; defer the
   campaign. Never rush a long-episode burn.

**Prereg fields** (seal into ONE new store BEFORE any burn; reuse
`scripts/prereg_clear_build.py` shape): `preregistration` (target, blocks, provider triple,
`scale_dev_by_power`, `require_judgement`, `max_generations`); `chain_battery_plan`
(hypotheses: a mission success, b **per-sub-goal** first-death attribution, c persistent-
recovery evolution GATED on b); `mission_graph` (the 12-node manifest: id, kind, predicate
ref, live oracle, retry budget, replan routing) + each perceive/verify node's declared
privilege; `calibration` (the sealed probe the go/no-go was decided on).

Discipline (unchanged): held-out burns once; paired same-seed McNemar on the mission boolean;
blind-twin where a rule is claimed; **a null is a valid result**; two-态 铁律 — campaigns run
via the **script path** (`scripts/`), never through the execution runtime; sealed parity
(`base_profile` b905a51 + sealed store/eval shas) must not move.

---

## 4. Sequencing (lazy path) + honest caveats

1. **Base delta (§2a-2f)**: extract `governed_segment` from `governed_rollout` (parity-pinned),
   add the `EpisodeContext` + `episodic` opt-in in `run()`, one mode-0 `TASKS` entry, one
   `sub_goal_transition` event, per-span sealing. Tests + base-gate snapshot refresh, same
   commit. `stack`/`pick`/`clear_build`/`inventory_build` stay byte-identical (parity reruns
   prove it). **This is the deliverable** — a generic persistent-episode runner any mission uses.
2. **`clear_workspace` card** (`plugins/clear_workspace/`): manifest (`episodic: true`, mode-0
   env binding, retargetable driver) + planner (12-node graph) + `PREDICATES` (survey/sweep
   perceive, verify-X live `not_in_bin`, report decide) + `CATALOGUE`/`ORACLES`. Pure data; no
   base edit; `discover()` folds it (`manifest.py` L134).
3. **Smoke on a scratch seed** (<542k, off-ledger) through the operator runtime path — verify
   ONE persistent episode: one reset, four segments, live verifies, a real in-episode retry on
   an induced drop, one `env.close`. Archive the six UI panels to `local-archive/` (gitignore).
4. **Calibration** (51500-51649) → **measured wall-clock** + base rate + per-sub-goal first-death
   + horizon-exhaust rate → decide go/no-go (§3). Prereg BEFORE this burn.
5. Only if calibration proves a **governable** persistent-recovery residual within budget: the
   recovery-rule dev campaign + held-out ONLY on a promotion.

**Honest caveats, stated up front:**
* M7's headline deliverable is the **architecture** — a generic persistent-episode mission
  runner + a genuinely big single-episode ≥12-sub-step task with live-state verify and
  in-episode, consequence-carrying replan — NOT a guaranteed new promotion.
* **In-episode recovery is a NEW evolvable surface** — a rule that fires on a live-oracle drop
  and re-targets in the persistent world recovers a *consequence*, not a within-grasp slip; it
  is qualitatively past every M5/M6 rule (all of which fire inside one throwaway rollout). It is
  the frontier this mission opens — **flag it, do not campaign tonight** unless the measured
  wall-clock proves cal + 1 dev gen fits ≤2h (§3 gate 5).
* Multi-object clutter means real inter-object contact; the policy may knock objects while
  clearing neighbors. That is honest persistent-world difficulty (the safety-abort predicate in
  §2c catches an object off the table), not a bug to design away.
