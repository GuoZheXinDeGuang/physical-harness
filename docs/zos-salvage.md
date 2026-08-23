# zos salvage — the design capital worth keeping (2026-08-23, charter W4 / R10)

zos is retired (see the tombstone at the top of `Z-Robotics-Lab/zos/README.md`).
its duties split to two places already living in this line of work: the operator
surface goes to the dsh cockpit (round 95), and the OS/evidence kernel is this
repo. W4 asked the audit to "confirm nothing useful was lost". it wasn't — but
the useful part was never the 16k lines of runtime. it was a handful of hard-won
design decisions, each paid for by a real incident on real hardware. this
document is that capital, written down so it survives the repo.

**v4.2 framing (this is the load-bearing correction).** the round-96 design first
ruled the real-robot ACT half "shelved". v4.2 overturns that: sim and real are two
embodiment cards on the SAME `embodiment.env` seam. so this document is not an
obituary for the actuation half — it is the **requirements spec for the future
`actuation:real` embodiment card** and its separate authenticated runtime. every
"→ card" note below is a thing that card MUST reproduce; the sim runtime keeps its
`actuation:real`-refusing guard (GOAL v4.1) precisely so that the day someone
builds this card, they build it against these requirements, not a brief away.

references below are `zos/<file>.py:<line>` in the frozen zos repo at
tag `zos-retirement-2026-08-23`.

---

## 1. authority FSM — the arbiter (`zos/authority.py`)

**what.** one finite state machine, `Auth` ∈ {IDLE, NAV, SERVO, ARM, ESTOP},
guarding who may write the two shared resources: the base velocity channel and
the arm's exclusive `can0` bus. legal transitions are a static table
(`_LEGAL`); ESTOP is reachable from everywhere and left ONLY by an operator
`resume()`.

**why it exists (physics, not taste).** two writers on `/way_point` froze the
robot — 2465 zero-length paths, 2026-07-15 bag. the arm and the base share one
CAN owner. an FSM is the smallest thing that makes "two owners" unrepresentable.

**the non-obvious rules, each a scar:**
- **stops are never refused and never latch-clearing** (`STOP_TOOLS`,
  `authority.py:64`, `check()` line 87). `nav.stop` declared `Auth.IDLE,
  reads_only=False`, so before the STOP_TOOLS carve-out it was refused by the very
  ESTOP latch it was meant to run under — while the base was still driving because
  the dog-side estop had failed. a stop that waits on a prompt is not a stop.
- **ESTOP is operator-only to clear** (`resume()`, line 139). nothing in the
  system may auto-resume a latch. `release()` explicitly refuses to clear it
  (line 126), and the self-check exercises the exact path where owner matches AND
  state is ESTOP so the guard is provably load-bearing (line 186).
- **re-entrant holds** (`hold()`, line 148): a nested `hold` of the same
  (state, tool) must not release the outer one.
- **describe() refuses to lie** (line 160, and the `_WHO` comment at line 36):
  the panel says "must not issue commands", NOT "zero-velocity latch", because
  nothing actually engages a latch — claiming one would be a false sentence in the
  operator's prompt.

**→ actuation:real card requirement.** any real embodiment that owns more than one
actuator behind one bus MUST carry an arbiter of this shape: static legal-transition
table, operator-only ESTOP release, unconditional stop path, reason-string refusals
(not exceptions — an exception ends the turn, a sentence re-plans). the sim runtime
has no arbiter because sim has no shared physical bus; this is exactly the kind of
capability that distinguishes the two cards on the one seam.

---

## 2. risk-derivation tree — declarations inherit, never drift (`zos/skills.py`)

**what.** a skill tree where each tool "hangs" on a branch, and its risk /
preconditions / authority are DERIVED from the path, not declared per-tool.

