#!/usr/bin/env python3
"""Render the cordis patch template into `$DSH_HOME/cordis.patch.yml`.

WHY THIS EXISTS
    `$DSH_HOME/cordis.patch.yml` is what gives the console its
    `mcp__physical-harness__*` tools, its backbone LLM route, and the `physical`
    agent preset. dsh has no `--patch` flag, so the file must physically exist
    in $DSH_HOME before the server starts. Without it the agent has NO harness
    tools and falls back to native bash -- the ungoverned path the MCP server
    exists to prevent. The cockpit used to export DSH_HOME and seed the
    workspace but never deploy this file, so a fresh clone could never reach a
    working console.

WHY A TEMPLATE
    The committed file previously doubled as one machine's deployed artifact and
    carried three absolute `/home/<user>/...` paths. Every path here is derived
    from the REPO ROOT (this file's own location), so a clone anywhere renders
    correctly and no home directory is ever written down.

WHAT IS CONFIGURABLE
    The `PH_*` keys below, read from the repo root's git-ignored `.env` (see
    `.env.example`). Anything unset falls back to the reference deployment's
    value. `.env` is parsed here directly -- no third-party dependency; dsh's own
    credential store reads the same file for the API key.

IDEMPOTENT + ATOMIC
    Re-run any number of times: identical output is left untouched, and a write
    goes to a temp file in the destination directory and is `os.replace`d into
    place, so a reader never sees a half-written patch.

USAGE
    deploy_profile.py --dsh-home ~/.dsh
    deploy_profile.py --selftest        # hermetic assert-based check, temp dirs
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

#: The committed template, beside this script.
TEMPLATE = Path(__file__).resolve().parent / "cordis.patch.template.yml"
#: The repo root: profiles/dsh/deploy_profile.py -> profiles/dsh -> profiles -> repo.
REPO = Path(__file__).resolve().parents[2]

#: Only `${PH_...}` is a placeholder. `$DSH_HOME` and friends appear in the
#: template's comments and must survive rendering untouched.
_PLACEHOLDER = re.compile(r"\$\{(PH_[A-Z0-9_]+)\}")


def defaults(repo: Path) -> dict:
    """Every substitutable key, with the reference deployment's value.

    Paths are derived from `repo`; a key absent from this mapping is not
    settable from `.env` (rendering raises on an unknown placeholder instead of
    emitting a literal `${PH_...}` into the deployed file).
    """
    return {
        "PH_PYTHON": str(repo / ".venv" / "bin" / "python"),
        "PH_MCP_SERVER": str(repo / "board" / "mcp_server.py"),
        "PH_RUNS": str(repo / "runs"),
        "PH_MODEL_BASE_URL": "http://127.0.0.1:30001/v1",
        "PH_MODEL_ID": "qwen3.8-27b",
        "PH_MODEL_DISPLAY": "Qwen3.8-27B (local)",
        "PH_MODEL_KEY_ENV": "LOCAL_QWEN_API_KEY",
    }


def read_env(path: Path) -> dict:
    """Parse a `KEY=value` env file. Missing or unreadable file -> `{}`.

    Deliberately minimal and matched by `scripts/cockpit`'s shell reader: `#`
    comment lines and blanks are skipped, a leading `export ` is dropped, one
    layer of matching quotes is stripped, the value is NOT split on an inline
    `#`, and the last assignment of a key wins.
    """
    values = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def render(template: str, values: dict) -> str:
    """Substitute every `${PH_*}`; raise `KeyError` on one `values` has no entry for.

    Each placeholder stands for a WHOLE YAML scalar and is emitted JSON-quoted,
    so a repo path with a space or a display name with a colon cannot break the
    document. JSON's string escapes are a subset of YAML's double-quoted ones.
    """
    def one(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError("unknown placeholder ${%s} in %s" % (key, TEMPLATE.name))
        return json.dumps(values[key], ensure_ascii=False)

    return _PLACEHOLDER.sub(one, template)


def write_atomic(path: Path, text: str) -> None:
    """Replace `path` with `text` in one step: temp file in the same directory,
    then `os.replace`, so the console never reads a half-written patch.

    A near-copy of `seed_workspace.py`'s writer; `tests/test_boundaries.py`
    forbids anything under `profiles/` from importing a sibling, so these twelve
    stdlib lines are duplicated rather than shared.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def settings_notes(settings: Path, *, log=print) -> None:
    """Report the host-state traps this patch cannot fix. Never changes anything.

    `$DSH_HOME/settings.yaml` is the operator's own state and outranks the
    rendered entries, so a stale setting there silently defeats the deploy.
    """
    try:
        text = settings.read_text(encoding="utf-8")
    except OSError:
        return
    if "agent-default-model" in text:
        log("  [note] %s declares agent-default-model; SETTINGS WIN over the "
            "patch's row -- change it there too to move the default" % settings)
    if re.search(r"^\s*reasoningEffort:\s*high\b", text, re.MULTILINE):
        log("  [note] %s sets reasoningEffort: high, which fights the `physical` "
            "preset's one-call dispatch; left alone (operator state)" % settings)


