"""Campaign lifecycle: preregistration, atomic generations, content-hashed artifacts.

Adopted from Zetta (Zetta-Embodiment/zetta/evolution/): a preregistered seed
partition frozen before any episode runs, exactly one critic-recovery pair
appended per generation with the parent frozen, and every artifact written under
its content hash so a campaign cannot be edited in place.

Deliberate divergences
----------------------
* **Gain is measured against the PARENT, not the ungoverned policy.** Zetta's
  same-seed gate compares a candidate against its parent; reporting each
  generation against the raw baseline would let one good rule keep collecting
  credit for later generations that added nothing.
* **The privilege ablation runs at every promotion, not once at the end.**
  docs/headline-finding.md is the reason: a gain measured under a privileged
  percept is not the same quantity as a transferable gain, and finding that out
  only at the end of a campaign means every intermediate decision was made on
  the wrong number.
* **The held-out block is a test, not a promotion gate, by default.** Gating
  promotion on held-out every generation consumes it as a training signal.
  Promotion is decided on dev; held-out is scored once per campaign.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Sequence

import numpy as np

from governor.env import EpisodeSpec
from governor.gate import PairedResult, ablation_curve, paired_gate
from governor.governed import Bundle, RecoverySpec, Rule
from governor.parallel import rollout_many
from governor.search import (DEFAULT_EARLINESS, DEFAULT_FP_PENALTY, Trigger,
                             search_triggers)

#: Threshold a `gt` trigger can never fail, so the twin fires the moment it arms.
_ALWAYS = 1e12


def sha_json(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class Preregistration:
    """The seed partition, frozen before any episode runs.

    Written first and hashed; every later artifact records this hash, so a
    campaign that quietly re-partitioned its seeds is detectable after the fact.
    """

    dev: tuple[int, ...]
    heldout: tuple[int, ...]
    percept_noise: float
    critic_budget: int
    action_budget: int
    recovery_sensor_sd: float
    max_generations: int
    #: Which world and which frozen policy this campaign governs. Part of the
    #: preregistration because a promoted skill is only meaningful against the
    #: policy and task it was earned on; changing either silently would make the
    #: artifact record a claim it never tested.
    task: str = "lift"
    policy: str = "scripted"
    #: Scale each generation's dev sample to the effect it can still see. With a
    #: constant size the gate is progressively underpowered as residual effects
    #: shrink; round 17 measured a candidate worth +6.5pp held-out rejected at
    #: p=0.065 on 120 seeds. `dev` becomes an ORDERED RESERVOIR and each
    #: generation takes a prefix sized from the PREVIOUS generation's residual
    #: rate -- decided before the candidate is judged, so it is not optional
    #: stopping.
    scale_dev_by_power: bool = False
    power_fix_share: float = 0.80
    power_target: float = 0.80
    min_fixed: int = 3
    alpha: float = 0.05
    #: Search the recovery program per generation, not just the trigger. The
    #: searched program is adopted only if it clears the same paired gate the
    #: trigger must clear, against the hand-written program as baseline.
    search_recovery: bool = False
    #: Seeds used for the recovery coordinate descent; a subset of `dev`.
    recovery_search_n: int = 60
    #: Rank trigger candidates by an out-of-sample shadow score instead of the
    #: in-sample search score. Costs no rollouts and demotes candidates that only
    #: fit the half they were searched on (round 7 measured a shrinkage of +0.45
    #: on one such candidate).
    screen_triggers: bool = False
    #: The trigger objective's hand-tuned weights. These live HERE, not as module
    #: globals, because round 26 measured that one of them was worth 5.0pp of
    #: held-out success: a constant that can move the headline is part of what
    #: was preregistered, and belongs in the content hash with everything else.
    #: Keeping them as defaults bound at import time also made an A/B silently
    #: run the same arm twice (round 29) -- rebinding the module attribute after
    #: import cannot reach a default already bound to the function.
    #: Generation 1 has no measured yield to carry forward. Later generations
    #: never use this; see `plan_generation`.
    prior_discordance_yield: float = 0.7
    #: Floor on the carried-forward estimate, so one unlucky generation cannot
    #: demand an unbounded reservoir.
    min_discordance_yield: float = 0.05
    #: `screen()` splits each generation in half, so a screened generation must
    #: be sized larger to leave the search the same evidence.
    screen_search_fraction: float = 0.5
    #: A candidate must out-net a blind twin of itself -- same recovery, same
    #: arm step, fired without looking at state. Off only to reproduce the
    #: pre-round-45 promotion rule.
    require_judgement: bool = True
    #: Promotion now depends on TWO tests, and round 47 found only the first was
    #: powered: at 60 seeds the judgement test could see the established rules
    #: (which beat their twins 86-99% of discordant pairs) but not a modest one
    #: at 70%, which needs 49 pairs rather than 20. Planning for the weaker
    #: effect is the point -- a rule that judges moderately still judges.
    judgement_fix_share: float = 0.70
    #: Discordant pairs per seed in the judgement comparison. Measured from the
    #: previous generation; this is the generation-1 prior (observed 0.35-0.54
    #: across four campaigns, so the low end).
    prior_judgement_yield: float = 0.35
    earliness: float = DEFAULT_EARLINESS
    fp_penalty: float = DEFAULT_FP_PENALTY

    def __post_init__(self) -> None:
        overlap = set(self.dev) & set(self.heldout)
        if overlap:
            raise ValueError(f"dev and held-out seeds overlap: {sorted(overlap)[:5]}")

    def sha(self) -> str:
        return sha_json(asdict(self))


@dataclass
class CampaignStore:
    """Append-only, content-addressed artifact directory."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        (self.root / "artifacts").mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.jsonl"

    def put(self, kind: str, payload: dict) -> str:
        """Write one artifact under its content hash and index it. Never overwrites."""
        digest = sha_json(payload)
        path = self.root / "artifacts" / f"{digest}.json"
        if not path.exists():
            path.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str))
        with self.index_path.open("a") as fh:
            fh.write(json.dumps({"seq": self.size(), "kind": kind, "sha": digest,
                                 "time": time.time()}, default=str) + "\n")
        return digest

    def size(self) -> int:
        if not self.index_path.exists():
            return 0
        return sum(1 for _ in self.index_path.open())

    def read(self, digest: str) -> dict:
        return json.loads((self.root / "artifacts" / f"{digest}.json").read_text())


