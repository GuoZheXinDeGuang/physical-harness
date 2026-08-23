#!/usr/bin/env python3
"""One-command RSI campaign for the R2 place repair (round 90 rung 3).

Mirrors scripts/stack_campaign.py: same embodiment, same eight-phase stack
driver, same stage chain and terminal label. What differs is that this campaign
does not start from scratch -- it SEEDS from the sealed stack-g1 final bundle
(the promoted regrasp skill) and grows a place-shaped repair on top of it. Two
things carry that:

* ``recovery_name="replace"`` -- every proposed rule wires the place-shaped
  primitive (plugins.rsi.repertoire), not the grasp-shaped regrasp.
* ``critic_budget=1`` -- round 90 rung 2's probes showed the place failure is
  invisible to proprioception (the cube stays held but seated wrong), so the
  trigger must read a privileged residual. The search sees privileged features
  only because the budget admits them.

Nothing else is place-specific: the campaign classifies rules by the same paired
gate, measures gain against the (rebuilt, sha-asserted) parent, and scores
held-out once, exactly as stack_campaign does.

    PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python scripts/place_campaign.py \
        --out runs/place-g1

Seed blocks are burn-once. This is the round-92 rematch (place-g2): dev
46267-46999 and heldout 47200-47399 are fresh, sidestepping the blocks v1 burned
(46000-46266 to selection, 47000-47199 to final scoring). The --smoke path NEVER
touches them (see main()).
"""

from __future__ import annotations

import argparse
import dataclasses
import shutil
import sys
import tempfile
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

#: The sealed stack-g1 store this campaign seeds from. Absolute because the
#: campaign may run from a worktree whose cwd is not the repo root.
PARENT_STORE = "/home/yusenzlabpc/Desktop/physical-harness/runs/stack-g1"
#: The final promoted child_sha in PARENT_STORE, read programmatically from
#: runs/stack-g1/index.jsonl -> campaign_result.final_sha (== gen1 child_sha,
#: the only promoted generation). run_campaign asserts the rebuilt parent bundle
#: hashes to exactly this before seeding, and fails loud otherwise.
PARENT_FINAL_SHA = "2f5f3756f23c51aaff93685412b760ae42132b72e9e9229d31140c1a9d562c1a"


def place_prereg() -> Preregistration:
    """The preregistered place campaign. Factored out (like stack_prereg) so its
    ``.sha()`` is inspectable without mounting a kernel or running an episode.

    Deviations from stack_campaign.stack_prereg() and why:
    * critic_budget 0 -> 1 : the place trigger must read a privileged residual
      (round 90 rung 2).
    * recovery_name -> "replace" : the place-shaped repair.
    * parent_store / parent_final_sha set : seed from stack-g1.
    * prior_discordance_yield 0.4 -> 0.15 : this brief's value; place fires
      rarer than the grasp regrasp, so the generation-1 prior is lower.
    * dev/heldout : FRESH blocks for the round-92 rematch (place-g2). v1 burned
      46000-46266 (its candidate SAW those seeds -- selection contamination) and
      47000-47199 (v1's final held-out scoring), so this run uses dev
      46267-46999 (733 seeds) and heldout 47200-47399; 47400-47599 stays reserved
      for reproduction.
    Everything else matches stack_prereg: policy="scripted", action_budget=0,
    recovery_sensor_sd=0.020, max_generations=2, scale_dev_by_power=True,
    stages=stack_stages(), terminal_label=True, require_judgement=True,
    prior_judgement_yield=0.25; and alpha/earliness/fp_penalty/min_fixed/power/
    search_recovery/screen_triggers all stay the dataclass defaults stack uses.
    env/policy/percept provider refs are absent: rsi_run stamps them from the
    resolved mount plan, so the mount identity enters the content hash.
    """
    return Preregistration(
        dev=tuple(range(46267, 47000)), heldout=tuple(range(47200, 47400)),
        percept_noise=0.012,
        task="stack", policy="scripted", critic_budget=1, action_budget=0,
        recovery_sensor_sd=0.020, max_generations=2, scale_dev_by_power=True,
        stages=stack_stages(), terminal_label=True, require_judgement=True,
        prior_discordance_yield=0.15, prior_judgement_yield=0.25,
        recovery_name="replace",
        parent_store=PARENT_STORE, parent_final_sha=PARENT_FINAL_SHA,
    )


