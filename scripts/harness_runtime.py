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
90000,"max_replans":3,"max_actuations":3}``. The runtime resolves the task STRING
to its policy/planner/catalogue/oracles through the UNION of installed plugin
manifests at boot (``harness.manifest.discover``) and stamps them server-side;
adding a task is installing a plugin dir (a filesystem act), never a brief. A
manifest declaring ``actuation:real`` is refused at boot -- a real-actuator
embodiment is a DIFFERENT authenticated runtime, never a brief (nor a card) away.

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
import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from board.store import parse_ledger
from harness.config import Mount, Patch, resolve_plan
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness.kernel import Kernel
from harness.manifest import discover
from plugins.task import workload
from profiles import base_profile

#: The one prose seed-ledger the guard enforces (board.store.parse_ledger).
STATUS_MD = REPO_ROOT / "STATUS.md"


def _load_attr(ref: str):
    """Import ``module:attr`` and return the ATTRIBUTE (not call it).

    A task binding names its catalogue/oracles by ref -- data authored on the
    skill side (``type`` objects, not JSON), so they cannot ride a brief. This is
    the read half of the same ``module:attr`` crossing ``load_provider`` uses for
    factories, minus the call: the attribute IS the value.
    """
    module_name, attr = ref.split(":", 1)
    return getattr(importlib.import_module(module_name), attr)


#: The two session modes. EXECUTION is the fail-safe default: a real task run
#: never triggers RSI. EVOLUTION is the only mode that may accept a campaign
#: brief and let a campaign write the shared skills root.
MODES = ("execution", "evolution")


@dataclass(frozen=True)
class Runtime:
    """The booted state: the four intake dirs, the shared skills root, the log.

    ``mode`` is fixed at first boot (write-once MODE file) and immutable across
    restart. ``skills_manifest`` is the sorted content-digest stem set sealed in
    the ``runtime.boot`` row -- the baseline the per-execution-task immutability
    audit folds against, so an out-of-band skills-root mutation is machine-caught.
    """

    inbox: Path
    processing: Path
    done: Path
    failed: Path
    skills_root: Path
    log: SessionLog
    mode: str = "execution"
    skills_manifest: tuple[str, ...] = ()
    #: task STRING -> {policy, planner, catalogue, oracles} and campaign name ->
    #: script, both folded from the installed manifests at boot -- the
    #: authority-laundering allowlists, no longer welded into this module.
    task_bindings: dict[str, dict] = field(default_factory=dict)
    campaigns: dict[str, str] = field(default_factory=dict)


def _skills_manifest(skills_root: Path) -> list[str]:
    """Sorted content-digest stems under the skills root. Filenames already ARE
    content digests, so this list IS the skill set -- set-equality on it is the
    immutability check, no rehash needed."""
    return sorted(f.stem for f in skills_root.glob("*.json"))


