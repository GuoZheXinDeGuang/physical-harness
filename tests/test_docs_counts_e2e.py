"""Docs carry the base-lane COMMAND, never a hand-synced pass/skip count.

Literal counts (``846 passed, 31 skipped, ...``) lived in three files and drifted
every time a test landed. The invariant that matters is "base lane green with
zero simulators installed"; the number is whatever the command prints. This runs
the real ``git grep`` over the public docs so a count cannot creep back in.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = ["README.md", "README.zh.md", "docs/project-documentation.md", "CLAUDE.md"]
LANE = 'pytest -o addopts="" -q -m "not robosuite and not robocasa"'


def _git_grep(pattern: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "grep", "-nE", pattern, "--", *DOCS],
        cwd=REPO, capture_output=True, text=True, check=False,
    )


def test_no_hand_synced_test_counts_in_docs():
    hits = _git_grep(r"[0-9]{3} passed|[0-9]{2,3} skipped|[0-9]{2,3} deselected")
    assert hits.returncode == 1, f"hand-synced test counts came back:\n{hits.stdout}"


def test_docs_carry_the_base_lane_command():
    for doc in ("README.md", "README.zh.md", "docs/project-documentation.md"):
        assert LANE in (REPO / doc).read_text(), f"{doc} lost the base-lane command"
