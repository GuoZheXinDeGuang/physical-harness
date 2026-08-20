"""Chained session log: the kernel's append-only memory.

Same construction phase 1 proved out for episodes (each row extends a hash
chain over the canonical payload), applied to kernel events: mounts,
resolutions, privileged access. In-place tampering breaks the chain and
verify() reports it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_SEED = "harness-session-v1"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class SessionLog:
    """Append-only, chain-committed event log; optionally mirrored to disk."""

    def __init__(self, root: Path | None = None) -> None:
        self._rows: list[dict] = []
        self._chain = _sha(_SEED)
        self._root = root
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)

    def append(self, kind: str, data: dict) -> int:
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
        data_sha = _sha(payload)
        self._chain = _sha(f"{self._chain}:{kind}:{data_sha}")
        row = {"seq": len(self._rows), "kind": kind, "data": data,
               "sha": data_sha, "chain": self._chain}
        self._rows.append(row)
        if self._root is not None:
            with (self._root / "rows.jsonl").open("a") as fh:
                fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        return row["seq"]

    def rows(self) -> tuple[dict, ...]:
        return tuple(self._rows)

    def verify(self) -> bool:
        chain = _sha(_SEED)
        for row in self._rows:
            payload = json.dumps(row["data"], sort_keys=True, separators=(",", ":"),
                                 default=str)
            if _sha(payload) != row["sha"]:
                return False
            chain = _sha(f"{chain}:{row['kind']}:{row['sha']}")
            if chain != row["chain"]:
                return False
        return True
