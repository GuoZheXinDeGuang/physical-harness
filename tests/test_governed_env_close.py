"""RUNG 0 regression: governed_rollout must close the env even when the rollout
body raises. Without the try/finally, a fault between make_env and the normal
env.close() leaks the GL context; over a 50-task soak the process dies -- the
exact failure M4#7 must not have. See local-archive/docs/retired-from-public/m4-design.md.

The soak cannot cover this by data alone (a leak on the raise path is invisible
until the Nth task), so it gets a direct unit test: fake env whose close() flips
a flag, force a raise mid-rollout, assert close() ran and the exception still
propagated.
"""

import pytest

from harness.spec import EpisodeSpec
from plugins.rsi import governed


class _Boom(RuntimeError):
    pass


class _FakeEnv:
    def __init__(self):
        self.closed = False

    def reset(self):
        raise _Boom("mid-rollout fault")

    def close(self):
        self.closed = True


class _FakeEmbodiment:
    def __init__(self, env):
        self._env = env

    def make_env(self, spec):
        return self._env


def test_governed_rollout_closes_env_on_raise(monkeypatch):
    env = _FakeEnv()
    monkeypatch.setattr(governed, "_embodiment", lambda spec: _FakeEmbodiment(env))
    with pytest.raises(_Boom):
        governed.governed_rollout(EpisodeSpec(seed=90000), None)
    assert env.closed, "env.close() must run via finally when the rollout body raises"
