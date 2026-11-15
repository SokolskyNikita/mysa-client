"""Semantic normalization and application for Mysa schedule holds."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .commands import StateChanges, make_state
from .const import NO_SETPOINT_MODES, Mode
from .modes import parse_mode
from .setpoints import (
    Setpoint,
    SetpointModeError,
    SetpointRules,
    Unit,
    require_setpoint,
)


@dataclass(frozen=True, slots=True)
class HoldCommand:
    """A resolved hold state ready to send to Mysa.

    ``mode`` may be given as any spelling :func:`~mysa_client.cloud.modes.parse_mode`
    accepts and is stored as a ``Mode``; anything else raises
    ``UnknownModeError``.
    """

    mode: Mode
    temperature: Setpoint | None
    until: datetime | None = None

    def __post_init__(self) -> None:
        """Normalise the mode; reject temperatures not from ``SetpointRules``."""
        object.__setattr__(self, "mode", parse_mode(self.mode))
        if self.temperature is not None:
            require_setpoint(self.temperature)

    @property
    def state(self) -> StateChanges:
        """Mysa's state representation for this hold."""
        return make_state(self.mode, self.temperature)


def resolve_hold(
    *,
    requested_mode: Mode | str | None,
    current_mode: Mode | str | None,
    requested_temperature: float | Setpoint | None,
    current_temperature: float | Setpoint | None,
    until: datetime | None = None,
    rules: SetpointRules | None = None,
    unit: Unit = "C",
) -> HoldCommand:
    """Apply Mysa's default-mode and OFF/setpoint rules to a hold request.

    The hold's mode is ``requested_mode``, else ``current_mode``, else heat.
    Either may be a ``Mode`` or any spelling
    :func:`~mysa_client.cloud.modes.parse_mode` accepts (``"cool"``,
    ``"HEAT"``, ``"heat_cool"``); anything else raises ``UnknownModeError``.
    With ``rules``, temperatures may be plain numbers and are normalised for
    that mode here: ``requested_temperature`` (in ``unit``) strictly, raising
    ``SetpointOutOfRangeError``; ``current_temperature`` (the device's target,
    always °C) with ``strict=False``, since the hold only re-sends it. A
    ``Setpoint`` is used as given. Without ``rules`` a plain number raises
    ``TypeError``.

    An off, fan-only, dry, sleep or pre-cool hold carries no temperature: both
    temperatures are dropped without being checked. A heat/cool (``Mode.AUTO``) hold can't
    carry a single one: a requested temperature raises ``SetpointModeError``
    and the current target is dropped.
    """
    if requested_mode is not None:
        mode = parse_mode(requested_mode)
    elif current_mode is not None:
        mode = parse_mode(current_mode)
    else:
        mode = Mode.HEAT
    temperature: Setpoint | None = None
    if mode == Mode.OFF or mode in NO_SETPOINT_MODES:
        pass
    elif requested_temperature is not None:
        if mode == Mode.AUTO:
            raise SetpointModeError(
                mode, "A hold in heat/cool mode can't carry a single temperature"
            )
        temperature = _ruled(rules, mode, requested_temperature, unit, strict=True)
    elif current_temperature is not None and mode != Mode.AUTO:
        temperature = _ruled(rules, mode, current_temperature, "C", strict=False)
    return HoldCommand(mode=mode, temperature=temperature, until=until)


def _ruled(
    rules: SetpointRules | None,
    mode: Mode,
    value: float | Setpoint,
    unit: Unit,
    *,
    strict: bool,
) -> Setpoint:
    """A ``Setpoint`` as given, or a number normalised by ``rules``."""
    if isinstance(value, Setpoint):
        return value
    if rules is None:
        require_setpoint(value)  # Raises the standard TypeError.
    assert rules is not None
    return rules.setpoint(mode, value, unit=unit, strict=strict)


async def async_apply_hold(
    command: HoldCommand,
    *,
    set_hold: Callable[[Mapping[str, Any], datetime | None], Awaitable[None]],
    current_mode: Callable[[], Mode | None],
    current_temperature: Callable[[], float | None],
    set_mode: Callable[[Mode], Awaitable[None]],
    set_temperature: Callable[[Setpoint], Awaitable[None]],
) -> None:
    """Set the cloud hold, then immediately apply its state through callbacks.

    ``command`` comes from :func:`resolve_hold`, which already normalised its
    temperature, so no rules are needed here. Callbacks let an integration
    retain command tracing, local-first routing, and translated errors while
    sharing the Mysa-specific hold sequence.
    """
    if command.temperature is not None:
        require_setpoint(command.temperature)
    await set_hold(command.state, command.until)
    if current_mode() != command.mode:
        await set_mode(command.mode)
    if (
        command.temperature is not None
        and current_temperature() != command.temperature.celsius
    ):
        await set_temperature(command.temperature)
