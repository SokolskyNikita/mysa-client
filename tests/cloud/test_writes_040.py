"""0.4.0 writes: auto-mode targets, schedule/hold modes and cooling limits.

The encodings follow Mysa's Android app 4.21.1; none of them has been
verified on a device.

New names are read from their module inside each test, so on a release
without them each test fails on its own rather than the whole file failing to
import.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import time
from typing import Any

import pytest

from mysa_client.cloud import (
    Capabilities,
    DeviceRecord,
    DeviceState,
    Mode,
    ModelFamily,
    commands,
    holds,
    setpoints as sp,
)
from mysa_client.cloud.device_commands import climate_command
from mysa_client.cloud.schedule_workflow import build_schedule_events
from mysa_client.cloud.setpoints import (
    SetpointGapTooSmallError,
    SetpointKind,
    SetpointLimitOrderError,
    SetpointModeError,
    SetpointOutOfRangeError,
    SetpointRange,
    SetpointRules,
)
from mysa_client.cloud.validation import validate_cloud_command

BASE = Capabilities.from_api({})
BASEBOARD = replace(BASE, heat_setpoint_range=(5.0, 30.0, 0.5), model="BB-V2-0-L")
HVAC = replace(
    BASE,
    model="ST-V1-0",
    modes=(Mode.OFF, Mode.HEAT, Mode.COOL, Mode.AUTO),
    heat_setpoint_range=(5.0, 30.0, 0.5),
    cool_setpoint_range=(15.0, 35.0, 1.0),
    auto_heat_range=(10.0, 28.0, 0.5),
    auto_cool_range=(18.0, 35.0, 0.5),
    min_setpoint_delta=2.0,
)
AC_RECORD = DeviceRecord.from_api(
    "ac",
    {
        "Model": "AC-V1-0",
        "SupportedModes": {
            "2": {"temperatures": list(range(18, 29))},
            "3": {"temperatures": list(range(17, 31))},
            "4": {"temperatures": list(range(16, 31))},
        },
        "TempRange": [16, 30],
    },
)


def _hvac_state(**sections: Any) -> DeviceState:
    data: dict[str, Any] = {
        "modes": {"reported": {"mode": int(Mode.AUTO)}},
        "targetAuto": {"reported": {"heatSetpoint": 20.0, "coolSetpoint": 24.0}},
    }
    data.update(sections)
    return DeviceState.from_api("hvac", data)


def _no_pending(_name: str, value: Any) -> Any:
    return value


# ---- 1. A single temperature in auto mode ------------------------------------


def test_ac_auto_temperature_is_target_auto_setpoint() -> None:
    rules = SetpointRules(record=AC_RECORD)
    assert rules.family is ModelFamily.AC
    assert sp.kind_for_mode(Mode.AUTO, ModelFamily.AC) is SetpointKind.AUTO
    assert rules.range_for(Mode.AUTO) == SetpointRange(18.0, 28.0, 1.0)
    setpoint = rules.setpoint(Mode.AUTO, 22.4)
    assert setpoint.celsius == 22.0
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.setpoint(Mode.AUTO, 29)
    assert error.value.kind is SetpointKind.AUTO
    command = climate_command(ModelFamily.AC, Mode.AUTO, temperature=setpoint)
    assert command.payload == {"targetAuto": {"setpoint": 22.0}}
    assert command.expected == {"auto_setpoint": 22.0}
    changes = commands.temperature_changes(Mode.AUTO, setpoint, family=ModelFamily.AC)
    assert changes == {"targetAuto": {"setpoint": 22.0}}
    with_mode = climate_command(
        ModelFamily.AC, Mode.COOL, mode=Mode.AUTO, temperature=setpoint
    )
    assert with_mode.payload == {
        "modes": {"mode": int(Mode.AUTO)},
        "targetAuto": {"setpoint": 22.0},
    }


def test_ac_auto_range_fallbacks() -> None:
    # No auto list: the record's TempRange, as for any other AC mode.
    record = DeviceRecord.from_api("ac", {"Model": "AC-V1-0", "TempRange": [17, 29]})
    assert SetpointRules(record=record).range_for(Mode.AUTO) == SetpointRange(
        17.0, 29.0, 1.0
    )
    # Nothing in the record: the HomeKit range, else the app's AC picker
    # default (AC_MIN_TEMP 16 to MAX_TEMP 30 °C in whole degrees).
    bare = DeviceRecord.from_api("ac", {"Model": "AC-V1-0"})
    assert SetpointRules(homekit_range=(16.0, 30.0, 0.5), record=bare).range_for(
        Mode.AUTO
    ) == SetpointRange(16.0, 30.0, 0.5)
    assert SetpointRules(record=bare).range_for(Mode.AUTO) == SetpointRange(
        16.0, 30.0, 1.0
    )
    clamped = SetpointRules(family=ModelFamily.AC).setpoint(Mode.AUTO, 31, strict=False)
    assert clamped.celsius == 30.0
    # Other families advertise no single auto target.
    assert SetpointRules(HVAC).range_for(SetpointKind.AUTO) == SetpointRange(
        None, None, 0.5
    )


@pytest.mark.parametrize(
    "rules",
    [SetpointRules(HVAC), SetpointRules(BASEBOARD), SetpointRules()],
    ids=["hvac", "baseboard", "unknown"],
)
def test_single_auto_temperature_is_refused_without_an_ac(
    rules: SetpointRules,
) -> None:
    with pytest.raises(SetpointModeError) as error:
        rules.setpoint(Mode.AUTO, 21)
    assert error.value.mode is Mode.AUTO
    with pytest.raises(SetpointModeError):
        sp.kind_for_mode(Mode.AUTO, rules.family)


def test_central_hvac_bare_temperature_in_auto_points_to_low_and_high() -> None:
    heat = SetpointRules(HVAC).setpoint(Mode.HEAT, 21)
    with pytest.raises(SetpointModeError, match=r"low.*high") as error:
        climate_command(ModelFamily.CENTRAL_HVAC, Mode.AUTO, temperature=heat)
    assert error.value.mode is Mode.AUTO
    with pytest.raises(SetpointModeError, match=r"low.*high"):
        SetpointRules(HVAC).setpoint(Mode.AUTO, 21)
    with pytest.raises(SetpointModeError):
        commands.temperature_changes(Mode.AUTO, heat)
    with pytest.raises(SetpointModeError):
        climate_command(ModelFamily.BASEBOARD_LITE, Mode.AUTO, temperature=heat)


def test_central_hvac_changes_one_auto_side() -> None:
    rules = SetpointRules(HVAC)
    heat = rules.heat_cool_side("heat", 21.3, other=24)
    assert heat.celsius == 21.5
    command = climate_command(ModelFamily.CENTRAL_HVAC, Mode.AUTO, low=heat)
    assert command.payload == {"targetAuto": {"heatSetpoint": 21.5}}
    assert command.expected == {"auto_heat_setpoint": 21.5}
    cool = rules.heat_cool_side("cool", 26, other=20)
    command = climate_command(ModelFamily.CENTRAL_HVAC, Mode.AUTO, high=cool)
    assert command.payload == {"targetAuto": {"coolSetpoint": 26.0}}
    # The app refuses a change that breaks the gap and never moves the other.
    with pytest.raises(SetpointGapTooSmallError) as gap:
        rules.heat_cool_side("heat", 23, other=24)
    assert (gap.value.low, gap.value.high, gap.value.required) == (23.0, 24.0, 2.0)
    with pytest.raises(SetpointGapTooSmallError):
        rules.heat_cool_side("cool", 21, other=20)
    # Each side keeps its own range, named on the error.
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.heat_cool_side("cool", 17, other=10)
    assert (error.value.side, error.value.kind) == ("cool", SetpointKind.AUTO_COOL)
    # The gap can't be checked without the other side.
    with pytest.raises(sp.SetpointGapUnknownError) as unknown:
        rules.heat_cool_side("heat", 21, other=None)
    assert unknown.value.side == "heat"
    with pytest.raises(ValueError, match="side"):
        rules.heat_cool_side("auto", 21, other=24)  # type: ignore[arg-type]


def test_heat_cool_side_reads_the_other_side_in_fahrenheit_too() -> None:
    rules = SetpointRules(HVAC)
    other = rules.heat_cool(20, 24)[1]
    assert rules.heat_cool_side("heat", 70, unit="F", other=other).celsius == 21.0


def test_climate_command_heat_modes_and_typed_refusals() -> None:
    rules = SetpointRules(BASEBOARD)
    target = rules.setpoint(Mode.HEAT, 21)
    for selected in (None, Mode.HEAT, Mode.EMERGENCY_HEAT, Mode.OFF):
        command = climate_command(
            ModelFamily.CENTRAL_HVAC, selected, temperature=target
        )
        assert command.payload == {"targetHeat": {"setpoint": 21.0}}, selected
    command = climate_command(None, None, mode=Mode.EMERGENCY_HEAT, temperature=target)
    assert command.payload == {
        "modes": {"mode": int(Mode.EMERGENCY_HEAT)},
        "targetHeat": {"setpoint": 21.0},
    }
    with pytest.raises(SetpointModeError):
        climate_command(ModelFamily.AC, Mode.FAN_ONLY, temperature=target)
    low, high = SetpointRules(HVAC).heat_cool(20, 24)
    for family, selected, kwargs in (
        (ModelFamily.AC, Mode.AUTO, {"low": low}),
        (ModelFamily.CENTRAL_HVAC, Mode.HEAT, {"low": low, "high": high}),
        (ModelFamily.CENTRAL_HVAC, None, {"high": high}),
        (ModelFamily.CENTRAL_HVAC, Mode.AUTO, {"low": low, "temperature": target}),
    ):
        with pytest.raises(SetpointModeError):
            climate_command(family, selected, **kwargs)


def test_setpoint_for_auto_reads_the_auto_section() -> None:
    ac = DeviceState.from_api(
        "ac",
        {
            "modes": {"reported": {"mode": int(Mode.AUTO)}},
            "targetHeat": {"reported": {"setpoint": 19.0}},
            "targetAuto": {"reported": {"setpoint": 22.0}},
        },
    )
    assert ac.auto_setpoint == 22.0
    assert ac.setpoint_for(Mode.AUTO) == 22.0
    assert ac.setpoint_for(Mode.HEAT) == 19.0
    hvac = _hvac_state(targetHeat={"reported": {"setpoint": 21.0}})
    # A Central HVAC's auto targets are the pair, never targetHeat.
    assert hvac.setpoint_for(Mode.AUTO) is None
    assert (hvac.auto_heat_setpoint, hvac.auto_cool_setpoint) == (20.0, 24.0)
    assert hvac.setpoint_for(Mode.EMERGENCY_HEAT) == 21.0
    # A write confirmed by reading the mode's single setpoint can match.
    command = climate_command(
        ModelFamily.AC,
        Mode.AUTO,
        temperature=SetpointRules(record=AC_RECORD).setpoint(Mode.AUTO, 22),
    )
    assert command.expected["auto_setpoint"] == ac.setpoint_for(Mode.AUTO)


def test_validation_checks_one_auto_side_against_the_state() -> None:
    state = _hvac_state()
    rules = SetpointRules(HVAC)
    heat = rules.heat_cool_side("heat", 21, other=state.auto_cool_setpoint)
    command = climate_command(ModelFamily.CENTRAL_HVAC, Mode.AUTO, low=heat)
    validate_cloud_command(
        ModelFamily.CENTRAL_HVAC, HVAC, state, command.expected, _no_pending
    )
    # The cool side moved since the rules ran: the gap is checked again.
    closer = _hvac_state(
        targetAuto={"reported": {"heatSetpoint": 20.0, "coolSetpoint": 22.5}}
    )
    with pytest.raises(ValueError, match="gap"):
        validate_cloud_command(
            ModelFamily.CENTRAL_HVAC, HVAC, closer, command.expected, _no_pending
        )
    # A pending write of the other side counts.
    with pytest.raises(ValueError, match="gap"):
        validate_cloud_command(
            ModelFamily.CENTRAL_HVAC,
            HVAC,
            state,
            command.expected,
            lambda name, value: 22.0 if name == "auto_cool_setpoint" else value,
        )
    unknown = DeviceState.from_api(
        "hvac", {"modes": {"reported": {"mode": int(Mode.AUTO)}}}
    )
    with pytest.raises(ValueError, match="gap"):
        validate_cloud_command(
            ModelFamily.CENTRAL_HVAC, HVAC, unknown, command.expected, _no_pending
        )


# ---- 2. Schedule events and holds accept any spelling of a mode -------------


@pytest.mark.parametrize(
    ("given", "mode"),
    [
        (Mode.HEAT, Mode.HEAT),
        (Mode.COOL, Mode.COOL),
        (Mode.OFF, Mode.OFF),
        ("HEAT", Mode.HEAT),
        ("heat", Mode.HEAT),
        ("Cool", Mode.COOL),
        ("off", Mode.OFF),
        ("heat_cool", Mode.AUTO),
        ("auto", Mode.AUTO),
        ("fan_only", Mode.FAN_ONLY),
        ("dry", Mode.DRY),
        ("EMERGENCY_HEAT", Mode.EMERGENCY_HEAT),
        ("emergencyHeat", Mode.EMERGENCY_HEAT),
        ("fanOnly", Mode.FAN_ONLY),
        (" heat ", Mode.HEAT),
    ],
)
def test_parse_mode_accepts_every_spelling(given: Any, mode: Mode) -> None:
    from mysa_client.cloud.modes import parse_mode

    assert parse_mode(given) is mode


@pytest.mark.parametrize("given", ["banana", "", "4", 4, 4.0, True, None, b"heat"])
def test_parse_mode_refuses_anything_else(given: Any) -> None:
    from mysa_client.cloud.modes import UnknownModeError, parse_mode

    with pytest.raises(UnknownModeError) as error:
        parse_mode(given)
    assert error.value.value == given
    assert isinstance(error.value, ValueError)


@pytest.mark.parametrize(
    ("hvac_mode", "section"),
    [
        (Mode.HEAT, "targetHeat"),
        (Mode.COOL, "targetCool"),
        ("cool", "targetCool"),
        ("COOL", "targetCool"),
        ("Heat", "targetHeat"),
    ],
)
def test_schedule_events_accept_mode_values_and_names(
    hvac_mode: Any, section: str
) -> None:
    rules = SetpointRules(HVAC)
    event = {"days": ["mon"], "time": time(7), "hvac_mode": hvac_mode}
    (encoded,) = build_schedule_events([{**event, "temperature": 21}], rules=rules)
    assert encoded["state"][section] == {"setpoint": 21.0}
    assert encoded["state"]["modes"]["mode"] in (int(Mode.HEAT), int(Mode.COOL))
    (default,) = build_schedule_events(
        [{"days": ["mon"], "time": time(7), "temperature": 21}],
        default_mode=hvac_mode,
        rules=rules,
    )
    assert default["state"][section] == {"setpoint": 21.0}


def test_schedule_events_refuse_unknown_modes_with_a_typed_error() -> None:
    from mysa_client.cloud.modes import UnknownModeError

    event = {"days": ["mon"], "time": time(7), "hvac_mode": "banana"}
    with pytest.raises(UnknownModeError, match="banana"):
        build_schedule_events([event], rules=SetpointRules())
    with pytest.raises(UnknownModeError):
        build_schedule_events(
            [{"days": ["mon"], "time": time(7)}], default_mode="banana"
        )
    # None means "no mode given": the default applies.
    (encoded,) = build_schedule_events(
        [{"days": ["mon"], "time": time(7), "hvac_mode": None}]
    )
    assert encoded["state"]["modes"] == {"mode": int(Mode.HEAT)}


def test_schedule_event_spec_normalises_its_mode() -> None:
    from mysa_client.cloud.modes import UnknownModeError

    target = SetpointRules().setpoint(Mode.COOL, 24)
    spec = commands.ScheduleEventSpec(("mon",), time(7), "cool", target, "key")  # type: ignore[arg-type]
    assert spec.mode is Mode.COOL
    (encoded,) = commands.encode_schedule_events([spec])
    assert encoded["state"] == {
        "modes": {"mode": int(Mode.COOL)},
        "targetCool": {"setpoint": 24.0},
    }
    with pytest.raises(UnknownModeError):
        commands.ScheduleEventSpec(("mon",), time(7), "banana", None, "key")  # type: ignore[arg-type]


def test_holds_accept_mode_names_and_refuse_unknown_ones() -> None:
    from mysa_client.cloud.modes import UnknownModeError

    rules = SetpointRules(HVAC)
    command = holds.resolve_hold(
        requested_mode="cool",
        current_mode=Mode.HEAT,
        requested_temperature=24,
        current_temperature=None,
        rules=rules,
    )
    assert command.mode is Mode.COOL
    assert command.state == {
        "modes": {"mode": int(Mode.COOL)},
        "targetCool": {"setpoint": 24.0},
    }
    current = holds.resolve_hold(
        requested_mode=None,
        current_mode="HEAT",
        requested_temperature=None,
        current_temperature=21,
        rules=rules,
    )
    assert current.mode is Mode.HEAT
    assert current.state["targetHeat"] == {"setpoint": 21.0}
    # A name used to drop the temperature silently and crash in make_state.
    hold = holds.HoldCommand(mode="heat", temperature=rules.setpoint(Mode.HEAT, 21))  # type: ignore[arg-type]
    assert hold.mode is Mode.HEAT
    assert hold.state["targetHeat"] == {"setpoint": 21.0}
    with pytest.raises(UnknownModeError):
        holds.HoldCommand(mode="banana", temperature=None)  # type: ignore[arg-type]
    with pytest.raises(UnknownModeError):
        holds.resolve_hold(
            requested_mode="banana",
            current_mode=None,
            requested_temperature=None,
            current_temperature=None,
        )


# ---- 3. Cooling setpoint limits ----------------------------------------------


def test_cool_limit_encoder_writes_target_cool() -> None:
    rules = SetpointRules(HVAC)
    low = rules.limit(18, minimum=True, kind=SetpointKind.COOL)
    high = rules.limit(30, minimum=False, kind=SetpointKind.COOL)
    assert commands.setpoint_limit_changes(
        low, minimum=True, kind=SetpointKind.COOL
    ) == {"targetCool": {"lockoutMin": 18.0}}
    assert commands.setpoint_limit_changes(
        high, minimum=False, kind=SetpointKind.COOL
    ) == {"targetCool": {"lockoutMax": 30.0}}
    # The default stays the heating limit.
    heat = rules.limit(7, minimum=True)
    assert commands.setpoint_limit_changes(heat, minimum=True) == {
        "targetHeat": {"lockoutMin": 7.0}
    }
    command = commands.state_command(
        commands.setpoint_limit_changes(low, minimum=True, kind=SetpointKind.COOL)
    )
    assert command.expected == {"cool_min": 18.0}


@pytest.mark.parametrize("kind", [SetpointKind.HEAT, SetpointKind.COOL])
def test_ac_limits_go_to_both_sections(kind: SetpointKind) -> None:
    rules = SetpointRules(record=AC_RECORD)
    limit = rules.limit(18, minimum=False, kind=kind)
    changes = commands.setpoint_limit_changes(
        limit, minimum=False, kind=kind, family=ModelFamily.AC
    )
    assert changes == {
        "targetHeat": {"lockoutMax": 18.0},
        "targetCool": {"lockoutMax": 18.0},
    }
    expected = commands.state_command(changes).expected
    assert expected == {"setpoint_max": 18.0, "cool_max": 18.0}


def test_cool_limit_encoder_refusals() -> None:
    limit = SetpointRules(HVAC).limit(18, minimum=True, kind=SetpointKind.COOL)
    for kind in (SetpointKind.AUTO_HEAT, SetpointKind.AUTO_COOL, SetpointKind.AUTO):
        with pytest.raises(ValueError, match="limit"):
            commands.setpoint_limit_changes(limit, minimum=True, kind=kind)
    for family in (
        ModelFamily.BASEBOARD_V1,
        ModelFamily.BASEBOARD_V2,
        ModelFamily.BASEBOARD_LITE,
        ModelFamily.BASEBOARD_V3,
        ModelFamily.INFLOOR,
        ModelFamily.CLIMATE_SENSOR,
    ):
        with pytest.raises(SetpointModeError):
            commands.setpoint_limit_changes(
                limit, minimum=True, kind=SetpointKind.COOL, family=family
            )


def test_rules_check_cool_limits_against_the_cooling_hardware_range() -> None:
    rules = SetpointRules(HVAC, cool_min=18, cool_max=30)
    assert rules.hardware_cool_range == SetpointRange(15.0, 35.0, 1.0)
    assert rules.limit(16.4, minimum=True, kind=SetpointKind.COOL).celsius == 16.0
    with pytest.raises(SetpointOutOfRangeError) as error:
        rules.limit(14, minimum=True, kind=SetpointKind.COOL)
    assert (error.value.limit, error.value.kind) == (15.0, None)
    clamped = rules.limit(40, minimum=False, kind=SetpointKind.COOL, strict=False)
    assert clamped.celsius == 35.0
    # The other current cooling limit orders it, not the heating limits.
    with pytest.raises(SetpointLimitOrderError) as order:
        rules.limit(30, minimum=True, kind=SetpointKind.COOL)
    assert (order.value.value, order.value.other) == (30.0, 30.0)
    with pytest.raises(SetpointLimitOrderError):
        rules.limit(18, minimum=False, kind=SetpointKind.COOL)
    assert rules.limit(29, minimum=True, kind=SetpointKind.COOL).celsius == 29.0
    # The owner's cooling limits narrow cooling targets, as heat limits do.
    assert rules.range_for(Mode.COOL) == SetpointRange(18.0, 30.0, 1.0)
    with pytest.raises(SetpointOutOfRangeError):
        rules.setpoint(Mode.COOL, 31)
    with pytest.raises(ValueError, match="limit"):
        rules.limit(20, minimum=True, kind=SetpointKind.AUTO_COOL)


def test_rules_refuse_cool_limits_without_advertised_cooling() -> None:
    for rules in (
        SetpointRules(BASEBOARD),
        SetpointRules(BASEBOARD, homekit_range=(5.0, 30.0, 0.5)),
        SetpointRules(),
    ):
        with pytest.raises(SetpointModeError) as error:
            rules.limit(20, minimum=True, kind=SetpointKind.COOL)
        assert error.value.mode is Mode.COOL
    # A Mini-Split AC advertises cooling through its record.
    ac = SetpointRules(record=AC_RECORD)
    assert ac.hardware_cool_range == SetpointRange(16.0, 30.0, 1.0)
    assert ac.limit(20.4, minimum=True, kind=SetpointKind.COOL).celsius == 20.0
    with pytest.raises(sp.SetpointConfigError):
        SetpointRules(HVAC, cool_min=float("nan"))


def test_cool_limit_readback_uses_the_parsed_lockouts() -> None:
    state = DeviceState.from_api(
        "hvac", {"targetCool": {"reported": {"lockoutMin": 18, "lockoutMax": 30}}}
    )
    assert (state.cool_min, state.cool_max) == (18.0, 30.0)
    command = commands.state_command(
        commands.setpoint_limit_changes(
            SetpointRules(HVAC).limit(30, minimum=False, kind=SetpointKind.COOL),
            minimum=False,
            kind=SetpointKind.COOL,
        )
    )
    assert command.expected["cool_max"] == state.cool_max


def test_validation_checks_cool_limits() -> None:
    state = DeviceState.from_api(
        "ac",
        {"targetCool": {"reported": {"lockoutMin": 18, "lockoutMax": 30}}},
    )
    caps = replace(HVAC, model="AC-V1-0")
    validate_cloud_command(ModelFamily.AC, caps, state, {"cool_min": 20}, _no_pending)
    with pytest.raises(ValueError, match="cool_min"):
        validate_cloud_command(
            ModelFamily.AC, caps, state, {"cool_min": 14}, _no_pending
        )
    with pytest.raises(ValueError, match="below the maximum"):
        validate_cloud_command(
            ModelFamily.AC, caps, state, {"cool_min": 30}, _no_pending
        )
    with pytest.raises(ValueError, match="below the maximum"):
        validate_cloud_command(
            ModelFamily.AC, caps, state, {"cool_max": 18}, _no_pending
        )
    # A Mini-Split AC's capabilities may lack a cool range; its record has it.
    bare = replace(BASE, model="AC-V1-0", heat_setpoint_range=(16.0, 30.0, 1.0))
    validate_cloud_command(
        ModelFamily.AC,
        bare,
        state,
        {"setpoint_max": 25, "cool_max": 25},
        _no_pending,
    )
    with pytest.raises(ValueError, match="cool_min"):
        validate_cloud_command(
            ModelFamily.BASEBOARD_V2, BASEBOARD, None, {"cool_min": 20}, _no_pending
        )


async def test_client_sets_cool_limits(session, cloud) -> None:
    from tests.cloud.test_client import _client
    from tests.cloud_helpers import DEVICE_ID

    client = _client(session)
    rules = SetpointRules(HVAC)
    await client.set_setpoint_limit(
        DEVICE_ID,
        rules.limit(30, minimum=False, kind=SetpointKind.COOL),
        minimum=False,
        kind=SetpointKind.COOL,
    )
    await client.set_setpoint_limit(
        DEVICE_ID,
        SetpointRules(record=AC_RECORD).limit(18, minimum=True),
        minimum=True,
        family=ModelFamily.AC,
    )
    ac_auto = SetpointRules(record=AC_RECORD).setpoint(Mode.AUTO, 22)
    await client.set_temperature(DEVICE_ID, Mode.AUTO, ac_auto, family=ModelFamily.AC)
    assert cloud.updates == [
        {"source": 3, "targetCool": {"lockoutMax": 30.0}},
        {
            "source": 3,
            "targetHeat": {"lockoutMin": 18.0},
            "targetCool": {"lockoutMin": 18.0},
        },
        {"source": 3, "targetAuto": {"setpoint": 22.0}},
    ]
    # Cooling limits exist only in the structured state.
    with pytest.raises(ValueError, match="route"):
        await client.set_setpoint_limit(
            DEVICE_ID,
            rules.limit(30, minimum=False, kind=SetpointKind.COOL),
            minimum=False,
            kind=SetpointKind.COOL,
            route="R",
        )
