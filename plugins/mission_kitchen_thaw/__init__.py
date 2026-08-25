"""The ``kitchen_thaw`` persistent-episode mission card (M7 on RoboCasa): a 15-node
graph executed as ONE robocasa MicrowaveThawingFridge episode -- one reset, one
frozen meat, six appliance sub-goals driven sequentially in the SAME world, per-node
machine verify on the LIVE fixture/contact state, in-episode retry of a failed
sub-goal (no reset between sub-goals).

Narrative: survey the kitchen -> plan the appliance path -> nav-to-fridge, verify at
fridge -> grasp the meat, verify grasped -> nav-to-microwave (loaded), verify still
carried -> place inside, verify inside -> close the door, verify closed -> press
start, verify on -> machine report. Fifteen nodes over four KINDS (perceive x1,
decide x2, segment x6, verify x6).

Dropping this dir is the whole install: ``manifest.toml`` binds the new task
``kitchen_thaw`` to its deterministic planner + the robocasa composite kitchen
driver (task "kitchen_thaw" rides PandaOmron stage drivers, one per sub-goal) +
the card's own CATALOGUE / ORACLES / PREDICATES vocabulary AND the M7 persistence
declarations (``episodic`` + ``episode`` + ``segment_specs``) AND the per-session
``env`` / ``percept`` refs that mount the robocasa embodiment (out of the base fold,
enabled=false). No base edit beyond the shared runner that already landed plus the
generic heterogeneous-segment branch: the mission is PURE DATA. ``discover()`` folds
the binding into the union at boot; ``harness_runtime`` threads the episode block +
segment_specs + predicates + the sim env/percept onto the mount plan and brief.
"""
