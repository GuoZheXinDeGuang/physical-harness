"""The cordis patch is RENDERED, never copied: profiles/dsh/deploy_profile.py.

The committed patch used to double as one machine's deployed artifact and carried
three absolute ``/home/<user>/...`` paths, and nothing ever deployed it -- so a
fresh clone on another box reached a console with NO
``mcp__physical-harness__*`` tools and the agent silently fell back to native
bash. What is pinned here is that the template carries no home directory, that
every placeholder is substituted from the repo root, that ``.env`` can move the
model route, and that the write is idempotent and atomic.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles" / "dsh"


def _load():
    spec = importlib.util.spec_from_file_location(
        "ph_deploy_profile", PROFILE_DIR / "deploy_profile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dp = _load()


def _deploy(repo: Path, home: Path) -> str:
    assert dp.deploy(repo, home, log=lambda *_: None) == 0
    return (home / "cordis.patch.yml").read_text(encoding="utf-8")


def test_template_hardcodes_no_home_directory():
    """The regression itself: a committed absolute home path is what broke the clone."""
    text = dp.TEMPLATE.read_text(encoding="utf-8")
    assert "/home/" not in text
    assert "/Users/" not in text
    assert os.path.expanduser("~") not in text


def test_every_placeholder_is_substituted(tmp_path):
    """No `${PH_...}` may survive: dsh would take it as a literal path."""
    out = _deploy(tmp_path / "repo", tmp_path / "home")
    assert "${PH_" not in out


def test_paths_come_from_the_repo_root(tmp_path):
    repo, home = tmp_path / "clone", tmp_path / "home"
    out = _deploy(repo, home)
    # Quoted whole scalars, so a repo path with a space cannot break the YAML.
    assert 'command: %s' % json.dumps(str(repo / ".venv" / "bin" / "python")) in out
    assert json.dumps(str(repo / "board" / "mcp_server.py")) in out
    assert json.dumps(str(repo / "runs")) in out


def test_repo_path_with_a_space_stays_valid_yaml(tmp_path):
    repo = tmp_path / "my clone"
    out = _deploy(repo, tmp_path / "home")
    assert '"%s"' % str(repo / "runs") in out


def test_missing_env_falls_back_to_reference_values(tmp_path):
    repo = tmp_path / "repo"
    assert not (repo / ".env").exists()
    out = _deploy(repo, tmp_path / "home")
    assert '"qwen3.8-27b"' in out
    assert '"http://127.0.0.1:30001/v1"' in out
    assert '"LOCAL_QWEN_API_KEY"' in out


def test_env_overrides_the_model_route(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text(
        "# a comment\n"
        "\n"
        "PH_MODEL_BASE_URL=http://10.0.0.9:8000/v1\n"
        "export PH_MODEL_ID='mistral-small'\n"
        'PH_MODEL_DISPLAY="Mistral Small: 24B"\n'
        "PH_MODEL_KEY_ENV=MY_KEY\n"
        "DEEPSEEK_API_KEY=not-a-placeholder-and-not-rendered\n",
        encoding="utf-8")
    out = _deploy(repo, tmp_path / "home")
    assert '"http://10.0.0.9:8000/v1"' in out
    assert '"mistral-small"' in out
    assert '"Mistral Small: 24B"' in out   # a colon that would break a plain scalar
    assert '"MY_KEY"' in out
    assert "qwen3.8-27b" not in out
    # A credential is a dsh concern, never rendered into the committed-derived file.
    assert "not-a-placeholder-and-not-rendered" not in out


def test_the_console_defaults_to_the_physical_preset(tmp_path):
    """`standard` mounts a full shell; `physical` is the harness's own preset."""
    out = _deploy(tmp_path / "repo", tmp_path / "home")
    assert "- id: agent-presets\n  config:\n    default: physical\n" in out


def test_idempotent_and_leaves_no_temp_residue(tmp_path):
    home = tmp_path / "home"
    first = _deploy(tmp_path / "repo", home)
    second = _deploy(tmp_path / "repo", home)
    assert first == second
    assert sorted(p.name for p in home.iterdir()) == ["cordis.patch.yml"]


def test_unknown_placeholder_is_loud(tmp_path):
    with pytest.raises(KeyError):
        dp.render("a: ${PH_NOT_A_KEY}\n", dp.defaults(tmp_path))


def test_read_env_last_assignment_wins_and_strips_quotes(tmp_path):
    env = tmp_path / ".env"
    env.write_text("K=one\n#K=commented\nK='two'\nJ=\"three\"\nnot an assignment\n",
                   encoding="utf-8")
    assert dp.read_env(env) == {"K": "two", "J": "three"}
    assert dp.read_env(tmp_path / "absent") == {}


def test_settings_notes_report_but_never_write(tmp_path):
    """settings.yaml outranks the patch; the deploy reports it and leaves it alone."""
    settings = tmp_path / "settings.yaml"
    original = "agent-default-model:\n  provider: deepseek\n  reasoningEffort: high\n"
    settings.write_text(original, encoding="utf-8")
    lines = []
    dp.settings_notes(settings, log=lines.append)
    assert any("SETTINGS WIN" in line for line in lines)
    assert any("reasoningEffort" in line for line in lines)
    assert settings.read_text(encoding="utf-8") == original
    # No settings.yaml at all (a fresh $DSH_HOME) is silent, not an error.
    lines.clear()
    dp.settings_notes(tmp_path / "none.yaml", log=lines.append)
    assert lines == []
