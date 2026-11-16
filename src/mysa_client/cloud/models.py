"""Typed views of Mysa cloud responses.

Every model keeps the raw JSON in `raw`, so new fields can be read without a
library change. Parsing is lenient: a missing or oddly typed field becomes None.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
import itertools
import math
from typing import Any

from .const import (
    AC_FAN_KEYS,
    AC_FAN_TO_STATE,
    AC_MODE_KEYS,
    AC_SWING_KEYS,
    MODE_BY_NAME,
    SETPOINT_SECTION_BY_MODE,
    STATE_TO_AC_FAN,
    AcFan,
    ChangeSource,
    Mode,
    ModelFamily,
    Swing,
    model_family,
)
from .observations import (
    STATE_FIELDS,
    TELEMETRY_FIELDS,
    Observation,
    number,
    observe,
    timestamp,
    valid_value,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float(value: Any) -> float | None:
    return number(value)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _temperature(value: Any) -> float | None:
    """A JSON number, or a numeric string as some remote records hold."""
    if isinstance(value, str):
        try:
            return number(float(value))
        except ValueError:
            return None
    return _float(value)


def _int(value: Any) -> int | None:
    numeric = number(value)
    return int(numeric) if numeric is not None and numeric.is_integer() else None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _ints(value: Any) -> list[int]:
    """Whole numbers from a JSON list (numeric strings count too)."""
    if not isinstance(value, list):
        return []
    numbers = []
    for item in value:
        if isinstance(item, str) and item.strip().isdigit():
            numbers.append(int(item))
        elif (number := _int(item)) is not None:
            numbers.append(number)
    return numbers


def _time(value: Any) -> datetime | None:
    return timestamp(value)


def _section(data: Mapping[str, Any], name: str) -> dict[str, Any]:
    """Return a state section's confirmed (reported) values.

    A value only in `desired` was asked for but never confirmed: a field the
    model doesn't support, or a thermostat that has been offline since. It
    isn't the thermostat's state.
    """
    section = _dict(data.get(name))
    if "desired" in section or "reported" in section:
        return dict(_dict(section.get("reported")))
    # Sections such as drState aren't split into desired/reported.
    return dict(section)


def value_range(values: Iterable[Any]) -> tuple[float, float, float] | None:
    """Turn a capability validValues list into (min, max, step)."""
    numbers = sorted({v for v in (_float(x) for x in values) if v is not None})
    if not numbers:
        return None
    steps = [b - a for a, b in itertools.pairwise(numbers) if b > a]
    step = round(min(steps), 4) if steps else 1.0
    return numbers[0], numbers[-1], step


def _health_fields(
    data: Mapping[str, Any], reading: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    """Parse DeviceState's health and electrical fields without converting them.

    A section whose own clock is malformed or in the future yields None for
    its values, as the telemetry fields do.
    """

    def usable(clock: Any) -> bool:
        return observe(None, "S", clock, now).usable(now)

    def celsius(value: Any) -> float | None:
        result = _float(value)
        return result if result is not None and -100 <= result <= 100 else None

    def non_negative(value: Any) -> float | None:
        result = _float(value)
        return result if result is not None and result >= 0 else None

    def heap(value: Any) -> int | None:
        result = _int(value)
        return result if result is not None and result >= 0 else None

    power = _section(data, "power")
    diagnostics = _section(data, "diagnostics")
    reading = reading if usable(reading.get("timestamp")) else {}
    power_ok = usable(power.get("timestamp"))
    if not power_ok:
        power = {}
    if not usable(diagnostics.get("timestamp")):
        diagnostics = {}
    percent = _float(power.get("dutyCycle"))
    free_heap = heap(reading.get("freeHeap"))
    voltage = non_negative(power.get("voltage"))
    return {
        "free_heap": free_heap
        if free_heap is not None
        else heap(diagnostics.get("freeHeap")),
        "raw_temperature": celsius(reading.get("rawTemperature")),
        "core_temperature": celsius(reading.get("coreTemperature")),
        "probe_temperature": celsius(reading.get("probeTemperature")),
        "voltage": voltage
        if voltage is not None
        else non_negative(reading.get("voltage")),
        "power_wattage": non_negative(power.get("wattage")),
        "power_current": non_negative(power.get("current")),
        "power_duty_cycle": percent
        if percent is not None and 0 <= percent <= 100
        else None,
        "power_time": _time(power.get("timestamp")) if power_ok else None,
        "max_current": _float(reading.get("maxCurrent")),
        "power_consumed": _float(reading.get("powerConsumed")),
        "instant_load": _float(reading.get("instantLoad")),
        "on_time": _float(reading.get("onTime")),
        "effective_setpoint": celsius(reading.get("effectiveSetpoint")),
    }


@dataclass(frozen=True, slots=True)
class DeviceState:
    """One thermostat's state from /state/batch or /state/{dev}."""

    device_id: str
    raw: dict[str, Any] = field(repr=False)
    connected: bool | None
    last_connected: datetime | None
    reading_time: datetime | None
    room_temperature: float | None
    humidity: float | None
    rssi: int | None
    duty_cycle: float | None
    mode: Mode | None
    # modes.fan_mode as stored; a Mini-Split AC numbers it as in AC_FAN_TO_STATE.
    fan_mode: int | None
    vertical_swing: int | None
    horizontal_swing: int | None
    heat_setpoint: float | None
    cool_setpoint: float | None
    setpoint_min: float | None
    setpoint_max: float | None
    button_lock: int | None
    active_brightness: int | None
    idle_brightness: int | None
    display_unit: str | None
    temperature_offset: float | None
    control_type: int | None
    model: str | None
    firmware: str | None
    in_demand_response: bool | None
    observations: dict[str, Observation] = field(default_factory=dict)
    reported_observations: dict[str, Observation] = field(default_factory=dict)

    auto_heat_setpoint: float | None = None
    auto_cool_setpoint: float | None = None
    # targetAuto.setpoint: a Mini-Split AC's single target in auto mode.
    auto_setpoint: float | None = None
    auto_enabled: bool | int | None = None
    auto_deadband: float | None = None
    cool_min: float | None = None
    cool_max: float | None = None
    hvac_state: int | None = None
    hvac_stages: dict[str, Any] | None = None
    fan_always_on: int | None = None
    fan_runtime: float | None = None
    fan_period: float | None = None

    # Health and electrical fields (0.4.0). They come only from structured
    # state, keep None when absent or malformed, and are not converted. Values
    # from latestTelemetry.reading share `reading_time`; values from
    # power.reported share `power_time`. See "Device health and electrical
    # fields" in docs/reference/python/cloud.md for the evidence behind each.
    #: reading.freeHeap, else diagnostics.reported.freeHeap: free firmware
    #: memory in bytes. Confirmed on Baseboard Lite firmware 3.17.5.13.
    free_heap: int | None = None
    #: reading.rawTemperature in °C, the uncorrected sensor reading. Confirmed
    #: on Baseboard Lite firmware 3.17.5.13 (it sits above room temperature).
    raw_temperature: float | None = None
    #: reading.coreTemperature in °C, the thermostat's internal temperature.
    #: Confirmed on Baseboard Lite firmware 3.17.5.13.
    core_temperature: float | None = None
    #: reading.probeTemperature in °C (probe and In-Floor models). Derived from
    #: Mysa's app 4.21.1; not verified on a device.
    probe_temperature: float | None = None
    #: power.reported.voltage, else reading.voltage, in volts. Confirmed on
    #: Baseboard Lite firmware 3.17.5.13 (240). The app reads 0 on an In-Floor
    #: as a ground fault; see `ground_fault`.
    voltage: float | None = None
    #: power.reported.wattage in watts. Mysa's app 4.21.1 multiplies it by the
    #: duty cycle, so it behaves as the heater's rated load, not a measurement.
    power_wattage: float | None = None
    #: power.reported.current in milliamps. Not observed non-zero here; a
    #: third-party Baseboard V3 capture (axelrtgs/mysa) shows 240 V, 12458 and
    #: 2989 W together, and Mysa's app 4.21.1 names it `currentMa`. On that
    #: Baseboard V3 the whole power section stayed unchanged a day later with
    #: the heater off, so check `power_time` before treating it as live.
    power_current: float | None = None
    #: power.reported.dutyCycle as a percentage (0-100), unlike `duty_cycle`,
    #: which is a 0-1 fraction. Derived from Mysa's app 4.21.1; only 0 observed.
    power_duty_cycle: float | None = None
    #: The power.reported timestamp.
    power_time: datetime | None = None
    #: drState.state as sent, next to `in_demand_response`: "none" on a device
    #: outside an event (confirmed on Baseboard Lite firmware 3.17.5.13). The
    #: app's other values are "event", "preheat" and "precool".
    demand_response_state: str | None = None
    #: reading.maxCurrent in amps: the highest load current the thermostat has
    #: seen, not the present draw (a Baseboard V1 reported about 8 A while
    #: idle). From third-party captures (axelrtgs/mysa, kgelinas/Mysa_HA issue
    #: 32, dlenski/mysotherm); only 0 observed on the Lite, and the app never
    #: reads it.
    max_current: float | None = None
    #: reading.powerConsumed. Unit unverified (only 0 observed; the app never
    #: reads it). The name suggests energy used in the reading interval.
    power_consumed: float | None = None
    #: reading.instantLoad. Unit unverified (only 0 observed; the app never
    #: reads it). The name suggests the present load.
    instant_load: float | None = None
    #: reading.onTime. Unit unverified (only 0 observed here; the app never
    #: reads it). Two Mini-Split ACs in a third-party capture reported 30 while
    #: heating, which fits seconds on per 30-second reading interval.
    on_time: float | None = None
    #: reading.effectiveSetpoint in °C as reported. Meaning unconfirmed: a Lite
    #: reported 23 while its setpoint was 10.
    effective_setpoint: float | None = None

    @classmethod
    def from_api(cls, device_id: str, data: Mapping[str, Any]) -> DeviceState:
        """Parse the `data` object of a state response."""
        telemetry = _dict(data.get("latestTelemetry"))
        reading = _dict(telemetry.get("reading"))
        heat = _section(data, "targetHeat")
        cool = _section(data, "targetCool")
        modes = _section(data, "modes")
        ui = _section(data, "physicalInterface")
        identity = _section(data, "identity")
        dr_state = _section(data, "drState")
        raw_mode = _int(modes.get("mode"))
        try:
            mode = Mode(raw_mode) if raw_mode is not None else None
        except ValueError:
            mode = None
        connected = telemetry.get("isConnected")
        in_event = dr_state.get("inEvent")
        dr_text = dr_state.get("state")
        result = cls(
            device_id=device_id,
            auto_heat_setpoint=_float(_section(data, "targetAuto").get("heatSetpoint")),
            auto_cool_setpoint=_float(_section(data, "targetAuto").get("coolSetpoint")),
            auto_setpoint=_float(_section(data, "targetAuto").get("setpoint")),
            auto_enabled=_section(data, "targetAuto").get("enabled"),
            auto_deadband=_float(_section(data, "targetAuto").get("deadband")),
            cool_min=_float(cool.get("lockoutMin")),
            cool_max=_float(cool.get("lockoutMax")),
            hvac_state=_int(reading.get("hvacState")),
            hvac_stages=_dict(modes.get("hvacStates")) or None,
            fan_always_on=_int(_section(data, "hvacConfig").get("advFanAlwaysOn")),
            fan_runtime=_float(
                _section(data, "hvacConfig").get("advFanRuntimeSeconds")
            ),
            fan_period=_float(
                _section(data, "hvacConfig").get("advFanRuntimePeriodSeconds")
            ),
            raw=dict(data),
            connected=connected if isinstance(connected, bool) else None,
            last_connected=_time(telemetry.get("lastConnected")),
            reading_time=_time(reading.get("timestamp")),
            room_temperature=_float(reading.get("roomTemperature")),
            humidity=_float(reading.get("humidity")),
            rssi=_int(reading.get("rssi")),
            duty_cycle=_float(reading.get("dutyCycle")),
            mode=mode,
            fan_mode=_int(modes.get("fan_mode")),
            vertical_swing=_int(modes.get("verticalSwingState")),
            horizontal_swing=_int(modes.get("horizontalSwingState")),
            heat_setpoint=_float(heat.get("setpoint")),
            cool_setpoint=_float(cool.get("setpoint")),
            setpoint_min=_float(heat.get("lockoutMin")),
            setpoint_max=_float(heat.get("lockoutMax")),
            button_lock=_int(ui.get("lockout")),
            active_brightness=_int(ui.get("activeIntensity")),
            idle_brightness=_int(ui.get("idleIntensity")),
            display_unit=_str(ui.get("format")),
            temperature_offset=_float(_section(data, "tracking").get("ambientOffset")),
            control_type=_int(_section(data, "bbConfig").get("controlType")),
            model=_str(identity.get("model")),
            firmware=_str(identity.get("fw")),
            in_demand_response=in_event if isinstance(in_event, bool) else None,
            demand_response_state=dr_text if isinstance(dr_text, str) else None,
        )

        now = datetime.now(UTC)
        observations = {}
        for name in TELEMETRY_FIELDS:
            if valid_value(name, value := getattr(result, name)):
                observations[name] = observe(value, "S", reading.get("timestamp"), now)
        for name, (section, _) in STATE_FIELDS.items():
            if valid_value(name, value := getattr(result, name)):
                observations[name] = observe(
                    value, "S", _section(data, section).get("timestamp"), now
                )
        if result.connected is not None:
            observations["connected"] = observe(
                result.connected, "S", telemetry.get("lastConnected"), now
            )
        # Invalid fields do not poison the rest of the device.
        fields: dict[str, Any] = {
            name: obs.value if obs.usable(now) else None
            for name, obs in observations.items()
        }
        for name in (*TELEMETRY_FIELDS, *STATE_FIELDS):
            fields.setdefault(name, None)
        fields.update(_health_fields(data, reading, now))
        return replace(
            result,
            observations=observations,
            reported_observations=observations,
            **fields,
        )

    @classmethod
    def from_legacy(
        cls,
        device_id: str,
        data: Mapping[str, Any],
        *,
        source: str = "R",
        model: str | None = None,
    ) -> DeviceState:
        """Decode legacy per-field clocks and enums separately from structured state."""
        result = cls.from_api(device_id, {})
        now = datetime.now(UTC)
        observations: dict[str, Observation] = {}
        mapping = {
            "CorrectedTemp": "room_temperature",
            "Humidity": "humidity",
            "SetPoint": "heat_setpoint",
            "Duty": "duty_cycle",
            "Rssi": "rssi",
            "Connected": "connected",
            "Lock": "button_lock",
            "TstatMode": "mode",
        }
        for wire, name in mapping.items():
            item = _dict(data.get(wire))
            value = item.get("v")
            numeric = _int(value)
            if name == "mode":
                # TstatMode uses the legacy control enum; Mode is a distinct
                # operating-state enum and cannot be substituted here.
                modes = {1: Mode.OFF, 3: Mode.HEAT}
                family = model_family(model) if model else None
                if family == ModelFamily.AC:
                    modes.update(
                        {2: Mode.AUTO, 4: Mode.COOL, 5: Mode.FAN_ONLY, 6: Mode.DRY}
                    )
                elif family == ModelFamily.INFLOOR:
                    modes[5] = Mode.HEAT
                value = modes.get(numeric if numeric is not None else -1)
            elif name == "button_lock":
                value = {0: 0, 1: 3}.get(numeric if numeric is not None else -1)
            elif name == "connected":
                value = value if isinstance(value, bool) else None
            else:
                value = _int(value) if name == "rssi" else _float(value)
            if valid_value(name, value):
                observations[name] = observe(value, source, item.get("t"), now)
        values = {
            name: obs.value for name, obs in observations.items() if obs.usable(now)
        }
        reading = observations.get("room_temperature")
        connected = observations.get("connected")
        return replace(
            result,
            raw=dict(data),
            observations=observations,
            reported_observations=observations,
            reading_time=reading.observed_at
            if reading and reading.usable(now)
            else None,
            last_connected=connected.observed_at if connected else None,
            **values,
        )

    @property
    def heating(self) -> bool | None:
        """Whether the last telemetry interval recorded heating/cooling activity."""
        if self.duty_cycle is None:
            return None
        return self.duty_cycle > 0

    def setpoint_for(self, mode: Mode | None) -> float | None:
        """The single setpoint that applies in a mode (heat setpoint by default).

        Cool reads ``targetCool``; heat, emergency heat, every other mode and
        none read ``targetHeat``. Heat/cool mode reads ``auto_setpoint``
        (``targetAuto.setpoint``), the single target only a Mini-Split AC has,
        where ``temperature_changes`` writes it; it is ``None`` for a Central
        HVAC, whose auto targets are the pair ``auto_heat_setpoint`` /
        ``auto_cool_setpoint``.
        """
        if mode == Mode.AUTO:
            return self.auto_setpoint
        if mode is not None and SETPOINT_SECTION_BY_MODE.get(mode) == "targetCool":
            return self.cool_setpoint
        return self.heat_setpoint

    @property
    def ac_fan(self) -> AcFan | None:
        """The AC fan speed in Mysa's app enum, if the state has one."""
        return STATE_TO_AC_FAN.get(self.fan_mode) if self.fan_mode is not None else None

    @property
    def power_load_watts(self) -> int | None:
        """The load Mysa's app 4.21.1 displays: round(duty % * rated watts / 100).

        None unless both ``power_duty_cycle`` and ``power_wattage`` are known.
        Like the app, this is an estimate from the rated load, not a
        measurement. It does not check ``connected``; the app shows nothing
        while the thermostat is offline. Derived from Mysa's app 4.21.1; only
        0 was observed (Baseboard Lite firmware 3.17.5.13, heater idle).
        """
        if self.power_duty_cycle is None or self.power_wattage is None:
            return None
        return math.floor(self.power_duty_cycle * self.power_wattage / 100 + 0.5)

    @property
    def ground_fault(self) -> bool | None:
        """Whether an In-Floor thermostat reports 0 V, which the app calls a ground fault.

        None for other models or without a voltage. Derived from Mysa's app
        4.21.1; not verified on a device.
        """
        if self.voltage is None or model_family(self.model) != ModelFamily.INFLOOR:
            return None
        return self.voltage == 0


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a model supports, from GET /capabilities/{dev}."""

    raw: dict[str, Any] = field(repr=False)
    modes: tuple[Mode, ...]
    heat_setpoint_range: tuple[float, float, float] | None
    cool_setpoint_range: tuple[float, float, float] | None
    control_types: tuple[int, ...]
    offset_range: tuple[float, float, float] | None
    lockout_values: tuple[int, ...]
    brightness: bool
    units: tuple[str, ...]
    humidity_sensor: bool
    homekit: bool
    matter: bool
    scheduling: bool
    model: str | None
    firmware: str | None

    auto_heat_range: tuple[float, float, float] | None = None
    auto_cool_range: tuple[float, float, float] | None = None
    min_setpoint_delta: float | None = None
    auto_enabled: bool | None = None

    def fan_values(self, mode: Mode | None, field: str) -> tuple[Any, ...]:
        """Mode-specific native HVAC fan capabilities (not AC remote enums)."""
        climate = _dict(_dict(self.raw.get("features")).get("climateControl"))
        name = next((k for k, v in MODE_BY_NAME.items() if v == mode), "")
        node = _dict(climate.get(name))
        if mode not in (Mode.OFF, Mode.FAN_ONLY):
            node = _dict(node.get("fan"))
        capability = _dict(node.get(field))
        values = capability.get("validValues")
        return (
            tuple(values)
            if isinstance(values, list) and capability.get("userControllable", True)
            else ()
        )

    def hvac_fan_speeds(self, mode: Mode | None) -> tuple[int, ...]:
        """Native speed enum; auto=9, unlike Mini-Split AC auto=0."""
        modes = self.fan_values(mode, "fanMode")
        values = _ints(list(self.fan_values(mode, "fanSpeed")))
        return tuple(
            v
            for v in values
            if (v == 9 and "auto" in modes) or (0 <= v <= 8 and "manual" in modes)
        )

    @property
    def hvac_fan_control(self) -> bool:
        """Runtime fan operation is advertised by HVAC's fan period capability."""
        climate = _dict(_dict(self.raw.get("features")).get("climateControl"))
        misc = _dict(_dict(climate.get("advancedConfig")).get("misc"))
        node = _dict(misc.get("fanRuntimePeriod"))
        return bool(node) and node.get("userControllable", True) is True

    @property
    def hvac_fan_operations(self) -> tuple[str, ...]:
        """Basic app fan operations whose periods fit the device capability."""
        if not self.hvac_fan_control:
            return ()
        climate = _dict(_dict(self.raw.get("features")).get("climateControl"))
        misc = _dict(_dict(climate.get("advancedConfig")).get("misc"))
        period = _dict(misc.get("fanRuntimePeriod"))
        low, high = _float(period.get("minValue")), _float(period.get("maxValue"))
        return tuple(
            name
            for name, seconds in (("auto", 1800), ("on", 900), ("off", 0))
            if (low is None or seconds >= low) and (high is None or seconds <= high)
        )

    def basic_modes(self, family: ModelFamily | None) -> list[Mode]:
        """Only expose implemented controls with usable capability limits."""
        if family == ModelFamily.CLIMATE_SENSOR:
            return []
        allowed = {Mode.OFF}
        if self.heat_setpoint_range:
            allowed.add(Mode.HEAT)
        if family == ModelFamily.CENTRAL_HVAC:
            if self.cool_setpoint_range:
                allowed.add(Mode.COOL)
            if (
                self.auto_heat_range
                and self.auto_cool_range
                and self.auto_enabled is not False
            ):
                allowed.add(Mode.AUTO)
            allowed.add(Mode.FAN_ONLY)
        return [mode for mode in self.modes if mode in allowed]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> Capabilities:
        """Parse a capability document."""
        features = _dict(data.get("features"))
        climate = _dict(features.get("climateControl"))
        sensing = _dict(features.get("sensing"))
        interface = _dict(features.get("interface"))
        connectivity = _dict(features.get("connectivity"))
        smart = _dict(features.get("smart"))
        sensors = _dict(_dict(data.get("hardware")).get("sensors"))
        system = _dict(data.get("system"))

        def controllable(node: Any) -> dict[str, Any]:
            node = _dict(node)
            return node if node.get("userControllable", True) else {}

        def valid(node: Any) -> list[Any]:
            values = controllable(node).get("validValues")
            return values if isinstance(values, list) else []

        modes = tuple(
            MODE_BY_NAME[name]
            for name in valid(climate.get("mode"))
            if isinstance(name, str) and name in MODE_BY_NAME
        )
        control_type = _dict(
            _dict(_dict(climate.get("advancedConfig")).get("baseboardHeating")).get(
                "controlType"
            )
        )
        offset = _dict(_dict(sensing.get("temperature")).get("temperatureOffset"))
        auto = _dict(climate.get("auto"))
        misc = _dict(_dict(climate.get("advancedConfig")).get("misc"))
        auto_enabled = _dict(misc.get("enableAutoMode")).get("value")
        return cls(
            raw=dict(data),
            auto_heat_range=value_range(valid(auto.get("heatSetpoint"))),
            auto_cool_range=value_range(valid(auto.get("coolSetpoint"))),
            min_setpoint_delta=_float(
                _dict(_dict(auto.get("constraints")).get("minSetpointDelta")).get(
                    "value"
                )
            ),
            auto_enabled=bool(auto_enabled)
            if isinstance(auto_enabled, bool)
            or (type(auto_enabled) is int and auto_enabled in (0, 1))
            else None,
            modes=modes,
            heat_setpoint_range=value_range(
                valid(_dict(climate.get("heat")).get("setpoint"))
            ),
            cool_setpoint_range=value_range(
                valid(_dict(climate.get("cool")).get("setpoint"))
            ),
            control_types=tuple(
                v for v in (_int(x) for x in valid(control_type)) if v is not None
            ),
            offset_range=value_range(valid(offset)),
            lockout_values=tuple(
                v
                for v in (_int(x) for x in valid(interface.get("lockout")))
                if v is not None
            ),
            brightness=bool(controllable(interface.get("brightness"))),
            units=tuple(v for v in valid(interface.get("unit")) if isinstance(v, str)),
            humidity_sensor=sensors.get("humiditySensor") is True,
            homekit=connectivity.get("homekit") is True,
            matter=connectivity.get("matter") is True,
            scheduling=smart.get("scheduling") is True,
            model=_str(system.get("model")),
            firmware=_str(system.get("firmwareVersion")),
        )


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    """A thermostat's settings record (GET /devices or bootstrap)."""

    device_id: str
    raw: dict[str, Any] = field(repr=False)
    name: str | None
    model: str | None
    setpoint_min: float | None
    setpoint_max: float | None
    home_id: str | None
    time_zone: str | None
    display_format: str | None
    duty_cycle_opt: int | None

    @classmethod
    def from_api(cls, device_id: str, data: Mapping[str, Any]) -> DeviceRecord:
        """Parse one entry of DevicesObj."""
        return cls(
            device_id=device_id,
            raw=dict(data),
            name=_str(data.get("Name")),
            model=_str(data.get("Model")),
            setpoint_min=_float(data.get("MinSetpoint")),
            setpoint_max=_float(data.get("MaxSetpoint")),
            home_id=_str(data.get("Home")),
            time_zone=_str(data.get("TimeZone")),
            display_format=_str(data.get("Format")),
            duty_cycle_opt=_int(data.get("DutyCycleOpt")),
        )

    @property
    def display_unit(self) -> str | None:
        """C or F, from the record's 'celsius'/'fahrenheit'."""
        if self.display_format is None:
            return None
        return "F" if self.display_format.lower().startswith("f") else "C"

    # A Mini-Split AC's record describes its remote: SupportedModes maps each
    # mode (AC_MODE_KEYS) to the fan speeds and swing settings it can send, and
    # modifiedKeys, when present, lists the IR keys the user kept after
    # customizing the remote in Mysa's app.

    @property
    def modified_keys(self) -> frozenset[int] | None:
        """The remote's customized key list, or None if it wasn't customized."""
        keys = _ints(self.raw.get("modifiedKeys"))
        return frozenset(keys) if keys else None

    def _ac_mode(self, mode: Mode) -> dict[str, Any]:
        key = AC_MODE_KEYS.get(mode)
        if key is None:
            return {}
        return _dict(_dict(self.raw.get("SupportedModes")).get(str(key)))

    def ac_fan_speeds(self, mode: Mode) -> tuple[AcFan, ...]:
        """Fan speeds the AC's remote offers in a mode, slowest first."""
        keys = self.modified_keys
        valid_speeds = frozenset(AcFan)
        speeds = {
            AcFan(v)
            for v in _ints(self._ac_mode(mode).get("fanSpeeds"))
            if v in valid_speeds
        }
        if keys is not None:
            speeds = {s for s in speeds if AC_FAN_KEYS[s] in keys}
        # AC_FAN_TO_STATE numbers them auto, low, medium, high, 1-7, sleep.
        return tuple(sorted(speeds, key=AC_FAN_TO_STATE.__getitem__))

    def ac_temperature_range(self, mode: Mode) -> tuple[float, float, float] | None:
        """The (min, max, step) in °C the AC's remote offers in a mode.

        Mysa's app 4.21.1 reads ``SupportedModes[<mode>].temperatures``, a list
        of the remote's whole-degree targets, and falls back to the record's
        ``TempRange`` ``[min, max]`` (``[16, 30]`` by default). The step is the
        smallest gap in that list, or 1 °C (the app's ``stepIncrement``) when
        the list has fewer than two values. Values outside 0-50 °C are ignored,
        so a list in another unit is never taken for °C. ``None`` when the
        record advertises neither.
        """
        values = sorted(
            {
                v
                for v in map(
                    _temperature, _list(self._ac_mode(mode).get("temperatures"))
                )
                if v is not None and 0 <= v <= 50
            }
        )
        if values:
            gaps = [round(b - a, 4) for a, b in itertools.pairwise(values)]
            return values[0], values[-1], min(gaps) if gaps else 1.0
        pair = [
            v
            for v in map(_temperature, _list(self.raw.get("TempRange")))
            if v is not None and 0 <= v <= 50
        ]
        if len(pair) == 2:
            return min(pair), max(pair), 1.0
        return None

    def ac_swing(self, mode: Mode, direction: str) -> tuple[Swing, ...]:
        """Swing settings the AC's remote offers in a mode (vertical or horizontal)."""
        keys = self.modified_keys
        if keys is not None and not keys & AC_SWING_KEYS[direction]:
            return ()
        values = _ints(self._ac_mode(mode).get(direction))
        valid_swings = frozenset(Swing)
        return tuple(sorted({Swing(v) for v in values if v in valid_swings}))


