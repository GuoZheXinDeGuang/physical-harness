"""Beam search over rule chains, with a selection block of its own.

Why
---
Round 8 measured the failure mode: a campaign whose every step cleared its gate
converged earlier and lower than one that promoted a weaker first rule. Greedy
top-1 commits to a branch before anyone knows where it leads. Round 17 measured
the other half -- a candidate the gate rejected was worth +6.5pp on held-out --
so the single surviving chain is not obviously the best one available.

The discipline this forces
--------------------------
K branches need a step that CHOOSES among them, and that step cannot use the
dev seeds each branch was gated on, nor the held-out block, which is scored
once. So the preregistration carries a THREE-way split: a dev reservoir that
gates each branch generation by generation, a selection block that picks the
winner, and held-out that scores it. Skipping the middle block is the whole
reason beam search is easy to do dishonestly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Sequence

import numpy as np

from governor.campaign import CampaignStore, Preregistration, _specs, propose_rule, sha_json
from governor.env import EpisodeSpec
from governor.gate import PairedResult, ablation_curve, paired_gate
from governor.governed import Bundle, RecoverySpec, Rule
from governor.search import search_triggers


@dataclass
class Branch:
    """One chain under construction."""

    branch_id: str
    bundle: Bundle
    alive: bool = True
    history: list[dict] = field(default_factory=list)
    residual_rate: float | None = None
    #: Discordant pairs per residual failure, measured from this branch's own
    #: last sealed generation. Round 24 ran beam with the fixed 0.7 assumption
    #: that round 32 disproved, so every branch past depth 1 was undersampled --
    #: which is the same defect that made the greedy chain look exhausted.
    discordance_yield: float | None = None

    @property
    def depth(self) -> int:
        return len(self.bundle.rules)


def _run(job):
    from governor.governed import governed_rollout
    spec, bundle = job
    return governed_rollout(spec, bundle)


def _measure(specs, bundle, workers):
    from multiprocessing import Pool
    with Pool(workers) as pool:
        return pool.map(_run, [(s, bundle if bundle.rules else None) for s in specs])


#: Episodes the seeding search sees. Generation 1's gate must use the SAME
#: slice: `run_campaign` sizes measurement, search and gate together precisely
#: so a candidate is never judged on a population it was not fitted to, and
#: beam quietly searched 120 while gating 60 until round 40.
SEED_SLICE = 120


def run_beam_campaign(
    prereg: Preregistration, store: CampaignStore, *, beam_width: int = 3,
    selection: Sequence[int] = (), workers: int = 10, verbose: bool = True,
) -> dict:
    """Grow `beam_width` chains in parallel, then choose on a block of its own."""
    if not selection:
        raise ValueError("beam search needs a selection block distinct from dev and held-out")
    overlap = (set(selection) & set(prereg.dev)) | (set(selection) & set(prereg.heldout))
    if overlap:
        raise ValueError(f"selection block overlaps dev or held-out: {sorted(overlap)[:5]}")

    prereg_sha = store.put("preregistration", {**asdict(prereg), "beam_width": beam_width,
                                               "selection": list(selection)})
    reservoir = _specs(prereg.dev, prereg)
    sel_specs = [EpisodeSpec(seed=s, task=prereg.task, policy=prereg.policy,
                             percept_noise=prereg.percept_noise,
                             env_provider=prereg.env_provider,
                             policy_provider=prereg.policy_provider,
                             percept_provider=prereg.percept_provider) for s in selection]
    if verbose:
        print(f"preregistration {prereg_sha[:12]}  beam={beam_width} "
              f"dev={len(reservoir)} select={len(sel_specs)} heldout={len(prereg.heldout)}")

    root = Bundle(rules=(), critic_budget=prereg.critic_budget, action_budget=prereg.action_budget)
    base = _measure(reservoir[:SEED_SLICE], root, workers)
    ranked = search_triggers([r["trace"] for r in base], [r["success"] for r in base],
                             privilege_budget=prereg.critic_budget, top_k=beam_width)
    if not ranked:
        return {"preregistration_sha": prereg_sha, "branches": 0}

    branches = [
        Branch(f"b{i}", root.append(Rule(f"b{i}g1", cand.trigger,
                                         RecoverySpec(sensor_sd=prereg.recovery_sensor_sd))))
        for i, cand in enumerate(ranked)
    ]
    if verbose:
        print(f"\nseeding {len(branches)} branches from the top-{beam_width} candidates:")
        for b, cand in zip(branches, ranked):
            print(f"  {b.branch_id}: {cand.trigger.describe()}")

    for gen in range(1, prereg.max_generations + 1):
        live = [b for b in branches if b.alive]
        if not live:
            break
        if verbose:
            print(f"\n--- generation {gen}: {len(live)} live branches ---")
        for b in live:
            parent = Bundle(rules=b.bundle.rules[:-1], critic_budget=b.bundle.critic_budget,
                            action_budget=b.bundle.action_budget) if gen == 1 else b.bundle
            child = b.bundle
            if gen > 1:
                dev = _size_slice(b, reservoir, prereg, gen)
                cur = _measure(dev, b.bundle, workers)
                labels = [r["success"] for r in cur]
                if all(labels):
                    b.alive = False
                    continue
                rule = propose_rule([r["trace"] for r in cur], labels, generation=gen, prereg=prereg)
                if rule is None:
                    b.alive = False
                    continue
                rule = Rule(f"{b.branch_id}g{gen}", rule.trigger, rule.recovery)
                child = b.bundle.append(rule)
                child.assert_atomic_child_of(b.bundle)
                parent = b.bundle
            else:
                dev = _size_slice(b, reservoir, prereg, gen)
            verdict = paired_gate(dev, child, baseline=parent if parent.rules else None,
                                  workers=workers)
            ok = (verdict.fixed >= prereg.min_fixed and verdict.p_value < prereg.alpha
                  and verdict.fixed > verdict.broken)
            b.history.append({"generation": gen, "n": verdict.n, "gate": asdict(verdict),
                              "promoted": ok, "rule": child.rules[-1].canonical()})
            if verbose:
                print(f"  {b.branch_id} gen{gen} (n={verdict.n}): {verdict.line()} "
                      f"-> {'promoted' if ok else 'branch stops'}")
            if ok:
                b.bundle = child
                b.residual_rate = 1.0 - verdict.governed_rate
                residual = max(round((1.0 - verdict.base_rate) * verdict.n), 1)
                b.discordance_yield = max((verdict.fixed + verdict.broken) / residual,
                                          prereg.min_discordance_yield)
            else:
                b.alive = False

    grown = [b for b in branches if b.bundle.rules]
    if verbose:
        print(f"\n--- selection block (n={len(sel_specs)}), distinct from dev and held-out ---")
    scored = []
    for b in grown:
        r = paired_gate(sel_specs, b.bundle, baseline=None, workers=workers)
        scored.append((b, r))
        if verbose:
            print(f"  {b.branch_id} (depth {b.depth}): {r.line()}")
    if not scored:
        return {"preregistration_sha": prereg_sha, "branches": 0}
    best, best_r = max(scored, key=lambda t: t[1].delta)
    if verbose:
        print(f"\nselected {best.branch_id} (depth {best.depth}, "
              f"selection delta {best_r.delta:+.1%})")

    held = _specs(prereg.heldout, prereg)
    final = paired_gate(held, best.bundle, baseline=None, workers=workers)
    if verbose:
        print(f"\nheld-out (scored once, n={len(held)}): {final.line()}")
    curve = ablation_curve(held, best.bundle, workers=workers)
    if verbose:
        for sd, r in curve:
            tag = "GROUND TRUTH (privileged)" if sd == 0.0 else "onboard sensor"
            print(f"  sensor_sd={sd:.3f}  {r.line()}  [{tag}]")
    result = {
        "preregistration_sha": prereg_sha, "beam_width": beam_width,
        "branches": [{"id": b.branch_id, "depth": b.depth, "sha": b.bundle.sha(),
                      "history": b.history, "selection": asdict(r)} for b, r in scored],
        "selected": best.branch_id, "heldout": asdict(final),
        "ablation": [(sd, asdict(r)) for sd, r in curve],
    }
    store.put("beam_result", result)
    return result


def _size_slice(branch: Branch, reservoir, prereg: Preregistration, gen: int):
    """Power-size this branch's dev slice from ITS OWN residual rate."""
    if gen == 1:
        # Generation 1 was seeded by a search over SEED_SLICE; gate it there.
        # Keyed on the GENERATION, not the depth: a promoted branch still has
        # depth 1 when generation 2 starts, so keying on depth silently pinned
        # every later generation to the seeding size too.
        return reservoir[:SEED_SLICE]
    if not prereg.scale_dev_by_power:
        return reservoir[:SEED_SLICE]
    from governor.power import plan_generation

    rate = branch.residual_rate if branch.residual_rate is not None else 0.5
    plan = plan_generation(branch.depth + 1, round(rate * len(reservoir)), len(reservoir),
                           len(reservoir), fix_share=prereg.power_fix_share,
                           alpha=prereg.alpha, power=prereg.power_target,
                           discordance_yield=(branch.discordance_yield
                                              if branch.discordance_yield is not None
                                              else prereg.prior_discordance_yield),
                           search_fraction=(prereg.screen_search_fraction
                                            if prereg.screen_triggers else 1.0))
    return reservoir[: plan.seeds_used]
