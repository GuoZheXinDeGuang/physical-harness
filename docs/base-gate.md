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

## current snapshot (2026-08-26, isolated, robosuite blocked)

```
pass       : 601 passed
skips      : 29 skipped
             [2] test_grasp_geometric.py:141  camera env unavailable
             [1] test_grasp_geometry.py:231   camera env unavailable
             [1] test_reducers.py:171         cloned weights not present
             [1] test_plugin_doctor.py:264    robocasa unimportable (robocasa venv only)
             [4] test_robocasa_card.py         robocasa unimportable (robocasa venv only)
             [12] test_robocasa_drivers.py     robocasa unimportable (robocasa venv only)
             [1] test_robocasa_marker.py:11   robocasa unimportable (robocasa venv only)
             [4] test_robocasa_missions.py     robocasa unimportable (robocasa venv only)
             [1] test_runtime_frame.py         robocasa unimportable (robocasa venv only)
             [2] test_rsi_workload.py:592,609 runs/campaign-pj-scripted not present
wall time  : ~4.6s
AST green  : 17 passed (test_boundaries + test_kernel)
deselected : 28 robosuite-marked items
```

The +21 over the mission-E2E snapshot (580→601 pass, skips unchanged at 29) is
the generic RSI mechanism (`docs/rsi-mechanism.md`): `test_rsi_mechanism.py`'s 19
pure-dict tests over the chain's three judgement points -- seed-block allocation
(4), first-death attribution onto the governable node (4), the six-criterion
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

Full-suite parity (card present): `594 passed, 21 skipped` (the 18 robocasa-marked
items also skip in the harness .venv — robocasa is not installed there either; they
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
- +1 `test_runtime_frame.py` JPEG-write test skips when Pillow is absent (it
  rides the sim extras, not the base deps; dump() itself degrades to no-frames)
- the two 30-秒上手 commands in README work as written: the `dev` extra carries
  everything collection needs (including `mcp` for the both-faces tests)

A fresh clone that shows a FAILURE (not a skip) is a real regression.

## repeat-offender: keep this snapshot + the two README counts in lockstep

This count has drifted before — the robocasa-marked tally slipped 5→6 (commit
38fe596) and had to be chased down after the fact. So the rule above is a
STANDING one, not a nicety: any commit touching `tests/` re-runs the isolated
base lane and the parity suite and updates, in the SAME commit, (1) the snapshot
`pass` line here, (2) the parity line here, (3) README's 全量 count, (4) README's
底座快道 count. A snapshot that lags the tests is the bug this section exists to
prevent from recurring a fourth time.
