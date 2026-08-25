#!/usr/bin/env python3
"""Run the clear_build n4 (stack node) governed-evidence DEV campaign (design §5).

The preregistration is ``clear_build_prereg()`` from scripts/prereg_clear_build.py
-- place-g2's stack campaign fields on the clear_build dev block (49050-49349),
seeded from place-g2's both-families bundle (parent_final_sha b026831c). It runs
only after the v2 calibration cleared the §4 go/no-go (runs/clear-build-cal-v2,
proceed=true; the stack-first reorder made the governed node the one every chain
reaches and dies at).

PHASED: this runs the DEV generations only. Held-out (49350-49549) is deferred to
the next phase, so the run overrides ``heldout=()`` and run_campaign scores no
held-out block (paired_gate over zero seeds burns nothing). The full-held-out plan
stays sealed in runs/clear-build-cal-v2 (prereg 0f3de2e95e12) for that phase to
score on this campaign's frozen final bundle. Mount pattern mirrors
place_campaign._mount verbatim; nothing here is new machinery.

    PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python scripts/clear_build_campaign.py \
        --out runs/clear-build-g1
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.config import Mount, Patch, resolve_plan
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness.kernel import Kernel
from plugins.rsi.workload import run as rsi_run
from profiles import base_profile
from scripts.prereg_clear_build import (
    PARENT_FINAL_SHA,
    PARENT_STORE,
    clear_build_prereg,
)


def _mount(out: Path) -> Kernel:
    """base_profile + stack scripted driver + on-disk skill graph -- the
    place_campaign._mount pattern; embodiment.env / percept.model keep base refs."""
    plan = resolve_plan(base_profile(), patches=(
        Patch("clear_build_campaign", override=(
            Mount("policy.driver", "plugins.policies:stack_scripted_provider"),
            Mount("graph.skill", "plugins.graphs:skill_graph_provider",
                  {"root": str(out / "skills")}),)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog(out / "session-log"))
    kernel.mount(plan)
    return kernel


def _stamped_sha(prereg, kernel: Kernel) -> str:
    stamped = dataclasses.replace(
        prereg,
        env_provider=kernel.provider_ref("embodiment.env"),
        policy_provider=kernel.provider_ref("policy.driver"),
        percept_provider=kernel.provider_ref("percept.model"))
    return stamped.sha()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("runs/clear-build-g1"))
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    if (args.out / "index.jsonl").exists():
        print(f"{args.out} already holds a store; pass a fresh --out", file=sys.stderr)
        return 2

    # Dev-only phase: defer held-out (49350-49549) to the next phase.
    prereg = dataclasses.replace(clear_build_prereg(), heldout=())
    kernel = _mount(args.out)
    print(f"dev-only prereg sha {_stamped_sha(prereg, kernel)}  -> {args.out}")
    print(f"seeding from {PARENT_STORE} @ {PARENT_FINAL_SHA[:12]} "
          f"(held-out deferred: heldout=())\n", flush=True)

    out = rsi_run(prereg, args.out, kernel, workers=args.workers)
    r = out["result"]
    print(f"\ngenerations={r['generations']} promoted={r['promoted']} "
          f"final_sha={r['final_sha'][:12]} rules={r['rules']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
