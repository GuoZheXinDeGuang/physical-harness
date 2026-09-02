"""G1-G4 end to end: the REAL scripts/harness_runtime.py subprocess serving a tmp
session, briefs submitted through board.store.submit_brief, the REAL
``python -m board.storecli trajectories --out`` export. No simulator: a tmp card
(PH_PLUGINS_EXTRA) binds three test tasks to the fakes below -- an env whose obs
walks the stack stage chain, a stub driver, the stack card's own records/facts --
reached by the runtime by ref string, like every card. The planners: the stack
table planner under a test task name (G1), the same graph with an unsupported
object (G2), planner_vlm over the fake model endpoint (G3).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from board import store as bs
from plugins.task.planner_stack import StackPlanner

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "scripts" / "harness_runtime.py"
SESSION = "session-main"   # the default session: submit_brief needs no booted chain

# --- test-only providers (loaded by the runtime subprocess by ref) -------------


def _budgets() -> tuple[int, int]:
    from plugins.embodiment_robosuite.env import stack_stages
    grasp, place = stack_stages()
    return grasp.budget, grasp.budget + place.budget


def _obs(t: int) -> dict:
    """Stage-chain-walking obs: cubeA raised until the grasp budget, seated after."""
    seated = t > _budgets()[0]
    return {"robot0_gripper_qpos": [0.03, -0.03], "robot0_gripper_qvel": [0.0, 0.0],
            "robot0_joint_vel": [0.0] * 7, "robot0_eef_pos": [0.0, 0.0, 1.0],
            "cubeA_pos": [0.0, 0.0, 0.045 + (0.0 if seated else 0.06)],
            "cubeB_pos": [0.0, 0.0, 0.0]}


class _Handle:
    t = 0

    def reset(self):
        self.t = 0
        return _obs(0)

    def step(self, action):
        self.t += 1
        return _obs(self.t), 0.0, False, {}

    def close(self):
        pass


class _Env:
    def make_env(self, spec):
        return _Handle()

    def tasks(self):
        return ("stack",)

    def object_key(self, spec):
        return "cubeA_pos"

    def success(self, obs, spec, start_z):
        return True

    def terminal_success(self, obs, spec, start_z, env=None):
        return True


class _Driver:
    identity = "e2e:stub"

    def __init__(self):
        self.k = 0

    def observe_once(self, obs):
        pass

    def on_handback(self):
        pass

    @property
    def exhausted(self):
        return self.k >= _budgets()[1]

    def act(self, obs):
        self.k += 1
        return (0.0,) * 7


class _Policy:
    def make_driver(self, spec):
        return _Driver()


class _StackAs:
    """The stack card's table planner under a test task name; ``obj`` swaps the
    object so present(obj) is in no sigma0 fact (G2's unsupported graph)."""
    identity = "e2e:stack_as"

    def __init__(self, obj="cubeA"):
        self.obj = obj

    def plan(self, brief):
        plan = StackPlanner().plan({**brief, "task": "stack"})
        plan["nodes"][0]["args"]["object"] = self.obj
        return plan


def env_provider():
    return _Env()


def policy_provider():
    return _Policy()


def planner_provider():
    return _StackAs()


def unsupported_planner_provider():
    return _StackAs("bottle")


def vlm_planner_provider():
    from plugins.planner_vlm import provider
    return provider(endpoint="plugins.model_endpoint:fake_provider", endpoint_params={})


_CARD = """
[task_bindings.{task}]
env = "test_mission_e2e:env_provider"
policy = "test_mission_e2e:policy_provider"
planner = "test_mission_e2e:{planner}"
catalogue = "plugins.task.planner_stack:CATALOGUE"
records = "plugins.task.planner_stack:SKILL_RECORDS"
initial_facts = "plugins.task.planner_stack:INITIAL_FACTS"
oracles = "plugins.task.planner_stack:ORACLES"
"""
_PLANNERS = {"e2e_stack": "planner_provider",
             "e2e_unsupported": "unsupported_planner_provider",
             "e2e_vlm": "vlm_planner_provider"}
_CANNED = {"goal": "stack cubeA on cubeB",
           "nodes": [{"id": "stack-0", "skill": "stack",
                      "args": {"object": "cubeA", "target": "cubeB"}, "after": []}],
           "verify": [{"after": "stack-0", "predicate": "stack_success"}],
           "rationale": "cubeA and cubeB are present; stack ensures on(cubeA,cubeB)"}


# --- the live runtime ---------------------------------------------------------


def _wait(pred, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


class _Runtime:
    """One real runtime subprocess over ``runs/session-main``. ``card`` /
    ``canned`` / ``env`` let sibling e2e files ride the same boot with their own
    task card, fake-endpoint reply (a list = a reply sequence) and extra env."""

    def __init__(self, runs: Path, card: str | None = None, canned=None,
                 env: dict | None = None, mode: str = "execution"):
        self.runs = runs
        self.session = runs / SESSION
        (runs / "plugins" / "e2e").mkdir(parents=True)
        (runs / "plugins" / "e2e" / "manifest.toml").write_text(
            card if card is not None
            else "".join(_CARD.format(task=t, planner=p) for t, p in _PLANNERS.items()))
        (runs / "canned.json").write_text(json.dumps(_CANNED if canned is None else canned))
        env = {**os.environ, "PYTHONPATH": f"{REPO}:{REPO / 'tests'}",
               "PH_PLUGINS_EXTRA": str(runs / "plugins"),
               "PH_MODEL_ENDPOINT_FAKE": str(runs / "canned.json"), **(env or {})}
        env.pop("MUJOCO_GL", None)   # no headless-GL frames overlay on the fake env
        self.stderr = runs / "runtime.stderr"
        self.proc = subprocess.Popen(
            [sys.executable, str(RUNTIME), "--session-dir", str(self.session),
             "--poll-interval", "0.1", "--mode", mode],
            cwd=str(REPO), env=env, stdout=subprocess.DEVNULL,
            stderr=self.stderr.open("w"))
        _wait(lambda: (self.session / "runtime_status.json").exists() or self.proc.poll() is not None,
              60, "the runtime to boot")
        assert self.proc.poll() is None, self.stderr.read_text()

    def run(self, brief: dict, expect: str = "done") -> tuple[str, list[dict]]:
        """submit_brief -> the brief's chain rows once it is filed (serial runtime)."""
        before = len(bs.chain_rows(self.session))
        res = bs.submit_brief(self.runs, json.dumps(brief), session=SESSION)
        name = res["submitted"]
        where = lambda: [d for d in ("done", "failed", "cancelled")
                         if (self.session / d / name).exists()]
        _wait(lambda: where() or self.proc.poll() is not None, 120, f"{name} to be filed")
        assert where() == [expect], (where(), self.proc.poll(), self.stderr.read_text(),
                                     bs.chain_rows(self.session)[before:])
        return name, bs.chain_rows(self.session)[before:]

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    rt = _Runtime(tmp_path_factory.mktemp("runs"))
    yield rt
    rt.stop()


@pytest.fixture(scope="module")
def g1(runtime):
    return runtime.run({"kind": "task", "task": "e2e_stack", "seed": 41})


def _kinds(rows, kind):
    return [r["data"] for r in rows if r["kind"] == kind]


def test_g1_task_brief_runs_to_a_sealed_legal_episode(g1):
    _, rows = g1
    plans = _kinds(rows, "task.plan")
    assert len(plans) == 1 and plans[0]["legal"] is True and plans[0]["problems"] == []
    # non-vacuous Supported/Covered: facts, objects and the record set were passed
    assert "present(cubeA)" in plans[0]["facts"] and {"cubeA", "cubeB"} <= set(plans[0]["objects"])
    assert plans[0]["visible"] == ["pick", "stack"]
    assert plans[0]["graph"]["nodes"][0]["skill"] == "stack"
    verify = _kinds(rows, "task.verify")
    assert verify == [{"node": "stack-0", "results": {"stack_success": True}}]
    end = _kinds(rows, "task.plan_complete")
    assert len(end) == 1 and end[0]["success"] is True and end[0]["actuations"] == 1
    assert [s["success"] for s in end[0]["nodes"]["stack-0"]["stages"]] == [True, True]
    assert not _kinds(rows, "runtime.task_error")


def test_g2_unsupported_graph_is_refused_and_never_dispatched(runtime):
    _, rows = runtime.run({"kind": "task", "task": "e2e_unsupported", "seed": 7,
                           "max_replans": 1})
    plans = _kinds(rows, "task.plan")
    assert [p["legal"] for p in plans] == [False, False]
    assert any("supported" in m and "present(bottle)" in m for m in plans[0]["problems"])
    assert _kinds(rows, "task.replan_rejected")
    assert _kinds(rows, "task.verify") == []
    end = _kinds(rows, "task.plan_complete")[0]
    assert end["success"] is False and end["actuations"] == 0
    assert {f["kind"] for f in end["faults"]} == {"invalid_plan"}


def test_g3_vlm_planner_over_the_fake_endpoint_validates_and_seals_prompt_sha(runtime):
    _, rows = runtime.run({"kind": "task", "task": "e2e_vlm", "seed": 3})
    plan = _kinds(rows, "task.plan")[0]
    assert plan["legal"] is True, plan["problems"]
    planner = plan["graph"]["planner"]
    assert planner["provider"] == "plugins.model_endpoint:fake_provider"
    assert planner["endpoint"].startswith("fake(") and len(planner["prompt_sha"]) == 64
    assert plan["rationale"] == _CANNED["rationale"]
    assert _kinds(rows, "task.verify") == [{"node": "stack-0", "results": {"stack_success": True}}]
    assert _kinds(rows, "task.plan_complete")[0]["success"] is True


def test_g4_storecli_exports_dev_and_heldout_samples(runtime, g1, tmp_path):
    out = tmp_path / "traj"
    res = subprocess.run(
        [sys.executable, "-m", "board.storecli", "trajectories", SESSION,
         "--runs", str(runtime.runs), "--out", str(out)],
        cwd=str(REPO), capture_output=True, text=True, check=True)
    counts = json.loads(res.stdout)
    dev = [json.loads(l) for l in (out / "dev.jsonl").read_text().splitlines()]
    held = [json.loads(l) for l in (out / "heldout.jsonl").read_text().splitlines()]
    assert counts == {"dev": len(dev), "heldout": len(held)} and len(dev) >= 1
    assert all(s["o"]["role"] == "dev" and s["o"]["role_source"] == "no_store" for s in dev)
    good = [s for s in dev if s["x"]["mission"] == "e2e_stack"]
    assert good and good[0]["o"]["legal"] is True and good[0]["x"]["visible"]
    assert good[0]["o"]["verify"] == {"stack-0": {"stack_success": True}}
    assert good[0]["o"]["success"] is True and good[0]["o"]["seed"] == 41
