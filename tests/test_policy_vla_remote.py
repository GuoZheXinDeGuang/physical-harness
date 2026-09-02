"""The remote-VLA policy card: codec round-trip, handshake gate, chunk driver.

The card's own logic (reconcile + driver + TCP probe) is numpy/stdlib-only and
runs in the base lane. The vendored protocol layer (msgpack+ndarray codec, the
websocket client/server pair) needs the `policy_remote` extra -- those tests
skip loudly when `msgpack`/`websockets` are absent, so the base lane stays
green on a card-absent machine.
"""

from __future__ import annotations

import shutil
import socket
import threading

import numpy as np
import pytest

from plugins.policy_vla_remote import (
    RemoteChunkDriver, RemoteVlaPolicy, checkpoint_sha, provider, reconcile,
)

_METADATA = {
    "training_obs_image_size": [224, 224],
    "action_chunk_size": 10,
    "default_unnorm_key": "libero_spatial_no_noops",
    "available_unnorm_keys": ["libero_spatial_no_noops", "libero_object_no_noops"],
}

_CONTRACT = {"image_size": [224, 224], "views": ["base_0_rgb", "left_wrist_0_rgb"],
             "chunk": 10, "unnorm_key": "libero_spatial_no_noops"}


# ── the handshake gate ───────────────────────────────────────────────────────

def test_reconcile_matching_contract_seals_the_metadata():
    rec = reconcile(_CONTRACT, _METADATA)
    assert rec["metadata"] == _METADATA
    assert rec["contract"] == _CONTRACT
    # views has no handshake echo (the server refuses to own camera order).
    assert rec["unverified"] == ["views"]


def test_reconcile_mismatch_fails_loud():
    with pytest.raises(ValueError, match=r"image_size.*\[256, 256\].*\[224, 224\]"):
        reconcile(dict(_CONTRACT, image_size=[256, 256]), _METADATA)
    with pytest.raises(ValueError, match="chunk"):
        reconcile(dict(_CONTRACT, chunk=50), _METADATA)


def test_reconcile_tuple_vs_list_is_not_a_mismatch():
    # msgpack turns tuples into lists; the gate must not red on that.
    md = dict(_METADATA, training_obs_image_size=(224, 224))
    assert "views" in reconcile(_CONTRACT, md)["unverified"]


def test_reconcile_non_default_unnorm_key_passes_when_advertised():
    rec = reconcile(dict(_CONTRACT, unnorm_key="libero_object_no_noops"), _METADATA)
    assert rec["unverified"] == ["views"]
    with pytest.raises(ValueError, match="unnorm_key"):
        reconcile(dict(_CONTRACT, unnorm_key="not_a_dataset"), _METADATA)


def test_reconcile_unechoed_keys_land_in_unverified():
    rec = reconcile(_CONTRACT, {})  # openpi servers often send empty metadata
    assert rec["unverified"] == ["image_size", "views", "chunk", "unnorm_key"]


def test_reconcile_gates_checkpoint_identity_when_declared():
    """The four contract keys cannot tell two pi0.5 runs of the same task apart
    -- they share image_size/views/chunk/unnorm_key by construction. Declaring
    `checkpoint_sha` gates the WEIGHTS: the run the manifest names mounts, the
    other one is refused instead of quietly serving a different executor."""
    run_a = dict(_METADATA, checkpoint_sha="a" * 64, checkpoint_path="/w/pi05_run_A")
    run_b = dict(_METADATA, checkpoint_sha="b" * 64, checkpoint_path="/w/pi05_run_B")
    pinned = dict(_CONTRACT, checkpoint_sha="a" * 64)
    assert reconcile(pinned, run_a)["unverified"] == ["views"]  # digest matched
    with pytest.raises(ValueError, match="different weights"):
        reconcile(pinned, run_b)


def test_reconcile_identity_is_opt_in():
    """Bypass by design: a manifest that declares no digest is not gated on one,
    so a card mounts against a stock openpi server (which echoes {}). The swap
    is then merely *recorded* -- which is why a paired comparison that means to
    attribute a delta to this executor has to declare the digest."""
    run_b = dict(_METADATA, checkpoint_sha="b" * 64)
    rec = reconcile(_CONTRACT, run_b)  # no checkpoint_sha in the contract
    assert rec["unverified"] == ["views"]
    assert rec["metadata"]["checkpoint_sha"] == "b" * 64  # recorded, not gated
    assert "checkpoint_sha" not in rec["contract"]  # ...and visibly not claimed


