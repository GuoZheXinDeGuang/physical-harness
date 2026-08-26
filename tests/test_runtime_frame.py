"""The frames overlay (scripts/frame_dump.py + board.store.read_runtime_frame).

Three layers, one file, mirroring test_runtime_events: the writer's never-fail
contract (a dump can lose a frame but can never raise into a task), the mount
overlay as pure config (frames wraps whatever embodiment.env ref stands --
base, sim override, or the --render viewer), and face equivalence (storecli
dispatch == MCP tool == board.store). The frame is NEVER a chain row -- it is
runtime_status.json-family live state.

The robocasa live proof (a real kitchen env dumping a real offscreen frame)
rides the ``robocasa`` marker, so it runs only in sims/robocasa-venv.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import numpy as np
import pytest
from test_read_session import _session

from board import mcp_server as ms
from board import store as bs
from board import storecli
from scripts import frame_dump
from scripts import harness_runtime as runtime


@pytest.fixture(autouse=True)
def _disarm():
    """frame_dump is a module-level singleton (one resident runtime per process);
    restore the armed path and the wrapped base ref after each test."""
    base = frame_dump._BASE_REF
    yield
    frame_dump._PATH = None
    frame_dump._BASE_REF = base


class _FakeSim:
    """Just enough MjSim surface for dump(): camera names + a render counter."""

    def __init__(self):
        self._render_context_offscreen = object()  # pre-built: no robosuite import
        self.model = types.SimpleNamespace(
            camera_names=("birdview", "agentview", "frontview"))
        self.calls: list[str | None] = []

    def render(self, width, height, camera_name=None):
        self.calls.append(camera_name)
        img = np.zeros((height, width, 3), dtype=np.uint8)
        img[0, :, 0] = 255  # a marked TOP row (post-flip) to catch orientation
        return img[::-1]  # mjr_readPixels is bottom-up; dump() flips it back


class _FakeEnv:
    def __init__(self):
        self.sim = _FakeSim()

    def reset(self):
        return {"obs": 0}

    def step(self, action):
        return ({"obs": 1}, 0.0, False, {})

    def close(self):
        pass


def test_frame_env_dumps_on_reset_and_step_interval(tmp_path):
    # Pillow rides the sim stacks (robosuite/robocasa deps), not the base deps:
    # a card-absent machine has no PIL -- and dump() swallows that into "no
    # frames", so the JPEG assertions below would be vacuous there. Skip.
    Image = pytest.importorskip(
        "PIL.Image", reason="Pillow not installed (rides the sim extras)")

    frame_dump.arm(tmp_path / "frame.jpg")
    env = frame_dump._FrameEnv(_FakeEnv())

    assert env.reset() == {"obs": 0}, "reset passes through"
    assert len(env.sim.calls) == 1, "reset dumps the first frame"
    assert env.sim.calls[0] == "agentview", "first present preferred camera wins"

    for _ in range(frame_dump.EVERY - 1):
        assert env.step([0.0]) == ({"obs": 1}, 0.0, False, {}), "step passes through"
    assert len(env.sim.calls) == 1, "no dump before the step interval"
    env.step([0.0])
    assert len(env.sim.calls) == 2, "every EVERY-th step dumps"

    img = Image.open(tmp_path / "frame.jpg")
    assert img.size == (frame_dump.WIDTH, frame_dump.HEIGHT)
    px = np.asarray(img)
    assert px[0].mean() > px[-1].mean(), "flipped back upright (marked row on top)"
    assert not (tmp_path / "frame.jpg.tmp").exists(), "atomic publish leaves no temp"


def test_dump_never_raises(tmp_path):
    # unarmed: a plain no-op, no sim touch
    frame_dump._PATH = None
    frame_dump.dump(_FakeEnv())
    # armed but the env's render path is broken: swallowed, no file
    frame_dump.arm(tmp_path / "frame.jpg")

    class _Broken:
        @property
        def sim(self):
            raise RuntimeError("no GL")

    frame_dump.dump(_Broken())
    assert not (tmp_path / "frame.jpg").exists()
    # armed but the destination dir vanished: swallowed too
    frame_dump.arm(tmp_path / "gone" / "frame.jpg")
    frame_dump.dump(_FakeEnv())


def test_frame_env_and_provider_delegate(tmp_path):
    """The overlay wraps ANY base provider by ref; semantics come from it (the
    watch_stack ViewerEmbodiment pattern, structural members as real methods)."""
    v = frame_dump.FrameEmbodiment(base_ref="tests.test_render:fake_provider")
    assert v.tasks() == ("stack", "lift")
    assert v.object_key(object()) == "cubeA_pos"
    assert v.success({}, object(), 0.0) is True
    # __getattr__ delegation for the non-structural surface
    env = frame_dump._FrameEnv(_FakeEnv())
    env.close()


def test_frames_overlays_only_the_embodiment_mount():
    """frames=True overlays FRAMES_ENV_REF outermost and points the wrapped base
    ref at whatever stood: base fold / sim override / the --render viewer."""
    binding = {"policy": "plugins.policies:provider",
               "planner": "plugins.task.planner_stack:provider"}
    base_ref = runtime._mount_plan(binding, Path("/tmp/skills")).ref("embodiment.env")

    lit = runtime._mount_plan(binding, Path("/tmp/skills"), frames=True)
    assert lit.ref("embodiment.env") == runtime.FRAMES_ENV_REF
    assert frame_dump._BASE_REF == base_ref
    env_mount = next(m for m in lit.mounts if m.capability == "embodiment.env")
    assert env_mount.params == {}, "param-free (workload refuses env mount params)"

    sim_binding = dict(binding, env="plugins.embodiment_robocasa:provider")
    runtime._mount_plan(sim_binding, Path("/tmp/skills"), frames=True)
    assert frame_dump._BASE_REF == "plugins.embodiment_robocasa:provider"

    runtime._mount_plan(binding, Path("/tmp/skills"), render=True, frames=True)
    assert frame_dump._BASE_REF == runtime.RENDER_ENV_REF

    dark = runtime._mount_plan(binding, Path("/tmp/skills"))
    assert dark.ref("embodiment.env") != runtime.FRAMES_ENV_REF


def test_boot_arms_and_disarms_the_frame_path(tmp_path):
    rt = runtime.boot(tmp_path / "s1", frames=True)
    assert rt.frames is True
    assert frame_dump._PATH == str(tmp_path / "s1" / "frame.jpg")
    status = json.loads((tmp_path / "s1" / "runtime_status.json").read_text())
    assert status["frames"] is True
    rt2 = runtime.boot(tmp_path / "s2")
    assert rt2.frames is False and frame_dump._PATH is None
    assert json.loads(
        (tmp_path / "s2" / "runtime_status.json").read_text())["frames"] is False


def test_faces_are_byte_identical(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    sd = _session(runs)
    (sd / "frame.jpg").write_bytes(b"\xff\xd8jpegish\xff\xd9")
    # freeze board.store's clock so age_s is identical across the three calls
    ts = (sd / "frame.jpg").stat().st_mtime
    monkeypatch.setattr(bs, "time", types.SimpleNamespace(time=lambda: ts + 5.0))
    ms.configure(runs)

    def _same(a, b):
        return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    direct = bs.read_runtime_frame(sd)
    assert direct["jpeg_b64"] and direct["age_s"] == 5.0, "non-trivial fixture"
    assert _same(ms.runtime_frame("session-main"), direct)
    assert _same(storecli.dispatch("runtime_frame", "session-main", runs,
                                   tmp_path / "S.md", tmp_path / "p.md"), direct)
    # the shared safe_child guard fronts this fn on both faces too
    assert ms.runtime_frame("../session-main") == {"error": "unknown session"}
    with pytest.raises(ValueError):
        storecli.dispatch("runtime_frame", "../session-main", runs,
                          tmp_path / "S.md", tmp_path / "p.md")


def test_after_ts_short_circuits_on_every_face(tmp_path, monkeypatch):
    """The poller's cursor: an unchanged file returns the short {unchanged, ts,
    age_s} reply (no image bytes) on all three faces; a stale cursor still gets
    the full frame."""
    runs = tmp_path / "runs"
    runs.mkdir()
    sd = _session(runs)
    (sd / "frame.jpg").write_bytes(b"\xff\xd8jpegish\xff\xd9")
    ts = round((sd / "frame.jpg").stat().st_mtime, 3)
    monkeypatch.setattr(bs, "time", types.SimpleNamespace(time=lambda: ts + 2.0))
    ms.configure(runs)

    full = bs.read_runtime_frame(sd)
    assert full["ts"] == ts and "jpeg_b64" in full

    short = bs.read_runtime_frame(sd, after_ts=ts)
    assert short == {"unchanged": True, "ts": ts, "age_s": 2.0}
    assert "jpeg_b64" not in short

    # stale cursor (an older frame's ts) -> the full frame again
    assert "jpeg_b64" in bs.read_runtime_frame(sd, after_ts=ts - 5.0)
    # cursor 0 (first poll) -> full
    assert "jpeg_b64" in bs.read_runtime_frame(sd, after_ts=0.0)

    def _same(a, b):
        return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    assert _same(ms.runtime_frame("session-main", ts), short)
    assert _same(storecli.dispatch("runtime_frame", "session-main", runs,
                                   tmp_path / "S.md", tmp_path / "p.md",
                                   after_ts=ts), short)


def test_absent_frame_reads_as_error_on_every_face(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    sd = _session(runs)
    ms.configure(runs)
    want = {"error": "no frame"}
    assert bs.read_runtime_frame(sd) == want
    assert ms.runtime_frame("session-main") == want
    assert storecli.dispatch("runtime_frame", "session-main", runs,
                             tmp_path / "S.md", tmp_path / "p.md") == want


@pytest.mark.robocasa
def test_robocasa_offscreen_frame(tmp_path):
    """Live proof in the robocasa venv: a real kitchen env dumps a real EGL
    offscreen frame through the overlay -- lazy render-context creation, camera
    pick, flip, and the atomic publish, end to end."""
    from PIL import Image

    from harness.spec import EpisodeSpec

    frame_dump.arm(tmp_path / "frame.jpg")
    emb = frame_dump.FrameEmbodiment(
        base_ref="plugins.embodiment_robocasa:provider")
    env = emb.make_env(EpisodeSpec(seed=7, task="kitchen_thaw"))
    try:
        env.reset()
    finally:
        env.close()
    img = Image.open(tmp_path / "frame.jpg")
    assert img.size == (frame_dump.WIDTH, frame_dump.HEIGHT)
    assert float(np.asarray(img).std()) > 1.0, "a real scene, not a flat frame"
