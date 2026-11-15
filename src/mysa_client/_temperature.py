"""Typed temperatures shared by every write path, with no dependencies.

``mysa_client.cloud.setpoints.SetpointRules`` is the only code that creates
these values; it re-exports them, and that module is their public home. They
live here so the HomeKit and MQTT packages can require them without importing
the cloud package, which the protocol boundary forbids.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal, NoReturn, Self

Bound = Literal["min", "max"]

_TOKEN: Final = object()


@dataclass(frozen=True, slots=True)
class SetpointClamp:
    """Why a non-strict rules call changed a value beyond rounding it.

    ``requested`` is the caller's value converted to °C, before rounding.
    ``limit`` is the bound the result was clamped to, in °C.
    """

    requested: float
    limit: float
    bound: Bound


class _RuledTemperature:
    """A temperature produced by ``SetpointRules``; not constructible elsewhere."""

    __slots__ = ("_celsius", "_clamp")
    _celsius: float
    _clamp: SetpointClamp | None

    def __init__(
        self,
        celsius: float,
        *,
        clamp: SetpointClamp | None = None,
        _token: object = None,
    ) -> None:
        """Refuse construction outside ``SetpointRules``."""
        if _token is not _TOKEN:
            raise TypeError(
                f"{type(self).__name__} values can only be created by SetpointRules"
            )
        object.__setattr__(self, "_celsius", celsius)
        object.__setattr__(self, "_clamp", clamp)

    @property
    def celsius(self) -> float:
        """The value in °C, already on the device's step grid."""
        return self._celsius

    @property
    def clamp(self) -> SetpointClamp | None:
        """Set only when a non-strict call clamped the value to a bound."""
        return self._clamp

    @property
    def clamped(self) -> bool:
        """Whether a non-strict call clamped the value to a bound."""
        return self._clamp is not None

    def __setattr__(self, name: str, value: Any) -> NoReturn:
        """Keep the value immutable."""
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> NoReturn:
        """Keep the value immutable."""
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __eq__(self, other: object) -> bool:
        """Compare by type and temperature; clamp details are informational."""
        if type(other) is not type(self):
            return NotImplemented
        assert isinstance(other, _RuledTemperature)
        return self._celsius == other._celsius

    def __hash__(self) -> int:
        """Hash by type and temperature."""
        return hash((type(self), self._celsius))

    def __repr__(self) -> str:
        """Show the temperature and any clamp."""
        clamp = f", clamp={self._clamp!r}" if self._clamp else ""
        return f"{type(self).__name__}({self._celsius}{clamp})"

    def __copy__(self) -> Self:
        """Immutable values copy as themselves."""
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        """Immutable values copy as themselves."""
        return self

    def __reduce__(self) -> NoReturn:
        """Refuse pickling, which would bypass the construction check."""
        raise TypeError(f"{type(self).__name__} cannot be pickled; store .celsius")


class Setpoint(_RuledTemperature):
    """A target or limit temperature in °C, rounded and checked by the rules."""

    __slots__ = ()


class TemperatureOffset(_RuledTemperature):
    """A room-temperature calibration delta in °C, rounded and checked."""

    __slots__ = ()


def require_setpoint(value: object, name: str = "temperature") -> float:
    """Return a ``Setpoint``'s °C value, or raise ``TypeError`` for anything else."""
    if not isinstance(value, Setpoint):
        raise TypeError(
            f"{name} must be a Setpoint from SetpointRules, not {type(value).__name__}"
        )
    return value.celsius


def require_offset(value: object, name: str = "offset") -> float:
    """Return a ``TemperatureOffset``'s °C value, or raise ``TypeError``."""
    if not isinstance(value, TemperatureOffset):
        raise TypeError(
            f"{name} must be a TemperatureOffset from SetpointRules, "
            f"not {type(value).__name__}"
        )
    return value.celsius