def test_reconcile_declared_identity_with_no_echo_refuses():
    """The identity key does NOT fall through to `unverified`: a server that
    stays silent about its weights is indistinguishable from one serving the
    wrong ones, so an unproven identity fails closed."""
    with pytest.raises(ValueError, match="handshake gap.*echoes no checkpoint_sha"):
        reconcile(dict(_CONTRACT, checkpoint_sha="a" * 64), _METADATA)
    with pytest.raises(ValueError, match="checkpoint_sha"):
        reconcile(dict(_CONTRACT, checkpoint_sha="a" * 64), {})  # openpi default


def test_reconcile_absent_echo_beats_a_diverging_contract():
    """For the OBSERVATION-CONTRACT keys, an omitted echo is still skipped, not
    refused -- a partial echo gates only what it happens to cover. Here the
    server is a chunk-10 checkpoint, the manifest declares chunk=50, and the
    mount still passes. Deliberate: `views` has no echo upstream at all and
    openpi servers advertise little, so strictness here would make the card
    unmountable. Only `checkpoint_sha` fails closed on silence."""
    rec = reconcile(dict(_CONTRACT, chunk=50), {"training_obs_image_size": [224, 224]})
    assert rec["unverified"] == ["views", "chunk", "unnorm_key"]


# ── the chunk driver (stub client: no network, no msgpack) ───────────────────

class _StubClient:
    """Counts inferences; answers in the StarVLA envelope or the bare openpi
    shape depending on `bare`."""

    def __init__(self, chunk: np.ndarray, bare: bool = False):
        self.chunk, self.bare, self.calls = chunk, bare, 0

    def predict_action(self, query):
        self.calls += 1
        payload = {"actions": self.chunk}
        return payload if self.bare else {
            "status": "ok", "ok": True, "type": "inference_result",
            "request_id": "default", "data": payload}


def test_driver_one_inference_per_chunk():
    chunk = np.arange(12, dtype=np.float32).reshape(1, 4, 3)  # [B, T, D]
    client = _StubClient(chunk)
    driver = RemoteChunkDriver(client, handshake={"unverified": []})
    acts = [driver.act({"obs": 1}) for _ in range(4)]
    assert client.calls == 1  # one server round-trip served the whole chunk
    np.testing.assert_array_equal(np.stack(acts), chunk[0])
    driver.act({"obs": 1})
    assert client.calls == 2  # chunk exhausted -> re-infer


class _SeqClient:
    """Answers a different chunk per call, so which inference an action came
    from is readable off the value."""

    def __init__(self, chunks):
        self.chunks, self.calls = [np.asarray(c) for c in chunks], 0

    def predict_action(self, query):
        c = self.chunks[min(self.calls, len(self.chunks) - 1)]
        self.calls += 1
        return {"actions": c}


def test_replan_every_re_infers_mid_chunk_and_drops_the_rest():
    """k=2 of a 4-long chunk: two actions from each inference, the stale tail
    thrown away. The whole point is that step 2 is computed from the world step
    1 produced, not predicted before it happened."""
    a = np.array([[0.0], [1.0], [2.0], [3.0]])
    b = np.array([[10.0], [11.0], [12.0], [13.0]])
    client = _SeqClient([a, b])
    driver = RemoteChunkDriver(client, handshake={}, replan_every=2)
    got = [float(driver.act({})[0]) for _ in range(4)]
    assert got == [0.0, 1.0, 10.0, 11.0]  # a[2:], b[2:] never executed
    assert client.calls == 2 == driver.calls


def test_default_driver_is_untouched_by_the_new_knobs():
    """Off by default: no re-query, no ensembling, one inference per chunk --
    the behaviour every number sealed before these knobs existed was produced
    under."""
    client = _SeqClient([np.arange(4.0).reshape(4, 1),
                         np.arange(10.0, 14.0).reshape(4, 1)])
    driver = RemoteChunkDriver(client, handshake={})
    got = [float(driver.act({})[0]) for _ in range(5)]
    assert got == [0.0, 1.0, 2.0, 3.0, 10.0]
    assert client.calls == 2


