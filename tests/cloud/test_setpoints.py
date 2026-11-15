"""Setpoint rounding, validation and Fahrenheit display rules."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import time
import pickle
from typing import Any

import pytest

from mysa_client.cloud import (
    Capabilities,
    DeviceRecord,
    DeviceState,
    Mode,
    ModelFamily,
)
from mysa_client.cloud.client import MysaClient
from mysa_client.cloud.commands import (
    ScheduleEventSpec,
    auto_temperature_changes,
    make_state,
    setpoint_limit_changes,
    temperature_changes,
    temperature_offset_changes,
)
from mysa_client.cloud.device import heating_range
from mysa_client.cloud.device_commands import climate_command
from mysa_client.cloud.holds import HoldCommand, async_apply_hold, resolve_hold
from mysa_client.cloud.schedule_workflow import build_schedule_events
from mysa_client.cloud.setpoints import (
    DEFAULT_MAXIMUM,
    DEFAULT_MINIMUM,
    DEFAULT_STEP,
    Setpoint,
    SetpointClamp,
    SetpointError,
    SetpointGapTooSmallError,
    SetpointKind,
    SetpointLimitOrderError,
    SetpointModeError,
    SetpointOutOfRangeError,
    SetpointRange,
    SetpointRules,
    TemperatureOffset,
    fahrenheit_bounds,
    fahrenheit_label,
    fahrenheit_offset,
    kind_for_mode,
    limits_ordered,
    round_to_step,
)

BASE = Capabilities.from_api({})
BASEBOARD = replace(BASE, heat_setpoint_range=(5.0, 30.0, 0.5))
HVAC = replace(
    BASE,
    heat_setpoint_range=(5.0, 30.0, 0.5),
    cool_setpoint_range=(15.0, 35.0, 1.0),
    auto_heat_range=(10.0, 30.0, 0.5),
    auto_cool_range=(12.0, 32.0, 1.0),
    offset_range=(-5.0, 5.0, 0.1),
    min_setpoint_delta=2.0,
)
WIDE = SetpointRules(replace(BASE, heat_setpoint_range=(0.0, 50.0, 0.5)))


# Every whole °F in the Mysa app's range and the nearest 0.5 °C step.
FAHRENHEIT_TO_CELSIUS = [
    (f, round_to_step((f - 32) / 1.8, 0.5)) for f in range(41, 105)
]


@pytest.mark.parametrize(("fahrenheit", "celsius"), FAHRENHEIT_TO_CELSIUS)
def test_every_whole_fahrenheit_round_trips(fahrenheit: int, celsius: float) -> None:
    setpoint = WIDE.setpoint(Mode.HEAT, fahrenheit, unit="F")
    assert setpoint.celsius == celsius
    assert setpoint.celsius * 2 == int(setpoint.celsius * 2)
    assert abs(setpoint.celsius - (fahrenheit - 32) / 1.8) <= 0.25
    assert fahrenheit_label(setpoint) == fahrenheit


def test_seventy_three_fahrenheit_is_the_reported_bug() -> None:
    """HA's 73 °F arrives as 22.78 °C, which the thermostat rejects."""
    assert WIDE.setpoint(Mode.HEAT, 22.78).celsius == 23.0
    assert WIDE.setpoint(Mode.HEAT, 73, unit="F").celsius == 23.0


NO_WHOLE_FAHRENHEIT = {
    7.5: 46,
    12.5: 55,
    17.5: 64,
    22.5: 73,
    27.5: 82,
    32.5: 91,
    37.5: 100,
}
CELSIUS_LABELS = [
    (c / 2, NO_WHOLE_FAHRENHEIT.get(c / 2, round(c / 2 * 1.8 + 32)))
    for c in range(10, 81)
]


@pytest.mark.parametrize(("celsius", "label"), CELSIUS_LABELS)
def test_every_half_celsius_gets_the_app_label(celsius: float, label: int) -> None:
    assert fahrenheit_label(celsius) == label


@pytest.mark.parametrize(("celsius", "label"), sorted(NO_WHOLE_FAHRENHEIT.items()))
def test_halfway_labels_round_up_unlike_python(celsius: float, label: int) -> None:
    assert fahrenheit_label(celsius) == label
    assert celsius * 1.8 + 32 == pytest.approx(label - 0.5)
    # A whole °F of their own would round-trip; these values have none, so the
    # label's Celsius is a different step.
    assert WIDE.setpoint(Mode.HEAT, label, unit="F").celsius != celsius


@pytest.mark.parametrize(
    ("maximum", "minimum", "expected"),
    [
        (21.5, 18.0, (64, 71)),
        (22.5, 17.5, (64, 72)),
        (22.0, 18.5, (65, 72)),
        (30.0, 5.0, (41, 86)),
    ],
)
def test_fahrenheit_bounds(
    maximum: float, minimum: float, expected: tuple[int, int]
) -> None:
    rules = SetpointRules(BASEBOARD, setpoint_min=minimum, setpoint_max=maximum)
    low, high = rules.fahrenheit_bounds(Mode.HEAT)
    assert (low, high) == expected
    assert low is not None
    assert high is not None
    assert rules.setpoint(Mode.HEAT, low, unit="F").celsius >= minimum
    assert rules.setpoint(Mode.HEAT, high, unit="F").celsius <= maximum
    with pytest.raises(SetpointOutOfRangeError):
        rules.setpoint(Mode.HEAT, low - 1, unit="F")
    with pytest.raises(SetpointOutOfRangeError):
        rules.setpoint(Mode.HEAT, high + 1, unit="F")


