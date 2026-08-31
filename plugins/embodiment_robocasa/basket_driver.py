"""Same-counter pick/place driver for ``basket_smoke_vlm``."""

from __future__ import annotations

from typing import Any

from plugins.embodiment_robocasa import drivers as D
from plugins.embodiment_robocasa import stage_extras as X

ITEMS = ("item0", "item1", "item2")

_STAGES: dict[str, tuple[Any, int]] = {}
for _item in ITEMS:
    _STAGES[f"grasp_{_item}"] = (lambda item=_item: D.GraspDriver(item), 600)
    _STAGES[f"pack_{_item}"] = (
        lambda item=_item: X.ReceptaclePlaceDriver(item, "basket"), 450)


def provider() -> X.CompositePolicies:
    return X.CompositePolicies(_STAGES, "robocasa_basket_smoke@v2")
