#!/usr/bin/env python3
"""Migration-parity check: kernel-path rerun vs. an archived campaign store.

GOAL.md acceptance #3: rerunning an archived, pre-kernel-seam campaign through
the L0 kernel path (`plugins.rsi.workload.run`, which stamps the resolved
`embodiment.env` / `policy.driver` refs onto the preregistration before
calling `governor.campaign.run_campaign`) must reproduce the archive's
per-generation rule canonicals, dev-gate numbers, promotion decisions and
held-out numbers bit for bit. That is only meaningful if the two providers are
byte-identical adapters over the original code -- which is what
plugins/embodiment_robosuite and plugins/policies are -- and this script is
the check that catches it if they ever stop being that.

Usage::

    python scripts/parity_check.py runs/campaign-pj-scripted

Reruns the archived campaign through the kernel path into
``runs/campaign-pj-scripted-parity`` (or ``--rerun-dir``), prints one
PASS/FAIL line per compared field group, and exits nonzero on any mismatch.

This module is deliberately split into pure comparison/reconstruction
functions (unit-tested against small hand-built store fixtures, no simulator
involved) and `run_parity_check`, the impure driver that actually mounts a
kernel and runs a campaign. Only the orchestrator invokes the latter, against
real archived campaigns -- see tests/test_rsi_workload.py's parity-check
section for what is covered without a simulator.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

# Runnable as `python scripts/parity_check.py ...` without PYTHONPATH gymnastics.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from governor.campaign import Preregistration

#: Preregistration fields whose canonical (json-round-tripped) form is a list
#: but whose dataclass field is a tuple; every other field is json-stable.
_TUPLE_FIELDS = ("dev", "heldout")

#: PairedResult fields compared bit for bit between archive and rerun, for the
#: dev gate, the blind-twin judgement gate, and the held-out result alike.
#: `declared_privilege` and `fires` are deliberately excluded as bookkeeping;
#: everything that carries the statistical claim is in.
_PAIRED_FIELDS = ("n", "base_rate", "governed_rate", "fixed", "broken", "p_value")


@dataclasses.dataclass(frozen=True, slots=True)
class FieldGroupResult:
    """One PASS/FAIL line: a named comparison and whether it held."""

    name: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        return f"[{status}] {self.name}" + (f": {self.detail}" if self.detail else "")


def read_store_artifacts(store_dir: str | Path) -> dict[str, list[dict]]:
    """Every artifact in a `governor.campaign.CampaignStore` directory, by kind, in index order.

    A plain reader over the same index.jsonl + content-addressed
    artifacts/*.json shape `CampaignStore` writes, rather than a `CampaignStore`
    method: a parity check reads an ARCHIVED store this process never wrote to,
    old or new, and has no writer's lock on either.
    """
    store_dir = Path(store_dir)
    index_path = store_dir / "index.jsonl"
    if not index_path.exists():
        raise FileNotFoundError(f"no campaign store at {store_dir} (missing index.jsonl)")
    by_kind: dict[str, list[dict]] = {}
    with index_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            artifact_path = store_dir / "artifacts" / f"{row['sha']}.json"
            payload = json.loads(artifact_path.read_text())
            by_kind.setdefault(row["kind"], []).append(payload)
    return by_kind


def rebuild_preregistration(payload: dict[str, Any]) -> Preregistration:
    """Reconstruct a `Preregistration` from its archived canonical dict.

    Drops any key the current dataclass no longer declares -- an archive from
    an earlier round of this project may predate a field since removed -- and
    restores `dev` / `heldout` to tuples: `asdict` (and the json round-trip
    through the store) turns them into lists, but `Preregistration` expects
    tuples both for `__post_init__`'s overlap check and for `.sha()` to match
    what a freshly constructed `Preregistration` would hash to.

    Deliberately does NOT set `env_provider` / `policy_provider`: threading
    the kernel-resolved refs onto the rebuilt preregistration is
    `plugins.rsi.workload.run`'s job, not this reconstruction's.
    """
    known = {f.name for f in dataclasses.fields(Preregistration)}
    kwargs = {k: v for k, v in payload.items() if k in known}
    dropped = sorted(set(payload) - known)
    filled = sorted(known - set(payload))
    if dropped:
        print(f"[parity] archive keys the dataclass no longer declares, DROPPED: {dropped}",
              file=sys.stderr)
    if filled:
        # The dangerous direction: an old archive silently inherits today's
        # defaults. Every filled-in field is named so the operator can judge
        # whether the rerun is still the same experiment.
        print(f"[parity] dataclass fields the archive predates, FILLED WITH TODAY'S "
              f"DEFAULTS: {filled}", file=sys.stderr)
    for field_name in _TUPLE_FIELDS:
        if field_name in kwargs:
            kwargs[field_name] = tuple(kwargs[field_name])
    return Preregistration(**kwargs)


def _held_out_delta(paired: dict[str, Any]) -> float:
    """`PairedResult.delta` is a computed property, so it never round-trips through `asdict`."""
    return paired["governed_rate"] - paired["base_rate"]


def compare_generations(
    archived: list[dict], rerun: list[dict],
) -> tuple[FieldGroupResult, FieldGroupResult, FieldGroupResult]:
    """Compare per-generation rule canonicals, dev-gate numbers, and promotion decisions.

    Generations are compared in store order, which is generation order (both
    `run_campaign` and this reader are append-only / index-order). A
    generation-count mismatch is itself reported as a failure of every group
    rather than raising, so a parity break is one more line in the same
    PASS/FAIL report instead of a traceback.
    """
    if len(archived) != len(rerun):
        detail = f"generation count differs: archived={len(archived)} rerun={len(rerun)}"
        return (FieldGroupResult("per-generation rule canonicals", False, detail),
                FieldGroupResult("dev gate (fixed/broken/p_value/n)", False, detail),
                FieldGroupResult("promotion decisions", False, detail))

    rule_mismatches: list[int] = []
    gate_mismatches: list[int] = []
    promo_mismatches: list[int] = []
    for old, new in zip(archived, rerun):
        gen = old.get("generation")
        # child_sha is the strongest single check: the content hash of the
        # bundle the generation produced. parent_sha anchors the chain.
        if (old["rule"] != new["rule"]
                or old.get("child_sha") != new.get("child_sha")
                or old.get("parent_sha") != new.get("parent_sha")):
            rule_mismatches.append(gen)
        gates = [("dev_gate", True), ("blind_gate", False)]
        for key, required in gates:
            a, b = old.get(key), new.get(key)
            if a is None and b is None and not required:
                continue
            if (a is None) != (b is None) or (a is not None and any(
                    a[k] != b[k] for k in _PAIRED_FIELDS)):
                gate_mismatches.append(gen)
                break
        if old["promoted"] != new["promoted"]:
            promo_mismatches.append(gen)

    return (
        FieldGroupResult("per-generation rule canonicals + bundle shas", not rule_mismatches,
                         f"generations {rule_mismatches} differ" if rule_mismatches else ""),
        FieldGroupResult("dev + blind gates (n/base/governed/fixed/broken/p)", not gate_mismatches,
                         f"generations {gate_mismatches} differ" if gate_mismatches else ""),
        FieldGroupResult("promotion decisions", not promo_mismatches,
                         f"generations {promo_mismatches} differ" if promo_mismatches else ""),
    )


def compare_heldout(old: dict[str, Any] | None, new: dict[str, Any] | None) -> FieldGroupResult:
    """Compare the full held-out paired result between archive and rerun."""
    name = "held-out (n/base/governed/fixed/broken/p)"
    if old is None or new is None:
        if old is None and new is None:
            return FieldGroupResult(name, True, "neither run scored a held-out block")
        return FieldGroupResult(name, False, "one run scored held-out and the other did not")
    mismatches = [k for k in _PAIRED_FIELDS if old.get(k) != new.get(k)]
    if _held_out_delta(old) != _held_out_delta(new) and "governed_rate" not in mismatches:
        mismatches.append("delta")
    return FieldGroupResult(name, not mismatches, f"{mismatches} differ" if mismatches else "")


def run_parity_check(
    archived_dir: str | Path, rerun_store_root: str | Path, *,
    workers: int = 10, verbose: bool = True,
) -> list[FieldGroupResult]:
    """Rebuild the archived preregistration, rerun it through the kernel path, compare.

    Not covered by this project's own test suite -- it drives a real campaign
    (`plugins.rsi.workload.run` -> `governor.campaign.run_campaign` -> robosuite
    rollouts across every dev/held-out seed), which is exactly the heavy
    simulation unit tests must not run. `read_store_artifacts`,
    `rebuild_preregistration`, `compare_generations` and `compare_heldout`
    above are the pure parts, and those ARE unit-tested, against small
    hand-built store fixtures.
    """
    from harness.config import resolve_plan
    from harness.definitions import CAPABILITIES
    from harness.kernel import Kernel
    from plugins.rsi.workload import run as rsi_run
    from profiles import base_profile

    archived = read_store_artifacts(archived_dir)
    prereg_payloads = archived.get("preregistration", [])
    if not prereg_payloads:
        raise ValueError(f"{archived_dir} has no preregistration artifact")
    if "generation" not in archived and "campaign_result" not in archived:
        raise ValueError(
            f"{archived_dir} is not a run_campaign store (kinds: {sorted(archived)}); "
            "beam and other store shapes have no parity protocol here")
    prereg = rebuild_preregistration(prereg_payloads[0])

    rerun_root = Path(rerun_store_root)
    if (rerun_root / "index.jsonl").exists():
        raise FileExistsError(
            f"{rerun_root} already holds a campaign store; CampaignStore is "
            "append-only, so rerunning into it would interleave two campaigns. "
            "Pass a fresh --rerun-dir or delete the old one deliberately.")

    # base_profile() mounts embodiment_robosuite + policies + graphs + the
    # local-pool executor -- the L0 reference set (profiles/__init__.py).
    # The kernel logs to the rerun dir so mounts and resolutions are audit
    # facts of this parity run, not silent.
    from harness.events import SessionLog
    kernel = Kernel(CAPABILITIES, log=SessionLog(rerun_root / "session-log"))
    kernel.mount(resolve_plan(base_profile()))

    rsi_run(prereg, rerun_store_root, kernel, workers=workers, verbose=verbose)
    rerun = read_store_artifacts(rerun_store_root)

    gen_results = compare_generations(archived.get("generation", []), rerun.get("generation", []))
    old_result = archived.get("campaign_result", [None])[0]
    new_result = rerun.get("campaign_result", [None])[0]
    heldout_result = compare_heldout(
        old_result.get("heldout") if old_result else None,
        new_result.get("heldout") if new_result else None,
    )
    return [*gen_results, heldout_result]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("archived_dir", type=Path,
                        help="stored campaign dir, e.g. runs/campaign-pj-scripted")
    parser.add_argument("--rerun-dir", type=Path, default=None,
                        help="where to write the kernel-path rerun "
                             "(default: <archived_dir>-parity)")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--quiet", action="store_true", help="suppress run_campaign's own log")
    args = parser.parse_args(argv)

    rerun_dir = args.rerun_dir or args.archived_dir.parent / f"{args.archived_dir.name}-parity"
    results = run_parity_check(args.archived_dir, rerun_dir, workers=args.workers,
                               verbose=not args.quiet)

    for result in results:
        print(result.line())
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