def test_ensemble_averages_continuous_dims_toward_the_newest():
    two = np.zeros((2, 2))
    two[:, 0] = [0.0, 0.0]
    four = np.full((2, 2), 4.0)
    driver = RemoteChunkDriver(_SeqClient([two, four]), handshake={},
                               replan_every=1, ensemble=0.0)  # flat mean
    assert driver.act({})[0] == 0.0        # only one chunk live yet
    assert driver.act({})[0] == 2.0        # mean(0, 4)
    driver = RemoteChunkDriver(_SeqClient([two, four]), handshake={},
                               replan_every=1, ensemble=10.0)  # newest only
    driver.act({})
    assert driver.act({})[0] == pytest.approx(4.0, abs=1e-3)


def test_ensemble_never_averages_a_discrete_dimension():
    """The dimension the diagnosis blames is a two-valued switch. Averaging a
    chunk that says "drive the base" with one that says "move the arm" invents a
    value neither asked for; a majority vote invents a different one, and it
    censors the minority decision. Dim 1 here is that switch: it must come out
    +1 (the freshest chunk's answer), never 0."""
    minus = np.array([[1.0, -1.0], [1.0, -1.0]])
    plus = np.array([[3.0, +1.0], [3.0, +1.0]])
    driver = RemoteChunkDriver(_SeqClient([minus, plus]), handshake={},
                               replan_every=1, ensemble=0.0, discrete_dims=[1])
    driver.act({})
    out = driver.act({})
    assert out[0] == 2.0    # continuous dim: averaged
    assert out[1] == 1.0    # discrete dim: the newest chunk's decision, intact

    naive = RemoteChunkDriver(_SeqClient([minus, plus]), handshake={},
                              replan_every=1, ensemble=0.0)
    naive.act({})
    assert naive.act({})[1] == 0.0  # ...which is what NOT declaring it costs


def test_ensemble_without_replan_refuses_to_mount_as_a_no_op():
    with pytest.raises(ValueError, match="anything to ensemble"):
        RemoteChunkDriver(_SeqClient([np.zeros((2, 2))]), handshake={}, ensemble=0.5)


def test_reset_drops_buffered_actions_at_a_handover():
    client = _SeqClient([np.arange(4.0).reshape(4, 1),
                         np.arange(10.0, 14.0).reshape(4, 1)])
    driver = RemoteChunkDriver(client, handshake={})
    driver.act({})
    driver.reset()
    assert float(driver.act({})[0]) == 10.0  # re-inferred, not the stale tail


def test_execution_knobs_are_sealed_beside_the_contract_not_inside_it():
    """A record has to say which execution policy produced its numbers -- but
    not by pretending the server verified one."""
    prov = provider(port=1, replan_every=2, ensemble=0.25, discrete_dims=[4, 11],
                    **_CONTRACT)
    assert "replan_every" not in prov.contract  # not a training-contract key
    assert prov.execution == {"replan_every": 2, "ensemble": 0.25,
                              "discrete_dims": [4, 11]}


def test_driver_accepts_bare_openpi_reply():
    driver = RemoteChunkDriver(_StubClient(np.ones((2, 7)), bare=True), handshake={})
    assert driver.act({}).shape == (7,)


def test_driver_raises_on_server_error_envelope():
    class _Err:
        def predict_action(self, query):
            return {"status": "error", "ok": False, "error": {"message": "boom"}}

    with pytest.raises(RuntimeError, match="boom"):
        RemoteChunkDriver(_Err(), handshake={}).act({})


# ── the factory shape + doctor degradation ───────────────────────────────────

def test_provider_is_a_policy_factory_and_probe_is_offline():
    from harness.contracts import PolicyFactory
    prov = provider(**{"image_size": [224, 224], "chunk": 10, "port": 1})
    assert isinstance(prov, PolicyFactory)
    assert prov.available(timeout=0.2) is False  # nothing listens on port 1


def test_doctor_skips_the_smoke_when_no_server_listens(tmp_path):
    from scripts.plugin_doctor import check
    d = tmp_path / "remote"
    d.mkdir()
    (d / "manifest.toml").write_text(
        '[mounts."policy.driver"]\n'
        'ref = "plugins.policy_vla_remote:provider"\n'
        'params = { image_size = [224, 224], chunk = 10, port = 1 }\n')
    rep = check(d)
    assert rep.green, [(r.name, r.detail) for r in rep.results if r.status == "FAIL"]
    a = [r for r in rep.results if r.tier == "A" and r.name == "policy.driver"]
    assert a and a[0].status == "PASS"  # shape gates with zero deps installed
    b = [r for r in rep.results if r.tier == "B" and r.name == "policy.driver"]
    assert b and b[0].status == "SKIP" and "unreachable" in b[0].detail


