"""The ``clear_build`` long-horizon mission card (M5): a 4-node skill chain.

Narrative: arm bring-up, clear two clutter objects, then build the tower --
grasp a cube (Lift), pick the can then the milk (PickPlace), stack cubeA on
cubeB (Stack). Three DISTINCT SKILL_SPECS bindings (grasp / pick / stack) over
the four robosuite tasks the embodiment can stage, sequenced by the workload's
own replan loop (a succeeded node is skipped, never re-run).

Dropping this dir is the whole install: ``manifest.toml`` binds the new task
``clear_build`` to its planner + the composite ``clear_build_provider`` (which
routes the stack node to the eight-phase StackScriptedDriver and every other
node to the four-phase scripted driver -- one policy.driver mount, chosen by
spec.task). No base edit; ``discover()`` folds the binding into the union at
boot. Each node's governance is the bundle its task's mounted skills assemble to
(``plugins.task.workload.assemble_bundle``): a stack node pulls BOTH the stack-g1
regrasp and place-g2 replace families into one Bundle when their sealed records
are mounted -- the mission's "both governance families on one node" without any
new promotion. See local-archive/docs/retired-from-public/long-horizon-design.md.

The planner/catalogue/oracles are this card's own from-scratch vocabulary
(skill_geometric_grasp pattern), imported by ref, never by a sibling import
(plugins never import each other -- tests/test_boundaries.py). The grasp/pick/
stack EXECUTION bindings already live in plugins.task.workload.SKILL_SPECS, so
this card adds no SKILL_SPECS entry -- it only composes what exists.
"""
