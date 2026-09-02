"""The pi0.5 RoboCasa I/O contract: live robocasa obs -> the training observation
(``build_obs``), lerobot action order -> the PandaOmron action vector
(``lerobot_to_env``). One copy, shared by the harness executor path
(``kitchen_driver.KitchenThawDriver`` under an arm's executor) and the gate-2
probe (``scripts/probe_pi05_rollout.py``). Getting either wrong does not raise:
the base receives eef deltas and the rollout merely looks bad.

Images are flipped back to the dataset's top-down convention: the lerobot
export stored robosuite frames upside-down and the model was trained on them.
"""

from __future__ import annotations

import numpy as np

#: obs key -> slice, in modality.json's state order (robocasa's own
#: lerobot_utils.LEROBOT_STATE_TO_HDF5_STATE, read left to right).
STATE_KEYS = ("robot0_base_pos", "robot0_base_quat", "robot0_base_to_eef_pos",
              "robot0_base_to_eef_quat", "robot0_gripper_qpos")


def lerobot_to_env(a) -> np.ndarray:
    """Inverse of robocasa ``lerobot_utils.ACTION_KEY_ORDERING_HDF5``."""
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    if a.shape[0] != 12:
        raise ValueError(f"expected a 12-dim action, got {a.shape}")
    out = np.empty(12)
    out[0:3] = a[5:8]     # eef_position   -> arm OSC position
    out[3:6] = a[8:11]    # eef_rotation   -> arm OSC rotation
    out[6] = a[11]        # gripper_close  -> gripper
    out[7:11] = a[0:4]    # base_motion    -> base vx/vy/wyaw/torso
    out[11] = a[4]        # control_mode   -> base_mode
    return out


def build_obs(obs, prompt: str) -> dict:
    """The observation the checkpoint was trained on, out of a live robocasa obs."""
    return {
        "observation/image": np.ascontiguousarray(
            obs["robot0_agentview_left_image"][::-1]),
        "observation/wrist_image": np.ascontiguousarray(
            obs["robot0_eye_in_hand_image"][::-1]),
        "observation/state": np.concatenate(
            [np.asarray(obs[k], dtype=np.float32).reshape(-1) for k in STATE_KEYS]),
        "prompt": prompt,
    }
