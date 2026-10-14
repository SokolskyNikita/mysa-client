"""Cloud parser, routing, and schedule regressions independent of Home Assistant."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from mysa_client.cloud import (
    AcFan,
    Capabilities,
    DeviceRecord,
    DeviceState,
    Mode,
    ModelFamily,
    MysaApiError,
    MysaConnectionError,
    MysaNotFoundError,
    MysaRateLimitError,
    Swing,
)
from mysa_client.cloud.client import MysaClient
from mysa_client.cloud.const import LEGACY_API_BASE
from mysa_client.cloud.observations import timestamp
from tests.cloud_helpers import DEVICE_ID, HOME_ID, REFRESH_TOKEN, load_json

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


def make_client(session) -> MysaClient:
    """Build a cloud client that can be patched without making HTTP calls."""
    from mysa_client.cloud import MysaAuth

    auth = MysaAuth(session, REFRESH_TOKEN, user_agent="test")
    return MysaClient(session, auth, user_agent="test")


def structured(now: datetime = NOW, **sections: object) -> DeviceState:
    """Build a state response with timestamped telemetry."""
    return DeviceState.from_api(
        DEVICE_ID,
        {
            "latestTelemetry": {
                "isConnected": True,
                "lastConnected": now.timestamp(),
                "reading": {
                    "timestamp": now.timestamp(),
                    "roomTemperature": 21,
                    "humidity": 40,
                },
            },
            **sections,
        },
    )


def legacy(now: datetime = NOW, **fields: object) -> DeviceState:
    """Build a state from the older Wi-Fi endpoint."""
    return DeviceState.from_legacy(
        DEVICE_ID,
        {"CorrectedTemp": {"v": 22, "t": now.timestamp()}, **fields},
    )


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), 10**400, -1, "bad", True]
)
def test_bad_timestamp_does_not_raise(value: object) -> None:
    assert timestamp(value) is None


def test_mixed_clocks_and_enum_namespaces() -> None:
    state = legacy(
        Humidity={"v": 0, "t": NOW.timestamp() * 1000},
        TstatMode={"v": 3, "t": NOW.isoformat()},
        Mode={"v": 1, "t": NOW.timestamp()},
        Lock={"v": 0, "t": NOW.timestamp()},
        Connected={"v": False, "t": NOW.timestamp()},
    )
    assert state.mode == Mode.HEAT
    assert state.button_lock == 0
    assert state.connected is False
    assert state.humidity == 0
    assert state.observations["humidity"].observed_at == NOW
    assert state.observations["mode"].observed_at == NOW


def test_partial_invalid_state_and_future_clock() -> None:
    # Keep the future sample ahead of the parser's clock as calendar time advances.
    with patch("mysa_client.cloud.models.datetime", wraps=datetime) as clock:
        clock.now.return_value = NOW
        state = structured(
            targetHeat={"reported": {"timestamp": 10**400, "setpoint": 24}},
            modes={"desired": {"mode": 0}},
            physicalInterface={
                "reported": {
                    "timestamp": (NOW + timedelta(days=1)).timestamp(),
                    "lockout": 3,
                }
            },
        )
    assert state.room_temperature == 21
    assert state.heat_setpoint is None
    assert state.mode is None
    assert state.button_lock is None
    assert not state.observations["heat_setpoint"].clock_valid


async def test_read_fallback_backoff_and_recovery(session) -> None:
    client = make_client(session)
    request = AsyncMock(
        side_effect=[
            MysaConnectionError("down"),
            {"DeviceStatesObj": {}},
            {"DeviceStatesObj": {}},
            {"DeviceStatesObj": {}},
        ]
    )
    with (
        patch.object(client, "_request", request),
        patch(
            "mysa_client.cloud.client.time.monotonic",
            return_value=100,
        ),
    ):
        await client.get_legacy_states([DEVICE_ID])
        await client.get_legacy_states([DEVICE_ID])
    assert request.call_args_list[1].kwargs["base_url"] == LEGACY_API_BASE
    assert request.call_args_list[2].kwargs["base_url"] == LEGACY_API_BASE
    with (
        patch.object(client, "_request", request),
        patch(
            "mysa_client.cloud.client.time.monotonic",
            return_value=500,
        ),
    ):
        await client.get_legacy_states([DEVICE_ID])
    assert request.call_args_list[3].kwargs["base_url"] is None
    assert client.legacy_source == "R"


@pytest.mark.parametrize(
    "error", [MysaConnectionError("timeout"), MysaRateLimitError("limit", 600)]
)
async def test_ambiguous_write_never_uses_another_host(session, error) -> None:
    client = make_client(session)
    with (
        patch.object(client, "_request", AsyncMock(side_effect=error)) as request,
        pytest.raises(type(error)),
    ):
        await client.set_display_unit(DEVICE_ID, "F")
    assert request.await_count == 1


async def test_semantic_alternatives_send_one_write(session) -> None:
    client = make_client(session)
    with patch.object(client, "_request", AsyncMock()) as request:
        await client.set_button_lock(DEVICE_ID, 3, route="L")
        assert request.call_args.kwargs["body"] == {"ButtonState": "Locked"}
        assert request.call_args.kwargs["base_url"] == LEGACY_API_BASE
        await client.set_display_unit(DEVICE_ID, "F", route="S")
        assert request.call_args.kwargs["body"] == {
            "source": 3,
            "physicalInterface": {"format": "F"},
        }
        await client.set_display_brightness(DEVICE_ID, active=90, idle=20, route="R")
        assert request.call_args.kwargs["body"] == {
            "MaxBrightness": 90,
            "MinBrightness": 20,
        }
    assert request.await_count == 3


@pytest.mark.parametrize(
    "payload", [{}, [None], [{"id": "x", "deviceIds": [DEVICE_ID]}]]
)
async def test_malformed_schedule_is_an_error(session, payload) -> None:
    client = make_client(session)
    with (
        patch.object(client, "_request", AsyncMock(return_value=payload)),
        pytest.raises(MysaApiError),
    ):
        await client.get_schedules(HOME_ID)


async def test_native_schedule_ids_and_shared_attachments(session) -> None:
    client = make_client(session)
    records = {
        dev: DeviceRecord.from_api(dev, {"Model": "BB-V2-0-L", "Home": HOME_ID})
        for dev in (DEVICE_ID, "other")
    }
    raw = {
        "Id": "schedule-id",
        "Owner": "owner",
        "Version": 2,
        "Events": {"0": {"10": {"BB2L": "3|22|%|%|%|%|%"}}},
    }

    async def read(operation, method, path, **kwargs):
        return {"Schedules": []} if operation == "schedules_v1" else deepcopy(raw)

    with (
        patch.object(client, "get_schedules", AsyncMock(return_value=[])),
        patch.object(client, "get_devices", AsyncMock(return_value=records)),
        patch.object(client, "_legacy_request", read),
    ):
        schedules = await client.get_all_schedules(HOME_ID)
    assert schedules[0]["deviceIds"] == [DEVICE_ID, "other"]
    with patch.object(client, "_legacy_request", AsyncMock()) as request:
        await client.remove_schedule(schedules[0], DEVICE_ID)
    assert request.call_args.args == (
        "schedules_v2",
        "DELETE",
        "/schedules/v2/schedule-id",
    )
    assert request.call_args.kwargs["body"] == {
        "Devices": [DEVICE_ID],
        "Model": "BB-V2-0-L",
    }


@pytest.mark.parametrize("include_legacy", [False, True])
async def test_native_schedule_discovery_skips_new_models(
    session, include_legacy: bool
) -> None:
    client = make_client(session)
    records = {
        model: DeviceRecord.from_api(model, {"Model": model, "Home": HOME_ID})
        for model in ("BB-V3-0", "ST-V1-0", "CS-V1-0")
    }
    if include_legacy:
        records[DEVICE_ID] = DeviceRecord.from_api(
            DEVICE_ID, {"Model": "BB-V2-0-L", "Home": HOME_ID}
        )
    modern = [{"id": "new-model-schedule", "deviceIds": ["BB-V3-0"], "events": []}]

    async def read(operation, method, path, **kwargs):
        if operation == "schedules_v1":
            return {"Schedules": []}
        assert path == f"/schedules/v2/{DEVICE_ID}"
        raise MysaNotFoundError("No schedule")

    with (
        patch.object(client, "get_schedules", AsyncMock(return_value=modern)),
        patch.object(client, "get_devices", AsyncMock(return_value=records)),
        patch.object(client, "_legacy_request", AsyncMock(side_effect=read)) as request,
    ):
        assert await client.get_all_schedules(HOME_ID) == modern
    assert request.await_count == (2 if include_legacy else 0)


def test_parser_capabilities_and_desired_is_not_reported() -> None:
    data = load_json("app_4201_new_models.json")["models"]["ST-V1-0"]
    caps = Capabilities.from_api(data["capabilities"])
    assert Mode.AUTO in caps.basic_modes(ModelFamily.CENTRAL_HVAC)
    doc = deepcopy(data["capabilities"])
    doc["features"]["climateControl"]["advancedConfig"]["misc"]["enableAutoMode"][
        "value"
    ] = False
    assert Mode.AUTO not in Capabilities.from_api(doc).basic_modes(
        ModelFamily.CENTRAL_HVAC
    )
    state = DeviceState.from_api(
        DEVICE_ID,
        {
            "targetAuto": {"desired": {"heatSetpoint": 20, "coolSetpoint": 24}},
            "latestTelemetry": {"reading": {"hvacState": True}},
        },
    )
    assert state.auto_heat_setpoint is None
    assert state.auto_cool_setpoint is None
    assert state.hvac_state is None


def test_ac_record_capabilities_drop_unknown_enum_values() -> None:
    record = DeviceRecord.from_api(
        DEVICE_ID,
        {
            "Model": "AC-V1-0",
            "SupportedModes": {
                "4": {
                    "fanSpeeds": ["6", 1, 99, None, True],
                    "verticalSwing": [12, 3, "1", 99],
                },
                "5": "not a dict",
            },
            "modifiedKeys": [],
        },
    )
    assert record.modified_keys is None
    assert record.ac_fan_speeds(Mode.COOL) == (AcFan.AUTO, AcFan.HIGH)
    assert record.ac_swing(Mode.COOL, "verticalSwing") == (Swing.OFF, Swing.AUTO)
    assert record.ac_fan_speeds(Mode.FAN_ONLY) == ()
    assert record.ac_fan_speeds(Mode.SLEEP) == ()
