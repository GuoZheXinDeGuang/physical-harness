#!/usr/bin/env python3
"""RUNG 2: the inject-fault soak -- the stability gate for rungs 0+1 (M4#7).

The soak IS the test. It generates N=50 briefs across six fault classes into a
fresh session inbox, drains them through the REAL resident runtime
(scripts/harness_runtime.py as a subprocess, real MuJoCo rollouts), KILLS the
drain mid-run and reboots it to exercise restart-resume, then asserts the M4#7
invariants and exits 0 iff they all held:

  * the runtime process never crashed (survived every injected fault; the reboot
    drain finished with rc 0 and no traceback),
  * every well-formed brief closed with exactly one ``task.plan_complete`` OR
    ``runtime.task_error`` note (the at-least-once re-run of the one killed brief
    may add a single duplicate -- see below),
  * malformed briefs went to failed/ with no note (nothing to attribute),
  * the one shared SessionLog verifies (``load().verify()``) AFTER the restart --
    the chain continued from disk, it did not restart at genesis.

    MUJOCO_GL=egl PYTHONPATH=. .venv/bin/python scripts/soak.py

Fault classes (seeds 90000-90049, each used once; the stack pass/fail split is
empirical, from a 2026-08-23 scan of the scripted policy):

  clean stack (20)   task=stack, default budget          -> plan_complete, done/
  clean clear_table (10) task=clear_table, default budget -> plan_complete, done/
  budget (5)         task=clear_table, max_actuations=1   -> plan_complete(fail), done/
  node_failure (5)   task=stack on seeds the policy fails -> plan_complete(fail), done/
  planner-raise (5)  task=nonsense (planner.plan raises)  -> task_error, failed/
  malformed (5)      not JSON                             -> failed/, NO note

The first four are well-formed and non-escaping: workload.run folds every
planning/node fault back into its own replan loop and always closes with one
plan_complete note (win or lose). planner-raise is well-formed but STRUCTURAL:
the planner raises, workload.run escapes, and the runtime layer catches it and
writes one task_error note. Malformed never parses, so there is nothing to
attribute a note to.

The kill lands mid-rollout on purpose (we poll processing/ and kill while a
~2s rollout holds the claim). During a rollout the ledger is quiescent -- rows
are appended only at mount/resolve (before) and the note (after) -- so the kill
never truncates a row, and the killed brief has no note yet, so its reboot
re-run produces exactly one. The theoretical microsecond window between the note
write and the done/ rename is the only source of a duplicate; the assert
tolerates that single extra note rather than flaking on it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
VENV_PY = REPO / ".venv" / "bin" / "python"
RUNTIME = REPO / "scripts" / "harness_runtime.py"

from harness.events import SessionLog
from scripts.brief_drop import drop as _atomic_drop

# Seed -> class map. Stack classes need specific seeds (clean stack must pass,
# node_failure must fail), so those two draw from an empirical scan of the
# scripted policy over 90000-90049; the other four classes are seed-agnostic
# (clear_table clean passes broadly, budget always exhausts, planner-raise and
# malformed never reach a rollout) and take whatever seeds are left.
CLEAN_STACK = [90000, 90001, 90004, 90005, 90006, 90007, 90008, 90009, 90010,
               90011, 90013, 90014, 90015, 90018, 90019, 90020, 90021, 90025,
               90027, 90028]                                          # 20, pass
NODE_FAILURE = [90002, 90003, 90012, 90016, 90017]                    # 5, fail
CLEAN_CLEAR = [90022, 90023, 90024, 90026, 90029, 90030, 90031, 90032,
               90033, 90034]                                          # 10
BUDGET = [90035, 90036, 90037, 90038, 90039]                          # 5
PLANNER_RAISE = [90040, 90041, 90042, 90043, 90044]                   # 5
MALFORMED = [90045, 90046, 90047, 90048, 90049]                       # 5


def _briefs() -> list[tuple[str, str, str]]:
    """(filename, class, raw-bytes-to-write), in drop/mtime order.

    Rollout classes go first so the kill (which fires the instant processing/ is
    non-empty) lands on a ~2s rollout, not a fast-failing planner-raise/malformed
    brief that only flashes through processing/.
    """
    out: list[tuple[str, str, str]] = []
    for s in CLEAN_STACK:
        out.append((f"clean-stack-{s}.json", "complete",
                    json.dumps({"kind": "task", "task": "stack", "seed": s})))
    for s in NODE_FAILURE:
        out.append((f"node-failure-{s}.json", "complete",
                    json.dumps({"kind": "task", "task": "stack", "seed": s})))
    for s in CLEAN_CLEAR:
        out.append((f"clean-clear-{s}.json", "complete",
                    json.dumps({"kind": "task", "task": "clear_table", "seed": s})))
    for s in BUDGET:
        out.append((f"budget-{s}.json", "complete",
                    json.dumps({"kind": "task", "task": "clear_table",
                                "seed": s, "max_actuations": 1})))
    for s in PLANNER_RAISE:
        out.append((f"planner-raise-{s}.json", "escape",
                    json.dumps({"kind": "task", "task": "nonsense", "seed": s})))
    for s in MALFORMED:
        out.append((f"malformed-{s}.json", "malformed", "{not json,"))
    return out


def _drop(inbox: Path, name: str, raw: str, mtime: float) -> None:
    """Drop a brief the atomic way an external writer must (the shared
    brief_drop.drop: temp write + os.replace), then stamp an explicit mtime so
    the runtime's mtime-sorted intake is deterministic."""
    _atomic_drop(inbox, name, raw)
    os.utime(inbox / name, (mtime, mtime))


