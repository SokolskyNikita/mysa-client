"""One parser for every spelling of a Mysa operating mode."""

from __future__ import annotations

from typing import Final

from .const import MODE_BY_NAME, Mode

# Home Assistant's HVACMode values that differ from a Mode name. HA calls
# Mysa's auto mode "heat_cool"; the other HA names ("off", "heat", "cool",
# "fan_only", "dry", "auto") already match a Mode name in lowercase.
_HOME_ASSISTANT_NAMES: Final[dict[str, Mode]] = {"heat_cool": Mode.AUTO}


class UnknownModeError(ValueError):
    """A mode was given that is not a ``Mode`` and names none.

    ``value`` is what the caller passed.
    """

    def __init__(self, value: object) -> None:
        """Store the refused value."""
        self.value = value
        super().__init__(f"Unknown Mysa mode: {value!r}")


def parse_mode(value: Mode | str) -> Mode:
    """The ``Mode`` for a ``Mode`` or any spelling of a mode name.

    Accepts a ``Mode``; its name in any case (``"HEAT"``, ``"heat"``,
    ``"Fan_Only"``); the capability document's names (``"fanOnly"``,
    ``"emergencyHeat"``); and Home Assistant's lowercase ``HVACMode`` values,
    where ``"heat_cool"`` is ``Mode.AUTO``. Surrounding whitespace is ignored.
    Anything else, including a plain number, raises ``UnknownModeError``: a
    ``Mode`` is an ``IntEnum``, so ``str(Mode.HEAT)`` is ``"4"``, and a bare
    number is not taken for a mode.
    """
    if isinstance(value, Mode):
        return value
    if not isinstance(value, str):
        raise UnknownModeError(value)
    name = value.strip()
    mode = MODE_BY_NAME.get(name) or _HOME_ASSISTANT_NAMES.get(name.lower())
    if mode is not None:
        return mode
    try:
        return Mode[name.upper()]
    except KeyError:
        raise UnknownModeError(value) from None
