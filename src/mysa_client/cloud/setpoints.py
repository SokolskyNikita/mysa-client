"""The single source of truth for Mysa setpoint rounding and validation.

Mysa thermostats advertise their setpoints on a step grid (usually 0.5 °C).
Callers that convert from Fahrenheit, such as Home Assistant, hand over values
like 22.78 °C that are off it. Mysa's cloud doesn't refuse such a value: on a
Baseboard Lite it answered 200 and stored it rounded to 0.1 °C, and it answered
200 to a value above the range and then dropped it. This package keeps every
write on the advertised grid and inside the range, so what is sent is what the
thermostat lists. ``SetpointRules`` rounds and
range-checks every temperature an application wants to write, and it is the
only code that can create :class:`Setpoint` and :class:`TemperatureOffset`
values. The command builders accept nothing else, so a type checker reports
any write path that skips the rules, and the builders raise ``TypeError`` at
run time for a plain number.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Final, Literal, TypeVar

# The value types live in a dependency-free module so the HomeKit and MQTT
# packages can check them without importing the cloud package; only these
# rules hold the construction token.
from mysa_client._temperature import (
    _TOKEN,
    Bound,
    Setpoint,
    SetpointClamp,
    SetpointLimit,
    TemperatureOffset,
    _RuledTemperature,
    require_limit,
    require_offset,
    require_setpoint,
)

from .const import SETPOINT_SECTION_BY_MODE, Mode, ModelFamily, model_family
from .device import heating_range
from .models import Capabilities, DeviceRecord

DEFAULT_STEP: Final = 0.5
"""The step assumed when neither HomeKit nor the cloud advertises one."""

DEFAULT_MINIMUM: Final = 5.0
"""The lowest target, in °C, an envelope assumes when no range advertises one.

Mysa's app uses 5-30 °C (``MIN_TEMP`` / ``MAX_TEMP``) for thermostats without
other limits, and the MQTT protocol enforces the same range.
"""

DEFAULT_MAXIMUM: Final = 30.0
"""The highest target, in °C, an envelope assumes when no range advertises one."""

SAFE_MINIMUM: Final = 5.0
"""The lowest temperature, in °C, a write may use when its minimum is unknown.

Validation and clamping use ``SAFE_MINIMUM``-``SAFE_MAXIMUM`` for any bound
neither HomeKit, the capabilities, a record nor the owner advertises, so rules
built before capabilities load never pass an absurd value to the cloud. No
Mysa range in this repository's evidence starts below 5 °C (the app's
``MIN_TEMP``).
"""

SAFE_MAXIMUM: Final = 35.0
"""The highest temperature, in °C, a write may use when its maximum is unknown.

35 °C is the highest bound any captured Mysa capability document advertises
(Central HVAC cool and heat/cool cool targets); a known, wider range still
wins.
"""

SAFE_OFFSET: Final = 5.0
"""The largest calibration delta, in °C either way, when the offset range is unknown.

The captured capability documents advertise -5 to 5 °C.
"""

MAX_MAGNITUDE: Final = 1000.0
"""Any temperature, bound or step whose size in °C exceeds this is refused.

