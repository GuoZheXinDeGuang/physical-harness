"""Config layering: profile -> bundles -> patches -> MountPlan.

The resolved plan is content-hashed and carries per-capability provenance, so
"what was mounted, and which layer decided it" is auditable. Round 29's lesson
(anything that can move a conclusion must leave an auditable trace) is a
structure here, not a convention.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


def sha_json(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


#: Mount params that name WHERE output is written, never WHAT is produced. The
#: hash law is round 29's: anything that can move a conclusion enters the hash.
#: An output directory cannot move a conclusion, so it is excluded from the
#: canonical form the plan is content-hashed over -- this is the exact license,
#: and it is what makes a skill record path-portable: the SAME campaign written
#: to a different --out yields a byte-identical mount_plan_sha. The value still
#: rides the session log's capability.provide row, so where skills landed stays
#: auditable. Excluded by key here, not filtered downstream, so it never enters
#: the sha from any mount at all (base_profile mounts graph.skill root-less; the
#: campaign scripts patch the --out root in, and only that root is dropped -- the
#: provider ref, which CAN move conclusions, stays in the hash).
_STORAGE_PARAMS = frozenset({"root"})


@dataclass(frozen=True, slots=True)
class Mount:
    """Bind one capability to one provider factory reference plus its params."""

    capability: str
    provider: str                      # "package.module:factory"
    params: Mapping[str, Any] = field(default_factory=dict)

    def canonical(self) -> dict:
        params = {k: v for k, v in self.params.items() if k not in _STORAGE_PARAMS}
        return {"capability": self.capability, "provider": self.provider,
                "params": dict(sorted(params.items()))}


@dataclass(frozen=True, slots=True)
class Profile:
    """The base layer: a named, complete-enough set of mounts."""

    name: str
    mounts: tuple[Mount, ...]


@dataclass(frozen=True, slots=True)
class Bundle:
    """A named group of mounts layered over the profile."""

    name: str
    mounts: tuple[Mount, ...]


@dataclass(frozen=True, slots=True)
class Patch:
    """The final overlay: overrides and removals."""

    name: str
    override: tuple[Mount, ...] = ()
    remove: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MountPlan:
    """The resolved binding, with per-capability provenance. This enters content hashes."""

    mounts: tuple[Mount, ...]
    sources: tuple[str, ...]           # which layer won each capability, aligned with mounts

    def canonical(self) -> dict:
        return {"mounts": [m.canonical() for m in self.mounts],
                "sources": list(self.sources)}

    def sha(self) -> str:
        return sha_json(self.canonical())

    def ref(self, capability: str) -> str:
        for m in self.mounts:
            if m.capability == capability:
                return m.provider
        raise KeyError(capability)


def resolve_plan(profile: Profile, bundles: tuple[Bundle, ...] = (),
                 patches: tuple[Patch, ...] = ()) -> MountPlan:
    """Later layers override earlier ones by capability name; order is canonical."""

    chosen: dict[str, tuple[Mount, str]] = {}
    for m in profile.mounts:
        chosen[m.capability] = (m, f"profile:{profile.name}")
    for b in bundles:
        for m in b.mounts:
            chosen[m.capability] = (m, f"bundle:{b.name}")
    for p in patches:
        for cap in p.remove:
            chosen.pop(cap, None)
        for m in p.override:
            chosen[m.capability] = (m, f"patch:{p.name}")
    ordered = [chosen[k] for k in sorted(chosen)]
    return MountPlan(mounts=tuple(m for m, _ in ordered),
                     sources=tuple(s for _, s in ordered))
