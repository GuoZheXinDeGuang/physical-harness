# VLM-generated task graphs + modular insertion — design & paper plan

> Branch worklog for `vlm-graph`. Not for main. Synthesized 2026-08-27 from four
> scout reports (StarVLA deep-read, planner map, insertion seams, benchmark survey).

## 0. Where we start from (facts, verified in code)

- Graph generation today is **100% hand-written deterministic code**: per-task
  planner classes with hard-coded chains (`mission_kitchen_thaw/planner.py:_CHAIN`,
  `planner_stack.py` literal tables). Only `clear_workspace` adapts (fault-driven
  skip sets), and even that is a pure function of (seed, fault stream).
- The VLM seat **already exists and is empty**: `harness.contracts.TaskPlanner`
  is one method `plan(brief) -> Mapping`, mounted via a `[task_bindings.X]`
  `planner` ref swap — zero kernel changes. The design docs call this out
  explicitly ("a VLM planner is a later provider behind the same seam").
- The untrusted-planner boundary is **already built**:
  `plugins/task/validate.py:validate_plan` runs after every `plan()` before any
  dispatch; a bad graph costs one replan, not a crash. Budgets are a
  model-agnostic floor on the workload side — no planner can mint actuations.
- The brief already carries everything a generator needs: task, skill catalogue
  (select-only, no inventing), oracles, scene snapshot, remaining budget, and
  the previous round's fault fold-back.

## 1. VLM graph generation — design

**New card `plugins/planner_vlm/`** exposing a `TaskPlanner` provider. The
provider prompts a VLM with (catalogue, oracles, scene snapshot, goal, fault)
and parses a strict-JSON graph `{goal, nodes[], verify[]}`. Everything invalid
is rejected by the existing validator and folds back as `invalid_plan` — the
mechanism the boundary was written for.

Four hardening items the scouts identified as real gaps (in priority order):

1. **Doctor exemption for non-deterministic planners.** `plugin_doctor`
   currently double-runs planners and diffs (determinism-required kind). Copy
   the existing `_smoke_reasoner` precedent: `available()` probe + "shape
   validated, not diffed".
2. **Replan stability rule (new validator check).** Attribution, per-node
   billing, and completed-node skipping all key on node ids across replans.
   Today that stability is a free side effect of determinism. Add to
   `validate_plan`: a replan must preserve `{id, skill, args}` of every node
   the fault reports as done. Without this, RSI first-death attribution drifts
   and finished work gets re-billed.