@dataclass(frozen=True, slots=True)
class FirmwareRecord:
    """One legacy firmware catalog entry; no update availability is implied."""

    device_id: str
    installed_version: str | None
    raw: dict[str, Any] = field(repr=False)

    @classmethod
    def from_api(cls, device_id: str, data: Mapping[str, Any]) -> FirmwareRecord:
        """Parse an entry of the Firmware mapping, preserving unknown fields."""
        return cls(
            device_id=device_id,
            installed_version=_str(data.get("InstalledVersion")),
            raw=dict(data),
        )


@dataclass(frozen=True, slots=True)
class FirmwareInfo:
    """GET /devices/update_available/{dev}."""

    update_available: bool
    installed_version: str | None
    allowed_version: str | None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> FirmwareInfo:
        """Parse the response. `update` may be false or a truthy value."""
        return cls(
            update_available=bool(data.get("update")),
            installed_version=_str(data.get("installedVersion")),
            allowed_version=_str(data.get("allowedVersion")),
        )


@dataclass(frozen=True, slots=True)
class UsageBucket:
    """30-minute usage bucket.

    ``temperature`` is Mysa's ``t`` for the bucket. On a Baseboard Lite it is
    not the mean room temperature of those 30 minutes: it moves by a few
    hundredths of a degree per bucket while the room swings by more, like a
    long trailing average. Don't use it as a per-bucket room reading.
    """

    start: datetime
    heat_seconds: float | None
    temperature: float | None


