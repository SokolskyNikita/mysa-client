"""Semantic Mysa cloud commands and their wire representation.

Home Assistant code should ask for an operation such as setting a temperature.
Only this module translates that operation into Mysa's state/settings fields.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import time
from types import MappingProxyType
from typing import Any, Literal, TypedDict, cast

from .const import (
    AC_FAN_TO_STATE,
    SETPOINT_SECTION_BY_MODE,
    SWING_HORIZONTAL,
    SWING_VERTICAL,
    AcFan,
    ButtonLock,
    HeaterType,
    Mode,
    Swing,
)
from .observations import STATE_FIELDS

StateChanges = dict[str, dict[str, Any]]
SettingsChanges = dict[str, Any]


@dataclass(frozen=True, slots=True)
class _FrozenList:
    """Distinguish frozen JSON arrays from immutable tuples."""

    values: tuple[Any, ...]


def _freeze(value: Any) -> Any:
    """Take a recursively immutable snapshot of a JSON-like value."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(tuple(_freeze(item) for item in value))
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return deepcopy(value)


def _thaw(value: Any) -> Any:
    """Build a detached mutable payload from a command snapshot."""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, _FrozenList):
        return [_thaw(item) for item in value.values]
    if isinstance(value, tuple):
        return tuple(_thaw(item) for item in value)
    if isinstance(value, frozenset):
        return {_thaw(item) for item in value}
    return deepcopy(value)


@dataclass(frozen=True, slots=True, init=False)
class CloudCommand:
    """One encoded write and the semantic effects used for validation/readback.

    ``kind`` chooses the protocol operation: state commands are sent through
    ``MysaClient.update_state`` and settings commands through
    ``MysaClient.update_settings``. ``expected`` names normalized device
    fields; ``stored_fields`` names settings-record wire keys and only confirms
    that configuration was stored.
    """

    kind: Literal["state", "settings"]
    _payload: Any = field(repr=False)
    _expected: Any = field(repr=False)
    _stored_fields: Any = field(repr=False)

    def __init__(
        self,
        kind: Literal["state", "settings"],
        payload: Mapping[str, Any],
        expected: Mapping[str, Any],
        stored_fields: Mapping[str, Any],
    ) -> None:
        """Freeze payload and semantic effects into one command snapshot."""
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "_payload", _freeze(payload))
        object.__setattr__(self, "_expected", _freeze(expected))
        object.__setattr__(self, "_stored_fields", _freeze(stored_fields))

    @property
    def payload(self) -> StateChanges | SettingsChanges:
        """Return a detached payload, preserving the validated snapshot."""
        return cast(StateChanges | SettingsChanges, _thaw(self._payload))

    @property
    def expected(self) -> dict[str, Any]:
        """Return detached semantic effects derived when the command was built."""
        return cast(dict[str, Any], _thaw(self._expected))

    @property
    def stored_fields(self) -> dict[str, Any]:
        """Return detached settings fields used for storage readback."""
        return cast(dict[str, Any], _thaw(self._stored_fields))


_SETTINGS_TO_STATE = {
    "Format": "display_unit",
    "DutyCycleOpt": "control_type",
    "MinSetpoint": "setpoint_min",
    "MaxSetpoint": "setpoint_max",
    "MaxBrightness": "active_brightness",
    "MinBrightness": "idle_brightness",
    "ButtonState": "button_lock",
}


def state_command(changes: StateChanges) -> CloudCommand:
    """Describe a state-section write and derive its normalized expected values."""
    payload = {section: dict(fields) for section, fields in changes.items()}
    expected = {
        name: payload[section][wire]
        for name, (section, wire) in STATE_FIELDS.items()
        if section in payload and wire in payload[section]
    }
    return CloudCommand("state", payload, expected, {})


def settings_command(fields: SettingsChanges) -> CloudCommand:
    """Describe a settings-record write and derive stored and semantic values."""
    payload = dict(fields)
    expected = {
        _SETTINGS_TO_STATE[key]: value
        for key, value in payload.items()
        if key in _SETTINGS_TO_STATE
    }
    if "Format" in payload:
        expected["display_unit"] = {
            "celsius": "C",
            "fahrenheit": "F",
        }.get(payload["Format"])
    if "ButtonState" in payload:
        expected["button_lock"] = {"Locked": 3, "Unlocked": 0}.get(
            payload["ButtonState"]
        )
    return CloudCommand("settings", payload, expected, payload.copy())


class ScheduleWireEvent(TypedDict):
    """One schedule event in the cloud's API representation."""

    executionTime: str
    dayOfWeek: str
    clientKey: str
    state: StateChanges


_WEEKDAY_NUMBERS = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


