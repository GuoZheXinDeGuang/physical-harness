# GOAL — π0.5 as a segment executor on RoboCasa

> Campaign goal, opened 2026-08-29. Not `GOAL.md` (that one is fixed and states
> the project's north star; this states one campaign under it). Design:
> [fast-slow-brain-design.md](fast-slow-brain-design.md).

## The goal

**Get π0.5 to execute one RoboCasa segment inside a real episode, under the
existing governance, and produce an honest paired number against the scripted
driver it replaces.**

Done means a number exists. **Promotion and NO-GO both count as done. Only
"no number" is failure.**

## Why this segment, and why one

`place` is where the wall is: 0/22 in the last calibration, while `nav` passes
150/150 and `grasp` reaches 49%. The reason is structural, not tuning — the
scripted driver holds the object's pose from t=0, never rotates the wrist, and
descends by a constant. "Seat a held object onto another object" is not
expressible in that shape.

One segment, because the paired gate compares one variable. The other five keep
their scripts, so a delta is attributable to the executor alone.

## The three gates, in order

Each step ends in a fact. A step whose fact is negative **stops the campaign
honestly** — that outcome is a deliverable, not a failure to be worked around.

### 1. Is there a real signal to learn from?

Two questions, both answerable in hours, both read-only:

- **Does `place`'s predicate discriminate?** Replay demonstrations through it and
  show it separates success from failure. This repo has scar tissue from a
  near-always-true grasp check. A wrong rule is merely useless; **data filtered
  by a wrong predicate bakes the lie into weights**, where it is far harder to
  find.
- **Is there enough demonstration data?** RoboCasa's registry carries `human_path`
  and `mg_path` per atomic task. Count the place-shaped ones and confirm the
  action space matches our `PandaOmron` 12-dim mount.

**Stop condition**: a predicate that cannot discriminate, or too little data.
Either one ends the campaign here with a written finding.

### 2. Does a fine-tune produce a servable checkpoint?

LoRA on openpi, in its own venv, targeting the 12-dim action space. Output: a
checkpoint plus its digest, served by `scripts/serve_policy.py`, and a proof that
`policy_vla_remote`'s handshake gate **rejects a wrong checkpoint** — that gate is
the freeze mechanism, so it has to be shown working, not assumed.

**Stop condition**: training does not converge, or the served policy cannot
complete the segment even once in a smoke run. Report the failure with its
evidence.

### 3. What does the gate say?

Scripted driver as incumbent, fine-tuned policy as challenger, **same seeds**,
paired → blind twin → held-out scored once. Promotion writes a `SkillRecord`
naming the checkpoint digest.

**Thresholds come from `plugins/rsi/stats/search.py`. They do not move.** No
swapping the predicate, no re-picking seeds, no re-scoring held-out.

## Boundaries this campaign does not cross

- **The RSI skeleton is untouched.** allocate → calibrate → gate → prereg → dev →
  held-out → install, first-death attribution, the seed ledger, the two-state
  law: all unchanged. This campaign adds a proposer and an executor, it does not
  rewrite a gate.
- **π0.5 does not replace the scripts.** It mounts beside them as another
  provider for the same seam, chosen per segment by one manifest row. Without
  that row every mission stays byte-identical.
- **The VLM does not talk to the policy.** What crosses the boundary is the
  segment spec — a sub-goal, a budget, and one card-declared instruction string.
  No free-form text, no actions.
- **Weights never enter the chain.** The `SkillRecord` and the sealed log carry
  the checkpoint **digest**; the weights live beside it, addressed by that digest.

## Known costs

- **One 24 GB card.** π0.5 LoRA is ~22.5 GB, so the backbone LLM stops during
  training, and training cannot share the card with a calibration's ten EGL
  workers.
- **The clock changes units.** A scripted generation is minutes; a trained one is
  hours. One generation end to end before discussing many.
- **Demonstration data is tens of GB.** Content-addressed under `datasets/`,
  referenced by digest from the training prereg, outside the sealed chain — it is
  data, not evidence.
