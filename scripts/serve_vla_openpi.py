#!/usr/bin/env python3
"""Serve an openpi checkpoint to the policy_vla_remote card, identity attached.

**Why it lives in this repo and not in openpi.** openpi's own
``scripts/serve_policy.py`` already builds the policy and already hands a
``metadata`` dict to ``WebsocketPolicyServer`` -- the only thing it does not do
is say WHICH weights it loaded, because upstream has no reason to care. That
missing sentence is this repo's ``checkpoint_sha`` contract (defined in
``plugins/policy_vla_remote/__init__.py:_IDENTITY_KEY``), and a contract with
two sides drifts unless both sides read the same code. So the echo side calls
the very :func:`checkpoint_sha` the gate calls, and openpi stays completely
untouched -- not forked, not vendored, not patched.

**Why scripts/ and not the card directory.** This runs under **openpi's**
interpreter. ``plugins/`` is mounted code and may import only what its manifest
declares (``tests/test_boundaries.py`` enforces it), and openpi is emphatically
not a dependency of a card whose entire point is that the model stack lives
behind a socket -- declaring it would be a false dependency claim. An operator
entry point that boots a foreign stack belongs with the other entry points.

    cd <openpi>
    PYTHONPATH=<physical-harness> HF_LEROBOT_HOME=~/Desktop/datasets HF_HUB_OFFLINE=1 \\
      .venv/bin/python <physical-harness>/scripts/serve_vla_openpi.py \\
        --checkpoint-dir checkpoints/pi05_robocasa_lora/gate2_bs8/199 \\
        --config pi05_robocasa_lora --port 8000

It prints the digest before loading any weights, so the value to paste into a
manifest's ``checkpoint_sha`` param comes out of the same call that will echo it.

**What is echoed, and what is deliberately not.** ``training_obs_image_size``,
``action_chunk_size`` and ``default_unnorm_key`` are read off the resolved
``TrainConfig`` -- never typed in here, so they cannot disagree with the model
that answers. ``camera_views`` is NOT echoed: the slot order lives inside the
config's input transform (for RoboCasa, ``RoboCasaInputs``) with no accessor to
ask, and a hand-copied list is precisely the second copy that goes stale
silently. It stays in the client's ``handshake["unverified"]``, which is what
the card already expects of it.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from plugins.policy_vla_remote import checkpoint_sha


def _train_image_size(data_config) -> list[int] | None:
    """The resolution the model actually saw, read off the ResizeImages the
    config's model transforms carry -- asking the pipeline beats restating a
    number here that goes stale the day a config stops using 224."""
    for t in data_config.model_transforms.inputs:
        h, w = getattr(t, "height", None), getattr(t, "width", None)
        if h is not None and w is not None:
            return [int(h), int(w)]
    return None


def build_metadata(train_config, checkpoint_dir: Path) -> dict:
    """The first-frame handshake dict: identity + the observation contract, all
    of it read off the config that built the model rather than restated here."""
    data_config = train_config.data.create(train_config.assets_dirs, train_config.model)
    return {
        # Identity. The gate fails CLOSED on this one -- see _IDENTITY_KEY.
        "checkpoint_sha": checkpoint_sha(checkpoint_dir),
        # Observation contract. This is the size the model was TRAINED on, not a
        # demand: a client sending the dataset's native resolution is resized
        # server-side by the same transform this value is read from.
        "training_obs_image_size": _train_image_size(data_config),
        "action_chunk_size": train_config.model.action_horizon,
        "default_unnorm_key": data_config.asset_id,
        # Provenance, not gated: a renameable path never proves identity (that is
        # the digest's job), but it is what an operator reads in a log.
        "checkpoint_path": str(checkpoint_dir),
        "config": train_config.name,
        **(train_config.policy_metadata or {}),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--checkpoint-dir", required=True, type=Path)
    ap.add_argument("--config", default="pi05_robocasa_lora", help="openpi TrainConfig name")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--print-sha", action="store_true",
                    help="print the digest and exit -- no weights, no GPU")
    a = ap.parse_args()

    ckpt = a.checkpoint_dir.resolve()
    if not ckpt.is_dir():
        raise SystemExit(f"no such checkpoint directory: {ckpt}")
    if a.print_sha:
        print(checkpoint_sha(ckpt))
        return

    # openpi is imported here, not at module scope, so `--print-sha` works from
    # the harness venv too -- the digest an operator pastes into a manifest must
    # not require a JAX install to compute.
    from openpi.policies import policy_config as _policy_config
    from openpi.serving import websocket_policy_server
    from openpi.training import config as _config

    train_config = _config.get_config(a.config)
    metadata = build_metadata(train_config, ckpt)
    logging.info("checkpoint_sha = %s", metadata["checkpoint_sha"])
    logging.info("handshake metadata = %s", metadata)

    policy = _policy_config.create_trained_policy(train_config, ckpt)
    logging.info("serving %s on %s:%d", ckpt, a.host, a.port)
    websocket_policy_server.WebsocketPolicyServer(
        policy=policy, host=a.host, port=a.port, metadata=metadata,
    ).serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, force=True)
    main()
