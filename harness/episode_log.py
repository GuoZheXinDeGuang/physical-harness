"""Durable episode log: a row stream plus one packed column block per episode.

Shape, and why it is not one event per frame
--------------------------------------------
dsh keeps every model-visible fact in an append-only session log, but a chat
turn is ~10 events while a 20 Hz episode is 100 control steps and a campaign is
thousands of episodes. dsh already solved the same pressure once, packing many
``assistant/chunk`` rows into a single ``text-chunks`` row that keeps per-chunk
timing (see the packed row in local-archive/docs/retired-from-public/verified-environment.md). Governor uses the
same split: a handful of semantic ROWS per episode, and the per-step feature
values as one columnar BLOCK addressed by content hash.

Reconstruction without storing 100 digests
------------------------------------------
The invariant to preserve is dsh's "visible IFF logged", asserted rather than
documented. Storing a sha256 per control step would cost more than the data it
protects, so the runner keeps a CHAINED digest
``h_t = sha256(h_{t-1} || digest(view_t))`` and logs the single final value.
The audit rebuilds every view from the stored columns and recomputes the chain:
a tampered value, an added feature, or a dropped step all break it. The chain is
computed online from live views and re-derived offline from storage, so unlike a
cached per-view digest it is not comparing a value to itself.
"""

from __future__ import annotations

import hashlib
import json
import zlib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

CHAIN_SEED = "governor/episode-chain/v1"

# L1 rung 3: the chain math is harness.events'; only the seed is ours. The
# construction is bit-identical to the local functions this replaced (there is
# a golden-value test), so every archived episode log still verifies.
from harness.events import chain_start as _chain_start
from harness.events import chain_step  # noqa: F401  deliberate re-export for callers


def chain_start() -> str:
    return _chain_start(CHAIN_SEED)


@dataclass
class EpisodeLog:
    """Append-only row stream plus a content-addressed block store."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        (self.root / "blocks").mkdir(parents=True, exist_ok=True)
        self.rows_path = self.root / "rows.jsonl"

    # -- blocks ---------------------------------------------------------------
    def put_block(self, columns: Mapping[str, Sequence[float]]) -> str:
        """Store one episode's feature columns; return the content hash."""
        names = sorted(columns)
        payload = {"names": names, "n": len(columns[names[0]]) if names else 0,
                   "columns": {n: [round(float(v), 9) for v in columns[n]] for n in names}}
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        digest = hashlib.sha256(raw).hexdigest()
        path = self.root / "blocks" / f"{digest}.json.z"
        if not path.exists():
            path.write_bytes(zlib.compress(raw, 6))
        return digest

    def get_block(self, digest: str) -> dict:
        raw = zlib.decompress((self.root / "blocks" / f"{digest}.json.z").read_bytes())
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"block {digest[:12]} failed its own content hash")
        return json.loads(raw)

    # -- rows -----------------------------------------------------------------
    def append(self, kind: str, data: dict) -> int:
        """Append one semantic row. `seq == line number`, as in dsh's session log."""
        seq = self.size()
        with self.rows_path.open("a") as fh:
            fh.write(json.dumps({"seq": seq, "type": kind, "data": data},
                                separators=(",", ":"), default=str) + "\n")
        return seq

    def size(self) -> int:
        if not self.rows_path.exists():
            return 0
        return sum(1 for _ in self.rows_path.open())

    def rows(self) -> Iterator[dict]:
        if not self.rows_path.exists():
            return iter(())
        with self.rows_path.open() as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)

    def episodes(self) -> list[dict]:
        """Group rows into per-episode records keyed by (seed, bundle_sha)."""
        out: dict[tuple, dict] = {}
        for row in self.rows():
            d = row["data"]
            key = (d.get("seed"), d.get("bundle_sha"))
            rec = out.setdefault(key, {"seed": d.get("seed"), "bundle_sha": d.get("bundle_sha"),
                                       "fires": []})
            if row["type"] == "episode/start":
                rec["spec"] = d["spec"]
            elif row["type"] == "episode/frames":
                rec["block"] = d["block"]
                rec["names"] = d["names"]
            elif row["type"] == "critic/fire":
                rec["fires"].append({"rule_id": d["rule_id"], "step": d["step"]})
            elif row["type"] == "episode/end":
                rec["success"] = d["success"]
                rec["steps"] = d["steps"]
                rec["chain"] = d["chain"]
        return list(out.values())


def write_episode(log: EpisodeLog, result: dict, spec_payload: dict, bundle_sha: str) -> str:
    """Persist one rollout result as rows plus one column block."""
    columns = {k: list(map(float, v)) for k, v in result["trace"].items()}
    block = log.put_block(columns)
    common = {"seed": result["seed"], "bundle_sha": bundle_sha}
    log.append("episode/start", {**common, "spec": spec_payload})
    log.append("episode/frames", {**common, "block": block, "names": sorted(columns)})
    for fire in result.get("fires", []):
        log.append("critic/fire", {**common, **fire})
    log.append("episode/end", {**common, "success": result["success"],
                               "steps": result["steps"], "chain": result["chain"]})
    return block
