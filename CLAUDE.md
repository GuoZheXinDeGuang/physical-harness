# Operator handbook — for every agent working in this repository

Read `GOAL.md` first. It is the project's north star; every piece of work you
do should move toward it. **Never edit GOAL.md** — all agents align on the same
fixed direction, and only the operator may change it. Current state lives in
the board (query it), architecture in `ARCHITECTURE.md`.

## Hard rules

- **First principles.** Reason from what the system must guarantee, not from
  what similar projects usually do.
- **Simplest thing that works.** Prefer deletion over addition; no speculative
  abstractions; a task-name `if` branch in a generic path means the design is
  wrong — add a card instead.
- **Docs stay true, and there are few of them.** When you change behavior,
  update the doc in the same commit; when a doc goes stale, delete or rewrite
  it. A wrong doc is worse than no doc. `docs/` is a **closed allowlist**
  (`docs/README.md`, enforced by `tests/test_docs_allowlist.py`): a file ships
  only if a stranger cloning this repo would be worse off without it. Round
  plans, scout reports, design drafts and acceptance write-ups go to
  `local-archive/docs/` — never to `docs/`.
- **Evidence over demos.** A claim is worth exactly the sealed evidence behind
  it. An honest null and an honest NO-GO are deliverables, not failures.
- **Never game a gate.** No tuning thresholds, swapping gates, or cherry-picking
  seeds to manufacture a promotion. If the semantics of a trigger change, old
  numbers are dead — re-earn them.
- **Audit oracles before trusting them.** Simulator-provided predicates have
  lied to us before (a near-always-true grasp check). Verify a predicate
  discriminates before using it as a success criterion.
- **Rendering is live state, not evidence.** Frames and screenshots never enter
  the session-log chain.

## Your one execution door

`submit_brief(brief, session=...)` — drop a work order into a resident
runtime's inbox. A brief is a **pure selector plus budgets**; providers are
chosen server-side from manifests, and any extra key is rejected.

```
{"kind":"task", "task":"kitchen_thaw", "seed":420011, "max_replans":3, "max_actuations":40}
{"kind":"campaign", "campaign":"stack", "dev":[41000,41999], "heldout":[42000,42199]}
{"kind":"rsi", "task":"kitchen_thaw"}
```

`kind:"rsi"` is the generic self-improvement chain (evolution mode only): the
minimal form needs only a task name; the runtime runs allocate → calibrate →
gates → prereg → dev → held-out → install by itself. See `docs/rsi-mechanism.md`.
Three things are never yours to pick:

- **The target node comes from first-death attribution**, not from you
  (an explicit `node` override is recorded in the verdict).
- **Thresholds come from `plugins/rsi/stats/search.py`**, not from you.
- **If an embodiment has no registered recovery primitive, say "nothing to work
  with"** — never improvise an action to fill the gap.

`session` picks the robot: `session-main` (robosuite) / `session-robocasa`
(kitchen; separate interpreter and dependencies). Unsure? Call `sessions()`.

**Submitting does not block.** `submit_brief`/`run_task` hand back a handle;
`brief_status(brief_id, wait_ms=…)` is the ONE call that says where the brief is
(queued/running/**stalled**/done/failed/cancelled, with queue position and how
long the thing ahead has been running) and what it did. Waiting out `wait_ms` is
not an error — it means "still running", so wait again. **`stalled` means nobody
will ever claim it**: that session has no live runtime, `runtime.reason` says
why, and waiting is pointless — say so and stop polling. Never rebuild a brief's
fate by hand from `runtime_events` + `session` + `session_progress`.
`cancel_brief(brief_id)` stops one; it lands at a node boundary, seals as
`runtime.task_cancelled`, and is never counted as a failure.

**When anything looks wrong, call `health()` FIRST** — one dict covering every
session's runtime liveness (asked of `/proc`, never of its own leftover
`runtime_status.json`), mode, heartbeat age, inbox backlog and crash orphans,
plus the console and the model server. Read its `problems` list. Never report
"the runtime is alive" from `runtime_status()` — that file outlives the process
that wrote it, and doing so is exactly how a brief sat queued for 21 hours.

Read results through `runtime_events` / `session_progress` / `store` /
`heldout` / `vault_node` — do not reassemble conclusions from raw files under
`runs/`; sealed artifacts are chain-verified and the board is how you read them.

## The seed ledger is irreversible

- Every seed block burns **once**. Reusing a burned block as a gate or held-out
  poisons the conclusion — the whole result is void.
- Calibration blocks are the exception: never a gate, always re-runnable.
- Scratch seeds (< 542000, outside any declared block; 42xxxx/43xxxx by
  convention) never burn the ledger. Seeds ≳ 542479 overflow and crash.
- Held-out is scored **once**, and only when something actually promoted.

## The two-state law

**Execution mode** mounts frozen SkillRecords and writes nothing; **evolution
mode** is the only place experiments happen. Sealed artifacts (prereg,
calibration, held-out results) are immutable — if one is wrong, run a new
round; never edit the old one.

## Known traps

- `python -m pytest`, never `bin/pytest` (its 59 collection errors are all
  spurious). cwd is always the repo root — anywhere that can see
  `sims/robocasa/`, the import silently resolves to a namespace package and
  374 kitchen envs never register.
- Base-lane test counts changed ⇒ refresh `docs/base-gate.md` + `README.md`
  **in the same commit**.
- `STATUS.md` and `progress.md` are the operator's local, untracked ledgers —
  never `git add` them; the runtime treats a missing ledger as nothing burned.

Before you start: skim the board's recent rounds. This repository's history is
full of "looked right but a fake predicate said so" lessons — reading for five
minutes is cheaper than rediscovering one.