**why (safety-relevant silent drift).** risk and preconditions used to be two
dicts keyed by tool NAME (`gate.py`'s old `RISK` / `PRECONDS`). adding a skill
meant editing three places; forgetting one was silent AND safety-relevant —
declare a tool `move`, let the gate believe `read`, and it stops asking before it
moves 40 kg of robot (`skills.py:9`).

**the three inheritance rules, all failing toward "does not move"
(`skills.py:15`):**
1. **preconditions ACCUMULATE root→leaf** (conjunction; a subtree may only ADD
   constraints). `pose_fresh` hangs on `nav.goal`/`nav.rel`, NOT on `nav`, because
   `nav.stop` is the software e-stop and a stop that waits on fresh odom is not a
   stop.
2. **risk is DERIVED, never a Tool field:** `reads_only AND Auth.IDLE ⇒ "read"`
   (a structurally checkable claim — it holds no actuator); otherwise the deepest
   declaring branch. a leaf cannot talk its own risk down, and its AUTHORITY is a
   floor under its risk (a leaf holding `Auth.ARM` is at least "arm").
3. **authority:** the leaf's explicit value wins, EXCEPT an arm-risk skill always
   acquires ARM — otherwise it would drive the arm without the arbiter ever
   recording it.
- **undeclared ⇒ refused at validation**, naming the tool (I12). not "run with
  maximum ceremony": the failing direction stands still.
- **one rank ordering** `read < move < infra < arm` (`skills.py:50`), taken as a
  max over a whole graph so the operator is asked once. define a second ordering
  anywhere and it drifts.

**→ where it lands.** the harness already carries the structural half of this: a
plugin's `manifest.toml` `[task_bindings]` are DATA parsed not imported, resolved
as a union at boot (R5), so "one table, no drift" is the manifest discipline. the
*risk-derivation* half — deriving a permission class from where a capability hangs,
failing toward inert — is the actuation:real card's authorization model; it does
not belong in the sim base (sim has no permission ladder), but the card MUST NOT
reintroduce a hand-kept name→risk table.

---

## 3. permission ladder — should this happen at all (`zos/gate.py`)

**what.** the layer between "the model decided to actuate" and the robot moving.
two mechanisms (`gate.py:10`): **preconditions** (a skill refuses itself by reading
the world — "the pose is not fresh", "the target is a guess not a measurement")
and **permissions** (the robot asks before it moves, the way Claude Code asks
before it writes a file).

**why (the 93 m drive).** 2026-07-30 16:24: the operator typed `look`; zos started
the nav stack, servo'd the live arm (`live=True` the model set by itself), and
commanded a 93 m drive across a 30 m office — because one wrong ROS topic made
`depth()` return None, a ground-plane fallback fired on a nearly level ray, and
NOTHING downstream asked whether a 93 m goal was plausible (`gate.py:5`).

**the six-step ladder** (`Permissions.check`, `gate.py:487`), deliberately mirroring
zeno's `PermissionContext` / Claude Code's `hasPermissionsToUseTool`, adapted for a
robot:
1. **HARD DENY, no flag overrides:** (a) anything but reads while ESTOP-latched;
   (b) live arm motion the OPERATOR did not unlock this session — a model-chosen
   `live=True` never counts (that is how the incident started).
2. operator deny set → DENY.
3. resolved risk == "read" → ALLOW, never asks (the tool's own `reads_only` is NOT
   re-consulted here; step 2's derivation already refused to believe it on an
   actuator-holding tool, and a second laxer reading is how an ARM skill walks past).
4. session always-allow FOR THESE ARGS (`allow_key`) AND risk != "arm" → ALLOW.
   "allow all session" may NEVER cover the arm.
5. yolo AND risk ∈ {move, infra} → ALLOW ("arm" is deliberately absent).
6. default → ASK, with a one-line summary of what will happen.

`check_graph` applies the SAME six steps ONCE to a whole plan at its highest
resolved risk (I8/I9) — the graph is not a way around the gate, it is the same gate
asked once (`gate.py:562`). every refusal is a REASON STRING fed back to the model,
never an exception.

- **the `resume` subtlety** (`gate.py:77` UNTREED_RISK): `resume` posts
  `/estop_release`. classed "infra" it was covered by BOTH step 4 and yolo, so yolo
  silently released an e-stop with no prompt. it is classed "arm" — the ONE level
  both steps 4 and 5 exclude — not because it touches the arm but because "arm" is
  the only level with the property wanted (never auto-approvable).

**→ actuation:real card requirement.** preconditions (read the world, refuse
before the body runs) + a permission ladder with an un-overridable hard-deny tier,
an args-scoped allow that never reaches the arm, and reason-string refusals. the
operator ASK surface routes through the dsh cockpit (W3). the sim runtime has no
ladder — nothing to ask permission of — which is why this is card capability, not
base.

---

## 4. verify — the model never judges its own success (`zos/verify.py`)

**what.** the model calls `verify(predicate="<expr>")`. the expression is evaluated
in a sandbox whose namespace holds ONLY oracle callables reading ground truth the
actor cannot forge (live odometry, CAN telemetry, the 8766 status doc). the action
chain comes from the LOOP, never the model — this is the one module whose premise
is that the actor cannot author its own evidence (`verify.py:200`).

**the AST allowlist and the operator-not-operand rule.** `_screen()`
(`verify.py:99`) walks the AST and REFUSES any node outside `_OK_NODES`
(`verify.py:56`); an invented name silently evaluates falsy, which makes "the robot
failed" indistinguishable from "the model hallucinated a predicate" — z-agent
shipped exactly that bug (`verify.py:8`).

**the resource-exhaustion lesson (four days, measured, not imagined)
(`verify.py:38`).** `9**9**9**9 > 0`, written by the model, ran on the 4090 at
99.9% CPU / 16 GB **for four days** (2026-07-30 → 08-03), swapped the box, and
starved the kernel's network path until ping to the NUC showed 48% loss / 1.6 s
RTT, `/registered_scan` fell to 0 Hz, and rviz rendered empty. the operator's bug
report was "rviz won't open." the fix that failed: excluding `Pow` alone
("called the class closed. it was not"). the fix that failed harder: excluding by
OPERAND SHAPE — `approach_phase() * 200000000` walks straight through it, because a
static check cannot see what an oracle returns, and the eval cannot be sandboxed at
runtime (oracles read live ROS/HTTP, cannot move to a subprocess with an RLIMIT, a
thread cannot be killed). **the rule that held: ban the OPERATOR, not the operand.**
a physical predicate compares readings (`battery() > 20`); in the whole repo not one
predicate multiplies. `Pow`, `Mult`, `Mod` are absent from `_OK_NODES`; the
self-check asserts each bomb is REFUSED in < 0.1 s (`verify.py:282`), because a test
that only checks `not ok` passes just as happily after eval melts the box.

**non-bool is not a criterion (`verify.py:137`).** `bool()` used to wrap the result,
which made whole predicates tautologies: `approach_phase()` returns a non-empty
string on EVERY path ("unknown" included), so it was True forever — a stationary
robot under "1 steps all verified". a non-bool oracle is fine INSIDE a comparison;
it may not BE the whole predicate. **rejected, not failed:** a hallucinated or
malformed predicate records no `Step` — it is evidence of neither success nor
failure (`verify.py:158`). and zero steps verified ≠ success (`verdict()` line 165).

**→ where it lands.** the "actor cannot author its own evidence" premise IS the
harness's held-out/blind-twin discipline and the `FeatureView` privilege accounting.
the *online* half — an AST-screened oracle predicate over unforgeable live telemetry,
with the operator-not-operand rule — is the actuation:real card's online verify
surface. the AST allowlist and the < 0.1 s refusal test are copy-forward artifacts:
do not re-derive them, port them.

---

## 5. not-measured discipline — uncomputable is not permission

**what.** a family of decisions that all refuse to let "we don't know" quietly read
as "it's fine".

- **not-measured vs measured-empty are different facts** and must not be
  conflated. `map_reach()` returns None for three reasons (no pose / landmark list
  never read / read-and-genuinely-empty) and its callers each re-read `world.places`
  to say WHICH — because only the measured-empty case (fresh mapping, no landmarks
  yet) may legitimately fall back to the `MAX_GOAL_M` constant; an unreachable NUC
  must NOT silently widen a motion guard from "furthest landmark" to a guessed
  building-size constant (`gate.py:113`, "UNCOMPUTABLE IS NOT PERMISSION"). world.py
  catalogues six sites of this same conflation (`world.py:201`); the one inside the
  safety gate is the sixth.
- **never fabricate a reading.** stale battery → `stack.battery = None`, never a
  made-up number (`world.py:37`); a pose older than `ODOM_STALE_S` renders stale and
  is not trusted (`world.py:36`). the failing direction is always "less claimed",
  never "invented".
- **the advisory boundary — the IRON RULE (`zos/evidence.py:16`).** the harness's
  measured SkillRecords are consumed by zos as DISPLAY ONLY. nothing out of the
  evidence reader may enter `gate.check_pre` or `skills.resolve`: measured
  preconditions live in robosuite feature space, zos preconds live in World
  pose/conf space — not interchangeable, so the only safe coupling is advisory prose.
  a record may annotate a catalogue line; it may NEVER relax a refusal. and
  `heldout_judgement_established=True` is ONE held-out block — it annotates as
  "established (1 block)", never "proven" (settled needs ≥ 3). every link in the
  chain degrades to an empty index with one honest log line: less decoration, never
  a crash and never a fabricated annotation.

**→ where it lands.** this is BASE discipline and largely already home: the harness's
"headline needs ≥ 3 blocks", the held-out-established one-block field, the
content-hash-or-it-didn't-happen rule, and `plugins/graphs`' `WorldSceneGraph`
staleness propagation (a frozen pose that reads as live is the exact defect) all
encode it. keep it as the line the actuation:real card's advisory coupling must not
cross: real-world evidence advises the planner and the operator; it never edits a
gate verdict.

---

## 6. World state model — a robot agent's context is 3D world state, not a transcript (`zos/world.py`)

**what.** one `World`, one frame (map), re-rendered into the prompt every turn from
live sensors: pose, landmarks, objects, authority, stack health. the model never
calls a tool to ask "where am I / what do I see" — the world block is the tail of
every turn (`world.py:1`).

**the design argument (worth keeping verbatim).** a coding agent's state lives in
the transcript, append-only. a robot's state lives in the world and *changes by
itself*. so the world is re-rendered every turn and it OVERRIDES anything said
earlier in the conversation about position, objects, or authority; coordinates from
old messages are void (loop `SYSTEM` prompt, `loop.py:52`). staleness is first-class
throughout: `ODOM_STALE_S` / `BATT_STALE_S` / `NAV_SILENT_S` / `DOG_STALE_S`
(`world.py:36`) each say when a channel stops being trustworthy rather than letting a
frozen value pass for fresh.

**→ where it lands.** already partly home: `plugins/graphs.WorldSceneGraph`
(`plugins/graphs/__init__.py`) is the ported normalizer that turns a
`World.snapshot()` dict into the harness scene-graph schema — bearing math ported
byte-for-byte from `zos.world.relative` (`world.py:471`), goldens frozen 2026-08-22,
staleness propagated. it consumes a plain dict, imports neither zos nor numpy, and is
mounted via the `robot-world` bundle. that class is the actuation:real card's
`graph.scene` provider: the card supplies the live `World.snapshot()`, the harness
already knows how to read it.

---

## 7. the real-robot ACT half — requirements for the actuation:real card (`zos/tools/`, `zos/loop.py`)

not shelved (v4.2). these are the four actuator surfaces the future card must
provide behind its own authenticated runtime, and the disciplines each one paid for:

- **see** (`tools/see.py`): the brain eats the JPEG directly and emits
  [0,1000]-normalised boxes back-projected through aligned depth into map-frame
  `Obj(x, y, z)` — one hop fewer than "describe the image", and z survives.
- **nav** (`tools/nav.py`): three hardware-paid rules — (1) SINGLE `/way_point`
  writer (two writers = 2465 zero-length paths, frozen robot); (2) `nav.sh` EXIT
  CODES LIE (`... || true` returns 0 on failed service calls) — grade motion on
  `/state_estimation`, which the actor cannot forge, never on an exit code;
  (3) the robot is BLIND BEHIND (front-mounted Mid-360, pitched 20° down) — reverse
  is an escape hatch (≤ 1.5 m, ≤ 0.3 m/s), never a driving mode. the NUC's
  `cmd_vel_guard` is the clamp authority (0.6 m/s, 1.0 rad/s, 0.4 s deadman);
  the client mirrors it — a mirror, not a substitute.
- **manip** (`tools/manip.py`): SINGLE CAN OWNER — zos never touches `can0`, only
  sends HTTP routes to the one process that owns the arm (8766); driving the arm
  around that process = two owners. auth is three exact-match headers, body strict
  JSON ≤ 512 bytes.
- **dog** (`tools/dog.py`): THE POSTCONDITION IS WHY THE FILE EXISTS.
  `nav_bringup(up)` could only ever say "command dispatched" because zos couldn't
  see posture — so the model filled the silence with "the robot has stood up" while
  the dog had REFUSED (`move_state TRANSIENT_FAULT, last_robot_code -1`). nothing
  grades itself on a service return code; it polls what the ROBOT says about itself
  (`/go2w/posture_state`) and quotes the robot's own sentence verbatim when the
  answer is no.

**the loop** (`loop.py`): one flat ReAct turn — render world fresh, tail-placed for
prompt caching, gate motion on the arbiter, feed every refusal BACK to the model
instead of raising.

**→ the card's contract.** all of the above mounts on `embodiment.env` (and a real
`embodiment.ground_truth` for the verify oracles), declares `actuation:real` +
`needs_sim=false` in its manifest, and runs ONLY under a separate authenticated
runtime. the sim runtime refuses to mount `actuation:real` (GOAL v4.1) — so a real
actuator is never one brief away from a sim session. the disciplines above (postcondition
over return code, single owner, single writer, blind-behind, mirror-not-substitute
clamps) are the card's acceptance criteria, and each has a hardware calibration knob
(`ZOS_MAX_GOAL_M`, the boot/discovery/probe timeouts) that a real deployment must
re-tune — a minimal model cannot see a drifting clock or an unreachable NUC.

---

## what already came home, and what is deliberately NOT salvaged

**came home:** `WorldSceneGraph` + the ported `relative` bearing math and staleness
(`plugins/graphs`); the evidence advisory-reader pattern and its IRON RULE
(harness-side as the ≥3-blocks / held-out-established discipline); the "actor cannot
author its own evidence" premise (blind-twin / held-out / `FeatureView` accounting);
manifest task-bindings as the one-table-no-drift replacement for the risk/preconds
dicts.

**not salvaged, on purpose:** the 2339-line `cli.py`, the 1508-line `render.py`, and
the glue forms that shelled two runtimes into each other — M0 proved that need and
proved the glue can't hold (GOAL.md: "胶水永远会漏"). the operator surface is dsh
now. none of that is design capital; it is the plumbing the capital was trapped in.

the code stays readable at tag `zos-retirement-2026-08-23` in the frozen repo; this
document is the part meant to outlive it.
