"""docs/ is a closed allowlist of ONE file (CLAUDE.md states the rule).

A doc that ships is a doc someone has to keep true, and CLAUDE.md requires
behaviour and its doc to move in the same commit. That promise is affordable for
one file and empty for thirty, so the set is closed by a test rather than by
intent: adding a file here fails until a human edits the list and says why in
the commit. Development docs go to `docs-dev/` (git-ignored, the operator's
live worklog) and retired design capital to `local-archive/docs/`.

Two counterpart rules keep the arrangement honest:

- No PUBLIC link may point into the local archive. Citing `local-archive/...`
  from a docstring is fine and deliberate (it tells a reader the reference is
  not in the clone); a `docs/<retired>.md` link would claim something ships
  that does not.
- `docs-dev/` is never tracked. The .gitignore entry carries no trailing slash
  on purpose -- a pattern with one matches only a directory, and this repo has
  already lost data to `runs/` failing that way -- so the rule is checked
  against git itself, not against the ignore file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"

#: Every prose file the public repository carries. Edit deliberately.
ALLOWED = {
    "project-documentation.md",
}


def test_docs_is_a_closed_set():
    present = {p.name for p in DOCS.glob("*.md")}
    extra = present - ALLOWED
    assert not extra, (
        f"docs/ grew: {sorted(extra)}. A development doc belongs in `docs-dev/` "
        f"and retired design capital in `local-archive/docs/` (the defaults -- "
        f"see CLAUDE.md); shipping one instead means this list gains it in the "
        f"same commit."
    )
    missing = ALLOWED - present
    assert not missing, (
        f"allowlisted but absent: {sorted(missing)}. A retired doc leaves the "
        f"list in the commit that retires it, so the list never lies."
    )


def test_docs_has_no_exempt_subdirectory():
    """The allowlist globs the top level, so a subdirectory would be a blind spot.

    It was one: a `docs/design/` carve-out for "diagram sources, not prose" ended
    up holding 110 KB of design Q&A. Prose parks wherever the rule is not looking,
    so the rule looks everywhere.
    """
    nested = [str(p.relative_to(DOCS)) for p in DOCS.rglob("*") if p.is_dir()]
    assert not nested, (
        f"docs/ gained subdirectories: {nested}. There is no exempt corner -- "
        f"development docs go to docs-dev/, retired ones to local-archive/docs/."
    )


def test_no_public_link_points_into_the_local_archive_as_if_it_shipped():
    """A `docs/<name>.md` link must resolve inside the clone."""
    broken: list[str] = []
    for doc in DOCS.glob("*.md"):
        for lineno, line in enumerate(doc.read_text().splitlines(), 1):
            for chunk in line.split("docs/")[1:]:
                name = chunk.split(")")[0].split(" ")[0].split(")")[0]
                if name.endswith(".md") and "/" not in name and name not in ALLOWED:
                    broken.append(f"{doc.name}:{lineno} -> docs/{name}")
    assert not broken, f"public links to retired docs: {broken}"


def test_docs_dev_is_never_tracked():
    """The dev worklog is local state; git must not know about it."""
    tracked = subprocess.run(
        ["git", "ls-files", "docs-dev"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    assert not tracked, (
        f"docs-dev/ is tracked: {tracked}. It is the operator's local worklog -- "
        f"`git rm --cached` it; the public set is docs/project-documentation.md."
    )
