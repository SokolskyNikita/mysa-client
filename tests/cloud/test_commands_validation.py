"""Tests for shared command descriptions and cloud-side validation."""

from __future__ import annotations

from typing import Any

import pytest

from mysa_client.cloud import Capabilities, DeviceState, ModelFamily
from mysa_client.cloud.commands import settings_command, state_command
from mysa_client.cloud.const import Mode
from mysa_client.cloud.validation import validate_cloud_command
from tests.cloud_helpers import DEVICE_ID, load_json


def test_state_command_derives_expected_semantic_effects() -> None:
    command = state_command(
        {
            "targetHeat": {"setpoint": 22.5, "lockoutMin": 8},
            "modes": {"mode": int(Mode.HEAT)},
            "identity": {"fw": "3.17"},
        }
    )
    assert command.kind == "state"
    assert command.payload == {
        "targetHeat": {"setpoint": 22.5, "lockoutMin": 8},
        "modes": {"mode": int(Mode.HEAT)},
        "identity": {"fw": "3.17"},
    }
    assert command.expected == {
        "heat_setpoint": 22.5,
        "setpoint_min": 8,
        "mode": int(Mode.HEAT),
        "firmware": "3.17",
    }
    assert command.stored_fields == {}


def test_settings_command_maps_wire_fields_and_keeps_record_payload() -> None:
    payload = {
        "Format": "fahrenheit",
        "ButtonState": "Locked",
        "DutyCycleOpt": 8,
        "MinSetpoint": 5,
        "MaxSetpoint": 30,
        "MaxBrightness": 90,
        "MinBrightness": 20,
        "Name": "Kitchen",
    }
    command = settings_command(payload)
    assert command.kind == "settings"
    assert command.payload == payload
    assert command.expected == {
        "display_unit": "F",
        "button_lock": 3,
        "control_type": 8,
        "setpoint_min": 5,
        "setpoint_max": 30,
        "active_brightness": 90,
        "idle_brightness": 20,
    }
    assert command.stored_fields == payload


def test_command_descriptor_is_an_immutable_snapshot_of_inputs_and_views() -> None:
    changes: dict[str, Any] = {"targetHeat": {"setpoint": 22}}
    state = state_command(changes)
    changes["targetHeat"]["setpoint"] = 99
    state_payload = state.payload
    state_payload["targetHeat"]["setpoint"] = 100
    state_expected = state.expected
    state_expected["heat_setpoint"] = 101
    assert state.payload["targetHeat"]["setpoint"] == 22
    assert state.expected["heat_setpoint"] == 22

    settings_input: dict[str, Any] = {
        "Name": {"labels": ["original"]},
        "Format": "fahrenheit",
    }
    settings = settings_command(settings_input)
    settings_input["Name"]["labels"].append("mutated input")
    settings_view = settings.payload
    settings_view["Name"]["labels"].append("mutated view")
    stored_view = settings.stored_fields
    stored_view["Name"]["labels"].append("mutated stored view")
    assert settings.payload["Name"]["labels"] == ["original"]
    assert settings.stored_fields["Name"]["labels"] == ["original"]
    assert settings.expected["display_unit"] == "F"


def _state(**sections: Any) -> DeviceState:
    return DeviceState.from_api(DEVICE_ID, sections)


def _command_value(_name: str, fallback: Any) -> Any:
    return fallback


def test_common_validation_uses_reported_target_bounds() -> None:
    capabilities = Capabilities.from_api(load_json("capabilities_bb_v2_0_l.json"))
    state = _state(
        targetHeat={
            "reported": {
                "setpoint": 20,
                "lockoutMin": 10,
                "lockoutMax": 25,
                "timestamp": 1_791_830_400,
            }
        }
    )
    with pytest.raises(ValueError, match="known device limits"):
        validate_cloud_command(
            ModelFamily.BASEBOARD_LITE,
            capabilities,
            state,
            {"heat_setpoint": 8},
            _command_value,
        )


def test_common_validation_checks_choices_pairs_and_brightness_order() -> None:
    capabilities = Capabilities.from_api(load_json("capabilities_bb_v2_0_l.json"))
    state = _state()
    with pytest.raises(ValueError, match="Unsupported Mysa setting: display_unit"):
        validate_cloud_command(
            ModelFamily.BASEBOARD_LITE,
            capabilities,
            state,
            {"display_unit": "X"},
            _command_value,
        )
    with pytest.raises(ValueError, match="Minimum setpoint exceeds maximum"):
        validate_cloud_command(
            ModelFamily.BASEBOARD_LITE,
            capabilities,
            state,
            {"setpoint_min": 24, "setpoint_max": 20},
            _command_value,
        )
    with pytest.raises(ValueError, match="Brightness must satisfy"):
        validate_cloud_command(
            ModelFamily.BASEBOARD_LITE,
            capabilities,
            state,
            {"active_brightness": 20, "idle_brightness": 30},
            _command_value,
        )
    with pytest.raises(ValueError, match="Minimum setpoint exceeds maximum"):
        validate_cloud_command(
            ModelFamily.BASEBOARD_LITE,
            capabilities,
            None,
            {"setpoint_min": 24, "setpoint_max": 20},
            _command_value,
        )
    # The protocol accepts equal bounds, though Home Assistant's number
    # controls can impose the stricter UI invariant independently.
    validate_cloud_command(
        ModelFamily.BASEBOARD_LITE,
        capabilities,
        None,
        {"setpoint_min": 20, "setpoint_max": 20},
        _command_value,
    )


def test_experimental_validation_requires_capabilities_and_restricts_sensor() -> None:
    with pytest.raises(ValueError, match="capabilities are unavailable"):
        validate_cloud_command(
            ModelFamily.BASEBOARD_V3,
            None,
            None,
            {"mode": int(Mode.HEAT)},
            _command_value,
        )
    with pytest.raises(ValueError, match="Legacy device settings are not implemented"):
        validate_cloud_command(
            ModelFamily.BASEBOARD_V3,
            None,
            None,
            {},
            _command_value,
            command_kind="settings",
        )
    capabilities = Capabilities.from_api(
        load_json("app_4201_new_models.json")["models"]["CS-V1-0"]["capabilities"]
    )
    with pytest.raises(ValueError, match="readings only"):
        validate_cloud_command(
            ModelFamily.CLIMATE_SENSOR,
            capabilities,
            None,
            {},
            _command_value,
        )
