"""--render overlay: the viewer wraps the bound env provider and the runtime
refuses a window it cannot draw.

No window, no robosuite here (DISPLAY=:1 on the workstation would make a real
window pop mid-suite, so the live path is deliberately NOT exercised): the
generalised viewer is checked against a FAKE base provider, the mount overlay is
checked as pure config, and the boot-time DISPLAY refusal + brief-schema
immutability are checked directly. The live window is the SAME governed_rollout
code path watch_stack proved in round 80, minus the window this suite must not open.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import harness_runtime as runtime
from scripts import watch_stack


class _FakeEnv:
    """A base env provider whose semantics the viewer overlay must delegate to."""

    def make_env(self, spec):  # never called here (robosuite window)
        raise AssertionError("no window in these tests")

    def tasks(self):
        return ("stack", "lift")

    def object_key(self, spec):
        return "cubeA_pos"

    def success(self, obs, spec, start_z):
        return True


def fake_provider():  # reached by ref string, like every provider factory
    return _FakeEnv()


def test_render_env_passes_through_and_renders():
    """_RenderEnv wraps ANY env: reset/step pass through, render fires each time."""
    calls: list[str] = []

    class _E:
        def reset(self):
            return "obs0"

        def step(self, action):
            return ("obs1", 1.0, False, {})

        def render(self):
            calls.append("render")

        def close(self):
            calls.append("close")

    r = watch_stack._RenderEnv(_E(), delay=0.0)
    assert r.reset() == "obs0"
    assert calls == ["render"]
    assert r.step([0.0]) == ("obs1", 1.0, False, {})
    assert calls == ["render", "render"]
    r.close()  # delegates via __getattr__
    assert calls[-1] == "close"


def test_viewer_delegates_contract_to_the_wrapped_provider():
    """The overlay wraps whatever base ref it is given; semantics come from it --
    so the window watches ANY task, not just the stack the CLI was built for."""
    v = watch_stack.ViewerEmbodiment(base_ref="tests.test_render:fake_provider")
    assert v.tasks() == ("stack", "lift")
    assert v.object_key(object()) == "cubeA_pos"
    assert v.success({}, object(), 0.0) is True


def test_render_overlays_only_the_embodiment_mount():
    """render=True overlays RENDER_ENV_REF on embodiment.env; headless is untouched."""
    binding = {"policy": "plugins.policies:provider",
               "planner": "plugins.task.planner_stack:provider"}
    lit = runtime._mount_plan(binding, Path("/tmp/skills"), render=True)
    assert lit.ref("embodiment.env") == runtime.RENDER_ENV_REF
    dark = runtime._mount_plan(binding, Path("/tmp/skills"), render=False)
    assert dark.ref("embodiment.env") != runtime.RENDER_ENV_REF
    # the overlay is param-free (the workload refuses env mount params).
    env_mount = next(m for m in lit.mounts if m.capability == "embodiment.env")
    assert env_mount.params == {}


def test_render_without_display_refuses_at_boot(tmp_path, monkeypatch):
    """No $DISPLAY -> refuse loudly, never a silent headless fallback."""
    monkeypatch.delenv("DISPLAY", raising=False)
    with pytest.raises(RuntimeError, match="DISPLAY"):
        runtime.boot(tmp_path / "s", render=True)


def test_render_unsets_headless_mujoco_gl(tmp_path, monkeypatch):
    """round-80: MUJOCO_GL=egl is headless; --render unsets it for native GL."""
    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setenv("MUJOCO_GL", "egl")
    runtime.boot(tmp_path / "s", render=True)
    assert "MUJOCO_GL" not in os.environ


def test_render_is_not_a_brief_key(tmp_path):
    """render is a per-boot flag; a brief carrying it is rejected (reject-unknown
    still holds -- the schema gains no render key)."""
    session = tmp_path / "s"
    inbox = session / "inbox"
    inbox.mkdir(parents=True)
    tmp = inbox / "r.json.tmp"
    tmp.write_text(json.dumps({"kind": "task", "task": "stack", "seed": 90000,
                               "render": True}))
    os.replace(tmp, inbox / "r.json")

    rt = runtime.main(session, drain=True)  # default: no render, headless

    assert (rt.failed / "r.json").exists()
    errors = [r for r in rt.log.rows() if r["kind"] == "runtime.task_error"]
    assert len(errors) == 1
    assert "unknown brief keys" in errors[0]["data"]["error"]
    assert "render" in errors[0]["data"]["error"]
