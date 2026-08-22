#!/usr/bin/env python3
"""Round 88 rung 2 validation: does the FIXED search objective arm at the peak?

Zero fresh seeds, diagnostic grade. Reuses the burned round25-rerun dev block
(44000-44059) and held-out block (44200-44399). The reconstruction is
scripts/probe_arm_time.py's exact path: rebuild the preregistration and the
sealed search rule from the content-addressed store, regenerate the 60 ungoverned
dev traces through gate._run, then run the FIXED SearchProposer -- value-only
enumeration (search.RUNTIME_REDUCERS), peak-anchored arms, and a repair-aware
tie-break that replays the float-exact top-score ties on the dev block and keeps
the one that fixes the most.

The sealed anchor search@43 (fixed=54, broken=0, fires=119) MUST reproduce on the
held-out block before any downstream number is trusted; the WHOLE discordant
signature is guarded, and the probe stops without writing if any of the three
drifts. One diagnostic artifact is sealed into a fresh store, lineage-tagged to
the round25-rerun store and the round88-armtime probe artifact.

    PYTHONPATH=. .venv/bin/python scripts/probe_round88_fix.py
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

# Runnable as `python scripts/probe_round88_fix.py` without PYTHONPATH gymnastics.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from governor.proposer import SearchProposer
from plugins.rsi import gate
from plugins.rsi.campaign import CampaignStore, _specs, sha_json
from plugins.rsi.gate import paired_gate
from plugins.rsi.governed import Bundle
from plugins.rsi.stats.search import search_triggers
from scripts.parity_check import read_store_artifacts, rebuild_preregistration
from scripts.rescore_heldout import rule_from_canonical


def _trigger_view(trig) -> dict:
    return {"feature": trig.feature, "op": trig.op, "threshold": float(trig.threshold),
            "dwell": int(trig.dwell), "arm_after": int(trig.arm_after),
            "reducer": trig.reducer}


def run_fix_probe(store_dir: str | Path, out_dir: str | Path,
                  probe_store: str | Path = "runs/round88-armtime", *,
                  workers: int = 10, verbose: bool = True, executor=None) -> dict:
    """Rebuild, reconstruct dev, run the FIXED proposer, gate on held-out, seal one artifact."""
    store_dir = Path(store_dir)
    out_root = Path(out_dir)
    if out_root.exists():
        raise FileExistsError(f"{out_root} already exists; a probe writes a fresh store")

    archived = read_store_artifacts(store_dir)
    prereg_payloads = archived.get("preregistration", [])
    rerun_payloads = archived.get("round25_rerun", [])
    if not prereg_payloads:
        raise ValueError(f"{store_dir} has no preregistration artifact")
    if not rerun_payloads:
        raise ValueError(f"{store_dir} has no round25_rerun artifact")
    prereg = rebuild_preregistration(prereg_payloads[0])
    rerun = rerun_payloads[0]

    # Round 69 registry inversion: load the archived env provider ref first, so
    # the admissible-feature scan in search_triggers sees a populated catalog.
    from harness.registry import load_provider

    load_provider(prereg.env_provider or "plugins.embodiment_robosuite:provider", {})

    search_canon = rerun["arms"]["search"]["rule"]
    search_rule = rule_from_canonical(search_canon)
    if search_rule.canonical() != search_canon:
        raise AssertionError("rebuilt search rule does not reproduce its archived canonical")

    if executor is None:
        from plugins.rsi.parallel import default_executor
        executor = default_executor()

    # --- dev reconstruction: round25_rerun's exact ungoverned trace path ------
    dev_specs = _specs(prereg.dev, prereg)
    if verbose:
        print(f"dev rollout: {len(dev_specs)} ungoverned episodes on {prereg.task}")
    cur = executor.map(gate._run, [(s, None) for s in dev_specs], workers=workers)
    traces = [r["trace"] for r in cur]
    labels = [r["success"] for r in cur]
    if verbose:
        print(f"  dev rate {sum(labels)}/{len(labels)}")

    # --- the FIXED search proposal; the tie-break replays on the dev block ----
    ranked = search_triggers(traces, labels, privilege_budget=prereg.critic_budget,
                             earliness=prereg.earliness, fp_penalty=prereg.fp_penalty)
    top_score = float(ranked[0].score) if ranked else None
    proposer = SearchProposer()
    new_rule = proposer.propose(traces, labels, generation=1,
                                privilege_budget=prereg.critic_budget,
                                recovery_sensor_sd=prereg.recovery_sensor_sd,
                                dev_specs=dev_specs, executor=executor, workers=workers)
    if new_rule is None:
        raise SystemExit("the fixed search produced no admissible candidate")
    if verbose:
        print(f"  fixed pick: {new_rule.trigger.describe()}  score={top_score}")
        print(f"  selection: {proposer.selection}")

    # --- held-out gates: anchor first, then the new pick ----------------------
    heldout_specs = _specs(prereg.heldout, prereg)

    def _paired(rule):
        bundle = Bundle(rules=(), critic_budget=prereg.critic_budget,
                        action_budget=prereg.action_budget).append(rule)
        return paired_gate(heldout_specs, bundle, baseline=None, workers=workers,
                           executor=executor)

    sealed = rerun["arms"]["search"]["heldout"]
    sealed_fixed, sealed_broken, sealed_fires = (
        int(sealed["fixed"]), int(sealed["broken"]), int(sealed["fires"]))
    if verbose:
        print(f"held-out anchor: search@43 must reproduce "
              f"fixed={sealed_fixed} broken={sealed_broken} fires={sealed_fires}")
    anchor = _paired(search_rule)
    if verbose:
        print(f"  search@43: {anchor.line()}")
    drift = {k: (got, seal) for k, got, seal in
             (("fixed", anchor.fixed, sealed_fixed),
              ("broken", anchor.broken, sealed_broken),
              ("fires", anchor.fires, sealed_fires)) if got != seal}
    if drift:
        raise SystemExit(
            f"anchor drift: search@43 {drift} (got, sealed). The reconstruction or the "
            "environment drifted; every downstream number would be garbage. Stopping "
            "WITHOUT writing an artifact.")

    new_res = _paired(new_rule)
    if verbose:
        print(f"  fixed pick held-out: {new_res.line()}")
    naive_sealed_fixed = int(rerun["arms"]["naive_mock"]["heldout"]["fixed"])

    # --- lineage: the round88-armtime probe artifact sha ----------------------
    probe_store = Path(probe_store)
    probe_arts = read_store_artifacts(probe_store).get("arm_time_probe", []) \
        if (probe_store / "index.jsonl").exists() else []
    probe_sha = sha_json(probe_arts[0]) if probe_arts else None

    payload = {
        "grade": "diagnostic",  # burned-block reuse -- NOT headline numbers
        "source_store": str(store_dir),
        "source_preregistration_sha": sha_json(prereg_payloads[0]),
        "source_round25_rerun_sha": sha_json(rerun),
        "source_probe_store": str(probe_store),
        "source_arm_time_probe_sha": probe_sha,
        "dev_block": [min(prereg.dev), max(prereg.dev) + 1],
        "heldout_block": [min(prereg.heldout), max(prereg.heldout) + 1],
        "dev_rate": float(sum(labels) / len(labels)),
        "top_score": top_score,
        "fixed_pick": {"rule": new_rule.canonical(), "trigger": _trigger_view(new_rule.trigger)},
        "selection": proposer.selection,
        "heldout": {
            "fixed_pick": asdict(new_res),
            "search_arm43_anchor": asdict(anchor),
        },
        "anchors": {
            "old_search_arm43_fixed": sealed_fixed,
            "naive_arm95_fixed": naive_sealed_fixed,
        },
    }
    digest = CampaignStore(out_root).put("round88_fix", payload)
    if verbose:
        print(f"round88_fix {digest[:12]} -> {out_root}")
        print(f"SUMMARY: pick arm={new_rule.trigger.arm_after} fixed={new_res.fixed} "
              f"vs old_search=54 naive={naive_sealed_fixed}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--store", type=Path, default=Path("runs/round25-rerun"),
                        help="sealed round25_rerun store (default runs/round25-rerun)")
    parser.add_argument("--probe-store", type=Path, default=Path("runs/round88-armtime"),
                        help="round88-armtime probe store for lineage (default runs/round88-armtime)")
    parser.add_argument("--out", type=Path, default=Path("runs/round88-fix"),
                        help="fresh store for the artifact; must not exist")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    run_fix_probe(args.store, args.out, args.probe_store,
                  workers=args.workers, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
