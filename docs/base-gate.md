# base gate (双层测试)

The **base fast lane** is `pytest -m "not robosuite"` run **ISOLATED** — a fresh
process on a machine where robosuite is genuinely unimportable (the
`embodiment_robosuite` extra not installed, or the import blocked). Never a
subset of a full green run: a full run imports the card, and the
collection-order pollution only stays fixed if the card is truly absent.

The merge gate: capture this snapshot before AND after any base-touching change;
a regression in any field blocks the merge. The eval battery is the RSI-scoring
gate, NOT the base lane.

## snapshot format

```
pass       : <N> passed
skips      : <M> skipped, each with its reason
wall time  : <T>s
AST green  : test_boundaries + test_kernel green (harness-imports-nothing +
             profiles-declarative)
```

## how to run isolated

Any process where `importlib.util.find_spec("robosuite") is None` triggers the
`tests/conftest.py` collection hook to auto-skip the `robosuite`-marked items.
Two ways:

- **venv without the extra**: `pip install -e .[dev]` (base deps only, no
  `embodiment_robosuite`), then `pytest -m "not robosuite"`.
- **blocked import** (in a fresh process): a `sitecustomize.py` that does
  `import sys; sys.modules["robosuite"] = sys.modules["mujoco"] = None`, on
  `PYTHONPATH`, then `pytest -m "not robosuite"`.

## current snapshot (2026-08-29, isolated, robosuite blocked)

```
pass       : 734 passed
skips      : 32 skipped
             [2] test_grasp_geometric.py:141  camera env unavailable
             [1] test_grasp_geometry.py:231   camera env unavailable
             [1] test_reducers.py:171         cloned weights not present
             [1] test_plugin_doctor.py:307    robocasa unimportable (robocasa venv only)
             [4] test_robocasa_card.py         robocasa unimportable (robocasa venv only)
             [12] test_robocasa_drivers.py     robocasa unimportable (robocasa venv only)
             [1] test_robocasa_marker.py:11   robocasa unimportable (robocasa venv only)
             [4] test_robocasa_missions.py     robocasa unimportable (robocasa venv only)
             [1] test_runtime_frame.py         robocasa unimportable (robocasa venv only)
             [1] test_libero_marker.py:15     libero unimportable (libero venv only)
             [2] test_policy_vla_remote.py     policy_remote extra not installed
             [2] test_rsi_workload.py:592,609 runs/campaign-pj-scripted not present
wall time  : ~18.2s
AST green  : 17 passed (test_boundaries + test_kernel)
deselected : 28 robosuite-marked items
```

The +6 over the fresh-clone-deploy snapshot (728 -> 734, skips unchanged at 32)
is runtime LIFECYCLE — a status file must not be able to lie about being alive
(`tests/test_read_session.py` +4, `tests/test_runtime_drain.py` +2). Three times
running, an operator brief was submitted into an inbox no process was serving,
and every face reported "runtime up" because `runtime_status.json` still named a
pid that had exited days earlier. `read_runtime_status` now DERIVES two fields
instead of passing the file through verbatim: `alive`, decided against `/proc`
(argv[0] an existing `python*` file, a later arg naming `harness_runtime.py`,
another resolving to THIS session dir -- structural, because a substring scan for
`harness_runtime.py` matches the shell that is grepping for it, the same lesson
`/proc/<pid>/exe` taught the model-server face), and `heartbeat_age_s`, the
second axis. `discover_sessions` carries the bool as `runtime_alive` so the FIRST
call an agent makes shows the dead inbox. The beat moved out of the poll loop
into a daemon thread — the loop only came back around BETWEEN briefs, so a
runtime an hour into an rsi chain and a runtime dead an hour produced the same
stale badge; a node-boundary stamp would not have covered it either, because
campaigns and rsi chains run in subprocesses the parent only waits on. What is
pinned: a reaped pid reads dead, a live pid that is NOT this session's runtime
reads dead, a never-stamped file ages as `null` and never as `0`, and a clean
exit stamps `stopped_ts` (belt-and-braces; `kill -9` never writes it, which is
why `alive` asks `/proc`). Sim-free (`_process` monkeypatched to a plain sleep),
no seeds burned.

