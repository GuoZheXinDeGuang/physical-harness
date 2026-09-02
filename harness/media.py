"""Segment media recorder: 128px frames in memory, on disk only after verify.

One ``SegmentRecorder`` per (session media root, task, seed). ``start(env,
driver)`` taps ``driver.act`` so every EVERY-th driver step grabs one frame from
the duck-typed source (``driver.frame()`` else ``env.frame()``; neither ->
nothing recorded). ``keep(node)`` encodes ``media/<task>/<seed>/<node>.mp4``
(imageio+ffmpeg importable) else ``.gif`` (PIL), re-encoding at a lower fps
until under MAX_BYTES, and updates the seed's ``index.json``; ``drop()``
discards. Frames are live state like scripts/frame_dump: they never enter the
session-log chain (only the index/paths reach the board's rsi_frames face), and
every capture/encode failure is swallowed -- a lost clip never fails a task.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

SIZE = 128
FPS = 10
#: capture every Nth driver step; a 300-step robocasa segment -> ~75 frames
EVERY = 4
MAX_BYTES = 1_000_000


class SegmentRecorder:
    def __init__(self, root: str | os.PathLike, task: str, seed: int, *,
                 every: int = EVERY) -> None:
        self.root = Path(root)
        self.task = str(task)
        self.seed = int(seed)
        self.every = max(1, int(every))
        self.frames: list[Any] = []   # PIL RGB images, SIZE x SIZE
        self._src = None
        self._untap = None
        self._n = 0

    # -- recording -------------------------------------------------------------
    def start(self, env: Any, driver: Any) -> None:
        self.stop()
        self.frames, self._n = [], 0
        self._src = getattr(driver, "frame", None) or getattr(env, "frame", None)
        if self._src is None:
            return
        orig = driver.act

        def act(obs):
            self.capture()
            return orig(obs)

        driver.act = act   # instance attr shadows the class method; stop() removes it
        self._untap = lambda: driver.__dict__.pop("act", None)

    def capture(self) -> None:
        self._n += 1
        if self._n % self.every or self._src is None:
            return
        try:
            img = _to_image(self._src())
            if img is not None:
                self.frames.append(img)
        except Exception:  # noqa: BLE001, S110 -- a lost frame never touches the task
            pass

    def stop(self) -> None:
        if self._untap is not None:
            self._untap()
            self._untap = None
        self._src = None

    # -- outcome ---------------------------------------------------------------
    def drop(self) -> None:
        self.stop()
        self.frames = []

    def keep(self, node: str) -> Path | None:
        """Encode the segment's frames to ``<root>/<task>/<seed>/<node>.(mp4|gif)``
        and index it. None when nothing was captured or every encode failed."""
        self.stop()
        frames, self.frames = self.frames, []
        if not frames:
            return None
        seed_dir = self.root / self.task / str(self.seed)
        try:
            seed_dir.mkdir(parents=True, exist_ok=True)
            path, fps, n = _encode(frames, seed_dir / str(node))
            _index(seed_dir, node, path, fps, n)
            return path
        except Exception:  # noqa: BLE001 -- a lost clip never touches the task
            return None

    def finish(self, node: str, ok: bool) -> Path | None:
        """The workload's one call: keep on verify success, drop otherwise."""
        if ok:
            return self.keep(node)
        self.drop()
        return None


def recorder_for(brief: Any, seed: int) -> SegmentRecorder | None:
    """A recorder when the brief names a ``media_dir`` (the runtime sets it for
    evolve/suite briefs and for a task brief with ``media: true``); else None."""
    root = brief.get("media_dir")
    return SegmentRecorder(root, brief.get("task", "task"), seed) if root else None


# -- helpers -------------------------------------------------------------------

def _to_image(raw: Any):
    """A frame from any source shape -> SIZE x SIZE PIL RGB image. ``bytes`` is
    a packed SIZE*SIZE*3 RGB buffer (the stdlib-only fake); anything else is an
    HxWx3 uint8 array."""
    if raw is None:
        return None
    from PIL import Image

    if isinstance(raw, (bytes, bytearray)):
        img = Image.frombytes("RGB", (SIZE, SIZE), bytes(raw))
    else:
        import numpy as np

        img = Image.fromarray(np.ascontiguousarray(np.asarray(raw, dtype=np.uint8)))
    if img.mode != "RGB":
        img = img.convert("RGB")
    if img.size != (SIZE, SIZE):
        img = img.resize((SIZE, SIZE))
    return img


def _writer():
    try:
        import imageio.v2 as imageio  # noqa: F401
        import imageio_ffmpeg  # noqa: F401
        return ".mp4", _write_mp4
    except ImportError:
        return ".gif", _write_gif


def _write_mp4(frames, path: str, fps: int) -> None:
    import imageio.v2 as imageio
    import numpy as np

    imageio.mimwrite(path, [np.asarray(f) for f in frames], fps=fps,
                     format="FFMPEG", codec="libx264", macro_block_size=None)


def _write_gif(frames, path: str, fps: int) -> None:
    frames[0].save(path, "GIF", save_all=True, append_images=frames[1:],
                   duration=int(1000 / fps), loop=0)


def _encode(frames: list, stem: Path) -> tuple[Path, int, int]:
    """Write ``stem + ext`` atomically; halve the frame rate (subsample) until
    the file is under MAX_BYTES or a single frame remains."""
    ext, write = _writer()
    path = Path(str(stem) + ext)
    tmp = Path(str(stem) + ".tmp" + ext)
    stride = 1
    while True:
        sub = frames[::stride]
        fps = max(1, FPS // stride)
        write(sub, str(tmp), fps)
        if tmp.stat().st_size <= MAX_BYTES or len(sub) <= 1:
            break
        stride *= 2
    os.replace(tmp, path)
    return path, fps, len(sub)


def _index(seed_dir: Path, node: str, path: Path, fps: int, n: int) -> None:
    idx = seed_dir / "index.json"
    try:
        data = json.loads(idx.read_text())
    except (OSError, ValueError):
        data = {}
    files = data.setdefault("files", {})
    files[str(node)] = {"file": path.name, "bytes": path.stat().st_size,
                        "frames": n, "fps": fps, "ts": time.time()}
    tmp = idx.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True, indent=1))
    os.replace(tmp, idx)


def index_of(root: str | os.PathLike, task: str, seed: int) -> dict:
    """The kept files for one (task, seed): ``{node: {file, bytes, frames, fps, ts}}``
    -- what the board's rsi_frames face lists. Empty when nothing was kept."""
    idx = Path(root) / str(task) / str(seed) / "index.json"
    try:
        return dict(json.loads(idx.read_text()).get("files") or {})
    except (OSError, ValueError):
        return {}
