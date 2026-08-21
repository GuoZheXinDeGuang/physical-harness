"""Regression for the L0 capability seam: provider identity travels with specs.

`EpisodeSpec` carries `env_provider` / `policy_provider` as "module:factory"
strings, not module-global hooks, because a hook does not survive
multiprocessing spawn while a string pickles, content-hashes, and audits
cleanly (see governor/env.py, ARCHITECTURE.md's "L0 迁移方式", and
docs/design/observability.md's spawn findings). This file covers:

(a) the new fields default to the pre-seam shape, and a spec with refs set
    still pickles;
(b) each new plugin adapter satisfies its harness.contracts Protocol;
(c) make_env dispatch equivalence, with one short real env build/step/close;
(d) make_driver dispatch equivalence.
"""

from __future__ import annotations

import dataclasses as dc
import pickle

import numpy as np

from governor.env import NOMINAL_SCHEDULE, EpisodeSpec, make_env
from governor.policy import ScriptedDriver, make_driver
from harness.contracts import EnvProvider, PolicyFactory, Reasoner
from harness.registry import load_provider

ENV_REF = "plugins.embodiment_robosuite:provider"
POLICY_REF = "plugins.policies:provider"
REASONER_REF = "plugins.reasoner:provider"


# --- (a) EpisodeSpec: unchanged shape when the new fields are omitted -------

def test_episode_spec_omits_new_fields_unchanged():
    """A spec built with no provider refs is exactly what it was pre-seam."""
    s = EpisodeSpec(seed=7)
    assert dc.asdict(s) == {
        "seed": 7, "task": "lift", "robot": "Panda", "horizon": 900,
        "percept_noise": 0.020, "arm_noise": 0.02, "kp": 8.0, "policy": "scripted",
        "schedule": NOMINAL_SCHEDULE, "grasp_height_offset": 0.0, "env_provider": None, "policy_provider": None, "percept_provider": None,
    }


def test_new_fields_are_appended_at_the_end():
    """This codebase's defaulted-field-ordering rule, checked structurally."""
    names = [f.name for f in dc.fields(EpisodeSpec)]
    assert names[-3:] == ["env_provider", "policy_provider", "percept_provider"]
    assert all(f.default is None for f in dc.fields(EpisodeSpec)
              if f.name in ("env_provider", "policy_provider"))


def test_episode_spec_with_provider_refs_pickles():
    """Strings survive multiprocessing spawn; that is the whole point."""
    s = EpisodeSpec(seed=0, env_provider=ENV_REF, policy_provider=POLICY_REF)
    blob = pickle.dumps(s)
    assert pickle.loads(blob) == s


# --- (b) each provider satisfies its harness contract -----------------------
# Loaded the same way the kernel would mount them: through the registry ref
# string, not a direct import -- exercising the actual "module:factory" path.

def test_robosuite_embodiment_satisfies_env_provider():
    p = load_provider(ENV_REF)
    assert isinstance(p, EnvProvider)
    assert p.tasks() == ("lift", "pickcan", "stack")


def test_governor_policies_satisfies_policy_factory():
    assert isinstance(load_provider(POLICY_REF), PolicyFactory)


def test_search_reasoner_satisfies_reasoner():
    assert isinstance(load_provider(REASONER_REF), Reasoner)


def test_search_reasoner_propose_thin_adapter_round_trips():
    """Not required by the contract shape check above, but this is new code:
    a brief in, a plain-Mapping proposal out, matching governor.proposer's
    own return shape (a Rule or None) collapsed through Rule.canonical().
    """
    from plugins.rsi.campaign import Preregistration

    prereg = Preregistration(dev=tuple(range(20)), heldout=tuple(range(20, 30)),
                             percept_noise=0.02, critic_budget=0, action_budget=0,
                             recovery_sensor_sd=0.02, max_generations=1)
    traces, labels = [], []
    for i in range(20):
        failing = i % 2 == 0
        traces.append({
            "observable.finger_gap": np.full(40, 0.001 if failing else 0.04),
            "observable.eef_z": np.linspace(1.0, 0.9, 40),
            "observable.gripper_effort": np.zeros(40),
            "observable.joint_speed": np.zeros(40),
        })
        labels.append(not failing)

    reasoner = load_provider(REASONER_REF)
    out = reasoner.propose({"traces": traces, "labels": labels, "generation": 1,
                            "prereg": prereg})
    assert isinstance(out, dict) and "rule" in out
    assert out["rule"] is None or out["rule"]["rule_id"] == "g1"


# --- (c) make_env dispatch equivalence, with ONE short real env build -------

