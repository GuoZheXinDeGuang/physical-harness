#!/usr/bin/env python3
"""Round 88 probe: is the search objective blind to fire-time repair yield?

Diagnosis carried in (verify against runs/round25-rerun): the naive arm rule
[running_range(observable.finger_gap) < 0.005, dwell 1, arm_after 95] and the
search arm rule [observable.finger_gap < 0.007392, dwell 1, arm_after 43] have
IDENTICAL detection on held-out 44200-44399 (119 fires, 0 broken both) but
different repair yield (naive 63 fixed vs search 54). Hypothesis H1: the search
objective (plugins/rsi/stats/search.py score = recall - 1.2*fpr, no repair
term; candidate arms only {eod, eod+2, eod+6} anchored at sigma-ONSET) cannot
see fire-time-dependent repair success, and the naive rule's sigma-peak arming
(t=95) is structurally unreachable by the search.

This is modelled on scripts/rescore_heldout.py: the reconstruction/scoring
functions are pure and unit-tested against fakes (tests/test_probe_arm_time.py);
only `run_probe` touches real rollouts. Both sealed rules are rebuilt from the
content-addressed store via rescore_heldout.rule_from_canonical and asserted
against their archived canonicals -- never hand-typed.

P1 (zero fresh seeds, ~1 min): regenerate the 60 ungoverned dev traces (seeds
44000-44059) through round25_rerun.py's exact trace-collection path, then in
memory score both sealed rules under the search objective, count how many
search candidates tie at the top score, and check whether the naive rule's arm
(95) and threshold (0.005) are even reachable by the search enumeration.

P2 (diagnostic reuse of the burned block 44200-44399, ~10 min at workers=10):
four paired gates vs the ungoverned baseline -- the search rule at arm_after
43/70/95 and the naive rule at arm_after 43 -- so repair yield can be read as a
function of fire time with detection held fixed. The search rule at arm 43 is
the sealed anchor and MUST reproduce fixed=54; if it does not, the
reconstruction or the environment drifted and every downstream number is
garbage, so the probe stops instead of writing.

The artifact is graded "diagnostic": the held-out block is reused, so these are
NOT headline numbers.

    PYTHONPATH=. .venv/bin/python scripts/probe_arm_time.py
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from dataclasses import asdict
from pathlib import Path

# Runnable as `python scripts/probe_arm_time.py` without PYTHONPATH gymnastics.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from plugins.rsi import gate
from plugins.rsi.campaign import CampaignStore, _specs, sha_json
from plugins.rsi.gate import paired_gate
from plugins.rsi.governed import Bundle
from plugins.rsi.stats.search import (
    _quantile_grid,
    earliest_divergence,
    reduce_series,
    search_triggers,
)
from scripts.parity_check import read_store_artifacts, rebuild_preregistration
from scripts.rescore_heldout import rule_from_canonical

FINGER_GAP = "observable.finger_gap"


# --- pure reconstruction / scoring (unit-tested with fakes) -----------------

def score_trigger(trigger, traces, labels, *, fp_penalty, earliness) -> dict:
    """Score one trigger EXACTLY as search_triggers scores an enumerated candidate.

    The inner block is lifted verbatim from search_triggers so that scoring the
    sealed rules is the same arithmetic the search applied to its own head --
    that is the whole point of P1(a), and the unit test pins it by scoring a
    candidate the search itself enumerated.
    """
    labels = np.asarray(labels, dtype=bool)
    n_steps = max(len(next(iter(t.values()))) for t in traces)
    fires = [trigger.fire_step(t) for t in traces]
    tp = [f for f, y in zip(fires, labels) if not y and f is not None]
    fp = [f for f, y in zip(fires, labels) if y and f is not None]
    n_bad = int((~labels).sum())
    n_ok = int(labels.sum())
    recall = len(tp) / max(n_bad, 1)
    fpr = len(fp) / max(n_ok, 1)
    med = float(np.median(tp)) if tp else float(n_steps)
    lead = n_steps - med
    score = recall - fp_penalty * fpr + earliness * (lead / n_steps)
    return {"recall": recall, "false_positive": fpr, "median_fire": med,
            "lead": lead, "score": score, "fires": len(tp) + len(fp),
            "tp": len(tp), "fp": len(fp)}


def arm_candidates(traces, labels, feature: str, reducer: str):
    """The {eod, eod+2, eod+6} arm set the search would enumerate for
    (feature, reducer) -- the reduced-series divergence scan, verbatim from
    search_triggers. Returns (eod, arms); (None, ()) when nothing diverges."""
    labels = np.asarray(labels, dtype=bool)
    n_steps = max(len(next(iter(t.values()))) for t in traces)
    reduced = [{feature: reduce_series(t[feature], reducer)} for t in traces]
    eod = earliest_divergence(reduced, labels, feature)
    if eod is None:
        return None, ()
    arms = sorted({eod, min(eod + 2, n_steps - 1), min(eod + 6, n_steps - 1)})
    return eod, tuple(int(a) for a in arms)


def threshold_grid(traces, feature: str, reducer: str, eod: int) -> np.ndarray:
    """The _quantile_grid over reduced values from eod on -- exactly the
    thresholds the search enumerates for this (feature, reducer)."""
    reduced = [{feature: reduce_series(t[feature], reducer)} for t in traces]
    values = np.concatenate([t[feature][eod:] for t in reduced if len(t[feature]) > eod])
    return _quantile_grid(values)


def tie_count_at_top(ranked) -> tuple[int, float | None]:
    """How many ranked candidates share the top (float-exact) score.

    search_triggers returns the list sorted descending and deduped to one entry
    per (feature, op, reducer), so this counts distinct families the objective
    cannot pick between: a large tie is the objective failing to discriminate.
    """
    if not ranked:
        return 0, None
    top = ranked[0].score
    return sum(1 for s in ranked if s.score == top), top


def _armed(rule, arm_after: int):
    """The same rule with only its trigger's arm_after moved -- recovery untouched."""
    return dataclasses.replace(
        rule, trigger=dataclasses.replace(rule.trigger, arm_after=arm_after))


