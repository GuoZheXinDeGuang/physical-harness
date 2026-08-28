# docs/ — the public set, and why it is closed

This directory is an **allowlist**, not a folder. `tests/test_docs_allowlist.py`
fails if a file appears here that is not on the list below, so growing the set is
a deliberate act with a review, not a side effect of a busy week.

## The rule

**A file lives here only if an outside reader needs it to use or extend this
repository.** Everything else — round plans, scout reports, design drafts,
acceptance write-ups, overnight goals, paper notes — belongs in
`local-archive/docs/` (git-ignored, kept on the operator's box).

The test of membership is not "is it useful" but **"would a stranger cloning
this repo be worse off without it"**. Design capital that only explains how we
got here is history: it lives in git history and in the local archive.

Two consequences worth stating plainly:

- **A doc here is maintained.** `CLAUDE.md` already requires that behaviour and
  its doc change in the same commit. A closed set is what makes that promise
  affordable — eight files can be kept true, thirty cannot.
- **Adding one means retiring one, or arguing for the seat.** The list below is
  edited by a human, in a commit that says why.

## The set

| File | What it answers | Who reads it |
|---|---|---|
| `project-documentation.md` | Where things live, who owns which decision | anyone new |
| `rsi-mechanism.md` | How the self-improvement chain runs and what its gates mean | anyone submitting an rsi brief |
| `base-gate.md` | The test snapshot and how to reproduce it isolated | anyone changing the base |
| `sim-adaptation.md` | How to add a simulator (venv, card, traps) | anyone adding an embodiment |
| `plug-in-your-model.md` | How to bring your own VLM planner, VLA policy, or recovery primitives | anyone integrating a model |
| `ph-station-design.md` | The console: fork rationale, board bridge, backbone LLM deployment | anyone touching the UI or its config |
| `fast-slow-brain-design.md` | Where a learned policy plugs in beside the scripted drivers | anyone working on the VLA seam |
| `pi05-segment-goal.md` | The open campaign: its goal, its three gates, its stop conditions | anyone joining that work |

`docs/design/` holds diagram sources for the images the READMEs embed; it is not
prose and is exempt.

## Where the rest went

`local-archive/docs/retired-from-public/` — 22 files retired on 2026-08-29, all
still readable on this box and all still in git history. Code that cited them
now cites that path, so a reader can tell at a glance that the reference is
local: a dangling public link would be a lie about what ships.