def _mount(out: Path) -> Kernel:
    """Base profile patched with the stack policy driver and the on-disk skill
    graph -- identical to stack_campaign; embodiment.env / percept.model keep the
    base refs (place is another robosuite task on the same embodiment)."""
    plan = resolve_plan(base_profile(), patches=(
        Patch("place", override=(
            Mount("policy.driver", "plugins.policies:stack_scripted_provider"),
            Mount("graph.skill", "plugins.graphs:skill_graph_provider",
                  {"root": str(out / "skills")}),)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog(out / "session-log"))
    kernel.mount(plan)
    return kernel


def _stamped_sha(prereg: Preregistration, kernel: Kernel) -> str:
    """The FULL prereg sha that rsi_run will seal, computed by stamping the
    kernel-resolved provider refs exactly as workload.run does -- so the startup
    log carries the auditable full hash, not run_campaign's 12-char prefix."""
    stamped = dataclasses.replace(
        prereg,
        env_provider=kernel.provider_ref("embodiment.env"),
        policy_provider=kernel.provider_ref("policy.driver"),
        percept_provider=kernel.provider_ref("percept.model"))
    return stamped.sha()


def _run_smoke(workers: int) -> int:
    """Full-pipeline-shape smoke on the bring-up reservoir. NEVER the campaign's
    virgin 46000+/47000+ blocks: dev is 8 seeds from 41881+, held-out is a small
    throwaway bring-up slice (not the sealed 47000+ block), one generation, into a
    THROWAWAY store deleted at the end. Proves: parent rebuilds + sha asserts,
    search runs at critic_budget=1 and sees privileged features, the proposal
    threads recovery_name='replace', and the gate runs without crashing.
    """
    prereg = dataclasses.replace(
        place_prereg(),
        dev=tuple(range(41881, 41889)),        # 8 bring-up seeds, never 46000+
        heldout=tuple(range(41890, 41898)),    # throwaway bring-up slice, never 47000+
        max_generations=1)
    out = Path(tempfile.mkdtemp(prefix="place-smoke-",
                                dir=str(Path(__file__).resolve().parent.parent / "runs")))
    print(f"[SMOKE] THROWAWAY store {out}  (deleted at the end)")
    try:
        kernel = _mount(out)
        print(f"[SMOKE] preregistration sha {_stamped_sha(prereg, kernel)}  -> {out}")
        result = rsi_run(prereg, out, kernel, workers=workers)["result"]
        print("\n[SMOKE] === proposal ===")
        gens = _read_generations(out)
        if not gens:
            print("[SMOKE] no candidate proposed (no residual failures on the 8-seed dev)")
        for g in gens:
            trig = g["rule"]["trigger"]
            rec = g["rule"]["recovery"]
            rid = g["rule"]["rule_id"]
            # Round 92: the rule appended onto stack-g1's g1 must mint a fresh id,
            # not reuse g1 -- the collision that made the place rule unfireable.
            assert rid != "g1", f"minted id {rid} collides with the seeded parent's g1"
            print(f"[SMOKE] gen{g['generation']} rule_id={rid}: trigger {trig['feature']} {trig['op']} "
                  f"{trig['threshold']:.5g} armed@{trig['arm_after']}  "
                  f"privilege={'PRIVILEGED' if trig['feature'].startswith('privileged.') else 'observable'}  "
                  f"recovery={rec['name']}  dev_gate {g['dev_gate']['fixed']} fixed/"
                  f"{g['dev_gate']['broken']} broken p={g['dev_gate']['p_value']:.4g}  "
                  f"-> {g['reason']}")
        print(f"[SMOKE] final bundle {result['final_sha'][:12]} "
              f"rules={result['rules']} promoted={result['promoted']}")
        return 0
    finally:
        shutil.rmtree(out, ignore_errors=True)
        print(f"[SMOKE] deleted throwaway store {out}")


def _read_generations(out: Path) -> list[dict]:
    import json
    idx = out / "index.jsonl"
    if not idx.exists():
        return []
    rows = [json.loads(line) for line in idx.open()]
    gens = [json.loads((out / "artifacts" / f"{r['sha']}.json").read_text())
            for r in rows if r["kind"] == "generation"]
    gens.sort(key=lambda g: g["generation"])
    return gens


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("runs/place-g1"),
                        help="campaign store + skill graph output (default runs/place-g1)")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--smoke", action="store_true",
                        help="full-pipeline-shape smoke on the bring-up reservoir into a "
                             "throwaway store; does NOT run the real campaign")
    args = parser.parse_args()

    if args.smoke:
        return _run_smoke(args.workers)

    if (args.out / "index.jsonl").exists():
        print(f"{args.out} already holds a campaign store; pass a fresh --out", file=sys.stderr)
        return 2

    kernel = _mount(args.out)
    # Auditable startup line: the FULL prereg sha that will be sealed, and where.
    print(f"preregistration sha {_stamped_sha(place_prereg(), kernel)}  -> {args.out}")
    print(f"seeding from {PARENT_STORE} @ {PARENT_FINAL_SHA[:12]}\n")

    out = rsi_run(place_prereg(), args.out, kernel, workers=args.workers)

    print("\n=== published skills ===")
    graph = kernel.resolve("graph.skill", consumer="place")
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
