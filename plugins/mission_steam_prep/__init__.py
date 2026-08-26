"""The ``steam_prep`` persistent-episode mission card (M7 on RoboCasa): a
21-node graph executed as ONE robocasa MultistepSteaming episode -- the
five-phase steaming prep with temporal constraints (faucet on -> vegetable into
the RUNNING sink -> faucet off -> vegetable into the pot -> pot onto the chosen
burner), each phase a driven segment + a live-state verify, in-episode retry.

GRAPH-FIRST: the graph/predicates/vault presence are complete; the faucet
segments run an honest stub until a sink-handle driver exists (hinge-arc
actuation, the phase-3 door gap) -- the E2E frontier is the water-on verify,
xfail "awaiting sink driver". The temporal sequencing truth is MultistepSteaming
's own accumulated flags, enforced structurally by the verify order.
"""
