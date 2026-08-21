"""Durable log, offline audit, and shadow-replay fidelity."""
import json
import shutil
import zlib
from pathlib import Path

import numpy as np
import pytest

from governor.audit import audit_log, rebuild_views, shadow_replay
from governor.env import EpisodeSpec
from governor.episode_log import EpisodeLog, chain_start, chain_step, write_episode
from governor.governed import Bundle, RecoverySpec, Rule, governed_rollout
from plugins.rsi.stats.search import Trigger

ROOT = Path("runs/_test_log")
TRIG = Trigger("observable.gripper_effort", "gt", 0.050879, 2, 59)


@pytest.fixture(scope="module")
def logged():
    shutil.rmtree(ROOT, ignore_errors=True)
    log = EpisodeLog(ROOT)
    specs = [EpisodeSpec(seed=s, percept_noise=0.020) for s in range(3000, 3012)]
    for spec in specs:
        r = governed_rollout(spec, None)
        write_episode(log, r, {"seed": spec.seed}, "ungoverned")
    yield log, specs
    shutil.rmtree(ROOT, ignore_errors=True)


def test_chain_is_order_and_content_sensitive():
    a = chain_step(chain_step(chain_start(), "x"), "y")
    assert a != chain_step(chain_step(chain_start(), "y"), "x")
    assert a != chain_step(chain_step(chain_start(), "x"), "z")


def test_audit_verifies_every_episode(logged):
    log, _ = logged
    res = audit_log(log)
    assert res.ok, res.broken
    assert res.verified == res.episodes > 0


def test_corrupting_one_value_is_caught(logged):
    log, _ = logged
    rec = [r for r in log.episodes() if "block" in r][0]
    blob = ROOT / "blocks" / f"{rec['block']}.json.z"
    raw = zlib.decompress(blob.read_bytes())
    payload = json.loads(raw)
    payload["columns"]["observable.finger_gap"][10] += 1e-3
    blob.write_bytes(zlib.compress(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(), 6))
    try:
        res = audit_log(log)
        assert not res.ok, "a corrupted stored value must fail the audit"
    finally:
        blob.write_bytes(zlib.compress(raw, 6))
    assert audit_log(log).ok, "restore failed; fixture is dirty"


def test_rebuilt_views_carry_zero_based_steps(logged):
    log, _ = logged
    rec = [r for r in log.episodes() if "block" in r][0]
    views = rebuild_views(log.get_block(rec["block"]), rec["seed"])
    assert views[0].step == 0, "trace index and view step must share one convention"
    assert views[-1].step == len(views) - 1


def test_shadow_replay_predicts_the_live_fire_step_exactly(logged):
    """The log is only useful if replaying it reproduces what really happened.

    Regression for a one-step arming offset: governed_rollout counted steps
    1-based while the search and the traces were 0-based, so every trigger armed
    a step early. It showed up here as 6/40 disagreements.
    """
    log, specs = logged
    bundle = Bundle(rules=(Rule("g1", TRIG, RecoverySpec(sensor_sd=0.020)),),
                    critic_budget=0, action_budget=0)
    records = {r["seed"]: r for r in log.episodes() if "block" in r}
    for spec in specs:
        block = log.get_block(records[spec.seed]["block"])
        predicted = TRIG.fire_step({TRIG.feature: np.asarray(block["columns"][TRIG.feature])})
        live = governed_rollout(spec, bundle)["fired_at"]
        assert predicted == live, f"seed {spec.seed}: shadow={predicted} live={live}"


def test_shadow_replay_scores_without_touching_the_simulator(logged):
    log, _ = logged
    res = shadow_replay(log, TRIG, bundle_sha="ungoverned")
    assert res.n > 0 and 0.0 <= res.recall <= 1.0 and 0.0 <= res.false_positive <= 1.0


def test_chain_math_is_shared_and_unchanged():
    """L1 rung 3: one primitive, two ledgers, and archived logs still verify.

    The golden values were computed with the pre-unification local functions;
    if either moves, every archived episode log's audit breaks silently.
    """
    from harness import events

    assert chain_step is events.chain_step, "episode log grew its own chain again"
    assert chain_start() == "8204c8f48bc01bf20362cae11f58f967eaf90be025750499468b8cdfa6a74044"
    assert chain_step(chain_start(), "abc123") == \
        "a45aa0358b9a7f6e64b44aab89bab1b0a24f6d62a0397fb297f2650df0142ce1"
