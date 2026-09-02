"""Promote measured whole-task graphs into a skills root as PlanRecords.

The evolution door for plans: reads ``board.store.plan_index`` over the given
sessions, applies the selection rule (Jeffreys 95% lower bound of k/n >= theta
AND n >= n_min -- both explicit here, both recorded in the record's ``rule``),
re-runs Legal(G) on each candidate against the skill records with the real
goal, and publishes the survivors through ``InMemorySkillGraph.publish`` (the
same door the SkillRecords use). Refuses unless ``--mode evolution`` is
asserted, the runtime's own fail-safe default (two-state law: execution never
writes a skills root). Prints one JSON line per candidate, published or not,
with its lower bound.

    scripts/publish_plans.py --mode evolution --runs runs --skills-root skills \\
        session-a session-b --theta 0.8 --n-min 10 --goal stack='on(cubeA,cubeB)'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from board import store as bs
from harness import skill_library
from harness.protocol import PlanRecord, plan_lower_bound, to_plain
from harness.skill_record import SkillRecordError, validate_plan
from plugins.graphs import InMemorySkillGraph

#: Mirrors scripts/harness_runtime.MODES (not imported: that module drags the
#: simulator stack in). EXECUTION is the fail-safe default and publishes nothing.
MODES = ("execution", "evolution")


def merge(runs: Path, sessions: list[str]) -> list[dict]:
    """plan_index rows pooled across sessions by (task, graph_sha, embodiment, arm)."""
    pooled: dict[tuple, dict] = {}
    for name in sessions:
        path = bs.safe_child(runs, name, bs.is_session)
        if path is None:
            raise ValueError(f"unknown session {name!r}")
        for e in bs.plan_index(path):
            key = (e["task"], e["graph_sha"], e["embodiment"], e["arm"])
            p = pooled.get(key)
            if p is None:
                p = pooled[key] = {**e, "_L": 0.0, "sessions": [], "published_from": []}
                p["n"] = p["k"] = 0
                p["seeds"], p["blocks"] = [], []
            p["_L"] += e["L_mean"] * e["n"]
            p["n"] += e["n"]
            p["k"] += e["k"]
            p["seeds"] += e["seeds"]
            p["blocks"] += [b for b in e["blocks"] if b not in p["blocks"]]
            p["sessions"].append(name)
            p["published_from"].append({"session": name, "seqs": e["seqs"]})
    for p in pooled.values():
        p["L_mean"] = round(p.pop("_L") / p["n"], 4) if p["n"] else 0.0
    return [pooled[k] for k in sorted(pooled, key=lambda k: tuple(str(x) for x in k))]


def candidates(rows: list[dict], theta: float, n_min: int, goals: dict[str, list[str]],
               records) -> list[dict]:
    """One report line per pooled row: the record when the rule + Legal(G) hold,
    else the reason it was skipped."""
    out = []
    for e in rows:
        lower = plan_lower_bound(e["n"], e["k"])
        line = {"task": e["task"], "graph_sha": e["graph_sha"], "embodiment": e["embodiment"],
                "arm": e["arm"], "n": e["n"], "k": e["k"], "lower": lower, "published": False}
        if lower < theta or e["n"] < n_min:
            out.append({**line, "reason": f"rule: lower {lower} < theta {theta} or n {e['n']} < n_min {n_min}"})
            continue
        rec = to_plain(PlanRecord(
            id=e["graph_sha"], task=e["task"], goal=tuple(goals.get(e["task"], ())),
            graph=e["graph"], embodiment=e["embodiment"], arm=e["arm"],
            evidence={"n": e["n"], "k": e["k"], "L_mean": e["L_mean"],
                      "seed_blocks": e["blocks"], "sessions": e["sessions"]},
            rule={"theta": theta, "n_min": n_min, "lower": lower},
            published_from=e["published_from"]))
        try:
            validate_plan(rec, records, e["facts"], e["objects"])
        except SkillRecordError as exc:
            out.append({**line, "reason": str(exc)})
            continue
        out.append({**line, "record": rec})
    return out


def _goals(items: list[str]) -> dict[str, list[str]]:
    goals = {}
    for item in items:
        task, _, preds = item.partition("=")
        goals[task] = [p.strip() for p in preds.split(";") if p.strip()]
    return goals


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sessions", nargs="+")
    ap.add_argument("--mode", choices=MODES, default="execution")
    ap.add_argument("--runs", type=Path, default=Path("runs"))
    ap.add_argument("--skills-root", type=Path, required=True)
    ap.add_argument("--records", type=Path, default=skill_library.ROOT,
                    help="SkillRecordV0 dir the graphs are validated against")
    ap.add_argument("--theta", type=float, default=0.8)
    ap.add_argument("--n-min", type=int, default=10)
    ap.add_argument("--goal", action="append", default=[],
                    help="TASK=pred;pred -- the goal preds a task's record carries (Covered bites)")
    args = ap.parse_args(argv)
    if args.mode != "evolution":
        print(json.dumps({"error": f"publish_plans writes a skills root: refused in mode "
                                   f"{args.mode!r}; assert --mode evolution"}))
        return 3
    recs = skill_library.load_records(args.records)
    records = {**recs, **{r.id: r for r in recs.values()}}
    lines = candidates(merge(args.runs.resolve(), args.sessions), args.theta, args.n_min,
                       _goals(args.goal), records)
    graph = InMemorySkillGraph(root=str(args.skills_root))
    for line in lines:
        rec = line.pop("record", None)
        if rec is not None:
            line["published"], line["digest"] = True, graph.publish(rec)
        print(json.dumps(line, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
