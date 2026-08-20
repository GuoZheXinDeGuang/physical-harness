"""Runtime invariants, transplanted from dsh's `agent-loop/src/invariant.ts`.

dsh asserts, before every model request, that the messages being sent equal the
messages derivable from the durable log, so "model-visible IFF logged" is a
fact the process checks rather than a convention it documents. Governor asserts
the embodied analogue before every candidate dispatch:

  I1  critic-visible IFF logged   -- the view handed to candidate code has
      exactly the contents recorded for that step
  I2  privilege budget            -- the features actually READ fit the budget
      the candidate declared, checked against attested reads rather than the
      declaration

Both fail loudly. A harness that only documents these gets exactly the bug this
project already hit once (docs/headline-finding.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from governor.percept import FeatureView
from governor.features import REGISTRY, Privilege


class InvariantViolation(AssertionError):
    """A harness-owned relationship was broken. Never catch this to continue."""


@dataclass(frozen=True, slots=True)
class LoggedView:
    """What the durable log says a candidate was shown at one step."""

    episode: str
    step: int
    digest: str
    names: tuple[str, ...]


def record_view(view: FeatureView) -> LoggedView:
    """Freeze what the log will claim about this view, before anyone reads it."""
    return LoggedView(view.episode, view.step, view.digest(), tuple(sorted(view.snapshot())))


def assert_view_reconstructable(view: FeatureView, logged: LoggedView) -> None:
    """I1: the view about to be dispatched matches what was logged for that step.

    `view.digest()` is recomputed from live contents here. That is the entire
    mechanism: a cached digest captured at construction would compare a value to
    itself and pass unconditionally, catching nothing.
    """
    if view.episode != logged.episode or view.step != logged.step:
        raise InvariantViolation(
            f"view identity diverges from the log: view={view.episode}@{view.step} "
            f"logged={logged.episode}@{logged.step}"
        )
    live = view.digest()
    if live != logged.digest:
        added = sorted(set(view.snapshot()) - set(logged.names))
        removed = sorted(set(logged.names) - set(view.snapshot()))
        raise InvariantViolation(
            "critic-visible content diverges from the durable log "
            f"(episode={view.episode} step={view.step}); "
            f"added={added} removed={removed} live={live[:12]} logged={logged.digest[:12]}"
        )


def assert_privilege_budget(view: FeatureView, budget: int, *, role: str) -> None:
    """I2: features actually read fit `budget`.

    Checked against attested reads, so a candidate that declares zero privilege
    and then reads a privileged feature is caught by behaviour, not by its own
    declaration.
    """
    used = view.privilege_used()
    if used > budget:
        offenders = sorted(
            n for n in view.reads
            if REGISTRY[n].privilege is not Privilege.OBSERVABLE
        )
        raise InvariantViolation(
            f"{role} exceeded its privilege budget at {view.episode}@{view.step}: "
            f"used={used} budget={budget} privileged_reads={offenders}"
        )
