#!/usr/bin/env python3
"""Gate 2, offline half: what does pi0.5 predict on frames it was TRAINED on?

**TRAIN-SET, NOT HELD OUT.** All 100 MicrowaveThawingFridge demos went into the
fine-tune; there is no held-out demo split. Every number here is therefore an
upper bound on fidelity -- a memorisation ceiling, not a generalisation
measurement. It answers exactly one question: did the weights learn to reproduce
the actions they were shown? A model that cannot do that on its own training set
has not learned the task at all, and that is the only inference this file
licenses.

Per action dimension (modality.json layout: base_motion 0:4, control_mode 4:5,
eef_pos 5:8, eef_rot 8:11, gripper 11:12) it reports mean abs error and Pearson
correlation of the FIRST action of each predicted chunk against the human action
at the same frame, plus the same over the whole 10-step chunk against the human
actions at t..t+9 (which is what the model is actually trained to fit).

``action[3]`` (the torso channel of base_motion) is constant 0 across the
dataset -- a dead dimension. Its correlation is undefined and reported as null
rather than as a zero, because a zero would read as "uncorrelated" when the
truth is "there is nothing to correlate with".

Observations are built from the dataset's own parquet + mp4 streams, so the
frames handed to the socket are byte-for-byte the frames the training loader
handed the optimizer -- no live simulator, no re-render, no orientation question.

    cd /home/yusenzlabpc/Desktop/physical-harness && PYTHONPATH=. \
      /home/yusenzlabpc/Desktop/sims/robocasa-venv/bin/python \
      scripts/probe_pi05_fidelity.py --episodes 5 --per-episode 60 \
      --sha <digest> --out runs/gate2_eval/action_fidelity.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATASET = Path(os.environ.get(
    "PH_LEROBOT_ROOT", Path.home() / "Desktop/datasets/robocasa/lerobot"))

#: modality.json action layout, expanded per dimension.
DIM_NAMES = ["base_x", "base_y", "base_yaw", "base_torso", "control_mode",
             "eef_x", "eef_y", "eef_z", "eef_rx", "eef_ry", "eef_rz", "gripper"]

#: The dimension the dataset holds constant at 0 (base_motion's torso channel).
DEAD_DIM = 3

CONTRACT = {"image_size": [224, 224], "views": ["base_0_rgb", "left_wrist_0_rgb"],
            "chunk": 10, "unnorm_key": "robocasa/lerobot"}


def _video(idx: int, key: str) -> Path:
    return DATASET / "videos" / "chunk-000" / f"observation.images.{key}" / \
        f"episode_{idx:06d}.mp4"


def load_episode(idx: int):
    """(state[T,16], action[T,12], base[T,256,256,3], wrist[T,...], prompt)."""
    import imageio.v2 as imageio
    import pandas as pd

    df = pd.read_parquet(DATASET / "data" / "chunk-000" / f"episode_{idx:06d}.parquet")
    state = np.stack([np.asarray(v, dtype=np.float32) for v in df["observation.state"]])
    action = np.stack([np.asarray(v, dtype=np.float32) for v in df["action"]])
    prompt = str(df["annotation.human.task_description"].iloc[0])

    frames = {}
    for slot, key in (("base", "robot0_agentview_left"),
                      ("wrist", "robot0_eye_in_hand")):
        rdr = imageio.get_reader(_video(idx, key), format="ffmpeg")
        frames[slot] = np.stack([f for f in rdr])
        rdr.close()
    n = min(len(state), len(action), len(frames["base"]), len(frames["wrist"]))
    return state[:n], action[:n], frames["base"][:n], frames["wrist"][:n], prompt


def query(client, state, base, wrist, prompt) -> np.ndarray:
    """One chunk [10, 12] from the served checkpoint."""
    reply = client.predict_action({
        "observation/image": base, "observation/wrist_image": wrist,
        "observation/state": state, "prompt": prompt})
    a = np.asarray(reply.get("data", reply)["actions"])
    return a[0] if a.ndim == 3 else a


def _stats(pred: np.ndarray, true: np.ndarray) -> list[dict]:
    """Per-dimension MAE + Pearson r. A constant column has no correlation."""
    out = []
    for d in range(pred.shape[1]):
        p, y = pred[:, d], true[:, d]
        r = None
        if p.std() > 1e-9 and y.std() > 1e-9:
            r = float(np.corrcoef(p, y)[0, 1])
        out.append({
            "dim": d, "name": DIM_NAMES[d],
            "mean_abs_error": float(np.abs(p - y).mean()),
            "pred_mean": float(p.mean()), "pred_std": float(p.std()),
            "true_mean": float(y.mean()), "true_std": float(y.std()),
            "correlation": r,
            "dead_dimension": d == DEAD_DIM,
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--episodes", type=int, default=5, help="demo episodes to sample")
    ap.add_argument("--per-episode", type=int, default=60,
                    help="frames sampled uniformly per episode")
    ap.add_argument("--window-episode", type=int, default=0,
                    help="episode whose place window gets the dense sample trajectory")
    ap.add_argument("--window-stride", type=int, default=10)
    ap.add_argument("--windows", type=Path, default=None,
                    help="place_windows.json from scripts/probe_place_demos.py --replay")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--sha", default=None)
    ap.add_argument("--out", type=Path, default=Path("runs/gate2_eval/action_fidelity.json"))
    a = ap.parse_args(argv)

    from plugins.policy_vla_remote import RemoteVlaPolicy

    contract = dict(CONTRACT)
    if a.sha:
        contract["checkpoint_sha"] = a.sha
    factory = RemoteVlaPolicy(host=a.host, port=a.port, **contract)
    handshake = factory.connect()
    client = factory._client

    pred0, true0, predC, trueC = [], [], [], []
    per_episode = []
    for idx in range(a.episodes):
        state, action, base, wrist, prompt = load_episode(idx)
        n = len(state)
        ts = np.unique(np.linspace(0, n - 11, a.per_episode).astype(int))
        if idx == 0:  # warm the server before anything is measured
            query(client, state[0], base[0], wrist[0], prompt)
        ep_p0, ep_t0 = [], []
        for t in ts:
            chunk = query(client, state[t], base[t], wrist[t], prompt)
            pred0.append(chunk[0]); true0.append(action[t])
            ep_p0.append(chunk[0]); ep_t0.append(action[t])
            k = min(len(chunk), n - t)
            predC.append(chunk[:k]); trueC.append(action[t:t + k])
        per_episode.append({
            "episode": idx, "n_frames": int(n), "sampled": int(len(ts)),
            "prompt": prompt,
            "mean_abs_error_all_dims": float(
                np.abs(np.array(ep_p0) - np.array(ep_t0)).mean()),
        })
        print(f"ep {idx}: {len(ts)} frames sampled, "
              f"MAE {per_episode[-1]['mean_abs_error_all_dims']:.4f}", flush=True)

    pred0, true0 = np.array(pred0), np.array(true0)
    predC, trueC = np.concatenate(predC), np.concatenate(trueC)

    # dense sample trajectory over one episode's place window (gate 1's derivation)
    window = None
    traj = None
    if a.windows and a.windows.exists():
        rows = json.loads(a.windows.read_text())
        w = next((r for r in rows if r["episode"] == a.window_episode), None)
        if w and w["place_start"] >= 0 and w["place_end"] > w["place_start"]:
            window = {"episode": a.window_episode, "start": w["place_start"],
                      "end": w["place_end"],
                      "source": "scripts/probe_place_demos.py --replay "
                                "(last grasp onset -> first frame inside)"}
    if window is not None:
        state, action, base, wrist, prompt = load_episode(window["episode"])
        ts = list(range(window["start"], min(window["end"] + 1, len(state)),
                        a.window_stride))
        rows = []
        for t in ts:
            chunk = query(client, state[t], base[t], wrist[t], prompt)
            rows.append({"t": int(t),
                         "predicted": [float(x) for x in chunk[0]],
                         "actual": [float(x) for x in action[t]]})
        traj = {"window": window, "dim_names": DIM_NAMES, "stride": a.window_stride,
                "frames": rows}
        print(f"place-window trajectory: ep {window['episode']} "
              f"[{window['start']}, {window['end']}], {len(rows)} points", flush=True)

    doc = {
        "split": "TRAIN-SET",
        "split_note": "all 100 demos were in the fine-tune; there is no held-out "
                      "demo split, so these are memorisation numbers, an upper "
                      "bound on fidelity and not a generalisation claim. This is "
                      "the DEMO split and is unrelated to the SCENE split "
                      "(robocasa layouts) that probe_pi05_rollout.py records "
                      "under the same key -- this probe builds no env at all, it "
                      "reads the dataset's own parquet and mp4 streams.",
        "checkpoint_sha": handshake["metadata"].get("checkpoint_sha"),
        "checkpoint_path": handshake["metadata"].get("checkpoint_path"),
        "action_layout": {"base_motion": [0, 4], "control_mode": [4, 5],
                          "eef_pos": [5, 8], "eef_rot": [8, 11], "gripper": [11, 12]},
        "dim_names": DIM_NAMES,
        "dead_dimension": {"dim": DEAD_DIM, "name": DIM_NAMES[DEAD_DIM],
                           "why": "constant 0 across the dataset (torso channel)"},
        "n_episodes": a.episodes, "n_frames_sampled": int(len(pred0)),
        "first_action_of_chunk": {
            "compared_against": "human action at the same frame",
            "per_dim": _stats(pred0, true0),
            "mean_abs_error_all_dims": float(np.abs(pred0 - true0).mean()),
        },
        "full_chunk": {
            "compared_against": "human actions at t..t+9 (what the model fits)",
            "n_pairs": int(len(predC)),
            "per_dim": _stats(predC, trueC),
            "mean_abs_error_all_dims": float(np.abs(predC - trueC).mean()),
        },
        "per_episode": per_episode,
        "place_window_trajectory": traj,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(doc, indent=1, sort_keys=True))

    print(f"\nTRAIN-SET fidelity, {len(pred0)} frames over {a.episodes} episodes")
    print(f"{'dim':<12} {'MAE':>9} {'corr':>8} {'pred mu':>9} {'true mu':>9} "
          f"{'pred sd':>9} {'true sd':>9}")
    for s in doc["first_action_of_chunk"]["per_dim"]:
        c = "  n/a" if s["correlation"] is None else f"{s['correlation']:.3f}"
        print(f"{s['name']:<12} {s['mean_abs_error']:>9.4f} {c:>8} "
              f"{s['pred_mean']:>9.4f} {s['true_mean']:>9.4f} "
              f"{s['pred_std']:>9.4f} {s['true_std']:>9.4f}")
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