def test_make_env_dispatch_equivalence_and_smoke():
    """A ref'd spec builds through the plugin and behaves like a real env; a
    ref-less spec still resolves to the identical class (dispatch equivalence).
    """
    ref_spec = EpisodeSpec(seed=0, task="lift", env_provider=ENV_REF)
    plain_spec = EpisodeSpec(seed=0, task="lift")

    env = make_env(ref_spec)
    try:
        obs = env.reset()
        assert "robot0_eef_pos" in obs
        action = np.zeros(env.action_spec[0].shape, dtype=np.float32)
        env.step(action)
    finally:
        env.close()

    baseline = make_env(plain_spec)
    try:
        assert type(baseline) is type(env)
    finally:
        baseline.close()


# --- (d) make_driver dispatch equivalence ------------------------------------

def test_make_driver_dispatch_equivalence():
    ref_spec = EpisodeSpec(seed=0, policy="scripted", policy_provider=POLICY_REF)
    plain_spec = EpisodeSpec(seed=0, policy="scripted")

    ref_driver = make_driver(ref_spec)
    plain_driver = make_driver(plain_spec)
    assert type(ref_driver) is type(plain_driver) is ScriptedDriver


def test_preregistration_provider_fields_are_appended_at_the_end():
    """Same guard EpisodeSpec has: the refs must stay last, defaulted."""
    import dataclasses as dc

    from plugins.rsi.campaign import Preregistration

    names = [f.name for f in dc.fields(Preregistration)]
    assert names[-3:] == ["env_provider", "policy_provider", "percept_provider"]
    for f in dc.fields(Preregistration)[-3:]:
        assert f.default is None


def test_grasp_height_offset_moves_only_descend_and_close():
    """Round 60: the Sawyer correction must not touch above/lift, and 0.0 must
    reproduce the Panda action bit for bit."""
    import numpy as np

    from governor.env import EpisodeSpec, FrozenPolicy

    obs = {"robot0_eef_pos": np.array([0.0, 0.0, 0.9])}
    plain = FrozenPolicy(EpisodeSpec(seed=1))
    shifted = FrozenPolicy(EpisodeSpec(seed=1, grasp_height_offset=-0.01))
    plain.target = np.array([0.0, 0.0, 0.82])
    shifted.target = np.array([0.0, 0.0, 0.82])
    for phase in ("above", "lift"):
        assert np.array_equal(plain.act(obs, phase), shifted.act(obs, phase)), phase
    for phase in ("descend", "close"):
        assert not np.array_equal(plain.act(obs, phase), shifted.act(obs, phase)), phase
    default = FrozenPolicy(EpisodeSpec(seed=1, grasp_height_offset=0.0))
    default.target = plain.target
    for phase in ("above", "descend", "close", "lift"):
        assert np.array_equal(plain.act(obs, phase), default.act(obs, phase))


def test_sawyer_providers_satisfy_their_contracts():
    from harness.contracts import EnvProvider, PolicyFactory
    from harness.registry import load_provider

    assert isinstance(load_provider("plugins.embodiment_robosuite:sawyer_provider"), EnvProvider)
    assert isinstance(load_provider("plugins.policies:sawyer_scripted_provider"), PolicyFactory)


def test_recovery_actor_honours_the_grasp_height_offset():
    """Round 61: repair must be embodiment-corrected the same way the policy is.

    offset=0.0 reproduces the Panda behaviour bit for bit; a Sawyer offset
    moves descend/close goals and leaves above/lift alone.
    """
    import numpy as np

    from governor.policy import RecoveryActor

    target = np.array([0.0, 0.0, 0.82])
    obs = {"robot0_eef_pos": np.array([0.0, 0.0, 0.9])}
    prog = (("above", 1, 0.0, 0.0), ("descend", 1, 0.0, 0.0),
            ("close", 1, 0.0, 0.0), ("lift", 1, 0.0, 0.0))
    plain = RecoveryActor(prog, target)
    zero = RecoveryActor(prog, target, height_offset=0.0)
    shifted = RecoveryActor(prog, target, height_offset=-0.010)
    acts = {name: (plain.act(obs), zero.act(obs), shifted.act(obs))
            for name in ("above", "descend", "close", "lift")}
    for name, (a, b, c) in acts.items():
        assert np.array_equal(a, b), f"offset=0 changed {name}"
        if name in ("descend", "close"):
            assert not np.array_equal(a, c), f"offset ignored in {name}"
        else:
            assert np.array_equal(a, c), f"offset leaked into {name}"
