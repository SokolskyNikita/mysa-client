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
    MAX_SCHEDULE_EVENTS,
    NO_SETPOINT_MODES,
    SETPOINT_SECTION_BY_MODE,
    SWING_HORIZONTAL,
    SWING_VERTICAL,
    AcFan,
    ButtonLock,
    HeaterType,
    Mode,
    ModelFamily,
    Swing,
)
from .modes import parse_mode
from .observations import STATE_FIELDS
from .setpoints import (
    Setpoint,
    SetpointKind,
    SetpointModeError,
    TemperatureOffset,
    _auto_mode_error,
    require_limit,
    require_offset,
    require_setpoint,
)

# Families that have no cooling: a cooling limit is refused for them.
_HEAT_ONLY_FAMILIES = frozenset(
    {
        ModelFamily.BASEBOARD_V1,
        ModelFamily.BASEBOARD_V2,
        ModelFamily.BASEBOARD_LITE,
        ModelFamily.BASEBOARD_V3,
        ModelFamily.INFLOOR,
        ModelFamily.CLIMATE_SENSOR,
    }
)

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
    """A schedule event before encoding it for Mysa's cloud API.

    ``mode`` may be given as any spelling :func:`~mysa_client.cloud.modes.parse_mode`
    accepts and is stored as a ``Mode``; anything else raises
    ``UnknownModeError``.
    """

    days: tuple[str, ...]
    at: time
    mode: Mode
    temperature: Setpoint | None
    client_key: str

    def __post_init__(self) -> None:
        """Normalise the mode; reject temperatures not from ``SetpointRules``."""
        object.__setattr__(self, "mode", parse_mode(self.mode))
        if self.temperature is not None:
            require_setpoint(self.temperature)


def _refuse_water_heater(family: ModelFamily | None, mode: Mode | None) -> None:
    """Refuse a setpoint or setpoint-limit write to a Water Heater Controller.

    Its ``targetHeat`` setpoint is the thermal safety limit, not a room
    target. Mysa's app 4.21.1 refuses the write for every mode.
    """
    if family == ModelFamily.WATER_HEATER:
        raise SetpointModeError(
            mode,
            "A Water Heater Controller takes no setpoint: its heat setpoint is "
            "the thermal safety limit",
        )


def temperature_changes(
    mode: Mode | None,
    temperature: Setpoint,
    *,
    family: ModelFamily | None = None,
) -> StateChanges:
    """Encode a setpoint change for the selected operating mode.

    Cool writes ``targetCool``; heat, emergency heat, off and no mode write
    ``targetHeat``. Heat/cool (``Mode.AUTO``) has a single target only on a
    Mini-Split AC (``family`` is ``ModelFamily.AC``), written as
    ``targetAuto.setpoint``; for any other family, or none, it raises
    ``SetpointModeError`` (a Central HVAC's auto targets are the pair, see
    :func:`auto_temperature_changes`). Fan-only, dry, sleep and pre-cool raise
    ``SetpointModeError``: this encoder writes no target in those modes.
    Mysa's app 4.21.1 differs in two of them: it writes ``targetCool`` in dry
    and both ``targetHeat`` and ``targetCool`` in fan-only.

    A Water Heater Controller (``family`` is ``ModelFamily.WATER_HEATER``)
    raises ``SetpointModeError`` in every mode: its ``targetHeat`` setpoint is
    the thermal safety limit, not a room target, and Mysa's app 4.21.1 refuses
    the same write. The check needs ``family``; without it the model is
    unknown and nothing is refused.

    The emergency-heat and AC auto encodings are derived from Mysa's app
    4.21.1; not verified on a device.
    """
    value = require_setpoint(temperature)
    _refuse_water_heater(family, mode)
    if mode in NO_SETPOINT_MODES:
        raise SetpointModeError(
            mode, f"Mode {mode.name.lower()} has no setpoint to write"
        )
    if mode == Mode.AUTO:
        if family != ModelFamily.AC:
            raise _auto_mode_error(family)
        return {"targetAuto": {"setpoint": value}}
    section = SETPOINT_SECTION_BY_MODE.get(mode or Mode.HEAT, "targetHeat")
    return {section: {"setpoint": value}}


def auto_temperature_changes(
    low: Setpoint | None, high: Setpoint | None
) -> StateChanges:
    """Encode a Central HVAC's heat/cool targets, one or both.

    Writes ``targetAuto.heatSetpoint`` for ``low`` and
    ``targetAuto.coolSetpoint`` for ``high``, never ``targetHeat`` or
    ``targetCool``. Mysa's app 4.21.1 writes only the side that changed; a
    single side is derived from it and not verified on a device. Check the gap
    to the other side first (:meth:`SetpointRules.heat_cool_side`). At least
    one side is required.
    """
    fields: dict[str, float] = {}
    if low is not None:
        fields["heatSetpoint"] = require_setpoint(low, "low")
    if high is not None:
        fields["coolSetpoint"] = require_setpoint(high, "high")
    if not fields:
        raise ValueError("Supply low, high or both")
    return {"targetAuto": fields}


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