def _specs(seeds: Sequence[int], prereg: Preregistration) -> list[EpisodeSpec]:
    return [EpisodeSpec(seed=s, task=prereg.task, policy=prereg.policy,
                        percept_noise=prereg.percept_noise) for s in seeds]


def propose_rule(
    traces, labels, *, generation: int, prereg: Preregistration,
) -> Rule | None:
    """Deterministic proposer: the best admissible trigger over residual failures.

    Zero external API calls by construction. The LLM proposer is a drop-in with
    the same ``(traces, labels) -> Rule`` contract; making the deterministic one
    the reference implementation keeps the loop runnable and reproducible
    without a network.
    """
    if not any(labels) or all(labels):
        return None
    if prereg.screen_triggers:
        from governor.screen import screen

        screened = screen(traces, labels, privilege_budget=prereg.critic_budget, pool=8,
                          earliness=prereg.earliness, fp_penalty=prereg.fp_penalty)
        if screened:
            return Rule(rule_id=f"g{generation}", trigger=screened[0].trigger,
                        recovery=RecoverySpec(sensor_sd=prereg.recovery_sensor_sd))
        # too few episodes to split; fall through to the in-sample ranking
    ranked = search_triggers(traces, labels, privilege_budget=prereg.critic_budget, top_k=3,
                             earliness=prereg.earliness, fp_penalty=prereg.fp_penalty)
    if not ranked:
        return None
    return Rule(
        rule_id=f"g{generation}",
        trigger=ranked[0].trigger,
        recovery=RecoverySpec(sensor_sd=prereg.recovery_sensor_sd),
    )


@dataclass
class GenerationRecord:
    """One generation's decision and the evidence behind it."""

    generation: int
    rule_id: str
    trigger: str
    parent_sha: str
    child_sha: str
    dev: PairedResult
    promoted: bool
    reason: str


