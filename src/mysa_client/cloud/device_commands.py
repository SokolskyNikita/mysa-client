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
from .setpoints import Setpoint, SetpointModeError


def climate_command(
    family: ModelFamily | None,
    selected_mode: Mode | None,
    *,
    mode: Mode | None = None,
    temperature: Setpoint | None = None,
    low: Setpoint | None = None,
    high: Setpoint | None = None,
) -> CloudCommand:
    """Build one mode/target operation, as Mysa's app 4.21.1 encodes it.

    The targets apply to ``mode`` when given (it is written in the same
    request), else to ``selected_mode``, the thermostat's current mode.

    ``temperature`` is a single target: ``targetHeat`` in heat, emergency heat,
    off or no mode, ``targetCool`` in cool. In heat/cool mode (``Mode.AUTO``)
    only a Mini-Split AC has one, written as ``targetAuto.setpoint``.

    ``low`` and ``high`` are a Central HVAC's heat/cool targets
    (``targetAuto.heatSetpoint`` / ``coolSetpoint``) and need heat/cool mode.
    Either may be given alone; the app writes only the side that changed. Check
    the gap to the other side first with ``SetpointRules.heat_cool_side`` (or
    ``heat_cool`` for both).

    Every refusal raises ``SetpointModeError``: a single ``temperature`` in
    heat/cool mode on anything but a Mini-Split AC (a Central HVAC takes
    ``low``, ``high`` or both), ``low``/``high`` outside heat/cool mode or on
    another family, ``temperature`` together with ``low``/``high``, and a
    temperature in fan-only, dry, sleep or pre-cool. Temperatures must come
    from ``SetpointRules``; a plain number raises ``TypeError``.

    The emergency-heat, AC auto and single-side encodings are derived from
    Mysa's app 4.21.1; not verified on a device.
    """
    selected = mode if mode is not None else selected_mode
    changes = mode_changes(mode) if mode is not None else {}
    if low is not None or high is not None:
        if temperature is not None:
            raise SetpointModeError(
                selected, "Pass either a temperature or low/high, not both"
            )
        if family != ModelFamily.CENTRAL_HVAC or selected != Mode.AUTO:
            raise SetpointModeError(
                selected,
                "Low and high setpoints need a Central HVAC in heat/cool mode",
            )
        changes.update(auto_temperature_changes(low, high))
    if temperature is not None:
        changes.update(temperature_changes(selected, temperature, family=family))
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
