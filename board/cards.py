#!/usr/bin/env python3
"""Read-only 机箱 view: the installed cards (``plugins/*/manifest.toml``) as data.

The board's other faces read the evidence tree (runs/); this one reads the SLOT
tree -- what cards are seated in the chassis and what each contributes. Like
harness/manifest.py it parses every manifest AS DATA via tomllib and never
imports card code, so listing the chassis can never run a plugin. It reuses that
module's ``_load`` scan (the one deterministic ``plugins/*/manifest.toml`` walk),
so the listed set stays in lock-step with the set ``discover`` actually folds.

Unlike ``discover`` this is per-card and total: it does NOT union, does NOT skip
``enabled = false``, and does NOT refuse ``actuation = "real"`` -- a chassis view
must show the inactive and the refused card too. Executing a card's 体检 is an
ACTION (plugin_doctor), out of scope here.

Both call-faces are byte-thin passthroughs of ``list_cards``:
board/storecli.py (``cards`` subcommand) and board/mcp_server.py (``list_cards``
tool), the same MCP-与-CLI 同一函数 discipline as the rest of the board.
"""

from __future__ import annotations

from harness.manifest import PLUGINS_ROOT, _load


def list_cards(root=PLUGINS_ROOT) -> list[dict]:
    """Every seated card, in ``_load``'s deterministic scan order.

    Per card: its dir name, a repo-relative ``dir``, the full parsed manifest as
    JSON-safe data, a name-only ``contributes`` fold over the four contribution
    tables, and the two chassis flags (``actuation``/``needs_sim``, defaulted as
    manifest.py and plugin_doctor default them).
    """
    cards = []
    for name, data in _load(root):
        cards.append({
            "name": name,
            "dir": f"{root.name}/{name}",
            "actuation": data.get("actuation", "sim"),
            "needs_sim": bool(data.get("needs_sim", False)),
            "contributes": {
                "mounts": sorted(data.get("mounts", {})),
                "task_bindings": sorted(data.get("task_bindings", {})),
                "campaigns": sorted(data.get("campaigns", {})),
                "bundles": sorted(data.get("bundles", {})),
            },
            # ponytail: JSON-safe because manifests are pure config (str/num/
            # bool/table/array); add a datetime coercion when a manifest needs one.
            "manifest": data,
        })
    return cards


if __name__ == "__main__":  # smoke self-check: the real chassis parses and folds
    import json
    print(json.dumps(list_cards(), indent=2))
