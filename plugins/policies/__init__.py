"""Governor policies: the reference PolicyFactory for capability `policy.driver`.

Thin adapter, on purpose. The scripted/cloned dispatch stays exactly where it
was -- `governor.policy._default_make_driver` -- so mounting this provider
through the kernel and calling `governor.policy.make_driver` with no ref must
resolve the identical driver class as before this seam existed. See
governor/policy.py's `make_driver` docstring for the dispatch this plugin is
mounted behind.
"""

from __future__ import annotations

from typing import Any

import governor.policy as _policy


class GovernorPolicies:
    """Layer 3 `harness.contracts.PolicyFactory`, backed by governor.policy verbatim."""

    def make_driver(self, spec: Any) -> Any:
        return _policy._default_make_driver(spec)


def provider(**params: Any) -> GovernorPolicies:
    return GovernorPolicies(**params)
