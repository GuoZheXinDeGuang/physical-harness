======================================================================
# VERSION 1
======================================================================

## QUESTION
How should the Governor episode event log be designed so that dsh's "anything a decision-maker sees must be reconstructable from the log" invariant survives 20 Hz embodied execution (600 observation frames per 30 s episode, thousands of episodes), and how is that invariant enforced by a runtime assertion without storing everything?

## DECISION
Split the log into a **row log** (the durable, seq-addressed JSONL event stream — 20-30 rows per episode) and a **column store** (content-addressed, zstd-compressed struct-of-arrays blocks, referenced by digest from row events). No per-frame event ever exists. This is the direct adaptation of dsh's `assistant/chunk` → `assistant/message` packing (`packages/core/session/src/surface.ts`, `agent.ts:341-411`), with one deliberate divergence: dsh still materializes one row per chunk and cites them via `sourceEventSeqs: number[]`; Governor pushes the sequence space one level *down into the block* and cites frames as compound addresses `[block_seq, row_idx]`, because dsh's ratio is ~10 chunks/turn while ours is 600 frames/episode × 10^4 episodes.

The invariant is then restated precisely, and this restatement is the whole design:

> **The invariant is about decision inputs, not about observations.**
> A *decision view* — the exact feature dict a critic/skill/LLM stage was handed at step t — is stored verbatim, inline, columnar, and is **never** subject to retention (Tier 0, ~10 KB/episode compressed). Raw observation frames are *evidence*, stored under a three-tier retention policy and droppable.

Three enforcement layers, in order of strength:

1. **Structural (`visible ⟹ logged`, by construction).** A decider can only receive input through a `FeatureView` — a frozen, read-attesting mapping. In-process critics get a `__getitem__` that records `read_names`; sandboxed critic code gets the view marshalled across a Seatbelt-confined process boundary with no ambient access (dsh `code-runtime-worker-thread` empty-env + `ctx.sandbox.confine()` fail-closed posture). There is no path by which a critic reads `env.sim.data.qpos` and it does not appear in the log.
2. **Runtime assertion (`logged ⟹ sent`, always on).** `assert_view_reconstructable()` runs *before* every decider dispatch, registered `prepend=True` on the decider seam so a replay/mock decider cannot short-circuit it (dsh `agent-loop/src/invariant.ts:19-55`). It compares a 16-byte digest of the view against a digest independently re-derived from the frame the log holds. Cost is ~10-20 µs against a 50 ms control period (<0.05%), so unlike dsh's — which re-`JSON.stringify`s the whole message history and therefore had to be opt-in — **this one ships enabled on the robot.**
3. **Offline audit (`re-derivable`, degrades gracefully).** `audit_episode()` re-runs the derivation for every decision whose source block survives retention, and reports `derivable_fraction`. Retention never weakens the guarantee — the assertion already ran at write time when every frame was in memory — it only reduces how much of that can be *re-checked* later.

