"""Device semantics shared by applications, independent of their UI."""

from __future__ import annotations

from collections.abc import Callable

from .const import Mode, ModelFamily
from .models import Capabilities, DeviceState
from .observations import number

BRIGHTNESS_STEP = 5
MIN_WAKE_BRIGHTNESS = 10
HEATING_FAMILIES = frozenset(
    {
        ModelFamily.BASEBOARD_V1,
        ModelFamily.BASEBOARD_V2,
        ModelFamily.BASEBOARD_LITE,
        ModelFamily.BASEBOARD_V3,
        ModelFamily.INFLOOR,
        ModelFamily.CENTRAL_HVAC,
    }
)


def hvac_activity(
    state: DeviceState, fresh: Callable[[str], bool], mode: Mode | None = None
) -> str | None:
    """Decode Central HVAC stages using each report's own freshness."""
    selected_mode = state.mode if mode is None else mode
    idle = "off" if selected_mode == Mode.OFF else "idle"
    if state.hvac_stages is not None and fresh("hvac_stages"):
        stages = state.hvac_stages
        heat = any(stages[k] for k in ("HeatingStage1", "HeatingStage2", "AuxHeat"))
        cool = any(stages[k] for k in ("CoolingStage1", "CoolingStage2"))
        fan = False
    elif state.hvac_state is not None and fresh("hvac_state"):
        heat = bool(state.hvac_state & (256 | 512 | 4096))
        cool = bool(state.hvac_state & (1024 | 2048))
        fan = bool(state.hvac_state & 8192)
    else:
        return None
    if heat and cool:
        return None
    if heat:
        return "heating"
    if cool:
        return "cooling"
    return "fan" if fan else idle


def hvac_fan_operation(state: DeviceState | None) -> str | None:
    """Interpret always-on, circulation, auto and disabled fan configuration."""
    if state is None or state.fan_always_on is None:
        return None
    if state.fan_always_on == 1:
        return "on"
    if state.fan_runtime is None or state.fan_period is None:
        return None
    if state.fan_runtime > 0:
        return "runtime"
    return "auto" if state.fan_period > 0 else "off"


def basic_modes(
    family: ModelFamily | None, caps: Capabilities | None, state: DeviceState | None
) -> list[Mode]:
    """Advertised basic modes, respecting the device's disabled-auto setting."""
    modes = caps.basic_modes(family) if caps else []
    return (
        [m for m in modes if m != Mode.AUTO]
        if state and state.auto_enabled == 0
        else modes
    )


def brightness_pair(
    value: float, *, active: bool, wake: float = 100, sleep: float = 50
) -> tuple[int, int]:
    """Apply Mysa's five-percent steps and keep sleep no brighter than wake."""
    if number(value) is None or not 0 <= value <= 100:
        raise ValueError("Brightness must be between 0 and 100")
    rounded = round(value / BRIGHTNESS_STEP) * BRIGHTNESS_STEP
    if active:
        wake = max(MIN_WAKE_BRIGHTNESS, rounded)
        sleep = min(sleep, wake)
    else:
        sleep = rounded
        wake = max(wake, sleep)
    if not 0 <= sleep <= wake <= 100:
        raise ValueError("Brightness must satisfy 0 <= idle <= active <= 100")
    return int(wake), int(sleep)


def heating_range(
    local: tuple[float | None, float | None, float | None],
    caps: Capabilities | None,
    minimum: float | None,
    maximum: float | None,
) -> tuple[float | None, float | None, float | None]:
    """Intersect advertised and configured heating limits without inventing bounds."""
    low, high, step = local
    if caps is not None and caps.heat_setpoint_range is not None:
        c_low, c_high, c_step = caps.heat_setpoint_range
        low = c_low if low is None else max(low, c_low)
        high = c_high if high is None else min(high, c_high)
        step = step or c_step
    if minimum is not None:
        low = minimum if low is None else max(low, minimum)
    if maximum is not None:
        high = maximum if high is None else min(high, maximum)
    return low, high, step
