#!/usr/bin/env python3
"""Round 81 (M1): rerun round 25's search vs LLM comparison on a fresh block.

Single-generation A/B/C, standalone by design: run_campaign still hardwires the
deterministic propose_rule, and wiring reasoner.proposer into the campaign loop
is M2/M3 work. This script reuses the campaign's own pieces -- _specs, gate._run,
paired_gate, Bundle, CampaignStore -- so every number comes off the exact code
path the sealed campaigns used, while touching none of it.

Three arms, one shared dev rollout, one held-out paired gate each:
  search      the deterministic reference proposer (round 26's calibration in)
  naive_mock  round 25's brief-reading picker, the zero-GPU anchor
  qwen38      the local sglang endpoint; skipped with a message when down, so
              the other two arms still produce an artifact offline

    PYTHONPATH=. .venv/bin/python scripts/round25_rerun.py

Seed blocks are drawn from the 44000+ reserve (STATUS ledger) and are
burn-once. Every proposer exchange and the final comparison land in the
content-addressed store; rejections are kept as evidence, not noise.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from governor.proposer import (
    LlmProposer,
    SearchProposer,
    naive_transport,
    qwen38_transport,
)
from harness.config import resolve_plan
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness.kernel import Kernel
from plugins.rsi import gate
from plugins.rsi.campaign import CampaignStore, Preregistration, _specs
from plugins.rsi.governed import Bundle
from plugins.rsi.repertoire import names as strategy_names
from profiles import base_profile

CONSUMER = "round25_rerun"


def rerun_prereg() -> Preregistration:
    """The preregistered comparison block: Lift, same arena as rounds 25/26.

    max_generations=1 documents intent -- this script runs exactly one
    proposal round per arm and never loops. Provider refs are stamped from
    the resolved mount plan at run time, as the workload does.
    """
    return Preregistration(
        dev=tuple(range(44000, 44060)), heldout=tuple(range(44200, 44400)),
        percept_noise=0.02, task="lift", policy="scripted",
        critic_budget=0, action_budget=0, recovery_sensor_sd=0.02,
        max_generations=1,
    )


def qwen38_reachable(timeout: float = 3.0) -> bool:
    """One GET against /models decides whether the qwen38 arm runs at all."""
    base = os.environ.get("QWEN38_BASE_URL", "http://localhost:30000/v1").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/models", timeout=timeout):
            return True
    except OSError:
        return False


def run_rerun(prereg: Preregistration, out: Path, kernel: Kernel, *,
              workers: int = 10, draws: int = 1) -> dict:
    """One dev rollout, one proposal per arm, one held-out gate per rule."""
    # Resolve through the kernel so the audit trail records the dependency,
    # then stamp the provider refs onto the prereg exactly as workload.run does.
    kernel.resolve("embodiment.env", consumer=CONSUMER)
    kernel.resolve("policy.driver", consumer=CONSUMER)
    kernel.resolve("percept.model", consumer=CONSUMER)
    executor = kernel.resolve("exec.rollouts", consumer=CONSUMER)
    prereg = dataclasses.replace(
        prereg,
        env_provider=kernel.provider_ref("embodiment.env"),
        policy_provider=kernel.provider_ref("policy.driver"),
        percept_provider=kernel.provider_ref("percept.model"),
    )
    store = CampaignStore(Path(out))
    store.put("preregistration", dataclasses.asdict(prereg))

    def record(payload: dict) -> None:
        store.put(payload["kind"], payload)

    # Risk guard (round 81): parse_proposal rejects any recovery the brief did
    # not offer, so an empty vocabulary would make the LLM arms refuse 100% of
    # proposals -- a silent null result mistaken for "model can't propose".
    strategies = tuple(strategy_names())
    if not strategies:
        raise RuntimeError("plugins.rsi.repertoire offers no strategies; "
                           "the LLM arms would refuse every proposal")

    dev_specs = _specs(prereg.dev, prereg)
    print(f"dev rollout: {len(dev_specs)} ungoverned episodes on {prereg.task}")
    cur = executor.map(gate._run, [(s, None) for s in dev_specs], workers=workers)
    traces = [r["trace"] for r in cur]
    labels = [r["success"] for r in cur]
    print(f"  dev rate {sum(labels)}/{len(labels)}")

    proposers: dict[str, SearchProposer | LlmProposer] = {
        "search": SearchProposer(),
        "naive_mock": LlmProposer(transport=naive_transport(record=record),
                                  name="naive_mock"),
    }
    qwen38_up = qwen38_reachable()
    if qwen38_up:
        proposers["qwen38"] = LlmProposer(
            transport=qwen38_transport(record=record, temperature=0.0, seed=0),
            name="qwen38")
    else:
        print("qwen38 endpoint unreachable (launch ~/models/launch_qwen38.sh); "
              "skipping that arm -- search + naive_mock still run")

    heldout_specs = _specs(prereg.heldout, prereg)
    arms: dict[str, dict] = {}
    for name, proposer in proposers.items():
        rule = proposer.propose(traces, labels, generation=1,
                                privilege_budget=prereg.critic_budget,
                                recovery_sensor_sd=prereg.recovery_sensor_sd,
                                strategies=strategies)
        heldout = None
        if rule is not None:
            child = Bundle(rules=(), critic_budget=prereg.critic_budget,
                           action_budget=prereg.action_budget).append(rule)
            res = gate.paired_gate(heldout_specs, child, baseline=None,
                                   workers=workers, executor=executor)
            heldout = {**dataclasses.asdict(res), "delta": res.delta}
            print(f"{name}: {rule.trigger.describe()}\n  held-out {res.line()}")
        else:
            print(f"{name}: no admissible rule "
                  f"({len(getattr(proposer, 'rejections', ()))} rejections)")
        arms[name] = {
            "identity": proposer.identity,
            "rule": rule.canonical() if rule is not None else None,
            "rejections": list(getattr(proposer, "rejections", ())),
            "heldout": heldout,
        }

    summary = {
        "arena": prereg.task,
        "dev_block": [min(prereg.dev), max(prereg.dev) + 1],
        "heldout_block": [min(prereg.heldout), max(prereg.heldout) + 1],
        "arms": arms,
    }
    digest = store.put("round25_rerun", summary)
    print(f"round25_rerun artifact {digest[:12]} -> {out}")

    if qwen38_up and draws > 1:
        store.put("qwen38_draws", _qwen38_draws(
            traces, labels, prereg, strategies, record, draws))
    return summary


def _qwen38_draws(traces, labels, prereg: Preregistration, strategies, record,
                  draws: int) -> dict:
    """Informational variance sidebar, never a gate (round 25's ruling).

    K independent single-attempt draws with distinct seeds; every raw response
    lands in the store via `record`. No extra episode burn -- held-out scoring
    is deterministic per fixed Rule, so only proposal variance is measured.
    """
    # ponytail: sequential draws, trivially under sglang's
    # --max-running-requests 2 cap; parallelise to 2 if K ever gets large.
    counts: Counter[str] = Counter()
    for i in range(1, draws + 1):
        proposer = LlmProposer(transport=qwen38_transport(record=record, seed=i),
                               name=f"qwen38_seed{i}", attempts=1)
        rule = proposer.propose(traces, labels, generation=1,
                                privilege_budget=prereg.critic_budget,
                                recovery_sensor_sd=prereg.recovery_sensor_sd,
                                strategies=strategies)
        if rule is not None:
            counts[json.dumps(rule.canonical(), sort_keys=True)] += 1
    valid = sum(counts.values())
    modal = counts.most_common(1)[0] if counts else (None, 0)
    print(f"qwen38 draws: {valid}/{draws} valid, modal rule x{modal[1]}")
    return {"draws": draws, "valid": valid, "refused": draws - valid,
            "modal_rule": json.loads(modal[0]) if modal[0] else None,
            "modal_count": modal[1]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("runs/round25-rerun"),
                        help="campaign store output (default runs/round25-rerun)")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--draws", type=int, default=1,
                        help="extra qwen38 proposal draws for the variance sidebar")
    args = parser.parse_args()
    if (args.out / "index.jsonl").exists():
        print(f"{args.out} already holds a campaign store; pass a fresh --out",
              file=sys.stderr)
        return 2

    plan = resolve_plan(base_profile())
    kernel = Kernel(CAPABILITIES, log=SessionLog(args.out / "session-log"))
    kernel.mount(plan)
    print(f"mount plan sha {plan.sha()[:12]}  "
          f"({len(plan.mounts)} capabilities mounted)\n")

    run_rerun(rerun_prereg(), args.out, kernel,
              workers=args.workers, draws=args.draws)
    return 0


if __name__ == "__main__":
    sys.exit(main())
