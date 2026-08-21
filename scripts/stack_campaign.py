#!/usr/bin/env python3
"""One-command RSI campaign for the R2 Stack task (round 79 wiring).

Same shape as scripts/demo_campaign.py, one capability swapped: the frozen
policy is the eight-phase stack driver instead of the lift driver, mounted by
patching `policy.driver` over the base profile. embodiment.env and percept.model
stay the base reference providers -- Stack is another robosuite task on the same
embodiment, not another embodiment. Skills persist to disk exactly as the demo.

    PYTHONPATH=. .venv/bin/python scripts/stack_campaign.py

Every number is measured in simulation. Seed blocks (41000+/42000+) are this
campaign's own and are burn-once, not part of any published claim in docs/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.config import Mount, Patch, resolve_plan
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness.kernel import Kernel
from plugins.embodiment_robosuite.env import stack_stages
from plugins.rsi.campaign import Preregistration
from plugins.rsi.workload import run as rsi_run
from profiles import base_profile


def stack_prereg() -> Preregistration:
    """The preregistered stack campaign. Factored out so its `.sha()` can be
    checked as a literal without mounting a kernel or running an episode.

    env/policy/percept provider refs are deliberately absent: rsi_run stamps
    them from the resolved mount plan before the campaign runs, so the mount
    identity that produced the episodes is what enters the content hash.
    """
    # horizon stays EpisodeSpec's 900 default -- no new field (round 79 件2).
    # The stack schedule is 177 steps and each recovery program splices ~111
    # more, so base + max_generations=2 = 177 + 2*111 = 399 << 900. Two
    # constraints keep it there: search_recovery MUST stay False (a searched
    # program can grow past 111), and a probe_regrasp program (254 steps) would
    # top 900 -- neither is used here.
    return Preregistration(
        dev=tuple(range(41000, 42000)), heldout=tuple(range(42000, 42200)),
        # The calibrated operating point is the policy's one-shot percept sd,
        # NOT recovery_sensor_sd (the re-read the repair takes on a fire).
        percept_noise=0.012,
        task="stack", policy="scripted", critic_budget=0, action_budget=0,
        recovery_sensor_sd=0.020, max_generations=2, scale_dev_by_power=True,
        stages=stack_stages(), terminal_label=True, require_judgement=True,
        prior_discordance_yield=0.4, prior_judgement_yield=0.25,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("runs/stack"),
                        help="campaign store + skill graph output (default runs/stack)")
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()
    if (args.out / "index.jsonl").exists():
        print(f"{args.out} already holds a campaign store; pass a fresh --out", file=sys.stderr)
        return 2

    # Base profile, patched: the stack policy driver, and the skill graph
    # persisted to disk. embodiment.env / percept.model keep the base refs.
    plan = resolve_plan(base_profile(), patches=(
        Patch("stack", override=(
            Mount("policy.driver", "plugins.policies:stack_scripted_provider"),
            Mount("graph.skill", "plugins.graphs:skill_graph_provider",
                  {"root": str(args.out / "skills")}),)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog(args.out / "session-log"))
    kernel.mount(plan)
    print(f"mount plan sha {plan.sha()[:12]}  "
          f"({len(plan.mounts)} capabilities mounted)\n")

    out = rsi_run(stack_prereg(), args.out, kernel, workers=args.workers)

    print("\n=== published skills ===")
    graph = kernel.resolve("graph.skill", consumer="stack")
    for digest, skill in zip(out["skills"], graph.skills()):
        gate = skill["effects"]["dev_gate_vs_parent"]
        print(f"  {digest[:12]}  gen{skill['generation']}  "
              f"trigger {skill['preconditions']['feature']} "
              f"{skill['preconditions']['op']} {skill['preconditions']['threshold']:.4g}  "
              f"dev {gate['base_rate']:.1%} -> {gate['governed_rate']:.1%} "
              f"({gate['fixed']} fixed / {gate['broken']} broken)")
    heldout = out["result"].get("heldout") or {}
    if heldout:
        print(f"\nheld-out (n={heldout['n']}): {heldout['base_rate']:.1%} -> "
              f"{heldout['governed_rate']:.1%}, {heldout['fixed']} fixed / "
              f"{heldout['broken']} broken, p={heldout['p_value']:.2g}")
    print(f"\nartifacts: {args.out}/  (content-addressed store, chained session log, "
          f"skill records under skills/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
