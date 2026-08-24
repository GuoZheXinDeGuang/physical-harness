# The Skill Vault — a deterministic wiki over sealed skills & packages

> 操作者原话（design to this verbatim）："我还需要一个 skill graph 或者说 skill library……
> skill 和 package 要定义一种固定的 json 描述格式，中间要包含很多信息，要能像一个知识库一样
> 连接起来，我们需要有一个 skill vault，skill 和 package 之间需要有 relation……有点像 llm wiki
> 那样……skill 需要是一个网络，并且 agent 需要能理解这个网络，这个网络要 follow 一定的规则去
> 制定去扩张，技能和 package 之间需要有互相包含引用的关系，并且都需要 follow some sets of rules
> 这样 agents 才可以很好地理解，后期的 skill 也会更加 scalable，rsi 设计出来的新技能也会更加好扩张。"

**One sentence.** The vault is a **deterministic fold** that reads the shapes the
harness *already seals* — SkillRecords, Preregistrations, manifest cards, chain
seals, the capability catalog — and re-presents them as a **typed, backlinked
wiki graph**: every node a page, every edge a rule with a named derivation, no
new statistics, no second authority. It is `board/cards.py` and `board/store.py`
joined into one graph and given a wiki face.

**What it is NOT** (charter GOAL.md v4.2, and ponytail): not a database, not a
writer, not a curation tool, not a place where a human types a "truth" that can
disagree with `runs/`. It writes nothing to `runs/`. It invents no number. If the
sealed evidence says X, the vault says X or is silent — never Y.

---

## 0. Why a fold and not a store

Everything the operator wants **already exists as sealed, content-addressed data**.
The only thing missing is the *join* and the *reading order*. Building a second
store would mean a second thing to keep in sync with `runs/` — the exact
"文件对而行为错" failure the memory note `verify-against-runtime-not-files`
warns about. So the vault owns **zero durable state**. It is a pure function:

```
vault(runs/, plugins/) -> { schema_version, nodes[], edges[] }
```

Re-run it, get a byte-identical graph. Promote a skill, the node appears. Drop a
card dir, the package node appears. Nothing to migrate, nothing to reconcile.
This is the same discipline `board.cards.list_cards()` (a total fold over
`plugins/*/manifest.toml`) and `board.store.store_detail()` (a fold over one
sealed store) already follow; the vault is their union.

---

## 1. Codebase analysis — every shape a node/edge derives from

Nothing below is invented for the vault. Each is an existing sealed shape, cited.

### 1.1 SkillRecord — the skill node's body

Two roots hold the same JSON shape, both keyed by **content digest = filename stem**
(`sha_json(record)`, `plugins/graphs/__init__.py:51-58` `InMemorySkillGraph.publish`):

- **Sealed store records** — `runs/<store>/skills/<digest>.json`. Read a real one:
  `runs/stack-g1/skills/57162e40…41.json`, `runs/place-g2/skills/{adc55789…40, eb46481a…0f}.json`.
- **Mounted execution records** — `runs/session-main/skills/<digest>.json`. Same three
  digests as above (a live runtime *mounts* the sealed records; the digest is shared,
  so the fold dedupes by digest and the two roots are two *facts about one node*).

The fields a node folds (verbatim keys from the real records):

| field | meaning | node use |
|---|---|---|
| *(filename stem)* | `sha_json` content digest | **node id / identity** |
| `task` | `"stack"` — the world+policy this skill governs | node.task |
| `kind` | `"grasp_recovery"` | node.kind |
| `generation` | `1` | node.generation |
| `policy` | `"scripted"` | node.policy |
| `preconditions` | `{feature, op, threshold, dwell, arm_after, reducer}` — the trigger | node.trigger (affordance) |
| `recovery` | `{name, strategy, program[], max_invocations, sensor_sd}` | node.recovery |
| `bundle_evidence.declared_privilege` | `0` (stack-g1) / `1` (place-g2) | node.privilege |
| `bundle_evidence.heldout` | `{governed_rate, base_rate, fixed, n, p_value, broken}` | node.evidence.heldout |
| `bundle_evidence.judgement` | blind-twin comparison | node.evidence.judgement |
| `bundle_evidence.ablation` | `[[noise, {…}], …]` — the privilege ladder | node.evidence.ablation |
| `effects.dev_gate_vs_parent` | gain vs the parent bundle | node.evidence.dev_gate |
| `judgement_dev` | `{governed_rate, base_rate, p_value, n}` | node.evidence.judgement_dev |
| `heldout_judgement_established` | `true` — the commit gate | **node.status driver** |
| `bundle_sha` / `mount_plan_sha` / `prereg_sha` | content anchors | edge anchors |

