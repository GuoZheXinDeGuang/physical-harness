#!/usr/bin/env python3
"""Fold `probe_pi05_rollout.py` output directories into ONE paired table.

The question it exists to answer: the serving path drains the whole 10-action
chunk, so 9 of every 10 steps are open loop. How much of the closed-loop failure
is that, and how much is the policy's own compounding error? The experiment is
the SAME checkpoint over the SAME seeds under different execution policies
(`--replan-every` / `--ensemble`), so every arm is a paired sample and the only
thing that varies is how the chunk is consumed.

**A re-run of the control arm is not a copy of the sealed baseline.** openpi
draws a fresh noise key per request, so the policy is stochastic: the same
checkpoint on the same seed with the same execution policy flips outcomes. The
control arm is therefore re-run alongside every treatment, and its disagreement
with the sealed baseline IS the noise floor -- the bar every treatment has to
clear before its own difference means anything. Naming a `--baseline` and a
`--control` prints that floor instead of leaving a reader to assume it is zero.

Two tests, because the design is paired and the question is usually asked
unpaired:

  fisher    two-sided exact on the 2x2 of successes -- what gets asked for, and
            what you would use if the arms were independent samples. It throws
            away the pairing, so it is the CONSERVATIVE reading here.
  mcnemar   ``plugins.rsi.stats.power.mcnemar_p`` -- the repo's own exact test,
            over the discordant seeds only. This is the correct test for this
            design and the one to read first; it also prints the discordant
            split, which says how much the arms actually disagree per seed
            rather than only in the margin.

Both are stdlib-exact (``math.comb``), same as ``power.py``: no scipy, and the
numbers are reproducible from a fresh clone with nothing installed.

**The grasp column rests on a predicate under audit.** ``obj_grasped`` is
contact AND gripper-closed with no lift term, which this repo has already been
burned by once (a near-always-true grasp check). Every grasp number this script
prints carries that caveat; ``obj_in_microwave`` was audited in gate 1 and is
sound. The flag rides in the JSON artifact so it cannot be dropped downstream.

    .venv/bin/python scripts/compare_serving_arms.py \
      --arm "sealed baseline:runs/pi05-campaign/gate2_trainsplit" \
      --arm "control k=10:runs/pi05-campaign/round98_k10,runs/round98b_k10" \
      --arm "k=1:runs/pi05-campaign/round98_k1,runs/round98b_k1" \
      --baseline "sealed baseline" --control "control k=10" \
      --out runs/pi05-campaign/round98_serving_ablation.json

    # the tests, against values computed by hand (0.1 s, no env, no deps)
    .venv/bin/python scripts/compare_serving_arms.py --selfcheck
"""
from __future__ import annotations

import argparse
import json
from math import comb
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.rsi.stats.power import mcnemar_p  # noqa: E402

#: The two predicates this comparison reports, and how much each is worth.
PREDICATES = {
    "obj_in_microwave": "audited in gate 1 (3f02334) -- sound, and untouched by "
                        "the grasp audit. This is the headline.",
    "obj_grasped": "VOID under the `latch` ruler: commit 845b57a measured the "
                   "bare contact+fingers predicate reading True on 7/7 synthetic "
                   "controls where the hand closed around meat still on the "
                   "shelf. Numbers under `secure` (SECURE_DZ = 0.08 m lift term "
                   "conjoined) are the re-earned ruler.",
}

#: WHICH grasp ruler an episode was scored with. The audit replaced
#: ``load_predicates``' binding in the working tree at 2026-08-31 03:06:34, in
#: the middle of this campaign's k=1 arm -- the probe spawns one child per
#: episode, so each child picked up whichever version of the file existed when
#: it launched. Reconstructed from the per-episode file mtimes against that
#: write (``runs/round98b_k1/ep_pi05_420113.json`` is the first child launched
#: after it) and then FROZEN here as data, because mtimes do not survive the
#: first rsync of an evidence directory and this boundary has to.
#:
#: Pooling a latch count with a secure count would be averaging two different
#: questions, so :func:`compare` refuses rather than doing it quietly.
GRASP_RULER_BOUNDARY = ("round98b_k1", 420113)

#: Runs collected entirely AFTER the audit settled, so ``load_predicates`` bound
#: the SECURE_DZ predicate for every episode -- no per-seed boundary to freeze,
#: the whole directory is secure. A round100 secure count and a round98 latch
#: count are answers to different questions, so labelling this right is what
#: makes :func:`compare` REFUSE the r2-vs-round1 grasp cross-comparison instead
#: of printing a ruler swap as if it were a policy effect.
SECURE_DIRS = frozenset({"round100_r2_eval"})


