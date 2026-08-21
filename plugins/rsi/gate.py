"""Paired gating and the transfer-ablation curve.

Statistics
----------
Both arms run the same seeds through the same code path, so the comparison is
paired and the right test is exact McNemar on the discordant pairs (b01 fixed,
b10 broken) -- a two-sided binomial test against p=0.5. Under the bitwise
determinism verified in docs/verified-environment.md the only source of
difference between arms is the governance itself.

Transfer ablation
-----------------
`ablation_curve` re-runs the SAME bundle with the recovery's percept degraded
from ground truth to onboard-sensor quality. It is the number this project
exists to produce: docs/headline-finding.md records a +50pp gain collapsing to
+13.3pp (n.s.) once the recovery could no longer read the simulator. Reporting a
gain without its curve is reporting an artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import comb
from typing import Sequence

import numpy as np

from governor.env import EpisodeSpec
from governor.governed import Bundle, governed_rollout


@dataclass(frozen=True, slots=True)
class PairedResult:
    """One paired arm comparison."""

    n: int
    base_rate: float
    governed_rate: float
    fixed: int          # b01: failed alone, succeeded under governance
    broken: int         # b10: succeeded alone, failed under governance
    fires: int
    p_value: float
    declared_privilege: int

    @property
    def delta(self) -> float:
        return self.governed_rate - self.base_rate

    def line(self) -> str:
        return (f"n={self.n:3d} base={self.base_rate:6.1%} governed={self.governed_rate:6.1%} "
                f"delta={self.delta:+6.1%} fixed={self.fixed:3d} broken={self.broken:3d} "
                f"fires={self.fires:3d} p={self.p_value:.5f} priv={self.declared_privilege}")


def exact_mcnemar(fixed: int, broken: int) -> float:
    """Two-sided exact McNemar p-value on the discordant pairs."""
    n = fixed + broken
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(min(fixed, broken) + 1))
    return min(1.0, 2.0 * tail / 2**n)


def _run(job):
    spec, bundle = job
    return governed_rollout(spec, bundle)


def paired_gate(
    specs: Sequence[EpisodeSpec], bundle: Bundle,
    baseline: Bundle | None = None, workers: int = 10, executor=None,
) -> PairedResult:
    """Run both arms on identical seeds and test the discordant pairs.

    `baseline=None` measures the bundle against the ungoverned frozen policy.
    Passing the PARENT bundle instead is what makes a generation's gain
    attributable to the single rule it appended -- the atomic-increment
    discipline is only meaningful if the comparison is against the parent.
    """
    from governor.parallel import default_executor

    jobs = [(s, baseline) for s in specs] + [(s, bundle) for s in specs]
    out = (executor or default_executor()).map(_run, jobs, workers=workers)
    n = len(specs)
    base = np.array([r["success"] for r in out[:n]], dtype=bool)
    gov = np.array([r["success"] for r in out[n:]], dtype=bool)
    fires = sum(1 for r in out[n:] if r["fired_at"] is not None)
    fixed = int(((~base) & gov).sum())
    broken = int((base & (~gov)).sum())
    return PairedResult(
        n=n, base_rate=float(base.mean()), governed_rate=float(gov.mean()),
        fixed=fixed, broken=broken, fires=fires,
        p_value=exact_mcnemar(fixed, broken),
        declared_privilege=bundle.declared_privilege(),
    )


def ablation_curve(
    specs: Sequence[EpisodeSpec], bundle: Bundle,
    sensor_sds: Sequence[float] = (0.0, 0.010, 0.020, 0.030), workers: int = 10,
    executor=None,
) -> list[tuple[float, PairedResult]]:
    """The transfer curve: same bundle, recovery percept degraded rung by rung."""
    rows = []
    for sd in sensor_sds:
        # degrade EVERY rule's percept: a chain is only as transferable as its
        # most privileged recovery, so ablating one rule would understate the gap.
        degraded = replace(bundle, rules=tuple(
            replace(r, recovery=replace(r.recovery, sensor_sd=sd)) for r in bundle.rules
        ))
        rows.append((sd, paired_gate(specs, degraded, baseline=None, workers=workers,
                                     executor=executor)))
    return rows