The +11 over the node-level-RSI snapshot (717 -> 728, skips unchanged at 32) is
`tests/test_deploy_profile.py`: the fresh-clone deploy path. The console's
`$DSH_HOME/cordis.patch.yml` used to be a MANUAL copy of a committed file that
carried three absolute paths under one operator's home -- so a clone on another
machine reached a console with no `mcp__physical-harness__*` tools at all and the
agent silently fell back to native bash. The committed file is now a TEMPLATE and
`profiles/dsh/deploy_profile.py` renders it (cockpit runs it before serving).
What is pinned: the template carries no home directory at all, every `PH_`
placeholder is substituted (an unknown one raises rather than reaching the
deployed file), paths come from the repo root and stay valid YAML when that root
contains a space, `.env` moves the model route while a credential in the same
file is never rendered, the console default preset is `physical`, the write is
idempotent and leaves no temp residue, and `settings.yaml` -- which outranks the
patch -- is reported and never written. Sim-free, no seeds burned.

The +13 over the vlm-graph branch tip (704 -> 717) is `main`'s own node-level
RoboCasa RSI work (`e73476b`) arriving through the merge: its kitchen recovery
primitives now reach RSI through the manifest fold instead of the central
registry the branch deleted. `harness/manifest.py` folds `[recoveries.*]` ABOVE
the `enabled` gate for the same reason `third_party` is folded there -- a
second-simulator card is `enabled = false` permanently, so gating its repair
shapes on that flag would mean a card that can never contribute one.

The +26 over the three-track merge point (678 -> 704) is the brief-lifecycle
hardening, measured on the merged tree: the parent-side episode watchdog and the
runtime's own session lock (`hard-watchdog`), the async `run_task` + `brief_status`
long-poll + `cancel_brief` three-checkpoint stop (`brief-lifecycle`), and the
submit-time compatibility advisory (`submit-advisory`). Below, the per-branch
increments those three measured before the merge.

The +4 pass over the model-server snapshot (674→678, skips unchanged) is the
submit advisory (`tests/test_submit_advisory.py`): the session×task warning
`submit_brief`/`run_task` attach when a mission's binding names an embodiment the
target runtime's interpreter cannot import. What the tests pin is that it is
ADVICE — the incompatible brief is still delivered, the compatible one carries no
`warning` key at all (absent, not empty), and every unreadable input (no live
runtime, a pid whose cmdline is not a harness runtime) answers with silence
rather than a guess. The interpreter half is read from a really-spawned process
under this venv, so "robocasa is not importable here" is a fact of the venv
running the test, not a mock. Sim-free, no seeds burned.

The +8 pass over the host-vitals snapshot (666→674, skips unchanged) is
`model_server` — the console's local-model switch
(`tests/test_model_server.py`): the three status states the operator's badge
reads (stopped / `running and not healthy` = the 1-2 minute load / serving,
with `vram_mib` joined on our pid out of the same rows `host_vitals` reports),
and the guards, which are most of the file. The action word is the whole
caller-supplied surface — the launcher is a module constant, and every action
outside `status|start|stop` answers with an error beside a truthful status
while trip-wired `Popen`/`os.kill` prove nothing ran. Identity is
`/proc/<pid>/exe`, not argv, so the launcher's own here-doc in an editor's
command line is not adopted (and never killed); `start` adopts a live server
instead of spawning a second and otherwise spawns the constant argv with
`start_new_session`; `stop` SIGTERMs only a pid that still proves its identity
at kill time and refuses a recycled or garbage one. Three-face byte
equivalence includes the CLI's omitted argument reading rather than writing.
/proc, the health probe and nvidia-smi monkeypatched, sim-free, no seeds burned.

The +4 pass over the keyframes snapshot (662→666, skips unchanged) is
`host_vitals` — the operator's live view of the machine's headroom
(`tests/test_host_vitals.py`): the two-nvidia-smi join on GPU uuid
(`--query-compute-apps` has no index column) with the per-card process list
folded biggest-first, the MemTotal−MemAvailable RAM read, the statvfs disk read,
the three-face byte equivalence with `ts` pinned, and the DEGRADATION contract —
a missing binary, a timeout, a nonzero exit, an unparsable /proc/meminfo, and a
nonexistent disk path each read as an empty list or zeros, never an exception,
because this is live state on the same never-sealed footing as
`runtime_status`. Host reads monkeypatched, sim-free, no seeds burned.

