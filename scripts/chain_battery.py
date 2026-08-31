#!/usr/bin/env python3
"""E3: the long-horizon chain battery (local-archive/docs/retired-from-public/long-horizon-design.md §2).

Run the ``clear_build`` 4-node chain over a seed block under ONE mount config,
aggregating chain-success + a per-node first-death histogram read straight from
``workload.run``'s output (per-node success + node-level faults are already
emitted -- no workload change). The 3-arm study is this script run three times
with three ``--skills-root`` values:

  * baseline  -- ``--skills-root`` omitted (n4 ungoverned; base graph.skill has
                 no root, so skills() is empty)
  * governed  -- ``--skills-root`` a dir holding stack-g1 + place-g2's sealed
                 SkillRecord JSONs (n4 governed by BOTH families -- the E2 mount)
  * evolved   -- + any rule a Phase-2 dev campaign promotes (not built here)

    MUJOCO_GL=egl PYTHONPATH=. .venv/bin/python scripts/chain_battery.py \
        --seeds 40000 --skills-root runs/scripted-calibration/clear-build-gov/skills

This is measurement over existing pieces (base_profile + the card's binding +
workload.run); it seals nothing. It composes, it does not extend -- the one real
harness extension (the inter-node recovery rule) is Phase 2, gated on this
script's attribution first (§3c, §6.3). Every number is measured in simulation.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.config import Mount, Patch, resolve_plan
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness.kernel import Kernel
from harness.manifest import discover
from plugins.task import workload
from profiles import base_profile

TASK = "clear_build"


def _load_attr(ref: str):
    """Import ``module:attr`` and return the attribute (catalogue/oracles are
    ``type`` objects, so a ref, never JSON in a brief)."""
    module_name, attr = ref.split(":", 1)
    import importlib

    return getattr(importlib.import_module(module_name), attr)


def _mount_plan(binding: dict, skills_root: str | None):
    """base_profile + the card's policy/planner, and graph.skill pointed at the
    governance root when one is given (else the base's rootless, empty store)."""
    override = [
        Mount("task.planner", binding["planner"]),
        Mount("policy.driver", binding["policy"]),
    ]
    if skills_root is not None:
        override.append(Mount("graph.skill", "plugins.graphs:skill_graph_provider",
                              {"root": str(skills_root)}))
    return resolve_plan(base_profile(), patches=(
        Patch("chain_battery", override=tuple(override)),))


def _first_death(out: dict) -> str:
    """Which node killed the chain, or "none" on success -- the last fault's node
    (budget/invalid faults carry no node -> their kind)."""
    if out["success"]:
        return "none"
    fault = out["faults"][-1]
    return fault.get("node") or fault["kind"]


def _seeds(spec: str) -> list[int]:
    """"40000" -> [40000]; "40000:40010" -> [40000..40009]; "a,b,c" -> list."""
    if ":" in spec:
        lo, hi = (int(x) for x in spec.split(":", 1))
        return list(range(lo, hi))
    return [int(x) for x in spec.split(",")]


def run_battery(seeds: list[int], skills_root: str | None, *,
                max_replans: int, max_actuations: int, verbose: bool) -> dict:
    binding = discover().task_bindings.get(TASK)
    if binding is None:
        raise SystemExit(f"no task binding for {TASK!r}; is plugins/clear_build installed?")
    catalogue = _load_attr(binding["catalogue"])
    oracles = _load_attr(binding["oracles"])
    plan_sha = _mount_plan(binding, skills_root).sha()[:12]

    deaths: Counter[str] = Counter()
    per_episode: list[dict] = []
    successes = 0
    for seed in seeds:
        kernel = Kernel(CAPABILITIES, log=SessionLog())
        kernel.mount(_mount_plan(binding, skills_root))
        brief = {"task": TASK, "catalogue": catalogue, "oracles": oracles}
        t0 = time.perf_counter()
        out = workload.run(brief, kernel, seed=seed,
                           max_replans=max_replans, max_actuations=max_actuations)
        dt = time.perf_counter() - t0
        successes += bool(out["success"])
        deaths[_first_death(out)] += 1
        row = {"seed": seed, "success": bool(out["success"]),
               "first_death": _first_death(out), "replans": out["replans"],
               "actuations": out["actuations"], "seconds": round(dt, 3),
               "nodes": {nid: n["success"] for nid, n in out["nodes"].items()}}
        per_episode.append(row)
        if verbose:
            print(f"  seed {seed}: success={row['success']} "
                  f"death={row['first_death']} replans={row['replans']} "
                  f"actuations={row['actuations']} {row['seconds']}s "
                  f"nodes={row['nodes']}")

    n = len(seeds)
    total_s = sum(r["seconds"] for r in per_episode)
    return {
        "task": TASK, "arm": "baseline" if skills_root is None else "governed",
        "skills_root": skills_root, "mount_plan_sha": plan_sha, "n": n,
        "chain_success": successes, "chain_rate": round(successes / n, 4) if n else 0.0,
        "first_death_histogram": dict(deaths),
        "seconds_total": round(total_s, 3),
        "seconds_per_episode": round(total_s / n, 3) if n else 0.0,
        "episodes": per_episode,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", default="40000",
                    help='"S" | "S:E" (half-open) | "S1,S2,..."; default one smoke seed')
    ap.add_argument("--skills-root", default=None,
                    help="governance mount root (dir of sealed SkillRecord JSONs); "
                         "omit for the ungoverned baseline arm")
    ap.add_argument("--max-replans", type=int, default=2)
    ap.add_argument("--max-actuations", type=int, default=6,
                    help="4 nodes + replan headroom (design config sweep uses 4-6)")
    ap.add_argument("--quiet", action="store_true", help="suppress per-episode lines")
    args = ap.parse_args(argv)

    summary = run_battery(_seeds(args.seeds), args.skills_root,
                          max_replans=args.max_replans,
                          max_actuations=args.max_actuations,
                          verbose=not args.quiet)
    print(json.dumps({k: v for k, v in summary.items() if k != "episodes"},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
