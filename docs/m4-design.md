# M4 施工图 — 常驻系统层 (round 94 design, 2026-08-23)

由 round-94 设计 workflow(四路深读+综合)产出; 三个开放问题的裁决:
1. 会话持久化 = 单一 runs/session-main 长链, 签名检查点 YAGNI(触发: verify 变慢)。
2. rung 5(zos sim_test 改投递 brief)延后至 inbox 契约冻结后, 独立会话落地。
3. rung 3 的 RECORD 用 stub 证代码; 真 campaign 跑作为后续 RECORD(候选: place-g2 经运行时投递, 成为首个系统内 RSI 任务)。

## Design

## Round-94 construction plan for M4+R4 — the resident system layer

CORE ARCHITECTURE (one shape, derived from the four reads):
**A persistent SessionLog + a shared skills dir + a fresh Kernel per task.** The three vary at different rates and the reads force exactly this split:
- The SessionLog is the durable, cross-task chain (M4#3). It must be `load()`-or-fresh at boot, never `SessionLog(root)` on an existing dir (that FileExistsErrors — events.py:49-55; intake risk#3). One long-lived `runs/session-main/session-log/`.
- The Kernel is single-mount and its `policy.driver` is chosen *by task* (task_plan.py:49; task-loop risk#4), so it CANNOT be reused across stack/clear_table. Build a fresh `Kernel(CAPABILITIES, log=shared_log)` per task; all kernels append to the one shared chain. Cheap (dicts).
- The shared skills dir is the `graph.skill` root. RSI writes content-addressed SkillRecords there; the next task sees them only through a *fresh* graph.skill mount (re-globs at __init__; graphs.py:46-47; rsi risk#6). Fresh kernel per task gives this for free.

DECISION — intake: **watched inbox dir, JSON brief, os.replace atomic write, claim-by-rename** (intake seams Q1). Brief = pure JSON selector+budgets only `{"kind":"task","task":"stack","seed":90000,"max_replans":3,"max_actuations":3}`; catalogue/oracles are non-JSON skill-authored `type` objects (validate.py:57, planner_stack.py:25-33) and are stamped server-side from the mounted planner, never carried in the file. os.replace (not CampaignStore's write_text) because the inbox has an external writer and the runtime *acts* on the file — it must see it whole (intake risk#4). Rejected: stdin/CLI (dies with process), socket/HTTP (needs runtime up to accept; YAGNI until a remote submitter exists), queue lib (new dep for what a dir+os.replace does). Directory-polling is already the house style (board store_mtime).

DECISION — authority-laundering defense (intake risk#1, non-negotiable): the brief names NO provider/mount ref. Provider selection is server-side from a fixed allowlist keyed by task name (the task_plan.py:49 policy switch). A brief can never reach a real actuator because the resident runtime's providers are sim-only and fixed at boot; a real-actuator embodiment is a *different* runtime with a different authenticated intake.

DECISION — session-store the board renders: **reuse the already-written `session-log/rows.jsonl`, unmodified on the write side** (intake seams Q2). Do NOT turn the session into a CampaignStore (store_detail only shapes prereg/generation/campaign_result kinds; a session-as-store renders empty — ledger risk#4). Board addition is read-only and small: `read_session()` reusing the partial-tolerant line loop + a `chain_ok` from `SessionLog.load().verify()`; two endpoints; one tab. Discover sessions by presence of `session-log/rows.jsonl`, kept separate from campaign stores.

DECISION — campaign: **subprocess, not in-process** (rsi risks#1,#3,#5). The resident runtime will hold live state across N tasks; forking a Pool from it inherits any CUDA/GL context broken (rsi risk#1), and a second embodiment's import would crash on the process-global REGISTRY duplicate (features.py:72-73; rsi risk#3). The campaign scripts are already CLI-shaped (`stack_campaign.py --out`). So RSI-as-system-task = `subprocess.run([venv_py, script, --out, campaign_out])`, then copy `campaign_out/skills/*.json` into the shared skills dir (idempotent — content-addressed) and write one `runtime.campaign_scheduled` note (prereg_sha + out) into the session chain — cross-linked by content-hash, the only cross-link primitive that exists (ledger risk#3). Subprocess isolation eliminates fork+GPU and registry pollution entirely and keeps the resident loop responsive.

DECISION — restart-resume: boot does `SessionLog.load(dir)` if rows.jsonl exists else `SessionLog(dir)`; the chain continues from the last stored `chain` value (events.py:90-91). On boot, move any `processing/*.json` back to `inbox/` (at-least-once retry). `# ponytail: at-least-once — a task that crashed after its plan_complete note re-runs and appends a second note; dedup by brief-id grep only if soak shows it hurts.`

DECISION — crash-safety wrap: workload.run stays UNCHANGED (it is loud-as-data for planning faults, raises for structural — task-loop risk#2). The resident loop wraps its `workload.run` call in try/except and writes its own `runtime.task_error` note on any escape, then continues. Crash-safety lives in the resident layer, not smuggled into the well-tested workload.

DECISION — env-handle leak (task-loop risk#1, HIGHEST): fix at root in the shared function — `governed_rollout` gets `try/finally: env.close()` (governed.py ~247-416). Every in-process task rollout routes through it; over a 50-task soak with injected faults the current no-finally path leaks GL contexts until the process dies, directly failing M4#7. One guard in the shared function = the lazy root-cause fix.

DECISION — seed-ledger guard (non-negotiable invariant): enforced at the *scheduling boundary*, reusing `board/store.py:parse_ledger(STATUS.md)` burned intervals. Before spawning a campaign the runtime rejects any brief whose declared dev∪heldout intersects a burned range (→ failed/). No campaign.py edit; the one prose ledger becomes one enforced check where the runtime funnels campaigns. Soak seeds 90000+ are confirmed clear (all burned/reserved ranges are ≤48000).

R4 PLACEMENT (honest): the eval battery as a *smoke gate* (runs demo parity + stack three-block + closed-loop soak, pass/fail exit, content-addressed result) belongs in round 94 as the final rung — it is cheap because all three pieces already exist as scripts, and it is immediately useful as a CI reflex. The *paired before/after architecture-change comparison with its own preregistration/evidence ledger* (the actual meta-RSI 門禁) is DEFERRED to round 95, triggered by the first real architecture-level change proposal (round-88-style). Building the paired-diff harness now, before there is a change to gate, is speculative (ladder rung 1). Ship the battery-as-smoke-gate; wait for the battery-as-門禁's first consumer.

## Rungs

Each rung independently landable, with test + RECORD. Order: 0→1→2→3→4→5→6.

RUNG 0 — env-handle try/finally (prerequisite for safe in-process N rollouts).
- Files: harness/governed.py (wrap env lifecycle in governed_rollout, ~247-416, `try: ... finally: env.close()`).
- Test: harness/tests test_governed_env_close — monkeypatch embodiment.make_env to return a handle whose close() flips a flag; force a raise mid-rollout (patch a stage to raise); assert close() called and exception still propagates. Covers task-loop risk#1 directly (soak can't cover it by data alone).

RUNG 1 — resident loop + inbox intake + restart-resume (M4 #1,#2,#3, #6-server-side).
- Files: harness/runtime.py (NEW): boot(session_dir) → load-or-fresh SessionLog; poll inbox/*.json (mtime-sorted); claim by os.rename → processing/<id>.json; parse brief; pick policy_ref by task from fixed allowlist; build fresh plan+Kernel(log=shared_log); call workload.run wrapped in try/except → on escape write runtime.task_error note; rename → done/ or failed/. `--drain` (process pending then exit) vs default poll-forever. On boot move processing/*→inbox/. scripts/harness_runtime.py (NEW, ~20 lines): argparse --session-dir --inbox --drain, calls harness.runtime.
- Test: harness/tests test_runtime_drain — drop 3 briefs (2 valid stack/clear_table, 1 task="nonsense" that makes planner.plan raise), run --drain, assert 2 in done/ + 1 in failed/ with a runtime.task_error note, and SessionLog.load(dir).verify() is True.

RUNG 2 — inject-fault soak (M4 #7); the stability gate for rungs 0+1.
- Files: scripts/soak.py (NEW): generate N=50 briefs, seeds 90000-90049, mix ~20 clean stack + 10 clean clear_table + 5 budget (max_actuations=1 on clear_table) + 5 node_failure (seed/task known to fail) + 5 planner-raise (task="nonsense") + 5 malformed-JSON files; drop into a fresh session inbox; run runtime --drain; include one mid-run kill+reboot to exercise restart-resume; assert: process never crashed, every well-formed brief produced exactly one task.plan_complete OR runtime.task_error note, malformed briefs went to failed/ with no note, SessionLog.load().verify() True after restart.
- Test: the soak script IS the test (asserts + exit code); RECORD captures the run.

RUNG 3 — RSI campaign as in-system task (M4 #4).
- Files: harness/runtime.py (extend): brief kind=="campaign" → seed-ledger guard via board.store.parse_ledger(STATUS.md); on pass subprocess.run the campaign script with --out; on success copy campaign_out/skills/*.json → shared skills dir (idempotent); write runtime.campaign_scheduled note (prereg_sha,out); on ledger-overlap or nonzero exit → failed/ + note.
- Test: harness/tests test_runtime_campaign — stub script that writes a skills/<sha>.json and exits 0; assert skills copied to shared dir, note written, a following task's fresh graph.skill mount sees the new record; and a campaign brief with dev in a burned range is rejected to failed/. `# ponytail: real 15-40min campaign is a manual RECORD run, not a unit test.`

RUNG 4 — board renders runtime sessions (M4 #5).
- Files: board/store.py (add read_session(dir) reusing _index_rows partial-tolerant loop, returns rows-by-kind + skip-count + chain_ok; discover_sessions(runs_dir) by session-log/rows.jsonl presence); scripts/rsi_board.py (+/api/sessions, +/api/session?name=, reuse _safe_store guard, reuse --live-threshold); board/index.html (+["sessions","Sessions"] tab, +renderMain branch: plan_complete timeline from note data + green/red chain badge). The 4s poll/mtime loop is reused untouched.
- Test: board/tests test_read_session over a fixture session dir → asserts rows-by-kind + chain_ok True, and False on a tampered rows.jsonl.

RUNG 5 — zos sim_test brief-drop (M4 #6; CROSS-REPO: Z-Robotics-Lab/zos/zos/tools/sim.py).
- Files: zos .../tools/sim.py sim_test: os.replace a brief into the harness inbox, then bounded-poll the session-log for the matching task.plan_complete row. DELETES the subprocess/venv/cwd/MUJOCO_GL/timeout machinery and the entire _parse_scan stdout seam + its self-check (the file's own flagged rot point). Stays reads_only+Auth.IDLE. sim_watch (GUI Popen) unchanged.
- Test: zos-side self-check asserting a dropped brief round-trips to a plan_complete read. `# ponytail: different repo, possibly concurrently edited — landable independently after rung 1's inbox contract is stable; may spin to its own zos session.`

RUNG 6 — eval-battery smoke gate (R4 seed).
- Files: scripts/eval_battery.py (NEW): run demo parity (parity_check.py) + stack three-block reproduction (stack_campaign against sealed seeds) + closed-loop soak (scripts/soak.py); emit pass/fail exit + one content-addressed result artifact. NO paired before/after (deferred to round 95).
- Test: the script's own exit code on a known-good tree; RECORD captures a green run.

## Acceptance map

M4#1 (one command boots resident kernel, mounts skills+evidence, accepts tasks) -> RUNG 1. Proof: `python scripts/harness_runtime.py --session-dir runs/session-main` boots, load-or-fresh SessionLog, fixed sim mount allowlist, polls inbox; RECORD shows boot + a task accepted from a cp'd brief.

M4#2 (every task governed; single failure doesn't kill the system) -> RUNG 1 (workload.run per task wrapped in try/except; fault-as-data folds to replan, escape → runtime.task_error note, loop continues) + RUNG 0 (no env-context leak on the raise path). Proof: RUNG 2 soak shows the loop continuing past budget/node_failure/planner-raise faults with the process alive.

M4#3 (session chain cross-task continuous; restart resumable+verifiable) -> RUNG 1 (shared persistent SessionLog; SessionLog.load-or-fresh at boot; processing/→inbox re-queue on restart). Proof: RUNG 2 soak performs a mid-run kill+reboot and asserts SessionLog.load(dir).verify() True with the chain continuing (not a new genesis).

M4#4 (RSI as in-system service; SkillRecords immediately consumable by later tasks) -> RUNG 3. Proof: campaign brief → subprocess → skills copied to shared graph.skill root → a following task's fresh graph.skill mount re-globs and sees the new record; runtime.campaign_scheduled note in the session chain; seed-ledger guard rejects burned ranges.

M4#5 (board observes the runtime session, not just sealed campaigns) -> RUNG 4. Proof: /api/sessions lists runs/session-main, /api/session renders the task.plan_complete timeline (success/replans/actuations/faults from note data) + chain-verified badge; live via existing rows.jsonl mtime poll.

M4#6 (zos submits a brief via the same task seam; adapter→thin→delete) -> RUNG 5. Proof: sim_test os.replace's a brief into the inbox and reads back the plan_complete row; the stdout _parse_scan seam is deleted. Cross-repo; landable after RUNG 1.

M4#7 (inject-fault soak: N tasks, zero crashes, zero chain breaks) -> RUNG 2. Proof: soak.py N=50, seeds 90000-90049, 6 fault classes + a restart, asserts process-never-crashed + one-note-per-well-formed-brief + malformed→failed/ + verify() True; RECORD captures the run.

R4 (eval battery as a runnable gate) -> RUNG 6 (smoke gate: demo parity + stack three-block + soak, pass/fail exit). Paired before/after architecture-change 門禁 = round 95, triggered by first arch-change proposal.

## Open questions (已裁决, 存档)

1. Session persistence policy: I default to a single long-lived runs/session-main/ chain that grows across restarts (required for M4#3 "重启后可续"). verify() is O(n)-from-genesis every call (ledger risk#1) — fine at soak scale but unbounded long-term. A signed-checkpoint row is YAGNI-deferred (trigger: chain length makes verify() slow, or 20Hz-class note frequency). Confirm the single-dir policy and that no checkpoint is wanted in round 94.

2. RUNG 5 (zos sim_test rewrite) lives in a *different* repo (Z-Robotics-Lab/zos) that the reads flagged as possibly concurrently edited. Confirm whether it lands in this round's work or spins to a separate zos-repo session once RUNG 1's inbox contract is frozen — physical-harness rungs 0-4,6 don't depend on it.

3. For landing RUNG 3, does the round-94 RECORD require a real 15-40min RSI campaign run, or does the stubbed-subprocess proof (guard + skills-copy + note + consumption) suffice for the code to land, with the real campaign as a follow-on overnight RECORD? (I default: stub proves the code, real run is a separate manual RECORD.)

Everything else (intake mechanism, atomic-write choice, subprocess-vs-inprocess, restart semantics, soak N=50/seeds/fault mix, seed-ledger guard placement, board session shape, R4 split) is decided above and needs no orchestrator input.

YAGNI-deferred with triggers: concurrency/parallel tasks (trigger: sequential can't drain queue depth or a latency SLA appears); HTTP/socket intake (trigger: a submitter that can't share the filesystem); custom CPU-capping exec.rollouts provider (trigger: campaign subprocess measurably starves foreground tasks); brief-id dedup on at-least-once retry (trigger: soak shows duplicate re-runs hurt); ledger checkpoint (trigger: verify() slow); multi-embodiment in one process (never — a second embodiment is a separate runtime; also the authority defense); paired before/after meta-RSI 門禁 (trigger: first architecture-change proposal, round 95).