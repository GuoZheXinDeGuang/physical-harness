#!/usr/bin/env python3
"""Seed the physical-harness workspace into dsh's durable registry.

WHY THIS EXISTS
    Out of the box the dsh console opens on an empty workspace registry, so the
    composer is disabled behind "选择一个工作区开始" until the operator picks a
    workspace by hand. The dsh client already auto-selects on boot: its
    `startInitialSelection` connects the single most-recent workspace and opens
    a blank session in it (runtime/src/client/workspaces/service.ts), and
    `recentWorkspace()` returns a workspace even when it owns zero sessions
    (it falls back to `createdAt`). So seeding ONE workspace record is the whole
    unlock -- the composer is live the instant the page loads. No client
    behaviour is patched; this only writes a user-data storage unit.

WHAT IT WRITES
    $DSH_HOME/storages/workspace.json -- the storage-json backend's unit file
    for the `workspace` domain (version 2). The on-disk shape is fixed by
    packages/storage/storage-json/src/format.ts (serialize) and validated at
    startup by packages/workspace/workspace/src/index.ts (validateStoredState):
        {"unit": {...}, "global": {...}, "tables": {"workspaces": {...}}}
    The record shape is packages/workspace/workspace/src/spec.ts (workspaceRecord).

IDEMPOTENT + DRIFT-GUARDED
    Re-run any number of times: if a workspace already owns the canonical path,
    it is a no-op. If dsh ever bumps the workspace domain past version 2 the
    on-disk schema may change, so this refuses to touch an existing file whose
    unit version is not 2 and prints a [DRIFT] line instead of corrupting it.

TIMING
    The dsh server holds this unit open in memory and republishes the whole file
    on every mutation, so a seed written while the server is running is
    overwritten by the running process. Seed BEFORE the server starts (the
    cockpit runs the rebrand overlay, which calls this, before `exec dsh web`)
    or restart the server after seeding.

USAGE
    seed_workspace.py --workspace /abs/dir --dsh-home ~/.dsh
    seed_workspace.py --selftest        # hermetic assert-based check, temp dirs
"""
import argparse
import datetime
import json
import os
import sys
import tempfile

UNIT_VERSION = 2


def _now_iso() -> str:
    """ISO-8601 UTC instant with millisecond precision and a Z suffix."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _fresh_doc() -> dict:
    return {
        "unit": {"name": "workspace", "version": UNIT_VERSION},
        "global": {"initialized": True, "workspaceIds": [], "archivedSessionIds": []},
        "tables": {"workspaces": {}},
    }


def seed(workspace: str, dsh_home: str, *, log=print) -> int:
    """Ensure a workspace record for `workspace` exists in `dsh_home`.

    Returns 0 when the workspace is present after the call (seeded or already
    there), 1 on a skip/drift that left the goal unmet (bad path, unreadable
    file, or a foreign unit version). Never raises for those conditions -- a
    partly-native console still serves, same contract as the rebrand overlay.
    """
    ws_path = os.path.realpath(workspace) if workspace else ""
    if not ws_path or not os.path.isdir(ws_path):
        log("  [skip] workspace path is not a directory: %r" % workspace)
        return 1
    title = os.path.basename(ws_path)
    store = os.path.join(dsh_home, "storages", "workspace.json")

    if os.path.exists(store):
        try:
            with open(store, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as error:
            log("  [skip] workspace.json unreadable (%s); leaving as-is" % error)
            return 1
        version = doc.get("unit", {}).get("version")
        if version != UNIT_VERSION:
            log("  [DRIFT] workspace unit version %r != %d; not seeding "
                "(dsh schema changed -- update seed_workspace.py)" % (version, UNIT_VERSION))
            return 1
    else:
        doc = _fresh_doc()

    workspaces = doc.setdefault("tables", {}).setdefault("workspaces", {})
    for record in workspaces.values():
        if os.path.realpath(record.get("path", "")) == ws_path:
            log("  [already] workspace '%s' present" % title)
            return 0

    now = _now_iso()
    workspace_id = _uuid()
    workspaces[workspace_id] = {
        "path": ws_path,
        "title": title,
        "sessionIds": [],
        "createdAt": now,
        "updatedAt": now,
    }
    state = doc.setdefault("global", {})
    state["initialized"] = True
    state["workspaceIds"] = [workspace_id] + [
        w for w in state.get("workspaceIds", []) if w != workspace_id
    ]
    state.setdefault("archivedSessionIds", [])
    doc.setdefault("unit", {"name": "workspace", "version": UNIT_VERSION})

    _write_atomic(store, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    log("  [seeded] workspace '%s' -> %s" % (title, ws_path))
    return 0


def _uuid() -> str:
    import uuid
    return str(uuid.uuid4())


def _write_atomic(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _selftest() -> int:
    """Hermetic check: create -> idempotent re-run -> schema shape -> drift guard."""
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as ws:
        store = os.path.join(home, "storages", "workspace.json")

        assert seed(ws, home, log=lambda *_: None) == 0
        doc = json.load(open(store, encoding="utf-8"))
        # Shape matches the storage-json unit contract.
        assert doc["unit"] == {"name": "workspace", "version": 2}, doc["unit"]
        assert doc["global"]["initialized"] is True
        rows = doc["tables"]["workspaces"]
        assert len(rows) == 1, rows
        (wid, rec), = rows.items()
        assert doc["global"]["workspaceIds"] == [wid]
        assert rec["path"] == os.path.realpath(ws)
        assert rec["title"] == os.path.basename(os.path.realpath(ws))
        assert rec["sessionIds"] == []
        assert rec["createdAt"] == rec["updatedAt"]

        # Idempotent: a second seed adds nothing and keeps the same id.
        assert seed(ws, home, log=lambda *_: None) == 0
        doc2 = json.load(open(store, encoding="utf-8"))
        assert list(doc2["tables"]["workspaces"]) == [wid], doc2["tables"]["workspaces"]

        # Drift guard: a foreign unit version is refused, file untouched.
        bad = dict(doc2, unit={"name": "workspace", "version": 3})
        _write_atomic(store, json.dumps(bad) + "\n")
        assert seed(ws, home, log=lambda *_: None) == 1
        assert json.load(open(store, encoding="utf-8"))["unit"]["version"] == 3

        # Bad workspace path is a graceful skip, not a crash.
        assert seed(os.path.join(ws, "nope"), home, log=lambda *_: None) == 1
    print("seed_workspace selftest: ok")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", help="absolute directory to register as a workspace")
    parser.add_argument("--dsh-home", default=os.environ.get("DSH_HOME") or
                        os.path.join(os.path.expanduser("~"), ".dsh"))
    parser.add_argument("--selftest", action="store_true", help="run the hermetic self-check and exit")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.workspace:
        parser.error("--workspace is required (or pass --selftest)")
    return seed(args.workspace, args.dsh_home)


if __name__ == "__main__":
    sys.exit(main())