The +12 pass over the submit-face snapshot (650→662, skips unchanged) is
keyframes — stills pinned to opstream events (`tests/test_keyframes.py`): the
`on_emit` hook slot (fires only on an event that LANDED in the feed; a raising
listener is swallowed and never blocks the next one), `arm`'s clearing of
`keyframes/` on the feed's truncate-per-boot horizon, the capture layer
(`KEYFRAME_KINDS` as data, following `--frames`, retracting its env handle at
`close()`, and stopping at the per-boot ceiling instead of erroring), the
three-face byte equivalence of `runtime_keyframes`/`runtime_keyframe` plus the
`storecli serve` seq forwarding, and the INVARIANT: deleting the whole
`keyframes/` directory leaves the sealed session-log byte-identical, the chain
verifying, and only the live faces degraded. Sim-free (a fake env + a fake
sim), no seeds burned. 3 of the 12 are Pillow-gated (`importorskip`) and run in
the harness .venv; a fresh clone without Pillow skips them (see below).

The +4 pass over the RSI-life-sign snapshot (646→650) is the submit CLI face
plus the runtime heartbeat: +2 `test_storecli.py` (`submit_brief`, storecli's
ONE write fn — a passthrough into the shared `board.store.submit_brief` the
MCP tool also delegates to: raw-bytes brief_drop with ZERO validation, and an
unknown session drops nothing) and +2 `test_runtime_drain.py`
(`runtime_status.json` carries `heartbeat_ts` from boot on; the poll loop's
`_heartbeat` re-stamps only that field, atomically, and the board face passes
it through so the UI can age it; a missing status file is a no-op). Sim-free,
no seeds burned.

The +1 pass over the wall-cap snapshot (645→646) is the RSI 取景窗 life
sign: when the spawning runtime has `--frames`, `_run_rsi` passes
`PH_RSI_FRAMES` and exactly ONE calibration pool worker (O_EXCL lockfile
winner, stale locks stolen) mounts the frame overlay and mirrors its episodes
to the session's frame.jpg. Live state, not evidence.

The +2 pass over the planner_vlm snapshot (643→645) is the calibration
per-episode wall cap (`scripts/rsi_campaign.py:EPISODE_WALL_S`, SIGALRM in the
pool worker): 8 workers hung >1h each on pathological cal 0-149 scenes
(2026-08-28) and starved the chain at 138/153. A capped episode returns an
honest `first_death="wall_timeout"` row that `attribute()` counts as
ungoverned — charged to nobody, never a target.

The +10 pass over the five-track merge snapshot (633→643, skips unchanged) is
the planner_vlm card (docs/vlm-graph-paper-plan.md §1 landed): +9
`test_planner_vlm.py` (canned-endpoint generation through validate_plan, the
one-re-ask-then-rejectable parse path, the per-(task, seed) frozen-graph cache,
the replan prompt echoing done nodes, doctor exemption/SKIP, and the committed
stack_vlm binding + fold) and +1 `test_task_seam.py` (a dispatch-time grounding
refusal — the first thing the live VLM fabricated — folds back as a node fault
instead of crashing the loop). All canned-HTTP/fake-rollout, sim-free, no seeds
burned; the base plan sha is untouched (the card declares task_bindings only,
and model_endpoint stays enabled=false — planner_vlm reaches it by ref string).

The +28 pass / +3 skip over the campaign-progress-scan snapshot (605→633,
29→32) is the merge of the five vlm-graph build tracks, each verified
additively and re-measured isolated after the merge: +3 recoveries-fold
(vlm-recov), +6 model_endpoint seam + skill contracts (vlm-seam), +9
untrusted-planner hardening (vlm-valid), +10 policy_vla_remote transport
(vlm-policy, its 2 protocol-layer tests skip without the `[policy_remote]`
extra), +1 skip LIBERO marker self-proof (vlm-libero). Below, the older
increments this supersedes.

The +1 skip of the LIBERO scaffold (docs/sim-adaptation.md §5): `tests/test_libero_
marker.py`'s marker self-proof, libero venv only (`sims/libero-venv`, py3.10 --
LIBERO's 2022-era pins cannot share either existing interpreter), plus the
inactive `plugins/embodiment_libero/` card (enabled=false, so the base fold and
its sha are untouched -- the robocasa precedent). In the libero venv
`-m libero` on that file is 1 passed.

