"""The ``inventory_build`` heterogeneous mission card (M6): an 11-node graph
mixing four NODE KINDS -- perceive / decide / verify / manipulate -- over the
four robosuite tasks the embodiment can stage.

Narrative: survey the workspace -> classify what is present -> decide a build
order from geometry -> grasp / build / clear with verify gates between ->
final integrity check -> a machine report. Eleven nodes, four kinds (perceive
x2, decide x2, verify x3, manipulate x4); a verify-node failure is folded back
as a ``node_failure`` fault the workload's OWN replan loop reroutes -- no new
routing code (local-archive/docs/retired-from-public/m6-mission-design.md).

Dropping this dir is the whole install: ``manifest.toml`` binds the new task
``inventory_build`` to its planner + the composite ``clear_build_provider``
(stack node -> eight-phase StackScriptedDriver, every other manipulate node ->
four-phase scripted -- exactly the routing the four staged tasks need) + the
card's own CATALOGUE / ORACLES / PREDICATES vocabulary. No base edit; the
generic node-kind machinery already lives in the base (``plugins.task.workload``
handlers + ``plugins.task.validate.NODE_KINDS``), so this card is PURE DATA:
the base owns the kinds, the card owns the predicates. ``discover()`` folds the
binding into the union at boot; ``harness_runtime`` threads the PREDICATES table
onto the brief beside catalogue/oracles.

The grasp / pick / stack EXECUTION bindings already live in
``plugins.task.workload.SKILL_SPECS`` -- this card composes what exists and adds
only the perceive/decide/verify PREDICATES its kindful nodes name. Predicates
reach the env + percept providers by REF at run time (``load_provider``), never
by a sibling import (plugins never import each other -- tests/test_boundaries).
"""
