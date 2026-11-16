"""In-place schedule replacement, schedule limits and the extended usage read."""

from __future__ import annotations

from datetime import UTC, datetime, time
from unittest.mock import AsyncMock, patch

import pytest

from mysa_client.cloud import (
    BASE_SCHEDULE_EVENTS,
    MAX_SCHEDULE_EVENTS,
    USAGE_FIELDS,
    USAGE_RAW_FIELDS,
    Capabilities,
    Mode,
    ModelFamily,
    MysaAuth,
    MysaClient,
    ScheduleEventSpec,
    SetpointRules,
    UsageDay,
    check_schedule_events,
    encode_schedule_events,
    schedule_event_limit,
)
from mysa_client.cloud.exceptions import MysaNotFoundError
from mysa_client.cloud.schedule_workflow import build_schedule_events
from tests.cloud_helpers import DEVICE_ID, REFRESH_TOKEN, FakeCloud, load_json


def _client(session) -> MysaClient:
    auth = MysaAuth(session, REFRESH_TOKEN, user_agent="test")
    return MysaClient(session, auth, user_agent="test")


def _event(day: int, at: str) -> dict:
    return {
        "executionTime": at,
        "dayOfWeek": str(day),
        "clientKey": "test",
        "state": {"targetHeat": {"setpoint": 20}, "modes": {"mode": 4}},
    }


async def test_update_schedule_sends_the_whole_record(session, cloud: FakeCloud):
    client = _client(session)
    schedule_id = await client.create_schedule(
        home_id="home", device_id=DEVICE_ID, device_type="BB", events=[]
    )
    assert schedule_id == "s1"
    events = [_event(1, "06:30:00"), _event(1, "22:00:00")]
    await client.update_schedule(
        "s1",
        home_id="home",
        device_type="BB",
        device_ids=[DEVICE_ID],
        events=events,
        name="Weekdays",
    )
    assert cloud.calls("PUT", "/schedule/s1") == [
        {
            "name": "Weekdays",
            "deviceType": "BB",
            "homeId": "home",
            "reusable": False,
            "deviceIds": [DEVICE_ID],
            "events": events,
        }
    ]
    (stored,) = await client.get_schedules("home")
    assert stored["name"] == "Weekdays"
    assert stored["events"] == events


async def test_update_schedule_refusals(session, cloud: FakeCloud):
    client = _client(session)
    common = {"home_id": "home", "device_type": "BB", "name": "x"}
    with pytest.raises(ValueError, match="at least one thermostat"):
        await client.update_schedule("s1", device_ids=[], events=[], **common)
    with pytest.raises(ValueError, match="one event per day and time"):
        await client.update_schedule(
            "s1",
            device_ids=[DEVICE_ID],
            events=[_event(2, "07:00:00"), _event(2, "07:00:00")],
            **common,
        )
    assert cloud.calls("PUT", "/schedule/s1") == []
    with pytest.raises(MysaNotFoundError, match=r"PUT /schedule/\{id\}"):
        await client.update_schedule(
            "missing", device_ids=[DEVICE_ID], events=[], **common
        )


def test_schedule_limits():
    base = [_event(day, f"{hour:02d}:00:00") for day in range(7) for hour in range(8)]
    full = [_event(day, f"{hour:02d}:00:00") for day in range(7) for hour in range(10)]
    assert (len(base), len(full)) == (BASE_SCHEDULE_EVENTS, MAX_SCHEDULE_EVENTS)
    assert (BASE_SCHEDULE_EVENTS, MAX_SCHEDULE_EVENTS) == (56, 70)
    # With no model given, only more than any model takes is refused.
    check_schedule_events(full)
    with pytest.raises(ValueError, match="at most 70 events, not 71"):
        check_schedule_events([*full, _event(0, "23:30:00")])
    # A model's own limit.
    check_schedule_events(base, max_events=56)
    with pytest.raises(ValueError, match="at most 56 events, not 57"):
        check_schedule_events([*base, _event(0, "23:30:00")], max_events=56)
    # The same time on another day is a different slot.
    check_schedule_events([_event(0, "07:00:00"), _event(1, "07:00:00")])
    with pytest.raises(ValueError, match=r"day 3, 07:00:00"):
        check_schedule_events([_event(3, "07:00:00"), _event(3, "07:00:00")])


def test_schedule_event_limit_by_family():
    # A Baseboard Lite stored and ran 70 events; the app allows the AC 70 too.
    assert schedule_event_limit(ModelFamily.BASEBOARD_LITE) == 70
    assert schedule_event_limit(ModelFamily.AC) == 70
    for family in (
        ModelFamily.BASEBOARD_V1,
        ModelFamily.BASEBOARD_V2,
        ModelFamily.BASEBOARD_V3,
        ModelFamily.INFLOOR,
        ModelFamily.CENTRAL_HVAC,
        ModelFamily.CLIMATE_SENSOR,
        None,
    ):
        assert schedule_event_limit(family) == 56