Such values are not temperatures; refusing them early keeps the rounding
arithmetic finite.
"""

_MAX_STEP: Final = 10.0

# Mysa's app 4.21.1 shows a Mini-Split AC without a TempRange the range
# AC_MIN_TEMP-MAX_TEMP (16-30 °C) in whole degrees (getPickerPropsByDevice).
_AC_DEFAULT_RANGE: Final = (16.0, 30.0, 1.0)

Unit = Literal["C", "F"]
Side = Literal["heat", "cool"]
RangeTuple = tuple[float | None, float | None, float | None]

_T = TypeVar("_T", bound=_RuledTemperature)

__all__ = [
    "DEFAULT_MAXIMUM",
    "DEFAULT_MINIMUM",
    "DEFAULT_STEP",
    "MAX_MAGNITUDE",
    "SAFE_MAXIMUM",
    "SAFE_MINIMUM",
    "SAFE_OFFSET",
    "Bound",
    "RangeTuple",
    "Setpoint",
    "SetpointClamp",
    "SetpointConfigError",
    "SetpointError",
    "SetpointGapTooSmallError",
    "SetpointGapUnknownError",
    "SetpointKind",
    "SetpointLimit",
    "SetpointLimitOrderError",
    "SetpointModeError",
    "SetpointOutOfRangeError",
    "SetpointRange",
    "SetpointRangeEmptyError",
    "SetpointRules",
    "SetpointValueError",
    "Side",
    "TemperatureOffset",
    "Unit",
    "fahrenheit_bounds",
    "fahrenheit_label",
    "fahrenheit_offset",
    "kind_for_mode",
    "limits_ordered",
    "require_limit",
    "require_offset",
    "require_setpoint",
    "round_half_up",
    "round_to_step",
    "to_celsius",
]


class SetpointKind(StrEnum):
    """The setpoint ranges a thermostat can advertise, each with its own step.

    ``AUTO`` is a Mini-Split AC's single target in auto mode
    (``targetAuto.setpoint``); ``AUTO_HEAT`` and ``AUTO_COOL`` are a Central
    HVAC's heat/cool pair.
    """

    HEAT = "heat"
    COOL = "cool"
    AUTO_HEAT = "auto_heat"
    AUTO_COOL = "auto_cool"
    AUTO = "auto"


@dataclass(frozen=True, slots=True)
class SetpointRange:
    """One normalised range in °C; a ``None`` bound is unknown, not unlimited."""

    minimum: float | None
    maximum: float | None
    step: float


class SetpointError(ValueError):
    """Base class for values the setpoint rules refuse."""


class SetpointValueError(SetpointError):
    """A requested value is not a usable temperature.

    Raised for NaN, infinity and values beyond ``MAX_MAGNITUDE`` °C, in strict
    and non-strict calls alike. ``value`` is what the caller passed.
    """

    def __init__(self, value: float, message: str) -> None:
        """Store the refused value."""
        self.value = value
        super().__init__(message)


class SetpointConfigError(SetpointError):
    """``SetpointRules`` was given a range, limit or gap it cannot use.

    Raised when the rules are built, for a non-finite or absurd bound, a
    non-positive or absurd step, or a negative minimum gap. ``name`` says which
    input and ``value`` holds it.
    """

    def __init__(self, name: str, value: object, message: str) -> None:
        """Store the refused input."""
        self.name = name
        self.value = value
        super().__init__(message)


class SetpointRangeEmptyError(SetpointError):
    """No value on the step grid lies inside the applicable range.

    For example inverted owner limits (minimum 25, maximum 20 °C) or a range of
    20.2-20.4 °C with a 0.5 °C step. Raised in strict and non-strict calls, since
    no clamp could produce a valid value. ``limits`` is the range that applied
    (``SAFE_*`` standing in for unknown bounds), ``kind`` and ``side`` as in
    ``SetpointOutOfRangeError``.
    """

    def __init__(
        self,
        *,
        limits: SetpointRange,
        kind: SetpointKind | None = None,
        side: Side | None = None,
    ) -> None:
        """Store the empty range."""
        self.limits = limits
        self.kind = kind
        self.side: Side | None = side
        what = f"{side.capitalize()} setpoint range" if side else "Setpoint range"
        super().__init__(
            f"{what} {limits.minimum}-{limits.maximum} °C has no value on its "
            f"{limits.step} °C step"
        )


class SetpointOutOfRangeError(SetpointError):
    """A rounded value lies outside the applicable range.

    All numbers are in °C so callers can word the message in the user's unit.
    ``limits`` is the whole range that was violated, with ``SAFE_MINIMUM``,
    ``SAFE_MAXIMUM`` or ``SAFE_OFFSET`` standing in for a bound the rules do
    not know, and ``kind`` the setpoint
    kind it belongs to (``None`` for the hardware limit range and the offset
    range). ``side`` names the failed half of a heat/cool pair and is ``None``
    otherwise. ``delta`` is true for a calibration offset, whose numbers are
    temperature differences rather than temperatures.
    """

    def __init__(
        self,
        *,
        requested: float,
        rounded: float,
        limit: float,
        bound: Bound,
        limits: SetpointRange | None = None,
        kind: SetpointKind | None = None,
        side: Side | None = None,
        delta: bool = False,
    ) -> None:
        """Store the requested and rounded values and the violated range."""
        self.requested = requested
        self.rounded = rounded
        self.limit = limit
        self.bound: Bound = bound
        self.limits = limits
        self.kind = kind
        self.side: Side | None = side
        self.delta = delta
        relation = "below the minimum" if bound == "min" else "above the maximum"
        what = f"{side.capitalize()} setpoint " if side else ""
        super().__init__(f"{what}{rounded} °C is {relation} of {limit} °C")

    @property
    def fahrenheit_limit(self) -> int | None:
        """The closest whole °F on the allowed side of the violated bound.

        A whole °F the user can pick, rounded like every other written value;
        ``None`` when ``limits`` is unknown.
        """
        if self.limits is None:
            return None
        low, high = fahrenheit_bounds(self.limits, delta=self.delta)
        return low if self.bound == "min" else high


class SetpointLimitOrderError(SetpointError):
    """A new owner limit would not stay strictly inside the other limit.

    ``value`` is the rounded new limit and ``other`` the current opposite limit,
    both in °C. ``minimum`` says which limit was being set.
    """

    def __init__(self, *, value: float, other: float, minimum: bool) -> None:
        """Store the refused limit and the limit it collides with."""
        self.value = value
        self.other = other
        self.minimum = minimum
        if minimum:
            message = f"Minimum {value} °C must be below the maximum {other} °C"
        else:
            message = f"Maximum {value} °C must be above the minimum {other} °C"
        super().__init__(message)


class SetpointModeError(SetpointError):
    """A setpoint was given for a mode or model that cannot carry it.

    Raised for a single temperature in heat/cool mode on anything but a
    Mini-Split AC (a Central HVAC needs ``low`` and/or ``high``), for a heat/cool
    side outside heat/cool mode, for a mode with no setpoint section (fan-only,
    dry, sleep, pre-cool), for a hold or schedule event in heat/cool mode, and
    for a cooling limit on a model that doesn't advertise cooling. ``mode`` is
    the mode refused (``None`` when no mode was selected).
    """

    def __init__(self, mode: Mode | None, message: str) -> None:
        """Store the mode that was refused."""
        self.mode = mode
        super().__init__(message)


class SetpointGapUnknownError(SetpointError):
    """One heat/cool side was given, but the other side's value is unknown.

    The minimum gap can't be checked without it, so the side is refused rather
    than written unchecked. ``side`` is the side being set.
    """

    def __init__(self, side: Side) -> None:
        """Store the side that could not be checked."""
        self.side: Side = side
        other = "cool" if side == "heat" else "heat"
        super().__init__(
            f"The current {other} setpoint is unknown, so the {side} setpoint's "
            "gap can't be checked; pass both sides"
        )


class SetpointGapTooSmallError(SetpointError):
    """A heat/cool pair, after rounding, is closer than the device allows."""

    def __init__(self, *, low: float, high: float, required: float) -> None:
        """Store the rounded pair and the required minimum gap, in °C."""
        self.low = low
        self.high = high
        self.gap = round(high - low, 4)
        self.required = required
        super().__init__(
            f"Heat {low} °C and cool {high} °C must be at least {required} °C apart"
        )


def round_half_up(value: float) -> int:
    """Round halfway values up, like JavaScript's ``Math.round``."""
    return math.floor(round(value, 9) + 0.5)


