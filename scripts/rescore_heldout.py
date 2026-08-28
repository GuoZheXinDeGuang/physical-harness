#!/usr/bin/env python3
"""Rescore a sealed campaign's final bundle on a fresh held-out block.

The headline discipline asks for at least three held-out blocks behind a
claim; a sealed campaign scores exactly one. This script rebuilds the final
bundle from a sealed store -- preregistration plus the promoted generations'
rule canonicals, asserted per generation against the archived ``child_sha``
so a wrong reconstruction cannot be rescored -- and scores it on a NEW seed
block through the SAME path ``plugins/rsi/campaign.py``'s held-out section
uses: ``paired_gate`` against the ungoverned baseline, then the whole chain
head to head against its blind twins for the judgement verdict. The result
is one content-addressed ``heldout_rescore`` artifact in a fresh store,
traceable to the source campaign by preregistration sha and bundle sha.

Usage::

    python scripts/rescore_heldout.py --store runs/stack-g1 --block 42200 \\
        --out runs/stack-g1-rescore-42200

Like scripts/parity_check.py, the reconstruction functions are pure and
unit-tested against fake stores (tests/test_rescore_heldout.py); only
`run_rescore` runs real rollouts.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

# Runnable as `python scripts/rescore_heldout.py ...` without PYTHONPATH gymnastics.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.rsi.campaign import (
    CampaignStore,
    _specs,
    blind_twin,
    sha_json,
    stage_attribution,
)
from plugins.rsi.gate import paired_gate
from plugins.rsi.governed import Bundle

# Store reconstruction moved to the plugins layer (round 90: run_campaign needs
# it and a plugin may not import scripts); re-exported so callers and tests that
# do `rescore_heldout.rebuild_final_bundle` / `.rule_from_canonical` are unchanged.
from plugins.rsi.rebuild import (
    read_store_artifacts,
    rebuild_final_bundle,
    rebuild_preregistration,
    rule_from_canonical,
)

__all__ = ["read_store_artifacts", "rebuild_final_bundle",
           "rebuild_preregistration", "rule_from_canonical", "run_rescore"]


def run_rescore(store_dir: str | Path, out_dir: str | Path, block: int, *,
                n: int | None = None, workers: int = 10, verbose: bool = True,
                executor=None) -> dict:
    """Rebuild the sealed bundle, score it on seeds [block, block+n), store the artifact."""
    archived = read_store_artifacts(store_dir)
    prereg_payloads = archived.get("preregistration", [])
    if not prereg_payloads:
        raise ValueError(f"{store_dir} has no preregistration artifact")
    prereg = rebuild_preregistration(prereg_payloads[0])
    # Round 69 registry inversion: features register when the embodiment plugin
    # is imported, which in a worker happens via the spec's provider ref. In
    # THIS parent process nothing has imported it yet, so declared_privilege()
    # on the rebuilt bundle would hit an empty catalog. Load the archived
    # provider ref first -- the same order workload.run establishes via
    # kernel.resolve. The full pytest suite masks this (sibling test modules
    # import the plugin); only a fresh process exercises it.
    from harness.registry import load_provider

    load_provider(prereg.env_provider or "plugins.embodiment_robosuite:provider", {})
    bundle = rebuild_final_bundle(prereg, archived.get("generation", []))

    n = n if n is not None else len(prereg.heldout)
    seeds = tuple(range(block, block + n))
    overlap = (set(prereg.dev) | set(prereg.heldout)) & set(seeds)
    if overlap:
        raise ValueError(
            f"block [{block}, {block + n}) overlaps the campaign's own seeds "
            f"(e.g. {sorted(overlap)[:5]}); a rescore block must be fresh")

    out_root = Path(out_dir)
    if out_root.exists():
        raise FileExistsError(f"{out_root} already exists; a rescore writes a fresh store")

    # Same path as run_campaign's held-out section: the bundle vs the
    # ungoverned baseline, then (when the campaign preregistered judgement)
    # the whole chain vs its blind twins, head to head on the same seeds.
    held = _specs(seeds, prereg)
    if verbose:
        print(f"bundle {bundle.sha()[:12]} ({len(bundle.rules)} rules) "
              f"on block [{block}, {block + n})")
    final = paired_gate(held, bundle, baseline=None, workers=workers, executor=executor)
    if verbose:
        print(f"held-out rescore: {final.line()}")
    vs_blind = judged = None
    if prereg.require_judgement:
        blind = Bundle(rules=tuple(blind_twin(r) for r in bundle.rules),
                       critic_budget=bundle.critic_budget,
                       action_budget=bundle.action_budget)
        vs_blind = paired_gate(held, bundle, baseline=blind, workers=workers,
                               executor=executor)
        judged = bool(vs_blind.p_value < prereg.alpha and vs_blind.fixed > vs_blind.broken)
        if verbose:
            print(f"vs blind twin: {vs_blind.line()}  -> judgement "
                  f"{'established' if judged else 'NOT established'}")

    attribution = None
    if prereg.stages is not None:
        # The stage attribution follows the rescored block, same as run_campaign's
        # dev measurement: a reproduction block without its failure distribution
        # would be a thinner claim than the campaign it reproduces. paired_gate
        # keeps returning a PairedResult and discarding rollouts, so this re-runs
        # the governed arm -- bitwise-identical per local-archive/docs/retired-from-public/verified-environment.md.
        # ponytail: n extra rollouts on the stages path; teach paired_gate to
        # surface raw results if that cost ever matters.
        from plugins.rsi.gate import _run
        from plugins.rsi.parallel import default_executor
        rolled = (executor or default_executor()).map(
            _run, [(s, bundle) for s in held], workers=workers)
        attribution = stage_attribution(rolled)

    payload = {
        "source_store": str(store_dir),
        "source_preregistration_sha": sha_json(prereg_payloads[0]),
        "bundle_sha": bundle.sha(),
        "block": block,
        "n": n,
        "paired": asdict(final),
        "vs_blind": asdict(vs_blind) if vs_blind is not None else None,
        "judgement": judged,
    }
    if attribution is not None:
        # Key added only on the stages path, so a stages=None rescore artifact
        # stays byte-identical to rung 1's shape.
        payload["stage_attribution"] = attribution
    digest = CampaignStore(out_root).put("heldout_rescore", payload)
    if verbose:
        print(f"heldout_rescore {digest[:12]} -> {out_root}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--store", type=Path, required=True,
                        help="sealed campaign store, e.g. runs/stack-g1")
    parser.add_argument("--block", type=int, required=True,
                        help="first seed of the fresh held-out block, e.g. 42200")
    parser.add_argument("--n", type=int, default=None,
                        help="block length (default: same as the campaign's held-out)")
    parser.add_argument("--out", type=Path, required=True,
                        help="fresh store for the rescore artifact; must not exist")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    run_rescore(args.store, args.out, args.block, n=args.n, workers=args.workers,
                verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