@dataclass(frozen=True, slots=True)
class UsageDay:
    """One local day of POST /telemetry/usage/{dev}.

    ``average_temperature`` is Mysa's summary ``t`` for the day. See
    :class:`UsageBucket` for how the per-bucket value behaves.
    """

    start: datetime
    heat_seconds: float | None
    average_temperature: float | None
    buckets: tuple[UsageBucket, ...]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> UsageDay | None:
        """Parse one day; None if it has no start time."""
        start = _time(data.get("T"))
        if start is None:
            return None
        summary = _dict(_dict(data.get("summary")).get("a"))
        buckets = []
        for raw_item in data.get("aggregatedReadings") or []:
            item = _dict(raw_item)
            bucket_start = _time(item.get("T"))
            if bucket_start is None:
                continue
            values = _dict(item.get("a"))
            buckets.append(
                UsageBucket(
                    start=bucket_start,
                    heat_seconds=_float(values.get("oH1")),
                    temperature=_float(values.get("t")),
                )
            )
        return cls(
            start=start,
            heat_seconds=_float(summary.get("oH1")),
            average_temperature=_float(summary.get("t")),
            buckets=tuple(buckets),
        )


@dataclass(frozen=True, slots=True)
class StateChange:
    """One entry of GET /state/history/{dev}."""

    field: str
    time: datetime
    value: Any
    source: str | None

    @property
    def from_device(self) -> bool:
        """True if someone pressed the thermostat's own buttons."""
        return self.source == ChangeSource.DEVICE


