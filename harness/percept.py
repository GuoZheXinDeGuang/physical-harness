"""The privilege boundary: candidate code never touches a raw observation.

Why containment rather than a tier lattice
------------------------------------------
The privilege leak that actually happened in this project was not a mis-tiered
feature name. It was ``target = obs["cube_pos"]`` inside a hand-written recovery
(docs/headline-finding.md) -- a raw-observation read that no naming rule would
have caught, and which turned a +13.3% (n.s.) result into a reported +50%.

So the enforcement is structural: ``governed_rollout`` holds the raw dict, and
both the critic and the recovery receive only a :class:`FeatureView`. A view
contains exactly the features the campaign's privilege policy admits, so a
zero-budget campaign cannot read a privileged quantity even by name -- it is
simply not in the mapping.

Attestation
-----------
``FeatureView`` subclasses ``collections.abc.Mapping``, deliberately NOT ``dict``.
Against a ``dict`` subclass, ``.get(k)``, ``{**view}`` and ``dict(view)`` are
served by the C implementation and never reach an overridden ``__getitem__``, so
a privileged read becomes invisible to attestation. The ``Mapping`` ABC routes
every accessor through ``__getitem__``. Bulk accessors then over-attest -- they
report every key as read -- which is the safe direction: a candidate can only
ever be charged for more privilege than it used, never less.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from harness.features import REGISTRY, Privilege, privilege_cost


class FeatureView(Mapping):
    """A frozen, read-attesting projection of one control step.

    Every ``__getitem__`` is recorded. :meth:`digest` hashes the LIVE contents,
    never a value captured at construction, so a wrapper that adds a key after
    staging changes the digest and is caught by the reconstruction assertion.
    """

    __slots__ = ("_episode", "_reads", "_step", "_values")

    def __init__(self, values: Mapping[str, float], *, step: int, episode: str) -> None:
        self._values: dict[str, float] = {k: float(v) for k, v in values.items()}
        self._reads: set[str] = set()
        self._step = int(step)
        self._episode = str(episode)

    # -- Mapping protocol; every accessor funnels through __getitem__ ---------
    def __getitem__(self, key: str) -> float:
        value = self._values[key]          # KeyError first: an absent key is not a read
        self._reads.add(key)
        return value

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    # -- attestation ---------------------------------------------------------
    @property
    def reads(self) -> frozenset[str]:
        """Feature names this view has actually served."""
        return frozenset(self._reads)

    @property
    def step(self) -> int:
        return self._step

    @property
    def episode(self) -> str:
        return self._episode

    def privilege_used(self) -> int:
        """Budget consumed by the reads that actually happened."""
        return privilege_cost(self._reads)

    def digest(self) -> str:
        """Hash of the view's LIVE contents.

        A method, not a cached attribute: the whole point of the reconstruction
        assertion is to detect content that appeared after the view was staged.
        Caching the digest at construction makes that check compare a value to
        itself and pass unconditionally.
        """
        payload = json.dumps(
            {"episode": self._episode, "step": self._step,
             "values": {k: round(v, 9) for k, v in sorted(self._values.items())}},
            separators=(",", ":"), sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def snapshot(self) -> dict[str, float]:
        """Contents for the log. Not a read: the harness owns this, not the candidate."""
        return dict(self._values)

    def __repr__(self) -> str:
        return f"FeatureView(episode={self._episode!r}, step={self._step}, keys={len(self._values)})"


@dataclass(frozen=True, slots=True)
class PrivilegePolicy:
    """What a campaign admits into candidate-visible views.

    ``critic_budget`` and ``action_budget`` are separate on purpose, and the
    default makes the action side strictly stricter. Privilege in a TRIGGER is a
    sensing problem: a real deployment may be able to substitute a sensor for it.
    Privilege in an ACTION is not substitutable -- a recovery that steers using
    ground truth cannot be built at all outside a simulator.
    """

    critic_budget: int = 0
    action_budget: int = 0

    def admitted(self, budget: int) -> list[str]:
        """Feature names a view built under `budget` may contain."""
        return sorted(
            name for name, f in REGISTRY.items()
            if (0 if f.privilege is Privilege.OBSERVABLE else 1) <= budget
        )

    def critic_names(self) -> list[str]:
        return self.admitted(self.critic_budget)

    def action_names(self) -> list[str]:
        return self.admitted(self.action_budget)


def project(obs: Mapping, names: Sequence[str], *, step: int, episode: str) -> FeatureView:
    """Build the candidate-visible view for one control step.

    `names` comes from the campaign's :class:`PrivilegePolicy`, not from the
    candidate. This is the containment: a name the policy excludes is absent
    from the mapping, so reading it raises KeyError rather than leaking.
    """
    return FeatureView({n: REGISTRY[n].extract(obs) for n in names}, step=step, episode=episode)
