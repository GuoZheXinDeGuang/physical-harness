#!/usr/bin/env python3
"""Round 90 rung 2: two probes that decide whether the place campaign is worth preregistering.

The `replace` recovery (rung 1) is a place-shaped repair, but nothing measured
whether the failures it targets exist in enough number, carry an OBSERVABLE tell,
or actually convert. Two probes answer that on the bring-up reservoir slice
(seeds 41581-41880), replayed under the sealed stack-g1 gen1 bundle:

PROBE A -- observable-tell share (decides critic_budget).
    Among PLACE failures (grasp stage held, place clauses failed), how many show
    observable.finger_gap collapsing during the transit/place window? A small
    share means the place trigger cannot be built observable-only -- the campaign
    needs critic_budget=1 (a privileged place trigger).

PROBE B -- bring-up conversions (decides go/no-go).
    Run the same place-failure seeds under a DIAGNOSTIC bundle (gen1 rule + a
    privileged place trigger driving `replace` at sd=0.020) and count conversions
    (place-failure seed -> terminal success) against the paired gen1-only arm.
    >0 conversions = campaign green light; 0 = the primitive needs work.

The DIAGNOSTIC bundle is scaffolding to measure the primitive's ceiling; it never
enters any campaign lineage. The gen1 bundle is rebuilt exactly as
scripts/rescore_heldout.py does (rebuild_preregistration + rebuild_final_bundle,
asserted against the sealed child_sha) and the one artifact traces to the stack-g1
preregistration sha.

Like scripts/rescore_heldout.py, the reconstruction/classification functions are
pure and unit-tested against fakes (tests/test_probe_place.py); only `run_probes`
runs real rollouts.

Usage::

    MUJOCO_GL=egl python scripts/probe_place.py \\
        --store runs/stack-g1 --out runs/round90-probes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Runnable as `python scripts/probe_place.py ...` without PYTHONPATH gymnastics.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from harness.spec import STACK_SCHEDULE
from plugins.rsi.campaign import CampaignStore, _specs, sha_json
from plugins.rsi.governed import DEFAULT_PERCEPT_REF, Bundle, RecoverySpec, Rule
from plugins.rsi.stats.search import Trigger
from scripts.parity_check import read_store_artifacts, rebuild_preregistration
from scripts.rescore_heldout import rebuild_final_bundle

#: finger_gap below this during the transit/place window = the cube was lost
#: (fingers closed on nothing while the grip is commanded shut). A held 0.04-wide
#: cube reads ~0.04, so this cleanly separates "dropped" from "carrying".
TELL_THRESH = 0.005
#: The pre-execution scout ceiling: a fresh independent cubeB draw at sd=0.020
#: lands within the 0.025 xy tolerance ~54% of the time, roughly halved by
#: execution loss. Reported alongside where reality lands, not enforced.
SCOUT_CEILING = 0.50


# --- pure: schedule geometry -------------------------------------------------
def schedule_step(phase: str, schedule=STACK_SCHEDULE) -> int:
    """Cumulative policy-schedule step at which `phase` begins."""
    acc = 0
    for name, dur in schedule:
        if name == phase:
            return acc
        acc += dur
    raise KeyError(f"{phase} not in schedule {[n for n, _ in schedule]}")


#: The transit/place window: from `over_b` onset (cube lifted, heading to cubeB)
#: to `release` onset (past which an open gripper is the INTENDED release, not a
#: dropped cube). Steps ~92-142 on the sealed STACK_SCHEDULE.
PLACE_WINDOW = (schedule_step("over_b"), schedule_step("release"))


# --- pure: classification over rollout result dicts --------------------------
def is_place_failure(result: dict) -> bool:
    """Grasp stage held, place clauses failed: exactly what a place repair targets.

    A grasp failure (grasp not held) is a DIFFERENT repair -- the sealed regrasp
    already owns it -- so it is excluded here.
    """
    stages = {s["name"]: s for s in result.get("stages", [])}
    g, p = stages.get("grasp"), stages.get("place")
    return bool(g and p and g["success"] and p["reached"] and not p["success"])


def has_observable_tell(result: dict, window=PLACE_WINDOW, thresh: float = TELL_THRESH) -> bool:
    """Did observable.finger_gap collapse below `thresh` during the place window?

    True means the failure announced itself observably (the cube was dropped/lost
    mid-place); False means it was invisible to proprioception -- the cube stayed
    held but seated wrong, which only a privileged residual can catch.
    """
    fg = np.asarray(result["trace"]["observable.finger_gap"])
    lo, hi = window
    seg = fg[lo:hi]
    return bool(seg.size and float(seg.min()) < thresh)


def diagnostic_bundle(gen1: Bundle) -> Bundle:
    """gen1 rule(s) + a privileged place trigger armed at the place-window start,
    driving the `replace` primitive at sd=0.020.

    critic_budget=1 so the privileged trigger's feature is admitted into the view.
    DIAGNOSTIC scaffolding to measure the primitive's ceiling -- never a campaign
    lineage bundle.
    """
    place = Rule(
        "place-bringup",
        Trigger("privileged.stack_xy_residual", "gt", 0.025, 1, schedule_step("over_b"), "value"),
        RecoverySpec(name="replace", sensor_sd=0.020, max_invocations=1),
    )
    return Bundle(rules=(*gen1.rules, place), critic_budget=1, action_budget=0)


def fired(result: dict, rule_id: str) -> bool:
    """Did `rule_id` fire in this rollout?"""
    return rule_id in result.get("fired_rules", [])


def pair_outcomes(gen1_success: dict, diag_success: dict) -> dict:
    """conversions / broken over the paired place-failure seeds (both keyed by seed).

    conversion = failed under gen1-only, succeeded under the diagnostic bundle.
    broken     = succeeded under gen1-only, failed under the diagnostic bundle.
    """
    seeds = sorted(diag_success)
    conversions = [s for s in seeds if not gen1_success[s] and diag_success[s]]
    broken = [s for s in seeds if gen1_success[s] and not diag_success[s]]
    return {"conversions": conversions, "broken": broken}


def _last_finite(arr) -> float | None:
    a = np.asarray(arr, dtype=float)
    a = a[np.isfinite(a)]
    return float(a[-1]) if a.size else None


def failure_mode(result: dict) -> dict:
    """Diagnose one non-converted place attempt from its final privileged residuals.

    The diagnostic bundle runs at critic_budget=1, so the trace carries the
    privileged residuals. The last recorded value is the retreat-region reading,
    after the recovery has placed and released -- i.e. where the cube ended up.
    """
    trace = result["trace"]
    xy = _last_finite(trace.get("privileged.stack_xy_residual", []))
    z = _last_finite(trace.get("privileged.stack_z_residual", []))
    if xy is not None and xy > 0.025:
        mode = "xy_off"          # fresh draw / execution left it off-target in plane
    elif z is not None and z > 0.02:
        mode = "z_high"          # released too high; never seated down
    elif z is not None and z < -0.02:
        mode = "z_low_dropped"   # fell below the seat: dropped/knocked off
    else:
        mode = "within_tol_terminal_false"  # geometrically seated, terminal still false
    return {"seed": result["seed"], "mode": mode,
            "xy_residual": xy, "z_residual": z,
            "fired": fired(result, "place-bringup")}


# --- impure: the real-rollout driver -----------------------------------------
def run_probes(store_dir: str | Path, out_dir: str | Path, *,
               block: int = 41581, n: int = 300,
               extend_block: int = 41881, extend_n: int = 119,
               min_place_failures: int = 30, workers: int = 10,
               verbose: bool = True, executor=None) -> dict:
    """Rebuild the sealed gen1 bundle, run both probes, store one diagnostic artifact."""
    from harness.registry import load_provider
    from plugins.rsi.gate import _run
    from plugins.rsi.parallel import default_executor

    out_root = Path(out_dir)
    if out_root.exists():
        raise FileExistsError(f"{out_root} already exists; a probe run writes a fresh store")

    archived = read_store_artifacts(store_dir)
    prereg_payloads = archived.get("preregistration", [])
    if not prereg_payloads:
        raise ValueError(f"{store_dir} has no preregistration artifact")
    prereg = rebuild_preregistration(prereg_payloads[0])
    # Same registry-inversion guard as rescore_heldout: register the embodiment's
    # features in THIS process before any privilege read (declared_privilege on
    # the rebuilt bundles), by loading the archived provider refs first.
    load_provider(prereg.env_provider or "plugins.embodiment_robosuite:provider", {})
    load_provider(prereg.percept_provider or DEFAULT_PERCEPT_REF, {})
    gen1 = rebuild_final_bundle(prereg, archived.get("generation", []))  # asserts child_sha
    diag = diagnostic_bundle(gen1)

    ex = executor or default_executor()

    def rollout(seeds, bundle):
        specs = _specs(seeds, prereg)
        results = ex.map(_run, [(s, bundle) for s in specs], workers=workers)
        return dict(zip(seeds, results))

    # --- PROBE A: replay seeds under the gen1 bundle -------------------------
    seeds = list(range(block, block + n))
    if verbose:
        print(f"probe A: gen1 bundle {gen1.sha()[:12]} on {len(seeds)} seeds "
              f"[{block}, {block + n})")
    res_a = rollout(seeds, gen1)
    place_fail = [s for s in seeds if is_place_failure(res_a[s])]
    extended = False
    if len(place_fail) < min_place_failures:
        extended = True
        ext_seeds = list(range(extend_block, extend_block + extend_n))
        if verbose:
            print(f"probe A: only {len(place_fail)} place failures (<{min_place_failures}); "
                  f"extending to [{extend_block}, {extend_block + extend_n})")
        res_a.update(rollout(ext_seeds, gen1))
        seeds = seeds + ext_seeds
        place_fail = [s for s in seeds if is_place_failure(res_a[s])]

    tell = [s for s in place_fail if has_observable_tell(res_a[s])]
    tell_share = (len(tell) / len(place_fail)) if place_fail else 0.0
    if verbose:
        print(f"probe A: {len(place_fail)} place failures / {len(seeds)} seeds; "
              f"{len(tell)} with observable tell -> share {tell_share:.1%}")

    # --- PROBE B: the place-failure seeds under the diagnostic bundle --------
    if verbose:
        print(f"probe B: diagnostic bundle {diag.sha()[:12]} "
              f"(critic_budget={diag.critic_budget}) on {len(place_fail)} place-failure seeds")
    res_b = rollout(place_fail, diag)
    gen1_success = {s: bool(res_a[s]["success"]) for s in place_fail}
    diag_success = {s: bool(res_b[s]["success"]) for s in place_fail}
    outcomes = pair_outcomes(gen1_success, diag_success)
    n_fired = sum(1 for s in place_fail if fired(res_b[s], "place-bringup"))
    conv = outcomes["conversions"]
    conv_rate = (len(conv) / len(place_fail)) if place_fail else 0.0
    non_converted = [failure_mode(res_b[s]) for s in place_fail if s not in set(conv)]
    modes: dict[str, int] = {}
    for fm in non_converted:
        modes[fm["mode"]] = modes.get(fm["mode"], 0) + 1
    if verbose:
        print(f"probe B: {n_fired} fired, {len(conv)} conversions, {len(outcomes['broken'])} broken; "
              f"rate {conv_rate:.1%} (scout ceiling ~{SCOUT_CEILING:.0%})")
        print(f"probe B: non-converted failure modes {modes}")

    payload = {
        "grade": "diagnostic",
        "note": ("place-campaign go/no-go probe. the diagnostic bundle (gen1 + a "
                 "privileged place trigger driving `replace`) is scaffolding to measure "
                 "the primitive's ceiling and NEVER enters any campaign lineage."),
        "source_store": str(store_dir),
        "source_preregistration_sha": sha_json(prereg_payloads[0]),
        "gen1_bundle_sha": gen1.sha(),
        "diagnostic_bundle_sha": diag.sha(),
        "seeds": {"block": block, "n": n, "extended": extended,
                  "extend_block": extend_block if extended else None,
                  "extend_n": extend_n if extended else None,
                  "total_seeds": len(seeds)},
        "probe_a": {
            "n_seeds": len(seeds),
            "n_place_failures": len(place_fail),
            "n_observable_tell": len(tell),
            "tell_share": tell_share,
            "window": list(PLACE_WINDOW),
            "tell_thresh": TELL_THRESH,
            "place_failure_seeds": place_fail,
            "tell_seeds": tell,
            "decision": ("critic_budget=1 (privileged place trigger) -- the tell is too "
                         "rare to build an observable trigger" if tell_share < 0.20
                         else "observable might carry the place trigger"),
        },
        "probe_b": {
            "n_place_failure_seeds": len(place_fail),
            "n_fired": n_fired,
            "n_conversions": len(conv),
            "conversion_rate": conv_rate,
            "n_broken": len(outcomes["broken"]),
            "scout_ceiling": SCOUT_CEILING,
            "conversions": conv,
            "broken": outcomes["broken"],
            "failure_modes": modes,
            "failure_examples": non_converted[:6],
            "verdict": ("green light -- >0 conversions" if len(conv) > 0
                        else "primitive needs work -- 0 conversions"),
        },
    }
    digest = CampaignStore(out_root).put("place_probe", payload)
    if verbose:
        print(f"place_probe {digest[:12]} -> {out_root}")
    payload["_artifact_sha"] = digest
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--store", type=Path, default=Path("runs/stack-g1"),
                        help="sealed campaign store to rebuild the gen1 bundle from")
    parser.add_argument("--out", type=Path, required=True,
                        help="fresh store for the one diagnostic artifact; must not exist")
    parser.add_argument("--block", type=int, default=41581, help="first bring-up-reservoir seed")
    parser.add_argument("--n", type=int, default=300, help="reservoir block length")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    run_probes(args.store, args.out, block=args.block, n=args.n,
               workers=args.workers, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
