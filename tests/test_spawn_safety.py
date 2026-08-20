"""Provider refs must survive a real process boundary (review round 54, major #8).

The parity reruns prove this end to end with robosuite, but a fast regression
test keeps the property pinned without a simulator: a spawned Pool worker gets
nothing but the pickled ref string and must reconstruct the provider itself.
"""

import multiprocessing as mp

from harness.registry import load_provider


def _resolve_in_child(ref: str) -> str:
    provider = load_provider(ref)
    return type(provider).__name__


def test_a_provider_ref_reconstructs_inside_a_spawned_worker():
    ctx = mp.get_context("spawn")
    with ctx.Pool(1) as pool:
        name = pool.apply(_resolve_in_child, ("plugins.graphs:skill_graph_provider",))
    assert name == "InMemorySkillGraph"