@dataclass(frozen=True, slots=True)
class Bootstrap:
    """GET /mobile/bootstrap: who the user is and what they can see."""

    user_id: str | None
    home_ids: tuple[str, ...]
    devices: dict[str, DeviceRecord]
    # Home ID -> name ("DefaultHome" unless the user renamed it).
    homes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> Bootstrap:
        """Parse the bootstrap response."""
        body = _dict(data.get("data"))
        user = _dict(body.get("user"))
        homes = user.get("AllowedHomes")
        devices = {
            dev_id: DeviceRecord.from_api(dev_id, _dict(record))
            for dev_id, record in _dict(body.get("devices")).items()
        }
        home_ids = (
            tuple(h for h in homes if isinstance(h, str))
            if isinstance(homes, list)
            else ()
        )
        names: dict[str, str] = {}
        raw_homes = body.get("homes")
        for home in raw_homes if isinstance(raw_homes, list) else []:
            home_id = _str(_dict(home).get("Id"))
            if home_id:
                names[home_id] = _str(_dict(home).get("Name")) or home_id
        for home_id in home_ids:
            names.setdefault(home_id, home_id)
        return cls(
            user_id=_str(user.get("Id")),
            home_ids=home_ids,
            devices=devices,
            homes=names,
        )


# ---- demand response, fleet firmware, sensor links (0.4.0) ----------------

