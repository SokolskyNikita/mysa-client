"""Edge cases where SetpointRules accepted or returned values it should not.

New names are read from the module inside each test, so on a release without
them each test fails on its own rather than the whole file failing to import.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import time
import itertools
from typing import Any

import pytest

from mysa_client.cloud import Capabilities, Mode, ModelFamily, setpoints as sp
from mysa_client.cloud.commands import (
    ScheduleEventSpec,
    auto_temperature_changes,
    make_state,
    setpoint_limit_changes,
    temperature_changes,
)
from mysa_client.cloud.device_commands import climate_command
from mysa_client.cloud.holds import HoldCommand, async_apply_hold, resolve_hold
from mysa_client.cloud.schedule_workflow import build_schedule_events
from mysa_client.cloud.setpoints import (
    Setpoint,
    SetpointError,
    SetpointModeError,
    SetpointOutOfRangeError,
    SetpointRules,
    require_setpoint,
)

BASE = Capabilities.from_api({})
BASEBOARD = replace(BASE, heat_setpoint_range=(5.0, 30.0, 0.5))
HOMEKIT = (5.0, 30.0, 0.5)


# 1. Strict mode with an unknown bound ----------------------------------------


def test_unknown_bounds_use_the_safe_envelope_in_strict_mode() -> None:
    rules = SetpointRules()
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.setpoint(None, 500)
    assert (error.value.limit, error.value.bound) == (sp.SAFE_MAXIMUM, "max")
    # The envelope fills the range on the error, so a °F message is possible.
    assert error.value.limits == sp.SetpointRange(5.0, 35.0, 0.5)
    assert error.value.fahrenheit_limit == 95
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.setpoint(Mode.COOL, 2)
    assert (error.value.limit, error.value.bound) == (sp.SAFE_MINIMUM, "min")
    # Ordinary targets are untouched, and range_for still reports "unknown".
    assert rules.setpoint(None, 21.3).celsius == 21.5
    assert rules.setpoint(Mode.COOL, 35).celsius == 35.0
    assert rules.range_for(Mode.HEAT) == sp.SetpointRange(None, None, 0.5)


def test_unknown_bounds_clamp_to_the_safe_envelope() -> None:
    result = SetpointRules().setpoint(None, 500, strict=False)
    assert result.celsius == 35.0
    assert result.clamp == sp.SetpointClamp(requested=500.0, limit=35.0, bound="max")


def test_heat_cool_without_auto_ranges_is_bounded() -> None:
    rules = SetpointRules(homekit_range=HOMEKIT)
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.heat_cool(99, 99)
    assert (error.value.side, error.value.limit) == ("heat", 35.0)
    assert [s.celsius for s in rules.heat_cool(20, 24)] == [20.0, 24.0]


def test_cool_schedule_event_without_a_cool_range_is_bounded() -> None:
    event = {"days": ["mon"], "time": time(7), "hvac_mode": "cool", "temperature": 99}
    with pytest.raises(SetpointOutOfRangeError):
        build_schedule_events([event], rules=SetpointRules())
    event["temperature"] = 24
    (encoded,) = build_schedule_events([event], rules=SetpointRules())
    assert encoded["state"]["targetCool"] == {"setpoint": 24.0}


def test_unknown_limit_and_offset_ranges_are_bounded() -> None:
    rules = SetpointRules()
    with pytest.raises(SetpointOutOfRangeError):
        rules.limit(90, minimum=False)
    assert rules.limit(25, minimum=False).celsius == 25.0
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.offset(30)
    assert (error.value.limit, error.value.delta) == (sp.SAFE_OFFSET, True)
    assert rules.offset(-5).celsius == -5.0
    assert rules.offset(-9, unit="F").celsius == -5.0


def test_a_known_bound_wider_than_the_envelope_still_wins() -> None:
    rules = SetpointRules(homekit_range=(5.0, 40.0, 0.5))
    assert rules.setpoint(Mode.HEAT, 38).celsius == 38.0
    offsets = SetpointRules(replace(BASE, offset_range=(-8.0, 8.0, 0.5)))
    assert offsets.offset(7).celsius == 7.0


# 2. A limit is not a target ---------------------------------------------------


def _limit() -> Setpoint:
    rules = SetpointRules(homekit_range=HOMEKIT, setpoint_max=21)
    return rules.limit(28, minimum=False)


def test_a_limit_is_a_distinct_setpoint() -> None:
    limit = _limit()
    assert type(limit) is sp.SetpointLimit
    assert isinstance(limit, Setpoint)  # Code that stores limits keeps working.
    assert limit.celsius == 28.0
    assert setpoint_limit_changes(limit, minimum=False) == {
        "targetHeat": {"lockoutMax": 28.0}
    }


@pytest.mark.parametrize(
    "build",
    [
        lambda v: temperature_changes(Mode.HEAT, v),
        lambda v: climate_command(ModelFamily.BASEBOARD_V2, Mode.HEAT, temperature=v),
        lambda v: auto_temperature_changes(v, v),
        lambda v: make_state(Mode.HEAT, v),
        lambda v: ScheduleEventSpec(("mon",), time(7), Mode.HEAT, v, "key"),
        lambda v: HoldCommand(Mode.HEAT, v),
        lambda v: resolve_hold(
            requested_mode=Mode.HEAT,
            current_mode=None,
            requested_temperature=v,
            current_temperature=None,
        ),
        lambda v: build_schedule_events(
            [{"days": ["mon"], "time": time(7), "temperature": v}]
        ),
        require_setpoint,
    ],
)
def test_target_writers_refuse_a_limit(build: Any) -> None:
    with pytest.raises(TypeError, match="SetpointLimit"):
        build(_limit())


def test_homekit_and_mqtt_writers_refuse_a_limit() -> None:
    from mysa_client.homekit.device import _semantic_write_values
    from mysa_client.homekit.models import Field
    from mysa_client.mqtt.protocol import encode_thermostat_command

    with pytest.raises(TypeError, match="SetpointLimit"):
        _semantic_write_values({Field.TARGET_TEMPERATURE: _limit()})
    with pytest.raises(TypeError, match="SetpointLimit"):
        encode_thermostat_command(
            device_id="d",
            user_sub="u",
            request_id=1,
            command_time=1,
            setpoint=_limit(),
        )


# 3. A clamp stays inside the range ---------------------------------------------


def test_a_range_with_no_on_step_value_raises() -> None:
    rules = SetpointRules(homekit_range=(20.2, 20.4, 0.5))
    for strict in (False, True):
        with pytest.raises(sp.SetpointRangeEmptyError) as error:
            rules.setpoint(None, 10, strict=strict)
        assert error.value.limits == sp.SetpointRange(20.2, 20.4, 0.5)
        assert error.value.kind == sp.SetpointKind.HEAT


def test_inverted_owner_limits_raise_instead_of_clamping() -> None:
    rules = SetpointRules(homekit_range=HOMEKIT, setpoint_min=25, setpoint_max=20)
    with pytest.raises(sp.SetpointRangeEmptyError):
        rules.setpoint(Mode.HEAT, 22, strict=False)


@pytest.mark.parametrize(
    "limits",
    [
        (20.2, 20.4, 0.5),
        (20.2, 21.3, 0.5),
        (20.0, 20.0, 0.5),
        (16.3, 29.7, 1.0),
        (5.0, 30.0, 0.5),
        (25.0, 20.0, 0.5),
        (18.1, 18.9, 1.0),
    ],
)
def test_non_strict_results_always_lie_inside_the_range(
    limits: tuple[float, float, float],
) -> None:
    low, high, _ = limits
    rules = SetpointRules(homekit_range=limits)
    for value, unit in itertools.product(
        [-40, 0, 10, low - 0.01, low, (low + high) / 2, high, high + 0.01, 33, 99],
        ("C", "F"),
    ):
        try:
            result = rules.setpoint(Mode.HEAT, value, unit=unit, strict=False)
        except sp.SetpointRangeEmptyError:
            continue
        assert low <= result.celsius <= high, (limits, value, unit, result)


# 4. Typed errors for non-finite or absurd input and configuration -------------


@pytest.mark.parametrize(
    "call",
    [
        lambda r: r.setpoint(Mode.HEAT, 1e308, strict=False),
        lambda r: r.setpoint(Mode.HEAT, -1e308),
        lambda r: r.setpoint(Mode.HEAT, 1e308, unit="F", strict=False),
        lambda r: r.setpoint(Mode.HEAT, 10**400, strict=False),
        lambda r: r.setpoint(Mode.HEAT, 1500, strict=False),
        lambda r: r.setpoint(Mode.HEAT, float("nan")),
        lambda r: r.setpoint(Mode.HEAT, float("inf"), strict=False),
        lambda r: r.heat_cool(1e308, 1e308, strict=False),
        lambda r: r.limit(1e308, minimum=True, strict=False),
        lambda r: r.offset(1e308, strict=False),
        lambda r: r.offset(float("nan"), unit="F"),
    ],
)
def test_absurd_values_raise_a_setpoint_error(call: Any) -> None:
    with pytest.raises(SetpointError) as error:
        call(SetpointRules(BASEBOARD))
    assert type(error.value).__name__ == "SetpointValueError"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"homekit_range": (5.0, 30.0, float("nan"))},
        {"homekit_range": (5.0, 30.0, -0.5)},
        {"homekit_range": (5.0, 30.0, 1e300)},
        {"homekit_range": (float("nan"), 30.0, 0.5)},
        {"homekit_range": (5.0, float("inf"), 0.5)},
        {"homekit_range": (5.0, 1e308, 0.5)},
        {"setpoint_min": float("nan")},
        {"setpoint_max": float("-inf")},
        {"capabilities": replace(BASE, heat_setpoint_range=(5.0, 30.0, float("nan")))},
        {"capabilities": replace(BASE, cool_setpoint_range=(float("nan"), 30.0, 0.5))},
        {"capabilities": replace(BASE, offset_range=(-5.0, 5.0, float("inf")))},
        {"capabilities": replace(BASE, min_setpoint_delta=float("nan"))},
        {"capabilities": replace(BASE, min_setpoint_delta=-1.0)},
    ],
)
def test_unusable_configuration_raises_a_setpoint_error(
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(SetpointError) as error:
        SetpointRules(**kwargs)
    assert type(error.value).__name__ == "SetpointConfigError"


def test_a_nan_minimum_no_longer_disables_the_minimum() -> None:
    with pytest.raises(SetpointError):
        SetpointRules(homekit_range=HOMEKIT, setpoint_min=float("nan")).setpoint(
            Mode.HEAT, 6
        )


def test_typed_errors_are_still_value_errors() -> None:
    """Callers catching ValueError (the old NaN error) keep working."""
    with pytest.raises(ValueError, match="finite"):
        SetpointRules().setpoint(None, float("nan"))
    assert issubclass(sp.SetpointValueError, ValueError)
    assert issubclass(sp.SetpointConfigError, ValueError)
    assert issubclass(sp.SetpointRangeEmptyError, ValueError)


def test_non_numbers_still_raise_type_error() -> None:
    with pytest.raises(TypeError):
        SetpointRules().setpoint(None, True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        SetpointRules().setpoint(None, "21")  # type: ignore[arg-type]


# 5. Modes without a setpoint ------------------------------------------------------

NO_SETPOINT = [Mode.FAN_ONLY, Mode.DRY, Mode.SLEEP, Mode.PRE_COOL]


@pytest.mark.parametrize("mode", NO_SETPOINT)
def test_schedule_events_drop_a_temperature_their_mode_cannot_use(mode: Mode) -> None:
    event = {
        "days": ["mon"],
        "time": time(7),
        "hvac_mode": mode.name.lower(),
        "temperature": 99,
    }
    (encoded,) = build_schedule_events([event], rules=SetpointRules(BASEBOARD))
    assert encoded["state"] == {"modes": {"mode": int(mode)}}
    # Without rules a plain number is dropped too, like an off event's.
    (encoded,) = build_schedule_events([event])
    assert encoded["state"] == {"modes": {"mode": int(mode)}}


@pytest.mark.parametrize("mode", NO_SETPOINT)
async def test_holds_drop_a_temperature_their_mode_cannot_use(mode: Mode) -> None:
    rules = SetpointRules(BASEBOARD)
    command = resolve_hold(
        requested_mode=mode,
        current_mode=Mode.HEAT,
        requested_temperature=99,
        current_temperature=None,
        rules=rules,
    )
    assert command.temperature is None
    assert command.state == {"modes": {"mode": int(mode)}}
    kept = resolve_hold(
        requested_mode=None,
        current_mode=mode,
        requested_temperature=None,
        current_temperature=21.0,
        rules=rules,
    )
    assert kept.temperature is None
    written: list[Setpoint] = []

    async def set_temperature(value: Setpoint) -> None:
        written.append(value)

    async def ignore(*_: Any) -> None:
        return None

    await async_apply_hold(
        kept,
        set_hold=ignore,
        current_mode=lambda: mode,
        current_temperature=lambda: 21.0,
        set_mode=ignore,
        set_temperature=set_temperature,
    )
    assert written == []


@pytest.mark.parametrize("mode", NO_SETPOINT)
def test_temperature_changes_refuse_modes_without_a_setpoint(mode: Mode) -> None:
    setpoint = SetpointRules(BASEBOARD).setpoint(mode, 21)
    with pytest.raises(SetpointModeError) as error:
        temperature_changes(mode, setpoint)
    assert error.value.mode == mode


@pytest.mark.parametrize(
    ("mode", "section"),
    [
        (None, "targetHeat"),
        (Mode.OFF, "targetHeat"),
        (Mode.HEAT, "targetHeat"),
        (Mode.EMERGENCY_HEAT, "targetHeat"),
        (Mode.COOL, "targetCool"),
    ],
)
def test_temperature_changes_keep_the_other_modes(
    mode: Mode | None, section: str
) -> None:
    setpoint = SetpointRules(BASEBOARD).setpoint(mode, 21)
    assert temperature_changes(mode, setpoint) == {section: {"setpoint": 21.0}}
