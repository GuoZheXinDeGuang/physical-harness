"""Reconstruct a sealed CampaignStore back into its dataclasses.

These pure functions read the same ``index.jsonl`` + content-addressed
``artifacts/*.json`` shape ``plugins.rsi.campaign.CampaignStore`` writes and
turn it back into a :class:`~plugins.rsi.campaign.Preregistration` and the
promoted :class:`~plugins.rsi.governed.Bundle`, asserting the rebuilt bundle
against the sealed ``child_sha`` per generation so a wrong reconstruction can
never be rescored/reseeded.

They lived in ``scripts/parity_check.py`` and ``scripts/rescore_heldout.py``
until round 90 rung 3 needed them from ``plugins.rsi.campaign.run_campaign``
(to seed a place campaign from its sealed parent). A plugin may not import
``scripts`` (tests/test_boundaries.py), and the reconstruction is genuinely
plugins-layer logic about the campaign data model, so it lives here and the
scripts re-export it -- one implementation, not two.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

from harness.stages import Clause, StageSpec
from plugins.rsi.campaign import Preregistration
from plugins.rsi.governed import Bundle, RecoverySpec, Rule
from plugins.rsi.stats.search import Trigger

#: Preregistration fields whose canonical (json-round-tripped) form is a list
#: but whose dataclass field is a tuple; every other field is json-stable.
_TUPLE_FIELDS = ("dev", "heldout")


def read_store_artifacts(store_dir: str | Path) -> dict[str, list[dict]]:
    """Every artifact in a `CampaignStore` directory, by kind, in index order.

    A plain reader over the same index.jsonl + content-addressed
    artifacts/*.json shape `CampaignStore` writes, rather than a `CampaignStore`
    method: callers read an ARCHIVED store this process never wrote to, old or
    new, and have no writer's lock on either.
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
    # A staged archive (R2, e.g. runs/stack-g1) stores StageSpec chains as
    # dicts; the rollout consumes StageSpec attributes, so restore the real
    # dataclasses -- asdict/sha round-trip identically either way.
    if kwargs.get("stages") is not None:
        kwargs["stages"] = tuple(
            StageSpec(s["name"], tuple(Clause(**c) for c in s["success"]), s["budget"])
            for s in kwargs["stages"])
    return Preregistration(**kwargs)


def rule_from_canonical(payload: dict) -> Rule:
    """Invert ``Rule.canonical()``: the archived byte-stable form back to a Rule.

    The canonical program is the RESOLVED steps ``(name, dur, dx, dy)``. A
    zero-offset program round-trips exactly as an explicit
    ``RecoverySpec.program``; one with lateral offsets cannot (the explicit
    form carries no dx/dy), so it falls back to the named repertoire strategy
    -- and the caller's child_sha assertion is what catches a repertoire that
    drifted since sealing.
    """
    t = payload["trigger"]
    r = payload["recovery"]
    if any(dx or dy for _name, _dur, dx, dy in r["program"]):
        program = None  # repertoire-owned offsets; the child_sha assertion pins them
    else:
        program = tuple((name, int(dur)) for name, dur, _dx, _dy in r["program"])
    return Rule(
        payload["rule_id"],
        Trigger(t["feature"], t["op"], t["threshold"], t["dwell"], t["arm_after"], t["reducer"]),
        RecoverySpec(name=r["name"], program=program, sensor_sd=r["sensor_sd"],
                     max_invocations=r["max_invocations"]),
    )


def rebuild_final_bundle(prereg: Preregistration, generations: list[dict]) -> Bundle:
    """The promoted chain, re-grown through ``Bundle.append`` and asserted per
    generation against the sealed ``child_sha`` -- rescoring/reseeding the wrong
    object would be worse than not doing it at all.

    A SEEDED campaign (round 90: ``prereg.parent_store`` set, e.g. place-g2) grew
    its generations onto the parent's final bundle, so its sealed child_shas are
    over ``parent + gen`` -- rebuilding from an empty root would fail the very
    first child_sha assertion. Start from the same seed run_campaign did (the
    parent rebuilt, sha-asserted and rebudgeted) so the chain reproduces. A
    from-scratch campaign (``parent_store is None``) keeps the empty root exactly."""
    if prereg.parent_store is not None:
        from plugins.rsi.campaign import _seed_from_parent
        bundle = _seed_from_parent(prereg, verbose=False)
    else:
        bundle = Bundle(rules=(), critic_budget=prereg.critic_budget,
                        action_budget=prereg.action_budget)
    for gen in generations:
        if not gen["promoted"]:
            continue
        bundle = bundle.append(rule_from_canonical(gen["rule"]))
        if bundle.sha() != gen["child_sha"]:
            raise AssertionError(
                f"rebuilt generation {gen['generation']} hashes to {bundle.sha()[:12]}, "
                f"archive sealed child_sha {gen['child_sha'][:12]}: the reconstruction "
                "does not reproduce the sealed bundle")
    if not bundle.rules:
        raise ValueError("no promoted generations in this store; nothing to rebuild")
    return bundle