def boot(session_dir: str | Path, inbox: str | Path | None = None, *,
         mode: str = "execution") -> Runtime:
    """Load-or-fresh the session chain, make the intake dirs, re-queue crashes.

    The session's ``mode`` is written once to ``<session-dir>/MODE`` at first
    boot and asserted on every re-boot -- a mismatched ``--mode`` is refused, not
    overwritten (write-once). Boot also seals a ``runtime.boot`` row
    ``{mode, skills_manifest, mount_plan_sha}``: row 0 of a fresh chain, or -- for
    a session that predates MODE -- retrofitted at the tail with a ``migrated``
    marker (appending never rewrites the existing chain, so no sealed sha moves).
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
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

    # write-once mode: fixed at first boot, immutable across restart.
    mode_file = session_dir / "MODE"
    if mode_file.exists():
        recorded = mode_file.read_text().strip()
        if recorded != mode:
            raise ValueError(
                f"{mode_file} is {recorded!r}; refusing to re-boot as {mode!r} "
                "(MODE is write-once)")
    else:
        mode_file.write_text(mode)

    log = (SessionLog.load(log_dir) if (log_dir / "rows.jsonl").exists()
           else SessionLog(log_dir))

    # restart-resume: anything claimed but not finished goes back to inbox.
    # ponytail: at-least-once -- a task that crashed after its plan_complete note
    # re-runs and appends a second note; dedup by brief-id only if soak shows it
    # hurts.
    for p in processing.glob("*.json"):
        os.replace(p, inbox / p.name)

    # boot seal: the immutability baseline comes from the SEALED row (catches a
    # skills-root mutation that happened between boots), not a fresh recompute.
    # A fresh session seals row 0; a pre-MODE session gets it retrofitted at the
    # tail (marked migrated) since row 0 is already spoken for.
    boot_row = next((r for r in log.rows() if r["kind"] == "runtime.boot"), None)
    if boot_row is None:
        manifest = _skills_manifest(skills_root)
        seal = {"mode": mode, "skills_manifest": manifest,
                "mount_plan_sha": resolve_plan(base_profile()).sha()}
        if log.rows():
            seal["migrated"] = True  # predates MODE; not row 0
        log.append("runtime.boot", seal)
        baseline = tuple(manifest)
    else:
        baseline = tuple(boot_row["data"]["skills_manifest"])

    # The authority allowlists are the manifest union at boot (discover() raises
    # on an actuation:real card, so the sim runtime refuses to boot with one).
    registry = discover()
    return Runtime(inbox, processing, done, failed, skills_root, log, mode,
                   baseline, registry.task_bindings, registry.campaigns)


def _mount_plan(binding: dict, skills_root: Path):
    """A fresh MountPlan for this task: base profile + the task's sim policy +
    planner + the shared skills root (so a fresh graph.skill mount re-globs RSI's
    output). policy and planner come from the task binding (a manifest), so
    swapping either is a card edit, never a base one."""
    return resolve_plan(base_profile(), patches=(
        Patch("runtime", override=(
            Mount("task.planner", binding["planner"]),
            Mount("policy.driver", binding["policy"]),
            Mount("graph.skill", "plugins.graphs:skill_graph_provider",
                  {"root": str(skills_root)}),)),))


def _run_task(brief: dict, rt: Runtime) -> dict:
    """Build a fresh kernel on the shared log and run one governed plan loop.

    The task STRING resolves to its binding through the installed manifests; an
    unknown task (no card declares it) is refused HERE, before any mount -- a
    brief cannot conjure a task no plugin provides. catalogue/oracles are
    skill-authored ``type`` objects imported by ref from the binding, never
    carried in the JSON brief.
    """
    task = brief["task"]
    binding = rt.task_bindings.get(task)
    if binding is None:
        raise ValueError(f"no task binding for {task!r}; install a plugin that "
                         f"declares it (known: {sorted(rt.task_bindings)})")
    seed = int(brief.get("seed", 0))
    max_replans = int(brief.get("max_replans", 3))
    # clear_table's two pick nodes need one extra actuation of headroom, matching
    # scripts/task_plan.py's default.
    max_actuations = int(brief.get("max_actuations",
                                   4 if task == "clear_table" else 3))
    kernel = Kernel(CAPABILITIES, log=rt.log)
    kernel.mount(_mount_plan(binding, rt.skills_root))
    wbrief = {"task": task, "catalogue": _load_attr(binding["catalogue"]),
              "oracles": _load_attr(binding["oracles"])}
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
    script = rt.campaigns.get(name)
    if script is None:
        raise ValueError(f"unknown campaign {name!r}")
    # A manifest carries the script path relative to the repo; REPO_ROOT / an
    # already-absolute path is that absolute path, so both forms resolve here.
    script = REPO_ROOT / script

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


#: A brief is a selector plus budgets -- nothing else. Providers are chosen
#: server-side (the manifest union's task_bindings / campaigns); any other key
#: is rejected.
_BRIEF_KEYS = {
    "task": {"kind", "task", "seed", "max_replans", "max_actuations"},
    "campaign": {"kind", "campaign", "dev", "heldout"},
}


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
        unknown = set(brief) - _BRIEF_KEYS.get(kind, set())
        if unknown:
            # Rejection, not neutralization: a brief is selector+budgets only, and
            # an injected key (a provider ref, say) must fail loudly rather than
            # ride along ignored until some future reader starts honoring it.
            raise ValueError(f"unknown brief keys {sorted(unknown)}")
        if kind == "task":
            # execution mounts a frozen skills root; a mid-session out-of-band
            # mutation (some record added or removed since the boot seal) must
            # fail loudly rather than let a task run against skills it wasn't
            # admitted with. Evolution's _copy_skills legitimately grows the root,
            # so the audit is execution-only.
            if rt.mode == "execution":
                current = _skills_manifest(rt.skills_root)
                if set(current) != set(rt.skills_manifest):
                    raise ValueError(
                        "skills-root mutated mid-session (execution mode): "
                        f"boot manifest {list(rt.skills_manifest)} != {current}")
            _run_task(brief, rt)
        elif kind == "campaign":
            # v4.1 hard rule: a campaign brief is accepted ONLY in evolution mode.
            # Rejection, not neutralization -- same pattern as the injected-key
            # defense: a real task run provably triggers no RSI.
            if rt.mode != "evolution":
                raise ValueError(
                    f"campaign briefs are accepted only in evolution mode "
                    f"(session mode is {rt.mode!r})")
            _run_campaign(brief, rt, brief_id)
        else:
            raise ValueError(f"unknown brief kind {kind!r}")
    except Exception as exc:  # noqa: BLE001 -- escape hatch: crash-safety lives here
        rt.log.append("runtime.task_error",
                      {"brief": brief_id, "task": brief.get("task"),
                       "error": repr(exc)})
        os.replace(claimed, rt.failed / brief_id)
        return
    try:
        os.replace(claimed, rt.done / brief_id)
    except OSError as exc:
        # A filesystem failure at the very last rename must not kill the loop;
        # the brief stays in processing/ and re-queues on the next boot.
        rt.log.append("runtime.task_error",
                      {"brief": brief_id, "task": brief.get("task"),
                       "error": f"done-rename failed: {exc!r}"})


def _pending(rt: Runtime) -> list[Path]:
    return sorted(rt.inbox.glob("*.json"), key=lambda p: p.stat().st_mtime)


def main(session_dir: str | Path, inbox: str | Path | None = None, *,
         drain: bool = False, poll_interval: float = 1.0,
         mode: str = "execution") -> Runtime:
    """Boot, then drain the inbox once (``drain``) or poll it forever."""
    rt = boot(session_dir, inbox, mode=mode)
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
    ap.add_argument("--mode", choices=MODES, default="execution",
                    help="session mode, fixed write-once at first boot "
                         "(default: execution -- campaigns rejected)")
    ap.add_argument("--drain", action="store_true",
                    help="process pending briefs then exit (default: poll forever)")
    ap.add_argument("--poll-interval", type=float, default=1.0)
    args = ap.parse_args()
    main(args.session_dir, args.inbox, drain=args.drain,
         poll_interval=args.poll_interval, mode=args.mode)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
