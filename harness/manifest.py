"""Plugin self-registration: ``plugins/*/manifest.toml`` parsed as DATA.

A manifest is TOML, read and never imported -- so a card declaring its mounts,
task bindings, campaigns and third-party surface stays declarative in exactly
the way a hand-written profile was (``tests/test_boundaries.py``). Discovery is a
directory scan; the registry is the UNION of every installed manifest, and any
collision across manifests -- a duplicate capability mount, a duplicate task, a
duplicate campaign -- is LOUD, because two cards silently fighting over one seam
is the failure the fold exists to catch.

The union is what makes "adding a task = installing a plugin dir" true: a brief
still names only a task STRING, and the runtime resolves task->policy through the
manifests present at boot. A manifest declaring ``actuation = "real"`` is refused
here -- the sim runtime never mounts a real actuator; a real card rides a
separate authenticated runtime (GOAL v4.2).

Imports only stdlib + ``harness.config`` (never a plugin), so ``base_profile``
folds it while ``harness`` stays plugin-free (``tests/test_kernel.py``).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from harness.config import Mount

#: The one plugin cage the scan walks. A card is a directory with a manifest.
PLUGINS_ROOT = Path(__file__).resolve().parent.parent / "plugins"


@dataclass(frozen=True)
class Registry:
    """The folded union of every installed manifest.

    ``mounts`` feed ``base_profile``; ``task_bindings`` and ``campaigns`` feed the
    runtime's server-side selectors (the tables that used to be welded into
    ``harness_runtime``); ``third_party`` feeds the plugin-boundary AST test.
    """

    mounts: tuple[Mount, ...] = ()
    task_bindings: dict[str, dict] = field(default_factory=dict)
    campaigns: dict[str, str] = field(default_factory=dict)
    third_party: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _load(root: Path) -> list[tuple[str, dict]]:
    """(plugin dir name, parsed manifest), in a deterministic scan order."""
    return [(mf.parent.name, tomllib.loads(mf.read_text()))
            for mf in sorted(root.glob("*/manifest.toml"))]


def card_mounts(data: dict) -> list[Mount]:
    """Every Mount a SINGLE manifest declares.

    ``discover`` folds many manifests (unioning, loud on collision);
    ``plugin_doctor`` checks one card in isolation. Both share this per-manifest
    ``cap -> ref (+params)`` extraction so the mount shape has one reader.
    """
    out = []
    for cap, spec in data.get("mounts", {}).items():
        ref = spec if isinstance(spec, str) else spec["ref"]
        params = {} if isinstance(spec, str) else dict(spec.get("params", {}))
        out.append(Mount(cap, ref, params))
    return out


def _claim(registry: dict, owner: dict, key, value, *, kind: str, plugin: str) -> None:
    """Record key->value in one namespace, refusing a second claimant loudly.

    ``owner`` is per-namespace (capabilities, tasks and campaigns are independent
    name spaces), so a task and a campaign may legitimately share a name -- only a
    second card claiming the SAME kind of thing is a collision.
    """
    if key in owner:
        raise ValueError(
            f"duplicate {kind} {key!r}: declared by both {owner[key]!r} and "
            f"{plugin!r} -- two cards cannot claim one {kind}")
    owner[key] = plugin
    registry[key] = value


def discover(root: Path = PLUGINS_ROOT) -> Registry:
    """Fold every ``<root>/*/manifest.toml`` into one registry (union, loud on collision)."""
    mounts: dict[str, Mount] = {}
    task_bindings: dict[str, dict] = {}
    campaigns: dict[str, str] = {}
    third_party: dict[str, tuple[str, ...]] = {}
    cap_owner: dict = {}
    task_owner: dict = {}
    camp_owner: dict = {}

    for plugin, data in _load(root):
        if data.get("actuation", "sim") == "real":
            raise ValueError(
                f"plugin {plugin!r} declares actuation:real; the sim runtime "
                "refuses it (a real actuator needs a separate authenticated runtime)")
        # ``enabled = false`` = installed but inactive: doctor it (card_mounts
        # reads the file directly, ignoring this), then flip it on and disable the
        # incumbent. Lets an ALTERNATIVE provider for an already-claimed seam --
        # the qwen reasoner card beside plugins/reasoner (R7) -- sit in the cage
        # without tripping the duplicate-capability guard, and keeps it out of the
        # folded plan so the base sha is untouched.
        if not data.get("enabled", True):
            continue
        for m in card_mounts(data):
            _claim(mounts, cap_owner, m.capability, m,
                   kind="capability", plugin=plugin)
        for task, binding in data.get("task_bindings", {}).items():
            _claim(task_bindings, task_owner, task, dict(binding),
                   kind="task", plugin=plugin)
        for name, script in data.get("campaigns", {}).items():
            _claim(campaigns, camp_owner, name, script,
                   kind="campaign", plugin=plugin)
        if "third_party" in data:
            third_party[plugin] = tuple(data["third_party"])

    return Registry(tuple(mounts.values()), task_bindings, campaigns, third_party)