def deploy(repo: Path, dsh_home: Path, *, log=print) -> int:
    """Render the template into `dsh_home/cordis.patch.yml`. 0 on success.

    Raises `OSError` when the template is unreadable or the destination cannot
    be written, and `KeyError` on an unknown placeholder -- a silently skipped
    deploy is the failure mode this whole script exists to remove.
    """
    values = defaults(repo)
    values.update({k: v for k, v in read_env(repo / ".env").items() if k in values})
    text = render(TEMPLATE.read_text(encoding="utf-8"), values)

    dest = Path(dsh_home) / "cordis.patch.yml"
    try:
        current = dest.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current == text:
        log("  [already] %s is current" % dest)
    else:
        write_atomic(dest, text)
        log("  [deployed] %s (mcp server + %s route + `physical` preset)"
            % (dest, values["PH_MODEL_ID"]))
    settings_notes(Path(dsh_home) / "settings.yaml", log=log)
    return 0


def _selftest() -> int:
    """Hermetic check: render -> no placeholders -> .env override -> idempotent."""
    def quiet(*_):
        return None

    with tempfile.TemporaryDirectory() as home_s, tempfile.TemporaryDirectory() as repo_s:
        home, repo = Path(home_s), Path(repo_s)
        dest = home / "cordis.patch.yml"

        assert deploy(repo, home, log=quiet) == 0
        out = dest.read_text(encoding="utf-8")
        assert "${PH_" not in out, "unsubstituted placeholder survived"
        assert json.dumps(str(repo / "board" / "mcp_server.py")) in out
        assert "default: physical" in out

        # .env overrides a default; an unrelated key (a credential) is ignored.
        (repo / ".env").write_text(
            "# comment\nexport PH_MODEL_ID='my-model'\nDEEPSEEK_API_KEY=x\n", encoding="utf-8")
        assert deploy(repo, home, log=quiet) == 0
        out = dest.read_text(encoding="utf-8")
        assert '"my-model"' in out and "qwen3.8-27b" not in out

        # Idempotent: a re-run leaves the exact bytes and no temp residue.
        assert deploy(repo, home, log=quiet) == 0
        assert dest.read_text(encoding="utf-8") == out
        assert [p.name for p in home.iterdir()] == ["cordis.patch.yml"]

        # An unknown placeholder is loud, never emitted literally.
        try:
            render("a: ${PH_NOPE}\n", defaults(repo))
        except KeyError:
            pass
        else:
            raise AssertionError("unknown placeholder did not raise")
    print("deploy_profile selftest: ok")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=str(REPO),
                        help="repository root (default: derived from this file)")
    parser.add_argument("--dsh-home", default=os.environ.get("DSH_HOME") or
                        os.path.join(os.path.expanduser("~"), ".dsh"))
    parser.add_argument("--selftest", action="store_true",
                        help="run the hermetic self-check and exit")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    try:
        return deploy(Path(args.repo), Path(args.dsh_home))
    except (OSError, KeyError) as error:
        print("  [FAILED] cordis patch not deployed: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
