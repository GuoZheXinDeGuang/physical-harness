#!/usr/bin/env python3
"""验货 template: run the existing evidence machine over a manifest CLAIM (R8, W2).

    PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python scripts/acceptance_campaign.py \
        --claim plugins/skill_stack --out runs/accept-stack
    (add --dry-run to print the preregistration sha without mounting or running)

The parameterized form of ``scripts/stack_campaign.py`` / ``place_campaign.py``:
same mount (base profile + the claim's ``policy.driver`` + an on-disk skill
graph), same ``plugins.rsi.workload.run`` (paired gate / blind twin / held-out /
transfer ablation), same published ``SkillRecord``s -- NO new statistics. What
was hand-written per campaign is now DATA in the card's ``[claim]`` table:
``task`` / ``policy`` (the driver ref) / ``stages`` (a ref, imported and called)
/ the ``dev`` and ``heldout`` seed blocks, plus any ``Preregistration`` field the
claim overrides (``critic_budget``, ``recovery_name``, ``parent_store`` ... --
what place-g1 set by hand). A stack-shaped claim rebuilds ``stack_prereg().sha()``
byte-for-byte, which is the whole point: 验货 is the campaign machine acting as
inspector, not a second implementation of it.

Evolution-mode only. Publishing a skill IS an evolution act; the enforcement is
the runtime's, REUSED not reimplemented (round-95 single-source discipline): the
runtime accepts a campaign brief only in evolution mode and spawns this script
from that gated path, and the seed-ledger overlap guard rejects a burned block
before spawning (``scripts/harness_runtime``). This script adds no second mode
check -- run standalone it is a manual evolution act, exactly like the sibling
campaign scripts.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from harness.config import Mount, Patch, resolve_plan
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness.kernel import Kernel
from plugins.rsi.campaign import Preregistration
from plugins.rsi.workload import run as rsi_run
from profiles import base_profile

#: The evidence machine's settings, lifted verbatim from stack_campaign.stack_prereg
#: (the reference campaign). A claim overrides any of these by naming the field.
_BASELINE = {
    "percept_noise": 0.012, "critic_budget": 0, "action_budget": 0,
    "recovery_sensor_sd": 0.020, "max_generations": 2, "scale_dev_by_power": True,
    "terminal_label": True, "require_judgement": True,
    "prior_discordance_yield": 0.4, "prior_judgement_yield": 0.25,
}

_PREREG_FIELDS = {f.name for f in dataclasses.fields(Preregistration)}
#: Claim keys handled specially below, never passed straight through to a prereg
#: field. ``policy`` is the driver MOUNT ref, not ``prereg.policy`` (that label is
#: ``policy_label``, default "scripted"); env/policy/percept provider refs are
#: stamped by ``rsi_run`` from the resolved mount, so a claim must not set them.
_CLAIM_SPECIAL = {"task", "policy", "policy_label", "dev", "heldout", "stages"}


def _load_attr(ref: str):
    """Import ``module:attr`` and return the attribute (the read half of
    load_provider). The claim names its ``stages`` builder this way -- data on the
    skill side, imported by ref, never carried in a brief."""
    module_name, attr = ref.split(":", 1)
    return getattr(importlib.import_module(module_name), attr)


def _block(pair) -> tuple[int, ...]:
    """A claim's ``[lo, hi]`` half-open seed block -> the seed tuple lo..hi-1."""
    lo, hi = int(pair[0]), int(pair[1])
    if hi <= lo:
        raise ValueError(f"seed block [{lo}, {hi}) is empty; need hi > lo")
    return tuple(range(lo, hi))


def load_claim(plugin_dir: str | Path) -> dict:
    """The ``[claim]`` table of a card's manifest (the acceptance claim it makes)."""
    data = tomllib.loads((Path(plugin_dir) / "manifest.toml").read_text())
    claim = data.get("claim")
    if not claim:
        raise ValueError(f"{plugin_dir} manifest has no [claim] table to 验货")
    return claim


def build_prereg(claim: dict) -> Preregistration:
    """A ``Preregistration`` from a claim: the evidence-machine baseline,
    parameterized by task/policy-label/stages/seed-blocks and overlaid with any
    prereg field the claim names. A typo'd field is a loud error, not silently
    ignored -- a claim is a trust boundary (it decides what a skill measured)."""
    for key in ("task", "policy", "dev", "heldout"):
        if key not in claim:
            raise ValueError(f"claim missing required key {key!r}")
    kwargs = dict(_BASELINE)
    kwargs["task"] = claim["task"]
    kwargs["policy"] = claim.get("policy_label", "scripted")
    if claim.get("stages"):
        kwargs["stages"] = tuple(_load_attr(claim["stages"])())
    for k, v in claim.items():
        if k in _CLAIM_SPECIAL:
            continue
        if k not in _PREREG_FIELDS:
            raise ValueError(f"claim key {k!r} is not a Preregistration field")
        kwargs[k] = v
    return Preregistration(dev=_block(claim["dev"]), heldout=_block(claim["heldout"]), **kwargs)


def _mount(claim: dict, out: Path) -> Kernel:
    """Base profile patched with the claim's policy driver and an on-disk skill
    graph -- identical to stack/place_campaign; embodiment.env / percept.model
    keep the base refs (a claimed skill is another task on the base embodiment)."""
    plan = resolve_plan(base_profile(), patches=(
        Patch(claim["task"], override=(
            Mount("policy.driver", claim["policy"]),
            Mount("graph.skill", "plugins.graphs:skill_graph_provider",
                  {"root": str(out / "skills")}),)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog(out / "session-log"))
    kernel.mount(plan)
    return kernel


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--claim", type=Path, required=True,
                    help="card dir holding manifest.toml with a [claim] table")
    ap.add_argument("--out", type=Path,
                    help="campaign store + skills output (required unless --dry-run)")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the claim + preregistration sha; mount nothing, "
                         "run no episodes (no simulator needed)")
    args = ap.parse_args(argv)

    claim = load_claim(args.claim)
    prereg = build_prereg(claim)
    if args.dry_run:
        print(f"claim {args.claim}: task={prereg.task} policy={claim['policy']} "
              f"dev={len(prereg.dev)} heldout={len(prereg.heldout)}")
        print(f"unstamped preregistration sha {prereg.sha()[:12]}  (the sealed sha "
              "additionally stamps env/policy/percept provider refs at run time)")
        return 0

    if args.out is None:
        ap.error("--out is required unless --dry-run")
    if (args.out / "index.jsonl").exists():
        print(f"{args.out} already holds a campaign store; pass a fresh --out", file=sys.stderr)
        return 2

    kernel = _mount(claim, args.out)
    print(f"preregistration sha {prereg.sha()[:12]}  -> {args.out}\n")
    out = rsi_run(prereg, args.out, kernel, workers=args.workers)

    print("\n=== published skills ===")
    graph = kernel.resolve("graph.skill", consumer=claim["task"])
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
    print(f"验货: PYTHONPATH=. .venv/bin/python scripts/plugin_doctor.py --verify {args.out}/skills")
    return 0


if __name__ == "__main__":
    sys.exit(main())