The doc `docs/agent-loop-design.md §2.2` already reads exactly these as *the ASPiRe
prior with a measured operating envelope*; `preconditions` IS the SayCan feasibility
predicate, `heldout_judgement_established` IS the Voyager commit gate. The vault is
the index that makes those reads navigable instead of per-file.

### 1.2 Preregistration — the lineage fields

`plugins/rsi/campaign.py` `Preregistration` (frozen, content-hashed). The lineage
fields that make skills a *network* (round-90 seeding, all fold OUT of the hash at
default so old archives keep their sha — `_HASH_FOLD_DEFAULTS`):

- `parent_store: str | None` — a sealed store whose **final promoted bundle** this
  campaign grew from (`campaign.py:158-169`). `runs/place-g2` prereg carries
  `parent_store = runs/stack-g1`.
- `parent_final_sha: str | None` — the exact `child_sha` the parent MUST rebuild to;
  `run_campaign` asserts it before seeding (`plugins/rsi/rebuild.py:125-153`
  `rebuild_final_bundle` + `_seed_from_parent`). `place-g2` → `2f5f3756…c1a`.
- `recovery_name`, `task`, `policy`, `stages`, `env_provider`/`policy_provider`/
  `percept_provider` — the campaign's identity coordinates; two skills are the same
  lineage only if these agree.

Within a store the **generation chain** is the other lineage axis: gen N+1 is
`bundle.append(rule)` onto gen N, asserted per generation against the sealed
`child_sha` (`rebuild.py:142-150`). Today's stores are gen-1, but the rule generalizes.

`board/store.py` and `plugins/rsi/rebuild.py` both already read a sealed store's
`index.jsonl` + `artifacts/<sha>.json`; the vault reuses `rebuild.read_store_artifacts`
(pure, reads an archived store this process never wrote) to pull the prereg and
generations for lineage edges.

### 1.3 Manifest cards — the package node's body (all 9)

`plugins/*/manifest.toml`, parsed as DATA (`harness/manifest.py:52-55` `_load`, never
imported), folded per-card by `board/cards.py:26-51` `list_cards`. The 9 seated cards
and what each contributes:

| card dir | provides (mounts) | binds (task_bindings / campaigns) | claim / claim.sealed | flags |
|---|---|---|---|---|
| `embodiment_robosuite` | `embodiment.env`, `percept.model` | — (+ `[bundles.sawyer]`) | — | `actuation=sim`, `needs_sim`, `third_party=[robosuite,mujoco]` |
| `policies` | `policy.driver` | — | — | — |
| `reasoner` | `reasoner.proposer` (`top_k=3`) | — | — | — |
| `model_qwen` | `reasoner.proposer` (`enabled=false`) | — | — | alt provider, `needs_sim=false` |
| `graphs` | `graph.skill`, `graph.scene` (+ `[bundles.robot-world]`) | — | — | — |
| `task` | — | `stack`, `clear_table` / `campaigns.stack` | **`[claim]` stack** → `[claim.sealed]` `store=runs/stack-g1`, `skills=[57162e40…]` | — |
| `skill_place` | — (recovery rides the stack binding) | — | **`[claim]` place** (`recovery_name=replace`, `parent_store=runs/stack-g1`, `parent_final_sha=2f5f…`) → `[claim.sealed]` `store=runs/place-g2`, `skills=[adc5…, eb46…]` | claim-only card |
| `skill_geometric_grasp` | — | `lift_geometric` / `campaigns.lift_geometric` | `[claim]` lift (0 promoted — 100% base rate, no `[claim.sealed]`) | `actuation=sim`, `needs_sim`, `third_party` |
| `skill_toy` | — | `toy` | — | — |

Key shapes the package node folds (from `list_cards`): `contributes.{mounts,
task_bindings, campaigns, bundles}` (name-only), `actuation`, `needs_sim`,
`manifest.third_party`, `manifest.enabled`, and — new to the vault but pure data
already in the parsed manifest — the `[claim]` and `[claim.sealed]` tables. `[claim]`
is the re-runnable acceptance half (`acceptance_campaign` rebuilds the prereg
byte-for-byte); `[claim.sealed]` is the sealed half (`plugin_doctor --verify-claim`
checks it). Both are exactly the package→skill and package→lineage references the
operator wants ("互相包含引用的关系").

### 1.4 The capability catalog — the join between skills and packages