def round_to_step(value: float, step: float) -> float:
    """Round to a multiple of ``step`` anchored at 0, halfway values up."""
    return round(round_half_up(value / step) * step, 4)


def to_celsius(value: float, unit: Unit) -> float:
    """Convert an absolute temperature to °C."""
    return (value - 32) / 1.8 if _unit(unit) == "F" else value


def fahrenheit_label(celsius: float | Setpoint) -> int:
    """Whole °F as the Mysa app shows it: ``Math.round(c * 1.8 + 32)``."""
    value = celsius.celsius if isinstance(celsius, Setpoint) else celsius
    return round_half_up(value * 1.8 + 32)


def fahrenheit_offset(delta_c: float | TemperatureOffset) -> int:
    """Whole °F for a temperature delta, halfway values rounded up."""
    value = delta_c.celsius if isinstance(delta_c, TemperatureOffset) else delta_c
    return round_half_up(value * 1.8)


def _unit(unit: str) -> Unit:
    if unit == "C":
        return "C"
    if unit == "F":
        return "F"
    raise ValueError(f"Unsupported temperature unit: {unit!r}")


def _finite(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"Temperature must be a number, not {type(value).__name__}")
    try:
        number = float(value)
    except OverflowError:
        number = math.inf
    if not math.isfinite(number):
        raise SetpointValueError(value, "Temperature must be finite")
    if abs(number) > MAX_MAGNITUDE * 2:  # Allows the same size in °F.
        raise SetpointValueError(value, f"{value} is not a plausible temperature")
    return number


