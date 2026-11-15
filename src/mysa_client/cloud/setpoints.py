"""The single source of truth for Mysa setpoint rounding and validation.

Mysa thermostats accept setpoints only on their advertised step grid (usually
0.5 °C). Callers that convert from Fahrenheit, such as Home Assistant, hand
over values like 22.78 °C that the device rejects. ``SetpointRules`` rounds and
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
    TemperatureOffset,
    _RuledTemperature,
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

Unit = Literal["C", "F"]
Side = Literal["heat", "cool"]
RangeTuple = tuple[float | None, float | None, float | None]

_T = TypeVar("_T", bound=_RuledTemperature)

__all__ = [
    "DEFAULT_MAXIMUM",
    "DEFAULT_MINIMUM",
    "DEFAULT_STEP",
    "Bound",
    "RangeTuple",
    "Setpoint",
    "SetpointClamp",
    "SetpointError",
    "SetpointGapTooSmallError",
    "SetpointKind",
    "SetpointLimitOrderError",
    "SetpointModeError",
    "SetpointOutOfRangeError",
    "SetpointRange",
    "SetpointRules",
    "Side",
    "TemperatureOffset",
    "Unit",
    "fahrenheit_bounds",
    "fahrenheit_label",
    "fahrenheit_offset",
    "kind_for_mode",
    "limits_ordered",
    "require_offset",
    "require_setpoint",
    "round_half_up",
    "round_to_step",
    "to_celsius",
]


class SetpointKind(StrEnum):
    """The setpoint ranges a thermostat can advertise, each with its own step."""

    HEAT = "heat"
    COOL = "cool"
    AUTO_HEAT = "auto_heat"
    AUTO_COOL = "auto_cool"


@dataclass(frozen=True, slots=True)
class SetpointRange:
    """One normalised range in °C; a ``None`` bound is unknown, not unlimited."""

    minimum: float | None
    maximum: float | None
    step: float


class SetpointError(ValueError):
    """Base class for values the setpoint rules refuse."""


class SetpointOutOfRangeError(SetpointError):
    """A rounded value lies outside the applicable range.

    All numbers are in °C so callers can word the message in the user's unit.
    ``limits`` is the whole range that was violated and ``kind`` the setpoint
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
    """A single setpoint was given for a mode that cannot carry one here.

    Holds and schedule events store one ``targetHeat`` or ``targetCool``
    setpoint; heat/cool mode needs a pair, so a hold or event in that mode
    cannot carry a temperature.
    """

    def __init__(self, mode: Mode, message: str) -> None:
        """Store the mode that was refused."""
        self.mode = mode
        super().__init__(message)


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
    if not math.isfinite(value):
        raise ValueError("Temperature must be finite")
    return float(value)


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
    and ``validate_command``.
    """
    return round(minimum, 4) < round(maximum, 4)


def _range(values: RangeTuple | None) -> SetpointRange:
    low, high, step = values or (None, None, None)
    return SetpointRange(
        minimum=None if low is None else round(float(low), 4),
        maximum=None if high is None else round(float(high), 4),
        step=float(step) if step else DEFAULT_STEP,
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


def kind_for_mode(mode: Mode | None) -> SetpointKind:
    """The single-setpoint range a mode writes, matching the command encoder.

    Cool writes ``targetCool``; every other mode, including none, writes
    ``targetHeat``. In heat/cool mode (``Mode.AUTO``) a single setpoint means
    the heat target, so it uses the heat range; use
    :meth:`SetpointRules.heat_cool` for the pair. ``temperature_changes`` and
    ``DeviceState.setpoint_for`` follow the same rule.
    """
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
    Without capabilities or a record (a local-only thermostat) only the HomeKit
    range applies. A missing step is assumed to be 0.5 °C; a missing bound is
    not checked when validating, and an :meth:`envelope` assumes
    ``DEFAULT_MINIMUM``-``DEFAULT_MAXIMUM`` for it.
    """

    def __init__(
        self,
        capabilities: Capabilities | None = None,
        homekit_range: RangeTuple | None = None,
        *,
        setpoint_min: float | None = None,
        setpoint_max: float | None = None,
        record: DeviceRecord | None = None,
    ) -> None:
        """Derive every range once from the thermostat's advertised limits."""
        local = homekit_range or (None, None, None)
        caps = capabilities
        self.capabilities = caps
        self.record = record
        # Only a Mini-Split AC's record describes its remote's temperatures.
        ac = record if record and model_family(record.model) == ModelFamily.AC else None
        if ac is not None and (caps is None or caps.heat_setpoint_range is None):
            local = _intersect(local, ac.ac_temperature_range(Mode.HEAT))
        cool: RangeTuple | None = caps.cool_setpoint_range if caps else None
        if cool is None and ac is not None:
            cool = ac.ac_temperature_range(Mode.COOL)
        if cool is None and homekit_range is not None:
            cool = homekit_range
        self.setpoint_min = None if setpoint_min is None else round(setpoint_min, 4)
        self.setpoint_max = None if setpoint_max is None else round(setpoint_max, 4)
        self.hardware_heat_range = _range(heating_range(local, caps, None, None))
        self._ranges = {
            SetpointKind.HEAT: _range(
                heating_range(local, caps, setpoint_min, setpoint_max)
            ),
            SetpointKind.COOL: _range(cool),
            SetpointKind.AUTO_HEAT: _range(caps.auto_heat_range if caps else None),
            SetpointKind.AUTO_COOL: _range(caps.auto_cool_range if caps else None),
        }
        self.offset_range = _range(caps.offset_range if caps else None)
        delta = caps.min_setpoint_delta if caps else None
        self.min_setpoint_delta = round(delta, 4) if delta is not None else 0.0

    def range_for(self, kind: SetpointKind | Mode | None) -> SetpointRange:
        """The normalised range for a setpoint kind or a single-setpoint mode."""
        if not isinstance(kind, SetpointKind):
            kind = kind_for_mode(kind)
        return self._ranges[kind]

    def setpoint(
        self,
        mode: Mode | None,
        value: float,
        *,
        unit: Unit = "C",
        strict: bool = True,
    ) -> Setpoint:
        """Normalise a single heat or cool target for ``mode``.

        In heat/cool mode a single target is the heat target
        (see :func:`kind_for_mode`).
        """
        kind = kind_for_mode(mode)
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

    def limit(
        self,
        value: float,
        *,
        minimum: bool,
        unit: Unit = "C",
        strict: bool = True,
    ) -> Setpoint:
        """Normalise an owner minimum or maximum against the hardware heat range.

        The rounded value must also stay strictly on its side of the other
        current limit (``setpoint_max`` for a new minimum, ``setpoint_min`` for
        a new maximum), or ``SetpointLimitOrderError`` is raised, whatever
        ``strict`` says: equal limits would leave a single allowed target.
        ``validate_command`` applies the same rule (:func:`limits_ordered`).
        """
        setpoint = self._normalise(
            Setpoint, self.hardware_heat_range, _finite(value), unit, strict=strict
        )
        other = self.setpoint_max if minimum else self.setpoint_min
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
        rounded = round_to_step(requested, limits.step)
        for bound, limit in (("min", limits.minimum), ("max", limits.maximum)):
            if limit is None:
                continue
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
