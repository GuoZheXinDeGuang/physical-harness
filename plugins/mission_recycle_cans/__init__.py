"""The ``recycle_cans`` persistent-episode mission card (M7 on RoboCasa): a
32-node graph executed as ONE robocasa RecycleSodaCans episode -- one reset,
four scattered soda cans, sixteen driven sub-goals (a nav/grasp/carry/drop
quartet per can) in the SAME world, per-node machine verify on the LIVE
contact/fixture state, in-episode retry of a failed sub-goal.

Narrative: survey the kitchen -> rank the cans by distance (decide) -> per can:
nav to its counter, verify arrived; grasp, verify SECURE_DZ-grasped; carry to
the stove standoff; drop on the stove-side counter, verify placed -> sweep for
strays (perceive) -> machine report. Thirty-two nodes over four KINDS
(perceive x2, decide x2, segment x16, verify x12).

Pure data + a deterministic planner: ``manifest.toml`` binds the task to the
planner and the robocasa composite recycle driver by ref; discover() folds the
binding; harness_runtime threads the episode block + segment_specs + predicates.
"""
