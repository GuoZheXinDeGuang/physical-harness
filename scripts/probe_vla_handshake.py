#!/usr/bin/env python3
"""Gate 2 of the pi0.5 segment campaign: does the serving path actually hold?

Gate 1 asked whether there is a signal to learn (`probe_place_demos.py`). This
one asks the question that has to be answered BEFORE a 20-hour fine-tune, not
after: when the weights come back, can the harness mount them AND prove which
ones they are? A serving path that cannot be gated is a fine-tune that produces
an unattributable number.

`tests/test_policy_vla_remote.py` already unit-tests `reconcile()` on
dictionaries. That proves the function, not the path. This runs the real card
against a REAL policy server over a real socket, so the msgpack boundary, the
first-frame protocol and the server wrapper are all in the loop:

  identity matches    manifest digest == server digest    -> MOUNTS
  identity differs    manifest digest != server digest    -> REFUSED
  identity unclaimed  no digest in the manifest           -> MOUNTS (opt-in)

The fourth case -- manifest declares a digest, server echoes none -- is what a
STOCK `openpi/scripts/serve_policy.py` does (its metadata is `{}`), so it needs
no flag here: point this probe at one and case A must come back REFUSED with
`handshake gap`. That is the fail-closed rule, checked against the very server
that motivated it.

    # against scripts/serve_vla_openpi.py (echoes the digest)
    PYTHONPATH=. .venv/bin/python scripts/probe_vla_handshake.py \
        --port 8000 --sha <digest> --roundtrip /path/to/place_obs.npz

    # against a stock openpi serve_policy.py (echoes {})
    PYTHONPATH=. .venv/bin/python scripts/probe_vla_handshake.py --port 8001 --sha <digest>

Read-only: opens sockets, seals nothing, burns no seed. Needs the
`policy_remote` extra (websockets + msgpack).
"""
from __future__ import annotations

import argparse
import time

import numpy as np

from plugins.policy_vla_remote import RemoteVlaPolicy

#: What the manifest would declare for the RoboCasa pi0.5 executor. `views` has
#: no server echo by design, so it always lands in handshake["unverified"].
CONTRACT = {"image_size": [224, 224], "views": ["base_0_rgb", "left_wrist_0_rgb"],
            "chunk": 10, "unnorm_key": "robocasa/lerobot"}


def _mount(host: str, port: int, contract: dict):
    """Returns (handshake, None) on mount, (None, error) when the gate refuses."""
    factory = RemoteVlaPolicy(host=host, port=port, **contract)
    try:
        return factory.connect(), None
    except ValueError as e:
        return None, e


def _case(name: str, host: str, port: int, contract: dict, want_mount: bool) -> bool:
    hs, err = _mount(host, port, contract)
    got = "MOUNTED" if hs else "REFUSED"
    ok = bool(hs) is want_mount
    print(f"\n--- {name} ---")
    print(f"  declared checkpoint_sha : {contract.get('checkpoint_sha', '(none)')}")
    print(f"  outcome                 : {got}   (want {'MOUNTED' if want_mount else 'REFUSED'}) "
          f"{'OK' if ok else 'WRONG'}")
    if hs:
        print(f"  unverified              : {hs['unverified']}")
        print(f"  sealed metadata         : {hs['metadata']}")
    else:
        print(f"  refusal                 : {err}")
    return ok


def roundtrip(host: str, port: int, contract: dict, npz: str, chunks: int) -> None:
    """One real inference, on an observation shaped the way the harness would
    build it for the `place` segment: the two camera streams the training
    repack feeds (agentview_left -> base_0_rgb, eye_in_hand -> left_wrist_0_rgb),
    the 16-dim PandaOmron state, and the episode's language instruction."""
    d = np.load(npz, allow_pickle=True)
    obs = {"observation/image": d["image"], "observation/wrist_image": d["wrist_image"],
           "observation/state": d["state"], "prompt": str(d["prompt"])}
    print("\n--- round trip ---")
    print(f"  observation: image {d['image'].shape} {d['image'].dtype}, "
          f"wrist {d['wrist_image'].shape} {d['wrist_image'].dtype}, "
          f"state {d['state'].shape} {d['state'].dtype}")
    print(f"  prompt: {str(d['prompt'])!r}  (demo frame {int(d['frame'])})")

    factory = RemoteVlaPolicy(host=host, port=port, **contract)
    driver = factory.make_driver(spec=None)

    for i in range(chunks):
        t0 = time.perf_counter()
        first = driver.act(obs)                       # this call does the inference
        infer_ms = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        rest = [driver.act(obs) for _ in range(int(CONTRACT["chunk"]) - 1)]
        pop_ms = (time.perf_counter() - t1) * 1000
        chunk = np.stack([first, *rest])
        print(f"  chunk {i}: {chunk.shape} {chunk.dtype}  "
              f"infer {infer_ms:.0f} ms, {len(rest)} pops {pop_ms:.3f} ms  "
              f"-> {infer_ms / len(chunk):.0f} ms/action amortized")
        if i == 0:
            np.set_printoptions(precision=3, suppress=True, linewidth=200)
            print(f"  actions[0] = {chunk[0]}")
            print(f"  actions[-1]= {chunk[-1]}")
            print(f"  per-dim min = {chunk.min(axis=0)}")
            print(f"  per-dim max = {chunk.max(axis=0)}")
            print(f"  global min/max/mean/std = {chunk.min():.4f} / {chunk.max():.4f} "
                  f"/ {chunk.mean():.4f} / {chunk.std():.4f}")
            print(f"  finite: {bool(np.isfinite(chunk).all())}  "
                  f"all-zero: {bool(not chunk.any())}  "
                  f"constant across chunk: {bool(np.allclose(chunk, chunk[0]))}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--sha", required=True, help="the digest the manifest would declare")
    ap.add_argument("--roundtrip", help="path to a .npz observation; runs one real inference")
    ap.add_argument("--chunks", type=int, default=3, help="inferences to time")
    a = ap.parse_args()

    # Ask the server what it says about itself FIRST, so the expectations below
    # are read off the wire rather than assumed -- against a stock server that
    # echoes nothing, case A must flip to REFUSED and this probe must know it.
    metadata, _ = _mount(a.host, a.port, {})
    echoed = (metadata or {}).get("metadata", {}).get("checkpoint_sha")
    print(f"server {a.host}:{a.port} first-frame metadata:\n  {(metadata or {}).get('metadata')}")
    print(f"echoed checkpoint_sha: {echoed or '(none)'}")

    wrong = ("b" if a.sha[0] != "b" else "c") + a.sha[1:]  # one character off
    ok = _case("A. identity matches (manifest digest == served weights)",
               a.host, a.port, dict(CONTRACT, checkpoint_sha=a.sha),
               want_mount=echoed == a.sha)
    ok &= _case("B. identity differs (one character off)",
                a.host, a.port, dict(CONTRACT, checkpoint_sha=wrong), want_mount=False)
    ok &= _case("C. identity unclaimed (opt-in: no digest in the manifest)",
                a.host, a.port, dict(CONTRACT), want_mount=True)

    if a.roundtrip:
        if echoed != a.sha:
            print("\nskipping the round trip: this server is not the pinned checkpoint")
        else:
            roundtrip(a.host, a.port, dict(CONTRACT, checkpoint_sha=a.sha),
                      a.roundtrip, a.chunks)

    print("\nGATE 2 SERVING PATH HOLDS" if ok else "\nGATE 2 FAILED -- the serving path does not hold")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