`harness/definitions.py:8-29` `CAPABILITIES` — a **fixed 9-element vocabulary**
of seams (`embodiment.env`, `embodiment.ground_truth` *(privileged=True)*,
`policy.driver`, `percept.model`, `exec.rollouts`, `reasoner.proposer`,
`task.planner`, `graph.skill`, `graph.scene`). A package **PROVIDES** a capability
(it mounts it); a skill **REQUIRES** a capability (its trigger reads a privileged or
observable feature). The feature namespace that resolves REQUIRES:
`harness/features.py:35,55` — `Privilege.PRIVILEGED = "privileged"` /
`OBSERVABLE = "observable"`, and `privilege_cost` (`features.py:83`) is the exact
function `assemble_bundle` uses to size budgets (`plugins/task/workload.py:120`). So
`preconditions.feature = "privileged.stack_xy_residual"` (place-g2, `declared_privilege=1`)
→ REQUIRES `embodiment.ground_truth`; `"observable.finger_gap"` (stack-g1,
`declared_privilege=0`) → REQUIRES `percept.model`. **This is the whole headline
finding made navigable** (`docs/headline-finding.md`): a skill that REQUIRES a
privileged read won't transfer to a real robot, and the vault shows it as an edge.

### 1.5 The governance seal — which skill governed which live node

`plugins/task/workload.py:159-168, 307-315` — every `task.plan_complete` chain note
seals per node `governance = {skills:[digest,…], bundle_sha, critic_budget,
action_budget}` (the digests ARE skills-root stems, `assemble_bundle` returns them
`workload.py:125`), and `runtime.boot` seals the session's `skills_manifest`
(the mounted set — real row: `runs/session-main/session-log/rows.jsonl:1`). Read via
`board.store.read_session` / `session_progress` (`board/store.py:357,429`). This is
the **GOVERNS** edge source: skill → the live task node it steered, on the sealed
hash chain.

### 1.6 The board fold + dual-face — the pattern to copy exactly

- Read layer: `board/store.py` (pure, robust to mid-write, never imports plugin code),
  `board/cards.py` (total per-card fold).
- MCP face: `board/mcp_server.py` — `@mcp.tool()` one-call passthroughs; name-addressed
  reads guarded by `board.store.safe_child` (`store.py:31`).
- CLI face: `board/storecli.py` — `dispatch(fn, name, …)` returns the **same object**
  the matching MCP tool returns; `docs/ph-station-design.md`'s "MCP 与 CLI 是同一函数的
  两个调用面", proven by `tests/test_storecli.py` byte-equivalence.
- Fork bridge: `ph-station` `packages/host/dsh-ph-board/src/index.ts` — `@Remote('cards')`
  etc. `execFile`s `python -m board.storecli <fn>` and `JSON.parse`s stdout verbatim
  (`index.ts:106-108, 187-194`).

The vault adds exactly three functions and rides this whole rig unchanged.

### 1.7 Rung C — the prior-in-selection this unblocks

`docs/agent-loop-design.md:376-385` (Rung C) already specifies scoring candidate
skills by precondition-match against `graph.scene` + `judgement_dev.governed_rate`.
The vault is the **read side** of Rung C: it is how the planner (and the operator
agent) *finds* the candidate skills and their measured envelope before selection.
The `skills` MCP tool sketched at `agent-loop-design.md:264` is subsumed by
`vault_node` (richer: adds lineage + backlinks).

---

## 2. THE SCHEMA — the operator's 固定的 JSON 描述格式

Versioned, JSON-Schema-shaped. **Three node kinds** (skill, package, capability) and
**one edge kind** with a typed `rel` vocabulary. Every field is *derived* — the schema
is a view contract, not a new authority. `schema_version` is a monotonic integer;
adding an optional field bumps nothing, a breaking rename bumps it (same discipline as
`SESSION_FORMAT_VERSION` in the fork).

```jsonc
// vault() top-level
{
  "schema_version": 1,
  "generated_from": {                 // provenance, for cache/debug — NOT identity
    "runs": "runs", "plugins": "plugins",
    "store_mtimes": { "stack-g1": 1690.0, "place-g2": 1691.0 }
  },
  "nodes": [ SkillNode | PackageNode | CapabilityNode, … ],  // sorted by (kind, id)
  "edges": [ Edge, … ]                                        // sorted by (rel, src, dst)
}
```

### 2.1 SkillNode