# ── the vendored protocol layer (needs the policy_remote extra) ──────────────

def test_codec_round_trips_ndarrays_and_refuses_object_dtype():
    pytest.importorskip("msgpack", reason="policy_remote extra not installed")
    from plugins.policy_vla_remote import msgpack_numpy

    payload = {"actions": np.arange(30, dtype=np.float32).reshape(1, 10, 3),
               "state": np.array([0.1, 0.2], dtype=np.float64),
               "prompt": "pick up the bowl", "step": 7}
    out = msgpack_numpy.unpackb(msgpack_numpy.packb(payload))
    np.testing.assert_array_equal(out["actions"], payload["actions"])
    assert out["actions"].dtype == np.float32 and out["state"].dtype == np.float64
    assert out["prompt"] == "pick up the bowl" and out["step"] == 7
    with pytest.raises(ValueError, match="Unsupported dtype"):
        msgpack_numpy.packb({"bad": np.array([object()])})


def test_live_socket_handshake_and_inference_round_trip():
    pytest.importorskip("websockets", reason="policy_remote extra not installed")
    pytest.importorskip("msgpack", reason="policy_remote extra not installed")
    from plugins.policy_vla_remote.websocket_policy_server import WebsocketPolicyServer

    class _Policy:
        def predict_action(self, **payload):
            return {"actions": np.zeros((1, 10, 7), dtype=np.float32)}

    with socket.socket() as s:  # grab a free port
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = WebsocketPolicyServer(_Policy(), host="127.0.0.1", port=port,
                                   metadata=_METADATA)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    factory = RemoteVlaPolicy(host="127.0.0.1", port=port, **_CONTRACT)
    driver = factory.make_driver(spec=None)
    hs = driver.handshake()   # sealed for evidence, normalized
    assert hs["transport"] == "ssp" and hs["meta"]["metadata"] == _METADATA
    assert driver.act({"prompt": "x"}).shape == (7,)

    # and the gate: a second client with a diverging contract must NOT mount.
    bad = RemoteVlaPolicy(host="127.0.0.1", port=port,
                          **dict(_CONTRACT, image_size=[256, 256]))
    with pytest.raises(ValueError, match="handshake mismatch"):
        bad.connect()


def _fake_checkpoint(root, *, weights=b"W", stats=b"S", opt=b"O"):
    """An orbax-shaped checkpoint: served bytes, plus optimizer state beside them."""
    (root / "params").mkdir(parents=True)
    (root / "params" / "p.msgpack").write_bytes(weights)
    (root / "assets" / "robocasa").mkdir(parents=True)
    (root / "assets" / "robocasa" / "norm_stats.json").write_bytes(stats)
    (root / "train_state").mkdir()
    (root / "train_state" / "opt.msgpack").write_bytes(opt)
    return root


def test_checkpoint_sha_survives_reclaiming_disk(tmp_path):
    """Deleting train_state/ must NOT move the identity.

    Optimizer state decides the next training step, never a response, and it is
    half a 9 GB checkpoint -- so it is the first thing pruned when the disk
    fills. A digest that moved when it went would force every manifest that
    declared it to be re-declared for weights nobody touched.
    """
    a = _fake_checkpoint(tmp_path / "a")
    before = checkpoint_sha(a)
    shutil.rmtree(a / "train_state")
    assert checkpoint_sha(a) == before

    # ... and it is not blind: different optimizer state, same served bytes,
    # same digest -- but different SERVED bytes must differ.
    b = _fake_checkpoint(tmp_path / "b", opt=b"different-optimizer-state")
    assert checkpoint_sha(b) == before


def test_checkpoint_sha_moves_when_norm_stats_move(tmp_path):
    """Same weights, different norm stats, is a different policy.

    Un-normalization runs on every response, so identical params against
    different stats return different actions. Digesting params alone would call
    those one checkpoint.
    """
    a = _fake_checkpoint(tmp_path / "a")
    b = _fake_checkpoint(tmp_path / "b", stats=b"rescaled")
    assert checkpoint_sha(a) != checkpoint_sha(b)


def test_checkpoint_sha_refuses_an_empty_claim(tmp_path):
    """A digest over nothing would be a stable, meaningless string that mounts."""
    (tmp_path / "train_state").mkdir(parents=True)
    with pytest.raises(ValueError, match="nothing servable"):
        checkpoint_sha(tmp_path)
