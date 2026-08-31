#!/usr/bin/env python3
"""M6 chain battery: paired baseline vs governed on the ``inventory_build`` chain.

Claim (a)+(b) of the prereg (runs/scripted-calibration/inventory-build-cal, sha f7b3a89e8087): does the
EXISTING M5 governance bundle (stack-g1 regrasp + place-g2 replace x2, both
families) lift the heterogeneous 11-node chain boolean when mounted at its
``build-stack`` node? No new promotion -- this MEASURES a transfer, it seals
nothing.

Both arms run the SAME seed over the SAME mount as ``probe_inventory_build.py``
(base profile + the card's planner/policy + predicates brief), the governed arm
additionally mounting ``graph.skill`` at the assembled skills-root. Outcomes are
paired by seed and scored with the exact two-sided McNemar test on the chain
boolean (plugins.rsi.stats.power.mcnemar_p) -- the prereg's gate method. Fixed
2-arm battery, so the full block runs for both arms; power-scaled dev sizing is a
generation-loop concern and does not apply.

    MUJOCO_GL=egl PYTHONPATH=. .venv/bin/python scripts/chain_battery_inventory.py \
        --seeds 50150:50450 --skills-root runs/scripted-calibration/inventory-build-gov/skills \
        --out runs/scripted-calibration/inventory-build-cal/dev.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.rsi.stats.power import mcnemar_p

TASK = "inventory_build"
GOVERNED_NODE = "build-stack"
MAX_REPLANS, MAX_ACTUATIONS = 3, 40


def _load_attr(ref: str):
    mod, attr = ref.split(":", 1)
    return getattr(importlib.import_module(mod), attr)


def discover_binding() -> dict:
    from harness.manifest import discover
    binding = discover().task_bindings.get(TASK)
    if binding is None:
        raise SystemExit(f"no task binding for {TASK!r}; is plugins/inventory_build installed?")
    return binding


def _run_one(arg: tuple[int, str | None, str]) -> dict:
    """One chain under one mount. ``skills_root=None`` is the ungoverned baseline
    (the base's rootless empty store); a dir mounts ``graph.skill`` there so the
    manipulate nodes assemble their per-task governance bundles. ``arm`` is the
    caller-declared label -- the baseline arm may itself carry a root (an ablation
    that governs SOME nodes but not the one under test), so the label is explicit,
    not derived from ``skills_root is None``. Clean Pool task -- builds its own
    kernel, no sim state crosses the fork."""
    seed, skills_root, arm = arg
    from harness.config import Mount, Patch, resolve_plan
    from harness.definitions import CAPABILITIES
    from harness.events import SessionLog
    from harness.kernel import Kernel
    from plugins.task import workload
    from profiles import base_profile

    binding = discover_binding()
    catalogue = _load_attr(binding["catalogue"])
    oracles = _load_attr(binding["oracles"])
    predicates = _load_attr(binding["predicates"])
    override = [
        Mount("task.planner", binding["planner"]),
        Mount("policy.driver", binding["policy"]),
    ]
    if skills_root is not None:
        override.append(Mount("graph.skill", "plugins.graphs:skill_graph_provider",
                              {"root": str(skills_root)}))
    plan = resolve_plan(base_profile(), patches=(
        Patch("chain_battery_inventory", override=tuple(override)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog())
    kernel.mount(plan)
    brief = {"task": TASK, "catalogue": catalogue, "oracles": oracles,
             "predicates": predicates}
    t0 = time.perf_counter()
    out = workload.run(brief, kernel, seed=seed,
                       max_replans=MAX_REPLANS, max_actuations=MAX_ACTUATIONS)
    dt = time.perf_counter() - t0
    death = "none" if out["success"] else (out["faults"][-1].get("node")
                                           or out["faults"][-1]["kind"])
    return {"seed": seed, "arm": arm,
            "success": bool(out["success"]), "first_death": death,
            "replans": out["replans"], "actuations": out["actuations"],
            "seconds": round(dt, 3)}


def _seeds(spec: str) -> list[int]:
    if ":" in spec:
        lo, hi = (int(x) for x in spec.split(":", 1))
        return list(range(lo, hi))
    return [int(x) for x in spec.split(",")]


def _arm_summary(rows: list[dict]) -> dict:
    n = len(rows)
    succ = sum(r["success"] for r in rows)
    return {"n": n, "chain_success": succ,
            "chain_rate": round(succ / n, 4) if n else 0.0,
            "first_death_histogram": dict(Counter(r["first_death"] for r in rows))}


def summarize(base: list[dict], gov: list[dict], *, alpha: float, min_fixed: int) -> dict:
    b = {r["seed"]: r for r in base}
    g = {r["seed"]: r for r in gov}
    seeds = sorted(set(b) & set(g))
    # discordant pairs on the chain boolean: fixed = governed rescued a baseline
    # failure; broken = governance regressed a baseline success.
    fixed = sum(g[s]["success"] and not b[s]["success"] for s in seeds)
    broken = sum(b[s]["success"] and not g[s]["success"] for s in seeds)
    p = mcnemar_p(fixed, broken)
    base_rate = _arm_summary(base)["chain_rate"]
    gov_rate = _arm_summary(gov)["chain_rate"]
    passes = (p < alpha) and (fixed >= min_fixed) and (fixed > broken)
    return {
        "task": TASK, "n_paired": len(seeds),
        "baseline": _arm_summary(base), "governed": _arm_summary(gov),
        "delta_pp": round((gov_rate - base_rate) * 100, 2),
        "mcnemar": {"fixed": fixed, "broken": broken, "p_value": p,
                    "alpha": alpha, "min_fixed": min_fixed},
        "pass": bool(passes),
    }


def _selfcheck() -> None:
    """The discordance count + gate, on a hand-built pair set (mcnemar_p itself
    is covered by tests/test_power.py)."""
    def rows(flags):
        return [{"seed": i, "success": f, "first_death": "none" if f else "build-stack"}
                for i, f in enumerate(flags)]
    base = rows([True, False, False, True])
    gov = rows([True, True, True, False])
    s = summarize(base, gov, alpha=0.05, min_fixed=1)
    assert s["mcnemar"] == {"fixed": 2, "broken": 1, "p_value": mcnemar_p(2, 1),
                            "alpha": 0.05, "min_fixed": 1}, s["mcnemar"]
    assert s["pass"] is (mcnemar_p(2, 1) < 0.05 and 2 >= 1 and 2 > 1), s["pass"]
    print("selfcheck OK")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selfcheck", action="store_true", help="run the discordance self-check and exit")
    ap.add_argument("--seeds", help='"S:E" (half-open) | "S1,S2,.." | "S"')
    ap.add_argument("--skills-root", default="runs/scripted-calibration/inventory-build-gov/skills")
    ap.add_argument("--baseline-root", default=None,
                    help="root for the reference arm (default None = ungoverned); "
                         "set to an ablation root to measure the DELTA of the extra "
                         "governance in --skills-root over it")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--min-fixed", type=int, default=3)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.selfcheck:
        _selfcheck()
        return 0
    if not args.seeds:
        ap.error("--seeds is required unless --selfcheck")

    from harness.executor import LocalPoolExecutor

    seeds = _seeds(args.seeds)
    tasks = ([(s, args.baseline_root, "baseline") for s in seeds]
             + [(s, args.skills_root, "governed") for s in seeds])
    rows = LocalPoolExecutor().map(_run_one, tasks, workers=args.workers)
    base = sorted((r for r in rows if r["arm"] == "baseline"), key=lambda r: r["seed"])
    gov = sorted((r for r in rows if r["arm"] == "governed"), key=lambda r: r["seed"])
    summary = summarize(base, gov, alpha=args.alpha, min_fixed=args.min_fixed)
    summary["roots"] = {"baseline": args.baseline_root, "governed": args.skills_root}
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({**summary, "episodes": {"baseline": base,
                                        "governed": gov}}, indent=1, sort_keys=True))
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
