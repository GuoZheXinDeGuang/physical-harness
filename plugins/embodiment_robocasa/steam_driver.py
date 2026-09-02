"""Composite ``policy.driver`` for the steam_prep persistent mission
(MultistepSteaming): faucet on -> vegetable into the sink -> faucet off ->
vegetable into the pot -> pot onto the chosen burner. The graph/predicates are
complete; the DRIVER surface is honest about its gap: there is NO faucet driver
yet (the handle needs the same hinge-arc torque the phase-3 door work proved the
straight-line OSC cannot produce), so the faucet stages are a stub that burns a
small cap and fails at the water-on verify -- the mission's xfail frontier
("awaiting sink driver"), not a fake success.

Everything else rides existing primitives: GraspDriver for the vegetable and
pot, PlaceDriver into the sink cavity, ReceptaclePlaceDriver into the pot, plus
a burner-site place for the final pot move.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from plugins.embodiment_robocasa import drivers as D
from plugins.embodiment_robocasa import stage_extras as X


class FaucetStubDriver:
    """The honest placeholder for the missing sink-handle skill: holds still
    (zero action) and is done only if the faucet is ALREADY in the desired
    state. A real scene starts water-off, so faucet_on burns its cap and the
    water-on verify fails -- the graph's declared frontier.
    """
    # ponytail: awaiting sink driver -- the handle is a hinge-arc actuation the
    # scripted OSC cannot produce (phase-3 door finding); replace this stub when
    # a faucet skill lands, the graph/verify layer needs no change.

    def __init__(self, on: bool = True):
        self.on = on

    def act(self, env, obs):
        return np.zeros(D.ADIM)

    def done(self, env) -> bool:
        return bool(env.sink.get_handle_state(env=env)["water_on"]) is self.on


class BurnerPlaceDriver(X.PointPlaceDriver):
    """Place the held pot onto the episode's CHOSEN burner (env.knob, sealed in
    ep_meta): target that burner site's centre at stove-top height."""

    DROP_DZ = 0.06

    def __init__(self, obj_name: str = "pot"):
        super().__init__(obj_name)
        self._point = None

    def _drop_point(self, env) -> np.ndarray:
        if self._point is None:
            stove = D._fixture(env, "stove")
            site = stove.burner_sites[env.knob]
            p = np.asarray(env.sim.data.get_site_xpos(site.get("name")), float)
            self._point = np.array([p[0], p[1], p[2] + self.DROP_DZ])
        return self._point

    def done(self, env) -> bool:
        import robocasa.utils.object_utils as OU

        return bool(env._check_obj_location_on_stove(self.obj_name) == env.knob
                    and OU.gripper_obj_far(env, obj_name=self.obj_name))


#: spec.task -> (stage factory, step cap). Faucet caps are small on purpose --
#: the stub cannot succeed, so its honest failure should be cheap.
_STAGES: dict[str, tuple[Any, int]] = {
    "faucet_on":   (lambda: FaucetStubDriver(on=True), 60),
    "grasp_veg":   (lambda: D.GraspDriver("vegetable1"), 600),
    "sink_veg":    (lambda: D.PlaceDriver("vegetable1", "sink"), 300),
    "faucet_off":  (lambda: FaucetStubDriver(on=False), 60),
    "regrasp_veg": (lambda: D.GraspDriver("vegetable1"), 600),
    "carry_veg":   (lambda: X.NavToObjectDriver("stove_counter", "pot",
                                                carry=True), 700),
    "pot_veg":     (lambda: X.ReceptaclePlaceDriver("vegetable1", "pot"), 300),
    "grasp_pot":   (lambda: D.GraspDriver("pot"), 600),
    "burner_pot":  (lambda: BurnerPlaceDriver("pot"), 300),
}


def provider(**params: Any) -> X.CompositePolicies:
    return X.CompositePolicies(_STAGES, "robocasa_steam_prep@v1", **params)


if __name__ == "__main__":
    drv = provider().make_driver(object())

    class _S:
        task = "faucet_on"
    drv.enter_segment(object(), _S())
    assert isinstance(drv._stage, FaucetStubDriver) and drv._stage.on is True
    a = drv.act({})   # the stub holds still on a fake env -- no sim needed
    assert a.shape == (D.ADIM,) and not a.any()

    class _S2:
        task = "burner_pot"
    drv.enter_segment(object(), _S2())
    assert isinstance(drv._stage, BurnerPlaceDriver)
    print("plugins/embodiment_robocasa/steam_driver.py self-check OK")