The +1 over the kitchen-thaw-horizon snapshot (604→605 pass, skips unchanged at
29) is `test_campaign_progress.py`'s nested-layout case: `campaign_progress()`
scanned only `runs/*/progress.json`, but a chain fired THROUGH the runtime lands
at `runs/<session>/campaigns/<brief>/progress.json`, so the console's 演进 panel
showed the hand-run stores and never the live chain. Sim-free, no seeds burned.

The +2 over the RSI-mechanism snapshot (602→604 pass, skips unchanged at 29) is
`tests/test_kitchen_thaw_horizon.py`: the calibration-r2 finding pinned. The
mission's EPISODE horizon had fallen below the six kitchen_driver segment caps
summed (2000 vs 2350) after capability-r1 widened the grasp cap, so 110/150
calibration episodes died on the clock -- the RSI gate's own
`c3_budget_exhaust_dominant`. The card cannot import the driver card (plugin
boundary), so its `_NOMINAL_STEPS` is a written-down copy; a test may import
both, so both halves are pinned. Sim-free and unmarked, adds to both lanes.

The +22 over the mission-E2E snapshot (580→602 pass, skips unchanged at 29) is
the generic RSI mechanism (`docs/rsi-mechanism.md`): `test_rsi_mechanism.py`'s 20
pure-dict tests over the chain's three judgement points -- seed-block allocation
(5), first-death attribution onto the governable node (4), the six-criterion
go/no-go verdict incl. the honest-NO-GO paths (9), plus the repertoire's
per-embodiment registration (2) -- and 2 in `test_campaign_progress.py` for the
heartbeat fields the console's stage badge reads. Sim-free, no seeds burned.

The +1 skip over the mission-cards snapshot (28→29) is the capability-r1 grasp
rework (`local-archive/robocasa-adapt/capability-r1.md`): `test_robocasa_
drivers.py` grows one parametrized grasp case (seed 100007, the attempt-0
high-shelf secure grasp) -- robocasa venv only, so the base-lane pass count is
unchanged. In the robocasa venv `-m robocasa` on `test_robocasa_drivers.py` is
now 7 passed + 5 xfailed (was 6+5), suite-wide 17 passed + 6 xfailed.

The +17/+4 over the campaign-progress snapshot (563→580 pass, 24→28 skips) is
the three robocasa composite mission cards (`mission_recycle_cans` 32-node,
`mission_pack_lunch` 31-node, `mission_steam_prep` 21-node graph-first):
+17 base-lane graph-shape/binding tests (`test_mission_recycle_cans.py` 5,
`test_mission_pack_lunch.py` 6, `test_mission_steam_prep.py` 6 -- pure-data
planner + discover() fold checks, sim-free) and +4 skips from the new
`test_robocasa_missions.py` (3 live env/predicate smokes + 1 strict xfail
"awaiting sink driver", robocasa venv only). In the robocasa venv,
`-m robocasa` on that tree was 16 passed + 6 xfailed (now 17+6, above).

The +6 over the viewport snapshot (557→563) is the live campaign-progress
heartbeat (`tests/test_campaign_progress.py`, 6 items): the atomic
progress.json writer + its never-raise contract, the tracker's python-side
rolling-stat fold, the board scan's running/stale/done split (mid-write
skipped) and empty-runs case, and campaign_progress's three-face byte
equivalence. All sim-free and unmarked, so 6 add to both lanes.

The +5 over the carry-probe snapshot (552→557) is the viewport upgrade
(`test_runtime_frame.py` 9→14 items): the PH_FRAMES_SIZE parse fallback, the
wait_ms long poll answering on a frame change, its timeout falling back to the
usual short/error replies (wait_ms=0 staying immediate), the wait_ms
passthrough on both faces, and the `storecli serve` resident line-JSON loop
the ph-station frame worker rides. All sim-free and unmarked, so 5 add to
both lanes.

The +2 skips over the frames-overlay snapshot (22→24) are the carry-probe
driver tests (`test_robocasa_drivers.py` 9→11 items): +1 secure-grasp GREEN
(seed 11), +1 loaded-transport GREEN (seed 11), +2 false-latch xfails (seeds
4/5), −2 former grasp "GREEN"s that the carry-probe proved were false-positive
latches (the fingers never enclosed the meat — local-archive/robocasa-adapt/
carry-probe.md). Base-lane PASS count untouched (all robocasa-marked).

