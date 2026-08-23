"""The geometric-grasp skill card (round 97): first perception-driven grasp, carded.

Binds a new task ``lift_geometric`` to the zero-privilege geometric grasp policy
(``plugins.policies:lift_geometric_provider``) purely through ``manifest.toml``,
carries a re-runnable acceptance ``[claim]`` (dev/held-out preregistered in
STATUS.md), and points its ``[campaigns]`` entry at the parameterized 验货 script.
No base edit -- dropping this dir is the whole install (charter 终态验收). The
grasp math lives in the embodiment card; this card reaches it by ref, never by
import (plugins never import each other -- tests/test_boundaries.py).
"""
