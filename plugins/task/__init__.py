"""Task-planning seam plugins: the Plan -> Act -> Verify edge, harness-side.

The planner (capability ``task.planner``) authors a skill-call graph — the
inter-skill CONTROLLER. A StageSpec chain stays the intra-skill SCORER; the
two layers never merge, because stages' "scorer never controller" invariant is
what licenses their privileged reads. Every plan, whoever authored it, passes
``validate.validate_plan`` before anything actuates: the schema is the
boundary, not the author (governor/proposer.py's stance, re-expressed
harness-native because plugins import neither zos nor sibling plugins).
"""
