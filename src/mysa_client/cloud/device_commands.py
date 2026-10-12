"""Semantic device operations built once for execution and confirmation."""

from __future__ import annotations

from .commands import (
    CloudCommand,
    auto_temperature_changes,
    display_brightness_changes,
    display_unit_settings,
    hvac_fan_changes,
    mode_changes,
    settings_command,
    state_command,
    temperature_changes,
)
from .const import EXPERIMENTAL_CLOUD_FAMILIES, Mode, ModelFamily
from .device import brightness_pair


def climate_command(
    family: ModelFamily | None,
    selected_mode: Mode | None,
    *,
    mode: Mode | None = None,
    temperature: float | None = None,
    low: float | None = None,
    high: float | None = None,
) -> CloudCommand:
    """Build one mode/target operation, preserving native HVAC auto targets."""
    selected = mode if mode is not None else selected_mode
    changes = mode_changes(mode) if mode is not None else {}
    if low is not None or high is not None:
        if (
            family != ModelFamily.CENTRAL_HVAC
            or temperature is not None
            or selected != Mode.AUTO
            or low is None
            or high is None
        ):
            raise ValueError("Supply both low and high setpoints in heat/cool mode")
        changes.update(auto_temperature_changes(low, high))
    if temperature is not None:
        if selected not in (Mode.HEAT, Mode.COOL, Mode.OFF):
            raise ValueError("A single setpoint requires heat or cool mode")
        changes.update(temperature_changes(selected, temperature))
    return state_command(changes)


def hvac_fan_speed_command(speed: int) -> CloudCommand:
    """Use the Central HVAC fan enumeration, independently of AC IR speeds."""
    return state_command({"modes": {"fan_mode": speed}})


def hvac_fan_command(mode: str) -> CloudCommand:
    """Build the native always-on/runtime/period combination."""
    return state_command(hvac_fan_changes(mode))


def brightness_command(
    value: float, *, active: bool, wake: float = 100, sleep: float = 50
) -> CloudCommand:
    """Adjust a brightness setting together with its dependent partner."""
    wake, sleep = brightness_pair(value, active=active, wake=wake, sleep=sleep)
    return state_command(display_brightness_changes(wake, sleep))


def display_unit_command(unit: str, family: ModelFamily | None) -> CloudCommand:
    """Use structured state for newer models and stored settings for legacy ones."""
    settings = display_unit_settings(unit)
    if family in EXPERIMENTAL_CLOUD_FAMILIES:
        return state_command({"physicalInterface": {"format": unit.upper()}})
    return settings_command(settings)
