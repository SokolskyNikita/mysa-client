"""Semantic normalization and application for Mysa schedule holds."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .commands import StateChanges, make_state
from .const import Mode
from .setpoints import Setpoint, require_setpoint


@dataclass(frozen=True, slots=True)
class HoldCommand:
    """A resolved hold state ready to send to Mysa."""

    mode: Mode
    temperature: Setpoint | None
    until: datetime | None = None

    def __post_init__(self) -> None:
        """Reject temperatures that did not pass through ``SetpointRules``."""
        if self.temperature is not None:
            require_setpoint(self.temperature)

    @property
    def state(self) -> StateChanges:
        """Mysa's state representation for this hold."""
        return make_state(self.mode, self.temperature)


def resolve_hold(
    *,
    requested_mode: Mode | None,
    current_mode: Mode | None,
    requested_temperature: Setpoint | None,
    current_temperature: Setpoint | None,
    until: datetime | None = None,
) -> HoldCommand:
    """Apply Mysa's default-mode and OFF/setpoint rules to a hold request.

    Normalise the current target with ``SetpointRules(...).setpoint(mode,
    value, strict=False)`` before passing it, since the hold re-sends it.
    """
    if requested_mode is not None:
        mode = requested_mode
    elif current_mode is not None:
        mode = current_mode
    else:
        mode = Mode.HEAT
    if mode == Mode.OFF:
        temperature = None
    elif requested_temperature is None:
        temperature = current_temperature
    else:
        temperature = requested_temperature
    return HoldCommand(mode=mode, temperature=temperature, until=until)


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

    Callbacks let an integration retain command tracing, local-first routing,
    and translated errors while sharing the Mysa-specific hold sequence.
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
