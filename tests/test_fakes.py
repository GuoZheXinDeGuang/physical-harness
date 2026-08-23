"""R1: the base boots on its own fakes -- zero plugins, one green episode.

Proves W1's "开机" claim at the kernel level: mount fakes_profile() on
Kernel(CAPABILITIES) and every non-privileged seam has a provider that
satisfies its contract, and a governed-style rollout (env -> policy -> percept,
scored by the env's success hook) runs to completion through the kernel alone.
"""

from __future__ import annotations

from types import SimpleNamespace

from harness import Kernel, SessionLog, contracts, resolve_plan
from harness.definitions import CAPABILITIES
from profiles import fakes_profile

# The seam -> contract map the fakes are expected to satisfy once mounted.
_EXPECTED = {
    "embodiment.env": contracts.EnvProvider,
    "policy.driver": contracts.PolicyFactory,
    "percept.model": contracts.PerceptModel,
    "reasoner.proposer": contracts.Reasoner,
    "task.planner": contracts.TaskPlanner,
    "graph.skill": contracts.SkillGraph,
    "graph.scene": contracts.SceneGraph,
}


def _mounted_kernel() -> Kernel:
    k = Kernel(CAPABILITIES, log=SessionLog())
    k.mount(resolve_plan(fakes_profile()))
    return k


def test_fakes_profile_mounts_every_seam_satisfying_its_contract():
    k = _mounted_kernel()
    for cap, contract in _EXPECTED.items():
        provider = k.resolve(cap, consumer="test")
        assert isinstance(provider, contract), f"{cap} fake violates {contract.__name__}"
    # exec.rollouts is base-owned (the local pool), not a fake, but the profile
    # still boots it so a zero-plugin boot has an execution fabric.
    assert isinstance(k.resolve("exec.rollouts", consumer="test"),
                      contracts.RolloutExecutor)


def test_one_fake_governed_episode_runs_green():
    k = _mounted_kernel()
    env = k.resolve("embodiment.env", consumer="rollout")
    policy = k.resolve("policy.driver", consumer="rollout")
    percept = k.resolve("percept.model", consumer="rollout")
    scene = k.resolve("graph.scene", consumer="rollout")
    skills = k.resolve("graph.skill", consumer="rsi")

    spec = SimpleNamespace(seed=90001, task="fake", horizon=5)
    handle = env.make_env(spec)
    obs = handle.reset()
    driver = policy.make_driver(spec)
    start_z = float(obs[env.object_key(spec)][2])

    steps = 0
    for t in range(spec.horizon):
        action = driver.act(obs)
        obs, _r, done, _info = handle.step(action)
        percept.object_estimate(obs, spec, sensor_sd=0.02, draw=t)
        steps += 1
        if done:
            break
    handle.close()

    success = env.success(obs, spec, start_z)
    assert success is False  # a null env never lifts; green != success
    assert steps == spec.horizon
    assert driver.identity == "fake:zero"

    # The planning/graph seams answer too, and publish is idempotent (content
    # addressed), so a governed loop can record a skill without a real graph.
    record = {"trigger": "observable.x > 0", "effect": {"delta": 0.0}}
    digest = skills.publish(record)
    assert digest == skills.publish(record)
    assert skills.skills()[0]["effect"]["delta"] == 0.0
    assert "objects" in scene.snapshot(obs)

    # The whole rollout is one verifiable chain of accounted resolutions.
    assert k.resolutions()[0].consumer == "rollout"
    assert k._log.verify()


def test_reasoner_and_planner_fakes_answer_null():
    k = _mounted_kernel()
    brief = {"task": "fake", "scene": {}, "catalogue": []}
    assert k.resolve("reasoner.proposer", consumer="rsi").propose(brief) == {}
    plan = k.resolve("task.planner", consumer="rsi").plan(brief)
    assert plan["goal"] == "fake"
    assert [n["id"] for n in plan["nodes"]] == ["n0"]