#: DeviceDrState.NewState values Mysa's app 4.21.1 treats as an active event.
DR_EVENT_STATES = frozenset({"event", "preheat", "precool"})
#: The app ignores a DR state whose Timestamp is more than this many whole
#: hours old.
DR_STATE_MAX_HOURS = 8


@dataclass(frozen=True, slots=True)
class DemandResponseState:
    """GET /devices/{id}/drstate: the separate demand-response state record.

    Distinct from ``DeviceState.in_demand_response`` (structured
    ``drState.inEvent``). A thermostat that is not enrolled answers
    ``{"DeviceDrState": {}}``, parsed with ``has_data`` False; that is not the
    same as an explicit ``new_state == "none"``. Confirmed on Baseboard Lite
    firmware 3.17.5.13 (empty object only). The populated field names and the
    meaning of ``new_state`` are derived from Mysa's app 4.21.1; not verified
    on a device.

    ``device_time``, ``current_state``, ``current_setpoint`` and
    ``new_setpoint`` are passed through as sent: their types, and the unit of
    the setpoints, are not established.
    """

    raw: dict[str, Any] = field(repr=False)
    device_id: str | None
    #: "event", "none", "preheat", "precool", or an unknown string as sent.
    new_state: str | None
    #: Timestamp (unix seconds in the app).
    timestamp: datetime | None
    device_time: Any = None
    current_state: Any = None
    current_setpoint: Any = None
    new_setpoint: Any = None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> DemandResponseState:
        """Parse the ``DeviceDrState`` object (not the envelope)."""
        return cls(
            raw=dict(data),
            device_id=_str(data.get("Device")),
            new_state=_str(data.get("NewState")),
            timestamp=_time(data.get("Timestamp")),
            device_time=data.get("DeviceTime"),
            current_state=data.get("CurrentState"),
            current_setpoint=data.get("CurrentSetpoint"),
            new_setpoint=data.get("NewSetpoint"),
        )

    @property
    def has_data(self) -> bool:
        """False for the empty ``{}`` a non-enrolled thermostat returns."""
        return bool(self.raw)

    def in_event(self, now: datetime) -> bool:
        """Mysa's app 4.21.1 rule for "this thermostat is in a DR event".

        True when ``new_state`` is ``event``, ``preheat`` or ``precool`` and
        the timestamp is at most ``DR_STATE_MAX_HOURS`` whole hours before
        ``now`` (an aware datetime). The app compares whole hours, so a
        timestamp 8 h 59 min old still counts. Unlike the app, a record
        without a usable timestamp is never in an event. Derived from Mysa's
        app 4.21.1; not verified on a device.
        """
        if self.new_state not in DR_EVENT_STATES or self.timestamp is None:
            return False
        hours = math.trunc((now - self.timestamp).total_seconds() / 3600)
        return hours <= DR_STATE_MAX_HOURS