@dataclass(frozen=True, slots=True)
class ScheduleEventSpec:
    """A schedule event before encoding it for Mysa's cloud API."""

    days: tuple[str, ...]
    at: time
    mode: Mode
    temperature: float | None
    client_key: str


def temperature_changes(mode: Mode | None, temperature: float) -> StateChanges:
    """Encode a setpoint change for the selected operating mode."""
    section = SETPOINT_SECTION_BY_MODE.get(mode or Mode.HEAT, "targetHeat")
    return {section: {"setpoint": temperature}}


def auto_temperature_changes(low: float, high: float) -> StateChanges:
    """APK 4.20.1 non-legacy AUTO uses a dedicated pair, never targetHeat/Cool."""
    return {"targetAuto": {"heatSetpoint": low, "coolSetpoint": high}}


def hvac_fan_changes(mode: str) -> StateChanges:
    """Basic fan operation, matching the app's always-on/runtime combination."""
    values = {"auto": (0, 0, 1800), "on": (1, 900, 900), "off": (0, 0, 0)}
    if mode not in values:
        raise ValueError(f"Unsupported HVAC fan operation: {mode}")
    always, runtime, period = values[mode]
    return {
        "hvacConfig": {
            "advFanAlwaysOn": always,
            "advFanRuntimeSeconds": runtime,
            "advFanRuntimePeriodSeconds": period,
        }
    }


def mode_changes(mode: Mode) -> StateChanges:
    """Encode an operating mode change."""
    return {"modes": {"mode": int(mode)}}


def make_state(mode: Mode, temperature: float | None) -> StateChanges:
    """Build the state object used by a hold or schedule event."""
    changes: StateChanges = mode_changes(mode)
    section = SETPOINT_SECTION_BY_MODE.get(mode)
    if section is not None and temperature is not None:
        changes[section] = {"setpoint": temperature}
    return changes


def encode_schedule_events(
    events: Iterable[ScheduleEventSpec],
) -> list[dict[str, Any]]:
    """Encode schedule events using Mysa's weekday and state representation."""
    encoded: list[dict[str, Any]] = []
    for event in events:
        for day in event.days:
            try:
                day_number = _WEEKDAY_NUMBERS[day.lower()]
            except KeyError as err:
                raise ValueError(f"Unsupported schedule weekday: {day!r}") from err
            wire_event: ScheduleWireEvent = {
                "executionTime": event.at.strftime("%H:%M:00"),
                "dayOfWeek": str(day_number),
                "clientKey": event.client_key,
                "state": make_state(event.mode, event.temperature),
            }
            encoded.append(dict(wire_event))
    encoded.sort(key=lambda event: (int(event["dayOfWeek"]), event["executionTime"]))
    return encoded


def fan_speed_changes(speed: AcFan, mode: Mode | None = None) -> StateChanges:
    """Encode an AC fan change, including the current mode when known."""
    fields: dict[str, int] = {"fan_mode": AC_FAN_TO_STATE[speed]}
    if mode is not None:
        fields["mode"] = int(mode)
    return {"modes": fields}


def swing_changes(direction: str, value: Swing) -> StateChanges:
    """Encode a vertical or horizontal AC swing change."""
    if direction not in (SWING_VERTICAL, SWING_HORIZONTAL):
        raise ValueError(f"Unsupported swing direction: {direction!r}")
    return {"modes": {f"{direction}State": int(value)}}


def display_unit_settings(unit: str) -> SettingsChanges:
    """Encode the thermostat's display unit setting."""
    normalized = unit.upper()
    if normalized not in ("C", "F"):
        raise ValueError(f"Unsupported display unit: {unit!r}")
    return {"Format": "fahrenheit" if normalized == "F" else "celsius"}


def display_brightness_changes(active: int, idle: int) -> StateChanges:
    """Encode both brightness values, as Mysa's app does in one request."""
    return {"physicalInterface": {"activeIntensity": active, "idleIntensity": idle}}


def button_lock_changes(value: ButtonLock) -> StateChanges:
    """Encode the physical button lock state."""
    return {"physicalInterface": {"lockout": int(value)}}


def setpoint_limit_changes(value: float, *, minimum: bool) -> StateChanges:
    """Encode the minimum or maximum allowed heating setpoint."""
    field = "lockoutMin" if minimum else "lockoutMax"
    return {"targetHeat": {field: value}}


def temperature_offset_changes(value: float) -> StateChanges:
    """Encode the ambient temperature offset."""
    return {"tracking": {"ambientOffset": value}}


def heater_type_settings(heater_type: int | HeaterType) -> SettingsChanges:
    """Encode the heater profile in the device settings record."""
    return {"DutyCycleOpt": int(heater_type)}