def test_fahrenheit_bounds_for_specific_values() -> None:
    rules = SetpointRules(BASEBOARD, setpoint_min=18, setpoint_max=21.5)
    assert rules.setpoint(Mode.HEAT, 71, unit="F").celsius == 21.5
    assert rules.setpoint(Mode.HEAT, 64, unit="F").celsius == 18.0
    assert SetpointRules().fahrenheit_bounds(Mode.HEAT) == (None, None)
    assert SetpointRules(HVAC).fahrenheit_bounds(SetpointKind.AUTO_COOL) == (53, 90)


@pytest.mark.parametrize(
    ("value", "step", "expected"),
    [
        (20.25, 0.5, 20.5),
        (22.75, 0.5, 23.0),
        (20.24, 0.5, 20.0),
        (-0.25, 0.5, 0.0),
        (-0.26, 0.5, -0.5),
        (21.5, 1.0, 22.0),
        (20.5, 1.0, 21.0),
        (0.15, 0.1, 0.2),
        (1.05, 0.1, 1.1),
        (7.3, 0.5, 7.5),
    ],
)
def test_rounding_is_anchored_at_zero_and_half_up(
    value: float, step: float, expected: float
) -> None:
    assert round_to_step(value, step) == expected


def test_rounding_is_anchored_at_zero_not_the_range_minimum() -> None:
    """A range starting at 5.25 does not shift the grid."""
    rules = SetpointRules(replace(BASE, heat_setpoint_range=(5.25, 30.0, 0.5)))
    assert rules.setpoint(Mode.HEAT, 20.6).celsius == 20.5


@pytest.mark.parametrize(
    ("homekit", "caps", "expected_step"),
    [
        (None, None, DEFAULT_STEP),
        ((5.0, 30.0, None), None, DEFAULT_STEP),
        ((5.0, 30.0, 0), None, DEFAULT_STEP),
        (None, replace(BASE, heat_setpoint_range=None), DEFAULT_STEP),
        ((5.0, 30.0, 1.0), BASEBOARD, 1.0),
        ((5.0, 30.0, None), BASEBOARD, 0.5),
    ],
)
def test_missing_step_defaults_to_half_a_degree(
    homekit: Any, caps: Capabilities | None, expected_step: float
) -> None:
    rules = SetpointRules(caps, homekit)
    assert rules.range_for(Mode.HEAT).step == expected_step
    # Without a cloud cool range, cooling falls back to the HomeKit range.
    homekit_step = homekit[2] if homekit and homekit[2] else DEFAULT_STEP
    assert rules.range_for(Mode.COOL).step == homekit_step
    assert rules.offset_range.step == DEFAULT_STEP
    assert rules.setpoint(Mode.HEAT, 20.3).celsius == (
        20.0 if expected_step == 1 else 20.5
    )


def test_local_only_thermostat_uses_the_homekit_range() -> None:
    rules = SetpointRules(homekit_range=(7.0, 28.0, 0.5))
    assert rules.range_for(Mode.HEAT) == SetpointRange(7.0, 28.0, 0.5)
    assert rules.setpoint(None, 21.3).celsius == 21.5
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.setpoint(Mode.HEAT, 28.3)
    assert (error.value.limit, error.value.bound, error.value.rounded) == (
        28.0,
        "max",
        28.5,
    )
    assert error.value.requested == 28.3
    # A partial HomeKit range checks the known bound, and SAFE_MAXIMUM stands
    # in for the unknown one.
    partial = SetpointRules(homekit_range=(7.0, None, None))
    assert partial.setpoint(Mode.HEAT, 35).celsius == 35.0
    with pytest.raises(SetpointOutOfRangeError) as error:
        partial.setpoint(Mode.HEAT, 45)
    assert (error.value.limit, error.value.bound) == (35.0, "max")
    with pytest.raises(SetpointOutOfRangeError):
        partial.setpoint(Mode.HEAT, 6.5)


def test_heating_range_is_kept_and_matches_the_rules() -> None:
    assert heating_range((6.0, None, None), BASEBOARD, 10, 25) == (10, 25, 0.5)
    rules = SetpointRules(
        BASEBOARD, (6.0, None, None), setpoint_min=10, setpoint_max=25
    )
    assert rules.range_for(Mode.HEAT) == SetpointRange(10.0, 25.0, 0.5)


