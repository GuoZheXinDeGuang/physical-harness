"""Offline audit and shadow replay over the durable episode log.

Two jobs, both of which only a persisted log can do.

`audit_episode` re-derives every decision view from the stored columns and
recomputes the episode's chained commitment. Online, the invariant in
`governor.invariant` checks each dispatch as it happens; this is the after-the-
fact half -- it proves the record on disk is the record that was acted on, so a
result can be re-examined months later without trusting the process that made it.

`shadow_replay` evaluates a CANDIDATE trigger against already-recorded episodes
without touching the simulator. This is Zetta's shadow-replay stage
(Zetta-Embodiment/zetta/evolution/shadow_replay.py): it screens candidates and
measures false positives offline, so only survivors cost real rollouts. The
prediction is exact for the FIRST fire on an ungoverned trace -- after a
recovery runs, the trajectory diverges and the recording no longer describes it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from harness.episode_log import EpisodeLog, chain_start, chain_step
from harness.percept import FeatureView
from plugins.rsi.stats.search import Trigger


@dataclass(frozen=True, slots=True)
class AuditResult:
    episodes: int
    verified: int
    broken: list[str]

    @property
    def ok(self) -> bool:
        return not self.broken

    def line(self) -> str:
        return (f"audited {self.episodes} episodes, chain verified {self.verified}, "
                f"broken {len(self.broken)}")


def rebuild_views(block: dict, seed: int) -> list[FeatureView]:
    """Reconstruct the exact per-step views a critic was handed."""
    names = block["names"]
    n = block["n"]
    cols = block["columns"]
    return [
        FeatureView({name: cols[name][i] for name in names}, step=i, episode=f"s{seed}")
        for i in range(n)
    ]


def audit_episode(log: EpisodeLog, record: dict) -> str | None:
    """Recompute the chain from storage. Returns an error string, or None if sound.

    Two independent layers catch corruption: the block's own content hash (a
    storage-integrity check) and the chained view commitment (a semantic check
    that the values are the ones acted on). A failure at either layer is
    reported rather than raised, so one bad block cannot abort an audit.
    """
    try:
        block = log.get_block(record["block"])
    except (ValueError, OSError) as exc:
        return f"seed {record['seed']}: block unreadable or corrupt: {exc}"
    chain = chain_start()
    for view in rebuild_views(block, record["seed"]):
        chain = chain_step(chain, view.digest())
    if chain != record["chain"]:
        return (f"seed {record['seed']}: chain mismatch "
                f"(stored {record['chain'][:12]}, re-derived {chain[:12]})")
    return None


def audit_log(log: EpisodeLog) -> AuditResult:
    """Verify every episode in the log reconstructs to its recorded commitment."""
    broken: list[str] = []
    records = [r for r in log.episodes() if "chain" in r and "block" in r]
    for record in records:
        err = audit_episode(log, record)
        if err:
            broken.append(err)
    return AuditResult(len(records), len(records) - len(broken), broken)


@dataclass(frozen=True, slots=True)
class ShadowResult:
    """What a candidate WOULD have done on recorded episodes."""

    trigger: Trigger
    n: int
    fires_on_failures: int
    fires_on_successes: int
    n_failures: int
    n_successes: int
    median_fire: float | None

    @property
    def recall(self) -> float:
        return self.fires_on_failures / max(self.n_failures, 1)

    @property
    def false_positive(self) -> float:
        return self.fires_on_successes / max(self.n_successes, 1)

    def line(self) -> str:
        med = f"{self.median_fire:.0f}" if self.median_fire is not None else "-"
        return (f"recall={self.recall:.2f} fp={self.false_positive:.2f} "
                f"median_fire={med}  {self.trigger.describe()}")


def shadow_replay(log: EpisodeLog, trigger: Trigger, bundle_sha: str | None = None) -> ShadowResult:
    """Score a candidate against recorded episodes, without running the simulator."""
    records = [r for r in log.episodes() if "block" in r and "success" in r
               and (bundle_sha is None or r["bundle_sha"] == bundle_sha)]
    fires_f = fires_s = n_f = n_s = 0
    steps: list[int] = []
    for record in records:
        block = log.get_block(record["block"])
        series = np.asarray(block["columns"][trigger.feature])
        fire = trigger.fire_step({trigger.feature: series})
        if record["success"]:
            n_s += 1
            fires_s += fire is not None
        else:
            n_f += 1
            fires_f += fire is not None
            if fire is not None:
                steps.append(fire)
    return ShadowResult(trigger, len(records), fires_f, fires_s, n_f, n_s,
                        float(np.median(steps)) if steps else None)
