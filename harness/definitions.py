"""The backbone's capability catalog: what the three research layers plug into."""

from __future__ import annotations

from harness import contracts
from harness.capability import Definition

CAPABILITIES: tuple[Definition, ...] = (
    Definition("embodiment.env", contracts.EnvProvider,
               "Builds environments for episode specs (layer 3)."),
    Definition("embodiment.ground_truth", contracts.GroundTruthState,
               "Simulator-only oracle state; absent on a real robot.",
               privileged=True),
    Definition("policy.driver", contracts.PolicyFactory,
               "Builds the frozen policy driver an episode runs under (layer 3)."),
    Definition("percept.model", contracts.PerceptModel,
               "Onboard state estimate, degradable for the ablation ladder."),
    Definition("exec.rollouts", contracts.RolloutExecutor,
               "Execution fabric: maps episode jobs to results."),
    Definition("reasoner.proposer", contracts.Reasoner,
               "Layer 1 seam: proposes interventions from an evidence brief."),
    Definition("graph.skill", contracts.SkillGraph,
               "Layer 2 seam: measured skills with preconditions/effects/failure modes."),
    Definition("graph.scene", contracts.SceneGraph,
               "Layer 2 seam: object/relation snapshot of the world."),
)
