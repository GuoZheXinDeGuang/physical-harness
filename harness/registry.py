"""Provider loading by reference string.

Provider identity is a "package.module:factory" string on purpose: strings
survive multiprocessing spawn (module-global hooks do not, measured in phase
1), pickle cleanly into episode specs, and enter content hashes unchanged.
"""

from __future__ import annotations

import importlib
from typing import Any, Mapping


def load_provider(ref: str, params: Mapping[str, Any] | None = None) -> Any:
    if ":" not in ref:
        raise ValueError(f"provider ref must be 'module:factory', got {ref!r}")
    module_name, attr = ref.split(":", 1)
    factory = getattr(importlib.import_module(module_name), attr)
    return factory(**dict(params or {}))
