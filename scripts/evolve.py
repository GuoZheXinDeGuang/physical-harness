"""The lightweight RSI loop: look -> try -> re-run -> publish, one round at a time.

Spawned by ``scripts/harness_runtime._run_evolve`` (an ``evolve`` brief, evolution
mode only) as its own process group. One round: run the task's seed suite in-process
(the SAME ``_mount_plan``/``task_brief``/``workload.run`` a task brief uses), read
each seed's first-death node + fault signature + per-node executor, let the built-in
proposer pick ONE change -- the first-death node's executor (a bound policy whose
``evidence.by_executor`` beats the measured rate) else a one-dimensional +/-20%
tunables perturbation of that node's driver (its card's mount params, applied via
``PH_MOUNT_PARAMS_OVERRIDE``) else nothing, with the honest reason -- re-run the
same seeds, and publish when the success count improves: the skill record with
the measured ``by_executor`` row folded in goes through the evolution-only skills
root door (``InMemorySkillGraph.publish``, the same one scripts/publish_plans.py
uses). Every round lands atomically in ``campaigns/evolve-<task>/campaign.json``
(rounds[], best, cursor, status); the runtime seals the ``rsi_step`` rows off it.
Cancel is checked at the round boundary (``--cancel-marker``); a resubmitted task
resumes from ``cursor``. Media never enters this file's outputs beyond paths read
from ``media/<task>/<seed>/index.json``.

    scripts/evolve.py --mode evolution --task kitchen_thaw --session runs/session-x \\
        --skills-root runs/session-x/skills --seeds 1 2 --rounds 3 --arm auto
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.config import Mount, Patch, Profile, resolve_plan, sha_json
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness import media
from harness.kernel import Kernel
from harness.manifest import discover, mount_params
from harness.protocol import SkillRecordV0, to_plain
from harness.registry import load_provider
from harness.skill_library import rearm, segment_specs
from plugins.graphs import InMemorySkillGraph
from plugins.task import workload
from scripts import harness_runtime as hr

MODES = ("execution", "evolution")
#: JSON ``{provider ref: {param: value}}`` merged over a card's mount params by
#: ``harness.manifest.mount_params`` -- how a tunables trial reaches a driver.
OVERRIDE_ENV = "PH_MOUNT_PARAMS_OVERRIDE"
PLANNER_REF = "scripts.evolve:planner_provider"


class EvolveStore:
    """``campaigns/evolve-<task>/campaign.json``, written atomically (tmp+rename)."""

    def __init__(self, session: Path, task: str) -> None:
        self.path = session / "campaigns" / f"evolve-{task}" / "campaign.json"

    def load(self) -> dict | None:
        return json.loads(self.path.read_text()) if self.path.exists() else None

    def save(self, doc: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True))
        os.replace(tmp, self.path)


# ── the executor-switch seam: a planner wrapper that stamps node.executor ────────

class _Forced:
    def __init__(self, inner, executors: dict) -> None:
        self._inner, self._executors = inner, dict(executors)

    def plan(self, brief):
        plan = dict(self._inner.plan(brief))
        plan["nodes"] = [{**n, "executor": self._executors[n["id"]]}
                         if n.get("id") in self._executors else n
                         for n in plan.get("nodes") or ()]
        return plan

    def __getattr__(self, name):
        return getattr(self._inner, name)


def planner_provider(inner: str, inner_params=None, executors=None) -> _Forced:
    return _Forced(load_provider(inner, dict(inner_params or {})), executors or {})


def _mount(binding: dict, skills_root: Path, executors: dict):
    plan = hr._mount_plan(binding, skills_root)
    if not executors:
        return plan
    m = next(m for m in plan.mounts if m.capability == "task.planner")
    forced = Mount("task.planner", PLANNER_REF,
                   {"inner": m.provider, "inner_params": dict(m.params),
                    "executors": dict(executors)})
    return resolve_plan(Profile("evolve", plan.mounts),
                        patches=(Patch("evolve", override=(forced,)),))


# ── look: the seed suite, in-process ──────────────────────────────────────────────

def _get(budgets, binding: dict, key: str, default):
    """Budget precedence: the brief's value, else the task binding's, else the default."""
    v = (budgets or {}).get(key)
    return binding.get(key, default) if v is None else v


def run_suite(task: str, binding: dict, seeds: list, arm: str, skills_root: Path,
              applied: dict, media_dir: Path | None = None, budgets: dict | None = None) -> dict:
    """{count, seeds: {seed: {success, first_death, fault, nodes}}, sha}. ``media_dir``
    (<session>/media) turns on the workload's segment recorder: kept-on-success clips."""
    os.environ[OVERRIDE_ENV] = json.dumps(applied["tunables"])
    per = {}
    brief = {**hr.task_brief(task, binding), "arm": arm}
    if media_dir is not None:
        brief["media_dir"] = str(media_dir)
    for seed in range(int(seeds[0]), int(seeds[1]) + 1):
        log = SessionLog()
        kernel = Kernel(CAPABILITIES, log=log)
        kernel.mount(_mount(binding, skills_root, applied["executors"]))
        out = workload.run(dict(brief), kernel, seed=seed,
                           max_replans=int(_get(budgets, binding, "max_replans", 3)),
                           max_actuations=int(_get(budgets, binding, "max_actuations", 3)),
                           segment_retries=int(binding.get("segment_retries", 0)))
        skills = {}
        for r in log.rows():
            if r["kind"] == "task.plan" and r["data"].get("graph"):
                skills.update({n["id"]: n["skill"] for n in r["data"]["graph"].get("nodes") or []})
        nodes, faults = out["nodes"], out.get("faults") or []
        per[str(seed)] = {
            "success": bool(out["success"]),
            "first_death": next((nid for nid, n in nodes.items() if not n["success"]), None),
            "fault": {k: faults[0].get(k) for k in ("kind", "node", "msg")} if faults else None,
            "nodes": {nid: {"skill": skills.get(nid), "success": bool(n["success"]),
                            "executor": n.get("executor") or "scripted"}
                      for nid, n in nodes.items()}}
    return {"count": sum(s["success"] for s in per.values()), "seeds": per, "sha": sha_json(per)}