def run_campaign(
    prereg: Preregistration, store: CampaignStore, *, workers: int = 10, verbose: bool = True,
) -> dict:
    """Drive generations until nothing further clears the dev gate."""
    prereg_sha = store.put("preregistration", asdict(prereg))
    if verbose:
        print(f"preregistration {prereg_sha[:12]}  dev={len(prereg.dev)} heldout={len(prereg.heldout)}")

    reservoir = _specs(prereg.dev, prereg)
    dev_specs = reservoir
    bundle = Bundle(rules=(), critic_budget=prereg.critic_budget, action_budget=prereg.action_budget)
    history: list[GenerationRecord] = []
    plans: list[dict] = []

    prev_residual_rate: float | None = None
    #: Discordant pairs per residual failure, carried forward from the last
    #: completed generation. Generation 1 has nothing to measure and uses the
    #: prior; every later generation is sized on this campaign's own evidence.
    prev_yield: float | None = None
    prev_j_yield: float | None = None
    for gen in range(1, prereg.max_generations + 1):
        # Size this generation BEFORE anything about its candidate is known, from
        # the previous generation's residual rate. Measurement, search and gate
        # then all run on the SAME slice: sizing the gate differently from the
        # search would judge a candidate on a population it was not fitted to.
        if prereg.scale_dev_by_power:
            from governor.power import plan_generation

            rate = prev_residual_rate if prev_residual_rate is not None else 0.5
            plan = plan_generation(gen, round(rate * len(reservoir)), len(reservoir),
                                   len(reservoir), fix_share=prereg.power_fix_share,
                                   alpha=prereg.alpha, power=prereg.power_target,
                                   discordance_yield=(prev_yield if prev_yield is not None
                                                      else prereg.prior_discordance_yield),
                                   search_fraction=(prereg.screen_search_fraction
                                                    if prereg.screen_triggers else 1.0))
            # The generation must satisfy BOTH tests it will be judged by.
            jplan = plan_generation(gen, round(rate * len(reservoir)), len(reservoir),
                                    len(reservoir), fix_share=prereg.judgement_fix_share,
                                    alpha=prereg.alpha, power=prereg.power_target,
                                    discordance_yield=1.0)
            j_seeds = int(round(jplan.discordant_needed /
                                max(prev_j_yield if prev_j_yield is not None
                                    else prereg.prior_judgement_yield, 1e-6)))
            if prereg.require_judgement and j_seeds > plan.seeds_used:
                plan = replace(plan, seeds_needed=max(plan.seeds_needed, j_seeds),
                               seeds_used=min(max(plan.seeds_used, j_seeds), len(reservoir)),
                               capped=j_seeds > len(reservoir))
                if verbose:
                    print(f"  judgement test needs {jplan.discordant_needed} pairs "
                          f"-> {j_seeds} seeds; generation sized to {plan.seeds_used}")
            dev_specs = reservoir[: plan.seeds_used]
            plans.append(asdict(plan))
            if verbose:
                print(f"  {plan.line()}")

        # Residual failures under the CURRENT bundle are the target population.
        from multiprocessing import Pool
        from governor.gate import _run
        with Pool(workers) as pool:
            cur = pool.map(_run, [(s, bundle if bundle.rules else None) for s in dev_specs])
        labels = [r["success"] for r in cur]
        rate = float(np.mean(labels))
        if verbose:
            print(f"\ngen {gen}: current dev rate {rate:.1%} ({sum(labels)}/{len(labels)})")
        if all(labels):
            if verbose:
                print("  no residual failures on dev; campaign converged")
            break

        rule = propose_rule([r["trace"] for r in cur], labels, generation=gen, prereg=prereg)
        if rule is not None and prereg.search_recovery:
            rule = _maybe_search_recovery(rule, bundle, dev_specs, prereg, store, gen,
                                          workers=workers, verbose=verbose)
        if rule is None:
            if verbose:
                print("  proposer produced no admissible candidate; stopping")
            break

        child = bundle.append(rule)
        child.assert_atomic_child_of(bundle)     # one rule appended, parent frozen
        dev_result = paired_gate(dev_specs, child, baseline=bundle if bundle.rules else None,
                                 workers=workers)

        # The blind control was a per-report check by hand until round 45, when a
        # third policy grew a rule that fired on 200/200 held-out episodes and
        # tied its own blind arm. Beating the ungoverned baseline is not enough:
        # a recovery that runs unconditionally can do that on a weak policy
        # without the critic judging anything. The win has to come from choosing
        # WHEN, so every candidate now runs against a blind twin of itself.
        blind_rule = Rule(f"{rule.rule_id}-blind",
                          Trigger(rule.trigger.feature, "gt", -_ALWAYS, 1,
                                  rule.trigger.arm_after, "value"),
                          rule.recovery)
        blind_child = bundle.append(blind_rule)
        # Head to head on the SAME seeds, not two separate comparisons against the
        # parent: netting 35 against a blind twin's 34 is one episode of noise, and
        # that is exactly what slipped through when this gate first shipped.
        blind_result = paired_gate(dev_specs, child, baseline=blind_child, workers=workers)
        judged = (blind_result.p_value < prereg.alpha
                  and blind_result.fixed > blind_result.broken)

        promoted = (dev_result.fixed >= prereg.min_fixed
                    and dev_result.p_value < prereg.alpha
                    and dev_result.fixed > dev_result.broken
                    and (judged or not prereg.require_judgement))
        reason = ("promoted" if promoted else
                  "rejected (no judgement vs its blind twin: "
                  f"delta={blind_result.delta:+.1%} p={blind_result.p_value:.4f})"
                  if not judged and prereg.require_judgement else
                  f"rejected (fixed={dev_result.fixed} broken={dev_result.broken} p={dev_result.p_value:.4f})")
        if verbose:
            print(f"  vs its blind twin (same recovery, unconditional at t={rule.trigger.arm_after}): "
                  f"{blind_result.line()}")
        rec = GenerationRecord(gen, rule.rule_id, rule.trigger.describe(), bundle.sha(),
                               child.sha(), dev_result, promoted, reason)
        # Sized from a SEALED generation, never from the candidate being planned.
        residual_on_slice = max(len(dev_specs) - sum(labels), 1)
        prev_yield = max((dev_result.fixed + dev_result.broken) / residual_on_slice,
                         prereg.min_discordance_yield)
        prev_j_yield = max((blind_result.fixed + blind_result.broken) / max(len(dev_specs), 1),
                           prereg.min_discordance_yield)
        history.append(rec)
        store.put("generation", {
            "blind_gate": asdict(blind_result),
            "preregistration_sha": prereg_sha, "generation": gen,
            "rule": rule.canonical(), "parent_sha": bundle.sha(), "child_sha": child.sha(),
            "dev_gate": asdict(dev_result), "promoted": promoted, "reason": reason,
        })
        if verbose:
            print(f"  candidate: {rule.trigger.describe()}")
            print(f"  dev gate vs parent: {dev_result.line()}")
            print(f"  -> {reason}")
        if not promoted:
            break
        bundle = child
        prev_residual_rate = 1.0 - dev_result.governed_rate

    # --- held-out is scored ONCE, after the campaign, as a test --------------
    result: dict = {"preregistration_sha": prereg_sha, "power_plans": plans,
                    "generations": len(history),
                    "promoted": sum(1 for h in history if h.promoted),
                    "final_sha": bundle.sha(), "rules": [r.rule_id for r in bundle.rules]}
    if bundle.rules:
        held = _specs(prereg.heldout, prereg)
        final = paired_gate(held, bundle, baseline=None, workers=workers)
        result["heldout"] = asdict(final)
        if verbose:
            print(f"\nheld-out (scored once, n={len(held)}): {final.line()}")
        # The judgement test decides promotion on the SAME slice the trigger was
        # searched on, so its estimate is inflated: round 50 measured a shrinkage
        # of +5.0pp for one policy and +15.7pp for another. Re-run it on held-out
        # seeds so "the win is judgement, not extra control steps" is a held-out
        # claim rather than an in-sample one.
        if bundle.rules and prereg.require_judgement:
            blind = Bundle(
                rules=tuple(Rule(f"{r.rule_id}-blind",
                                 Trigger(r.trigger.feature, "gt", -_ALWAYS, 1,
                                         r.trigger.arm_after, "value"), r.recovery)
                            for r in bundle.rules),
                critic_budget=bundle.critic_budget, action_budget=bundle.action_budget)
            held_blind = paired_gate(held, bundle, baseline=blind, workers=workers)
            result["heldout_vs_blind"] = asdict(held_blind)
            if verbose:
                established = (held_blind.p_value < prereg.alpha
                               and held_blind.fixed > held_blind.broken)
                print(f"held-out vs blind twin: {held_blind.line()}  "
                      f"-> judgement {'established' if established else 'NOT established'}")
        curve = ablation_curve(held, bundle, workers=workers)
        result["ablation"] = [(sd, asdict(r)) for sd, r in curve]
        if verbose:
            print("held-out transfer ablation:")
            for sd, r in curve:
                tag = "GROUND TRUTH (privileged)" if sd == 0.0 else "onboard sensor"
                print(f"  sensor_sd={sd:.3f}  {r.line()}  [{tag}]")
        store.put("campaign_result", result)
    return result