```jsonc
{
  "kind": "skill",
  "id": "57162e40d2bd4a0d59973d8c51d19f7267b682ba582c7b5c84568b334f02d41d", // digest
  "task": "stack",
  "skill_kind": "grasp_recovery",
  "generation": 1,
  "policy": "scripted",
  "label": "stack · regrasp (g1)",            // derived display: task · recovery.name (gN)
  "trigger": {                                 // preconditions verbatim — the affordance
    "feature": "observable.finger_gap", "op": "lt", "threshold": 0.001,
    "arm_after": 58, "dwell": 1, "reducer": "value"
  },
  "recovery": { "name": "regrasp", "strategy": "regrasp", "steps": 5, "max_invocations": 1 },
  "privilege": 0,                              // bundle_evidence.declared_privilege
  "evidence": {                                // ALL verbatim from bundle_evidence/effects
    "heldout":      { "governed_rate": 0.65, "base_rate": 0.585, "fixed": 13, "n": 200, "p_value": 2.4e-4, "broken": 0 },
    "judgement":    { "governed_rate": 0.65, "base_rate": 0.295, "p_value": 8.4e-16 },
    "judgement_dev":{ "governed_rate": 0.658, "base_rate": 0.388, "p_value": 7.3e-11, "n": 196 },
    "dev_gate":     { "governed_rate": 0.658, "base_rate": 0.597, "fixed": 12, "n": 196, "p_value": 4.9e-4 },
    "ablation":     [ [0.0, {"fixed":36,"governed_rate":0.765,"declared_privilege":1}], … ],
    "heldout_delta": 0.065                     // board.store._delta(heldout), the one derived number
  },
  "heldout_judgement_established": true,
  "status": "promoted",                        // derived (§2.6)
  "anchors": { "bundle_sha": "2f5f…", "prereg_sha": "febf…", "mount_plan_sha": "3c79…" },
  "evidenced_by": "stack-g1",                  // sealed store this record lives in
  "annotations": null                          // §3.4 sidecar, or null
}
```

### 2.2 PackageNode

```jsonc
{
  "kind": "package",
  "id": "plugins/skill_place",                 // card dir = identity
  "name": "skill_place",
  "provides": [],                              // capabilities it mounts
  "binds": { "tasks": [], "campaigns": [] },   // task_bindings + campaigns names
  "bundles": [],                               // named overlays (sawyer, robot-world)
  "actuation": "sim", "needs_sim": false,
  "third_party": [],
  "enabled": true,
  "claim": {                                   // [claim] — the re-runnable half, verbatim
    "task": "stack", "policy": "plugins.policies:stack_scripted_provider",
    "recovery_name": "replace", "dev": [46267,47000], "heldout": [47200,47400],
    "parent_store": "runs/stack-g1", "parent_final_sha": "2f5f…"
  },
  "claim_sealed": {                            // [claim.sealed] — the sealed half
    "store": "runs/place-g2",
    "skills": ["adc5…40", "eb46…0f"],
    "heldout_judgement_established": true,
    "rescore_blocks": ["runs/place-g2-rescore-47400","runs/place-g2-rescore-48000"]
  },
  "annotations": null
}
```

### 2.3 CapabilityNode

```jsonc
{
  "kind": "capability",
  "id": "embodiment.ground_truth",
  "contract": "GroundTruthState",             // Definition.contract name
  "privileged": true,                          // Definition.privileged
  "doc": "Simulator-only oracle state; absent on a real robot.",
  "annotations": null
}
```

Nine capability nodes, folded from the fixed `CAPABILITIES` tuple. They are the
**bridge**: without them REQUIRES and PROVIDES point at bare strings and the wiki
backlink "谁提供 / 谁依赖 embodiment.ground_truth" cannot resolve — the graph would be
two disconnected clusters (skill lineage; package contributions). The capability node
is what makes skill↔package one network, which is the operator's core ask.

### 2.4 Edge

```jsonc
{ "rel": "DESCENDS_FROM", "src": "adc5…40", "dst": "57162e40…41",
  "rule": "prereg.parent_store+parent_final_sha", "via": "runs/place-g2/prereg" }
```

Every edge carries `rule` (the named derivation, below) and `via` (the artifact it
was read from) so an auditor re-derives it. Edges are directed; the API returns both
directions so **backlinks are first-class** (the wiki property).

### 2.5 The relation vocabulary — FIXED semantics, each with a derivation rule