class FleetUpdateStatus(StrEnum):
    """Mysa's app 4.21.1 ``FirmwareUpdateStatus`` for fleet OTA requests."""

    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    REJECTED = "REJECTED"
    REMOVED = "REMOVED"
    CANCELED = "CANCELED"


#: Statuses after which a fleet request makes no further progress.
FLEET_FINAL_STATUSES = frozenset(
    {
        FleetUpdateStatus.SUCCEEDED,
        FleetUpdateStatus.FAILED,
        FleetUpdateStatus.TIMED_OUT,
        FleetUpdateStatus.REJECTED,
        FleetUpdateStatus.REMOVED,
        FleetUpdateStatus.CANCELED,
    }
)


@dataclass(frozen=True, slots=True)
class FleetFirmwareAvailability:
    """GET /ota/update/available/{id}: newer-model (fleet) firmware offers.

    Fleet models are V3 baseboards, Central HVAC, Climate Sensors and the
    water-heater controller. A legacy model is not in a fleet: Mysa answers
    404 ("No fleet found containing device"), which
    ``MysaClient.get_fleet_firmware_availability`` returns as ``in_fleet``
    False. That outcome is confirmed on Baseboard Lite firmware 3.17.5.13; the
    fields of a fleet answer are derived from Mysa's app 4.21.1; not verified
    on a device.
    """

    raw: dict[str, Any] = field(repr=False)
    in_fleet: bool
    update_available: bool
    message: str | None
    #: firmwareVersion.id, opaque (the app's placeholder is the number 0).
    version_id: str | int | None
    version: str | None
    description: str | None
    release_notes: str | None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> FleetFirmwareAvailability:
        """Parse a 200 answer; ``updateAvailable`` defaults to False as in the app."""
        version = _dict(data.get("firmwareVersion"))
        version_id = version.get("id")
        return cls(
            raw=dict(data),
            in_fleet=True,
            update_available=data.get("updateAvailable") is True,
            message=_str(data.get("message")),
            version_id=version_id
            if isinstance(version_id, str | int) and not isinstance(version_id, bool)
            else None,
            version=_str(version.get("version")),
            description=_str(version.get("description")),
            release_notes=_str(version.get("releaseNotes")),
        )

    @classmethod
    def not_in_fleet(cls) -> FleetFirmwareAvailability:
        """The outcome for a device Mysa has in no fleet (HTTP 404)."""
        return cls(
            raw={},
            in_fleet=False,
            update_available=False,
            message=None,
            version_id=None,
            version=None,
            description=None,
            release_notes=None,
        )