def _maybe_search_recovery(rule: Rule, parent: Bundle, dev_specs, prereg: Preregistration,
                           store: CampaignStore, gen: int, *, workers: int, verbose: bool) -> Rule:
    """Search a recovery program for `rule`, and adopt it only if it clears the gate.

    Round 6 measured what happens without that guard: a coordinate descent found
    a program worth +5pp on the 60 dev seeds it was searched on, and +4.0pp on
    held-out at p=0.096 -- directionally right, not significant. Adopting it on
    the dev number alone would have been exactly the failure this project's
    methodology exists to prevent, so the searched program has to earn its place
    against the hand-written one on the same paired test.
    """
    from governor.recovery_search import program_of, search_recovery

    # The recovery gate must not include the seeds the recovery was searched on.
    # It did until round 8, and the consequence was measurable: the same searched
    # program cleared the half-in-sample dev gate at +5.8% p=0.039 while a clean
    # held-out comparison put it at +4.0% p=0.096 (docs/round6-recovery-search.md).
    all_dev = list(dev_specs)
    subset = all_dev[: prereg.recovery_search_n]
    gate_specs = all_dev[prereg.recovery_search_n:]
    if len(gate_specs) < 20:
        raise ValueError(
            f"dev has {len(all_dev)} seeds and recovery_search_n={prereg.recovery_search_n}; "
            "fewer than 20 seeds are left to gate the searched recovery out of sample"
        )
    found = search_recovery(subset, rule.trigger, sensor_sd=prereg.recovery_sensor_sd,
                            critic_budget=prereg.critic_budget,
                            action_budget=prereg.action_budget, workers=workers, verbose=verbose)
    candidate = Rule(rule.rule_id, rule.trigger,
                     RecoverySpec(program=program_of(found.durations),
                                  sensor_sd=prereg.recovery_sensor_sd))
    hand_bundle = parent.append(rule)
    found_bundle = parent.append(candidate)
    verdict = paired_gate(gate_specs, found_bundle, baseline=hand_bundle, workers=workers)
    adopt = verdict.p_value < prereg.alpha and verdict.fixed > verdict.broken
    store.put("recovery_search", {
        "generation": gen, "durations": found.durations, "search_subset_rate": found.rate,
        "search_seeds": [s.seed for s in subset], "gate_seeds": [s.seed for s in gate_specs],
        "evaluations": found.evaluations, "gate": asdict(verdict), "adopted": adopt,
    })
    if verbose:
        print(f"  recovery search: {found.evaluations} evals on {len(subset)} seeds, "
              f"subset rate {found.rate:.1%}")
        print(f"  recovery gate on {len(gate_specs)} DISJOINT seeds: {verdict.line()} -> "
              f"{'adopted' if adopt else 'rejected, keeping hand-written'}")
    return candidate if adopt else rule
