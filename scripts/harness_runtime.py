#!/usr/bin/env python3
"""The resident runtime: one boot, a persistent session chain, a fresh kernel
per task, and a watched inbox of JSON briefs.

    PYTHONPATH=. .venv/bin/python scripts/harness_runtime.py \
        --session-dir runs/session-main
    (add --drain to process whatever is pending and exit)

M4's system layer. The three durable things vary at three rates, so they split
three ways (round-94 design):

- ONE long-lived ``SessionLog`` under ``<session-dir>/session-log/``, ``load()``-
  or-fresh at boot -- never ``SessionLog(dir)`` over an existing ledger (that
  ``FileExistsError``s; events.py:49-55). Every task appends to this one chain,
  so "restart-resumable + verifiable" is just reopening the file.
- a FRESH ``Kernel`` per task on that shared log. The kernel is single-mount and
  its ``policy.driver`` is chosen BY TASK, so it cannot be reused across
  stack/clear_table; a fresh ``Kernel(CAPABILITIES, log=shared_log)`` per task is
  cheap (dicts) and every kernel's mounts/resolutions land in the one chain.
- a SHARED skills dir as the ``graph.skill`` root. RSI (rung 3) writes content-
  addressed records there; the next task sees them because its FRESH graph.skill
  mount re-globs the root at ``__init__`` -- fresh-kernel-per-task gives this for
  free.

Intake is a watched directory: an external writer drops ``inbox/<id>.json`` with
``os.replace`` (atomic, so the runtime never reads a half-written file); the
runtime claims by ``os.rename`` into ``processing/`` (the loser of a race gets
``FileNotFoundError`` and moves on), runs it, then ``os.replace``s the file into
``done/`` or ``failed/``. On boot any ``processing/*.json`` left by a crash is
re-queued to ``inbox/`` (at-least-once).

Authority-laundering defense (non-negotiable): a brief names NO provider/mount
ref. It is a pure selector+budgets -- ``{"kind":"task","task":"stack","seed":
90000,"max_replans":3,"max_actuations":3}``. The runtime picks the policy from a
fixed, SIM-ONLY allowlist keyed by task name and stamps the skill-authored
catalogue/oracles from the mounted planner; a real-actuator embodiment is a
DIFFERENT runtime with a different authenticated intake, never a brief away.

Crash-safety lives HERE, not in the well-tested workload: ``workload.run`` stays
loud-as-data for planning faults and raises for structural ones; the loop wraps
its call in try/except and writes its own ``runtime.task_error`` note on any
escape, then continues. A single task's failure never kills the system.

This wires plugins + profiles together, so it lives in scripts/ beside its
sibling closed-loop driver scripts/task_plan.py -- harness/ imports no plugin
(tests/test_kernel.py) and profiles/ stays declarative (tests/test_boundaries.py).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from board.store import parse_ledger
from harness.config import Mount, Patch, resolve_plan
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness.kernel import Kernel
from plugins.task import workload
from plugins.task.planner_stack import CATALOGUE, ORACLES
from profiles import base_profile

#: Server-side provider allowlist keyed by task name -- the authority-laundering
#: defense in one dict. A brief cannot name a provider; the runtime picks a
#: SIM-ONLY policy from here. Mirrors scripts/task_plan.py's policy switch; an
#: unknown task defaults to the stack driver and then fails loudly at
#: planner.plan (the planner only knows stack/clear_table), so it never actuates.
POLICY_BY_TASK: dict[str, str] = {
    "stack": "plugins.policies:stack_scripted_provider",
    "clear_table": "plugins.policies:provider",
}
_DEFAULT_POLICY = "plugins.policies:stack_scripted_provider"
_PLANNER_REF = "plugins.task.planner_stack:provider"

#: Server-side campaign allowlist keyed by campaign name -- the authority-
#: laundering defense for RSI, symmetric with POLICY_BY_TASK. A brief names a
#: campaign KEY, never a script path; the runtime spawns only these fixed,
#: sim-only, CLI-shaped campaign scripts as a SUBPROCESS. In-process is
#: forbidden: the resident kernel holds live state across N tasks, so forking it
#: inherits a broken CUDA/GL context (rsi risk#1) and a second embodiment import
#: trips the process-global REGISTRY duplicate (rsi risk#3) -- subprocess
#: isolation kills both and keeps the resident loop responsive.
CAMPAIGN_SCRIPTS: dict[str, Path] = {
    "stack": REPO_ROOT / "scripts" / "stack_campaign.py",
}
#: The one prose seed-ledger the guard enforces (board.store.parse_ledger).
STATUS_MD = REPO_ROOT / "STATUS.md"


@dataclass(frozen=True)
class Runtime:
    """The booted state: the four intake dirs, the shared skills root, the log."""

    inbox: Path
    processing: Path
    done: Path
    failed: Path
    skills_root: Path
    log: SessionLog


def boot(session_dir: str | Path, inbox: str | Path | None = None) -> Runtime:
    """Load-or-fresh the session chain, make the intake dirs, re-queue crashes."""
    session_dir = Path(session_dir)
    log_dir = session_dir / "session-log"
    skills_root = session_dir / "skills"
    inbox = Path(inbox) if inbox is not None else session_dir / "inbox"
    # processing/done/failed are SIBLINGS of inbox on purpose: os.rename is only
    # atomic within one filesystem, and the claim is a rename inbox->processing.
    processing = inbox.parent / "processing"
    done = inbox.parent / "done"
    failed = inbox.parent / "failed"
    for d in (log_dir, skills_root, inbox, processing, done, failed):
        d.mkdir(parents=True, exist_ok=True)

    log = (SessionLog.load(log_dir) if (log_dir / "rows.jsonl").exists()
           else SessionLog(log_dir))

    # restart-resume: anything claimed but not finished goes back to inbox.
    # ponytail: at-least-once -- a task that crashed after its plan_complete note
    # re-runs and appends a second note; dedup by brief-id only if soak shows it
    # hurts.
    for p in processing.glob("*.json"):
        os.replace(p, inbox / p.name)

    return Runtime(inbox, processing, done, failed, skills_root, log)


def _mount_plan(task: str, skills_root: Path):
    """A fresh MountPlan for this task: base profile + the task's sim policy +
    the shared skills root (so a fresh graph.skill mount re-globs RSI's output)."""
    policy_ref = POLICY_BY_TASK.get(task, _DEFAULT_POLICY)
    return resolve_plan(base_profile(), patches=(
        Patch("runtime", override=(
            Mount("task.planner", _PLANNER_REF),
            Mount("policy.driver", policy_ref),
            Mount("graph.skill", "plugins.graphs:skill_graph_provider",
                  {"root": str(skills_root)}),)),))


def _run_task(brief: dict, log: SessionLog, skills_root: Path) -> dict:
    """Build a fresh kernel on the shared log and run one governed plan loop.

    catalogue/oracles are skill-authored ``type`` objects, stamped server-side
    from the mounted planner's module -- never carried in the JSON brief.
    """
    task = brief["task"]
    seed = int(brief.get("seed", 0))
    max_replans = int(brief.get("max_replans", 3))
    # clear_table's two pick nodes need one extra actuation of headroom, matching
    # scripts/task_plan.py's default.
    max_actuations = int(brief.get("max_actuations",
                                   4 if task == "clear_table" else 3))
    kernel = Kernel(CAPABILITIES, log=log)
    kernel.mount(_mount_plan(task, skills_root))
    wbrief = {"task": task, "catalogue": CATALOGUE, "oracles": ORACLES}
    return workload.run(wbrief, kernel, seed=seed,
                        max_replans=max_replans, max_actuations=max_actuations)


def _burned_ranges(status_md: Path = STATUS_MD) -> list[tuple[int, int]]:
    """Burned seed intervals from the one prose ledger (STATUS.md 区块预算)."""
    return [(r["lo"], r["hi"]) for r in parse_ledger(status_md.read_text())
            if r["state"] == "burned"]


def _declared_ranges(brief: dict) -> list[tuple[int, int]]:
    """dev∪heldout the brief declares it will burn, as inclusive [lo,hi] pairs."""
    ranges = []
    for key in ("dev", "heldout"):
        for pair in brief.get(key, ()):
            lo, hi = int(pair[0]), int(pair[1])
            ranges.append((min(lo, hi), max(lo, hi)))
    return ranges


def _copy_skills(src: Path, dst: Path) -> list[str]:
    """Fold a campaign's published skill records into the shared graph.skill root
    (idempotent -- the filename stem IS the content digest, so a record already
    in the root is skipped). Returns every digest now present from this run."""
    copied = []
    for f in (sorted(src.glob("*.json")) if src.is_dir() else ()):
        if not (dst / f.name).exists():
            shutil.copy2(f, dst / f.name)
        copied.append(f.stem)
    return copied


def _prereg_sha(out: Path) -> str | None:
    """The campaign's preregistration content hash, read back from its store
    index (run_campaign puts it as row 0). None if the store wrote none."""
    index = out / "index.jsonl"
    if not index.exists():
        return None
    for line in index.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") == "preregistration":
            return row.get("sha")
    return None


def _run_campaign(brief: dict, rt: Runtime, brief_id: str) -> None:
    """Schedule one preregistered RSI campaign as an in-system task: guard the
    seed ledger, spawn the fixed campaign script as a SUBPROCESS, fold its
    published skills into the shared root, and note it in the chain.

    Cross-link is by the prereg content hash -- the only cross-link primitive
    that exists. A later task's FRESH graph.skill mount re-globs the shared root
    and sees the copied records for free (M4#4). Ledger-overlap and any nonzero
    exit raise, so the caller's crash-safety wrap files the brief under failed/
    with a runtime.task_error note -- one failure path, not two.
    """
    name = brief["campaign"]
    script = CAMPAIGN_SCRIPTS.get(name)
    if script is None:
        raise ValueError(f"unknown campaign {name!r}")

    # seed-ledger guard (non-negotiable invariant): the one prose ledger becomes
    # one enforced check at the scheduling boundary. Reject BEFORE spawning if
    # the declared dev∪heldout intersects any burned range (inclusive intervals).
    burned = _burned_ranges()
    for lo, hi in _declared_ranges(brief):
        for blo, bhi in burned:
            if lo <= bhi and blo <= hi:
                raise ValueError(
                    f"seed-ledger overlap: campaign {name!r} declares [{lo},{hi}] "
                    f"which hits burned [{blo},{bhi}]")

    out = rt.inbox.parent / "campaigns" / Path(brief_id).stem
    proc = subprocess.run(
        [sys.executable, str(script), "--out", str(out)],
        cwd=str(REPO_ROOT), env={**os.environ, "MUJOCO_GL": "egl"},
        capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"campaign {name!r} exited {proc.returncode}: {proc.stderr.strip()[-500:]}")

    copied = _copy_skills(out / "skills", rt.skills_root)
    rt.log.append("runtime.campaign_scheduled",
                  {"brief": brief_id, "campaign": name, "out": str(out),
                   "prereg_sha": _prereg_sha(out), "skills": copied})


def _process(rt: Runtime, path: Path) -> None:
    """Claim one brief, run it, file it under done/ or failed/."""
    brief_id = path.name
    claimed = rt.processing / brief_id
    try:
        os.rename(path, claimed)  # atomic claim; a racing worker gets FileNotFound
    except FileNotFoundError:
        return
    try:
        brief = json.loads(claimed.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError):
        # malformed: nothing to attribute a note to (design: failed/ with no note)
        os.replace(claimed, rt.failed / brief_id)
        return
    try:
        kind = brief.get("kind", "task")
        if kind == "task":
            _run_task(brief, rt.log, rt.skills_root)
        elif kind == "campaign":
            _run_campaign(brief, rt, brief_id)
        else:
            raise ValueError(f"unknown brief kind {kind!r}")
    except Exception as exc:  # noqa: BLE001 -- escape hatch: crash-safety lives here
        rt.log.append("runtime.task_error",
                      {"brief": brief_id, "task": brief.get("task"),
                       "error": repr(exc)})
        os.replace(claimed, rt.failed / brief_id)
        return
    os.replace(claimed, rt.done / brief_id)


def _pending(rt: Runtime) -> list[Path]:
    return sorted(rt.inbox.glob("*.json"), key=lambda p: p.stat().st_mtime)


def main(session_dir: str | Path, inbox: str | Path | None = None, *,
         drain: bool = False, poll_interval: float = 1.0) -> Runtime:
    """Boot, then drain the inbox once (``drain``) or poll it forever."""
    rt = boot(session_dir, inbox)
    if drain:
        while True:
            pending = _pending(rt)
            if not pending:
                return rt
            for p in pending:
                _process(rt, p)
    while True:
        for p in _pending(rt):
            _process(rt, p)
        time.sleep(poll_interval)


def _cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--inbox", default=None,
                    help="brief inbox dir (default <session-dir>/inbox)")
    ap.add_argument("--drain", action="store_true",
                    help="process pending briefs then exit (default: poll forever)")
    ap.add_argument("--poll-interval", type=float, default=1.0)
    args = ap.parse_args()
    main(args.session_dir, args.inbox, drain=args.drain,
         poll_interval=args.poll_interval)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
