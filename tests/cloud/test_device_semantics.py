"""Reusable device facts and dependent command construction."""

from dataclasses import replace

import pytest

from mysa_client.cloud import DeviceState, Mode, ModelFamily
from mysa_client.cloud.device import brightness_pair, hvac_activity, hvac_fan_operation
from mysa_client.cloud.device_commands import brightness_command, climate_command


@pytest.mark.parametrize(
    ("mask", "expected"),
    [
        (256, "heating"),
        (512, "heating"),
        (4096, "heating"),
        (1024, "cooling"),
        (2048, "cooling"),
        (8192, "fan"),
        (0, "idle"),
        (1280, None),
    ],
)
def test_hvac_activity(mask, expected):
    state = replace(DeviceState.from_api("device", {}), mode=Mode.HEAT, hvac_state=mask)
    assert hvac_activity(state, lambda _: True) == expected
    assert hvac_activity(state, lambda _: False) is None


def test_brightness_adjusts_both_fields_and_exposes_same_effects_as_payload():
    command = brightness_command(12, active=True, wake=100, sleep=50)
    assert command.expected == {"active_brightness": 10, "idle_brightness": 10}
    assert command.payload == {
        "physicalInterface": {"activeIntensity": 10, "idleIntensity": 10}
    }
    assert brightness_pair(76, active=False, wake=50, sleep=0) == (75, 75)
    with pytest.raises(ValueError, match="Brightness"):
        brightness_pair(float("nan"), active=False)


def test_auto_command_is_atomic_and_requires_both_targets():
    command = climate_command(
        ModelFamily.CENTRAL_HVAC, Mode.HEAT, mode=Mode.AUTO, low=19, high=23
    )
    assert command.expected == {
        "mode": Mode.AUTO,
        "auto_heat_setpoint": 19,
        "auto_cool_setpoint": 23,
    }
    assert "targetAuto" in command.payload
    assert "targetHeat" not in command.payload
    with pytest.raises(ValueError, match="both low and high"):
        climate_command(ModelFamily.CENTRAL_HVAC, Mode.AUTO, low=19)


def test_fan_circulation_is_distinct_from_auto():
    state = replace(
        DeviceState.from_api("device", {}),
        fan_always_on=0,
        fan_runtime=900,
        fan_period=1800,
    )
    assert hvac_fan_operation(state) == "runtime"
    assert hvac_fan_operation(replace(state, fan_runtime=0)) == "auto"
    assert hvac_fan_operation(replace(state, fan_always_on=1)) == "on"


async def test_standalone_execution_validates_before_sending(session):
    from unittest.mock import AsyncMock, MagicMock

    from mysa_client.cloud import MysaClient

    client = MysaClient(session, MagicMock(), user_agent="test")
    client.update_state = AsyncMock()
    command = climate_command(ModelFamily.CENTRAL_HVAC, Mode.HEAT, temperature=21)
    with pytest.raises(ValueError, match="capabilities are unavailable"):
        await client.execute_command(
            "device",
            command,
            family=ModelFamily.CENTRAL_HVAC,
            capabilities=None,
            state=None,
        )
    client.update_state.assert_not_awaited()
    await client.execute_command(
        "device",
        brightness_command(20, active=True),
        family=ModelFamily.BASEBOARD_LITE,
        capabilities=None,
        state=None,
    )
    client.update_state.assert_awaited_once_with(
        "device", {"physicalInterface": {"activeIntensity": 20, "idleIntensity": 20}}
    )


async def test_standalone_execution_sends_the_validated_command_snapshot(session):
    from unittest.mock import AsyncMock, MagicMock

    from mysa_client.cloud import Capabilities, MysaClient
    from mysa_client.cloud.commands import state_command
    from tests.cloud_helpers import load_json

    client = MysaClient(session, MagicMock(), user_agent="test")
    client.update_state = AsyncMock()
    changes = {"modes": {"mode": int(Mode.HEAT)}, "targetHeat": {"setpoint": 22}}
    command = state_command(changes)
    changes["targetHeat"]["setpoint"] = 100
    command.payload["targetHeat"]["setpoint"] = 101
    command.expected["heat_setpoint"] = 102
    capabilities = Capabilities.from_api(
        load_json("app_4201_new_models.json")["models"]["BB-V3-0"]["capabilities"]
    )

    await client.execute_command(
        "device",
        command,
        family=ModelFamily.BASEBOARD_V3,
        capabilities=capabilities,
        state=None,
    )

    client.update_state.assert_awaited_once_with(
        "device",
        {"modes": {"mode": int(Mode.HEAT)}, "targetHeat": {"setpoint": 22}},
    )
