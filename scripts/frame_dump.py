"""The frames overlay: offscreen viewport frames on ``runs/<session>/frame.jpg``.

watch_stack's sibling for a REMOTE browser: the operator watches the sim through
the ph-station 取景窗 panel, which polls the board's ``runtime_frame`` face --
so this dumps a small JPEG per step interval instead of opening an X window.
Same delegating-wrapper design as scripts/watch_stack.py: a generic
embodiment.env provider wraps ANY base ref (robosuite card, robocasa card, or
the viewer overlay itself) and only ADDS a render -- semantics delegate
verbatim, so the wrapper can never move an episode's outcome.

Live state, not evidence (the runtime_status.json family): the frame file is
overwritten in place (temp + os.replace, the brief_drop atomicity), never enters
the session-log chain, and every failure in the dump path is swallowed -- a
broken GL stack loses the picture, never the task. The offscreen render reads
mjData and consumes no rng (mjv_updateScene/mjr_render are read-only; the one
``sim.forward()`` in lazy context creation recomputes derived quantities from
the existing state), so frames cannot perturb episode determinism.

Frame cadence is a STEP interval, not a wall clock: time-based throttling would
make the dump sequence depend on host load. Every step (~30fps on a paced
rollout); size/quality tunables sit below with the measured costs.
"""

from __future__ import annotations

import os

#: Armed destination (str path) or None. Module-level singleton like
#: harness.opstream: one resident runtime per process, armed at boot only.
_PATH: str | None = None

#: The base embodiment.env ref whose SEMANTICS the frames overlay delegates to.
#: harness_runtime._mount_plan sets it per task (the sim override or the viewer
#: ref when --render is also on). Read at CALL time -- in-process only, module
#: globals do not survive spawn, which is fine: campaigns run headless subprocesses
#: that never mount this overlay.
_BASE_REF = "plugins.embodiment_robosuite:provider"

#: Dump every Nth env step. A step interval, not a time throttle, so the dump
#: sequence is deterministic per episode. 1 = every step: measured on the 4090
#: EGL stack a 640x480 offscreen render is ~0.2ms and the JPEG encode ~2ms
#: against a ~30ms paced step, so per-step dumping costs the sim <10% and the
#: reader (the board's long-poll), not the writer, is the viewport fps ceiling.
EVERY = 1


def _size(raw: str) -> tuple[int, int]:
    """Parse a WxH spec (e.g. ``640x480``); anything malformed falls back to
    the 640x480 default -- a bad env var degrades resolution, never the frames."""
    try:
        w, h = raw.lower().split("x")
        return int(w), int(h)
    except ValueError:
        return 640, 480


#: Frame size, overridable per boot via PH_FRAMES_SIZE=WxH (default 640x480:
#: fills a dashboard grid cell; ~15-50KB as a q70 JPEG depending on the scene).
WIDTH, HEIGHT = _size(os.environ.get("PH_FRAMES_SIZE", "640x480"))

#: JPEG quality: 70 keeps the 640x480 frame's b64-over-RPC hop cheap (~2x the
#: old 400x300 q80 bytes for 2.6x the pixels).
QUALITY = 70

#: Offscreen camera preference, first present in the model wins: the robocasa
#: kitchen head cam, then the robosuite tabletop views. None (free camera) as
#: the last resort keeps the dump alive on an unknown model.
CAMERAS = ("robot0_agentview_left", "agentview", "frontview")


def arm(path) -> None:
    """Direct subsequent dumps at ``path`` (str or Path), or disarm with None.
    Runtime-boot only; unarmed, the wrapper is a pure pass-through."""
    global _PATH
    _PATH = str(path) if path else None


def dump(env) -> None:
    """Render one offscreen frame of ``env`` and atomically publish it.

    NEVER raises (the opstream.emit contract): a lost frame is a stale viewport,
    a raised one would kill the task. Creates the sim's offscreen render context
    lazily -- the production envs are built windowless AND cameraless, so none
    exists yet. The image is flipped vertically (mjr_readPixels is bottom-up).
    """
    if _PATH is None:
        return
    try:
        sim = env.sim
        if sim._render_context_offscreen is None:
            from robosuite.utils.binding_utils import MjRenderContextOffscreen
            MjRenderContextOffscreen(sim, device_id=-1)
        names = tuple(getattr(sim.model, "camera_names", ()) or ())
        camera = next((c for c in CAMERAS if c in names), None)
        px = sim.render(width=WIDTH, height=HEIGHT, camera_name=camera)
        from PIL import Image

        tmp = _PATH + ".tmp"
        Image.fromarray(px[::-1]).save(tmp, "JPEG", quality=QUALITY)
        os.replace(tmp, _PATH)
    except Exception:
        pass


class _FrameEnv:
    """Delegating wrapper: same env, plus a frame dump every EVERY steps.

    Only renders -- reset/step return the base env's values verbatim and the
    dump consumes no rng, so wrapping changes no episode semantics.
    """

    def __init__(self, env):
        self._env = env
        self._steps = 0

    def reset(self):
        obs = self._env.reset()
        self._steps = 0
        dump(self._env)
        return obs

    def step(self, action):
        out = self._env.step(action)
        self._steps += 1
        if self._steps % EVERY == 0:
            dump(self._env)
        return out

    def __getattr__(self, name):  # sim, close, _check_success, get_ep_meta, ...
        return getattr(self._env, name)


class FrameEmbodiment:
    """A manifest embodiment.env provider overlaying the frame dump on ANY base.

    ViewerEmbodiment's pattern verbatim: the semantic contract methods delegate
    to the wrapped provider (explicit forwards for the STRUCTURAL EnvProvider
    members -- runtime_checkable's getattr_static does not see __getattr__);
    only make_env is specialised, and unlike the viewer it needs no construction
    change at all -- the offscreen context is created lazily at first dump.
    """

    def __init__(self, base_ref: str | None = None):
        from harness.registry import load_provider

        # Read the module global at CALL time, not def time (the binding trap).
        self._base = load_provider(base_ref or _BASE_REF)

    def make_env(self, spec):
        return _FrameEnv(self._base.make_env(spec))

    def tasks(self):
        return self._base.tasks()

    def object_key(self, spec):
        return self._base.object_key(spec)

    def success(self, obs, spec, start_z):
        return self._base.success(obs, spec, start_z)

    def __getattr__(self, name):  # terminal_success/close/...
        return getattr(self._base, name)


def frames_provider():
    return FrameEmbodiment()