| `rel` | src → dst | derivation rule (mechanical) |
|---|---|---|
| **DESCENDS_FROM** | skill → skill | store S's prereg `parent_store=P` + `parent_final_sha` rebuilds P's final bundle (`rebuild_final_bundle`); each promoted record in S descends from each promoted record in P. Also: within a store, gen N+1 → gen N via the `child_sha` append chain. |
| **GOVERNS** | skill → task-node | `task.plan_complete[].nodes[nid].governance.skills` contains the digest (`workload.py:163`); dst id = `<session>/<nid>`. Session scope: `runtime.boot.skills_manifest`. |
| **REQUIRES** | skill → capability | `preconditions.feature` prefix: `privileged.*`→`embodiment.ground_truth`, `observable.*`→`percept.model` (`features.py` namespace); guarded by `declared_privilege>0` for the privileged case. |
| **PROVIDES** | package → capability | manifest `[mounts.<cap>]` → `list_cards().contributes.mounts`. |
| **BINDS** | package → task | manifest `[task_bindings.<task>]` and `[campaigns.<name>]`. |
| **EVIDENCED_BY** | skill → store | the sealed store dir the record lives in (`runs/<store>/skills/<digest>.json`); cross-checked by `bundle_sha`/`prereg_sha` present among the store's artifacts. |
| **CLAIMS** | package → skill | manifest `[claim.sealed].skills` digests (the acceptance ticket `plugin_doctor --verify-claim` checks). |
| **SUPERSEDES** | package → package | two cards mount the SAME capability, exactly one `enabled=true` (the duplicate-seam alternative, e.g. `reasoner` over disabled `model_qwen` on `reasoner.proposer`). |
| **MOUNTED_IN** | skill → session | `runtime.boot.skills_manifest` lists the digest (the live runtime mounted it). |

Nine relations. No verb is added that a read wouldn't already justify (the same
"any extra tool launders authority" discipline as `agent-loop-design.md:2.4`).
DESCENDS_FROM + generation chain gives lineage; GOVERNS + MOUNTED_IN gives the
runtime story; REQUIRES + PROVIDES + the capability node gives the affordance/transfer
story; CLAIMS + EVIDENCED_BY + BINDS + SUPERSEDES gives the packaging story.

### 2.6 Derived `status` — promoted / candidate / retired (no hand authority)

Three-valued, fully mechanical:

- **promoted** — the digest is named in some card's `[claim.sealed].skills` (the
  campaign-sealed acceptance) *and* `heldout_judgement_established == true`. These are
  the report-grade, planner-admissible skills (Voyager commit gate).
- **candidate** — a record present in a store but not claimed by any card, or not
  judgement-established. Real evidence exists; it is not yet a sealed capability.
- **retired** — a promoted record that a **newer promoted card claim on the same
  `(task, policy, recovery_name)` lineage DESCENDS_FROM**. Conservative: place
  (`recovery_name=replace`) does NOT retire stack (`regrasp`) — different repair
  lineage, sibling not replacement. Only a same-lineage successor retires.

---

## 3. THE EXPANSION RULES — 这个网络 follow 一定的规则去制定去扩张

### 3.1 The fold is the only mechanism

The vault is `build_graph(runs, plugins)`. Its inputs, all read-only:

1. **Skills roots** — `runs/*/skills/*.json` (glob, sorted). Every SkillRecord → a
   skill node (deduped by digest across roots).
2. **Sealed stores** — `runs/*/` with `index.jsonl`, via
   `plugins.rsi.rebuild.read_store_artifacts` → prereg (lineage) + generations
   (child_sha chain) → DESCENDS_FROM, EVIDENCED_BY.
3. **Manifests** — `plugins/*/manifest.toml` via `board.cards.list_cards()` →
   package nodes + PROVIDES/BINDS/CLAIMS/SUPERSEDES.
4. **Session chains** — `runs/*/session-log` via `board.store.read_session` →
   GOVERNS/MOUNTED_IN.
5. **Capability catalog** — `harness.definitions.CAPABILITIES` (+ `features` namespace)
   → capability nodes + the REQUIRES/PROVIDES targets.

### 3.2 Determinism guarantees

- **Node identity is content, not a serial.** skill = `sha_json` digest, package =
  card dir, capability = seam name. Re-folding the same tree yields the same ids.
- **Order is a sorted scan.** nodes sorted by `(kind, id)`, edges by `(rel, src, dst)`.
- **Output is byte-stable.** `json.dumps(…, sort_keys=True)` → two folds of an
  unchanged tree are byte-identical (the property `test_storecli.py` already asserts
  for board reads; the vault gets the same test).
- **No writes, no network, no clock in identity.** `store_mtimes` in `generated_from`
  is provenance only, never hashed into a node.
- **Robust to mid-write** (a campaign sealing tonight): reuse `board.store`'s
  skip-unreadable-row behavior; a half-written artifact is skipped and re-read next
  fold, never crashes the graph.

### 3.3 Auto-entry (the scalability the operator wants)

- **RSI promotes a record** → `InMemorySkillGraph.publish` writes
  `runs/<store>/skills/<digest>.json` and the campaign seals the store. Next fold: the
  skill node exists; the moment its prereg's `parent_store` also exists as a store, the
  DESCENDS_FROM edge exists. **No vault edit.** This is "rsi 设计出来的新技能更加好扩张".
- **A new card dir** with a `manifest.toml` → `list_cards()` folds it → package node +
  PROVIDES/BINDS auto-appear. **No vault edit.** ("skill 会更加 scalable".)
