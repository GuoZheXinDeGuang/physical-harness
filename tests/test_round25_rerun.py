"""Offline smoke of scripts/round25_rerun.py -- no simulation, no GPU.

gate._run is monkeypatched with a seed-deterministic fake (the
test_rsi_workload trick), so the dev rollout and both held-out paired gates
run on synthetic episodes. QWEN38_BASE_URL points at a port nothing listens
on, so the qwen38 arm exercises the skip path while search + naive_mock still
produce the round25_rerun artifact and their llm_exchange audit rows.
"""

from __future__ import annotations

import json
import socket

import numpy as np
import pytest

import plugins.embodiment_robosuite.features  # noqa: F401  registry population
from harness.definitions import CAPABILITIES
from harness.kernel import Kernel
from plugins.rsi import gate
from plugins.rsi.campaign import Preregistration
from scripts import round25_rerun


class _FakeEnvProvider:
    """Satisfies harness.contracts.EnvProvider; must never actually be called."""

    def make_env(self, spec):
        raise AssertionError("make_env must not run: gate._run is monkeypatched")

    def tasks(self):
        return ("lift",)

    def object_key(self, spec):
        return "cube_pos"

    def success(self, obs, spec, start_z):
        return False


class _FakePolicyFactory:
    def make_driver(self, spec):
        raise AssertionError("make_driver must not run: gate._run is monkeypatched")


class _FakePercept:
    def object_estimate(self, obs, spec, sensor_sd, draw):
        return None


class _FakeExecutor:
    def map(self, fn, items, *, workers):
        return [fn(item) for item in items]


def _kernel() -> Kernel:
    k = Kernel(CAPABILITIES)
    k.provide("embodiment.env", _FakeEnvProvider(), ref="tests.fakes:env")
    k.provide("policy.driver", _FakePolicyFactory(), ref="tests.fakes:policy")
    k.provide("percept.model", _FakePercept(), ref="tests.fakes:percept")
    k.provide("exec.rollouts", _FakeExecutor(), ref="tests.fakes:executor")
    return k


def _fake_run(job):
    """Seed-deterministic stand-in for governed_rollout: even seeds fail
    ungoverned, any governed bundle fixes them. Failing episodes collapse
    finger_gap so both the search and the brief separate cleanly."""
    spec, bundle = job
    failing = spec.seed % 2 == 0
    success = (not failing) or (bundle is not None and bool(bundle.rules))
    return {
        "success": success,
        "fired_at": 0 if bundle is not None and bundle.rules and failing else None,
        "trace": {
            "observable.finger_gap": np.full(80, 0.001 if failing else 0.04),
            "observable.eef_z": np.linspace(1.0, 0.9, 80),
            "observable.gripper_effort": np.zeros(80),
            "observable.joint_speed": np.zeros(80),
        },
    }


@pytest.fixture
def closed_port_env(monkeypatch):
    # Bind-then-close guarantees a refused port for the reachability probe.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    monkeypatch.setenv("QWEN38_BASE_URL", f"http://127.0.0.1:{port}/v1")


def test_offline_run_produces_artifact_and_audit_rows(tmp_path, monkeypatch, closed_port_env):
    monkeypatch.setattr(gate, "_run", _fake_run)
    prereg = Preregistration(
        dev=tuple(range(44000, 44020)), heldout=tuple(range(44200, 44220)),
        percept_noise=0.02, task="lift", policy="scripted",
        critic_budget=0, action_budget=0, recovery_sensor_sd=0.02,
        max_generations=1,
    )

    summary = round25_rerun.run_rerun(prereg, tmp_path / "store", _kernel(), workers=2)

    # qwen38 skipped, the offline arms both landed a rule and a held-out gate.
    assert set(summary["arms"]) == {"search", "naive_mock"}
    assert summary["arena"] == "lift"
    assert summary["dev_block"] == [44000, 44020]
    assert summary["heldout_block"] == [44200, 44220]
    for arm in summary["arms"].values():
        assert arm["rule"] is not None
        assert arm["rule"]["trigger"]["feature"] == "observable.finger_gap"
        assert arm["heldout"]["fixed"] == 10 and arm["heldout"]["broken"] == 0
        assert arm["heldout"]["delta"] == pytest.approx(0.5)

    # The store holds the comparison artifact plus one llm_exchange audit row
    # per naive_mock transport call (search is not a transport and logs none).
    index = [json.loads(line) for line in (tmp_path / "store" / "index.jsonl").open()]
    kinds = [row["kind"] for row in index]
    assert kinds.count("round25_rerun") == 1
    assert kinds.count("llm_exchange") == 1
    assert kinds.count("preregistration") == 1