def _spawn(session: Path, logfile: Path) -> subprocess.Popen:
    env = {**os.environ, "MUJOCO_GL": "egl", "PYTHONPATH": str(REPO)}
    fh = logfile.open("w")
    return subprocess.Popen(
        [str(VENV_PY), str(RUNTIME), "--session-dir", str(session), "--drain"],
        cwd=str(REPO), env=env, stdout=fh, stderr=subprocess.STDOUT)


def _kill_mid_rollout(proc: subprocess.Popen, processing: Path, done: Path,
                      min_done: int = 5, timeout: float = 180.0) -> str:
    """Let the drain complete a few briefs (so the chain has real rows on disk),
    then SIGKILL it while the next rollout holds the claim. Returns the stranded
    brief's filename -- the one restart-resume must re-queue. This makes the
    reboot resume a mid-stream chain, not one killed on its very first brief."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError("drain finished before we could kill it mid-run")
        claimed = list(processing.glob("*.json"))
        if len(list(done.glob("*.json"))) >= min_done and claimed:
            proc.kill()
            proc.wait()
            return claimed[0].name
        time.sleep(0.02)
    raise AssertionError("timed out waiting for the drain to reach mid-stream")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--session-dir", default=None,
                    help="fresh session dir (default: a fresh temp dir)")
    args = ap.parse_args()

    session = Path(args.session_dir) if args.session_dir else Path(
        tempfile.mkdtemp(prefix="soak-"))
    inbox = session / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    print(f"soak session: {session}")

    briefs = _briefs()
    assert len(briefs) == 50, len(briefs)
    seeds = ([s for lst in (CLEAN_STACK, NODE_FAILURE, CLEAN_CLEAR, BUDGET,
                            PLANNER_RAISE, MALFORMED) for s in lst])
    assert sorted(seeds) == list(range(90000, 90050)), "seeds must cover 90000-90049 once"

    base = time.time()
    for i, (name, _cls, raw) in enumerate(briefs):
        _drop(inbox, name, raw, base + i)

    complete = {n for n, c, _ in briefs if c == "complete"}
    escape = {n for n, c, _ in briefs if c == "escape"}
    malformed = {n for n, c, _ in briefs if c == "malformed"}
    print(f"dropped {len(briefs)} briefs: {len(complete)} complete, "
          f"{len(escape)} escape, {len(malformed)} malformed")

    processing = session / "processing"
    done = session / "done"
    failed = session / "failed"

    # First drain, killed mid-rollout.
    print("drain #1: launching, will kill mid-rollout ...")
    p1 = _spawn(session, session / "drain1.log")
    killed = _kill_mid_rollout(p1, processing, done)
    print(f"drain #1: SIGKILLed while {killed!r} was in flight (stranded in processing/)")
    assert list(processing.glob("*.json")), "the killed brief should be stranded in processing/"

    # Reboot: re-queues the stranded brief and finishes the drain.
    print("drain #2 (reboot): re-queues the stranded brief and finishes ...")
    p2 = _spawn(session, session / "drain2.log")
    rc = p2.wait()
    stderr = (session / "drain2.log").read_text()

    # ---- assertions (M4#7) ----
    fails: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)

    check(rc == 0, f"reboot drain exited {rc}, expected 0 (process crashed)")
    check("Traceback" not in stderr, "reboot drain printed a traceback (process crashed)")
    check(not list(inbox.glob("*.json")), "inbox not empty after drain")
    check(not list(processing.glob("*.json")), "processing not empty after drain")

    done_names = {p.name for p in done.glob("*.json")}
    failed_names = {p.name for p in failed.glob("*.json")}
    check(done_names == complete,
          f"done/ mismatch: extra={done_names - complete} missing={complete - done_names}")
    check(failed_names == escape | malformed,
          f"failed/ mismatch: got {len(failed_names)}, want {len(escape | malformed)}")

    log = SessionLog.load(session / "session-log")
    rows = log.rows()
    n_complete = sum(1 for r in rows if r["kind"] == "task.plan_complete")
    error_briefs = {r["data"]["brief"] for r in rows if r["kind"] == "runtime.task_error"}
    n_error = sum(1 for r in rows if r["kind"] == "runtime.task_error")

    # exactly one note per well-formed brief; the killed brief's at-least-once
    # re-run may add ONE duplicate plan_complete (kill-during-rollout makes this
    # rare, but tolerate it rather than flake).
    check(len(complete) <= n_complete <= len(complete) + 1,
          f"plan_complete notes {n_complete}, expected {len(complete)} (+1 at-least-once)")
    check(n_error == len(escape),
          f"task_error notes {n_error}, expected {len(escape)}")
    check(error_briefs == escape,
          f"task_error brief set mismatch: {error_briefs ^ escape}")
    check(not (error_briefs & malformed),
          "a malformed brief got a task_error note (should have none)")

    verified = log.verify()
    check(verified, "SessionLog.load().verify() is False after restart")

    # ---- report every count ----
    print("\n==== SOAK RESULT ====")
    print("briefs                 : 50 (20 clean-stack, 5 node-failure, "
          "10 clean-clear, 5 budget, 5 planner-raise, 5 malformed)")
    print(f"reboot drain rc        : {rc}")
    print(f"done/                  : {len(done_names)} (want 40)")
    print(f"failed/                : {len(failed_names)} (want 10)")
    print(f"task.plan_complete     : {n_complete} (want 40, +1 tolerated for the killed re-run)")
    print(f"runtime.task_error     : {n_error} (want 5)")
    print(f"  error briefs         : {sorted(error_briefs)}")
    print(f"session rows total     : {len(rows)}")
    print(f"chain verify()         : {verified}")
    print(f"killed brief (requeued): {killed}")
    if fails:
        print("\nFAIL:")
        for m in fails:
            print(f"  - {m}")
        return 1
    print("\nOK: every M4#7 invariant held.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