- **A card seals a `[claim.sealed]`** → CLAIMS edges appear and status flips
  candidate→promoted. **No vault edit.**

The rules the network "follows to expand" ARE the harness's existing sealing rules
(content-addressed publish, prereg lineage, manifest self-registration, chain seal).
The vault adds no new rule; it *reads* them. An agent that understands those five
inputs understands the whole network — that is why the network is comprehensible.

### 3.4 Hand-curated annotations — additive only, doctor-validated

The operator wants a *knowledge base* feel (cross-links, notes) beyond what the fold
can derive. Those live in **schema-validated sidecars**, never in `runs/`:

- Location: `docs/vault/annotations/<node-id>.json` (optional; **absent by default**).
- Shape: `{ "note": str?, "tags": [str]?, "see_also": [node-id]? }` — a *fixed,
  additive* key set.
- **The fold attaches an annotation only to `node.annotations`; it NEVER merges over a
  derived field.** A derived field and an annotation can coexist; they can never
  overwrite.
- A **doctor-tier check** `vault_doctor` (sibling of `plugin_doctor`) fails loud if an
  annotation: (a) targets a node id not in the folded graph, (b) uses a key outside the
  fixed additive set (a reserved/derived key like `evidence` or `status` is refused),
  or (c) fails JSON-schema validation. So an annotation may *add context* but can never
  *contradict sealed truth* (charter). `see_also` targets are validated to exist, so a
  hand link can't dangle.

> ponytail: ship the loader + `vault_doctor` (~20 lines, one test) but **zero
> annotation files**. The mechanism is the guarantee; the content is added when a real
> cross-link the fold genuinely cannot derive appears. `skipped: annotation content;
> add when a non-derivable cross-link is actually needed.`

---

## 4. AGENT READABILITY — the MCP/CLI face + preset knowledge

### 4.1 Three functions, thin folds (the whole surface)

Living in `board/vault.py`, wired identically on both faces:

| fn | args | returns |
|---|---|---|
| `vault` | — | the full `{schema_version, nodes[], edges[]}` (small graph: fold-all, like `list_cards`). |
| `vault_node` | `id` | one node + its `backlinks` (in-edges) + `out` (out-edges), resolved. The **wiki page**. |
| `vault_neighbors` | `id`, `relation?` | adjacency for one node, optionally one `rel`. Lazy/targeted for when the graph grows. |

Search folds into `vault()` client-side (substring over `id`/`task`/`label`) — at this
scale (single-digit stores, 9 cards, 9 caps) there is no index to build. `skipped:
server-side search; add when the node count outgrows a client filter.`

`id` validation: not a filesystem path, so not `safe_child` — instead, an unknown id
returns `{"error": "unknown node"}` (checked against the folded node set). Store-name
reads still route through the existing `safe_child` guard when the fold reads a store.

Dual-face wiring, byte-for-byte the board pattern:
- `board/mcp_server.py`: `@mcp.tool() def vault()/vault_node(id)/vault_neighbors(id, relation=None)`.
- `board/storecli.py`: three `dispatch` branches returning the same objects.
- fork `dsh-ph-board`: `@Remote('vault')`, `@Remote('vaultNode')`, `@Remote('vaultNeighbors')`
  `execFile`ing `python -m board.storecli vault …`.

### 4.2 Preset knowledge — the paragraph for the physical operator preset