def test_owner_limits_narrow_heat_but_not_the_hardware_range() -> None:
    rules = SetpointRules(BASEBOARD, setpoint_min=16, setpoint_max=24)
    assert rules.range_for(Mode.HEAT) == SetpointRange(16.0, 24.0, 0.5)
    assert rules.hardware_heat_range == SetpointRange(5.0, 30.0, 0.5)
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.setpoint(Mode.HEAT, 25)
    assert (error.value.limit, error.value.bound) == (24.0, "max")
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.schedule_event(Mode.HEAT, 15.5)
    assert (error.value.limit, error.value.bound) == (16.0, "min")
    # New limits are validated against the hardware range, not the old limits.
    assert rules.limit(28, minimum=False).celsius == 28.0
    assert rules.limit(80, minimum=False, unit="F").celsius == 26.5
    with pytest.raises(SetpointOutOfRangeError):
        rules.limit(4.5, minimum=True)
    # Owner limits only narrow a range; they never widen the hardware one.
    loose = SetpointRules(BASEBOARD, setpoint_min=1, setpoint_max=40)
    assert loose.range_for(Mode.HEAT) == SetpointRange(5.0, 30.0, 0.5)


def test_owner_limits_without_any_advertised_range() -> None:
    rules = SetpointRules(setpoint_min=10, setpoint_max=20)
    assert rules.range_for(Mode.HEAT) == SetpointRange(10, 20, 0.5)
    assert rules.hardware_heat_range == SetpointRange(None, None, 0.5)


@pytest.mark.parametrize(
    ("low", "high", "unit", "expected"),
    [
        (19, 23, "C", (19.0, 23.0)),
        (19.8, 22.4, "C", (20.0, 22.0)),
        (68, 72, "F", (20.0, 22.0)),
    ],
)
def test_heat_cool_accepts_the_minimum_gap(
    low: float, high: float, unit: Any, expected: tuple[float, float]
) -> None:
    heat, cool = SetpointRules(HVAC).heat_cool(low, high, unit=unit)
    assert (heat.celsius, cool.celsius) == expected


@pytest.mark.parametrize(
    ("low", "high", "unit", "rounded"),
    [
        (20, 21, "C", (20.0, 21.0)),
        (20.3, 21.9, "C", (20.5, 22.0)),
        (21.0, 20.0, "C", (21.0, 20.0)),
        (70, 72, "F", (21.0, 22.0)),
    ],
)
def test_heat_cool_rejects_a_small_gap_without_adjusting(
    low: float, high: float, unit: Any, rounded: tuple[float, float]
) -> None:
    rules = SetpointRules(HVAC)
    with pytest.raises(SetpointGapTooSmallError) as error:
        rules.heat_cool(low, high, unit=unit, strict=False)
    assert (error.value.low, error.value.high) == rounded
    assert error.value.required == 2.0
    assert error.value.gap == round(rounded[1] - rounded[0], 4)


def test_heat_cool_without_a_known_delta_requires_order() -> None:
    rules = SetpointRules()
    heat, cool = rules.heat_cool(21, 21)
    assert heat == cool
    with pytest.raises(SetpointGapTooSmallError):
        rules.heat_cool(21.5, 21)


def test_strict_raises_and_non_strict_clamps_with_details() -> None:
    rules = SetpointRules(BASEBOARD, setpoint_max=24)
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.setpoint(Mode.HEAT, 80, unit="F")
    assert error.value.requested == pytest.approx(26.6667, abs=1e-4)
    assert (error.value.rounded, error.value.limit, error.value.bound) == (
        26.5,
        24.0,
        "max",
    )
    assert isinstance(error.value, ValueError)

    clamped = rules.setpoint(Mode.HEAT, 80, unit="F", strict=False)
    assert clamped.celsius == 24.0
    assert clamped.clamped
    assert clamped.clamp == SetpointClamp(
        requested=error.value.requested, limit=24.0, bound="max"
    )
    low = rules.setpoint(Mode.HEAT, 2, strict=False)
    assert (low.celsius, low.clamp and low.clamp.bound) == (5.0, "min")
    inside = rules.setpoint(Mode.HEAT, 21.2, strict=False)
    assert (inside.celsius, inside.clamped, inside.clamp) == (21.0, False, None)
    # Clamp details are informational and do not affect equality.
    assert clamped == rules.setpoint(Mode.HEAT, 24)


def test_clamping_to_an_off_grid_limit_stays_on_the_step_grid() -> None:
    rules = SetpointRules(homekit_range=(4.4, 29.8, 0.5))
    high = rules.setpoint(Mode.HEAT, 35, strict=False)
    low = rules.setpoint(Mode.HEAT, 0, strict=False)
    assert (high.celsius, high.clamp and high.clamp.limit) == (29.5, 29.8)
    assert (low.celsius, low.clamp and low.clamp.limit) == (4.5, 4.4)
    assert isinstance(high.celsius, float)
    assert isinstance(
        SetpointRules(homekit_range=(5, 30, 1)).range_for(None).step, float
    )


def test_rounding_inside_the_range_is_not_out_of_range() -> None:
    rules = SetpointRules(BASEBOARD)
    assert rules.setpoint(Mode.HEAT, 30.2).celsius == 30.0
    with pytest.raises(SetpointOutOfRangeError):
        rules.setpoint(Mode.HEAT, 30.25)