def make_state(mode: Mode, temperature: Setpoint | None) -> StateChanges:
    """Build the state object used by a hold or schedule event.

    ``mode`` may be any spelling :func:`~mysa_client.cloud.modes.parse_mode`
    accepts.
    """
    value = None if temperature is None else require_setpoint(temperature)
    mode = parse_mode(mode)
    changes: StateChanges = mode_changes(mode)
    section = SETPOINT_SECTION_BY_MODE.get(mode)
    if section is not None and value is not None:
        changes[section] = {"setpoint": value}
    return changes


def encode_schedule_events(
    events: Iterable[ScheduleEventSpec],
    *,
    max_events: int = MAX_SCHEDULE_EVENTS,
) -> list[dict[str, Any]]:
    """Encode schedule events using Mysa's weekday and state representation.

    ``max_events`` is the most events allowed; pass
    ``schedule_event_limit(family)`` for the thermostat's model.
    """
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
    check_schedule_events(encoded, max_events=max_events)
    return encoded


def check_schedule_events(
    events: Iterable[Mapping[str, Any]], *, max_events: int = MAX_SCHEDULE_EVENTS
) -> None:
    """Refuse a weekly schedule that breaks the limits Mysa's app applies.

    A schedule has at most ``max_events`` events and at most one per day and
    time. The default is the most any model takes (70); pass
    ``schedule_event_limit(family)`` to apply the thermostat's own limit (56
    for most models). Mysa's cloud stores a schedule that breaks either rule
    without an error (seen on a Baseboard Lite), so check before sending.
    Raises ``ValueError``.
    """
    seen: set[tuple[str, str]] = set()
    count = 0
    for event in events:
        count += 1
        slot = (str(event.get("dayOfWeek")), str(event.get("executionTime")))
        if slot in seen:
            raise ValueError(
                "A schedule can have only one event per day and time "
                f"(day {slot[0]}, {slot[1]})"
            )
        seen.add(slot)
    if count > max_events:
        raise ValueError(
            f"A schedule can have at most {max_events} events, not {count}"
        )


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


def setpoint_limit_changes(
    value: Setpoint,
    *,
    minimum: bool,
    kind: SetpointKind = SetpointKind.HEAT,
    family: ModelFamily | None = None,
) -> StateChanges:
    """Encode the minimum or maximum allowed heating or cooling setpoint.

    ``kind`` is ``SetpointKind.HEAT`` (``targetHeat.lockoutMin`` /
    ``lockoutMax``, the default) or ``SetpointKind.COOL`` (``targetCool``);
    any other kind raises ``ValueError``, since Mysa's app has no limit for
    the auto section. A cooling limit on a heat-only ``family`` (baseboards,
    in-floor, the Climate Sensor) raises ``SetpointModeError``. On a Mini-Split
    AC (``family`` is ``ModelFamily.AC``) the value goes under both
    ``targetHeat`` and ``targetCool`` in one write, whichever ``kind``, as
    Mysa's app does. Pass a ``SetpointLimit`` from ``SetpointRules.limit``
    with the same ``kind``.

    A Water Heater Controller raises ``SetpointModeError``, as in
    ``temperature_changes``.

    The cooling and AC encodings are derived from Mysa's app 4.21.1; not
    verified on a device.
    """
    if kind not in (SetpointKind.HEAT, SetpointKind.COOL):
        raise ValueError(
            f"Setpoint limits are heating or cooling limits, not {kind.value}"
        )
    _refuse_water_heater(family, None)
    if kind == SetpointKind.COOL and family in _HEAT_ONLY_FAMILIES:
        raise SetpointModeError(
            Mode.COOL, f"A {family.value} thermostat has no cooling limits"
        )
    field = "lockoutMin" if minimum else "lockoutMax"
    limit = require_limit(value, "value")
    if family == ModelFamily.AC:
        return {"targetHeat": {field: limit}, "targetCool": {field: limit}}
    section = "targetCool" if kind == SetpointKind.COOL else "targetHeat"
    return {section: {field: limit}}


def temperature_offset_changes(value: TemperatureOffset) -> StateChanges:
    """Encode the ambient temperature offset."""
    return {"tracking": {"ambientOffset": require_offset(value, "value")}}


def heater_type_settings(heater_type: int | HeaterType) -> SettingsChanges:
    """Encode the heater profile in the device settings record."""
    return {"DutyCycleOpt": int(heater_type)}