def test_encode_and_build_apply_the_given_limit():
    setpoint = SetpointRules().setpoint(Mode.HEAT, 20)
    days = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
    specs = [
        ScheduleEventSpec(days, time(hour, 0), Mode.HEAT, setpoint, "test")
        for hour in range(9)
    ]
    assert len(encode_schedule_events(specs)) == 63
    with pytest.raises(ValueError, match="at most 56 events, not 63"):
        encode_schedule_events(specs, max_events=56)
    events = [
        {"days": days, "time": time(hour, 0), "temperature": 20} for hour in range(9)
    ]
    assert len(build_schedule_events(events, rules=SetpointRules())) == 63
    with pytest.raises(ValueError, match="at most 56 events, not 63"):
        build_schedule_events(events, rules=SetpointRules(), max_events=56)


async def test_update_schedule_takes_a_model_limit(session, cloud: FakeCloud):
    client = _client(session)
    await client.create_schedule(
        home_id="home", device_id=DEVICE_ID, device_type="BB", events=[]
    )
    events = [_event(day, f"{hour:02d}:00:00") for day in range(7) for hour in range(9)]
    common = {
        "home_id": "home",
        "device_type": "BB",
        "device_ids": [DEVICE_ID],
        "name": "x",
    }
    with pytest.raises(ValueError, match="at most 56 events, not 63"):
        await client.update_schedule("s1", events=events, max_events=56, **common)
    assert cloud.calls("PUT", "/schedule/s1") == []
    await client.update_schedule("s1", events=events, **common)
    assert len(cloud.calls("PUT", "/schedule/s1")[0]["events"]) == 63


def _v2_snapshot() -> dict:
    return {
        "origin": "v2",
        "deviceIds": [DEVICE_ID],
        "deviceType": "BB-V2-0-L",
        "native": {
            "Id": "v2-id",
            "Version": "3.0.0",
            "Events": {"0": {"10": {"BB": "3|22|%|%|%|%|%"}}},
        },
    }


@pytest.mark.parametrize(
    ("current", "method"),
    [
        ({"Id": "v2-id"}, "PUT"),  # still followed: update in place, id kept
        ({"Id": "another"}, "POST"),  # follows another schedule now
        (None, "POST"),  # no schedule any more: a PUT would attach nothing
    ],
)
async def test_restore_v2_schedule_picks_put_or_post(session, current, method):
    client = _client(session)
    calls = []

    async def legacy(operation, verb, path, **kwargs):
        calls.append((verb, path, kwargs.get("body")))
        if verb == "GET":
            if current is None:
                raise MysaNotFoundError("no schedule")
            return current
        return {}

    with patch.object(client, "_legacy_request", legacy):
        await client.restore_native_schedule(_v2_snapshot())
    assert calls[0][:2] == ("GET", f"/schedules/v2/{DEVICE_ID}")
    verb, path, body = calls[-1]
    assert (verb, path) == (method, "/schedules/v2/")
    assert body == {
        "Schedule": _v2_snapshot()["native"],
        "Devices": [DEVICE_ID],
        "Model": "BB-V2-0-L",
    }


async def test_restore_v2_schedule_without_an_id_is_created(session):
    client = _client(session)
    snapshot = _v2_snapshot()
    del snapshot["native"]["Id"]
    with patch.object(client, "_legacy_request", AsyncMock(return_value={})) as request:
        await client.restore_native_schedule(snapshot)
    assert request.await_count == 1
    assert request.call_args.args[:3] == ("schedules_v2", "POST", "/schedules/v2/")


def test_encode_schedule_events_refuses_a_repeated_slot():
    setpoint = SetpointRules().setpoint(Mode.HEAT, 20)
    spec = ScheduleEventSpec(("mon",), time(7, 0), Mode.HEAT, setpoint, "test")
    assert len(encode_schedule_events([spec])) == 1
    with pytest.raises(ValueError, match="one event per day and time"):
        encode_schedule_events([spec, spec])


async def test_get_usage_default_request_is_unchanged(session, cloud: FakeCloud):
    client = _client(session)
    now = datetime.now(UTC)
    await client.get_usage(DEVICE_ID, "UTC", now, now)
    (body,) = cloud.calls("POST", f"/telemetry/usage/{DEVICE_ID}")
    assert body["summary"] == {"oH1": "sum", "t": "average"}
    assert body["aggregatedReadings"] == ["oH1", "t"]
    assert "rawReadings" not in body


