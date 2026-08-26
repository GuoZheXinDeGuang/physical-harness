"""Remote-VLA policy card: ``policy.driver`` over a websocket policy server.

**What it serves.** Any policy server speaking the StarVLA/openpi msgpack+
ndarray websocket protocol: first frame after connect is the server's metadata
dict, every subsequent request/response is a msgpack-packed plain dict whose
ndarrays ride the ``{b"__ndarray__", data, dtype, shape}`` extension (pickle
refused by construction). openpi's official pi0.5 ``serve_policy.py`` server is
shape-identical to this boundary; StarVLA's own ``server_policy.py`` is the
reference implementation. The protocol layer here is vendored verbatim from
starVLA@0ed0aad (MIT, three files, zero torch imports) -- see each file's
header for local changes.

**Why a socket.** The VLA stack (torch/flash-attn/transformers, pinned) lives
in its own venv/process behind the server; this side needs only
``websockets + msgpack + numpy`` (``pip install .[policy_remote]``). Same
isolation move as venv-per-sim, applied to the policy seam.

**The handshake gate.** The card's manifest params declare the TRAINING
observation contract -- ``image_size``, ``views`` (camera order), ``chunk``,
``unnorm_key``. At connect, :func:`reconcile` checks that contract against the
server's handshake metadata: a mismatch **raises** (StarVLA's own banner: a
silent train/test gap does not error, it quietly kills the success rate -- we
turn that warning into a mount-time gate). Keys the server does not echo (e.g.
camera order, which StarVLA explicitly refuses to own) land in
``handshake["unverified"]``; the whole handshake record rides on the driver so
the caller can seal it into episode evidence.

**Denormalization never leaves the server**: actions crossing this boundary
are already un-normalized; norm stats stay with the checkpoint on the server
side. The driver only unwraps, caches the chunk, and pops one action per step.

Installed but INACTIVE (``enabled = false``): it claims ``policy.driver``,
which plugins/policies already owns -- one card per seam (model_qwen
precedent). Doctor Tier-A shapes it without any dependency installed;
Tier-B probes the server port and SKIPs loudly when nothing is listening.
"""

from __future__ import annotations

import socket
from collections.abc import Mapping
from typing import Any

import numpy as np

#: manifest param -> the handshake metadata key that echoes it (StarVLA
#: policy_wrapper.metadata key names). ``views`` has no echo upstream -- the
#: server refuses to own camera order -- so it normally lands in "unverified".
_HANDSHAKE_KEYS = {
    "image_size": "training_obs_image_size",
    "views": "camera_views",
    "chunk": "action_chunk_size",
    "unnorm_key": "default_unnorm_key",
}


def _norm(v: Any) -> Any:
    """Tuples/lists compare equal across the msgpack boundary."""
    return [_norm(x) for x in v] if isinstance(v, (list, tuple)) else v


def reconcile(contract: Mapping, metadata: Mapping) -> dict:
    """Check the declared training-observation contract against the server's
    first-frame metadata. Mismatch on any echoed key raises ``ValueError``
    (fail loud at mount, never a silently degraded success rate); keys the
    server does not echo are returned in ``unverified``. The returned record
    is what the caller seals into episode evidence."""
    unverified = []
    for param_key, meta_key in _HANDSHAKE_KEYS.items():
        if param_key not in contract:
            continue
        got = metadata.get(meta_key)
        if got is None:
            unverified.append(param_key)
            continue
        if _norm(contract[param_key]) == _norm(got):
            continue
        # unnorm_key: differing from the server's DEFAULT is fine when the
        # server advertises it -- the client asks for it per request.
        if (param_key == "unnorm_key"
                and contract[param_key] in (metadata.get("available_unnorm_keys") or ())):
            continue
        raise ValueError(
            f"policy_vla_remote handshake mismatch: manifest {param_key}="
            f"{contract[param_key]!r} vs server {meta_key}={got!r} -- train/test "
            f"observation contracts diverge, refusing to mount. Full server "
            f"metadata: {dict(metadata)!r}")
    return {"contract": dict(contract), "metadata": dict(metadata),
            "unverified": unverified}


class RemoteChunkDriver:
    """The frozen driver an episode runs under: one inference per chunk,
    pop one action per ``act``. ``handshake`` rides along for the evidence log.

    ponytail: no action ensembling / sticky gripper / retarget-on-task-change
    yet -- add in this file when a real checkpoint's eval needs them.
    """

    def __init__(self, client: Any, handshake: dict, task: str | None = None) -> None:
        self._client = client
        self.handshake = handshake
        self._task = task
        self._chunk: list = []

    def _infer(self, obs: Mapping) -> list:
        reply = self._client.predict_action(dict(obs))
        if isinstance(reply, Mapping) and reply.get("ok") is False:
            raise RuntimeError(f"policy server error: {reply.get('error')!r}")
        # StarVLA wraps in {"status", "ok", ..., "data": {...}}; openpi returns
        # the payload dict bare. Either way the actions are already un-normalized.
        data = reply.get("data", reply)
        actions = np.asarray(data["actions"])
        if actions.ndim == 3:  # [B, T, D] -> first batch element
            actions = actions[0]
        return [np.asarray(a) for a in actions]

    def act(self, obs: Mapping) -> Any:
        if not self._chunk:
            self._chunk = self._infer(obs)
        return self._chunk.pop(0)


class RemoteVlaPolicy:
    """``harness.contracts.PolicyFactory`` backed by a websocket policy server.

    Construction is dependency-free and offline (Tier-A shapes it anywhere);
    the vendored client is imported and the socket opened lazily at the first
    ``make_driver``/``connect``, where the handshake gate runs.
    """

    def __init__(self, *, host: str = "127.0.0.1", port: int = 8000,
                 api_key: str | None = None, **contract: Any) -> None:
        self._host, self._port, self._api_key = host, port, api_key
        self.contract = contract
        self._client: Any = None
        self.handshake: dict | None = None

    def available(self, timeout: float = 1.0) -> bool:
        """One TCP probe decides whether a server is listening at all --
        plugin_doctor Tier-B SKIPs (not reds) on False, qwen-card style."""
        try:
            with socket.create_connection((self._host, self._port), timeout=timeout):
                return True
        except OSError:
            return False

    def connect(self) -> dict:
        """Open the socket, take the first-frame metadata, run the handshake
        gate. Idempotent; returns the sealed handshake record."""
        if self._client is None:
            from plugins.policy_vla_remote.websocket_policy_client import (
                WebsocketClientPolicy,
            )
            client = WebsocketClientPolicy(host=self._host, port=self._port,
                                           api_key=self._api_key)
            try:
                self.handshake = reconcile(self.contract, client.get_server_metadata())
            except Exception:
                client.close()
                raise
            self._client = client
        assert self.handshake is not None
        return self.handshake

    def make_driver(self, spec: Any) -> RemoteChunkDriver:
        self.connect()
        return RemoteChunkDriver(self._client, self.handshake,
                                 task=getattr(spec, "task", None))


def provider(**params: Any) -> RemoteVlaPolicy:
    return RemoteVlaPolicy(**params)