The +8 over the phase-5 snapshot (544→552) is the frames overlay
(`test_runtime_frame.py`: the never-raise dump contract, the step-interval
frame write, the frames mount overlay as pure config, runtime_frame's
three-face equivalence, and the after_ts unchanged short-circuit the 取景窗
fast poll rides). All sim-free and unmarked but one (the robocasa live frame
proof, marked), so 8 add to both lanes and 1 skips outside the robocasa venv.

The +15 over the phase-4 snapshot (529→544) is phase 5's routing tests:
`test_session_routing.py` (9: the session param across board fn / storecli / mcp
faces — default / whitelist / traversal) + `test_cockpit_stop.py` (6: per-session
--stop reaping by exact pid, adopted web/runtime left up). All are sim-free and
unmarked, so they add to both lanes and skip in neither.

Full-suite parity (card present): `759 passed, 29 skipped` (re-measured
2026-08-29 with the deploy-profile tests; 728 base + 28 robosuite-marked
+ the 3 camera-env skips that convert to passes when the card is present).
This line had gone stale at `709` -- the arithmetic was last done in the
678-base era and the base count moved to 717 without it (the robocasa-marked
items also skip in the harness .venv — robocasa is not installed there either, and
the 1 libero-marked item likewise runs only in sims/libero-venv; the robocasa items
run only in sims/robocasa-venv via `pytest -m robocasa` → `13 passed, 5 xfailed`;
the 5 xfails are the measured driver honest-failure surfaces —
nav-microwave unloaded (fridge blocks the seed-7 aisle) / close-door /
place-from-standoff / false-latch grasps on seeds 4 and 5 — see
local-archive/robocasa-adapt/phase3.md and carry-probe.md). The kitchen_thaw mission card (phase 4)
adds no robocasa-marked test — its live proof is the runtime E2E
(local-archive/robocasa-adapt/phase4.md), not a pytest; it contributes 7 base-lane
tests (2 heterogeneous-segment runner tests + 5 mission-card structural tests).
base_profile sha is byte-stable at `b905a5…` (folds to the value sealed in
runs/round25-rerun) — the manifest fold reproduces the old hard-coded mounts, and
the inactive embodiment_robocasa card (enabled=false) folds no mount.

**Discipline: a commit that adds or removes tests refreshes this snapshot + the
two README counts IN THE SAME COMMIT.**

## fresh-clone variance

The snapshot above is defined on a checkout WITH the sealed `runs/` evidence
(not in git). A fresh clone legitimately shows MORE skips, never failures:

- +2 `test_plugin_doctor.py` verify-claim tests skip (sealed stores absent)
- +2 more skips where tests read sealed rescore/campaign artifacts
- +1 `test_runtime_frame.py` JPEG-write test and +3 `test_keyframes.py` capture
  tests skip when Pillow is absent (it rides the sim extras, not the base deps;
  dump() itself degrades to no-frames, and so does the keyframe listener)
- the two 30-秒上手 commands in README work as written: the `dev` extra carries
  everything collection needs (including `mcp` for the both-faces tests)

A fresh clone that shows a FAILURE (not a skip) is a real regression -- no
exceptions. There used to be one:
`test_runtime_campaign.py::test_burned_range_brief_is_rejected_without_spawning`
asserted against a burned block in the operator's REAL `STATUS.md`, which is
untracked -- so a clone had no ledger, nothing was burned, and the brief was
(correctly, per the runtime's own rule) accepted. The test now writes a ledger of
its own into `tmp_path` and points `harness_runtime.STATUS_MD` at it. A test that
reads operator-local state is the bug, never the missing-ledger semantics.

## repeat-offender: keep this snapshot + the two README counts in lockstep

This count has drifted before — the robocasa-marked tally slipped 5→6 (commit
38fe596) and had to be chased down after the fact. So the rule above is a
STANDING one, not a nicety: any commit touching `tests/` re-runs the isolated
base lane and the parity suite and updates, in the SAME commit, (1) the snapshot
`pass` line here, (2) the parity line here, (3) README's 全量 count, (4) README's
底座快道 count. A snapshot that lags the tests is the bug this section exists to
prevent from recurring a fourth time.
