"""docs/ is a closed allowlist (docs/README.md states the rule).

A doc that ships is a doc someone has to keep true, and CLAUDE.md requires
behaviour and its doc to move in the same commit. That promise is affordable for
a handful of files and empty for thirty, so the set is closed by a test rather
than by intent: adding a file here fails until a human edits the list and says
why in the commit.

The counterpart rule -- no PUBLIC link may point into the local archive -- is
what keeps a retired doc honest. Citing `local-archive/...` from a docstring is
fine and deliberate (it tells a reader the reference is not in the clone); a
`docs/<retired>.md` link would claim something ships that does not.
"""

from __future__ import annotations

from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"

#: Every prose file the public repository carries. Edit deliberately.
ALLOWED = {
    "README.md",
    "project-documentation.md",
    "rsi-mechanism.md",
    "base-gate.md",
    "sim-adaptation.md",
    "plug-in-your-model.md",
    "ph-station-design.md",
    "fast-slow-brain-design.md",
    "pi05-segment-goal.md",
}


def test_docs_is_a_closed_set():
    present = {p.name for p in DOCS.glob("*.md")}
    extra = present - ALLOWED
    assert not extra, (
        f"docs/ grew: {sorted(extra)}. Either the file belongs in "
        f"local-archive/docs/ (the default -- see docs/README.md), or it earns a "
        f"seat here and this list gains it in the same commit."
    )
    missing = ALLOWED - present
    assert not missing, (
        f"allowlisted but absent: {sorted(missing)}. A retired doc leaves the "
        f"list in the commit that retires it, so the list never lies."
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
