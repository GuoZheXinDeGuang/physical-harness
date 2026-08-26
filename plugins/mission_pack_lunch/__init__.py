"""The ``pack_lunch`` persistent-episode mission card (M7 on RoboCasa): a
31-node graph executed as ONE robocasa PackFoodByTemp episode -- one reset, two
hot stove-area items + two cold fridge items, sixteen driven sub-goals (a
nav/grasp/carry/pack quartet per item) in the SAME world, per-node machine
verify, in-episode retry.

Narrative: survey -> sort-temp (decide: hot -> tupperware0, cold -> tupperware1,
a pure function of the item's temperature attribute) -> per item: nav, verify
arrived; grasp, verify SECURE_DZ-grasped; carry to the dining counter; pack into
the ASSIGNED tupperware, verify inside -> machine report. Thirty-one nodes over
four KINDS (perceive x1, decide x2, segment x16, verify x12).
"""