def test_central_hvac_uses_per_mode_ranges_and_steps() -> None:
    rules = SetpointRules(HVAC, setpoint_max=26)
    table = [
        (Mode.HEAT, 21.3, 21.5),
        (Mode.EMERGENCY_HEAT, 21.3, 21.5),
        (Mode.COOL, 21.3, 21.0),
        (Mode.COOL, 21.5, 22.0),
        (Mode.OFF, 21.3, 21.5),
        (None, 21.3, 21.5),
    ]
    for mode, value, expected in table:
        assert rules.setpoint(mode, value).celsius == expected, mode
    assert rules.range_for(SetpointKind.HEAT) == SetpointRange(5.0, 26.0, 0.5)
    assert rules.range_for(Mode.COOL) == SetpointRange(15.0, 35.0, 1.0)
    assert rules.range_for(SetpointKind.AUTO_HEAT) == SetpointRange(10.0, 30.0, 0.5)
    assert rules.range_for(SetpointKind.AUTO_COOL) == SetpointRange(12.0, 32.0, 1.0)
    # The owner's heat limit does not apply to cooling.
    assert rules.setpoint(Mode.COOL, 30).celsius == 30.0
    heat, cool = rules.heat_cool(20.3, 24.4)
    assert (heat.celsius, cool.celsius) == (20.5, 24.0)
    with pytest.raises(SetpointOutOfRangeError):
        rules.heat_cool(9.5, 24)
    # A Central HVAC has no single target in heat/cool mode, only the pair.
    hvac = SetpointRules(HVAC, family=ModelFamily.CENTRAL_HVAC)
    for call in (
        lambda: hvac.setpoint(Mode.AUTO, 21.3),
        lambda: hvac.range_for(Mode.AUTO),
        lambda: hvac.fahrenheit_bounds(Mode.AUTO),
    ):
        with pytest.raises(SetpointModeError, match="low, high or both"):
            call()


@pytest.mark.parametrize(
    ("delta", "unit", "expected"),
    [
        (1.24, "C", 1.2),
        (1.25, "C", 1.3),
        (-1.25, "C", -1.2),
        (2, "F", 1.1),
        (-3, "F", -1.7),
    ],
)
def test_offsets_round_to_the_offset_step(
    delta: float, unit: Any, expected: float
) -> None:
    offset = SetpointRules(HVAC).offset(delta, unit=unit)
    assert isinstance(offset, TemperatureOffset)
    assert offset.celsius == expected


def test_offsets_are_range_checked() -> None:
    rules = SetpointRules(HVAC)
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.offset(-10, unit="F")
    assert (error.value.limit, error.value.bound) == (-5.0, "min")
    assert rules.offset(12, strict=False).celsius == 5.0
    assert SetpointRules().offset(0.7).celsius == 0.5


@pytest.mark.parametrize(
    ("delta", "fahrenheit"),
    [
        (0.0, 0),
        (0.5, 1),
        (1.0, 2),
        (1.5, 3),
        (2.5, 5),
        (-0.5, -1),
        (-1.0, -2),
        (-2.5, -4),
    ],
)
def test_fahrenheit_offset(delta: float, fahrenheit: int) -> None:
    assert fahrenheit_offset(delta) == fahrenheit


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "21", True, None])
def test_rules_reject_non_numbers(bad: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        SetpointRules().setpoint(Mode.HEAT, bad)


def test_rules_reject_unknown_units() -> None:
    with pytest.raises(ValueError, match="unit"):
        SetpointRules().setpoint(Mode.HEAT, 21, unit="K")  # type: ignore[arg-type]


def test_setpoint_cannot_be_constructed_directly() -> None:
    with pytest.raises(TypeError, match="SetpointRules"):
        Setpoint(21.5)
    with pytest.raises(TypeError, match="SetpointRules"):
        TemperatureOffset(1.0)
    with pytest.raises(TypeError, match="SetpointRules"):
        Setpoint(21.5, _token=object())
    setpoint = SetpointRules().setpoint(Mode.HEAT, 21.5)
    with pytest.raises(AttributeError):
        setpoint._celsius = 30  # type: ignore[misc]
    with pytest.raises(AttributeError):
        del setpoint._celsius
    with pytest.raises(TypeError):
        pickle.dumps(setpoint)
    assert copy.copy(setpoint) is setpoint
    assert copy.deepcopy(setpoint) is setpoint
    assert repr(setpoint) == "Setpoint(21.5)"
    assert hash(setpoint) == hash(SetpointRules().setpoint(Mode.COOL, 21.5))
    offset = SetpointRules().offset(5)
    assert offset != SetpointRules().setpoint(Mode.HEAT, 5)
    assert setpoint != 21.5


PLAIN = 21.5


@pytest.mark.parametrize(
    "build",
    [
        lambda: temperature_changes(Mode.HEAT, PLAIN),
        lambda: auto_temperature_changes(PLAIN, PLAIN),
        lambda: make_state(Mode.HEAT, PLAIN),
        lambda: setpoint_limit_changes(PLAIN, minimum=True),
        lambda: temperature_offset_changes(1.0),
        lambda: temperature_offset_changes(SetpointRules().setpoint(Mode.HEAT, 5)),
        lambda: setpoint_limit_changes(SetpointRules().offset(1), minimum=False),
        lambda: climate_command(ModelFamily.BASEBOARD_V2, Mode.HEAT, temperature=PLAIN),
        lambda: climate_command(
            ModelFamily.CENTRAL_HVAC, Mode.AUTO, low=PLAIN, high=PLAIN + 3
        ),
        lambda: build_schedule_events(
            [{"days": ["mon"], "time": time(7), "temperature": PLAIN}]
        ),
        lambda: ScheduleEventSpec(("mon",), time(7), Mode.HEAT, PLAIN, "key"),
        lambda: HoldCommand(Mode.HEAT, PLAIN),
        lambda: resolve_hold(
            requested_mode=Mode.HEAT,
            current_mode=None,
            requested_temperature=PLAIN,
            current_temperature=None,
        ),
    ],
)
def test_builders_reject_plain_numbers(build: Any) -> None:
    with pytest.raises(TypeError, match="SetpointRules"):
        build()


async def test_apply_hold_and_client_reject_plain_numbers() -> None:
    command = HoldCommand(Mode.HEAT, None)
    object.__setattr__(command, "temperature", PLAIN)

    async def noop(*_args: Any) -> None:
        return None

    with pytest.raises(TypeError, match="SetpointRules"):
        await async_apply_hold(
            command,
            set_hold=noop,
            current_mode=lambda: Mode.HEAT,
            current_temperature=lambda: 20,
            set_mode=noop,
            set_temperature=noop,
        )
    client = MysaClient.__new__(MysaClient)
    with pytest.raises(TypeError, match="SetpointRules"):
        await client.set_temperature("device", Mode.HEAT, PLAIN)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="SetpointRules"):
        await client.set_temperature_offset("device", 1.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="SetpointRules"):
        await client.set_setpoint_limit("device", 7.0, minimum=True)  # type: ignore[arg-type]


