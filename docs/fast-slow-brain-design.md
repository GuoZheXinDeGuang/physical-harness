# Fast/slow brain — a VLA that shares the seam with the scripts

> Design, 2026-08-29. Slow brain: the console's VLM (DeepSeek V4-Flash-Vision-Exp)
> already generates the node graph. Fast brain: openpi π0.5, fine-tuned per
> segment, mounted as a card **beside** the scripted drivers, never replacing
> them. Evidence discipline (`docs/rsi-mechanism.md`) is untouched.

## Why coexistence is the whole design

Today a segment executes through one hand-written driver. That driver is
`code as policy`: a four-phase state machine whose heights, durations and gripper
schedule are constants, whose wrist rotation is hard-zero, and whose object
estimate is read once at t=0 and never refreshed
(`plugins/policies/drivers.py:FrozenPolicy.act`, `harness/spec_tabletop.py`).

That shape explains the measured wall exactly. `nav` succeeds because driving to
a pose survives open loop. `grasp` reaches 49% because recovery primitives patch
it. `place` is 0/22 because "seat a held object onto another object" needs the
three things the script structurally lacks: a live relative pose, wrist control,
and contact awareness.

So the VLA is not a better script — it is a **different provider for the same
seam**. Both must be mountable at once, because:

1. **The paired gate needs both.** The RSI comparison is challenger vs incumbent
   on the same seeds. If the VLA replaced the script, there would be no
   incumbent to pair against.
2. **Only one segment is being treated.** Attribution picks a node; the other
   five segments keep their scripts. A whole-task swap would change six
   variables to test one.
3. **A frozen SkillRecord must stay reproducible.** An installed record names
   the provider it was measured with. If the provider disappeared, the record
   would be unreplayable — and `scripts/parity_check.py` would have nothing to
   byte-compare.

## The seam it plugs into

The dispatch path already routes per segment, so no kernel change is needed:

```
plan node {kind: "segment", skill: "place"}
  → SEGMENT_SPECS["place"]              # mission card, pure data
  → EpisodeSpec(task="place_meat", …)
  → workload._governed_segment
      → driver.enter_segment(env, seg_spec)   # heterogeneous episodic driver
      → gov.governed_segment(...)              # bundle/recovery governance
```

`SEGMENT_SPECS` is a **pure-data table in the mission card**. Choosing a
different executor for one segment is therefore a manifest edit, not a code
edit — the same property that makes a task binding swappable.

**The change**: a segment spec may name its own executor.

```toml
# plugins/mission_kitchen_thaw/manifest.toml
[segment_executors.place]
ref = "plugins.policy_vla_remote:provider"
```

Absent that row, the segment keeps the mission's own driver — so every existing
mission is byte-identical, and `enabled = false` on the VLA card means the base
fold and its sha never move.

## The three layers, and who owns what

| Layer | Who | Changes here? |
|---|---|---|
| Task → node graph | VLM (DeepSeek V4-Flash-Vision-Exp, or local Qwen) | no — `planner_vlm` already ships |
| Node → segment spec | mission card's `SEGMENT_SPECS` (pure data) | +1 optional executor ref |
| **Segment → actions** | **scripted driver *or* π0.5** | **this design** |
| Did it work | card-declared predicate on live state | no |
| Repair on failure | `[recoveries.*]` folded from the embodiment card | no |

The slow brain's contract with the fast brain is exactly the segment spec — a
sub-goal plus budgets. It does not send free-form text, and it never sends
actions. That boundary is why the two brains cannot fight: one picks *which
segment and in what order*, the other decides *how to move within it*.

## The transport is already built

`plugins/policy_vla_remote/` vendors StarVLA's websocket+msgpack protocol layer
(zero torch imports) and adds a **handshake reconciliation gate**: the card's
manifest declares the training observation contract (image size, view order,
chunk length, unnorm key) and reconciles it against the server's first-frame
metadata at connect. A mismatch fails at mount, not 300 steps into an episode.

openpi's `scripts/serve_policy.py` speaks that same protocol. So the wiring is:

```
harness (base venv)  ──websocket+msgpack──▶  openpi venv (JAX/torch, π0.5)
   policy_vla_remote card                      serve_policy.py
   handshake gate ────── checkpoint digest ───── /v1 metadata
```

**This is also the freeze mechanism.** A SkillRecord stores the checkpoint
**digest**, never the weights (GB-scale). At execution time the handshake proves
the server is serving *that* checkpoint. A different checkpoint fails the mount
— the same guarantee the frozen-SkillRecord rule gives for scripted policies.

## Data: RoboCasa ships its own demonstrations

Verified in `sims/robocasa/robocasa/utils/dataset_registry.py`: every atomic task
carries both a `human_path` (teleoperation) and an `mg_path`
(MimicGen-generated), fetched by `robocasa/scripts/download_datasets.py`.

This removes the blocker that filtered behaviour cloning would have hit. Cloning
our own successful rollouts cannot work for `place` — the script has never
succeeded there, so there is nothing to imitate. RoboCasa's demonstrations do not
depend on our scripts being good.

**Open number, and it gates everything below**: how many demonstrations exist for
the place-shaped atomic tasks, and whether their action space matches our
`PandaOmron` 12-dim mount. Measure before training.

## Order of work

Each step ends in a fact, and a step that produces a negative fact stops the
chain honestly rather than proceeding on hope.

1. **Audit the predicate first.** This repo has scar tissue from a near-always-true
   grasp check. A predicate that cannot discriminate would poison the training
   filter and bake the lie into weights, where it is far harder to find than in a
   rule. Show `place`'s predicate separates success from failure on replayed
   demonstrations before any data is collected.
2. **Count the data.** Registry → how many human + MimicGen trajectories for the
   place-shaped tasks; confirm the action space matches. Too few is a valid stop.
3. **Trajectory capture** (only if we need our own): `plugins/task/workload.py`
   currently discards obs/action. Store to a content-addressed `datasets/` root,
   referenced by digest from the training prereg, **outside the sealed chain** —
   it is data, not evidence, and it is large.
4. **Training card** in its own venv (the sim-card isolation pattern), running
   openpi's `train.py`/`train_pytorch.py` with a LoRA recipe. Output: checkpoint +
   digest.
5. **Wire the executor**: `[segment_executors.place]`, flip the VLA card on, serve
   the checkpoint, prove the handshake gate rejects a wrong one.
6. **Gate it**: scripted driver as incumbent, fine-tuned policy as challenger,
   same seeds, paired → blind twin → held-out once. Promotion writes a
   SkillRecord naming the digest. **A NO-GO is a normal outcome; thresholds do
   not move to manufacture one.**

## Hard constraints

- **One 24 GB card.** π0.5 LoRA is ~22.5 GB, so the backbone LLM must be stopped
  during training (the operator rail has the switch), and training cannot share
  the card with a calibration's ten EGL workers.
- **Checkpoints are GB-scale.** Digest into the SkillRecord and the chain; weights
  live beside, addressed by that digest.
- **The clock changes units.** Minutes become hours, so the dev generation count
  shrinks — get one generation through end to end before discussing many.

## What this buys the paper

The comparison becomes a controlled one: **the same task, the same seeds, the
same governance, one segment swapped from hand-written code to a learned policy.**
Both arms run under the same predicate and the same recovery repertoire, so the
delta is attributable to the executor alone — which is the claim a
"code as policy vs learned policy" result needs and rarely has.
