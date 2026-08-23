"""R6: plugin_doctor 体检 -- Tier A shape + Tier B per-kind fake-episode smoke.

The acceptance IS this test: the doctor GREENS the fakes card (every base seam,
run on ``harness.fakes``) and the robosuite card (the real-sim tier, gated by
``needs_sim``), and REDDENS a contract-violating provider (Tier A isinstance) and
a non-deterministic percept (Tier B determinism-required). The two red fixtures
are built tiny and inline here -- a manifest pointing a percept mount at a
wrong-shaped provider, and one at the deliberately flaky provider below.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from scripts.plugin_doctor import check

_REPO = Path(__file__).resolve().parent.parent

#: The fakes card: every non-privileged base seam mounted to its null provider.
#: Not a committed plugin dir (it would collide with the real cards in
#: ``discover``) -- it is exactly the doctor's own fixture set, declared here.
_FAKES = """\
[mounts."embodiment.env"]
ref = "harness.fakes:env_provider"
[mounts."policy.driver"]
ref = "harness.fakes:policy_provider"
[mounts."percept.model"]
ref = "harness.fakes:percept_provider"
[mounts."reasoner.proposer"]
ref = "harness.fakes:reasoner_provider"
[mounts."task.planner"]
ref = "harness.fakes:task_planner_provider"
[mounts."graph.skill"]
ref = "harness.fakes:skill_graph_provider"
[mounts."graph.scene"]
ref = "harness.fakes:scene_graph_provider"
"""

_SEAMS = {"embodiment.env", "policy.driver", "percept.model", "reasoner.proposer",
          "task.planner", "graph.skill", "graph.scene"}


class _NonDetPercept:
    """Right shape (has ``object_estimate``), wrong discipline: a fresh draw every
    call, so the determinism-required smoke sees two different outputs."""

    def object_estimate(self, obs, spec, sensor_sd, draw):
        return [random.random(), random.random(), 0.0]


def nondet_percept_provider():  # referenced by ref from the red fixture manifest
    return _NonDetPercept()


def _card(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "manifest.toml").write_text(body)
    return d


def _fails(rep) -> list[tuple[str, str, str]]:
    return [(r.tier, r.name, r.detail) for r in rep.results if r.status == "FAIL"]


def test_doctor_greens_the_fakes_card(tmp_path):
    rep = check(_card(tmp_path, "fakes", _FAKES))
    assert rep.green, _fails(rep)
    # every seam cleared Tier A (shape) AND ran a Tier B smoke that passed.
    passed_b = {r.name for r in rep.results if r.tier == "B" and r.status == "PASS"}
    assert passed_b == _SEAMS, passed_b


def test_doctor_reddens_a_contract_violating_provider(tmp_path):
    # a scene-graph provider mounted as the percept seam: right nothing, wrong shape.
    body = '[mounts."percept.model"]\nref = "harness.fakes:scene_graph_provider"\n'
    rep = check(_card(tmp_path, "bad_shape", body))
    assert not rep.green
    a = [r for r in rep.results if r.tier == "A" and r.name == "percept.model"]
    assert a and a[0].status == "FAIL" and "PerceptModel" in a[0].detail, _fails(rep)


def test_doctor_reddens_a_non_deterministic_percept(tmp_path):
    body = ('[mounts."percept.model"]\n'
            'ref = "tests.test_plugin_doctor:nondet_percept_provider"\n')
    rep = check(_card(tmp_path, "flaky", body))
    assert not rep.green
    # it CLEARS Tier A (correct shape) -- the redness is determinism, Tier B.
    a = [r for r in rep.results if r.tier == "A" and r.name == "percept.model"]
    assert a and a[0].status == "PASS"
    b = [r for r in rep.results if r.tier == "B" and r.name == "percept.model"]
    assert b and b[0].status == "FAIL" and "non-deterministic" in b[0].detail


def test_doctor_refuses_actuation_real(tmp_path):
    body = ('actuation = "real"\n[mounts."embodiment.env"]\n'
            'ref = "harness.fakes:env_provider"\n')
    rep = check(_card(tmp_path, "real_arm", body))
    assert not rep.green
    assert any(r.name == "actuation" and r.status == "FAIL" for r in rep.results)


def test_doctor_skips_a_task_only_card(tmp_path):
    # a task-binding card declares no mounts -- nothing to shape-check, still green.
    body = ('[task_bindings.toy]\npolicy = "p:x"\nplanner = "p:x"\n'
            'catalogue = "p:C"\noracles = "p:O"\n')
    rep = check(_card(tmp_path, "task_only", body))
    assert rep.green
    assert any(r.status == "SKIP" for r in rep.results)


@pytest.mark.robosuite
def test_doctor_greens_the_robosuite_card():
    rep = check(_REPO / "plugins" / "embodiment_robosuite")
    assert rep.green, _fails(rep)
    got = {(r.tier, r.name, r.status) for r in rep.results}
    # Tier A shape on both declared seams, Tier B real-sim smoke on both, and the
    # percept smoke passed the determinism-required policy.
    assert ("A", "embodiment.env", "PASS") in got
    assert ("A", "percept.model", "PASS") in got
    assert ("B", "embodiment.env", "PASS") in got
    assert ("B", "percept.model", "PASS") in got