async def test_apply_hold_sends_the_normalised_setpoint() -> None:
    rules = SetpointRules()
    command = resolve_hold(
        requested_mode=None,
        current_mode=Mode.HEAT,
        requested_temperature=rules.setpoint(Mode.HEAT, 22.78),
        current_temperature=rules.setpoint(Mode.HEAT, 20, strict=False),
    )
    assert command.state == {"modes": {"mode": 4}, "targetHeat": {"setpoint": 23.0}}
    sent: list[Any] = []

    async def record(*args: Any) -> None:
        sent.append(args)

    await async_apply_hold(
        command,
        set_hold=record,
        current_mode=lambda: Mode.HEAT,
        current_temperature=lambda: 20.0,
        set_mode=record,
        set_temperature=record,
    )
    assert sent[-1] == (rules.setpoint(Mode.HEAT, 23),)


def test_builders_encode_typed_values() -> None:
    rules = SetpointRules(HVAC)
    low, high = rules.heat_cool(20, 24)
    assert climate_command(
        ModelFamily.CENTRAL_HVAC, Mode.AUTO, low=low, high=high
    ).payload == {"targetAuto": {"heatSetpoint": 20.0, "coolSetpoint": 24.0}}
    assert temperature_changes(Mode.COOL, rules.setpoint(Mode.COOL, 23.4)) == {
        "targetCool": {"setpoint": 23.0}
    }
    assert setpoint_limit_changes(
        rules.limit(64, minimum=True, unit="F"), minimum=True
    ) == {"targetHeat": {"lockoutMin": 18.0}}
    assert temperature_offset_changes(rules.offset(-1.04)) == {
        "tracking": {"ambientOffset": -1.0}
    }
    events = build_schedule_events(
        [
            {
                "days": ["mon"],
                "time": time(7),
                "temperature": rules.schedule_event(Mode.HEAT, 68, unit="F"),
            },
            {"days": ["tue"], "time": time(8), "hvac_mode": "off"},
        ]
    )
    assert events[0]["state"] == {
        "modes": {"mode": 4},
        "targetHeat": {"setpoint": 20.0},
    }
    assert events[1]["state"] == {"modes": {"mode": 0}}


# ---- Errors name the failed range and side --------------------------------


def test_out_of_range_errors_carry_the_violated_range_and_kind() -> None:
    rules = SetpointRules(HVAC, setpoint_max=26)
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.setpoint(Mode.COOL, 36)
    assert error.value.limits == SetpointRange(15.0, 35.0, 1.0)
    assert (error.value.kind, error.value.side, error.value.delta) == (
        SetpointKind.COOL,
        None,
        False,
    )
    assert error.value.fahrenheit_limit == 95
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.setpoint(Mode.HEAT, 80, unit="F")
    assert error.value.limits == SetpointRange(5.0, 26.0, 0.5)
    assert error.value.kind is SetpointKind.HEAT
    assert error.value.fahrenheit_limit == 79
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.limit(4, minimum=True)
    assert error.value.limits == rules.hardware_heat_range
    assert error.value.kind is None
    assert error.value.fahrenheit_limit == 41
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.offset(-12, unit="F")
    assert error.value.delta is True
    assert error.value.limits == rules.offset_range
    assert (error.value.kind, error.value.fahrenheit_limit) == (None, -9)
    # An error built without a range (older callers) has no °F limit.
    assert (
        SetpointOutOfRangeError(
            requested=1, rounded=1, limit=5, bound="min"
        ).fahrenheit_limit
        is None
    )