# ── try: the built-in proposer ────────────────────────────────────────────────────

def _none(reason: str, node=None) -> dict:
    return {"kind": "none", "node": node, "detail": {"reason": reason}}


def _tunables(params: dict) -> tuple[dict, list]:
    """Numeric knobs + the key path they live under (``[tunables]`` table or top-level)."""
    t = params.get("tunables")
    nested = isinstance(t, dict)
    src = t if nested else params
    num = {k: v for k, v in src.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return num, (["tunables"] if nested else [])


def propose(before: dict, records: dict, emb: str, arm: str, binding: dict,
            round_no: int, applied: dict) -> dict:
    deaths = Counter(s["first_death"] for s in before["seeds"].values() if s["first_death"])
    if not deaths:
        return _none("no first death: every seed succeeded")
    node = deaths.most_common(1)[0][0]
    runs = [s["nodes"][node] for s in before["seeds"].values() if node in s["nodes"]]
    skill, current = runs[0]["skill"], runs[0]["executor"]
    rate = sum(r["success"] for r in runs) / len(runs)
    rec = records.get(skill)
    if rec is None:
        return _none(f"no skill record for {skill!r}", node)
    spec = segment_specs({skill: rec}, emb).get(skill) or {}
    bound = {"scripted", *(spec.get("policies") or {})}
    ev = rec.evidence.get(emb)
    cands = {k: v for k, v in (ev.by_executor if ev else {}).items()
             if k in bound and k != current and v.get("n")}
    if cands:
        best = max(sorted(cands), key=lambda k: cands[k]["k"] / cands[k]["n"])
        if cands[best]["k"] / cands[best]["n"] > rate:
            return {"kind": "executor", "node": node,
                    "detail": {"skill": skill, "from": current, "to": best,
                               "evidence": dict(cands[best]), "measured": rate}}
    ref = (rearm(spec, arm, current if current in bound else None).get("policy_provider")
           or binding["policy"])
    tun, path = _tunables(mount_params(ref))
    if not tun:
        return _none(f"no better executor evidence for {skill!r} and no tunables on {ref!r}", node)
    key = sorted(tun)[round_no % len(tun)]
    return {"kind": "tunables", "node": node,
            "detail": {"skill": skill, "executor": current, "ref": ref, "path": [*path, key],
                       "from": tun[key], "to": tun[key] * (1.2 if round_no % 2 else 0.8)}}


def apply(tried: dict, applied: dict) -> dict:
    out = {"executors": dict(applied["executors"]),
           "tunables": json.loads(json.dumps(applied["tunables"]))}
    d = tried["detail"]
    if tried["kind"] == "executor":
        out["executors"][tried["node"]] = d["to"]
    elif tried["kind"] == "tunables":
        cur = out["tunables"].setdefault(d["ref"], {})
        for p in d["path"][:-1]:
            cur = cur.setdefault(p, {})
        cur[d["path"][-1]] = d["to"]
    return out


# ── publish: evidence write-back through the evolution-only skills-root door ───────

def publish(skills_root: Path, rec, emb: str, tried: dict, after: dict) -> tuple[str, dict]:
    d = to_plain(rec)
    node, det = tried["node"], tried["detail"]
    key = det["to"] if tried["kind"] == "executor" else det["executor"]
    runs = [s["nodes"][node] for s in after["seeds"].values() if node in s["nodes"]]
    ev = d.setdefault("evidence", {}).setdefault(emb, {"n": 0, "k": 0})
    row = ev.setdefault("by_executor", {}).setdefault(key, {"n": 0, "k": 0})
    row["n"] += len(runs)
    row["k"] += sum(r["success"] for r in runs)
    if tried["kind"] == "tunables":
        b = d.setdefault("bindings", {}).setdefault(emb, {})
        slot = b.get("policies", {}).get(key, b)   # the policy entry; scripted rides the binding
        cur = slot.setdefault("params", {})
        for p in det["path"][:-1]:
            cur = cur.setdefault(p, {})
        cur[det["path"][-1]] = det["to"]
    return InMemorySkillGraph(root=str(skills_root)).publish(d), d


def _media(session: Path, task: str, seeds: list) -> list[str]:
    """Session-relative paths of the clips kept so far (harness.media index), the
    list the board's rsi_frames face returns verbatim."""
    return [f"media/{task}/{seed}/{ent['file']}"
            for seed in range(int(seeds[0]), int(seeds[1]) + 1)
            for ent in media.index_of(session / "media", task, seed).values()]


# ── the round loop ────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=MODES, default="execution")
    ap.add_argument("--task", required=True)
    ap.add_argument("--session", type=Path, required=True)
    ap.add_argument("--skills-root", type=Path, required=True)
    ap.add_argument("--seeds", type=int, nargs=2, default=None)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--arm", default="auto")
    ap.add_argument("--cancel-marker", type=Path, default=None)
    ap.add_argument("--max-replans", type=int, default=None)
    ap.add_argument("--max-actuations", type=int, default=None)
    args = ap.parse_args(argv)
    budgets = {"max_replans": args.max_replans, "max_actuations": args.max_actuations}
    if args.mode != "evolution":
        print(json.dumps({"error": f"evolve writes a skills root: refused in mode "
                                   f"{args.mode!r}; assert --mode evolution"}))
        return 3
    binding = discover().task_bindings.get(args.task)
    if binding is None:
        raise SystemExit(f"no task binding for {args.task!r}")
    records = hr._binding_records(binding)
    emb = hr.task_brief(args.task, binding)["embodiment"]

    store = EvolveStore(args.session, args.task)
    doc = store.load() or {"task": args.task, "session": args.session.name,
                           "seeds": list(args.seeds or [0, 1]), "arm": args.arm,
                           "rounds": [], "best": 0, "cursor": 0, "status": "running",
                           "applied": {"executors": {}, "tunables": {}}}
    seeds, arm, applied = doc["seeds"], doc["arm"], doc["applied"]
    doc["status"] = "running" if doc["cursor"] < args.rounds else "done"
    store.save(doc)
    base = None
    for r in range(doc["cursor"] + 1, args.rounds + 1):
        if args.cancel_marker is not None and args.cancel_marker.exists():
            doc["status"] = "cancelled"
            store.save(doc)
            return 3
        before = base or run_suite(args.task, binding, seeds, arm, args.skills_root, applied,
                                     media_dir=args.session / "media", budgets=budgets)
        tried = propose(before, records, emb, arm, binding, r, applied)
        after, published = before, False
        if tried["kind"] != "none":
            trial = apply(tried, applied)
            try:
                after = run_suite(args.task, binding, seeds, arm, args.skills_root, trial,
                                  media_dir=args.session / "media", budgets=budgets)
            except Exception as exc:  # noqa: BLE001 -- the trial's failure is the round's finding
                tried["detail"]["error"] = repr(exc)
                after = before
            published = after["count"] > before["count"]
            if published:
                applied = trial
                skill = tried["detail"]["skill"]
                tried["detail"]["digest"], d = publish(
                    args.skills_root, records[skill], emb, tried, after)
                records[skill] = SkillRecordV0.from_dict(d)   # later rounds build on what was published
        kept = after if published else before
        doc["best"] = max(int(doc["best"] or 0), kept["count"])
        doc["rounds"].append({
            "round": r, "tried": tried, "before": before["count"], "after": after["count"],
            "best": doc["best"], "suite_sha": after["sha"], "published": published,
            "media": _media(args.session, args.task, seeds), "ts": time.time()})
        doc["cursor"], doc["applied"] = r, applied
        store.save(doc)
        base = kept
    doc["status"] = "done"
    store.save(doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