@dataclass(frozen=True, slots=True)
class FleetFirmwareStatus:
    """GET /ota/update/{requestId}: progress of one fleet firmware request.

    ``status`` keeps the string as sent, including values this library does
    not know; ``known_status`` maps it to ``FleetUpdateStatus``. A SUCCEEDED
    status alone does not prove the new firmware runs: Mysa's app also waits
    for the device's ``identity.reported.fw`` to match the target (see
    ``firmware_versions_match``). Derived from Mysa's app 4.21.1; not verified
    on a device.
    """

    raw: dict[str, Any] = field(repr=False)
    status: str | None
    #: percentComplete, 0-100; None when absent or outside that range.
    percent_complete: float | None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> FleetFirmwareStatus:
        """Parse a status answer leniently."""
        percent = _float(data.get("percentComplete"))
        return cls(
            raw=dict(data),
            status=_str(data.get("status")),
            percent_complete=percent
            if percent is not None and 0 <= percent <= 100
            else None,
        )

    @property
    def known_status(self) -> FleetUpdateStatus | None:
        """The status as a ``FleetUpdateStatus``, or None if unknown or absent."""
        try:
            return FleetUpdateStatus(self.status) if self.status else None
        except ValueError:
            return None

    @property
    def finished(self) -> bool:
        """Whether the request reached a final status (success or not)."""
        return self.known_status in FLEET_FINAL_STATUSES