def test_heat_cool_errors_name_the_side_that_failed() -> None:
    narrow = SetpointRules(
        replace(
            HVAC, auto_heat_range=(10.0, 30.0, 0.5), auto_cool_range=(12.0, 25.0, 1.0)
        )
    )
    # low == high, the heat side in range and the cool side above its maximum:
    # the error is the cool side's, even though its requested value equals low.
    with pytest.raises(SetpointOutOfRangeError) as error:
        narrow.heat_cool(28, 28)
    assert (error.value.side, error.value.kind, error.value.bound) == (
        "cool",
        SetpointKind.AUTO_COOL,
        "max",
    )
    assert error.value.limits == SetpointRange(12.0, 25.0, 1.0)
    assert error.value.fahrenheit_limit == 77
    assert str(error.value).startswith("Cool setpoint")
    with pytest.raises(SetpointOutOfRangeError) as error:
        narrow.heat_cool(8, 20)
    assert (error.value.side, error.value.kind, error.value.limit) == (
        "heat",
        SetpointKind.AUTO_HEAT,
        10.0,
    )
    with pytest.raises(SetpointOutOfRangeError) as error:
        narrow.heat_cool(31, 31)
    assert error.value.side == "heat"


# ---- Whole-°F bounds for any range, and envelopes --------------------------


def test_fahrenheit_bounds_accept_any_range_and_deltas() -> None:
    rules = SetpointRules(HVAC, setpoint_min=18, setpoint_max=21.5)
    assert rules.fahrenheit_bounds(rules.hardware_heat_range) == (41, 86)
    assert rules.fahrenheit_bounds(Mode.HEAT) == (64, 71)
    assert rules.fahrenheit_bounds(SetpointRange(15.0, 35.0, 1.0)) == (
        rules.fahrenheit_bounds(Mode.COOL)
    )
    assert rules.offset_fahrenheit_bounds() == (-9, 9)
    assert rules.fahrenheit_bounds(rules.offset_range, delta=True) == (-9, 9)
    assert fahrenheit_bounds(SetpointRange(-5.0, 5.0, 0.1), delta=True) == (-9, 9)
    assert fahrenheit_bounds(SetpointRange(None, 30.0, 0.5)) == (None, 86)
    for whole in (-9, 9):
        assert rules.offset(whole, unit="F")
    for whole in (-10, 10):
        with pytest.raises(SetpointOutOfRangeError):
            rules.offset(whole, unit="F")


def test_envelope_covers_every_listed_kind() -> None:
    rules = SetpointRules(HVAC)
    assert rules.envelope(SetpointKind) == SetpointRange(5.0, 35.0, 0.5)
    assert rules.envelope([Mode.COOL]) == SetpointRange(15.0, 35.0, 1.0)
    assert rules.fahrenheit_envelope(SetpointKind) == (41, 95)
    assert rules.fahrenheit_envelope([SetpointKind.AUTO_COOL]) == (53, 90)
    with pytest.raises(ValueError, match="at least one"):
        rules.envelope([])
    with pytest.raises(ValueError, match="at least one"):
        rules.fahrenheit_envelope([])


def test_envelope_defaults_unknown_bounds_to_five_and_thirty() -> None:
    assert (DEFAULT_MINIMUM, DEFAULT_MAXIMUM) == (5.0, 30.0)
    unknown = SetpointRules()
    assert unknown.envelope([Mode.HEAT]) == SetpointRange(5.0, 30.0, 0.5)
    assert unknown.fahrenheit_envelope([Mode.HEAT]) == (41, 86)
    partial = SetpointRules(homekit_range=(7.0, None, None))
    assert partial.envelope([Mode.HEAT]) == SetpointRange(7.0, 30.0, 0.5)
    assert partial.fahrenheit_envelope([Mode.HEAT]) == (45, 86)
    # Validation still invents no bound; only the display envelope does.
    assert partial.setpoint(Mode.HEAT, 35).celsius == 35.0


def test_envelope_always_has_two_ends() -> None:
    rules = SetpointRules(setpoint_min=20, setpoint_max=20)
    assert rules.envelope([Mode.HEAT]) == SetpointRange(20.0, 20.5, 0.5)
    assert rules.fahrenheit_envelope([Mode.HEAT]) == (68, 69)


# ---- One rule for owner limits ----------------------------------------------


def test_limits_must_stay_strictly_ordered() -> None:
    rules = SetpointRules(BASEBOARD, setpoint_min=16, setpoint_max=24)
    assert rules.limit(23.5, minimum=True).celsius == 23.5
    assert rules.limit(16.5, minimum=False).celsius == 16.5
    with pytest.raises(SetpointLimitOrderError) as error:
        rules.limit(24, minimum=True)
    assert (error.value.value, error.value.other, error.value.minimum) == (
        24.0,
        24.0,
        True,
    )
    assert isinstance(error.value, SetpointError)
    with pytest.raises(SetpointLimitOrderError) as error:
        rules.limit(61, minimum=False, unit="F")  # 16.1 °C rounds to 16.0
    assert (error.value.value, error.value.other, error.value.minimum) == (
        16.0,
        16.0,
        False,
    )
    # A clamped value is checked too: order is never adjusted.
    with pytest.raises(SetpointLimitOrderError):
        rules.limit(40, minimum=True, strict=False)
    # The range is checked before the order.
    with pytest.raises(SetpointOutOfRangeError):
        rules.limit(31, minimum=True)
    # Without the other limit there is nothing to order against.
    assert SetpointRules(BASEBOARD).limit(29, minimum=True).celsius == 29.0
    assert limits_ordered(16, 24)
    assert not limits_ordered(20, 20)
    assert not limits_ordered(24, 16)