async def test_get_usage_extended_fields_and_raw(session, cloud: FakeCloud):
    client = _client(session)
    cloud.usage = load_json("usage_detail.json")
    now = datetime.now(UTC)
    (day,) = await client.get_usage(
        DEVICE_ID, "UTC", now, now, fields=USAGE_FIELDS, raw_fields=USAGE_RAW_FIELDS
    )
    (body,) = cloud.calls("POST", f"/telemetry/usage/{DEVICE_ID}")
    assert body["summary"] == {
        "oH1": "sum",
        "oH2": "sum",
        "oHE": "sum",
        "oC1": "sum",
        "oC2": "sum",
        "k": "sum",
        "h": "average",
        "t": "average",
        "i": "average",
        "n": "sum",
    }
    assert body["aggregatedReadings"] == list(USAGE_FIELDS)
    assert body["rawReadings"] == list(USAGE_RAW_FIELDS)

    assert day.heat_seconds == 1200
    assert day.heat_stage2_seconds == 300
    assert day.emergency_heat_seconds == 60
    assert day.cool_seconds == 900
    assert day.cool_stage2_seconds == 0
    assert day.power == 1.25
    assert day.humidity == 41.5
    assert day.probe_temperature == 24.5
    assert day.samples == 118
    bucket = day.buckets[0]
    assert (bucket.heat_seconds, bucket.heat_stage2_seconds) == (1200, 300)
    assert (bucket.cool_seconds, bucket.samples) == (0, 59)
    assert bucket.humidity == 41.0
    # What Mysa leaves out stays None.
    assert day.buckets[1].power is None
    assert day.buckets[1].emergency_heat_seconds is None

    assert len(day.raw) == 2  # the entry without a time is skipped
    sample = day.raw[0]
    assert sample.time == datetime.fromtimestamp(1794792159, UTC)
    assert sample.room_temperature == 22.5
    assert sample.sensor_temperature == 28.07
    assert sample.setpoint == 22
    assert sample.heat_setpoint == 22
    assert sample.power == 31.5
    assert sample.rssi == -47
    assert sample.humidity is None
    assert sample.cool_setpoint is None


async def test_get_usage_refuses_unknown_fields(session, cloud: FakeCloud):
    client = _client(session)
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="Unsupported usage fields"):
        await client.get_usage(DEVICE_ID, "UTC", now, now, fields=("oH1", "oF"))
    with pytest.raises(ValueError, match="Unsupported raw usage fields"):
        await client.get_usage(DEVICE_ID, "UTC", now, now, raw_fields=("n",))
    with pytest.raises(ValueError, match="at least one usage field"):
        await client.get_usage(DEVICE_ID, "UTC", now, now, fields=())
    assert cloud.calls("POST", f"/telemetry/usage/{DEVICE_ID}") == []


async def test_get_usage_raw_only(session, cloud: FakeCloud):
    client = _client(session)
    now = datetime.now(UTC)
    await client.get_usage(DEVICE_ID, "UTC", now, now, fields=(), raw_fields=("c",))
    (body,) = cloud.calls("POST", f"/telemetry/usage/{DEVICE_ID}")
    assert body["rawReadings"] == ["c"]
    assert "summary" not in body
    assert "aggregatedReadings" not in body


def test_usage_day_without_extended_fields_keeps_defaults():
    (day,) = (UsageDay.from_api(item) for item in load_json("usage.json"))
    assert day is not None
    assert day.heat_seconds == 3600.5
    assert day.samples == 200
    assert day.cool_seconds is None
    assert day.raw == ()


def _caps(auto: dict) -> Capabilities:
    return Capabilities.from_api({"features": {"climateControl": {"auto": auto}}})


def _values(low: int, high: int) -> dict:
    return {"userControllable": True, "validValues": list(range(low, high + 1))}


def test_single_auto_setpoint_from_capabilities():
    lite = Capabilities.from_api(load_json("capabilities_bb_v2_0_l.json"))
    assert lite.single_auto_setpoint is None
    single = _caps(
        {
            "heatSetpoint": _values(16, 30),
            "coolSetpoint": _values(16, 30),
            "constraints": {"minSetpointDelta": {"value": 0}},
        }
    )
    assert single.single_auto_setpoint is True
    pair = _caps(
        {
            "heatSetpoint": _values(5, 28),
            "coolSetpoint": _values(7, 30),
            "constraints": {"minSetpointDelta": {"value": 2}},
        }
    )
    assert pair.single_auto_setpoint is False
    # The same range with a required gap is still a pair.
    gap = _caps(
        {
            "heatSetpoint": _values(16, 30),
            "coolSetpoint": _values(16, 30),
            "constraints": {"minSetpointDelta": {"value": 1}},
        }
    )
    assert gap.single_auto_setpoint is False
