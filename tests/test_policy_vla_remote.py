"""The remote-VLA policy card: codec round-trip, handshake gate, chunk driver.

The card's own logic (reconcile + driver + TCP probe) is numpy/stdlib-only and
runs in the base lane. The vendored protocol layer (msgpack+ndarray codec, the
websocket client/server pair) needs the `policy_remote` extra -- those tests
skip loudly when `msgpack`/`websockets` are absent, so the base lane stays
green on a card-absent machine.
"""

from __future__ import annotations

import socket
import threading

import numpy as np
import pytest

from plugins.policy_vla_remote import RemoteChunkDriver, RemoteVlaPolicy, provider, reconcile

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
    assert driver.handshake["metadata"] == _METADATA  # sealed for evidence
    assert driver.act({"prompt": "x"}).shape == (7,)

    # and the gate: a second client with a diverging contract must NOT mount.
    bad = RemoteVlaPolicy(host="127.0.0.1", port=port,
                          **dict(_CONTRACT, image_size=[256, 256]))
    with pytest.raises(ValueError, match="handshake mismatch"):
        bad.connect()