# ---- One rule for a single setpoint in heat/cool mode -----------------------


def test_single_setpoint_in_auto_is_never_the_heat_target() -> None:
    """0.3.0 wrote targetHeat; Mysa's app 4.21.1 never does (0.4.0)."""
    rules = SetpointRules(HVAC)
    with pytest.raises(SetpointModeError):
        kind_for_mode(Mode.AUTO)
    with pytest.raises(SetpointModeError):
        rules.setpoint(Mode.AUTO, 21.3)
    heat = rules.setpoint(Mode.HEAT, 21.3)
    with pytest.raises(SetpointModeError):
        temperature_changes(Mode.AUTO, heat)
    state = DeviceState.from_api(
        "device",
        {
            "modes": {"reported": {"mode": int(Mode.AUTO)}},
            "targetHeat": {"reported": {"setpoint": 21.5}},
            "targetCool": {"reported": {"setpoint": 25.0}},
        },
    )
    assert state.setpoint_for(state.mode) is None
    assert state.setpoint_for(Mode.HEAT) == 21.5
    assert state.setpoint_for(Mode.COOL) == 25.0


# ---- Mini-Split AC ranges from the settings record --------------------------

AC_RECORD = DeviceRecord.from_api(
    "ac",
    {
        "Model": "AC-V1-0",
        "SupportedModes": {
            "3": {"temperatures": list(range(17, 31))},
            "4": {"temperatures": [str(t) for t in range(16, 31)] + [72, "x"]},
        },
        "TempRange": [16, 30],
    },
)


def test_ac_record_exposes_per_mode_temperature_ranges() -> None:
    assert AC_RECORD.ac_temperature_range(Mode.COOL) == (16.0, 30.0, 1.0)
    assert AC_RECORD.ac_temperature_range(Mode.HEAT) == (17.0, 30.0, 1.0)
    # A mode without a list uses TempRange, whatever its order.
    assert AC_RECORD.ac_temperature_range(Mode.DRY) == (16.0, 30.0, 1.0)
    reversed_range = DeviceRecord.from_api(
        "ac", {"Model": "AC-V1-0", "TempRange": [31, 18]}
    )
    assert reversed_range.ac_temperature_range(Mode.COOL) == (18.0, 31.0, 1.0)
    half = DeviceRecord.from_api(
        "ac",
        {"Model": "AC-V1-0", "SupportedModes": {"4": {"temperatures": [20, 20.5, 22]}}},
    )
    assert half.ac_temperature_range(Mode.COOL) == (20.0, 22.0, 0.5)
    single = DeviceRecord.from_api(
        "ac", {"Model": "AC-V1-0", "SupportedModes": {"4": {"temperatures": [24]}}}
    )
    assert single.ac_temperature_range(Mode.COOL) == (24.0, 24.0, 1.0)
    empty = DeviceRecord.from_api("ac", {"Model": "AC-V1-0", "TempRange": [16]})
    assert empty.ac_temperature_range(Mode.COOL) is None
    assert empty.ac_temperature_range(Mode.EMERGENCY_HEAT) is None


def test_ac_without_capability_ranges_uses_the_record() -> None:
    """The Mini-Split AC keeps its 16-30 °C cool range (a Phase 2 regression)."""
    rules = SetpointRules(None, None, record=AC_RECORD)
    assert rules.range_for(Mode.COOL) == SetpointRange(16.0, 30.0, 1.0)
    assert rules.range_for(Mode.HEAT) == SetpointRange(17.0, 30.0, 1.0)
    assert rules.setpoint(Mode.COOL, 22.4).celsius == 22.0
    assert rules.setpoint(Mode.COOL, 73, unit="F").celsius == 23.0
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.setpoint(Mode.COOL, 31)
    assert (error.value.kind, error.value.limit) == (SetpointKind.COOL, 30.0)
    assert rules.fahrenheit_bounds(Mode.COOL) == (60, 86)
    # Capabilities without ranges (the AC's are empty) still defer to it.
    assert SetpointRules(BASE, record=AC_RECORD).range_for(Mode.COOL) == (
        SetpointRange(16.0, 30.0, 1.0)
    )
    # Advertised capability ranges win over the record.
    assert SetpointRules(HVAC, record=AC_RECORD).range_for(Mode.COOL) == (
        SetpointRange(15.0, 35.0, 1.0)
    )


def test_cool_range_falls_back_to_homekit_and_ignores_other_records() -> None:
    local = SetpointRules(homekit_range=(10.0, 32.0, 0.5))
    assert local.range_for(Mode.COOL) == SetpointRange(10.0, 32.0, 0.5)
    baseboard_record = DeviceRecord.from_api(
        "bb", {"Model": "BB-V2-0", "TempRange": [16, 30]}
    )
    rules = SetpointRules(BASEBOARD, record=baseboard_record)
    assert rules.range_for(Mode.COOL) == SetpointRange(None, None, DEFAULT_STEP)
    assert rules.range_for(Mode.HEAT) == SetpointRange(5.0, 30.0, 0.5)
    # An AC record with no ranges leaves the HomeKit fallback in place.
    bare = DeviceRecord.from_api("ac", {"Model": "AC-V1-0"})
    assert SetpointRules(homekit_range=(10.0, 32.0, 0.5), record=bare).range_for(
        Mode.COOL
    ) == SetpointRange(10.0, 32.0, 0.5)