def grasp_ruler(run_dir: str, seed: int) -> str:
    if run_dir in SECURE_DIRS:
        return "secure"
    d, s = GRASP_RULER_BOUNDARY
    return "secure" if (run_dir == d and seed >= s) else "latch"


def fisher_p(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact on [[a, b], [c, d]] (successes, failures per arm).

    Two-sided by the conventional definition: sum the probability of every table
    with the same margins that is no more likely than the observed one.
    """
    n = a + b + c + d
    r1, c1 = a + b, a + c
    def p(k: int) -> float:
        return comb(r1, k) * comb(n - r1, c1 - k) / comb(n, c1)
    obs = p(a)
    lo, hi = max(0, c1 - (n - r1)), min(r1, c1)
    return min(1.0, sum(p(k) for k in range(lo, hi + 1) if p(k) <= obs * (1 + 1e-9)))


def load_arm(dirs: list[Path]) -> dict[int, dict]:
    """seed -> episode record, over one or more probe output directories.

    A seed appearing twice is a collision between runs that were meant to be
    disjoint, and silently keeping one of them would put an unpaired sample in a
    paired table.
    """
    rows: dict[int, dict] = {}
    for d in dirs:
        for path in sorted(d.glob("ep_*.json")):
            rec = json.loads(path.read_text())
            seed = int(rec["seed"])
            if seed in rows:
                raise SystemExit(f"seed {seed} appears twice across {dirs} -- "
                                 f"these directories are not disjoint")
            rec["grasp_ruler"] = grasp_ruler(d.name, seed)
            rows[seed] = rec
    if not rows:
        raise SystemExit(f"no ep_*.json under {dirs}")
    return rows


def _mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def _sha(rec: dict) -> str:
    """Which weights answered. ``--sha`` is optional, so fall back to what the
    server echoed in the handshake -- an arm whose identity is unknown must not
    read the same as one that agrees with the others."""
    return str(rec.get("checkpoint_sha")
               or ((rec.get("handshake") or {}).get("metadata") or {})
               .get("checkpoint_sha"))


def _trace_share(eps: list[dict]) -> float | None:
    """base-mode share off ``action_trace`` (every ``--trace-every``th step).

    A SUBSAMPLE, and the per-step counter below is the number to quote. It
    exists only because the sealed baseline predates that counter, and an arm
    that cannot enter the mechanism comparison at all is worse than one that
    enters it through a coarser estimator applied identically to every arm.
    """
    acts = [a for r in eps for a in (r.get("action_trace") or [])]
    return round(sum(a["env_action"][11] > 0 for a in acts) / len(acts), 4) if acts else None


def summarise(name: str, rows: dict[int, dict]) -> dict:
    eps = list(rows.values())
    done = [r for r in eps if not r.get("truncated") and not r.get("crashed")]
    steps = sum(r.get("steps_run") or 0 for r in eps)
    counted = [r for r in eps if r.get("base_mode_steps") is not None]
    return {
        "arm": name,
        "n": len(eps),
        "seeds": sorted(rows),
        "truncated": sum(bool(r.get("truncated")) for r in eps),
        "crashed": sum(bool(r.get("crashed")) for r in eps),
        "splits": sorted({str(r.get("split")) for r in eps}),
        "execution": sorted({(r.get("replan_every"), r.get("ensemble"))
                             for r in eps}, key=str),
        "checkpoint_sha": sorted({_sha(r) for r in eps}),
        **{p: sum(bool(r["stage_reached"].get(p)) for r in eps) for p in PREDICATES},
        # grasp, split by the ruler that scored it -- the total above is only
        # readable when this has one entry
        "obj_grasped_by_ruler": {
            ruler: f"{sum(bool(r['stage_reached'].get('obj_grasped')) for r in eps if r['grasp_ruler'] == ruler)}"
                   f"/{sum(r['grasp_ruler'] == ruler for r in eps)}"
            for ruler in sorted({r["grasp_ruler"] for r in eps})},
        # pooled over steps, not a mean of per-episode shares: a longer episode
        # commanded more steps and should weigh more. Demos: 20.09%. null means
        # the run predates the counter -- never 0, which would read as "the
        # policy never commanded the base".
        "base_mode_share_pooled": round(
            sum(r["base_mode_steps"] for r in counted) / max(steps, 1), 4
        ) if counted else None,
        "base_mode_share_mean": _mean(r.get("base_mode_share") for r in eps),
        "base_mode_share_trace_subsample": _trace_share(eps),
        "inference_calls_mean": _mean(r.get("inference_calls") for r in eps),
        # over COMPLETED episodes only: a watchdog kill measures the watchdog
        "seconds_mean_completed": _mean(r.get("seconds") for r in done),
        "steps_mean": _mean(r.get("steps_run") for r in eps),
    }


def compare(pred: str, treat: dict[int, dict], ref: dict[int, dict]) -> dict:
    """Both tests on one predicate, over the seeds the two arms share."""
    def hit(r) -> bool:
        return bool(r["stage_reached"].get(pred))
    paired = sorted(set(treat) & set(ref))
    if pred == "obj_grasped":
        rulers = {r["grasp_ruler"] for s in paired for r in (treat[s], ref[s])}
        if len(rulers) > 1:
            # Not a hedge: the latch fires on a hand closed around meat that
            # never moved, so a latch count and a secure count are answers to
            # different questions and their difference is the RULER, not the
            # arm. Refusing is the only reading that is not a lie.
            return {"predicate": pred, "n_paired": len(paired),
                    "refused": f"the two arms were scored with different grasp "
                               f"rulers ({sorted(rulers)}) -- no comparison is "
                               f"possible without re-running one of them"}
    a = sum(hit(treat[s]) for s in paired)
    c = sum(hit(ref[s]) for s in paired)
    n = len(paired)
    fixed = sum(hit(treat[s]) and not hit(ref[s]) for s in paired)
    broken = sum(hit(ref[s]) and not hit(treat[s]) for s in paired)
    return {
        "predicate": pred, "n_paired": n,
        "treatment": f"{a}/{n}", "reference": f"{c}/{n}",
        "fisher_p": round(fisher_p(a, n - a, c, n - c), 4),
        "mcnemar_discordant": {"fixed": fixed, "broken": broken},
        "mcnemar_p": round(mcnemar_p(fixed, broken), 4),
        "unpaired_seeds": sorted(set(treat) ^ set(ref)) or None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", action="append", required=True, metavar="NAME:DIR[,DIR]",
                    help="an arm and the probe output dir(s) its episodes live in")
    ap.add_argument("--baseline", help="arm name every other arm is tested against")
    ap.add_argument("--control", help="the re-run control; its gap to --baseline "
                                      "is the noise floor, and every other arm is "
                                      "tested against it too")
    ap.add_argument("--out", help="write the artifact here (JSON)")
    a = ap.parse_args(argv)

    arms: dict[str, dict[int, dict]] = {}
    for spec in a.arm:
        name, _, dirs = spec.partition(":")
        if not dirs:
            ap.error(f"--arm {spec!r}: expected NAME:DIR[,DIR]")
        arms[name] = load_arm([Path(d) for d in dirs.split(",")])

    doc = {
        "question": "how much of pi0.5's closed-loop failure is the chunk being "
                    "drained open loop, vs compounding error in the policy",
        "design": "paired -- same checkpoint, same seeds, same split; only the "
                  "execution policy (replan_every/ensemble) varies",
        "predicates": PREDICATES,
        "seed_class": "scratch (42xxxx) -- burns no ledger block",
        "arms": {name: summarise(name, rows) for name, rows in arms.items()},
        "tests": {},
    }
    for ref_name in (a.baseline, a.control):
        if ref_name is None:
            continue
        if ref_name not in arms:
            ap.error(f"--baseline/--control {ref_name!r} is not one of {sorted(arms)}")
        doc["tests"][f"vs {ref_name}"] = {
            name: [compare(p, rows, arms[ref_name]) for p in PREDICATES]
            for name, rows in arms.items() if name != ref_name}

    print(json.dumps(doc, indent=1, sort_keys=True, default=str))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
    return 0


def _selfcheck() -> None:
    """The one runnable check: both tests against values computed by hand.

    fisher_p on a table nobody can argue about (2x2 with a 1/10 vs 1/10 split is
    p=1 by symmetry), the textbook tea-tasting table (p=0.4857), and a complete
    separation (10/10 vs 0/10, p=1.08e-5). mcnemar comes from power.py's own
    definition, so this only pins that a 0-discordant comparison is p=1.
    """
    assert abs(fisher_p(1, 9, 1, 9) - 1.0) < 1e-9, fisher_p(1, 9, 1, 9)
    assert abs(fisher_p(3, 1, 1, 3) - 0.4857) < 1e-4, fisher_p(3, 1, 1, 3)
    assert abs(fisher_p(10, 0, 0, 10) - 1.083e-5) < 1e-8, fisher_p(10, 0, 0, 10)
    # the number this campaign actually turns on: 2/10 vs 0/10 does not separate
    assert abs(fisher_p(2, 8, 0, 10) - 0.4737) < 1e-4, fisher_p(2, 8, 0, 10)
    assert mcnemar_p(0, 0) == 1.0
    assert abs(mcnemar_p(2, 0) - 0.5) < 1e-9
    print("compare_serving_arms selfcheck ok")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        sys.exit(main())