3. **Verify-coverage rule (new validator check).** Validator only requires
   verify non-empty; a VLM can emit 6 action nodes with 1 verify edge and the
   misses fail silently. Require every manipulate/segment node to be covered by
   a verify edge. (Repo's own lesson: audit oracles before trusting them.)
4. **Graph-as-evidence.** Plan shas already enter the sealed log. For anything
   feeding calibration or the seed ledger, the emitted graph must be
   generate-once-then-frozen per (task, seed): first emission is cached and
   sealed; replays mount the frozen graph. Otherwise "calibration blocks are
   re-runnable" silently stops being true.

What the VLM does **not** get to do (existing guardrails, keep them): invent
skills or predicates (catalogue/PREDICATES are card-authored symbols), touch
budgets, or bypass held-out discipline. Its freedom is *which declared symbols,
in what order, with what dependency structure* — that is exactly the planning
problem, and exactly what is hand-written today.

Model choice: start with the locally served Qwen (sglang, already running on
the 4090) behind an `available()` probe so doctor stays green on machines
without it. The card carries the prompt template; the kernel never sees it.

## 1b. One model seam: local or API, same interface

Decision: there is no "DeepSeek Flash vs local Qwen" choice to make — both are
OpenAI-compatible chat-completions endpoints, so the seam is a single
`ModelEndpoint` config: `{base_url, api_key, model}`. The local sglang serving
of Qwen3.8-27B-AWQ exposes `/v1/chat/completions`; DeepSeek/OpenAI/Anthropic
API endpoints are the same shape with a different `base_url`. dsh's hardwired
DeepSeek client is deleted and becomes one preset among several.

Three seats consume this one seam, and only through it:

1. **ph-station built-in agent** — its base model. The UI stays logic-free
   (red line): the endpoint config lives harness-side and is passed through.
2. **`planner_vlm` card** (§1) — graph generation.
3. **`reasoner.proposer`** — RSI candidate proposal, when a model-driven
   proposer ever mounts.

Defaults: our machines point at the local Qwen (multimodal, so the planner can
take scene images later); GPU-less users drop in an API key. An `available()`
probe (HTTP ping) keeps `plugin_doctor` green when neither is up. The seam is
a Protocol in `harness/contracts.py` like every other plug point — a provider
card `model_endpoint/` owns the client code; nothing else imports an HTTP
library.

## 2. Modular insertion — two changes, both "reuse the existing fold"

1. **Recoveries move into the manifest.** `[recoveries.<name>] ref = "…"` in
   the embodiment card, folded by `discover()` exactly like mounts/campaigns;
   `repertoire.strategies_for(card)` reads the registry. Kills the single
   remaining cross-card edit (today a new embodiment requires editing
   `plugins/rsi/repertoire.py`). This also unblocks robocasa RSI: register its
   recovery primitives in its own card.
2. **Delete per-task prereg/probe scripts** (~3400 lines across 11 files).
   `acceptance_campaign.py` already rebuilds prereg from `[claim]`; add a
   `--dry-run` smoke path and a doctor check that `[claim]` seed blocks don't
   overlap the STATUS.md ledger (check only — allocation stays a human act).

Target contributor experience, aligned with the card model (no new framework):
- new skill = write `manifest.toml` + `planner.py`, run `plugin_doctor`
  (mostly true already; the deletions above finish it);
- new sim = write one card dir (+ recoveries in its own manifest) + one launch
  entry, run doctor in the sim venv + base lane in base venv. A
  `scripts/new_sim.sh` scaffold does the mechanical wiring and prints the
  manual-discipline checklist (base-gate refresh, predicate audit).

Irreducible (stays manual, on purpose): seed allocation/burn discipline,
predicate audits, seam-conflict adjudication.

## 2b. Interface pass — make the plug points visible (the OOP complaint)

The confusion reading `plugins/` is real and diagnosable: the interfaces
exist (`runtime_checkable` Protocols, checked at mount) but they are not
*enumerable* — skills especially appear as three disconnected shapes
(strings in `CATALOGUE` dicts, providers behind manifest refs, frozen
`SkillRecord` files in skills_root) with no single `SkillLibrary` you can
point at. Fix by making `harness/contracts.py` the complete, closed list of
plug points rather than adding a class hierarchy:

1. **Every seam is a named Protocol in `contracts.py`, no exceptions.**
   Today's set (TaskPlanner, embodiment env, percept, …) plus the missing
   ones: `Skill` (name + arg schema + the episode/segment binding that today
   hides in `SKILL_SPECS`/`SEGMENT_SPECS`), `SkillLibrary` (list / get /
   install over skills_root — the RSI publish path and the execution mount
   path become two methods of one visible interface), `RecoveryStrategy`
   (§2.1), `ModelEndpoint` (§1b).
2. **A card declares what it provides in its manifest** — `[skills.*]`,
   `[recoveries.*]` sections folded by `discover()` like mounts/campaigns
   already are. Reading a card's manifest.toml then answers "what does this
   card plug in, where" without reading its Python.
3. **ARCHITECTURE.md gets a plug-point table**: one row per Protocol —
   name, method signatures, which cards implement it, how it is validated at
   mount. That table is the "explicit interface" a new contributor reads
   first; if a seam isn't in the table, it isn't a seam.

Explicitly not doing: base classes with inheritance, abstract factories, or a
plugin base framework. Protocols + manifest folds are already the mechanism;
the gap is completeness and visibility, not machinery.

## 3. Benchmark

Add **LIBERO + LIBERO-Plus** first: cheapest integration (robosuite lineage;
old pins neutralized by venv-per-sim, and we already have LIBERO scar tissue),
it is the comparison currency of VLA papers, and the Plus perturbation suite
lets us claim "frozen skills + harness orchestration hold up under the
perturbations that collapse memorized VLAs (95% → <30%)" — the community's
active pain point. Second, if time: **ManiSkill3** (pip-clean, non-MuJoCo —
proves sim-agnosticism with a third backend, and GPU-parallel rollouts feed the
RSI evidence loop). VLABench is the best narrative fit but has thin baselines;
BEHAVIOR-1K/RLBench/Meta-World/SimplerEnv: skip.

## 3b. VLA policy cards — transport and model selection

**Transport (from StarVLA code-level read): server-client is the answer to
venv hell.** StarVLA's policy-server protocol layer is 409 lines total
(msgpack+ndarray codec that refuses pickle, websocket server with
first-frame metadata handshake, blocking client) and imports zero torch —
verified in code, and MIT. Vendor it. A VLA policy runs in its own
venv/process behind the socket; harness side needs only
`websockets + msgpack + numpy`. Our own openpi checkout already proves the
pain (its LIBERO client venv is py3.8+cu113): isolation is not optional.

Three upgrades over StarVLA when we mount it as a card:

- **Handshake-vs-manifest reconciliation as a hard gate.** StarVLA repeats a
  "train/test mismatch silently kills success rate" warning banner in four
  places because it has no mount-time checkpoint. We do: the card's manifest
  params (image size, view order, chunk size, unnorm_key) are reconciled
  against the server's handshake metadata at mount — mismatch fails loud,
  and the metadata is sealed into the episode evidence.
- **Denormalization never leaves the server** (their single-source-of-truth
  norm-stats principle; the ckpt dir must contain weights + config +
  dataset_statistics or the mount fails).
- **Per-card fake-data smoke** (`--smoke`: fake obs in, action shape
  asserted out, with/without optional keys) as a doctor Tier-A item.

Driver-layer responsibilities (chunk caching, action ensembling, sticky
gripper, retarget-on-task-change) stay in the card's driver file (~200 lines,
mirrors StarVLA's per-benchmark thin adapters); none of it enters the kernel.

**Model selection (2026-08 survey):**

| Seat | Pick | Why |
|---|---|---|
| (A) frozen skill card, first | **π0.5 / openpi** | already runs locally; official websocket+msgpack server is shape-identical to our card boundary; Apache-2.0; official LIBERO ckpt ~98% |
| (A) backup | GR00T N1.7 | commercial-OK NVIDIA license, own policy server, 3B fits the 4090 |
| (A) high-score reference | MolmoAct2-LIBERO | 97-98% LIBERO head-of-field, transformers-native, half-day thin server |
| (B) RSI improvement target | **SmolVLA** | 450M — full fine-tune fits a single 24GB card in hours, so candidate generation is cheap; LIBERO scores unsaturated ⇒ a real failure axis for the evidence gates (the round-97 lesson: a zero-failure axis yields zero candidates) |

Ruled out: OpenVLA-OFT (LoRA needs >24GB; Llama-2 license taints weights),
RDT/RDT2 (dual-arm real-robot focus, no sim ckpts), WALL-OSS (no LIBERO,
observe only).

## 4. Paper framing (vs StarVLA)

StarVLA (arXiv:2604.05014) solves **research-iteration composability** (swap
backbone/action-head, run the matrix) with zero governance: no contract
validation, no plugin manifests, no privilege accounting, no tamper-evident
evidence — reproducibility is "here's the YAML". We solve **trusted
execution**. The two are orthogonal; StarVLA is our "modular but ungoverned"
contrast system.

Claims and the experiments that back them:

| Claim | Experiment |
|---|---|
| Model-generated plans can be governed, not trusted | deterministic vs VLM planner × task matrix (stack, clear_workspace, kitchen_thaw, LIBERO-Long), same harness invariants; count of bad graphs caught at the validator vs escaped |
| Frozen skills + orchestration survive perturbation | LIBERO-Plus suite with frozen SkillRecords |
| The harness is sim-agnostic | same kernel, 3 backends (robosuite / RoboCasa / LIBERO or ManiSkill3), mount-time contract validation as the mechanism |
| Sim-to-real gap is measurable, not a worry | privilege-ablation curves (already built) |
| Integration friction is quantifiable | LoC + files-touched + wall-clock per new card — the number StarVLA only asserts. §2 makes this number small first |

Borrow from StarVLA: the full cross-consistency matrix as the modularity
proof; raw-data-dict boundaries (audit our Protocol seams for leaked
model-specific preprocessing); per-card standalone smoke run as a doctor
check; effective-config sealed with the artifact (we already do this —
MountPlan.sha — say so loudly in the paper).

## 5. Order of work

1. §2.1 recoveries-in-manifest (small, unblocks robocasa RSI independently).
2. §1b model seam (`ModelEndpoint` Protocol + provider card + presets) — it
   is a dependency of everything model-driven that follows.
3. §1 planner_vlm card + the two validator rules + doctor exemption.
4. §2b interface pass (contracts completion + manifest folds + plug-point
   table) — do it while the planner_vlm work has the seams open anyway.
5. Matrix runs on stack/clear_workspace/kitchen_thaw (scratch seeds first).
6. §3b policy transport (vendor protocol layer + handshake gate + smoke) and
   the first VLA card: π0.5/openpi against LIBERO.
7. LIBERO card (§3), then LIBERO-Plus perturbation runs. (6 and 7 land
   together: the LIBERO embodiment card is the π0.5 card's test bed.)
8. SmolVLA as the RSI improvement target — fine-tune candidates through the
   evidence gates; this is the paper's layer-2 experiment.
9. §2.2 script deletion + friction measurement, §4 write-up.
