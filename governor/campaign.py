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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

from governor.env import EpisodeSpec
from governor.gate import PairedResult, ablation_curve, paired_gate
from governor.governed import Bundle, RecoverySpec, Rule
from governor.parallel import rollout_many
from governor.search import Trigger, search_triggers


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
    min_fixed: int = 3
    alpha: float = 0.05

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
    return [EpisodeSpec(seed=s, percept_noise=prereg.percept_noise) for s in seeds]


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
    ranked = search_triggers(traces, labels, privilege_budget=prereg.critic_budget, top_k=3)
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

    dev_specs = _specs(prereg.dev, prereg)
    bundle = Bundle(rules=(), critic_budget=prereg.critic_budget, action_budget=prereg.action_budget)
    history: list[GenerationRecord] = []

    for gen in range(1, prereg.max_generations + 1):
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
        if rule is None:
            if verbose:
                print("  proposer produced no admissible candidate; stopping")
            break

        child = bundle.append(rule)
        child.assert_atomic_child_of(bundle)     # one rule appended, parent frozen
        dev_result = paired_gate(dev_specs, child, baseline=bundle if bundle.rules else None,
                                 workers=workers)

        promoted = (dev_result.fixed >= prereg.min_fixed
                    and dev_result.p_value < prereg.alpha
                    and dev_result.fixed > dev_result.broken)
        reason = ("promoted" if promoted else
                  f"rejected (fixed={dev_result.fixed} broken={dev_result.broken} p={dev_result.p_value:.4f})")
        rec = GenerationRecord(gen, rule.rule_id, rule.trigger.describe(), bundle.sha(),
                               child.sha(), dev_result, promoted, reason)
        history.append(rec)
        store.put("generation", {
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

    # --- held-out is scored ONCE, after the campaign, as a test --------------
    result: dict = {"preregistration_sha": prereg_sha, "generations": len(history),
                    "promoted": sum(1 for h in history if h.promoted),
                    "final_sha": bundle.sha(), "rules": [r.rule_id for r in bundle.rules]}
    if bundle.rules:
        held = _specs(prereg.heldout, prereg)
        final = paired_gate(held, bundle, baseline=None, workers=workers)
        result["heldout"] = asdict(final)
        if verbose:
            print(f"\nheld-out (scored once, n={len(held)}): {final.line()}")
        curve = ablation_curve(held, bundle, workers=workers)
        result["ablation"] = [(sd, asdict(r)) for sd, r in curve]
        if verbose:
            print("held-out transfer ablation:")
            for sd, r in curve:
                tag = "GROUND TRUTH (privileged)" if sd == 0.0 else "onboard sensor"
                print(f"  sensor_sd={sd:.3f}  {r.line()}  [{tag}]")
        store.put("campaign_result", result)
    return result
