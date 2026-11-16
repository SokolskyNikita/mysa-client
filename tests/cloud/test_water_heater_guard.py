"""A Water Heater Controller is recognised and its setpoint writes are refused.

Its ``targetHeat`` setpoint is the thermal safety limit, not a room target, so
the encoders that would write it must refuse the model.
"""

from __future__ import annotations

import pytest

from mysa_client.cloud import (
    MODEL_NAMES,
    WIFI_ONLY_FAMILIES,
    Capabilities,
    Mode,
    ModelFamily,
    model_family,
)
from mysa_client.cloud.commands import setpoint_limit_changes, temperature_changes
from mysa_client.cloud.const import EXPERIMENTAL_CLOUD_FAMILIES
from mysa_client.cloud.setpoints import SetpointModeError, SetpointRules
from mysa_client.cloud.validation import validate_cloud_command

RULES = SetpointRules()


@pytest.mark.parametrize(
    "model", ["WHC-V1-0", "whc-v1-0", "WHC-V1", "whc-v1", " WHC-V1-0 "]
)
def test_model_strings_are_the_water_heater(model: str) -> None:
    assert model_family(model) is ModelFamily.WATER_HEATER


def test_water_heater_is_recognised_but_not_supported() -> None:
    assert MODEL_NAMES["WHC-V1-0"] == "Mysa Water Heater Controller"
    assert ModelFamily.WATER_HEATER not in WIFI_ONLY_FAMILIES
    assert ModelFamily.WATER_HEATER not in EXPERIMENTAL_CLOUD_FAMILIES
    assert Capabilities.from_api({}).basic_modes(ModelFamily.WATER_HEATER) == []


@pytest.mark.parametrize("mode", [None, Mode.HEAT, Mode.OFF, Mode.COOL, Mode.AUTO])
def test_setpoint_write_is_refused_in_every_mode(mode: Mode | None) -> None:
    setpoint = RULES.setpoint(Mode.HEAT, 21)
    with pytest.raises(SetpointModeError, match="thermal safety limit") as err:
        temperature_changes(mode, setpoint, family=ModelFamily.WATER_HEATER)
    assert err.value.mode is mode


@pytest.mark.parametrize("minimum", [True, False])
def test_setpoint_limit_write_is_refused(minimum: bool) -> None:
    limit = RULES.limit(28, minimum=minimum)
    with pytest.raises(SetpointModeError, match="thermal safety limit"):
        setpoint_limit_changes(limit, minimum=minimum, family=ModelFamily.WATER_HEATER)


def test_other_families_and_no_family_are_unchanged() -> None:
    setpoint = RULES.setpoint(Mode.HEAT, 21)
    expected = {"targetHeat": {"setpoint": 21.0}}
    assert temperature_changes(Mode.HEAT, setpoint) == expected
    assert (
        temperature_changes(Mode.HEAT, setpoint, family=ModelFamily.BASEBOARD_LITE)
        == expected
    )


@pytest.mark.parametrize(
    "field",
    ["heat_setpoint", "cool_setpoint", "setpoint_min", "setpoint_max", "cool_max"],
)
def test_validation_refuses_setpoints_and_limits(field: str) -> None:
    with pytest.raises(ValueError, match="no setpoint"):
        validate_cloud_command(
            ModelFamily.WATER_HEATER, None, None, {field: 21}, lambda _n, value: value
        )


def test_validation_leaves_other_commands_alone() -> None:
    """A mode write is allowed, as it was while the model was unknown."""
    for family in (ModelFamily.WATER_HEATER, None):
        validate_cloud_command(
            family, None, None, {"mode": Mode.OFF}, lambda _n, value: value
        )
