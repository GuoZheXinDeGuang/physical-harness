"""A behaviour-cloned frozen policy, so the failures are not ones we designed.

Why this exists
---------------
Through round 11 the frozen policy was a script whose failure mode -- act on one
noisy percept, open loop, never retry -- was chosen by us BECAUSE it is
detectable and recoverable. That is a fair model of how a vision-language-action
policy fails, but it leaves the obvious objection standing: the harness may only
work on failures its author built to be found.

A cloned policy answers that. It is trained on successful scripted rollouts and
then run closed-loop, so its errors are compounding-error and distribution-shift
failures that emerge from the fit, not from a schedule we wrote. Nothing about
where or how it breaks is chosen.

Implementation is a two-layer MLP in numpy: no new dependency, trains on CPU in
seconds, and its weakness is the point rather than a limitation to apologise for.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: Proprioception plus the one-shot percept the demonstrator acted on. Deliberately
#: NOT the true object pose: a cloned policy that could see ground truth would be
#: solving a different problem than the one a real VLA faces.
OBS_KEYS = ("robot0_joint_pos_cos", "robot0_joint_pos_sin", "robot0_joint_vel",
            "robot0_eef_pos", "robot0_gripper_qpos")


def encode(obs, target: np.ndarray, phase_clock: float) -> np.ndarray:
    """Flat observation vector: proprioception, the stale percept, and a clock."""
    parts = [np.asarray(obs[k], dtype=float).reshape(-1) for k in OBS_KEYS]
    eef = np.asarray(obs["robot0_eef_pos"], dtype=float)
    parts.append(np.asarray(target, dtype=float) - eef)   # relative, not absolute
    parts.append(np.array([phase_clock]))
    return np.concatenate(parts)


@dataclass
class MLPPolicy:
    """Two-layer tanh MLP. Frozen once trained; identified by a content hash."""

    w1: np.ndarray
    b1: np.ndarray
    w2: np.ndarray
    b2: np.ndarray
    mu: np.ndarray
    sd: np.ndarray

    def act(self, x: np.ndarray) -> np.ndarray:
        z = (x - self.mu) / self.sd
        h = np.tanh(z @ self.w1 + self.b1)
        out = h @ self.w2 + self.b2
        return np.clip(out, -1.0, 1.0)

    def sha(self) -> str:
        blob = b"".join(a.tobytes() for a in (self.w1, self.b1, self.w2, self.b2, self.mu, self.sd))
        return hashlib.sha256(blob).hexdigest()

    def save(self, path: Path) -> None:
        np.savez(path, w1=self.w1, b1=self.b1, w2=self.w2, b2=self.b2, mu=self.mu, sd=self.sd)

    @classmethod
    def load(cls, path: Path) -> "MLPPolicy":
        d = np.load(path)
        return cls(d["w1"], d["b1"], d["w2"], d["b2"], d["mu"], d["sd"])


def train(X: np.ndarray, Y: np.ndarray, *, hidden: int = 64, epochs: int = 400,
          lr: float = 0.02, seed: int = 0, verbose: bool = False) -> MLPPolicy:
    """Full-batch gradient descent on mean squared error.

    Deliberately small and deliberately under-trained: the goal is a policy that
    succeeds often enough to be worth governing and fails often enough to leave
    something to find, not the best clone obtainable.
    """
    rng = np.random.RandomState(seed)
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Z = (X - mu) / sd
    n_in, n_out = Z.shape[1], Y.shape[1]
    w1 = rng.randn(n_in, hidden) * (1.0 / np.sqrt(n_in))
    b1 = np.zeros(hidden)
    w2 = rng.randn(hidden, n_out) * (1.0 / np.sqrt(hidden))
    b2 = np.zeros(n_out)
    m = len(Z)
    for epoch in range(epochs):
        h = np.tanh(Z @ w1 + b1)
        out = h @ w2 + b2
        err = out - Y
        loss = float((err ** 2).mean())
        g_out = 2.0 * err / m
        g_w2, g_b2 = h.T @ g_out, g_out.sum(0)
        g_h = (g_out @ w2.T) * (1.0 - h ** 2)
        g_w1, g_b1 = Z.T @ g_h, g_h.sum(0)
        w2 -= lr * g_w2; b2 -= lr * g_b2; w1 -= lr * g_w1; b1 -= lr * g_b1
        if verbose and epoch % 100 == 0:
            print(f"    epoch {epoch:4d}  mse {loss:.5f}")
    return MLPPolicy(w1, b1, w2, b2, mu, sd)
