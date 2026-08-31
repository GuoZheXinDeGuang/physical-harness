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

**Checkpoint identity.** The observation contract does not say WHICH weights
are behind the socket -- two pi0.5 runs of the same task share ``image_size``,
``views``, ``chunk`` and ``unnorm_key`` by construction, so a swapped checkpoint
passes a contract-only gate. A manifest that also declares ``checkpoint_sha``
gates the weights themselves, and there it fails CLOSED: a server that does not
echo the digest is refused, not filed under ``unverified``. Identity is opt-in
(omit the param and nothing is checked) because a stock openpi server echoes
``{}`` -- see :data:`_IDENTITY_KEY` for the digest the server side must send.

**Denormalization never leaves the server**: actions crossing this boundary
are already un-normalized; norm stats stay with the checkpoint on the server
side. The driver only unwraps, caches the chunk, and pops one action per step.

Installed but INACTIVE (``enabled = false``): it claims ``policy.driver``,
which plugins/policies already owns -- one card per seam (model_qwen
precedent). Doctor Tier-A shapes it without any dependency installed;
Tier-B probes the server port and SKIPs loudly when nothing is listening.
"""

from __future__ import annotations

import hashlib
import socket
from collections.abc import Mapping
from pathlib import Path
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
    "checkpoint_sha": "checkpoint_sha",
}

#: The identity key: WHICH weights are behind the socket, as opposed to which
#: observation contract they were trained under. The serving wrapper must echo
#: ``metadata["checkpoint_sha"]`` = lowercase sha256 hexdigest (64 chars) over
#: the checkpoint's parameter files -- walk the checkpoint directory, sort by
#: POSIX relative path, and feed ``relpath.encode() + b"\0" + file_bytes`` of
#: each file into one sha256. Same move as ``plugins/policies/bc.py``'s
#: ``MLPPolicy.sha()``: hash the actual weight bytes, never a path or a run name
#: (those are renameable, and a SkillRecord's identity claim has to survive a
#: rename). The gate compares the string the manifest declares against the
#: string the server sends -- it does not verify the digest was computed that
#: way, so the reduction above is the contract both sides must agree on.
_IDENTITY_KEY = "checkpoint_sha"


def checkpoint_sha(root: str | Path) -> str:
    """THE digest both sides of the socket must agree on -- the executable form
    of the :data:`_IDENTITY_KEY` contract above, so the gate and the echo cannot
    drift apart into two implementations of one prose paragraph.

    Serving wrappers call this to fill ``metadata["checkpoint_sha"]``; operators
    call it to fill the manifest param. Stdlib-only, so the harness venv computes
    it with zero deps and a model venv computes it with none of ours::

        python -c "from plugins.policy_vla_remote import checkpoint_sha; \\
                   print(checkpoint_sha('/path/to/checkpoints/run/199'))"

    It digests **what determines the answer**, not the whole tree: the weights
    and the normalization assets. An orbax ``train_state/`` is optimizer state
    -- it decides the next training step, never a response -- and it is half the
    bytes, so it is the first thing deleted to reclaim disk. Hashing it would
    move the identity of weights that did not change, and this repo would rather
    a digest survive housekeeping than record a training moment. ``assets/`` IS
    hashed: it carries the norm stats, and identical weights un-normalized
    against different stats return different actions, which is a different
    policy by the only definition that matters here.
    """
    root = Path(root)
    served = [d for d in (root / "params", root / "assets") if d.exists()]
    if not served:
        raise ValueError(
            f"no params/ or assets/ under {root} -- nothing servable to digest, "
            f"and an identity claim over an empty directory would be a lie")
    files = sorted((p.relative_to(root).as_posix()
                    for d in served for p in d.rglob("*") if p.is_file()))
    h = hashlib.sha256()
    for rel in files:
        h.update(rel.encode())
        h.update(b"\0")
        with open(root / rel, "rb") as f:
            while chunk := f.read(1 << 20):  # 9 GB checkpoints do not fit in RAM
                h.update(chunk)
    return h.hexdigest()


def _norm(v: Any) -> Any:
    """Tuples/lists compare equal across the msgpack boundary."""
    return [_norm(x) for x in v] if isinstance(v, (list, tuple)) else v


def reconcile(contract: Mapping, metadata: Mapping) -> dict:
    """Check the declared training-observation contract against the server's
    first-frame metadata. Mismatch on any echoed key raises ``ValueError``
    (fail loud at mount, never a silently degraded success rate); contract keys
    the server does not echo are returned in ``unverified``, but a declared
    ``checkpoint_sha`` the server does not echo raises. The returned record is
    what the caller seals into episode evidence.

    A param the manifest omits is not gated at all -- including the identity
    key, which is deliberately opt-in. A stock openpi ``serve_policy.py`` echoes
    ``{}``, so a mandatory digest would make every unwrapped server unmountable
    and push operators to declare a digest nobody computes; an absent identity
    claim is honest silence, a fabricated one is a lie in the SkillRecord. The
    sealed record shows which keys were declared, so "this mount proved nothing
    about its weights" stays readable downstream -- deciding that a given paired
    comparison REQUIRES a gated identity belongs to whoever seals the record,
    not to a websocket client.
    """
    unverified = []
    for param_key, meta_key in _HANDSHAKE_KEYS.items():
        if param_key not in contract:
            continue
        got = metadata.get(meta_key)
        if got is None:
            # Absent echo is tolerated for the OBSERVATION-CONTRACT keys only:
            # servers advertise little (openpi's default metadata is {}) and
            # `views` has no echo upstream at all, so refusing there would make
            # the card unmountable. The identity key is the opposite case -- it
            # is declared precisely to be answered, and "nobody answered" is
            # indistinguishable from "the wrong weights answered". Fail closed:
            # an unproven identity must not mount, or every paired number
            # attributed to this executor rests on an empty claim.
            if param_key == _IDENTITY_KEY:
                raise ValueError(
                    f"policy_vla_remote handshake gap: manifest declares "
                    f"{param_key}={contract[param_key]!r} but the server echoes "
                    f"no {meta_key} -- cannot prove which checkpoint is behind "
                    f"the socket, refusing to mount. Full server metadata: "
                    f"{dict(metadata)!r}")
            unverified.append(param_key)
            continue
        if _norm(contract[param_key]) == _norm(got):
            continue
        # unnorm_key: differing from the server's DEFAULT is fine when the
        # server advertises it -- the client asks for it per request.
        if (param_key == "unnorm_key"
                and contract[param_key] in (metadata.get("available_unnorm_keys") or ())):
            continue
        why = ("the server is serving different weights than the manifest claims"
               if param_key == _IDENTITY_KEY else
               "train/test observation contracts diverge")
        raise ValueError(
            f"policy_vla_remote handshake mismatch: manifest {param_key}="
            f"{contract[param_key]!r} vs server {meta_key}={got!r} -- {why}, "
            f"refusing to mount. Full server metadata: {dict(metadata)!r}")
    return {"contract": dict(contract), "metadata": dict(metadata),
            "unverified": unverified}


class RemoteChunkDriver:
    """The frozen driver an episode runs under: infer a chunk, pop one action
    per ``act``. ``handshake`` rides along for the evidence log.

    **Default: drain the whole chunk** (one inference per ``chunk`` steps, the
    rest open loop) -- unchanged, so numbers sealed before these knobs existed
    keep meaning what they said. The two knobs below are opt-in, both off unless
    a manifest sets them:

    ``replan_every=k``
        execute only the first ``k`` actions of a chunk, then re-infer from the
        observation that actually resulted. ``k=1`` is closed loop every step.
        Cost is linear in inference calls, and the control loop has a deadline
        -- measure before choosing (``scripts/probe_pi05_rollout.py`` records
        ``inference_calls`` and wall seconds per episode).

    ``ensemble=m``
        chunks overlap once ``k < chunk``, so several inferences predict the
        same timestep; average them, weight ``exp(-m * age_in_inferences)``
        toward the newest (``m=0`` is a flat mean). Variance reduction over the
        server's sampling noise -- openpi draws a fresh noise key per request,
        so two chunks over the same timestep are two samples, not one answer.
        Requires ``replan_every``: without overlap there is nothing to average,
        and silently averaging one chunk with itself would be a knob that reads
        as enabled and does nothing.

    ``discrete_dims=(i, ...)``
        the action dimensions ensembling must NOT average. A mode switch or a
        gripper command is a decision between two values, not a quantity: the
        mean of a chunk saying +1 and a chunk saying -1 is 0, which is neither
        chunk's intent and which the controller reads as a third thing. Snapping
        the mean back to a sign would be no better -- that is a majority vote
        over stale predictions, and it censors exactly the rare minority
        decision (this checkpoint commands base mode on ~8% of steps) that the
        newest observation is best placed to make. So these dimensions take the
        freshest chunk's value verbatim while the continuous ones are averaged.
    """

    def __init__(self, client: Any, handshake: dict, task: str | None = None, *,
                 replan_every: int | None = None, ensemble: float | None = None,
                 discrete_dims: Any = ()) -> None:
        if ensemble is not None and replan_every is None:
            raise ValueError(
                "policy_vla_remote: ensemble needs replan_every < chunk to have "
                "anything to ensemble -- draining a chunk before re-inferring "
                "leaves no timestep predicted twice")
        self._client = client
        self.handshake = handshake
        self._task = task
        self._k = replan_every
        self._m = ensemble
        self._discrete = tuple(int(d) for d in discrete_dims)
        self._chunks: list[list] = []   # live chunks, oldest first, heads aligned
        self._since = 0                 # steps executed since the last inference
        self.calls = 0                  # inferences this driver has made

    def reset(self) -> None:
        """Drop every buffered action. A chunk was computed for a situation; at
        a hand-off (see the probe's handover arm) that situation is gone."""
        self._chunks, self._since = [], 0

    def _infer(self, obs: Mapping) -> list:
        self.calls += 1
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

    def _blend(self, heads: list) -> Any:
        """``heads`` are every live chunk's prediction for THIS timestep, oldest
        first, so ``heads[-1]`` is the freshest."""
        w = np.exp(-self._m * np.arange(len(heads) - 1, -1, -1.0))
        out = (w / w.sum()) @ np.stack(heads)
        for d in self._discrete:
            out[d] = heads[-1][d]
        return out

    def act(self, obs: Mapping) -> Any:
        if not self._chunks or (self._k is not None and self._since >= self._k):
            fresh = self._infer(obs)
            self._chunks = [*self._chunks, fresh] if self._m is not None else [fresh]
            self._since = 0
        self._since += 1
        heads = [c.pop(0) for c in self._chunks]
        self._chunks = [c for c in self._chunks if c]
        return heads[-1] if len(heads) == 1 else self._blend(heads)


class RemoteVlaPolicy:
    """``harness.contracts.PolicyFactory`` backed by a websocket policy server.

    Construction is dependency-free and offline (Tier-A shapes it anywhere);
    the vendored client is imported and the socket opened lazily at the first
    ``make_driver``/``connect``, where the handshake gate runs.
    """

    def __init__(self, *, host: str = "127.0.0.1", port: int = 8000,
                 api_key: str | None = None, replan_every: int | None = None,
                 ensemble: float | None = None, discrete_dims: Any = (),
                 **contract: Any) -> None:
        self._host, self._port, self._api_key = host, port, api_key
        # Named, not swept into **contract: how often the driver re-queries is a
        # SERVING choice, and the contract is the TRAINING observation the
        # handshake gate reconciles. A serving knob in there would be reported
        # as an "unverified" contract key -- a server has no opinion about it to
        # verify. It is sealed beside the contract instead (see connect()), so a
        # record still says which execution policy produced its numbers.
        self.execution = {"replan_every": replan_every, "ensemble": ensemble,
                          "discrete_dims": list(discrete_dims)}
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
                self.handshake = dict(
                    reconcile(self.contract, client.get_server_metadata()),
                    execution=dict(self.execution))
            except Exception:
                client.close()
                raise
            self._client = client
        assert self.handshake is not None
        return self.handshake

    def make_driver(self, spec: Any) -> RemoteChunkDriver:
        self.connect()
        return RemoteChunkDriver(self._client, self.handshake,
                                 task=getattr(spec, "task", None), **self.execution)


def provider(**params: Any) -> RemoteVlaPolicy:
    return RemoteVlaPolicy(**params)