> **The Vault is the wiki of everything this harness has learned — read it before you
> plan.** Call `vault()` to see which tasks have a *promoted* skill (green,
> `heldout_judgement_established=true`) versus only *candidate* or none — that is the
> real capability boundary; never propose a mission that assumes a skill the vault does
> not list. To ground a node, open its page with `vault_node(<digest>)`: `trigger` is
> the affordance predicate (the skill is admissible only if it matches the current
> `graph.scene`), and `evidence.heldout`/`judgement_dev` are the *measured* success
> numbers — quote them, don't estimate. Follow **REQUIRES** to a capability node: if it
> reaches `embodiment.ground_truth` (privileged), the skill leans on a simulator-only
> read and **will not transfer to a real robot** — say so. Follow **DESCENDS_FROM** to
> read a skill's lineage (place descends from stack), **GOVERNS** backlinks to see which
> live task nodes it actually steered, and **CLAIMS**/**EVIDENCED_BY** to find the card
> that packages it and the store that proves it. When several skills could serve a node,
> rank by `judgement_dev.governed_rate` + precondition match and record the scores
> (Rung C / SayCan feasibility). The vault writes nothing and invents nothing; if it is
> silent on a capability, the capability does not exist yet.

Every node is a page; every typed edge is a backlink on both endpoints. That is the
llm-wiki the operator asked for, and it is exactly the harness's own sealed evidence
re-read in reading order.

---

## 5. UI SPEC — the wiki view for the fork

Respect cockpit-v2 (read latest `origin/main` before building — current state:
client packages `ui-ph-ops` / `ui-ph-livegraph` / `ui-ph-panels` / `ui-ph-battle`
under `packages/client/`; board data plane `packages/host/dsh-ph-board` @Remote
bridge). The vault is a distinct browsable surface → a new **`ui-ph-vault`** client
package, sibling to the four, registering a conversation-view tab (技能库 / Vault).

### 5.1 Reuse, don't re-vendor

- **Graph canvas**: reuse `@xyflow/react` `^12.8.1` + `@dagrejs/dagre` `^1.1.4` — already
  a dependency of `ui-ph-livegraph` (`packages/client/ui-ph-livegraph/package.json`,
  `LiveGraphView.tsx:19 import { Background, ReactFlow } from '@xyflow/react'`). Lift the
  shared dagre-layout + `<GraphCanvas>` helper into a small shared module both packages
  import (satisfies the `pnpm run duplication` gate) rather than copying it.
- **Evidence numbers**: reuse `ui-ph-battle`'s 战报 held-out/ablation renderer for the
  skill page's evidence block — same verbatim board dict, no second number formatter.
- **Card renderer**: reuse `ui-ph-panels`' 机箱 card component for the package page header.
- Pure consumer, renders only (charter: TS renders, statistics stay in `board/vault.py`).

### 5.2 The three views

1. **Relation graph** (`vault()`): a React Flow graph, node color by kind
   (skill/package/capability) and, for skills, by status (promoted=green,
   candidate=amber, retired=grey). Edges labeled by `rel`. Filter chips per `rel` and per
   kind. Dagre left-to-right layout; DESCENDS_FROM renders as a vertical lineage spine.
   Click a node → its page.
2. **Node detail page** (`vault_node(id)`) — the wiki page, **typed backlinks in a
   sidebar**:
   - **skill page**: header (`label`, task, status badge, privilege badge); **evidence
     numbers verbatim** (heldout governed_rate vs base_rate, p_value, n; the ablation
     ladder; judgement_dev) — the exact board dict, no rounding beyond what board emits;
     a **lineage strip** (DESCENDS_FROM chain, horizontal, each a link); **governed
     tasks** (GOVERNS backlinks → session/node, links into `ui-ph-livegraph`);
     **backlinks** panel (CLAIMS from packages, EVIDENCED_BY store, MOUNTED_IN sessions);
     **REQUIRES** → capability chip (privileged chips flagged red — "won't transfer").
   - **package page**: contributions (PROVIDES capability chips, BINDS task chips,
     campaigns, bundles); **claims** (`[claim]` re-runnable params + `[claim.sealed]`
     digests as links to skill pages); chassis flags (actuation/needs_sim/third_party/
     enabled); SUPERSEDES/superseded-by links.
   - **capability page**: contract + privileged flag + doc; backlinks = every package
     that PROVIDES it and every skill that REQUIRES it (the affordance hub).
3. **Search / filter**: a text box over `vault()` (client-side substring on
   id/task/label) + the rel/kind/status chips. No server round-trip per keystroke.

### 5.3 Entry points

- **From 机箱** (`ui-ph-panels`): each card row gets a "→ Vault" link to its package page.
- **From the execution graph** (`ui-ph-livegraph`): a governed node (one whose
  `governance.skills` is non-empty) links to the skill page(s) that GOVERN it — closing
  the loop from a live run back to the sealed skill and its evidence.
- **Standalone**: the Vault tab itself (graph + search as the landing view).

---

## 6. ARCHITECTURE — where the fold lives, wiring, tests, build order

### 6.1 The fold: `board/vault.py` (not `harness/`)

**Decision: `board/vault.py`.** Reasons:
- It reads BOTH `plugins/*/manifest.toml` (via `board.cards`) AND `runs/` sealed
  evidence (via `board.store` + `plugins.rsi.rebuild`). `harness/` must stay
  **plugin-free** (`tests/test_kernel.py`, `harness/manifest.py:18`); the vault imports
  `plugins.rsi.rebuild`, so it cannot live in `harness/`.
- `board/` is *already* the read/index layer that crosses both trees (`board/cards.py`
  imports `harness.manifest._load`; `board/store.py` reads `runs/`). The vault is their
  union — same neighborhood, same "no writes, renders-only" charter.
- The dual-face rig (`mcp_server.py`/`storecli.py` + fork `@Remote`) already lives in
  `board/`. Adding three functions there is a few lines; a `harness/` module would have
  to be re-exported into `board/` anyway.

`board/vault.py` API: `build_graph(runs=…, plugins=…) -> dict`, `node(graph, id) -> dict`,
`neighbors(graph, id, relation=None) -> dict`. Pure; a `if __name__ == "__main__"`
assert-based self-check folds the **real** `runs/` and asserts the known truths (below).

### 6.2 Dual-face wiring (copy the board pattern verbatim)

- `board/mcp_server.py`: three `@mcp.tool()` passthroughs into `board.vault`.
- `board/storecli.py`: three `dispatch` branches + argparse `fn` entries
  (`vault|vault_node|vault_neighbors`), `--relation` optional arg for neighbors.
- `ph-station` `packages/host/dsh-ph-board/src/index.ts`: `@Remote('vault')`,
  `@Remote('vaultNode')`, `@Remote('vaultNeighbors')` → `this.run('vault'…)`. Types in
  `src/types.ts`. (Before merge: `nvm use 22`, fetch+rebase onto latest `origin/main` —
  cockpit-v2 may reshape the ui-ph-* packages; consume whatever main has.)

### 6.3 Test plan

1. **`tests/test_vault.py` — fold over real `runs/`** (the load-bearing self-check):
   - node for `57162e40…41` exists, `status=promoted`, `task=stack`, `privilege=0`,
     `evidence.heldout.governed_rate == 0.65` (verbatim from the record).
   - nodes `adc5…40`, `eb46…0f` exist, `privilege=1`, each **DESCENDS_FROM** `57162e40…41`
     (via `place-g2` prereg `parent_store=runs/stack-g1`).
   - `plugins/skill_place` **CLAIMS** `adc5…40` and `eb46…0f`; `plugins/task` **CLAIMS**
     `57162e40…41`.
   - `plugins/task` **BINDS** `stack`; `embodiment_robosuite` **PROVIDES** `embodiment.env`.
   - `eb46…0f` **REQUIRES** `embodiment.ground_truth` (feature `privileged.stack_xy_residual`);
     `57162e40…41` **REQUIRES** `percept.model` (`observable.finger_gap`).
   - `reasoner` **SUPERSEDES** `model_qwen` on `reasoner.proposer`.
2. **Byte-equivalence** (mirror `tests/test_storecli.py`): `board.vault.build_graph`
   == `storecli vault` stdout == `mcp_server.vault()` tool, for all three fns.
3. **Determinism**: two `build_graph` calls `json.dumps(sort_keys=True)`-equal.
4. **`vault_doctor`**: an annotation with a reserved key, an unknown node id, or a
   dangling `see_also` each fails loud; a valid additive annotation attaches under
   `node.annotations` and overwrites no derived field.
5. **Base-gate**: adding tests changes the count → refresh the snapshot + README in the
   **same commit**, measure both lanes (`docs/base-gate.md`).

### 6.4 Build order

- **A. `board/vault.py`** — `build_graph` (nodes: skill/package/capability; edges:
  the 9 rels) + real-`runs/` self-check. Commit, push.
- **B. Dual-face** — MCP + CLI three fns + byte-equivalence test. Commit, push.
- **C. Fork bridge** — `@Remote` three methods + types + byte-parity. (nvm 22; rebase
  onto latest origin/main first.) Commit, push.
- **D. `ui-ph-vault`** — graph view + node pages + search, reusing xyflow/battle/panels
  components. Commit, push.
- **E. Entry links** — 机箱 → package page, livegraph governed-node → skill page. Commit, push.
- **F. (deferred) annotations** — sidecar loader + `vault_doctor`, ship empty. Commit, push.

Each rung is independently landable and testable; A–C deliver the full agent-readable
vault with zero UI (the operator agent can traverse it immediately), D–E deliver the
wiki view, F is the optional curation seam.

---

## 7. Charter check

- **Read/index infra, deterministic fold** ✔ — `build_graph` is a pure function over
  sealed inputs; re-run = byte-identical.
- **No new statistics** ✔ — every number is verbatim from `bundle_evidence`/`effects`;
  the only derived scalar is `board.store._delta` (already existing).
- **No hand-authored truth contradicting sealed evidence** ✔ — annotations are additive,
  `vault_doctor`-guarded, never merged over derived fields.
- **TS renders only** ✔ — `ui-ph-vault` is a pure consumer of the board Remote.
- **Skill↔package互相包含引用** ✔ — CLAIMS (package→skill), BINDS/PROVIDES + capability
  hub, DESCENDS_FROM lineage, EVIDENCED_BY — the network the operator drew.
- **RSI/new cards auto-expand** ✔ — publish + manifest self-registration are the only
  expansion mechanism; the fold reads them with no edit.
