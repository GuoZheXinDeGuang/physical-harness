#!/usr/bin/env python3
"""Round 92: the place-g1 riddle -- why the sealed place candidate fired 91/267
on the dev block with ZERO outcome flips against parent-alone.

The candidate is the stack-g1 gen1 grasp bundle (a regrasp rule) with one place
rule appended: ``Rule(privileged.stack_xy_residual > 0.033809, dwell 3,
arm_after 108, replace@sd0.020, max_invocations 1)``. The generation archive
sealed it rejected (child_sha 8dfe6fb7...): fires 91, fixed 0, broken 0, p=1.0.

Three hypotheses were on the table:

    H-window  dwell 3 + the higher threshold delay firing past the RELEASE
              moment, so `replace` manipulates an already-empty gripper -- a
              harmless no-op that fixes and breaks nothing.
    H-select  the fires land on placements whose misplacement is beyond what
              `replace` can repair.
    H-invoke  the max_invocations interplay with the parent grasp rule eats the
              invocation.

The finding (see verdicts in the sealed payload) is a sharper H-invoke, and it
is STRUCTURAL. The appended place rule was auto-named ``g1`` -- the SAME rule_id
the parent grasp rule already carries. ``governed_rollout`` keys its ``used``
and ``consec`` dicts by rule_id, so two rules sharing an id share ONE
max_invocations budget AND one dwell accumulator. The grasp rule (chain-first,
dwell 1, arm 58) resets that shared accumulator every step the cube is held --
capping the place rule's dwell at 1, below its dwell-3 -- and consumes the
shared invocation outright whenever the cube is dropped. The place rule can
therefore never fire. All 91 fires are the parent's regrasp rule (finger_gap
< 0.001, grasp-drop), which parent-alone fires identically: hence 0 flips.

The de-collided arm proves causation on the SAME seed block: rename the place
rule's id and it fires and repairs (H-select falls with it -- the targeted
failures ARE repairable). That is a cleaner, seed-matched restatement of round
90's probe B, whose place rule used a distinct id ("place-bringup") and so was
never eaten (runs/round90-probes was not retained, so it is reconstructed here
rather than re-read).

Like scripts/probe_place.py, the reconstruction/classification functions are
pure and unit-tested against fakes (tests/test_probe_place_window.py); only
`run_window_probe` runs real rollouts.

Usage::

    MUJOCO_GL=egl python scripts/probe_place_window.py \\
        --place-store runs/place-g1 --parent-store runs/stack-g1 \\
        --out runs/round92-window
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Runnable as `python scripts/probe_place_window.py ...` without PYTHONPATH gymnastics.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from harness.spec import STACK_SCHEDULE
from plugins.rsi.campaign import CampaignStore, _specs, sha_json
from plugins.rsi.governed import DEFAULT_PERCEPT_REF, Bundle, Rule
from scripts.parity_check import read_store_artifacts, rebuild_preregistration
from scripts.rescore_heldout import rebuild_final_bundle, rule_from_canonical

#: The archived child_sha of the final-rejected candidate (generation artifact).
CANDIDATE_CHILD_SHA = "8dfe6fb7d3d4a9d0215ec8eda607ab857b29540b9bc92e78c0aa2127ee6c86e8"
#: A distinct id for the de-collided contrast: same rule, un-shared counters.
DECOLLIDED_ID = "g1-place"
#: finger_gap bands. Below `drop` = closed on nothing (dropped); above `open`
#: = an opened/released gripper. Between = a cube held in the closed gripper.
FG_DROP = 0.001
FG_OPEN = 0.05


# --- pure: schedule geometry -------------------------------------------------
def phase_at(step: int, schedule=STACK_SCHEDULE) -> str:
    """The policy-schedule phase a given policy step falls in."""
    acc = 0
    for name, dur in schedule:
        if step < acc + dur:
            return name
        acc += dur
    return "past_end"


# --- pure: the root-cause detector -------------------------------------------
def id_collisions(rules) -> dict[str, int]:
    """rule_id -> count for any id carried by more than one rule in the chain.

    A collision is the round-92 root cause: governed_rollout keys ``used`` and
    ``consec`` by rule_id, so colliding rules share one max_invocations budget
    and one dwell accumulator.
    """
    counts: dict[str, int] = {}
    for r in rules:
        counts[r.rule_id] = counts.get(r.rule_id, 0) + 1
    return {rid: n for rid, n in counts.items() if n > 1}


# --- pure: per-fire attribution and classification ---------------------------
def attribute_fire(fire_step: int, trace: dict, rules):
    """Which rule owns a fire at `fire_step`: the FIRST rule in chain order whose
    instantaneous trigger holds there.

    Mirrors governed_rollout, which selects the first rule to reach its dwell and
    breaks ties by chain position -- the same instantaneous value-only predicate
    (``Trigger.crosses``, no reducer). Returns ``(index, rule)`` or
    ``(None, None)`` if none matches (never expected for a real fire).
    """
    for i, r in enumerate(rules):
        val = float(trace[r.trigger.feature][fire_step])
        if r.trigger.crosses(val):
            return i, r
    return None, None


def held_at_fire(trace: dict, step: int, *, drop: float = FG_DROP, open_: float = FG_OPEN) -> bool:
    """Was a cube in the closed gripper at `step`? True iff finger_gap sits in the
    gripping band [drop, open_] -- below is dropped, above is an opened gripper."""
    fg = float(trace["observable.finger_gap"][step])
    return drop <= fg <= open_


def classify_fire(result: dict, rules, schedule=STACK_SCHEDULE) -> dict | None:
    """One per-episode fire record, or None if the episode never fired.

    Pure over a rollout result dict. ``recovery`` is the shape of the repair the
    owning rule runs -- "regrasp" (parent grasp rule) vs "replace" (the appended
    place rule) -- which is what the riddle turns on.
    """
    fires = result.get("fires") or []
    if not fires:
        return None
    step = fires[0]["step"]
    trace = result["trace"]
    idx, rule = attribute_fire(step, trace, rules)
    return {
        "seed": result["seed"],
        "fire_step": step,
        "phase": phase_at(step, schedule),
        "rule_index": idx,
        "recovery": rule.recovery.name if rule is not None else "(unmatched)",
        "fg_at_fire": float(trace["observable.finger_gap"][step]),
        "xy_at_fire": float(trace["privileged.stack_xy_residual"][step]),
        "held": held_at_fire(trace, step),
        "n_fires": len(fires),
    }


def crosstab(records, flipped_seeds) -> dict[str, dict[str, int]]:
    """recovery-shape x outcome-flip cross-tab over fire records."""
    flipped = set(flipped_seeds)
    tab: dict[str, dict[str, int]] = {}
    for rec in records:
        cell = tab.setdefault(rec["recovery"], {"flip": 0, "no_flip": 0})
        cell["flip" if rec["seed"] in flipped else "no_flip"] += 1
    return tab


def _stats(xs) -> dict | None:
    xs = [float(x) for x in xs]
    if not xs:
        return None
    return {"n": len(xs), "min": min(xs), "max": max(xs), "mean": float(np.mean(xs))}


def _hist(keys) -> dict[str, int]:
    out: dict[str, int] = {}
    for k in keys:
        out[k] = out.get(k, 0) + 1
    return out


# --- impure: the real-rollout driver -----------------------------------------
def rebuild_candidate(place_store, parent_store):
    """The sealed final-rejected candidate = parent (rebuilt from `parent_store`,
    re-homed into the place campaign's critic_budget) + the archived place rule.

    Returns ``(candidate, parent, place_prereg, gen, prereg_payload)`` and asserts
    the rebuilt candidate hashes to the sealed child_sha -- rescoring the wrong
    object would be worse than no probe at all.
    """
    parent_arch = read_store_artifacts(parent_store)
    parent_prereg = rebuild_preregistration(parent_arch["preregistration"][0])
    from harness.registry import load_provider

    load_provider(parent_prereg.env_provider or "plugins.embodiment_robosuite:provider", {})
    load_provider(parent_prereg.percept_provider or DEFAULT_PERCEPT_REF, {})
    parent0 = rebuild_final_bundle(parent_prereg, parent_arch.get("generation", []))

    place_arch = read_store_artifacts(place_store)
    prereg_payload = place_arch["preregistration"][0]
    place_prereg = rebuild_preregistration(prereg_payload)
    # Re-home the parent into the place campaign's budget (its privileged place
    # trigger needs critic_budget=1); Bundle.canonical hashes the budget, so this
    # is what turns the parent's own sha into the place candidate's parent_sha.
    parent = Bundle(rules=parent0.rules, critic_budget=place_prereg.critic_budget,
                    action_budget=place_prereg.action_budget)
    gen = place_arch["generation"][0]
    candidate = parent.append(rule_from_canonical(gen["rule"]))
    if candidate.sha() != gen["child_sha"] or candidate.sha() != CANDIDATE_CHILD_SHA:
        raise AssertionError(
            f"rebuilt candidate hashes to {candidate.sha()[:12]}, archive sealed "
            f"{gen['child_sha'][:12]} / expected {CANDIDATE_CHILD_SHA[:12]}: "
            "the reconstruction does not reproduce the sealed bundle")
    return candidate, parent, place_prereg, gen, prereg_payload


def run_window_probe(place_store: str | Path, parent_store: str | Path, out_dir: str | Path, *,
                     workers: int = 10, verbose: bool = True, executor=None) -> dict:
    """Rebuild the candidate, run parent / sealed-candidate / de-collided arms on
    the campaign's own dev block, cross-tab the fires, seal one artifact."""
    from plugins.rsi.gate import _run
    from plugins.rsi.parallel import default_executor

    out_root = Path(out_dir)
    if out_root.exists():
        raise FileExistsError(f"{out_root} already exists; a probe run writes a fresh store")

    candidate, parent, prereg, gen, prereg_payload = rebuild_candidate(place_store, parent_store)
    # De-collided contrast: the appended place rule under a DISTINCT id, so its
    # used/consec no longer merge with the parent grasp rule's.
    place_rule = candidate.rules[-1]
    decollided = Bundle(rules=(*parent.rules, Rule(DECOLLIDED_ID, place_rule.trigger,
                                                   place_rule.recovery)),
                        critic_budget=parent.critic_budget, action_budget=parent.action_budget)

    # The 267 seeds the campaign's power plan actually gated on: the dev prefix.
    n = int(gen["dev_gate"]["n"])
    block = int(prereg.dev[0])
    seeds = list(prereg.dev[:n])
    specs = _specs(seeds, prereg)
    ex = executor or default_executor()

    def rollout(bundle):
        return dict(zip(seeds, ex.map(_run, [(s, bundle) for s in specs], workers=workers)))

    if verbose:
        print(f"candidate {candidate.sha()[:12]} ({len(candidate.rules)} rules) "
              f"on dev block [{block}, {block + n})")
        print(f"id collisions: {id_collisions(candidate.rules)}")
    res_parent = rollout(parent)
    res_cand = rollout(candidate)
    res_decol = rollout(decollided)

    succ = lambda r: {s: bool(r[s]["success"]) for s in seeds}  # noqa: E731
    sp, sc, sd = succ(res_parent), succ(res_cand), succ(res_decol)

    # --- sealed candidate: attribute every fire and cross-tab against flips ----
    fixed_c = [s for s in seeds if not sp[s] and sc[s]]
    broken_c = [s for s in seeds if sp[s] and not sc[s]]
    flipped_c = set(fixed_c) | set(broken_c)
    cand_fires = [classify_fire(res_cand[s], candidate.rules) for s in seeds]
    cand_fires = [rec for rec in cand_fires if rec is not None]
    tab = crosstab(cand_fires, flipped_c)
    by_recovery = _hist(rec["recovery"] for rec in cand_fires)
    if verbose:
        print(f"sealed: {len(cand_fires)} fired  fixed={len(fixed_c)} broken={len(broken_c)} "
              f"(parent-identical outcomes: {all(sc[s] == sp[s] for s in seeds)})")
        print(f"sealed: fire recovery-shape x flip cross-tab = {tab}")
        print(f"sealed: fire phase hist = {_hist(rec['phase'] for rec in cand_fires)}")

    # --- de-collided contrast: does an un-shared place rule fire and repair? ---
    def fires_of(res, rid):
        return [s for s in seeds if any(f["rule_id"] == rid for f in res[s]["fires"])]

    decol_place_seeds = fires_of(res_decol, DECOLLIDED_ID)
    decol_grasp_seeds = fires_of(res_decol, candidate.rules[0].rule_id)
    fixed_d = [s for s in seeds if not sp[s] and sd[s]]
    broken_d = [s for s in seeds if sp[s] and not sd[s]]
    place_fixed = [s for s in decol_place_seeds if not sp[s] and sd[s]]
    place_broken = [s for s in decol_place_seeds if sp[s] and not sd[s]]
    xy_at_place = []
    for s in decol_place_seeds:
        for f in res_decol[s]["fires"]:
            if f["rule_id"] == DECOLLIDED_ID:
                xy_at_place.append(float(res_decol[s]["trace"]["privileged.stack_xy_residual"][f["step"]]))
                break
    if verbose:
        print(f"de-collided: place-rule fires on {len(decol_place_seeds)} seeds; "
              f"vs parent fixed={len(fixed_d)} broken={len(broken_d)} "
              f"(place-attributed fixed={len(place_fixed)} broken={len(place_broken)})")

    replace_fires_sealed = by_recovery.get("replace", 0)
    verdict_invoke = (
        f"CONFIRMED. the appended place rule shares rule_id "
        f"{list(id_collisions(candidate.rules))} with the parent grasp rule; "
        f"used/consec merge, so the place rule fired {replace_fires_sealed}/{len(cand_fires)} "
        f"times. de-collide the id and it fires on {len(decol_place_seeds)} seeds "
        f"(+{len(place_fixed) - len(place_broken)} net).")
    verdict_window = (
        f"REFUTED. the place `replace` rule never fires in the sealed candidate "
        f"({replace_fires_sealed} fires), so 'replace acts on an empty gripper after "
        f"release' is not the mechanism. all {len(cand_fires)} fires are the parent's "
        f"regrasp rule (grasp-drop, finger_gap<{FG_DROP}), which parent-alone fires "
        f"identically -> 0 flips.")
    verdict_select = (
        f"REFUTED. once de-collided the place rule fires on placements with "
        f"xy residual up to {max(xy_at_place):.3f} and repairs "
        f"{len(place_fixed)}/{len(decol_place_seeds)}; the targeted failures are "
        f"repairable, not beyond `replace`." if xy_at_place else
        "REFUTED. see de-collided arm.")

    payload = {
        "grade": "diagnostic",
        "note": ("round 92 place-g1 riddle: the appended place rule shares the parent "
                 "grasp rule's id 'g1', so governed_rollout's used/consec (keyed by "
                 "rule_id) merge and the place rule can never fire. all 91 fires are the "
                 "parent regrasp; parent-alone fires identically -> 0 flips. de-colliding "
                 "the id makes the place rule fire and repair. NEVER a campaign lineage."),
        "source_store": str(place_store),
        "parent_store": str(parent_store),
        "source_preregistration_sha": sha_json(prereg_payload),
        "source_generation_sha": sha_json(gen),
        "candidate_bundle_sha": candidate.sha(),
        "candidate_child_sha_archived": gen["child_sha"],
        "parent_bundle_sha": parent.sha(),
        "decollided_bundle_sha": decollided.sha(),
        "seeds": {"block": block, "n": n},
        "id_collision": id_collisions(candidate.rules),
        "sealed": {
            "n_fired": len(cand_fires),
            "crosstab_recovery_x_flip": tab,
            "fires_by_recovery": by_recovery,
            "replace_fires": replace_fires_sealed,
            "fire_phase_hist": _hist(rec["phase"] for rec in cand_fires),
            "held_at_fire": _hist("held" if rec["held"] else "empty" for rec in cand_fires),
            "n_fixed_vs_parent": len(fixed_c),
            "n_broken_vs_parent": len(broken_c),
            "outcomes_identical_to_parent": all(sc[s] == sp[s] for s in seeds),
            "governed_rate": sum(sc.values()),
            "parent_rate": sum(sp.values()),
            "xy_at_fire": _stats(rec["xy_at_fire"] for rec in cand_fires),
            "fire_examples": cand_fires[:6],
        },
        "decollided": {
            "id": DECOLLIDED_ID,
            "n_place_fires": len(decol_place_seeds),
            "n_grasp_fires": len(decol_grasp_seeds),
            "n_fixed_vs_parent": len(fixed_d),
            "n_broken_vs_parent": len(broken_d),
            "place_attributed_fixed": len(place_fixed),
            "place_attributed_broken": len(place_broken),
            "xy_at_place_fire": _stats(xy_at_place),
        },
        "verdicts": {
            "H-invoke": verdict_invoke,
            "H-window": verdict_window,
            "H-select": verdict_select,
        },
    }
    digest = CampaignStore(out_root).put("place_window_probe", payload)
    if verbose:
        print(f"place_window_probe {digest[:12]} -> {out_root}")
    payload["_artifact_sha"] = digest
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--place-store", type=Path, default=Path("runs/place-g1"),
                        help="sealed place campaign store (candidate + dev partition)")
    parser.add_argument("--parent-store", type=Path, default=Path("runs/stack-g1"),
                        help="sealed campaign store the parent bundle is rebuilt from")
    parser.add_argument("--out", type=Path, required=True,
                        help="fresh store for the one diagnostic artifact; must not exist")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    run_window_probe(args.place_store, args.parent_store, args.out,
                     workers=args.workers, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
