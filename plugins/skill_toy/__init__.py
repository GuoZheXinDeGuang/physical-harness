"""The smallest demonstrator card (R5 acceptance, the charter's "说明书").

It binds ONE new task name, ``toy``, purely through ``manifest.toml`` -- no edit
to the base, no line in ``harness_runtime``. The runtime resolves ``{"kind":
"task","task":"toy"}`` to this card's planner through the manifest union, proving
"adding a task = installing a plugin dir". It reuses the base ``stack`` skill's
execution binding, so the point on show is task-level self-registration, not a
new skill.
"""
