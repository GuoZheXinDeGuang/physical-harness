#!/usr/bin/env python3
"""Isolated-node calibration for the grasp-cube discovery campaign (M6 §3b c3).

Runs the grasp-cube node's EpisodeSpec (skill "grasp": task=lift, pick_stages,
terminal_label, percept_noise 0.012, under the chain's clear_build_provider mount)
BASELINE (ungoverned) over the calibration block, and reports the numbers the §4
go/no-go reads: isolated base rate + grasp-stage first-failure attribution.

Isolated on purpose (design: "calibrate the ISOLATED node, not the whole chain --
cheaper episodes"). The node is reached with probability ~1 inside the chain (its
predecessors survey/classify/plan-order are deterministic), so the isolated base
rate IS the chain's grasp-cube conditional death rate, at ~0.57s/episode instead
of ~5.46s/chain. q_pre and the c3 ungoverned-node gate do not apply to a single
node (there is no predecessor to dilute and this IS the ungoverned node); the only
live §4 gates are 0%/100% degenerate and the >=0.90 null ceiling.

The specs come from the SHARED prereg (prereg_grasp_cube.build_prereg) so the
calibration and the campaign build episodes from ONE definition. Calibration
blocks NEVER gate (STATUS ledger); this only MEASURES.

    MUJOCO_GL=egl PYTHONPATH=. .venv/bin/python scripts/probe_grasp_cube.py \
        --seeds 50850:51000 --out runs/scripted-calibration/grasp-cube-cal/calibration.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from plugins.rsi.campaign import _specs, stage_attribution
from plugins.rsi.gate import _run
from prereg_grasp_cube import build_prereg


def _seeds(spec: str) -> list[int]:
    if ":" in spec:
        lo, hi = (int(x) for x in spec.split(":", 1))
        return list(range(lo, hi))
    return [int(x) for x in spec.split(",")]


def summarize(results: list[dict], n: int) -> dict:
    successes = int(sum(bool(r["success"]) for r in results))
    base_rate = successes / n if n else 0.0
    attribution = stage_attribution(results)  # grasp-stage first_failure histogram
    base_degenerate = successes == 0 or successes == n
    c2_ceiling = base_rate >= 0.90
    proceed = not (base_degenerate or c2_ceiling)
    return {
        "task": "lift", "node": "inventory_build:grasp-cube", "arm": "baseline",
        "n": n, "successes": successes, "base_rate": round(base_rate, 4),
        "stage_attribution": attribution,
        "go_no_go": {
            "base_degenerate_0_or_100": base_degenerate,
            "c2_at_or_above_0.90": c2_ceiling,
            "proceed": proceed,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", default="50850:51000",
                    help='calibration block; "S:E" (half-open) | "S1,S2,.." | "S"')
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    from plugins.rsi.parallel import default_executor

    seeds = _seeds(args.seeds)
    prereg = build_prereg()
    specs = _specs(seeds, prereg)          # SAME spec factory the campaign uses
    t0 = time.perf_counter()
    results = default_executor().map(
        _run, [(s, None) for s in specs], workers=args.workers)  # baseline: bundle=None
    dt = time.perf_counter() - t0
    summary = summarize(results, len(seeds))
    summary["seconds_total"] = round(dt, 3)
    summary["seconds_per_episode"] = round(dt / len(seeds), 3) if seeds else 0.0
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=1, sort_keys=True))
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
