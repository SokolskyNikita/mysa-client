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

from .const import SETPOINT_SECTION_BY_MODE, Mode
from .device import heating_range
from .models import Capabilities

DEFAULT_STEP: Final = 0.5
"""The step assumed when neither HomeKit nor the cloud advertises one."""

Unit = Literal["C", "F"]
RangeTuple = tuple[float | None, float | None, float | None]

_T = TypeVar("_T", bound=_RuledTemperature)

__all__ = [
    "DEFAULT_STEP",
    "Bound",
    "RangeTuple",
    "Setpoint",
    "SetpointClamp",
    "SetpointError",
    "SetpointGapTooSmallError",
    "SetpointKind",
    "SetpointOutOfRangeError",
    "SetpointRange",
    "SetpointRules",
    "TemperatureOffset",
    "Unit",
    "fahrenheit_label",
    "fahrenheit_offset",
    "kind_for_mode",
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
    """

    def __init__(
        self, *, requested: float, rounded: float, limit: float, bound: Bound
    ) -> None:
        """Store the requested and rounded values and the violated bound."""
        self.requested = requested
        self.rounded = rounded
        self.limit = limit
        self.bound: Bound = bound
        relation = "below the minimum" if bound == "min" else "above the maximum"
        super().__init__(f"{rounded} °C is {relation} of {limit} °C")


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


def kind_for_mode(mode: Mode | None) -> SetpointKind:
    """The single-setpoint range a mode writes, matching the command encoder.

    Cool writes ``targetCool``; every other mode, including none, writes
    ``targetHeat``. ``Mode.AUTO`` has two setpoints and raises ``ValueError``.
    """
    if mode == Mode.AUTO:
        raise ValueError("Heat/cool mode has two setpoints; use heat_cool()")
    section = SETPOINT_SECTION_BY_MODE.get(mode or Mode.HEAT, "targetHeat")
    return SetpointKind.COOL if section == "targetCool" else SetpointKind.HEAT


class SetpointRules:
    """Round and validate every temperature written to one thermostat.

    ``homekit_range`` is the HomeKit accessory's ``(min, max, step)``; any part
    may be ``None``. It applies to the heat range, where it is intersected with
    the cloud capability range exactly as :func:`heating_range` does, and its
    step wins. Cool and heat/cool ranges come from ``capabilities`` only.
    ``setpoint_min`` and ``setpoint_max`` are the owner's limits; they narrow
    the heat range but not the hardware range used to validate new limits.
    Without capabilities (a local-only thermostat) only the HomeKit range
    applies. A missing step is assumed to be 0.5 °C; a missing bound is not
    checked.
    """

    def __init__(
        self,
        capabilities: Capabilities | None = None,
        homekit_range: RangeTuple | None = None,
        *,
        setpoint_min: float | None = None,
        setpoint_max: float | None = None,
    ) -> None:
        """Derive every range once from the thermostat's advertised limits."""
        local = homekit_range or (None, None, None)
        caps = capabilities
        self.capabilities = caps
        self.hardware_heat_range = _range(heating_range(local, caps, None, None))
        self._ranges = {
            SetpointKind.HEAT: _range(
                heating_range(local, caps, setpoint_min, setpoint_max)
            ),
            SetpointKind.COOL: _range(caps.cool_setpoint_range if caps else None),
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
        """Normalise a single heat or cool target for ``mode``."""
        return self._normalise(
            Setpoint, self.range_for(mode), _finite(value), unit, strict=strict
        )

    def heat_cool(
        self,
        low: float,
        high: float,
        *,
        unit: Unit = "C",
        strict: bool = True,
    ) -> tuple[Setpoint, Setpoint]:
        """Normalise a heat/cool pair; the gap is checked, never adjusted."""
        heat = self._normalise(
            Setpoint,
            self._ranges[SetpointKind.AUTO_HEAT],
            _finite(low),
            unit,
            strict=strict,
        )
        cool = self._normalise(
            Setpoint,
            self._ranges[SetpointKind.AUTO_COOL],
            _finite(high),
            unit,
            strict=strict,
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
        """Normalise an owner minimum or maximum against the hardware heat range."""
        del minimum  # Both limits share the hardware range; kept for call clarity.
        return self._normalise(
            Setpoint, self.hardware_heat_range, _finite(value), unit, strict=strict
        )

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
            TemperatureOffset, self.offset_range, celsius, "C", strict=strict
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
        self, kind: SetpointKind | Mode | None
    ) -> tuple[int | None, int | None]:
        """Lowest and highest whole °F whose normalised value is in range."""
        limits = self.range_for(kind)

        def inside(fahrenheit: int) -> bool:
            celsius = round_to_step(to_celsius(fahrenheit, "F"), limits.step)
            return (limits.minimum is None or celsius >= limits.minimum) and (
                limits.maximum is None or celsius <= limits.maximum
            )

        low = high = None
        if limits.minimum is not None:
            start = math.floor(limits.minimum * 1.8 + 32) - 2
            low = next((f for f in range(start, start + 6) if inside(f)), None)
        if limits.maximum is not None:
            start = math.ceil(limits.maximum * 1.8 + 32) + 2
            high = next((f for f in range(start, start - 6, -1) if inside(f)), None)
        return low, high

    @staticmethod
    def _normalise(
        value_type: type[_T],
        limits: SetpointRange,
        value: float,
        unit: Unit,
        *,
        strict: bool,
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
                    )
                side: Bound = "min" if bound == "min" else "max"
                clamp = SetpointClamp(requested=requested, limit=limit, bound=side)
                return value_type(
                    _clamp_on_grid(limit, limits.step, side),
                    clamp=clamp,
                    _token=_TOKEN,
                )
        return value_type(rounded, _token=_TOKEN)