def normalize_firmware_version(value: Any) -> str | None:
    """Normalize a firmware version as Mysa's app 4.21.1 does before comparing.

    Trim, drop one leading ``v`` or ``V``, keep only the text before the first
    ``-``, and trim again; an empty result (or a non-string) is None. For
    example ``" v3.17.5.13-beta "`` becomes ``"3.17.5.13"``.
    """
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    return text.split("-", 1)[0].strip() or None


def firmware_versions_match(reported: Any, target: Any) -> bool:
    """Whether two firmware versions are equal after ``normalize_firmware_version``.

    Mysa's app 4.21.1 judges a fleet update done when the device's
    ``identity.reported.fw`` matches the target version this way. Two
    unknown versions never match.
    """
    left = normalize_firmware_version(reported)
    return left is not None and left == normalize_firmware_version(target)


@dataclass(frozen=True, slots=True)
class SensorLink:
    """One entry of GET /linking?targetId={id}: a sensor feeding a thermostat.

    In Mysa's app 4.21.1 a Climate Sensor (source) feeds a Central HVAC
    thermostat (target); a link whose source is its own target is the
    thermostat's built-in sensor. The read is confirmed on Baseboard Lite
    firmware 3.17.5.13 (it answered an empty list); the entry fields are
    derived from Mysa's app 4.21.1; not verified on a device.
    """

    raw: dict[str, Any] = field(repr=False)
    link_id: str | None
    source_id: str | None
    target_id: str | None
    weight: float | None
    #: None when the entry has no boolean ``enabled``; the app treats that
    #: as enabled.
    enabled: bool | None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> SensorLink:
        """Parse one link entry leniently."""
        enabled = data.get("enabled")
        link_id = data.get("linkId")
        return cls(
            raw=dict(data),
            link_id=_str(link_id) or (str(link_id) if type(link_id) is int else None),
            source_id=_str(data.get("sourceId")),
            target_id=_str(data.get("targetId")),
            weight=_float(data.get("weight")),
            enabled=enabled if isinstance(enabled, bool) else None,
        )

    @property
    def participating(self) -> bool:
        """The app's rule: ``enabled`` is not False and ``weight`` > 0."""
        return self.enabled is not False and (self.weight or 0) > 0

    @property
    def own_sensor(self) -> bool:
        """Whether this is the target thermostat's own sensor."""
        return self.source_id is not None and self.source_id == self.target_id