# --- the one impure driver --------------------------------------------------

def run_probe(store_dir: str | Path, out_dir: str | Path, *,
              workers: int = 10, verbose: bool = True, executor=None) -> dict:
    """Rebuild both sealed rules, run P1 (dev) and P2 (held-out), seal one artifact."""
    store_dir = Path(store_dir)
    out_root = Path(out_dir)
    if out_root.exists():
        raise FileExistsError(f"{out_root} already exists; a probe writes a fresh store")

    archived = read_store_artifacts(store_dir)
    prereg_payloads = archived.get("preregistration", [])
    if not prereg_payloads:
        raise ValueError(f"{store_dir} has no preregistration artifact")
    rerun_payloads = archived.get("round25_rerun", [])
    if not rerun_payloads:
        raise ValueError(f"{store_dir} has no round25_rerun artifact")
    prereg = rebuild_preregistration(prereg_payloads[0])
    rerun = rerun_payloads[0]

    # Round 69 registry inversion (rescore_heldout line ~109): features register
    # when the embodiment plugin is imported via its provider ref. Nothing in
    # THIS parent process has imported it, so declared_privilege() below and the
    # admissible-feature scan in search_triggers would hit an empty catalog.
    # Load the archived env provider ref FIRST, before any privilege call.
    from harness.registry import load_provider

    load_provider(prereg.env_provider or "plugins.embodiment_robosuite:provider", {})

    # Rebuild BOTH sealed rules from the store, asserting against the archived
    # canonicals -- rescoring the wrong object would be worse than not rescoring.
    search_canon = rerun["arms"]["search"]["rule"]
    naive_canon = rerun["arms"]["naive_mock"]["rule"]
    search_rule = rule_from_canonical(search_canon)
    naive_rule = rule_from_canonical(naive_canon)
    if search_rule.canonical() != search_canon:
        raise AssertionError("rebuilt search rule does not reproduce its archived canonical")
    if naive_rule.canonical() != naive_canon:
        raise AssertionError("rebuilt naive rule does not reproduce its archived canonical")

    if executor is None:
        from plugins.rsi.parallel import default_executor
        executor = default_executor()

    fp_penalty = prereg.fp_penalty
    earliness = prereg.earliness

    # --- P1: the 60 ungoverned dev traces, round25_rerun's exact path --------
    dev_specs = _specs(prereg.dev, prereg)
    if verbose:
        print(f"P1 dev rollout: {len(dev_specs)} ungoverned episodes on {prereg.task}")
    cur = executor.map(gate._run, [(s, None) for s in dev_specs], workers=workers)
    traces = [r["trace"] for r in cur]
    labels = [r["success"] for r in cur]
    dev_rate = float(np.mean(labels))
    if verbose:
        print(f"  dev rate {sum(labels)}/{len(labels)} ({dev_rate:.1%})")

    search_score = score_trigger(search_rule.trigger, traces, labels,
                                 fp_penalty=fp_penalty, earliness=earliness)
    naive_score = score_trigger(naive_rule.trigger, traces, labels,
                                fp_penalty=fp_penalty, earliness=earliness)

    ranked = search_triggers(traces, labels, privilege_budget=prereg.critic_budget,
                             top_k=50, earliness=earliness, fp_penalty=fp_penalty)
    ties, top = tie_count_at_top(ranked)

    reducer = naive_rule.trigger.reducer  # "range" -- the naive rule's reduction
    eod, arms = arm_candidates(traces, labels, FINGER_GAP, reducer)
    naive_arm = int(naive_rule.trigger.arm_after)
    naive_thr = float(naive_rule.trigger.threshold)
    if eod is not None:
        grid = threshold_grid(traces, FINGER_GAP, reducer, eod)
        grid_list = [float(x) for x in grid]
        in_grid = bool(np.any(np.isclose(grid, naive_thr, atol=1e-9, rtol=0.0)))
    else:
        grid_list, in_grid = [], False

    p1 = {
        "dev_block": [min(prereg.dev), max(prereg.dev) + 1],
        "n_dev": len(prereg.dev),
        "dev_rate": dev_rate,
        "objective": {"score": "recall - fp_penalty*fpr + earliness*lead/n_steps",
                      "fp_penalty": fp_penalty, "earliness": earliness},
        "rule_scores": {"search": search_score, "naive": naive_score},
        "tie_count_at_top": ties,
        "top_score": top,
        "n_ranked": len(ranked),
        "arm_set": {
            "feature": FINGER_GAP, "reducer": reducer,
            "earliest_divergence": eod, "arms": list(arms),
            "naive_arm": naive_arm, "naive_arm_reachable": naive_arm in arms,
        },
        "grid_check": {
            "reducer": reducer, "threshold_grid": grid_list,
            "naive_threshold": naive_thr, "in_grid": in_grid,
        },
    }
    if verbose:
        print(f"  search rule score={search_score['score']:.4f} "
              f"recall={search_score['recall']:.2f} fpr={search_score['false_positive']:.2f}")
        print(f"  naive  rule score={naive_score['score']:.4f} "
              f"recall={naive_score['recall']:.2f} fpr={naive_score['false_positive']:.2f}")
        print(f"  {ties} candidate(s) tied at top score {top}")
        print(f"  ({reducer}) eod={eod} arms={list(arms)}  "
              f"naive arm {naive_arm} reachable={naive_arm in arms}  "
              f"0.005 in grid={in_grid}")

    # --- P2: repair yield vs fire time on the burned held-out block ----------
    heldout_specs = _specs(prereg.heldout, prereg)

    def _paired(rule):
        bundle = Bundle(rules=(), critic_budget=prereg.critic_budget,
                        action_budget=prereg.action_budget).append(rule)
        return paired_gate(heldout_specs, bundle, baseline=None, workers=workers,
                           executor=executor)

    sealed_fixed = int(rerun["arms"]["search"]["heldout"]["fixed"])
    if verbose:
        print(f"P2 held-out {len(heldout_specs)} seeds; anchor: search@43 must reproduce "
              f"fixed={sealed_fixed}")
    anchor = _paired(search_rule)  # sealed search rule already arms at 43
    if verbose:
        print(f"  search@43: {anchor.line()}")
    if anchor.fixed != sealed_fixed:
        raise SystemExit(
            f"P2 anchor drift: search@43 fixed={anchor.fixed} != sealed {sealed_fixed}. "
            "The reconstruction or the environment drifted; every downstream number "
            "would be garbage. Stopping WITHOUT writing an artifact.")

    p2_arms = {"search_arm43": anchor}
    for name, rule, arm in (("search_arm70", search_rule, 70),
                            ("search_arm95", search_rule, 95),
                            ("naive_arm43", naive_rule, 43)):
        res = _paired(_armed(rule, arm))
        p2_arms[name] = res
        if verbose:
            print(f"  {name}: {res.line()}")

    payload = {
        "grade": "diagnostic",  # burned-block reuse -- NOT headline numbers
        "source_store": str(store_dir),
        "source_preregistration_sha": sha_json(prereg_payloads[0]),
        "source_round25_rerun_sha": sha_json(rerun),
        "search_rule_canonical": search_canon,
        "naive_rule_canonical": naive_canon,
        "p1": p1,
        "p2": {
            "heldout_block": [min(prereg.heldout), max(prereg.heldout) + 1],
            "sealed_anchor": {
                "search_arm43_fixed": sealed_fixed,
                "naive_arm95_fixed": int(rerun["arms"]["naive_mock"]["heldout"]["fixed"]),
            },
            "arms": {k: asdict(v) for k, v in p2_arms.items()},
        },
    }
    digest = CampaignStore(out_root).put("arm_time_probe", payload)
    if verbose:
        print(f"arm_time_probe {digest[:12]} -> {out_root}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--store", type=Path, default=Path("runs/round25-rerun"),
                        help="sealed round25_rerun store (default runs/round25-rerun)")
    parser.add_argument("--out", type=Path, default=Path("runs/round88-armtime"),
                        help="fresh store for the probe artifact; must not exist")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    run_probe(args.store, args.out, workers=args.workers, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
