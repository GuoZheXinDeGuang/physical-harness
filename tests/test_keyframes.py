"""Keyframes: stills pinned to opstream events (harness.opstream.on_emit +
scripts/frame_dump.keyframe + board.store.read_runtime_keyframe[s]).

Four layers, mirroring test_runtime_frame: the hook contract (fires only on a
landed event, a raising listener can never reach the task), the capture layer
(kinds as data, follows --frames, per-boot ceiling, cleared per boot), face
equivalence (storecli dispatch == MCP tool == board.store), and the INVARIANT
that makes "live state, not evidence" mechanical -- deleting the whole
keyframes directory leaves every sealed artifact byte-identical and the chain
verifying.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import types
from pathlib import Path

import pytest
from test_read_session import _session
from test_runtime_frame import _FakeEnv

from board import mcp_server as ms
from board import store as bs
from board import storecli
from harness import opstream
from harness.events import SessionLog
from plugins.task import workload
from scripts import frame_dump
from scripts import harness_runtime as runtime


@pytest.fixture(autouse=True)
def _disarm():
    """Both writers are module-level singletons (one resident runtime per
    process); restore them and the hook list after every test."""
    hooks = list(opstream._hooks)
    yield
    opstream._path = None
    opstream._seq = 0
    opstream._hooks[:] = hooks
    frame_dump.arm(None)


def _armed(tmp_path: Path) -> Path:
    """A session dir with both live-state writers armed, as boot() arms them."""
    session = tmp_path / "session-main"
    session.mkdir(parents=True, exist_ok=True)
    frame_dump.arm(session / "frame.jpg")
    opstream.arm(session / "runtime_events.jsonl")
    return session


def _capture(session: Path, kinds) -> None:
    """Drive the overlay the way a rollout does, then emit ``kinds``."""
    env = frame_dump._FrameEnv(_FakeEnv())
    env.reset()
    for kind in kinds:
        opstream.emit(kind, node="n1")


# --- the hook slot (harness side) --------------------------------------------

def test_on_emit_fires_per_landed_event_and_swallows_listeners(tmp_path):
    seen: list[tuple[int, str]] = []
    opstream.on_emit(lambda seq, kind: (_ for _ in ()).throw(RuntimeError("boom")))
    opstream.on_emit(lambda seq, kind: seen.append((seq, kind)))

    # unarmed: emit is a no-op, so no listener fires
    opstream._path = None
    opstream.emit("node_start")
    assert seen == []

    opstream.arm(tmp_path / "runtime_events.jsonl")
    opstream.emit("boot")
    opstream.emit("node_start")
    # a raising listener neither reaches the caller nor blocks the next one
    assert seen == [(1, "boot"), (2, "node_start")]

    # a dropped event (destination gone) fires nothing: the hook contract is
    # "landed in the feed", so an index can never point at a missing line
    (tmp_path / "runtime_events.jsonl").unlink()
    tmp_path.chmod(0o500)
    try:
        opstream.emit("node_verified")
    finally:
        tmp_path.chmod(0o700)
    assert seen[-1] == (2, "node_start")


def test_arm_clears_the_keyframe_directory(tmp_path):
    d = tmp_path / "keyframes"
    d.mkdir()
    (d / "000004-node_start.jpg").write_bytes(b"stale")
    assert opstream.keyframe_dir() is None, "unarmed: no directory to name"

    opstream.arm(tmp_path / "runtime_events.jsonl")
    assert opstream.keyframe_dir() == str(d)
    assert list(d.iterdir()) == [], "arm clears: same truncate-per-boot horizon"
    # an absent directory is not an error either (the common first boot)
    d.rmdir()
    opstream.arm(tmp_path / "runtime_events.jsonl")


# --- the capture layer (scripts side) ----------------------------------------

def test_keyframe_kinds_are_data_not_branches():
    """The listener's only decision is set membership -- widening the capture
    set is a constant edit, never a new code path."""
    assert "stage_transition" in frame_dump.KEYFRAME_KINDS
    assert "actuation_start" not in frame_dump.KEYFRAME_KINDS, "per-step kinds stay out"
    assert isinstance(frame_dump.KEYFRAME_KINDS, frozenset), "a constant, not a mutable"


def test_capture_writes_seq_and_kind_addressed_stills(tmp_path):
    pytest.importorskip("PIL.Image", reason="Pillow not installed (rides the sim extras)")
    session = _armed(tmp_path)
    _capture(session, ("node_start", "actuation_start", "node_verified"))

    names = sorted(p.name for p in (session / "keyframes").iterdir())
    assert names == ["000001-node_start.jpg", "000003-node_verified.jpg"], \
        "one still per KEYFRAME_KINDS event, addressed by the feed seq"
    index = bs.read_runtime_keyframes(session)
    assert [(f["seq"], f["kind"]) for f in index["frames"]] == [
        (1, "node_start"), (3, "node_verified")]
    assert index["count"] == 2 and all(isinstance(f["ts"], float) for f in index["frames"])
    # the still is the same picture the live viewport publishes
    assert bs.read_runtime_keyframe(session, 1)["jpeg_b64"] == \
        bs.read_runtime_frame(session)["jpeg_b64"]


def test_capture_follows_the_frames_switch_and_the_open_world(tmp_path):
    session = tmp_path / "session-main"
    session.mkdir()
    # frames OFF (the runtime disarms frame_dump when --frames is absent)
    frame_dump.arm(None)
    opstream.arm(session / "runtime_events.jsonl")
    _capture(session, ("node_start",))
    assert not (session / "keyframes").exists(), "no frames switch, no keyframes"

    # frames ON but no world open (task_done fires after env.close())
    frame_dump.arm(session / "frame.jpg")
    env = frame_dump._FrameEnv(_FakeEnv())
    env.reset()
    env.close()
    assert frame_dump._LAST_ENV is None, "close retracts the listener's handle"
    opstream.emit("task_done")
    assert bs.read_runtime_keyframes(session)["count"] == 0


def test_capture_stops_at_the_per_boot_ceiling(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image", reason="Pillow not installed (rides the sim extras)")
    monkeypatch.setattr(frame_dump, "MAX_KEYFRAMES", 2)
    session = _armed(tmp_path)
    _capture(session, ("node_start",) * 5)
    assert bs.read_runtime_keyframes(session)["count"] == 2, "ceiling holds"
    # ...and it is a ceiling, not an error: the feed kept every event
    assert bs.read_runtime_events(session)["last_seq"] == 5
    # a re-boot clears the directory AND the budget
    opstream.arm(session / "runtime_events.jsonl")
    frame_dump.arm(session / "frame.jpg")
    assert bs.read_runtime_keyframes(session)["count"] == 0
    _capture(session, ("node_start",))
    assert bs.read_runtime_keyframes(session)["count"] == 1


def test_capture_never_raises(tmp_path):
    """A broken render, a vanished directory, an unarmed feed: all swallowed,
    exactly like dump() -- a lost still can never fail a task."""
    session = _armed(tmp_path)

    class _Broken:
        @property
        def sim(self):
            raise RuntimeError("no GL")

    frame_dump._LAST_ENV = _Broken()
    opstream.emit("node_start")
    assert bs.read_runtime_keyframes(session)["count"] == 0
    # keyframe_dir unwritable
    frame_dump._LAST_ENV = _FakeEnv()
    (session / "keyframes").mkdir(exist_ok=True)
    session.chmod(0o500)
    try:
        opstream.emit("node_verified")
    finally:
        session.chmod(0o700)


# --- the three faces ---------------------------------------------------------

def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_faces_are_byte_identical(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    sd = _session(runs)
    (sd / "keyframes").mkdir()
    (sd / "keyframes" / "000007-node_start.jpg").write_bytes(b"\xff\xd8jpegish\xff\xd9")
    (sd / "keyframes" / "000009-node_failed.jpg").write_bytes(b"\xff\xd8other\xff\xd9")
    ms.configure(runs)
    cli = dict(runs=runs, status=tmp_path / "S.md", progress=tmp_path / "p.md")

    index = bs.read_runtime_keyframes(sd)
    assert index["count"] == 2, "non-trivial fixture: identity is not identity-of-empty"
    assert _same(ms.runtime_keyframes("session-main"), index)
    assert _same(storecli.dispatch("runtime_keyframes", "session-main", **cli), index)

    one = bs.read_runtime_keyframe(sd, 9)
    assert one["kind"] == "node_failed" and one["jpeg_b64"]
    assert _same(ms.runtime_keyframe("session-main", 9), one)
    assert _same(storecli.dispatch("runtime_keyframe", "session-main", seq=9, **cli), one)

    # the shared safe_child guard fronts both fns on both faces
    for fn in ("runtime_keyframes", "runtime_keyframe"):
        with pytest.raises(ValueError):
            storecli.dispatch(fn, "../session-main", **cli)
    assert ms.runtime_keyframes("../session-main") == {"error": "unknown session"}
    assert ms.runtime_keyframe("../session-main", 9) == {"error": "unknown session"}


def test_absent_keyframe_reads_as_empty_and_error_on_every_face(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    sd = _session(runs)
    ms.configure(runs)
    cli = dict(runs=runs, status=tmp_path / "S.md", progress=tmp_path / "p.md")

    empty = {"frames": [], "count": 0}
    assert bs.read_runtime_keyframes(sd) == empty
    assert ms.runtime_keyframes("session-main") == empty
    assert storecli.dispatch("runtime_keyframes", "session-main", **cli) == empty

    missing = {"error": "no keyframe"}
    assert bs.read_runtime_keyframe(sd, 4) == missing
    assert ms.runtime_keyframe("session-main", 4) == missing
    assert storecli.dispatch("runtime_keyframe", "session-main", seq=4, **cli) == missing


def test_index_ignores_non_keyframe_files(tmp_path):
    """A mid-publish .tmp (or anything else that wanders in) is not a frame."""
    sd = _session(tmp_path)
    (sd / "keyframes").mkdir()
    for name in ("000002-node_start.jpg.tmp", "notes.txt", "000003.jpg",
                 "-node_start.jpg", "abc-node_start.jpg"):
        (sd / "keyframes" / name).write_bytes(b"x")
    (sd / "keyframes" / "000002-node_start.jpg").write_bytes(b"\xff\xd8ok\xff\xd9")
    assert bs.read_runtime_keyframes(sd) == {
        "frames": [{"seq": 2, "kind": "node_start",
                    "ts": bs.read_runtime_keyframes(sd)["frames"][0]["ts"]}],
        "count": 1}


def test_storecli_serve_forwards_seq(tmp_path):
    """The resident line-JSON worker the bridge rides carries the seq cursor."""
    import io

    runs = tmp_path / "runs"
    runs.mkdir()
    sd = _session(runs)
    (sd / "keyframes").mkdir()
    (sd / "keyframes" / "000005-stage_transition.jpg").write_bytes(b"\xff\xd8s\xff\xd9")
    reqs = "\n".join([
        json.dumps({"fn": "runtime_keyframes", "name": "session-main"}),
        json.dumps({"fn": "runtime_keyframe", "name": "session-main", "seq": 5}),
        json.dumps({"fn": "runtime_keyframe", "name": "session-main"}),
    ])
    out = io.StringIO()
    assert storecli.serve(io.StringIO(reqs + "\n"), out, runs,
                          tmp_path / "S.md", tmp_path / "p.md") == 0
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert lines[0]["count"] == 1
    assert lines[1]["kind"] == "stage_transition" and lines[1]["jpeg_b64"]
    assert lines[2] == {"error": "no keyframe"}, "no seq -> 0 -> nothing pinned"


# --- the invariant: live state, not evidence ---------------------------------

def _tree_digest(root: Path) -> str:
    """Byte-exact digest of every file under ``root`` (path + content)."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def test_deleting_every_keyframe_changes_no_sealed_byte(tmp_path, monkeypatch):
    """The mechanical proof of the charter's 'rendering is live state, not
    evidence'.

    A REAL runtime boot drains a REAL brief and seals a REAL chain while the
    listener captures stills through the real writer; then the whole keyframes/
    directory is deleted. Every sealed byte must hash the same, the chain must
    still verify, and the board must still answer -- an emptier index and a
    "no keyframe" are the only differences any reader can observe. If keyframes
    ever became evidence, this hash moves.
    """
    pytest.importorskip("PIL.Image", reason="Pillow not installed (rides the sim extras)")

    def _rollout(spec, bundle=None):
        # A world held open across nodes (the persistent-episode path), so the
        # listener has something to render when node_verified/task_done fire.
        env = frame_dump._FrameEnv(_FakeEnv())
        env.reset()
        env.step([0.0])
        return {"success": True, "steps": 10,
                "stages": [{"name": "grasp", "success": True}]}

    monkeypatch.setattr(workload, "_governed_rollout", _rollout)
    session = tmp_path / "session-main"
    (session / "inbox").mkdir(parents=True)
    (session / "inbox" / "a.json").write_text(
        json.dumps({"kind": "task", "task": "stack", "seed": 90901}))

    rt = runtime.main(session, drain=True, frames=True)
    frames = bs.read_runtime_keyframes(session)
    assert frames["count"] >= 1, "non-trivial fixture: the real drain left stills"
    assert {f["kind"] for f in frames["frames"]} <= frame_dump.KEYFRAME_KINDS
    assert bs.read_runtime_keyframe(session, frames["frames"][0]["seq"])["jpeg_b64"]

    sealed = session / "session-log"
    before = _tree_digest(sealed)
    before_rows = rt.log.rows()
    before_feed = bs.read_runtime_events(session)
    assert SessionLog.load(sealed).verify()
    # nothing sealed even NAMES a still or its directory -- the anchor is a
    # feed seq, so no sealed row can be left dangling by the deletion below
    rows_text = (sealed / "rows.jsonl").read_text()
    assert str(session / "keyframes") not in rows_text
    assert all(f"{f['seq']:06d}-{f['kind']}" not in rows_text for f in frames["frames"])

    shutil.rmtree(session / "keyframes")

    assert _tree_digest(sealed) == before, "a sealed byte moved when a still was deleted"
    assert SessionLog.load(sealed).rows() == before_rows
    assert SessionLog.load(sealed).verify(), "the chain broke without its stills"
    assert bs.read_session(session)["chain_ok"] is True
    # the live faces degrade to their absent-file replies, and nothing else
    assert bs.read_runtime_keyframes(session) == {"frames": [], "count": 0}
    assert bs.read_runtime_keyframe(session, 1) == {"error": "no keyframe"}
    assert bs.read_runtime_events(session) == before_feed, "the feed is untouched"
