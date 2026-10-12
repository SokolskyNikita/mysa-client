"""Conservative validation for semantic Mysa cloud commands."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal

from .const import EXPERIMENTAL_CLOUD_FAMILIES, Mode, ModelFamily
from .models import Capabilities, DeviceState
from .observations import number


def validate_cloud_command(
    family: ModelFamily | None,
    caps: Capabilities | None,
    state: DeviceState | None,
    expected: Mapping[str, Any],
    command_value: Callable[[str, Any], Any],
    *,
    command_kind: Literal["state", "settings"] | None = None,
) -> None:
    """Validate known Mysa constraints for any command before sending it.

    Experimental cloud-only models require complete capability data and use a
    conservative allowlist. Established models retain permissive operation
    support, while advertised choices, observed setpoint bounds, and paired
    setting invariants are still enforced when their data is available.
    """
    if command_kind == "settings" and family in EXPERIMENTAL_CLOUD_FAMILIES:
        raise ValueError("Legacy device settings are not implemented for this model")

    if family in EXPERIMENTAL_CLOUD_FAMILIES:
        _validate_basic_command(family, caps, state, expected, command_value)

    _validate_target_bounds(state, expected)
    if caps is None:
        return

    choices = {
        "button_lock": caps.lockout_values,
        "control_type": caps.control_types,
        "display_unit": caps.units,
    }
    for name, values in choices.items():
        if name in expected and expected[name] not in values:
            raise ValueError(f"Unsupported Mysa setting: {name}")

    ranges = {
        "temperature_offset": caps.offset_range,
        "setpoint_min": caps.heat_setpoint_range,
        "setpoint_max": caps.heat_setpoint_range,
    }
    for name, limits in ranges.items():
        if name in expected and (
            limits is None
            or number(expected[name]) is None
            or not limits[0] <= expected[name] <= limits[1]
        ):
            raise ValueError(f"Mysa setting outside supported range: {name}")

    if any(name in expected for name in ("active_brightness", "idle_brightness")):
        if not caps.brightness:
            raise ValueError("Brightness is unsupported by this device")
        active = number(
            expected.get(
                "active_brightness",
                command_value(
                    "active_brightness", state.active_brightness if state else None
                ),
            )
        )
        idle = number(
            expected.get(
                "idle_brightness",
                command_value(
                    "idle_brightness", state.idle_brightness if state else None
                ),
            )
        )
        if active is not None and idle is not None and not 0 <= idle <= active <= 100:
            raise ValueError("Brightness must satisfy 0 <= idle <= active <= 100")

    if "setpoint_min" in expected or "setpoint_max" in expected:
        low = expected.get(
            "setpoint_min",
            command_value("setpoint_min", state.setpoint_min if state else None),
        )
        high = expected.get(
            "setpoint_max",
            command_value("setpoint_max", state.setpoint_max if state else None),
        )
        low_number, high_number = number(low), number(high)
        if (
            low_number is not None
            and high_number is not None
            and low_number > high_number
        ):
            raise ValueError("Minimum setpoint exceeds maximum")


def validate_basic_command(
    family: ModelFamily,
    caps: Capabilities | None,
    state: DeviceState | None,
    expected: dict[str, Any],
    command_value: Callable[[str, Any], Any],
) -> None:
    """Validate controls for experimental cloud-only devices.

    Kept as a focused public helper for callers that need only the strict
    new-model command allowlist.
    """
    _validate_basic_command(family, caps, state, expected, command_value)


def _validate_target_bounds(
    state: DeviceState | None, expected: Mapping[str, Any]
) -> None:
    if state is None:
        return
    fields = (
        (
            "heat_setpoint",
            state.setpoint_min,
            state.setpoint_max,
            "setpoint_min",
            "setpoint_max",
        ),
        ("cool_setpoint", state.cool_min, state.cool_max, "cool_min", "cool_max"),
    )
    for name, low, high, low_field, high_field in fields:
        if name not in expected:
            continue
        target = number(expected[name])
        low_observation = state.reported_observations.get(low_field)
        high_observation = state.reported_observations.get(high_field)
        minima = [
            value
            for value in (
                low,
                low_observation.value if low_observation else None,
            )
            if value is not None
        ]
        maxima = [
            value
            for value in (
                high,
                high_observation.value if high_observation else None,
            )
            if value is not None
        ]
        if (
            target is None
            or (minima and target < max(minima))
            or (maxima and target > min(maxima))
        ):
            raise ValueError("Target exceeds known device limits")


def _validate_basic_command(
    family: ModelFamily,
    caps: Capabilities | None,
    state: DeviceState | None,
    expected: Mapping[str, Any],
    command_value: Callable[[str, Any], Any],
) -> None:
    """Require complete capabilities and validate experimental commands."""
    if caps is None:
        raise ValueError("Device capabilities are unavailable; retry after a refresh")
    if family == ModelFamily.CLIMATE_SENSOR:
        raise ValueError("Climate Sensor supports readings only")
    supported = caps.basic_modes(family)
    if state and state.auto_enabled == 0:
        supported = [mode for mode in supported if mode != Mode.AUTO]
    mode = expected.get("mode", command_value("mode", state.mode if state else None))
    if "mode" in expected and mode not in supported:
        raise ValueError("Operating mode is not supported by this device")

    ranges = {
        "heat_setpoint": (caps.heat_setpoint_range, Mode.HEAT),
        "cool_setpoint": (caps.cool_setpoint_range, Mode.COOL),
        "auto_heat_setpoint": (caps.auto_heat_range, Mode.AUTO),
        "auto_cool_setpoint": (caps.auto_cool_range, Mode.AUTO),
    }
    for name, (limits, required_mode) in ranges.items():
        if name not in expected:
            continue
        value = number(expected[name])
        if (
            required_mode not in supported
            or limits is None
            or value is None
            or not limits[0] <= value <= limits[1]
        ):
            raise ValueError(f"Unsupported or out-of-range target: {name}")
        if required_mode == Mode.AUTO and mode != Mode.AUTO:
            raise ValueError("Dual setpoints require heat/cool mode")
        if required_mode != Mode.AUTO and mode not in (required_mode, Mode.OFF):
            raise ValueError("Setpoint does not match the selected mode")
        if state:
            low = (
                state.cool_min
                if required_mode == Mode.COOL or name == "auto_cool_setpoint"
                else state.setpoint_min
            )
            high = (
                state.cool_max
                if required_mode == Mode.COOL or name == "auto_cool_setpoint"
                else state.setpoint_max
            )
            if (low is not None and value < low) or (high is not None and value > high):
                raise ValueError("Target exceeds known device limits")

    if "auto_heat_setpoint" in expected or "auto_cool_setpoint" in expected:
        low = number(expected.get("auto_heat_setpoint"))
        high = number(expected.get("auto_cool_setpoint"))
        delta = max(
            caps.min_setpoint_delta or 0,
            state.auto_deadband or 0 if state else 0,
        )
        if low is None or high is None or high <= low or high - low < delta - 1e-6:
            raise ValueError(
                "Both auto setpoints must be supplied in order, with the required minimum gap"
            )
    if "fan_mode" in expected and (
        family != ModelFamily.CENTRAL_HVAC
        or expected["fan_mode"] not in caps.hvac_fan_speeds(mode)
    ):
        raise ValueError("Fan speed is not supported in this mode")
    if any(
        name in expected for name in ("fan_always_on", "fan_runtime", "fan_period")
    ) and (family != ModelFamily.CENTRAL_HVAC or not caps.hvac_fan_control):
        raise ValueError("Fan operation is not supported by this device")
    allowed = {
        *ranges,
        "mode",
        "fan_mode",
        "fan_always_on",
        "fan_runtime",
        "fan_period",
        "display_unit",
        "button_lock",
        "active_brightness",
        "idle_brightness",
    }
    if expected.keys() - allowed:
        raise ValueError("This setting is not implemented for this model")
