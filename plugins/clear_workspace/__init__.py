"""The ``clear_workspace`` persistent-episode mission card (M7): a ≥12-node graph
executed as ONE robosuite mode-0 PickPlace episode -- one reset, four objects,
sub-goals driven sequentially in the SAME world, per-step machine verify on the
LIVE state, in-episode consequence-carrying replan (a dropped object stays where
it fell), never a reset between sub-goals.

Narrative: survey the cluttered workspace -> decide a clearing order -> for each
object: grasp+lift (a ``segment`` node driving the shared live env) then verify it
is in its bin on the LIVE state (``not_in_bin``); on a failure replan against the
world as it now is (retry a slipped grasp in place, or drop an unplaceable object
and continue the rest) -> a final integrity sweep -> a machine report. Twelve
nodes over four KINDS (perceive x2, decide x2, segment x4, verify x4); each
segment is itself grasp->lift->transport->release, so the graph unfolds to 20+
sub-steps inside one world (docs/m7-persistent-mission.md).

Dropping this dir is the whole install: ``manifest.toml`` binds the new task
``clear_workspace`` to its fault-adaptive planner + the composite
``clear_build_provider`` (task "clearall" is not "stack" -> the four-phase
ScriptedDriver the sub-goals retarget) + the card's own CATALOGUE / ORACLES /
PREDICATES vocabulary AND the M7 persistence declarations (``episodic`` +
``episode`` + ``segment_specs``). No base edit beyond the shared runner that
already landed: the generic EpisodeContext / ``segment`` kind live in
``plugins.task.workload``, so this card is PURE DATA. ``discover()`` folds the
binding into the union at boot; ``harness_runtime`` threads the episode block +
segment_specs + PREDICATES onto the brief.
"""
