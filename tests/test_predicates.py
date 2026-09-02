"""provides fold + harness.predicates: evaluate is three-valued, audit gate is parametric."""

from __future__ import annotations

import pytest

from harness.manifest import discover
from harness.predicates import audit_gate, evaluate, records


def _card(root, name, body):
    (root / name).mkdir()
    (root / name / "manifest.toml").write_text(body)


def test_discover_folds_provides_and_committed_cards_declare_predicates(tmp_path):
    _card(tmp_path, "card", '[[provides]]\nkind = "predicate"\nname = "near"\n'
          'ref = "m:near"\nreads = ["a"]\nargs = ["x"]\n'
          '[[provides]]\nkind = "planner"\nref = "m:plan"\n')
    reg = discover(tmp_path)
    assert reg.provides == ({"kind": "predicate", "ref": "m:near", "name": "near",
                             "plugin": "card", "reads": ("a",), "args": ("x",)},
                            {"kind": "planner", "ref": "m:plan", "name": "plan", "plugin": "card"})
    recs = records()
    assert recs["lifted"].reads == ("obs", "spec", "start_z")
    assert "embodiment_robocasa" in recs["fridge_is_open"].bindings


def test_unknown_provides_kind_fails_loud(tmp_path):
    _card(tmp_path, "card", '[[provides]]\nkind = "widget"\nref = "m:x"\n')
    with pytest.raises(ValueError, match="widget"):
        discover(tmp_path)
    (tmp_path / "b").mkdir()
    _card(tmp_path / "b", "card", '[[provides]]\nkind = "predicate"\nref = "m:x"\n')
    with pytest.raises(ValueError, match="reads"):
        discover(tmp_path / "b")


def test_evaluate_is_none_on_missing_key_else_bool(tmp_path):
    _card(tmp_path, "card", '[[provides]]\nkind = "predicate"\nname = "lifted"\n'
          'ref = "plugins.embodiment_robosuite.env:lifted_pred"\nreads = ["obs", "spec", "start_z"]\n')
    recs = records(discover(tmp_path))
    assert evaluate("lifted()", {"obs": {}}, recs=recs) is None
    import numpy as np

    from harness.spec import EpisodeSpec
    from plugins.embodiment_robosuite.env import object_key
    spec = EpisodeSpec(seed=0, task="lift")
    obs = {object_key(spec): np.array([0, 0, 1.0]), "robot0_gripper_qpos": np.array([0.02, -0.02])}
    assert evaluate("lifted()", {"obs": obs, "spec": spec, "start_z": 0.8}, recs=recs) is True
    obs[object_key(spec)] = np.array([0, 0, 0.8])
    assert evaluate("lifted()", {"obs": obs, "spec": spec, "start_z": 0.8}, recs=recs) is False


def test_audit_gate_rejects_near_always_true_predicate():
    always = {"n": 100, "tp": 50, "fn": 0, "fp": 49, "tn": 1}
    ok, reasons = audit_gate(always, th_sens=0.9, th_spec=0.9, eps=0.05)
    assert not ok and any("specificity" in r for r in reasons)
    ok, reasons = audit_gate({"n": 100, "tp": 45, "fn": 5, "fp": 3, "tn": 47}, 0.8, 0.8, 0.05)
    assert ok and reasons == []