# ---- Holds and schedules normalise raw temperatures -------------------------


def test_resolve_hold_normalises_raw_temperatures() -> None:
    rules = SetpointRules(BASEBOARD, setpoint_max=24)
    command = resolve_hold(
        requested_mode=None,
        current_mode=Mode.HEAT,
        requested_temperature=73,
        current_temperature=None,
        rules=rules,
        unit="F",
    )
    assert command.temperature == rules.setpoint(Mode.HEAT, 23)
    assert command.state == {"modes": {"mode": 4}, "targetHeat": {"setpoint": 23.0}}
    with pytest.raises(SetpointOutOfRangeError) as error:
        resolve_hold(
            requested_mode=Mode.HEAT,
            current_mode=None,
            requested_temperature=80,
            current_temperature=None,
            rules=rules,
            unit="F",
        )
    assert error.value.kind is SetpointKind.HEAT
    # The current target (°C) is re-sent, so it is clamped rather than refused.
    current = resolve_hold(
        requested_mode=None,
        current_mode=None,
        requested_temperature=None,
        current_temperature=25.3,
        rules=rules,
        unit="F",
    )
    assert current.mode is Mode.HEAT
    assert current.temperature is not None
    assert (current.temperature.celsius, current.temperature.clamped) == (24.0, True)
    # OFF carries nothing, so its temperature is not even checked.
    off = resolve_hold(
        requested_mode=Mode.OFF,
        current_mode=Mode.HEAT,
        requested_temperature=200,
        current_temperature=21,
        rules=rules,
    )
    assert (off.mode, off.temperature) == (Mode.OFF, None)
    # A typed Setpoint is still used as given.
    typed = resolve_hold(
        requested_mode=Mode.HEAT,
        current_mode=None,
        requested_temperature=rules.setpoint(Mode.HEAT, 20),
        current_temperature=None,
    )
    assert typed.temperature == rules.setpoint(Mode.HEAT, 20)


def test_resolve_hold_refuses_a_temperature_in_auto() -> None:
    rules = SetpointRules(HVAC)
    with pytest.raises(SetpointModeError) as error:
        resolve_hold(
            requested_mode=Mode.AUTO,
            current_mode=None,
            requested_temperature=21,
            current_temperature=None,
            rules=rules,
        )
    assert error.value.mode is Mode.AUTO
    assert isinstance(error.value, SetpointError)
    with pytest.raises(SetpointModeError):
        resolve_hold(
            requested_mode=None,
            current_mode=Mode.AUTO,
            requested_temperature=rules.setpoint(Mode.HEAT, 21),
            current_temperature=None,
        )
    # The current target is simply not carried by a heat/cool hold.
    command = resolve_hold(
        requested_mode=Mode.AUTO,
        current_mode=Mode.HEAT,
        requested_temperature=None,
        current_temperature=21,
        rules=rules,
    )
    assert (command.mode, command.temperature) == (Mode.AUTO, None)
    with pytest.raises(TypeError, match="SetpointRules"):
        resolve_hold(
            requested_mode=Mode.HEAT,
            current_mode=None,
            requested_temperature=None,
            current_temperature=21.0,
        )


def test_build_schedule_events_normalises_each_event() -> None:
    rules = SetpointRules(HVAC, setpoint_max=24)
    events = build_schedule_events(
        [
            {"days": ["mon"], "time": time(7), "temperature": 68},
            {"days": ["tue"], "time": time(8), "hvac_mode": "cool", "temperature": 73},
            {"days": ["wed"], "time": time(9), "hvac_mode": "off", "temperature": 99},
            {"days": ["thu"], "time": time(9), "hvac_mode": "auto"},
            {
                "days": ["fri"],
                "time": time(9),
                "temperature": rules.schedule_event(Mode.HEAT, 21),
            },
        ],
        rules=rules,
        unit="F",
    )
    assert [event["state"] for event in events] == [
        {"modes": {"mode": 4}, "targetHeat": {"setpoint": 20.0}},
        {"modes": {"mode": 3}, "targetCool": {"setpoint": 23.0}},
        {"modes": {"mode": 0}},
        {"modes": {"mode": int(Mode.AUTO)}},
        {"modes": {"mode": 4}, "targetHeat": {"setpoint": 21.0}},
    ]
    with pytest.raises(SetpointOutOfRangeError) as error:
        build_schedule_events(
            [{"days": ["mon"], "time": time(7), "temperature": 80}],
            rules=rules,
            unit="F",
        )
    assert (error.value.kind, error.value.limit) == (SetpointKind.HEAT, 24.0)
    with pytest.raises(SetpointModeError):
        build_schedule_events(
            [
                {
                    "days": ["mon"],
                    "time": time(7),
                    "hvac_mode": "auto",
                    "temperature": 21,
                }
            ],
            rules=rules,
        )