Privilege budget (the project's own contribution) falls out of this for free: `read_names` is logged per tick, so the declared budget is checked against **actual reads**, and the zero-privilege ablation required by GOAL.md acceptance #3 is a replay over the log with privileged columns nulled, not a separate experiment.

## RATIONALE
**Why the row/column split rather than smaller events.** The binding cost is not bytes, it is *rows scanned*. Failure segmentation, clustering, and gate accounting (Zetta `trajectory.py::_segments`, `clustering.py`, `gate_runner.py`) read only structure. At 600 rows/episode × 10^4 episodes a campaign scan touches 6×10^6 JSON lines; at ~25 rows/episode it touches 2.5×10^5, and the column blocks are opened only when a segment window is actually materialized. The row log is the index; the column store is the evidence. Bytes improve too (~65 KB vs ~360 KB per episode) but that is the smaller win.

**Why decision views are Tier 0 and frames are not.** Measured on this project's own calibration (`docs/difficulty-calibration.md`): the discriminating feature is `robot0_gripper_qpos` finger gap, 40× separation between success and failure. A decision view is ~10 float32. 600 ticks × ~60 B = 36 KB raw, ~8 KB zstd, per episode; 10^4 episodes = ~100 MB, permanently retainable. A raw frame is ~60 float32 including all of `robot0_proprio-state` + `object-state` + action; keeping all of those forever for every episode is what does not scale. So the thing the invariant is *about* is cheap and the thing it is *not* about is expensive — which is exactly why restating the invariant in terms of decision inputs is not a weakening dodge, it is the correct scoping.

**Why this is dsh's surface/log split, not a new idea.** dsh already separates "what happened" (the complete immutable log) from "what the model sees" (`surface.nodes`, an ordered subset that can be rewritten by `surfaceOp: replace` + `sourceEventSeqs`, `surface.ts:184-379`). Governor's decision views *are* the surface; frames blocks are log-only. Consequence: the same `replace` machinery lets an evidence-bundle compaction shadow 600 frames with one summary node for the offline diagnoser, without deleting anything — so the LLM stages' context is reconstructable by the same rule as the critic's.

**Why the assertion compares digests instead of dsh's `JSON.stringify` equality.** dsh's check is O(full history) per request and is therefore a registrable diagnostics companion you turn off in production (`runtime-diagnostics/invariants`). At 20 Hz that trade is unacceptable *and* unnecessary: our derivation is local (one frame → one view), so a blake2b-128 over ~80 bytes plus a re-run of the extractor is ~10 µs. Making it always-on is the point — the class of bug it catches (a perception wrapper slipping a feature into the critic that never entered the log) is the one that silently invalidates every offline eval and every gate decision.

**Why stage-then-assert-then-invoke rather than commit-then-invoke.** dsh logs `tool/call` before execution and `tool/result` after (`docs/tool-execution-pipeline.md`), so a call always has a result. Same discipline here, but a `decision/tick` is not its own row — it is a row appended into an in-memory block builder. The assertion checks against the *staged* row and a flush-time check verifies every staged row resolved to an outcome (synthesizing `not-dispatched`, dsh's `TOOL_ABORTED_BEFORE_DISPATCH`, for anything aborted mid-flight). In sim this is fully sufficient because a torn episode is `infra_invalid` and retried (Zetta `max_infrastructure_attempts=2`, attempts ledger separate from episodes ledger); the repair function is retained anyway so a real-robot provider can *close* torn episodes rather than discard them.

**Why stateful critics force a state digest per tick.** Zetta's `TemporalCritic` (`critic.py:46-110`) has dwell and cooldown. "Exactly what the critic saw at step t" is not the features alone — it is features *plus* the internal counters. The decision block therefore carries `state_digest` per tick, and replaying the block from row 0 must reproduce that digest column exactly. Without it, replay reconstructs the input but not the decision, and shadow replay's lead-time statistics become unfalsifiable.

**Why two timestamps and an explicit `log/gap` event.** dsh carries one `Date.now()` ms field, which the analysis flags as non-portable to embodied use. Governor carries `t_mono_us` (episode-relative monotonic, authoritative) and `t_wall_us` (correlation only), plus per-frame `dt_us` deltas as a column — the direct analogue of dsh's per-chunk `dt` in the packed text-chunks row. And because commit-then-publish onto a bounded queue can drop under backpressure, a drop must be a *logged event* (`log/gap`), never silence: a silent drop is the one failure mode that breaks the invariant everything else rests on.

## REJECTED
- **One event per observation frame (naive dsh port).** 600 rows/episode × 10^4 episodes = 6M JSON lines per campaign; `surface.nodes` becomes a million-entry list; dsh's O(surface) `deriveMessages` rebuild after any compaction becomes unaffordable; and `JSON.stringify(messages)` in the request invariant becomes a per-step cost measured in milliseconds against a 50 ms budget. Rejected outright — this is the antipattern the analysis names first.
- **Rosbag/MCAP as the primary log, with the event log as a sidecar.** MCAP is a better raw-telemetry container than anything written here, but it has no notion of provenance (`source_seqs`), no surface/shadow semantics, no seq-contiguity contract, and no place to hang the reconstruction assertion. Rejected as *primary*; the column-block format is deliberately shaped so an MCAP or rosbag provider can back it later (block = one chunk, digest = chunk CRC) behind the same `ColumnStore` seam.
- **Store only the raw frames and re-derive decision views on demand.** This is the tempting inversion: frames are the ground truth, views are a pure function of frames. It fails for three independent reasons. (a) The extractor is *evolving* — a promoted critic bundle changes the schema between generations, so re-derivation with today's extractor does not reproduce what generation-3's critic saw. (b) It makes the invariant unfalsifiable: there is nothing to compare the derivation against. (c) It couples audit to retention — drop a frame and the decision becomes unreconstructable. Storing the view redundantly (inline *and* derivable) is what makes the check have teeth at all.
- **Store only the decision views, drop raw frames entirely at episode close.** Satisfies the invariant perfectly and costs ~10 KB/episode, but destroys the campaign: Zetta's failure segmentation scans `states.jsonl` for the earliest divergence step with a sliding no-progress window (`trajectory.py::_window_no_progress`), and shadow replay (`shadow_replay.py`) replays *new, not-yet-written* critic rules against stored state rows. A rule authored in generation 4 needs frames from generation 1 that no generation-1 view ever read. Rejected — hence the three-tier policy with evidence windows rather than a binary keep/drop.
- **Per-frame delta compression against the previous frame instead of columnar struct-of-arrays.** Marginally better ratio on smooth trajectories but requires sequential decode to reach row t, which is exactly the access pattern the evidence-window and single-frame-derivation paths need to be O(1) in. Columnar + zstd per block keeps random row access at one block decompress. Delta-encoding *within* a column (applied to `dt_us` and joint positions) is kept; cross-frame chaining is not.
- **Make the runtime assertion an opt-in diagnostics companion, exactly as dsh does.** Correct for dsh's cost profile, wrong for ours. Once the derivation is local rather than whole-history, the assertion is cheap enough to be unconditional, and unconditional is worth far more: the bug class it catches produces *silently wrong gate decisions*, and a check that runs only in CI does not run during the campaign that produces the paper's numbers.
- **A single global relational trace (`one open episode, one open step`) as in dsh `session/invariant.ts:23-30`.** Adequate today (Panda, one arm, one critic) and adopted as-is, but explicitly marked as a known limit: a multi-limb or multi-track harness needs per-track open/close state, and the `decision/block` schema already carries a `track` field reserved for it so the migration is additive rather than a format break.

## RISKS
- **The read-attestation `FeatureView` is bypassable in-process.** A critic that captures a reference to the raw obs dict, or reaches into `env.sim`, defeats layer 1 entirely, and the digest assertion cannot see it. Mitigation: sandboxed execution is the *real* boundary and in-process critics are a dev-only fast path; the promotion gate must refuse any bundle whose declared privilege budget was produced under the in-process path. Concretely: `header/decider.attestation_mode` must be `sandboxed` for any episode used as a gate arm, checked in `store.promote`.
- **`derivable_fraction` can silently drift toward zero.** Retention is applied per generation; if the pin set is computed wrong, blocks that decisions cite get collected and the offline audit quietly loses coverage while still reporting 'no mismatches' (vacuously). Mitigation: `min_derivable_fraction` is a preregistered field of `RetentionProtocol`, hashed into the campaign manifest, and `audit_episode` must report `UNAVAILABLE` counts distinctly from `OK` — a campaign whose audit coverage falls below the preregistered floor fails rather than passes.
- **float32 bit-exactness of the derivation.** The assertion hashes raw f32 bytes. Any path that round-trips a value through float64, through Python arithmetic, or through a different BLAS ordering (e.g. a vectorized extractor at write time vs a scalar one at audit time) produces a spurious MISMATCH. Mitigation: one extractor implementation, pinned by `extractor_sha256`, invoked identically on both paths; a regression test that derives the same frame through both call sites and asserts digest equality. Expect this to fire during implementation.
- **Block flush cadence interacts with the paired same-seed gate.** Flushing on every `decision/fire` means a candidate bundle that fires more often produces a different block layout than its parent. Block layout must not leak into any digest that gating binds on — `initial_observation_identity` and the trajectory digests must be computed over *row content*, never over block boundaries or file bytes. Mitigation: `episode_actions_sha256` is defined over the concatenated per-row action bytes independent of blocking, with a test that re-blocks an episode at a different `BLOCK_ROWS` and asserts the digest is unchanged.
- **The 20 Hz assertion budget is estimated, not measured.** ~10-20 µs is an estimate from operation counts, not a benchmark; a Python-level `__getitem__` attestation on a dict of 40 keys plus a blake2b call could plausibly land at 100-200 µs, which is still <0.5% of the control period but no longer negligible if the critic itself is also Python. Mitigation: benchmark before committing to always-on, and keep a `GOVERNOR_INVARIANT=off` escape that emits a `header/decider.invariant_enforced=false` field so any episode run without the check is *marked in the log* and rejected by the gate rather than silently accepted.
- **`log/gap` is honest but not free.** Recording a drop preserves the invariant's truthfulness, but a campaign whose episodes carry gaps has degraded evidence for clustering and shadow replay. Sim episodes with any `log/gap` should be classified `infra_invalid` and retried, not analyzed — which means backpressure on the writer turns into throughput loss at 212 episodes/min. Mitigation: bound the staging buffer at one whole episode (~100 KB) so a worker never needs to drop; treat any `log/gap` in sim as a bug to fix rather than a condition to handle.

## SPEC
Proposed module layout under `/Users/yusenthebot/Desktop/physical-harness/governor/`:

```
governor/episode/
  events.py       # Event envelope, EventType, payload TypedDicts, snapshot_json
  log.py          # EpisodeLog: append, seq contiguity, staging, flush, folds
  columns.py      # ColumnBlockBuilder, ColumnStore (write/read/verify), schema hashing
  view.py         # FeatureView (frozen, read-attesting), view_digest, extractors
  invariant.py    # assert_view_reconstructable  <-- the teeth
  replay.py       # reconstruct_decision, verify_decision, audit_episode
  retention.py    # RetentionProtocol (frozen, hashed), pin set, apply
  repair.py       # torn_episode_closers(events) -> list[Event]
```

---

## 1. Envelope

```python
JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

SURFACE_ELIGIBLE = frozenset({
    "decision/block", "decision/fire", "skill/result", "task/goal",
    "context/note", "evidence/summary",
})

@dataclass(frozen=True, slots=True)
class Event:
    type: str
    seq: int            # INVARIANT: seq == len(log) at append; contiguous from 0
    t_mono_us: int      # episode-relative monotonic, authoritative ordering
    t_wall_us: int      # unix wall clock, correlation only
    data: JsonValue     # snapshot_json'd once at append, then frozen
    surface_op: SurfaceOp | None = None      # only on SURFACE_ELIGIBLE types
    source_seqs: tuple[int, ...] | None = None
    source_rows: tuple[tuple[int, int], ...] | None = None   # [(block_seq, row_idx)]

SurfaceOp = Literal["append"] | tuple[Literal["replace"], int, int]
```

`source_rows` is the divergence from dsh: dsh's provenance is a flat `sourceEventSeqs: number[]` because every chunk is its own row (`surface.ts:210-243`). Governor addresses *into* a block, so provenance is a compound `(block_seq, row_idx)`.

`snapshot_json` (one recursive read-validate-copy pass, dsh `session/src/index.ts:604-655`) **rejects at the append site**: numpy arrays, torch tensors, `datetime`, `-0.0`, `NaN`, `Inf`, circular refs, and any float64 where a f32 was intended. Large arrays are never inline — they go to the column store and the row carries a digest.

---

## 2. Event type list with payload schemas

### `episode/start` — exactly once, seq 0
```python
{"episode_id": str,              # "g0003-rollout-017"
 "generation": int,
 "campaign_id": str,
 "manifest_sha256": str,
 "bundle_sha256": str,           # the critic/recovery bundle under test
 "seed": int,                    # passed to suite.make(seed=), NOT np.random.seed
 "policy_rng": int,
 "task": str,                    # "Lift"
 "robot": str,                   # "Panda"
 "role": Literal["rollout","same_seed_candidate","same_seed_parent",
                 "regression","heldout","ablation"],
 "harness_version": str,
 "t0_wall_us": int}
```

### `header/env` — diff-and-append-on-change (dsh `request-header.ts:21-71`)
```python
{"reason": Literal["initial","resume","change"],
 "env_spec_sha256": str,
 "control_freq_hz": float,        # 20.0
 "horizon": int,                  # 100
 "action_dim": int,               # 7
 "controller": {...},             # OSC_POSE config, canonicalized
 "obs_schema": [                  # ORDERED; defines column layout
   {"name": "robot0_eef_pos", "dtype": "f4", "shape": [3],
    "group": "robot0_proprio-state", "privilege": "proprio"},
   {"name": "cube_pos", "dtype": "f4", "shape": [3],
    "group": "object-state", "privilege": "privileged"},
   ...],
 "obs_schema_sha256": str,
 "provider_defaults": [str],      # fields the ENV resolved, not the operator
                                  # (dsh adapterDefaults, renamed)
 "sim_version": {"mujoco": "3.3.7", "robosuite": "1.5.2"}}
```
`privilege` is a closed enum: `proprio | exteroceptive | privileged`. `proprio` = present on real hardware. `privileged` = simulator-internal. This classification is the type-system half of the privilege budget; the gate is the other half.

Canonicalization rule (dsh `canonicalHeader`): empty collections become **absent** fields, so one representation serves logging, folding, and equality.

### `header/decider` — diff-and-append-on-change
```python
{"reason": Literal["initial","resume","change"],
 "bundle_sha256": str,
 "parent_sha256": str | None,
 "rules": [{"rule_id": str, "kind": Literal["threshold","code"],
            "features": [str], "dwell": int, "cooldown": int,
            "code_sha256": str | None}],
 "view_schema": [{"name": str, "dtype": "f4", "shape": [int],
                  "privilege": "proprio|exteroceptive|privileged"}],
 "view_schema_sha256": str,
 "extractor_sha256": str,         # pins obs frame -> view derivation
 "declared_privilege_budget": {   # THE PROJECT'S OWN CONTRIBUTION
   "names": [str],                # privileged features the author declares
   "class": Literal["zero","bounded","unbounded"],
   "justification": str},
 "cadence_steps": int,            # 1 = every control step
 "attestation_mode": Literal["sandboxed","in_process"],
 "invariant_enforced": bool,      # false => episode rejected by gates
 "sandbox": {"runner": "seatbelt", "profile_sha256": str,
             "enforcement": Literal["full","partial"]} | None}
```
`invariant_enforced: false` is written whenever the assertion was disabled. `store.promote` refuses any gate arm with `invariant_enforced=false` or `attestation_mode="in_process"`.

### `env/reset` — the pairing identity (Zetta `initial_observation_identity`)
```python
{"state_sha256": str,             # sha256 over (qpos, qvel) f64 bytes
 "obs_sha256": str,               # sha256 over the canonical frame-0 row
 "seed": int, "policy_rng": int,
 "model_sha256": str}             # MJCF digest
```
Paired gates bind on `state_sha256` (Zetta `gating.py::_same_physical_reset`). Governor **requires** it — the legacy full-object fallback is not implemented.

### `frames/block` — THE PACKED ROW (the text-chunks adaptation)
```python
{"track": "control",                 # reserved for multi-limb; single value today
 "start_step": int, "end_step": int, "n_rows": int,
 "schema_sha256": str,               # == header/env.obs_schema_sha256
 "columns": [{"name": str, "dtype": "f4|i4|u1",
              "shape": [int], "offset": int, "nbytes": int,
              "min": float, "max": float, "mean": float}],
 "t_mono_us_base": int,
 "dt_us_column": "dt_us",            # per-frame delta, dsh's per-chunk dt
 "payload_sha256": str,              # over UNCOMPRESSED bytes -> recompression stable
 "payload_ref": {"file": "columns.bin", "offset": int, "clen": int,
                 "codec": "zstd-3"},
 "flush_reason": Literal["full","fire","schema_change","episode_end"],
 "dropped": false}
```
Flush policy: `min(BLOCK_ROWS=128, any decision/fire, schema change, episode end)`. Fencing on a fire guarantees a firing decision's source frame is in the immediately preceding block — a single block read for the offline derivation path.

Columns always present: every `obs_schema` entry, plus `action` (f4[7]), `reward` (f4), `done` (u1), `sim_time_us` (i4), `dt_us` (i4).

### `decision/block` — packed critic ticks (non-firing + firing alike)
```python
{"track": "control", "bundle_sha256": str,
 "view_schema_sha256": str, "extractor_sha256": str,
 "start_step": int, "end_step": int, "n_rows": int,
 "columns": [ # per tick:
   {"name": "step",          "dtype": "i4"},
   {"name": "<view feature>","dtype": "f4"},   # ALL view features, verbatim
   {"name": "read_mask",     "dtype": "u4"},   # bitset over view_schema, ACTUAL reads
   {"name": "state_digest",  "dtype": "u8"},   # critic dwell/cooldown state, per tick
   {"name": "view_digest",   "dtype": "u8"},   # blake2b-128 truncated to 8 bytes
   {"name": "outcome",       "dtype": "u1"},   # 0 no-fire 1 fire 2 abstain 3 error
                                               # 4 not-dispatched (aborted before run)
   {"name": "latency_us",    "dtype": "u4"}],
 "payload_sha256": str, "payload_ref": {...},
 "source_block_seq": int,          # the frames/block these views derive from
 "privilege_reads": {str: int},    # per-name actual read counts, from read_mask
 "surface_op": "append"}
```
**This block is Tier 0. It is never dropped by any retention policy.** ~60 B/tick × 600 = 36 KB raw, ~8 KB zstd.

### `decision/fire` — never packed, full inline payload
```python
{"step": int, "rule_id": str, "bundle_sha256": str,
 "features": {str: float},         # verbatim, inline, human-readable
 "read_names": [str],
 "privilege_class": Literal["zero","bounded","unbounded"],
 "state_digest": str, "view_digest": str,
 "proposal": {"skill_id": str, "args": {...}},
 "latency_us": int,
 "source_rows": [[block_seq, row_idx]],
 "surface_op": "append"}
```

### `decision/error`
```python
{"step": int, "rule_id": str,
 "kind": Literal["exception","timeout","abort","worker_exit",
                 "invalid_output","output_limit","feature_missing"],
 "message": str, "view_digest": str}
```
The six-way taxonomy is dsh's `CodeRunFailure` (`code-runtime/src/types.ts:79-107`) plus `feature_missing`. It is a **field on a resolved result, never a raised exception** — the control loop branches on `kind`, it does not catch.

### `skill/call` / `skill/result` — pairing is a durability invariant
```python
# skill/call
{"call_id": str, "skill_id": str, "args": {...}, "step": int,
 "triggered_by_seq": int}
# skill/result
{"call_id": str, "ok": bool, "steps_consumed": int,
 "error": {"code": str, "message": str} | None,
 "concludes_episode": bool,
 "surface_op": "append", "source_seqs": [call_seq]}
```
On abort: drain what started, commit real outcomes, then append a synthetic call+error result `SKILL_ABORTED_BEFORE_DISPATCH` for every unstarted call (dsh `tool-calls.ts:248-289`). The log never contains an action with no outcome.

### `code/run` — sandboxed critic program execution
```python
{"call_id": str, "code_sha256": str,
 "budgets": {"compute_ms": int, "wall_ms": int, "rss_mb": int},
 "outcome": {"kind": "ok|exception|timeout|abort|worker_exit|
                      invalid_output|output_limit", "message": str},
 "enforcement": Literal["full","partial"],   # never a boolean "sandboxed: true"
 "stdout_sha256": str, "elapsed_us": int,
 "binding_calls": [{"name": str, "denied": bool}]}
```

### `task/goal` (surface), `context/note` (surface)
```python
# task/goal  -- the episode's objective, one per episode
{"goal_id": str, "text": str, "spec": {...}}
# context/note -- perception/operator inject; visible next decision, not a wake
{"source": {"kind": "plugin|operator|perception", "id": str},
 "text": str, "surface_op": "append"}
```
`context/note` is dsh's `inject` (wake=false, `agent.ts:113-132`). Safety-critical input does **not** come through here — it goes through the phase-owned cancel path, which preempts immediately.

### `evidence/summary` (surface, replace) — compaction for offline stages
```python
{"kind": Literal["segment_window","episode_digest"],
 "shadowed_range": [int, int],        # positional over CURRENT surface nodes
 "shadowed_seqs": [int],
 "text": str, "stats": {...},
 "surface_op": ["replace", start, end],
 "source_seqs": [*shadowed_seqs]}     # MUST include every shadowed node
```
Positional replace requires a stability re-check before commit if any await intervened (Zetta `region.ts:190-192` — load-bearing, not defensive).

### `log/gap` — a drop is an event, never silence
```python
{"reason": Literal["backpressure","writer_error","overflow"],
 "start_step": int, "end_step": int, "n_rows_lost": int, "track": str}
```
Any episode containing a `log/gap` is classified `infra_invalid` and retried (Zetta attempts ledger, `max_infrastructure_attempts=2`); it never enters the episodes ledger.

### `retention/apply`
```python
{"protocol_sha256": str, "tier": Literal["A","B","C"],
 "dropped_block_seqs": [int], "retained_block_seqs": [int],
 "pinned_rows_count": int, "derivable_fraction": float,
 "applied_at_generation": int}
```

### `episode/end`
```python
{"reason": {"kind": Literal["completed","failed","horizon","aborted",
                            "safety","env_error","interrupted"],
            "cause": {"kind": "operator|estop|supervisor|watchdog|
                              safety_envelope|disposed"} | None},
 "success": bool, "steps": int,
 "degraded": [str],                  # STICKY, see below
 "actions_sha256": str,              # over concatenated per-row action bytes,
                                     # INDEPENDENT of block boundaries
 "final_obs_sha256": str,
 "n_fires": int, "n_skill_calls": int,
 "privilege_reads": {str: int},      # folded over the whole episode
 "invariant_checks": int, "invariant_failures": 0}
```
`degraded` is **sticky**: once any step records `torque_limited` / `envelope_clamped` / `perception_degraded`, a later clean step must not downgrade the episode's recorded outcome (dsh's max-tokens stickiness, `agent.ts:290`). `episode/end` is appended in a `finally` on every exit path.

---

## 3. Storage format

```
runs/<campaign_id>/episodes/<episode_id>/
  events.jsonl      # the row log. seq == line index. append-only, fsync at close.
  columns.bin       # concatenated zstd frames. blocks addressed by (offset, clen).
  manifest.json     # written at close: {events_sha256, columns_sha256,
                    #  block_index: [{seq, payload_sha256, offset, clen}], role, tier}
```

Block payload layout, uncompressed: struct-of-arrays, columns in schema order, each column `n_rows × prod(shape)` elements, little-endian, no padding. `payload_sha256` is over these uncompressed bytes so codec changes do not change identity.

Size budget, measured against this project's actual obs space (`docs/verified-environment.md`):
| | per episode (600 frames) | 10^4 episodes |
|---|---|---|
| frames columns, raw | ~144 KB | 1.4 GB |
| frames columns, zstd-3 | ~45 KB | 450 MB |
| decision columns, zstd-3 (**Tier 0**) | ~8 KB | 80 MB |
| events.jsonl (~25 rows) | ~12 KB | 120 MB |
| **total, Tier A** | **~65 KB** | **~650 MB** |
| **total, Tier C** (frames dropped) | **~20 KB** | **~200 MB** |

Naive per-frame JSON for comparison: ~360 KB/episode, 3.6 GB, and 6×10^6 lines to scan per campaign pass.

---

## 4. Retention policy — preregistered, hashed, not ad hoc

```python
@dataclass(frozen=True, slots=True)
class RetentionProtocol:
    decision_views: Literal["always"] = "always"   # Tier 0; not configurable
    full_frames_roles: tuple[str, ...] = (
        "same_seed_candidate", "same_seed_parent", "regression",
        "heldout", "ablation")
    full_frames_when: tuple[str, ...] = (
        "cluster_member", "diagnosis_cited", "precommit_cited", "has_fire")
    window_before: int = 40          # 2 s @ 20 Hz; matches Zetta context_before=8
    window_after: int = 40           #   scaled from segment steps to control steps
    decimation_k: int = 5            # Tier B keeps a 4 Hz survivor stream
    success_frames_ttl_generations: int = 1
    min_derivable_fraction: float = 1.0   # campaign FAILS below this

    def __post_init__(self):
        assert self.window_before >= 0 and self.window_after >= 0
        assert self.decimation_k >= 1
        assert 0.0 <= self.min_derivable_fraction <= 1.0
```
Flattened into `manifest.runtime["retention_protocol"]`, covered by `manifest_sha256`, therefore **frozen for the whole generation** (Zetta `EvolutionProtocol.runtime_policy()`).

Tiers:
- **Tier A (full)** — role in `full_frames_roles`, or any condition in `full_frames_when`. All frames kept forever.
- **Tier B (windowed)** — other valid failures. Keep frames within `[div - window_before, div + window_after]` for every failure segment's `earliest_divergence_step`, plus `± window` around every `decision/fire`, plus every `decimation_k`-th frame episode-wide.
- **Tier C (rows only)** — successes older than `success_frames_ttl_generations`. Frames dropped; `frames/block` rows survive with their per-column min/max/mean, so summary statistics remain queryable.

**The rule that preserves the invariant:**
```
pinned_rows(log) = { (b, r) : any decision event cites (b, r) in source_rows }
A block may be dropped only if the episode is not Tier A.
Tier A: pinned_rows ⊆ retained_rows is asserted before any drop.
```
Because `earliest_divergence_step` may be `None` (Zetta `models.py:245` — "known failure, evidence does not localize it"; encoding it as 0 poisons lead-time statistics), a Tier B episode with `None` on every segment falls back to whole-episode decimation rather than a window.

---

## 5. Replay: reconstructing exactly what a critic saw at step t

Two paths, deliberately redundant.

```python
@dataclass(frozen=True, slots=True)
class ReconstructedDecision:
    step: int
    features: dict[str, float]        # verbatim, from decision/block columns
    read_names: tuple[str, ...]       # ACTUAL reads, decoded from read_mask
    privilege_by_name: dict[str, str]
    critic_state_digest: str
    outcome: str
    rule_id: str | None
    bundle_sha256: str
    view_schema_sha256: str
    extractor_sha256: str
    view_digest: str
    source_rows: tuple[tuple[int, int], ...]

def reconstruct_decision(log: EpisodeLog, step: int) -> ReconstructedDecision:
    """EXACT and TIER-INDEPENDENT. Never touches the frame store.
    Fold header/env + header/decider up to the covering decision/block's seq,
    decode that block's row for `step`, return the view verbatim."""
```

```python
def verify_decision(log, columns, step) -> Literal["OK","UNAVAILABLE","MISMATCH"]:
    """DERIVED path. Re-runs the pinned extractor on the raw frame and
    compares digests. UNAVAILABLE iff retention dropped the source block."""
    d = reconstruct_decision(log, step)
    block_seq, row = d.source_rows[0]
    if not columns.has(block_seq):
        return "UNAVAILABLE"
    frame = columns.read_row(block_seq, row)
    derived = EXTRACTORS[d.extractor_sha256](frame)
    return "OK" if view_digest(derived, d.view_schema_sha256, step) == d.view_digest \
           else "MISMATCH"
```

**Stateful critics.** `reconstruct_decision` gives the *input*; reproducing the *decision* additionally requires the dwell/cooldown counters, which is why `state_digest` is a per-tick column. Full determinism check:

```python
def replay_critic(log, bundle) -> None:
    """Replay the bundle from row 0 over the logged views and assert the
    state_digest column reproduces exactly. If it does not, the critic is
    non-deterministic or reads something outside the view -> reject."""
```
This is the check that makes shadow-replay lead-time statistics falsifiable.

**Offline LLM stages** (diagnoser, proposer) are decision-makers too: their context is `fold_surface(events)` projected through `derive_event_message`, so a `evidence/summary` replace makes exactly what they saw reconstructable by the same rule.

---

## 6. THE ASSERTION

Registered `prepend=True` on the decider seam so a replay or mock decider cannot short-circuit it (dsh `agent-loop/src/invariant.ts:19-55` prepends on the `llm/stream` waterfall for exactly this reason).

```python
class InvariantError(RuntimeError): ...

def _fail(msg: str) -> NoReturn:
    raise InvariantError(f"[episode-log] {msg}")

# Identity by registry, NEVER by shape -- a dict that looks like a view is not one.
_HARNESS_VIEWS: weakref.WeakSet = weakref.WeakSet()

def mark_harness_view(v: FeatureView) -> FeatureView:
    _HARNESS_VIEWS.add(v); return v


def assert_view_reconstructable(log: EpisodeLog, view: FeatureView) -> None:
    """Runs BEFORE every decider dispatch. ~10-20 us against a 50 ms period.

    Enforces: anything a decision-maker sees is reconstructable from the log.
    """
    if view not in _HARNESS_VIEWS:
        return                                   # not harness-built; not our contract

    # 1. structural: an episode and a step are open, and this is that step
    if log.open_episode is None:
        _fail("view dispatched with no open episode")
    if log.open_step != view.step:
        _fail(f"view.step={view.step} but log.open_step={log.open_step}")

    # 2. immutability: the view was frozen before dispatch
    if not view.frozen:
        _fail("view is not frozen; a decider could mutate its own input")

    # 3. pinning: the staged decision row exists and addresses a real frame
    staged = log.staged_decision(view.step)
    if staged is None:
        _fail(f"no staged decision row for step {view.step} "
              f"(a decider was invoked without being logged first)")
    if not log.frames.has_row(*staged.source_row):
        _fail(f"staged decision cites unwritable frame {staged.source_row}")

    # 4. THE TEETH: byte-identical re-derivation from the durable frame.
    #    dsh compares JSON.stringify(messages) == JSON.stringify(deriveMessages());
    #    we compare digests, because the derivation here is local (frame -> view)
    #    rather than whole-history, which is what makes this affordable at 20 Hz.
    header = log.fold_decider_header()
    frame = log.frames.read_row(*staged.source_row)          # in memory, pre-flush
    derived = EXTRACTORS[header.extractor_sha256](frame)
    if view_digest(derived, header.view_schema_sha256, view.step) != view.digest:
        _fail(f"step {view.step}: view does not re-derive from the logged frame; "
              f"something entered the decider that never entered the log")

    # 5. header agreement (dsh checks model/system/tools against the folded header)
    if view.schema_sha256 != header.view_schema_sha256:
        _fail("view schema does not match the folded header/decider")
    if view.extractor_sha256 != header.extractor_sha256:
        _fail("extractor identity does not match the folded header/decider")

    # 6. PRIVILEGE BUDGET: this project's own contribution, checked on ACTUAL reads.
    #    Deferred to post-dispatch because read_names is only complete after the
    #    decider has run; see assert_privilege_budget below.
    if not set(view.provided_names) <= set(header.view_schema_names):
        _fail("view offers a feature absent from the declared view schema")


def assert_privilege_budget(log: EpisodeLog, view: FeatureView) -> None:
    """Runs AFTER dispatch, before the outcome is committed to the block."""
    if view not in _HARNESS_VIEWS:
        return
    header = log.fold_decider_header()
    read = set(view.read_names)                              # attested, not declared
    if not read <= set(view.provided_names):
        _fail(f"decider read {read - set(view.provided_names)} outside its view")
    privileged_read = {n for n in read
                       if header.privilege_by_name[n] == "privileged"}
    declared = set(header.declared_privilege_budget["names"])
    if not privileged_read <= declared:
        _fail(f"undeclared privileged read: {sorted(privileged_read - declared)}; "
              f"declared budget = {sorted(declared)}")
    if header.declared_privilege_budget["class"] == "zero" and privileged_read:
        _fail(f"bundle declares zero-privilege but read {sorted(privileged_read)}")
```

Dispatch ordering, mirroring dsh's log-call-then-execute-then-log-result:

```python
def run_decider(log, critic, frame, step) -> Outcome:
    view = mark_harness_view(build_view(log.fold_decider_header(), frame, step))
    token = log.stage_decision(step, view)        # validate-before-commit
    assert_view_reconstructable(log, view)        # THROWS before anything runs
    try:
        outcome = critic.evaluate(view)           # only path by which a critic reads
    except Exception as e:
        outcome = Outcome.error(kind="exception", message=str(e))
    assert_privilege_budget(log, view)
    log.commit_decision(token, outcome, view.read_names, critic.state_digest())
    return outcome
```

Flush-time completeness check (closes the stage-vs-commit gap):
```python
def flush_decision_block(log) -> None:
    for row in log._staging:
        if row.outcome is None:
            row.outcome = Outcome.NOT_DISPATCHED   # dsh TOOL_ABORTED_BEFORE_DISPATCH
    if len(log._staging) != (log._staging[-1].step - log._staging[0].step + 1):
        _fail("decision block has a step gap; emit log/gap instead of eliding")
    ...
```

Campaign-level audit, the offline half:
```python
@dataclass(frozen=True, slots=True)
class AuditReport:
    n_decisions: int
    n_ok: int
    n_unavailable: int
    n_mismatch: int
    derivable_fraction: float     # n_ok / (n_ok + n_unavailable)

def audit_episode(log, columns) -> AuditReport: ...
```
A campaign fails if `n_mismatch > 0` anywhere, or if `derivable_fraction < protocol.min_derivable_fraction`. `n_unavailable` is reported **distinctly** from `n_ok` so retention cannot make the audit pass vacuously.

---

## 7. Required tests (GOAL.md acceptance #4: "the invariant really does blow up")

1. `test_invariant_catches_injected_feature` — a critic wrapper adds `cube_pos` to the view after staging; `assert_view_reconstructable` must raise `InvariantError`. **This is acceptance #4.**
2. `test_invariant_catches_undeclared_privilege` — a zero-privilege bundle reads `cube_pos`; `assert_privilege_budget` must raise.
3. `test_block_boundary_does_not_leak_into_digests` — re-block an episode at `BLOCK_ROWS=17` and `BLOCK_ROWS=997`; `actions_sha256`, `state_sha256`, and every `view_digest` must be unchanged.
4. `test_extractor_bit_exact_across_call_sites` — write-path and audit-path derivation of the same frame produce identical f32 bytes.
5. `test_paired_seed_determinism` — `suite.make(seed=N)` twice, digests identical; and `np.random.seed(N)` twice, digests **differ** (regression test for the failure mode already documented in `STATUS.md`).
6. `test_replay_reproduces_critic_state` — `replay_critic` reproduces the whole `state_digest` column.
7. `test_retention_never_drops_pinned_rows` — apply Tier B to an episode with fires; assert every `source_rows` entry survives.
8. `test_torn_log_repairs_deterministically` — truncate `events.jsonl` mid-episode; `torn_episode_closers` produces the same synthetic closers twice, continuing the seq numbering and reusing the last real `t_mono_us`.


======================================================================
# VERSION 2 (post-critique, authoritative)
======================================================================

## QUESTION
How should the Governor episode event log be designed so that dsh's "anything a decision-maker sees must be reconstructable from the log" invariant survives 20 Hz embodied execution, and how is that invariant enforced by a runtime assertion without storing everything — in a version that actually builds and verifies tonight on macOS arm64, CPU-only MuJoCo?

## DECISION
**The core architecture survives. Its central mechanism does not, and I have proof.**

Keep: the row-log/column-store split, one packed block per episode instead of per-frame events, decision views stored verbatim as Tier 0, the always-on runtime assertion registered ahead of the decider seam, `read_names` attestation as the basis of the privilege budget, and identity-by-registry (WeakSet) rather than identity-by-shape.

**Four corrections, three of them load-bearing:**

**1. The assertion as specified is circular and cannot catch the bug GOAL.md acceptance #4 requires. Verified by running it.** The spec computes `view.digest` at build time from a frame, then compares it to a digest re-derived from that same frame. That is `f(x) == f(x)`. I built both versions and ran them against a real seeded robosuite `Lift`/Panda episode:

```
INJECTION CAUGHT (live-contents digest)
original spec (cached view.digest): NOT CAUGHT  <-- f(x)==f(x), circular
```

A wrapper that adds `privileged.leaked` to the view after staging sails straight through the spec's check. The fix is one line of semantics: **`view.digest` must be a method over the view's live contents evaluated at assertion time**, never a field cached at construction. Then injection, post-hoc mutation, and schema drift all bite. Measured on the real episode: 100 checks, **7.7 µs mean, 0.074 % of a control step**.

**2. `FeatureView` must subclass `collections.abc.Mapping`, not `dict`.** The spec says "a `__getitem__` that records `read_names`". Against a `dict` subclass, attestation leaks completely — `dict.get`, `.items()`, `.values()`, `dict(v)` and `{**v}` are C-level and never call the Python `__getitem__`:

```
dict subclass  -> attested reads: set()   <-- privileged read invisible
Mapping .get() -> attested reads: {'privileged.cube_z'}
Mapping {**v}  -> attested reads: {'observable.finger_gap','privileged.cube_z'}
```

The `Mapping` ABC routes every accessor through `__getitem__`. Bulk accessors over-attest (report every key as read), which is the *safe* direction for a budget: a bundle can only ever look more privileged than it is, never less. This matters because the privilege budget is the project's whole contribution, and on the specified implementation it silently reports zero.

**3. The privilege assertion must cover the recovery executor, not just the critic.** `progress.md` records that round 1's headline result was nearly invalidated by exactly this: *"我自己漏了 recovery 的特权 ... 是消融实验把 `target = obs["cube_pos"]` 这句抓出来的"*. The spec's `assert_privilege_budget` is registered on the decider seam only, so it would not have caught the bug that already bit this project once. Recovery skills read observations to act, and those reads spend budget. **Same `FeatureView`, same assertion, same seam** — a `recovery/call` gets a view and is attested identically. This is the single highest-value addition and it costs about twenty lines.

**4. Cut roughly 60 % of the spec. Two of its quantitative premises are wrong against this repo.**

- *An episode is 100 control steps / 5 s, not 600 frames / 30 s.* `NOMINAL_SCHEDULE` in `governor/env.py` sums to 25+25+12+38 = 100, and `rollout()` runs exactly `sum(d for _,d in spec.schedule)`. The spec's own `header/env` sample says `"horizon": 100` while its rationale argues from 600. Every size and scan figure is inflated 6×.
- *The control period is 10.4 ms, not 50 ms.* `docs/verified-environment.md` measures 96 control steps/s single-process (14.1 ms per worker at 10-way). The budget denominator is 5× smaller than claimed. The conclusion survives anyway — measured 7.7 µs is 0.074 % — but it now rests on a benchmark instead of an estimate, which retires the spec's own risk #5.

Correcting these collapses the case for most of the machinery. Measured storage for a real episode: **2.1 KB/episode zstd, 22 MB for 10⁴ episodes**; even storing the full raw obs space it is under 200 MB. The three-tier `RetentionProtocol`, pin sets, `derivable_fraction`, `min_derivable_fraction` and Tier-B divergence windows exist to manage **22 MB on a 64 GB laptop**. Cut entirely. Also cut: the whole `surface`/`replace`/`evidence/summary` subsystem (its only consumers are offline LLM stages, and GOAL.md requires the loop to close with zero external API calls); Seatbelt sandboxing and `code/run`; `log/gap` (the writer is synchronous and in-process, so backpressure cannot occur); `repair.py`; and the `proprio | exteroceptive | privileged` enum, which gratuitously replaces the shipped, tested two-value `Privilege` in `governor/features.py` where the namespace *is* the declaration.

**The sandbox cut is not just economy — as specified it deadlocks the campaign.** The spec has `store.promote` refuse any gate arm with `attestation_mode != "sandboxed"`, while sandboxing is a later frontier. Tonight that means no episode can be promoted, so acceptance #1 and #2 are unreachable by construction. Record `attestation_mode` in the header, make the promotion requirement a preregistered manifest field, and default it to accepting `in_process` for sim — where the critic is generated by this harness's own `search.py` and the threat is accidental leakage, not an adversary.

What ships tonight is four files, one real event log per episode, and a test that provably blows up.

## RATIONALE
**On self-deception, which is where the original design is weakest.** It contains three mechanisms that report success without evidence, and the third is the one the whole project rests on.

*The circular assertion.* Covered above — demonstrated, not argued.

*A metric that cannot fail.* `derivable_fraction = n_ok / (n_ok + n_unavailable)` with `min_derivable_fraction = 1.0` and no retention applied is identically 1.0 on every campaign. It would appear in the paper as a validated invariant while never having been in a position to be false. With retention cut, report `n_mismatch` and raw counts and nothing else.

*Honest scoping of what the runtime check catches.* Even with the live-contents fix, the assertion compares a view against a row derived from the same in-memory observation. It genuinely catches injection, post-staging mutation, schema drift, and — if the row is unpacked back out of its stored f32 layout, which I specify — column-layout and f64→f32 truncation bugs. It does **not** catch a wrong extractor, because the extractor defines both sides. Do not claim otherwise. The extractor is pinned by hashing `governor/features.py` and covered by a separate test. Writing this boundary into the docstring is what keeps the claim falsifiable.

**Why the row/column split still earns its place at 100 rows/episode.** The byte argument is now weak (2.1 KB), but the structural argument is untouched and was always the real one: the row log is the index, the arrays are the evidence, and `search.py` already consumes exactly this shape — it scans `trace[feature]` as a dense `np.ndarray` per episode and never wants JSON. Per-frame events would force every consumer to re-densify on read. The split is what the existing code already wants.

**Why the compound address `[block_seq, row_idx]` stays even with one block per episode.** It is free today (`block_seq` is always 0) and it is a format break to add later. Keep the shape, drop the flush-policy machinery, `BLOCK_ROWS`, and the offset/length arithmetic into a shared `columns.bin`. One `frames.zst` per episode directory.

**Why `zstandard` is fine.** I checked: `zstandard 0.25.0` is installed and already declared in `pyproject.toml`. Python 3.12 has no stdlib zstd (`compression.zstd` is 3.14+), so this would otherwise be a dependency gate. It is not — no new dependency, no gate to pause on.

**Why the smallest verifiable version is smaller than it looks.** `progress.md`'s own round-1 seed already names it: *"最短真实链路：跑 1 个 episode → 落事件日志 → 从日志重建第 t 步的 critic 视图 → 断言一致"*. That is one evening. My spike ran the full chain — real seeded episode, real f32 columns, real zstd blob, 100 live assertions, injection caught — in about forty lines. The spec's fifteen event types, three retention tiers, surface algebra, sandbox, and torn-log repair are a week that produces nothing observable on night one.

**What I keep from the original that deserves defending.** Identity-by-WeakSet rather than by duck-typed shape is correct and I would not have thought of it. `prepend=True` registration so a mock decider cannot short-circuit the check is correct. Storing decision views verbatim rather than re-deriving them on demand — with the rejection reasoning about an *evolving* extractor across generations — is correct and is what gives the check teeth at all. `state_digest` per tick for stateful critics is correct and directly necessary here, because `search.py`'s `Trigger` carries `dwell` and `arm_after`, so "what the critic saw" is not the features alone. Sticky `degraded`. Two timestamps with monotonic authoritative. `decision/error` as a resolved field rather than a raised exception. All kept.

**One inconsistency to fix while nearby.** `tests/test_determinism.py::_digest` hashes `np.round(flat, 9)`, so the existing determinism guarantee is 9-decimal, not bit-exact, despite `env.py`'s docstring saying "bit-identical". The new `frames_sha256` must hash raw f32 bytes with no rounding, and the two should be reconciled — otherwise the paired gate's identity claim and the log's identity claim are different claims wearing one name.

## REJECTED
- **The original spec's cached `view.digest` compared against a re-derivation from the same frame.** Rejected on evidence, not taste: built it, ran it on a real episode, and the injected privileged feature was not caught. It is `f(x) == f(x)`. Replaced by a live-contents digest evaluated at assertion time.
- **`FeatureView` as a `dict` subclass with an overridden `__getitem__`.** Rejected on evidence: `dict.get`/`.items()`/`{**v}` bypass it entirely and attest an empty read set, which would make the privilege budget — the project's own contribution — silently always report zero privilege. Replaced by `collections.abc.Mapping`.
- **The three-tier `RetentionProtocol` with pin sets, evidence windows and `min_derivable_fraction`.** Rejected as unearned by a factor of ~1000: measured 2.1 KB/episode, 22 MB for 10^4 episodes, on a 64 GB machine. It also depends on `earliest_divergence_step` from a failure-segmentation module that does not exist in this repo yet. Keep every frame forever; revisit when disk actually hurts, which on these numbers is around 10^7 episodes.
- **The `surface` / `surfaceOp: replace` / `evidence/summary` algebra ported from dsh.** Rejected for tonight: its only consumers are offline LLM diagnoser/proposer stages, and GOAL.md requires the evolution loop to close with zero external API calls. It brings positional-replace stability re-checks and an O(surface) rebuild for a decision-maker that is optional by design. `source_seqs` survives as a plain provenance field.
- **Seatbelt sandboxing plus `store.promote` refusing any `attestation_mode != "sandboxed"` gate arm.** Rejected as specified because it deadlocks its own acceptance: sandboxing is a stated later frontier, so tonight no episode is promotable and acceptance #1/#2 are unreachable. Sandboxing is also the wrong threat model for sim, where critics come from this harness's own `search.py`. Keep `attestation_mode` as a recorded header field and make the promotion requirement a preregistered manifest policy.
- **The `proprio | exteroceptive | privileged` three-value privilege enum.** Rejected: `governor/features.py` already ships a tested two-value `Privilege` where the `observable.` / `privileged.` namespace prefix *is* the declaration, enforced in `__post_init__`. Introducing a third class and a parallel vocabulary breaks working code for no gain. `exteroceptive` is a real distinction worth having on real hardware; add it when there is hardware.
- **`log/gap`, `repair.py` torn-episode closers, and the staged-vs-committed reconciliation.** Rejected for tonight: the writer is synchronous and in-process with a whole episode buffered in memory (~5 KB), so there is no bounded queue and no backpressure, therefore no drop to record. Reserve the `log/gap` type name so adding it later is additive.
- **Multi-block flushing with `BLOCK_ROWS=128`, flush-on-fire fencing, and shared `columns.bin` offset arithmetic.** Rejected: an episode is 100 rows, so it is always exactly one block. This also dissolves the spec's own risk about block layout leaking into gate digests — with one block per episode there is no boundary to leak. Retain the `[block_seq, row_idx]` address *shape* so multi-block is a non-breaking change.
- **Deriving the decision view directly from the live `obs` dict handed to the assertion.** Rejected in favour of deriving it from the frame *unpacked back out of its stored f32 column layout*. Costs one `unpack_obs` call and buys a genuine second check the live-obs path cannot give: it catches column-order, shape, and f64->f32 truncation bugs in the storage layer itself.
- **`snapshot_json` rejecting `-0.0` at the append site.** Rejected as cargo-culted: `-0.0` round-trips through `json` correctly. Rejecting `NaN`/`Inf` is right and kept, since `json.dumps` emits bare `NaN`, which is not valid JSON and would produce a log that does not reload.

## RISKS
- **The runtime assertion still cannot catch a wrong extractor** — the extractor defines both the view and the comparison target, so a bug in `governor/features.py` is invisible to it. This is a real residual limit, not a bug to fix, and the danger is overclaiming in the writeup. Mitigation: state the boundary explicitly in `invariant.py`'s module docstring; pin the extractor by hashing the `features.py` file bytes into `feature_registry_sha256`; cover extractor correctness with ordinary unit tests. Do not let `invariant_failures: 0` be read as 'the features are right'.
- **Over-attestation from bulk accessors could make a genuinely zero-privilege bundle fail its budget.** `dict(view)` or `{**view}` attests every key including privileged ones. The direction is safe (never under-reports), but a critic written with `sorted(view.items())` would be spuriously rejected as privileged. Mitigation: `build_view` includes only the features the bundle declared, so a zero-privilege bundle's view contains no privileged keys at all and bulk access is harmless. This makes the *view construction*, not the read attestation, the primary enforcement — attestation is the second layer.
- **f32 bit-exactness across the write and audit paths.** `features.py` extractors return Python floats (f64) via `float(np.linalg.norm(...))`; the digest casts to f32. Any path that re-derives through a different numpy call order or a vectorized variant can produce a different last bit and a spurious MISMATCH. Mitigation: one code path (`REGISTRY[n].extract`) invoked identically at both sites, and `test_extractor_bit_exact_across_call_sites`. Carried forward from the original design; I expect this to fire during implementation.
- **The paired-gate identity claim and the log identity claim currently disagree.** `tests/test_determinism.py` hashes `np.round(flat, 9)` while `env.py`'s docstring claims bit-identical and the new `frames_sha256` hashes raw f32 bytes. If unrounded reruns are not in fact bit-identical, the new digest will be flaky where the old test was green, and it will look like a logging bug. Mitigation: before wiring anything, run the unrounded digest on two same-seed reruns and find out which claim is true; reconcile the docstring to whichever it is.
- **The measured 7.7 us assertion cost does not include `unpack_obs` + full `REGISTRY.extract` on the audit-shaped path.** That adds roughly six numpy calls; a realistic ceiling is 50-100 us, still under 1% of a 10.4 ms step, but the headroom is 5x smaller than the spec assumed because the real period is 10.4 ms and not 50 ms. Mitigation: assert on the measured number in CI (`test_assertion_cost_under_budget`, threshold 500 us) so a regression is caught rather than discovered during a campaign; keep `GOVERNOR_INVARIANT=off` writing `invariant_enforced: false` into the header so any unchecked episode is marked in its own log.
- **Cutting retention is right at 10^4 episodes and wrong at 10^7.** If the frontier rounds scale the campaign or move to richer observations (camera obs, more tasks), storage stops being free abruptly. Mitigation: the cut is reversible by construction — frames already live in a separate file addressed by `[block_seq, row_idx]`, so retention is a later filter over a directory, not a format change. Record measured bytes/episode in `episode/end` so the crossover is observable rather than surprising.
- **Recovery-side privilege attestation depends on the recovery executor actually taking its observations through a `FeatureView`.** If the executor keeps a reference to `obs` — exactly the round-1 bug (`target = obs["cube_pos"]`) — the assertion sees nothing. Mitigation: the recovery executor's signature takes a `FeatureView` and never an obs dict; add a grep-level test that `governor/recovery.py` contains no `obs[` subscript. Crude, but it targets the specific line that already caused a false result once.
- **In-process attestation is genuinely weaker than the sandbox I am cutting, and the writeup must say so.** The claim tonight is 'the harness accounts for every declared read', not 'a hostile critic cannot exfiltrate privileged state'. Mitigation: `attestation_mode: "in_process"` is written into every header and into the campaign manifest, so the weaker claim is on the record in the artifact rather than in a footnote.

## SPEC
## What I would build tonight

Four files under `/Users/yusenthebot/Desktop/physical-harness/governor/episode/`. Reuses `governor/features.py` and `governor/env.py` unchanged.

```
governor/episode/
  view.py       # FeatureView (Mapping, read-attesting, live digest), build_view
  log.py        # Event, EpisodeLog, obs pack/unpack, write events.jsonl + frames.zst
  invariant.py  # assert_view_reconstructable, assert_privilege_budget   <- the teeth
  replay.py     # load_episode, reconstruct_decision, audit_episode
```

Cut relative to the original: `events.py`, `columns.py`, `retention.py`, `repair.py`, and roughly 60 % of the event types.

---

### 1. `view.py` — the seam, and the only path a decider reads through

```python
class InvariantError(RuntimeError): ...

class FeatureView(Mapping):                 # Mapping, NOT dict -- dict leaks attestation
    __slots__ = ("_d", "step", "read_names", "schema_sha256")

    def __init__(self, d: dict[str, float], step: int, schema_sha256: str):
        self._d = dict(d); self.step = step
        self.read_names: set[str] = set(); self.schema_sha256 = schema_sha256

    def __getitem__(self, k: str) -> float:
        if k not in self._d:
            raise InvariantError(f"step {self.step}: feature {k!r} not in view")
        self.read_names.add(k)
        return self._d[k]

    def __iter__(self):  return iter(self._d)
    def __len__(self):   return len(self._d)

    def live_digest(self) -> bytes:
        """Over LIVE contents at call time. Never cached -- caching is what makes
        the check circular; the cached version does not catch injection."""
        h = hashlib.blake2b(digest_size=16)
        h.update(self.schema_sha256.encode()); h.update(str(self.step).encode())
        for k in sorted(self._d):
            h.update(k.encode()); h.update(b"\0")
            h.update(np.float32(self._d[k]).tobytes())
        return h.digest()

_HARNESS_VIEWS: weakref.WeakSet = weakref.WeakSet()      # identity by registry, not shape

def build_view(names: Sequence[str], obs: Mapping[str, np.ndarray],
               step: int, schema_sha256: str) -> FeatureView:
    """The ONLY constructor. Contains exactly the declared names -- a zero-privilege
    bundle's view holds no privileged key at all, so bulk access cannot spend budget."""
    v = FeatureView({n: REGISTRY[n].extract(obs) for n in names}, step, schema_sha256)
    _HARNESS_VIEWS.add(v)
    return v
```

`Mapping` is load-bearing and measured: with `dict`, `.get()` / `.items()` / `{**v}` attest nothing.

---

### 2. `log.py` — nine event types, one block per episode

```python
@dataclass(frozen=True, slots=True)
class Event:
    type: str
    seq: int                # == line index in events.jsonl, contiguous from 0
    t_mono_us: int          # episode-relative, perf_counter_ns based, authoritative
    t_wall_us: int          # correlation only
    data: dict              # json-clean: rejects NaN/Inf/ndarray/circular at append
    source_seqs: tuple[int, ...] | None = None
    source_rows: tuple[tuple[int, int], ...] | None = None   # [(block_seq, row_idx)]
```

`source_rows` keeps the compound shape with `block_seq == 0` always, so multi-block is additive later.

**Event types (9).** Dropped from the original: `env/reset` (folded into `episode/start`), `code/run`, `task/goal`, `context/note`, `evidence/summary`, `log/gap`, `retention/apply`, and every `surface_op` field.

| type | when | key payload |
|---|---|---|
| `episode/start` | seq 0 | `spec` (`asdict(EpisodeSpec)` verbatim — never a hand-enumerated field list that drifts), `episode_id`, `generation`, `role`, `reset_state_sha256` (over `qpos\|qvel` f64) |
| `header/env` | after reset | `obs_schema` (ordered `[{name, shape, offset}]`), `obs_schema_sha256`, `control_freq_hz: 20.0`, `n_steps: 100`, `action_dim: 7`, `sim_version` |
| `header/decider` | before first tick | `bundle_sha256`, `view_names`, `view_schema_sha256`, `feature_registry_sha256` (sha256 of `features.py` bytes), `declared_privilege_budget: {names, class}`, `trigger` (`asdict(Trigger)`), `attestation_mode: "in_process"`, `invariant_enforced: bool` |
| `frames/block` | one, at close | `n_rows`, `columns` (obs schema + `action[7]`, `reward`, `done`), `payload_sha256` over **uncompressed** f32 bytes, `payload_ref: {file: "frames.zst", codec: "zstd-3"}` |
| `decision/block` | one, at close | per tick: `step`, every view feature verbatim (**Tier 0**), `read_mask` u4, `state_digest` u8, `view_digest` u8, `outcome` u1, `latency_us` u4 |
| `decision/fire` | on fire | `step`, `features` inline and human-readable, `read_names`, `view_digest`, `proposal`, `source_rows` |
| `decision/error` | on error | `step`, `kind: "exception"\|"timeout"\|"feature_missing"`, `message` — a resolved field, never a raised exception |
| `recovery/call` / `recovery/result` | on recovery | `call_id`, `skill_id`, `args`, `triggered_by_seq`; result carries `ok`, `steps_consumed`, **`read_names`** |
| `episode/end` | `finally`, every path | `success`, `steps`, `frames_sha256`, `actions_sha256`, `privilege_reads: {name: count}` folded over critic **and** recovery, `invariant_checks`, `invariant_failures`, `bytes_written` |

`recovery/result.read_names` is the round-1 fix: `progress.md` records that a privileged read inside the recovery (`target = obs["cube_pos"]`) nearly invalidated the headline number and was caught only by a manual ablation. It is now attested by the same mechanism as the critic.

**Layout** — one directory per episode, no shared files, no offset arithmetic:

```
runs/<campaign_id>/episodes/<episode_id>/
  events.jsonl     # seq == line index
  frames.zst       # one zstd frame: struct-of-arrays f32, columns in schema order
  manifest.json    # {events_sha256, frames_sha256, n_rows, role}
```

Measured on a real seeded `Lift`/Panda episode: **2.1 KB/episode**, 22 MB for 10⁴.

```python
def pack_obs(obs, schema)  -> np.ndarray            # (n_cols,) f32, schema order
def unpack_obs(row, schema) -> dict[str, np.ndarray] # inverse; used by the assertion
```

`unpack_obs` is what lets the assertion re-derive through the *stored layout* rather than the live dict — that is the part that catches column-order and f64→f32 bugs.

---

### 3. `invariant.py` — the teeth

```python
def assert_view_reconstructable(log: EpisodeLog, view: FeatureView) -> None:
    """Runs BEFORE every decider dispatch. Measured 7.7 us; ~0.07% of the
    10.4 ms control period measured in docs/verified-environment.md.

    CATCHES: a feature injected into the view after staging; post-staging
    mutation; view/header schema disagreement; a decision at a step whose frame
    was never written; column-order and f64->f32 truncation in the storage layer.

    DOES NOT CATCH: a wrong extractor. The extractor defines both sides of the
    comparison. It is pinned by feature_registry_sha256 and covered by tests.
    Do not read invariant_failures == 0 as 'the features are correct'.
    """
    if view not in _HARNESS_VIEWS:
        return                                    # not harness-built; not our contract

    if log.open_episode is None:
        _fail("view dispatched with no open episode")
    if log.open_step != view.step:
        _fail(f"view.step={view.step} but log.open_step={log.open_step}")

    hdr = log.fold_decider_header()
    if view.schema_sha256 != hdr.view_schema_sha256:
        _fail("view schema does not match the folded header/decider")

    # the frame must already be durable-in-memory BEFORE the view is dispatched
    row = log.frames.staged_row(view.step)
    if row is None:
        _fail(f"decider invoked at step {view.step} with no logged frame")

    # THE CHECK: live view contents vs a re-derivation through the STORED layout.
    # live_digest() must be evaluated here, not cached at build time -- the cached
    # form is f(x)==f(x) and provably fails to catch injection.
    derived = build_view(hdr.view_names, unpack_obs(row, hdr.obs_schema),
                         view.step, hdr.view_schema_sha256)
    if view.live_digest() != derived.live_digest():
        _fail(f"step {view.step}: view does not re-derive from the logged frame; "
              f"something entered the decider that never entered the log")


def assert_privilege_budget(log, view: FeatureView, *, actor: str) -> None:
    """AFTER dispatch, before the outcome is committed. actor in {'critic','recovery'}.
    Registering this for BOTH actors is the round-1 fix."""
    if view not in _HARNESS_VIEWS:
        return
    hdr = log.fold_decider_header()
    read = set(view.read_names)                       # attested, not declared
    priv = {n for n in read if REGISTRY[n].privilege is Privilege.PRIVILEGED}
    declared = set(hdr.declared_privilege_budget["names"])
    if not priv <= declared:
        _fail(f"{actor}: undeclared privileged read {sorted(priv - declared)}")
    if hdr.declared_privilege_budget["class"] == "zero" and priv:
        _fail(f"{actor}: declares zero-privilege but read {sorted(priv)}")
```

Dispatch order — log the frame, stage, assert, invoke, attest, commit:

```python
def run_tick(log, critic, obs, step) -> Outcome:
    row  = log.frames.append(pack_obs(obs, log.obs_schema), action, reward, done)
    hdr  = log.fold_decider_header()
    view = build_view(hdr.view_names, unpack_obs(row, hdr.obs_schema),
                      step, hdr.view_schema_sha256)
    log.stage_decision(step, view)
    assert_view_reconstructable(log, view)            # THROWS before anything runs
    try:
        outcome = critic.evaluate(view)               # the only path a critic reads
    except Exception as e:
        outcome = Outcome.error("exception", str(e))  # a field, never a raise
    assert_privilege_budget(log, view, actor="critic")
    log.commit_decision(step, outcome, view.read_names, critic.state_digest())
    return outcome
```

The recovery executor takes a `FeatureView` and never an obs dict, and runs the same `assert_privilege_budget(..., actor="recovery")`.

---

### 4. `replay.py` — reconstruction, honestly scoped

```python
@dataclass(frozen=True, slots=True)
class ReconstructedDecision:
    step: int; features: dict[str, float]; read_names: tuple[str, ...]
    privilege_by_name: dict[str, str]; state_digest: str; outcome: str
    view_digest: str; bundle_sha256: str; feature_registry_sha256: str

def reconstruct_decision(log, step) -> ReconstructedDecision:
    """Exact. Reads decision/block only; never touches frames.zst."""

def audit_episode(log) -> AuditReport:
    """Re-derives every decision from frames.zst through unpack_obs + REGISTRY
    and compares digests. Reports n_ok / n_mismatch. No derivable_fraction --
    with no retention it is identically 1.0, i.e. a metric that cannot fail."""

def replay_critic(log, bundle) -> None:
    """Replays the bundle over the logged views from row 0 and asserts the
    state_digest column reproduces exactly. Necessary because search.Trigger
    carries dwell and arm_after, so 'what the critic saw' is features PLUS
    internal counters. This is what makes lead-time statistics falsifiable."""
```

---

### 5. Tests — the acceptance, in build order

1. `test_invariant_catches_injected_feature` — **GOAL.md acceptance #4.** After staging, a wrapper adds `privileged.cube_z` to the view; `assert_view_reconstructable` must raise `InvariantError`. *Verified working in a spike against a real robosuite episode; also verified that the original spec's cached-digest form does **not** raise.*
2. `test_dict_view_would_leak_attestation` — asserts `FeatureView` is not a `dict` subclass and that `.get()`, `.items()` and `{**v}` all populate `read_names`. Guards the mechanism the privilege budget rests on.
3. `test_invariant_catches_undeclared_privilege` — zero-privilege bundle reads `privileged.cube_z`; must raise.
4. `test_recovery_privilege_is_attested` — a recovery reading `privileged.cube_z` under a zero-privilege declaration must raise. *This is the round-1 bug, as a test.*
5. `test_roundtrip_pack_unpack_is_bit_exact` — `unpack_obs(pack_obs(obs)) == obs` in f32, every key, every shape.
6. `test_extractor_bit_exact_across_call_sites` — write-path and audit-path derivation of one frame produce identical f32 bytes.
7. `test_log_reloads_and_audits_clean` — run one real seeded episode end to end, reload from disk, `audit_episode` returns `n_mismatch == 0` over all 100 steps.
8. `test_replay_reproduces_critic_state` — `replay_critic` reproduces the whole `state_digest` column.
9. `test_assertion_cost_under_budget` — 1000 assertions, mean under 500 µs (measured 7.7 µs for the digest path; the threshold is a regression guard, not a target).
10. `test_frames_digest_independent_of_write_chunking` — write one episode's frames in one call and in ten; `frames_sha256` unchanged.
11. Reconcile `tests/test_determinism.py` — it currently hashes `np.round(flat, 9)` while `env.py` claims bit-identical. Run the unrounded digest on two same-seed reruns, find out which claim is true, and make the docstring and `frames_sha256` agree with it.

**Tonight's observable result**, which is the whole point: `python -m governor.episode.demo --seed 3` runs one real `Lift`/Panda episode, writes `runs/dev/episodes/g0-dev-003/{events.jsonl,frames.zst,manifest.json}`, prints `success=False steps=100 invariant_checks=100 invariant_failures=0 bytes=2.1KB`, and `pytest tests/test_episode_log.py` goes green with test 1 proving the invariant blows up. My spike already ran that chain, so this is porting verified code into four files, not new design risk.