def _config_number(name: str, value: object) -> float:
    """A finite, plausible number from the rules' inputs, or a typed error."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SetpointConfigError(
            name, value, f"{name} must be a number, not {type(value).__name__}"
        )
    try:
        number = float(value)
    except OverflowError:
        number = math.inf
    if not math.isfinite(number) or abs(number) > MAX_MAGNITUDE:
        raise SetpointConfigError(name, value, f"{name} {value!r} is not usable")
    return number


def fahrenheit_bounds(
    limits: SetpointRange, *, delta: bool = False
) -> tuple[int | None, int | None]:
    """Lowest and highest whole °F whose normalised value is in ``limits``.

    With ``delta`` the numbers are temperature differences (a calibration
    offset): a °F delta converts by ``/ 1.8`` with no 32 °F shift. A ``None``
    bound stays ``None``.
    """
    shift = 0 if delta else 32

    def inside(fahrenheit: int) -> bool:
        celsius = round_to_step((fahrenheit - shift) / 1.8, limits.step)
        return (limits.minimum is None or celsius >= limits.minimum) and (
            limits.maximum is None or celsius <= limits.maximum
        )

    low = high = None
    if limits.minimum is not None:
        start = math.floor(limits.minimum * 1.8 + shift) - 2
        low = next((f for f in range(start, start + 6) if inside(f)), None)
    if limits.maximum is not None:
        start = math.ceil(limits.maximum * 1.8 + shift) + 2
        high = next((f for f in range(start, start - 6, -1) if inside(f)), None)
    return low, high


def limits_ordered(minimum: float, maximum: float) -> bool:
    """Whether owner limits are valid together: the minimum strictly below.

    The one comparison for owner limits, shared by :meth:`SetpointRules.limit`
    and ``validate_cloud_command``.
    """
    return round(minimum, 4) < round(maximum, 4)


def _range(values: RangeTuple | None, name: str = "range") -> SetpointRange:
    low, high, step = values or (None, None, None)
    if step is not None and step != 0:
        step = _config_number(f"{name} step", step)
        if not 0 < step <= _MAX_STEP:
            raise SetpointConfigError(
                f"{name} step", step, f"{name} step {step} must be in (0, 10] °C"
            )
    return SetpointRange(
        minimum=None if low is None else round(_config_number(f"{name} min", low), 4),
        maximum=None if high is None else round(_config_number(f"{name} max", high), 4),
        step=float(step) if step else DEFAULT_STEP,
    )


def _with_fallback(limits: SetpointRange, low: float, high: float) -> SetpointRange:
    """``limits`` with ``low`` and ``high`` standing in for unknown bounds."""
    return SetpointRange(
        minimum=low if limits.minimum is None else limits.minimum,
        maximum=high if limits.maximum is None else limits.maximum,
        step=limits.step,
    )


def _clamp_on_grid(limit: float, step: float, bound: Bound) -> float:
    """The closest step-grid value at or inside ``limit``.

    A limit that is not itself on the grid would otherwise be written as-is
    and rejected by the device.
    """
    steps = limit / step
    whole = (
        math.ceil(round(steps, 9)) if bound == "min" else math.floor(round(steps, 9))
    )
    return round(whole * step, 4)


def _intersect(
    local: RangeTuple, cloud: RangeTuple | None
) -> tuple[float | None, float | None, float | None]:
    """Narrow a HomeKit range by a cloud range; the HomeKit step wins."""
    low, high, step = local
    if cloud is not None:
        c_low, c_high, c_step = cloud
        if c_low is not None:
            low = c_low if low is None else max(low, c_low)
        if c_high is not None:
            high = c_high if high is None else min(high, c_high)
        step = step or c_step
    return low, high, step


def _auto_mode_error(family: ModelFamily | None) -> SetpointModeError:
    """The refusal for a single temperature in heat/cool mode on ``family``."""
    if family == ModelFamily.CENTRAL_HVAC:
        message = (
            "A Central HVAC in heat/cool mode has a heat and a cool target; "
            "pass low, high or both instead of a single temperature"
        )
    else:
        message = (
            "Only a Mini-Split AC has a single temperature in heat/cool mode; "
            "a Central HVAC takes low, high or both"
        )
    return SetpointModeError(Mode.AUTO, message)


def kind_for_mode(mode: Mode | None, family: ModelFamily | None = None) -> SetpointKind:
    """The single-setpoint range a mode writes, matching the command encoder.

    Cool uses the cool range (``targetCool``). Heat/cool mode (``Mode.AUTO``)
    has a single target only on a Mini-Split AC (``family`` is
    ``ModelFamily.AC``), whose auto range is ``SetpointKind.AUTO``
    (``targetAuto.setpoint``, as Mysa's app 4.21.1 writes it); for any other
    family, or none, it raises ``SetpointModeError``: a Central HVAC's auto
    targets are the pair (:meth:`SetpointRules.heat_cool` and
    :meth:`SetpointRules.heat_cool_side`). Every other mode, including
    emergency heat and none, uses the heat range (``targetHeat``).
    ``temperature_changes`` and ``DeviceState.setpoint_for`` follow the same
    rule, except that ``temperature_changes`` refuses fan-only, dry, sleep and
    pre-cool, which have no setpoint section.
    """
    if mode == Mode.AUTO:
        if family == ModelFamily.AC:
            return SetpointKind.AUTO
        raise _auto_mode_error(family)
    section = SETPOINT_SECTION_BY_MODE.get(mode or Mode.HEAT, "targetHeat")
    return SetpointKind.COOL if section == "targetCool" else SetpointKind.HEAT


class SetpointRules:
    """Round and validate every temperature written to one thermostat.

    ``homekit_range`` is the HomeKit accessory's ``(min, max, step)``; any part
    may be ``None``. It applies to the heat range, where it is intersected with
    the cloud capability range exactly as :func:`heating_range` does, and its
    step wins. The cool range comes from ``capabilities``; when they have none
    it comes from a Mini-Split AC's settings ``record``
    (:meth:`DeviceRecord.ac_temperature_range`; other models' records are
    not read for ranges), and failing that from the
    HomeKit range. The record's heat-mode range likewise stands in for a
    missing capability heat range. Heat/cool pair ranges come from
    ``capabilities`` only. ``setpoint_min`` and ``setpoint_max`` are the
    owner's limits; they narrow the heat range but not the hardware range used
    to validate new limits, and :meth:`limit` keeps them strictly ordered.
    ``cool_min`` and ``cool_max`` are the owner's cooling limits
    (``DeviceState.cool_min`` / ``cool_max``) and do the same for the cool range
    and :attr:`hardware_cool_range`, the capability cool range or a Mini-Split
    AC record's cool-mode range (the HomeKit range doesn't count, so a model
    advertising neither has no cooling limits).
    ``family`` is the model family, read from ``record.model``, else
    ``capabilities.model``, when not given. It decides what a single target in
    heat/cool mode means (:func:`kind_for_mode`): on a Mini-Split AC its range
    (``SetpointKind.AUTO``) is the record's auto-mode range
    (:meth:`DeviceRecord.ac_temperature_range`), else the HomeKit range, else
    16-30 °C in whole degrees, the range Mysa's app shows an AC without one.
    Without capabilities or a record (a local-only thermostat) only the HomeKit
    range applies. A missing step is assumed to be 0.5 °C. A missing bound
    stays ``None`` in :meth:`range_for`, but every write is checked and
    clamped against ``SAFE_MINIMUM``-``SAFE_MAXIMUM`` (5-35 °C) in its place,
    or ``-SAFE_OFFSET``-``SAFE_OFFSET`` for an offset; an :meth:`envelope`
    (for display) assumes ``DEFAULT_MINIMUM``-``DEFAULT_MAXIMUM`` instead.
    A non-finite or absurd bound, step, owner limit or gap raises
    ``SetpointConfigError`` here.
    """

    def __init__(
        self,
        capabilities: Capabilities | None = None,
        homekit_range: RangeTuple | None = None,
        *,
        setpoint_min: float | None = None,
        setpoint_max: float | None = None,
        record: DeviceRecord | None = None,
        cool_min: float | None = None,
        cool_max: float | None = None,
        family: ModelFamily | None = None,
    ) -> None:
        """Derive every range once from the thermostat's advertised limits."""
        local = homekit_range or (None, None, None)
        _range(local, "homekit_range")
        for name, limit in (
            ("setpoint_min", setpoint_min),
            ("setpoint_max", setpoint_max),
            ("cool_min", cool_min),
            ("cool_max", cool_max),
        ):
            if limit is not None:
                _config_number(name, limit)
        caps = capabilities
        self.capabilities = caps
        self.record = record
        if family is None:
            family = model_family(record.model if record else None) or model_family(
                caps.model if caps else None
            )
        self.family: ModelFamily | None = family
        # Only a Mini-Split AC's record describes its remote's temperatures.
        ac = record if record and model_family(record.model) == ModelFamily.AC else None
        if ac is not None and (caps is None or caps.heat_setpoint_range is None):
            local = _intersect(local, ac.ac_temperature_range(Mode.HEAT))
        hardware_cool: RangeTuple | None = caps.cool_setpoint_range if caps else None
        if hardware_cool is None and ac is not None:
            hardware_cool = ac.ac_temperature_range(Mode.COOL)
        self._advertises_cooling = hardware_cool is not None
        cool = hardware_cool
        if cool is None and homekit_range is not None:
            cool = homekit_range
        auto: RangeTuple | None = None
        if family == ModelFamily.AC:
            auto = ac.ac_temperature_range(Mode.AUTO) if ac is not None else None
            if auto is None:
                auto = homekit_range or _AC_DEFAULT_RANGE
        self.setpoint_min = None if setpoint_min is None else round(setpoint_min, 4)
        self.setpoint_max = None if setpoint_max is None else round(setpoint_max, 4)
        self.cool_min = None if cool_min is None else round(cool_min, 4)
        self.cool_max = None if cool_max is None else round(cool_max, 4)
        self.hardware_heat_range = _range(heating_range(local, caps, None, None))
        self.hardware_cool_range = _range(hardware_cool)
        self._ranges = {
            SetpointKind.HEAT: _range(
                heating_range(local, caps, setpoint_min, setpoint_max)
            ),
            SetpointKind.COOL: _range(
                _intersect(cool or (None, None, None), (cool_min, cool_max, None))
            ),
            SetpointKind.AUTO_HEAT: _range(caps.auto_heat_range if caps else None),
            SetpointKind.AUTO_COOL: _range(caps.auto_cool_range if caps else None),
            SetpointKind.AUTO: _range(auto),
        }
        self.offset_range = _range(caps.offset_range if caps else None)
        delta = caps.min_setpoint_delta if caps else None
        if delta is not None and _config_number("min_setpoint_delta", delta) < 0:
            raise SetpointConfigError(
                "min_setpoint_delta", delta, "min_setpoint_delta must not be negative"
            )
        self.min_setpoint_delta = round(delta, 4) if delta is not None else 0.0

    def range_for(self, kind: SetpointKind | Mode | None) -> SetpointRange:
        """The normalised range for a setpoint kind or a single-setpoint mode."""
        if not isinstance(kind, SetpointKind):
            kind = kind_for_mode(kind, self.family)
        return self._ranges[kind]

    def setpoint(
        self,
        mode: Mode | None,
        value: float,
        *,
        unit: Unit = "C",
        strict: bool = True,
    ) -> Setpoint:
        """Normalise a single target for ``mode``.

        In heat/cool mode only a Mini-Split AC has a single target (its auto
        range); for any other family this raises ``SetpointModeError``, and a
        Central HVAC uses :meth:`heat_cool` or :meth:`heat_cool_side` (see
        :func:`kind_for_mode`).
        """
        kind = kind_for_mode(mode, self.family)
        return self._normalise(
            Setpoint,
            self._ranges[kind],
            _finite(value),
            unit,
            strict=strict,
            kind=kind,
        )

    def heat_cool(
        self,
        low: float,
        high: float,
        *,
        unit: Unit = "C",
        strict: bool = True,
    ) -> tuple[Setpoint, Setpoint]:
        """Normalise a heat/cool pair; the gap is checked, never adjusted.

        A ``SetpointOutOfRangeError`` names the side that failed (``side`` is
        ``"heat"`` or ``"cool"``) and its range, even when ``low`` equals
        ``high``; the heat side is checked first.
        """
        heat = self._normalise(
            Setpoint,
            self._ranges[SetpointKind.AUTO_HEAT],
            _finite(low),
            unit,
            strict=strict,
            kind=SetpointKind.AUTO_HEAT,
            side="heat",
        )
        cool = self._normalise(
            Setpoint,
            self._ranges[SetpointKind.AUTO_COOL],
            _finite(high),
            unit,
            strict=strict,
            kind=SetpointKind.AUTO_COOL,
            side="cool",
        )
        if round(cool.celsius - heat.celsius, 4) < self.min_setpoint_delta:
            raise SetpointGapTooSmallError(
                low=heat.celsius, high=cool.celsius, required=self.min_setpoint_delta
            )
        return heat, cool

    def heat_cool_side(
        self,
        side: Side,
        value: float,
        *,
        other: float | Setpoint | None,
        unit: Unit = "C",
        strict: bool = True,
    ) -> Setpoint:
        """Normalise one side of a heat/cool pair, keeping the other as it is.

        Mysa's app 4.21.1 changes a Central HVAC's auto targets one at a time,
        writing only the changed one, and refuses a change that would leave the
        pair closer than ``min_setpoint_delta``; it never moves the other side.
        ``side`` is ``"heat"`` (the auto heat range) or ``"cool"`` (the auto
        cool range), and ``other`` the other side's current target in °C
        (``DeviceState.auto_cool_setpoint`` or ``auto_heat_setpoint``), or a
        ``Setpoint``. The rounded value is range-checked like
        :meth:`heat_cool`, then the gap: ``SetpointGapTooSmallError``, whatever
        ``strict`` says. An unknown ``other`` raises
        ``SetpointGapUnknownError``; pass both sides to :meth:`heat_cool`
        instead.
        """
        if side not in ("heat", "cool"):
            raise ValueError(f"A heat/cool side is 'heat' or 'cool', not {side!r}")
        kind = SetpointKind.AUTO_HEAT if side == "heat" else SetpointKind.AUTO_COOL
        setpoint = self._normalise(
            Setpoint,
            self._ranges[kind],
            _finite(value),
            unit,
            strict=strict,
            kind=kind,
            side=side,
        )
        if other is None:
            raise SetpointGapUnknownError(side)
        current = round(
            other.celsius if isinstance(other, Setpoint) else _finite(other), 4
        )
        low, high = (
            (setpoint.celsius, current)
            if side == "heat"
            else (current, setpoint.celsius)
        )
        if round(high - low, 4) < self.min_setpoint_delta:
            raise SetpointGapTooSmallError(
                low=low, high=high, required=self.min_setpoint_delta
            )
        return setpoint

    def limit(
        self,
        value: float,
        *,
        minimum: bool,
        kind: SetpointKind = SetpointKind.HEAT,
        unit: Unit = "C",
        strict: bool = True,
    ) -> SetpointLimit:
        """Normalise an owner minimum or maximum against the hardware range.

        ``kind`` is ``SetpointKind.HEAT`` (the default: the heating limits,
        checked against :attr:`hardware_heat_range`) or ``SetpointKind.COOL``
        (the cooling limits, checked against :attr:`hardware_cool_range`; a
        model that advertises no cooling range raises ``SetpointModeError``).
        Any other kind raises ``ValueError``.

        The result is a :class:`SetpointLimit`: ``setpoint_limit_changes``
        accepts it, and every target writer refuses it with ``TypeError``.

        The rounded value must also stay strictly on its side of the other
        current limit of the same kind (``setpoint_max`` or ``cool_max`` for a
        new minimum, ``setpoint_min`` or ``cool_min`` for a new maximum), or
        ``SetpointLimitOrderError`` is raised, whatever ``strict`` says: equal
        limits would leave a single allowed target. ``validate_cloud_command``
        applies the same rule (:func:`limits_ordered`). Mysa's app 4.21.1 also
        keeps the two limits at least 2 °C apart; this rule does not.
        """
        if kind == SetpointKind.HEAT:
            hardware = self.hardware_heat_range
            other = self.setpoint_max if minimum else self.setpoint_min
        elif kind == SetpointKind.COOL:
            if not self._advertises_cooling:
                raise SetpointModeError(
                    Mode.COOL,
                    "This thermostat advertises no cooling range, so it has no "
                    "cooling limits",
                )
            hardware = self.hardware_cool_range
            other = self.cool_max if minimum else self.cool_min
        else:
            raise ValueError(
                f"Setpoint limits are heating or cooling limits, not {kind.value}"
            )
        setpoint = self._normalise(
            SetpointLimit,
            hardware,
            _finite(value),
            unit,
            strict=strict,
        )
        if other is not None and not limits_ordered(
            setpoint.celsius if minimum else other,
            other if minimum else setpoint.celsius,
        ):
            raise SetpointLimitOrderError(
                value=setpoint.celsius, other=other, minimum=minimum
            )
        return setpoint

    def offset(
        self,
        delta: float,
        *,
        unit: Unit = "C",
        strict: bool = True,
    ) -> TemperatureOffset:
        """Normalise a calibration delta; a °F delta converts by ``/ 1.8``."""
        value = _finite(delta)
        celsius = value / 1.8 if _unit(unit) == "F" else value
        return self._normalise(
            TemperatureOffset,
            self.offset_range,
            celsius,
            "C",
            strict=strict,
            delta=True,
        )

    def schedule_event(
        self,
        mode: Mode | None,
        value: float,
        *,
        unit: Unit = "C",
        strict: bool = True,
    ) -> Setpoint:
        """Normalise a schedule event's temperature exactly like ``setpoint``."""
        return self.setpoint(mode, value, unit=unit, strict=strict)

    def fahrenheit_bounds(
        self,
        kind: SetpointKind | Mode | SetpointRange | None,
        *,
        delta: bool = False,
    ) -> tuple[int | None, int | None]:
        """Lowest and highest whole °F whose normalised value is in range.

        ``kind`` is a setpoint kind, a single-setpoint mode, or any range such
        as ``hardware_heat_range``; ``delta`` treats the range as temperature
        differences (see :meth:`offset_fahrenheit_bounds`).
        """
        limits = kind if isinstance(kind, SetpointRange) else self.range_for(kind)
        return fahrenheit_bounds(limits, delta=delta)

    def offset_fahrenheit_bounds(self) -> tuple[int | None, int | None]:
        """Lowest and highest whole °F calibration delta in ``offset_range``."""
        return fahrenheit_bounds(self.offset_range, delta=True)

    def envelope(self, kinds: Iterable[SetpointKind | Mode | None]) -> SetpointRange:
        """The smallest range in °C covering every listed kind, for display.

        A bound no listed range advertises becomes ``DEFAULT_MINIMUM`` or
        ``DEFAULT_MAXIMUM`` (5-30 °C, as in Mysa's app). The step is the finest
        listed step. If the bounds would not leave room for a second value
        (owner limits equal, say), the maximum is raised by one step, so a
        slider always has two ends. This never validates a write; each write
        is still checked against its own range.
        """
        ranges = [self.range_for(kind) for kind in kinds]
        if not ranges:
            raise ValueError("An envelope needs at least one setpoint kind")
        lows = [r.minimum for r in ranges if r.minimum is not None]
        highs = [r.maximum for r in ranges if r.maximum is not None]
        step = min(r.step for r in ranges)
        low = min(lows) if lows else DEFAULT_MINIMUM
        high = max(highs) if highs else DEFAULT_MAXIMUM
        if high <= low:
            high = round(low + step, 4)
        return SetpointRange(minimum=low, maximum=high, step=step)

    def fahrenheit_envelope(
        self, kinds: Iterable[SetpointKind | Mode | None]
    ) -> tuple[int, int]:
        """Lowest and highest whole °F any listed kind accepts, for display.

        Each kind's whole-°F bounds use its own step; unknown ones become the
        whole °F of the defaults (41 and 86 °F). The maximum is at least one
        degree above the minimum.
        """
        bounds = [self.fahrenheit_bounds(kind) for kind in kinds]
        if not bounds:
            raise ValueError("An envelope needs at least one setpoint kind")
        lows = [low for low, _ in bounds if low is not None]
        highs = [high for _, high in bounds if high is not None]
        low = min(lows) if lows else fahrenheit_label(DEFAULT_MINIMUM)
        high = max(highs) if highs else fahrenheit_label(DEFAULT_MAXIMUM)
        return low, max(high, low + 1)

    @staticmethod
    def _normalise(
        value_type: type[_T],
        limits: SetpointRange,
        value: float,
        unit: Unit,
        *,
        strict: bool,
        kind: SetpointKind | None = None,
        side: Side | None = None,
        delta: bool = False,
    ) -> _T:
        requested = to_celsius(value, unit)
        if abs(requested) > MAX_MAGNITUDE:
            raise SetpointValueError(value, f"{value} is not a plausible temperature")
        limits = (
            _with_fallback(limits, -SAFE_OFFSET, SAFE_OFFSET)
            if delta
            else _with_fallback(limits, SAFE_MINIMUM, SAFE_MAXIMUM)
        )
        assert limits.minimum is not None
        assert limits.maximum is not None
        if _clamp_on_grid(limits.minimum, limits.step, "min") > _clamp_on_grid(
            limits.maximum, limits.step, "max"
        ):
            raise SetpointRangeEmptyError(limits=limits, kind=kind, side=side)
        rounded = round_to_step(requested, limits.step)
        for bound, limit in (("min", limits.minimum), ("max", limits.maximum)):
            if (rounded < limit) if bound == "min" else (rounded > limit):
                if strict:
                    raise SetpointOutOfRangeError(
                        requested=requested,
                        rounded=rounded,
                        limit=limit,
                        bound="min" if bound == "min" else "max",
                        limits=limits,
                        kind=kind,
                        side=side,
                        delta=delta,
                    )
                edge: Bound = "min" if bound == "min" else "max"
                clamp = SetpointClamp(requested=requested, limit=limit, bound=edge)
                return value_type(
                    _clamp_on_grid(limit, limits.step, edge),
                    clamp=clamp,
                    _token=_TOKEN,
                )
        return value_type(rounded, _token=_TOKEN)
