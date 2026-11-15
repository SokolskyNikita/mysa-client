"""The 0.4.0 read-only additions: drstate, health fields, fleet OTA, sensor links."""

from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest

from mysa_client.cloud import (
    DemandResponseState,
    DeviceState,
    FleetFirmwareAvailability,
    FleetFirmwareStatus,
    FleetUpdateStatus,
    MysaApiError,
    MysaAuth,
    MysaClient,
    MysaConnectionError,
    MysaNotFoundError,
    SensorLink,
    firmware_versions_match,
    normalize_firmware_version,
)
from mysa_client.cloud.const import API_BASE, COGNITO_URL
from mysa_client.cloud.observations import MEASUREMENTS, TELEMETRY_FIELDS
from mysa_client.cloud.reconciliation import reconcile
from mysa_client.cloud.trace import route_template
from tests.cloud_helpers import DEVICE_ID, REFRESH_TOKEN, load_json, make_jwt

FIXTURE = load_json("reads_0_4_0.json")
# The fixture clocks: 2026-11-15T20:00:00Z.
STAMP = datetime(2026, 11, 15, 20, 0, tzinfo=UTC)
HEALTH_FIELDS = (
    "free_heap",
    "raw_temperature",
    "core_temperature",
    "probe_temperature",
    "voltage",
    "power_wattage",
    "power_current",
    "power_duty_cycle",
    "power_time",
    "max_current",
    "power_consumed",
    "instant_load",
    "on_time",
    "effective_setpoint",
)


def body(name):
    return FIXTURE[name]["body"]


@pytest.fixture
def client(session, aioclient_mock):
    aioclient_mock.post(
        COGNITO_URL,
        json={"AuthenticationResult": {"IdToken": make_jwt(), "ExpiresIn": 3600}},
    )
    return MysaClient(
        session, MysaAuth(session, REFRESH_TOKEN, user_agent="test"), user_agent="test"
    )


def requests(aioclient_mock, path):
    return [
        url
        for method, url, _data, _headers in aioclient_mock.mock_calls
        if method == "GET" and url.path == path
    ]


# ---- demand response -------------------------------------------------------


async def test_drstate_not_enrolled(client, aioclient_mock):
    aioclient_mock.get(
        f"{API_BASE}/devices/{DEVICE_ID}/drstate", json=body("drstate_not_enrolled")
    )
    state = await client.get_demand_response_state(DEVICE_ID)
    assert isinstance(state, DemandResponseState)
    assert not state.has_data
    assert state.new_state is None
    assert state.device_id is None
    assert state.raw == {}
    assert not state.in_event(STAMP)


async def test_drstate_event(client, aioclient_mock):
    aioclient_mock.get(
        f"{API_BASE}/devices/{DEVICE_ID}/drstate", json=body("drstate_event")
    )
    state = await client.get_demand_response_state(DEVICE_ID)
    assert state.has_data
    assert state.device_id == DEVICE_ID
    assert state.new_state == "event"
    assert state.timestamp == STAMP
    # Passed through untouched: types and units are not established.
    assert state.device_time == 1794772790
    assert state.current_state == "none"
    assert state.current_setpoint == 21
    assert state.new_setpoint == 18
    assert state.raw == body("drstate_event")["DeviceDrState"]
    assert state.in_event(STAMP + timedelta(hours=1))


def test_drstate_explicit_none_differs_from_no_data():
    explicit = DemandResponseState.from_api(body("drstate_none")["DeviceDrState"])
    empty = DemandResponseState.from_api({})
    assert explicit.has_data
    assert explicit.new_state == "none"
    assert not empty.has_data
    assert empty.new_state is None
    assert not explicit.in_event(STAMP)


@pytest.mark.parametrize(
    ("new_state", "age", "expected"),
    [
        ("event", timedelta(0), True),
        ("preheat", timedelta(hours=2), True),
        ("precool", timedelta(hours=8), True),
        # The app compares whole hours (moment diff), so 8 h 59 min still counts.
        ("event", timedelta(hours=8, minutes=59), True),
        ("event", timedelta(hours=9), False),
        ("event", timedelta(days=3), False),
        # A clock slightly ahead of ours is not "old".
        ("event", -timedelta(minutes=5), True),
        ("none", timedelta(0), False),
        ("EVENT", timedelta(0), False),
        ("future-state", timedelta(0), False),
    ],
)
def test_drstate_in_event_rule(new_state, age, expected):
    state = DemandResponseState.from_api(
        {"NewState": new_state, "Timestamp": STAMP.timestamp()}
    )
    assert state.in_event(STAMP + age) is expected


@pytest.mark.parametrize("stamp", [None, "soon", -5, True])
def test_drstate_without_usable_timestamp_is_not_in_event(stamp):
    state = DemandResponseState.from_api({"NewState": "event", "Timestamp": stamp})
    assert state.timestamp is None
    assert not state.in_event(STAMP)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"DeviceDrState": None},
        {"DeviceDrState": []},
        [],
        "text",
        {"DeviceDrState": {"Device": "000000000001", "NewState": "event"}},
    ],
)
async def test_drstate_malformed(client, aioclient_mock, payload):
    aioclient_mock.get(f"{API_BASE}/devices/{DEVICE_ID}/drstate", json=payload)
    with pytest.raises(MysaApiError):
        await client.get_demand_response_state(DEVICE_ID)


def test_drstate_stays_separate_from_device_state():
    names = {f.name for f in fields(DeviceState)}
    assert "in_demand_response" in names
    # `demand_response_state` mirrors structured drState.state; nothing from
    # the separate drstate endpoint is merged into DeviceState.
    assert not names & {"new_state", "dr_state", "current_state", "new_setpoint"}


# ---- health and electrical fields -----------------------------------------


def test_lite_health_fields():
    state = DeviceState.from_api(DEVICE_ID, body("state_lite_idle"))
    assert state.free_heap == 69320
    assert isinstance(state.free_heap, int)
    assert state.raw_temperature == 28.3
    assert state.core_temperature == 31.1
    assert state.probe_temperature is None
    assert state.voltage == 240
    assert state.power_wattage == 0
    assert state.power_current == 0
    assert state.power_duty_cycle == 0
    assert state.power_time == STAMP
    assert state.max_current == 0
    assert state.power_consumed == 0
    assert state.instant_load == 0
    assert state.on_time == 0
    # Reported as-is; not the target setpoint (10) and not interpreted.
    assert state.effective_setpoint == 23
    assert state.heat_setpoint == 10
    assert state.power_load_watts == 0
    assert state.ground_fault is None  # not an In-Floor
    # Existing fields are unchanged.
    assert state.rssi == -43
    assert state.duty_cycle == 0
    assert state.room_temperature == 22.9


def test_infloor_probe_power_and_ground_fault():
    state = DeviceState.from_api(DEVICE_ID, body("state_infloor_heating"))
    assert state.probe_temperature == 24.5
    assert state.voltage == 0
    assert state.ground_fault is True
    assert state.power_duty_cycle == 45
    assert state.duty_cycle == 0.45
    assert state.power_wattage == 1500
    assert state.power_load_watts == 675
    assert state.free_heap is None
    assert state.core_temperature is None


def test_load_watts_rounds_like_javascript():
    state = DeviceState.from_api(
        DEVICE_ID,
        {"power": {"reported": {"dutyCycle": 50, "wattage": 1001}}},
    )
    assert state.power_load_watts == 501  # Math.round(500.5)


def test_health_fields_absent_are_none():
    state = DeviceState.from_api(DEVICE_ID, {})
    assert all(getattr(state, name) is None for name in HEALTH_FIELDS)
    assert state.power_load_watts is None
    assert state.ground_fault is None


def test_health_fields_fallbacks():
    # No power section: reading.voltage. No reading heap: diagnostics.
    state = DeviceState.from_api(
        DEVICE_ID,
        {
            "latestTelemetry": {"reading": {"voltage": 120}},
            "diagnostics": {"reported": {"freeHeap": 5000}},
        },
    )
    assert state.voltage == 120
    assert state.free_heap == 5000
    # power.reported wins over the reading when both exist.
    state = DeviceState.from_api(
        DEVICE_ID,
        {
            "latestTelemetry": {"reading": {"voltage": 120, "freeHeap": 7}},
            "power": {"reported": {"voltage": 240}},
            "diagnostics": {"reported": {"freeHeap": 5000}},
        },
    )
    assert state.voltage == 240
    assert state.free_heap == 7


@pytest.mark.parametrize(
    ("section", "wire", "name", "value"),
    [
        ("reading", "freeHeap", "free_heap", -1),
        ("reading", "freeHeap", "free_heap", 1.5),
        ("reading", "freeHeap", "free_heap", True),
        ("reading", "coreTemperature", "core_temperature", 500),
        ("reading", "rawTemperature", "raw_temperature", "28.3"),
        ("reading", "probeTemperature", "probe_temperature", float("nan")),
        ("reading", "effectiveSetpoint", "effective_setpoint", -200),
        ("reading", "onTime", "on_time", "0"),
        ("power", "dutyCycle", "power_duty_cycle", 150),
        ("power", "dutyCycle", "power_duty_cycle", -1),
        ("power", "wattage", "power_wattage", -10),
        ("power", "voltage", "voltage", -1),
        ("power", "current", "power_current", None),
    ],
)
def test_health_fields_reject_bad_values(section, wire, name, value):
    if section == "reading":
        data = {"latestTelemetry": {"reading": {wire: value}}}
    else:
        data = {"power": {"reported": {wire: value}}}
    assert getattr(DeviceState.from_api(DEVICE_ID, data), name) is None


def test_health_fields_from_a_future_clock_are_dropped():
    future = (datetime.now(UTC) + timedelta(days=1)).timestamp()
    data = {
        "latestTelemetry": {
            "reading": {"timestamp": future, "coreTemperature": 31, "voltage": 1}
        },
        "power": {"reported": {"timestamp": future, "wattage": 100, "voltage": 240}},
        "diagnostics": {"reported": {"timestamp": future, "freeHeap": 1}},
    }
    state = DeviceState.from_api(DEVICE_ID, data)
    assert state.core_temperature is None
    assert state.power_wattage is None
    assert state.power_time is None
    assert state.voltage is None
    assert state.free_heap is None


def test_desired_only_power_values_are_ignored():
    state = DeviceState.from_api(
        DEVICE_ID, {"power": {"desired": {"wattage": 1000}, "reported": {}}}
    )
    assert state.power_wattage is None


def test_health_fields_are_outside_the_arbitrated_observation_sets():
    # They have no second source to arbitrate and must not change how
    # reconcile() or command confirmation behave; see the Python reference.
    assert not set(HEALTH_FIELDS) & set(TELEMETRY_FIELDS)
    assert not set(HEALTH_FIELDS) & MEASUREMENTS
    state = DeviceState.from_api(DEVICE_ID, body("state_lite_idle"))
    assert not set(HEALTH_FIELDS) & set(state.observations)


def test_reconcile_keeps_health_fields_from_structured_state():
    state = DeviceState.from_api(DEVICE_ID, body("state_lite_idle"))
    legacy = DeviceState.from_legacy(DEVICE_ID, {"Rssi": {"v": -50, "t": 1}})
    merged = reconcile(DEVICE_ID, [legacy, state], None, None, datetime.now(UTC))
    assert merged.free_heap == 69320
    assert merged.voltage == 240
    assert merged.effective_setpoint == 23
    assert legacy.free_heap is None


# ---- fleet firmware ----------------------------------------------------------


async def test_fleet_availability_not_in_fleet(client, aioclient_mock):
    aioclient_mock.get(
        f"{API_BASE}/ota/update/available/{DEVICE_ID}",
        status=404,
        json={"message": "No fleet found containing device"},
    )
    result = await client.get_fleet_firmware_availability(DEVICE_ID)
    assert isinstance(result, FleetFirmwareAvailability)
    assert result.in_fleet is False
    assert result.update_available is False
    assert result.version is None
    assert result.raw == {}


async def test_fleet_availability_offer(client, aioclient_mock):
    aioclient_mock.get(
        f"{API_BASE}/ota/update/available/{DEVICE_ID}",
        json=body("ota_available_update"),
    )
    result = await client.get_fleet_firmware_availability(
        DEVICE_ID, current_version="4.1.0"
    )
    assert result.in_fleet
    assert result.update_available
    assert result.message == "Update available"
    assert result.version_id == "fw-00000001"
    assert result.version == "v4.2.0-rc1"
    assert result.description == "Stability improvements"
    assert result.release_notes == "Example release notes."
    assert result.raw == body("ota_available_update")
    [url] = requests(aioclient_mock, f"/ota/update/available/{DEVICE_ID}")
    assert url.query["currentVersion"] == "4.1.0"


async def test_fleet_availability_without_current_version(client, aioclient_mock):
    aioclient_mock.get(
        f"{API_BASE}/ota/update/available/{DEVICE_ID}", json={"firmwareVersion": {}}
    )
    result = await client.get_fleet_firmware_availability(DEVICE_ID)
    assert result.in_fleet
    assert not result.update_available
    assert result.version_id is None
    [url] = requests(aioclient_mock, f"/ota/update/available/{DEVICE_ID}")
    assert "currentVersion" not in url.query


@pytest.mark.parametrize(
    ("payload", "update", "version_id"),
    [
        ({"updateAvailable": "yes"}, False, None),
        ({"updateAvailable": 1}, False, None),
        ({"firmwareVersion": {"id": 0}}, False, 0),
        ({"firmwareVersion": {"id": True}}, False, None),
        ({"firmwareVersion": "4.2.0"}, False, None),
    ],
)
def test_fleet_availability_lenient(payload, update, version_id):
    result = FleetFirmwareAvailability.from_api(payload)
    assert result.update_available is update
    assert result.version_id == version_id


@pytest.mark.parametrize(
    ("status", "error"), [(403, MysaApiError), (500, MysaConnectionError)]
)
async def test_fleet_availability_other_errors_raise(
    client, aioclient_mock, status, error
):
    aioclient_mock.get(f"{API_BASE}/ota/update/available/{DEVICE_ID}", status=status)
    with pytest.raises(error):
        await client.get_fleet_firmware_availability(DEVICE_ID)


async def test_fleet_availability_malformed(client, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/ota/update/available/{DEVICE_ID}", json=[])
    with pytest.raises(MysaApiError):
        await client.get_fleet_firmware_availability(DEVICE_ID)


async def test_fleet_status(client, aioclient_mock):
    request_id = "00000000-0000-4000-8000-0000000000c1"
    aioclient_mock.get(
        f"{API_BASE}/ota/update/{request_id}", json=body("ota_status_in_progress")
    )
    status = await client.get_fleet_firmware_status(request_id)
    assert isinstance(status, FleetFirmwareStatus)
    assert status.status == "IN_PROGRESS"
    assert status.known_status is FleetUpdateStatus.IN_PROGRESS
    assert status.percent_complete == 42
    assert not status.finished
    assert status.raw == body("ota_status_in_progress")


@pytest.mark.parametrize(
    ("payload", "known", "finished", "percent"),
    [
        ({"status": "SUCCEEDED", "percentComplete": 100}, "SUCCEEDED", True, 100),
        ({"status": "CANCELED"}, "CANCELED", True, None),
        ({"status": "QUEUED", "percentComplete": 0}, "QUEUED", False, 0),
        ({"status": "PAUSED", "percentComplete": 101}, None, False, None),
        ({"percentComplete": "50"}, None, False, None),
        ({}, None, False, None),
    ],
)
def test_fleet_status_lenient(payload, known, finished, percent):
    status = FleetFirmwareStatus.from_api(payload)
    assert status.status == payload.get("status")
    assert status.known_status == known
    assert status.finished is finished
    assert status.percent_complete == percent


async def test_fleet_status_errors(client, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/ota/update/r1", status=404)
    with pytest.raises(MysaNotFoundError):
        await client.get_fleet_firmware_status("r1")
    aioclient_mock.get(f"{API_BASE}/ota/update/r2", json=["IN_PROGRESS"])
    with pytest.raises(MysaApiError):
        await client.get_fleet_firmware_status("r2")


def test_fleet_status_values_match_the_app():
    assert [s.value for s in FleetUpdateStatus] == [
        "QUEUED",
        "IN_PROGRESS",
        "SUCCEEDED",
        "FAILED",
        "TIMED_OUT",
        "REJECTED",
        "REMOVED",
        "CANCELED",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("3.17.5.13", "3.17.5.13"),
        ("  v3.17.5.13 ", "3.17.5.13"),
        ("V4.2.0-rc1", "4.2.0"),
        ("4.2.0 - beta", "4.2.0"),
        ("vv1", "v1"),
        ("v", None),
        ("-1", None),
        ("   ", None),
        ("", None),
        (None, None),
        (4, None),
    ],
)
def test_normalize_firmware_version(value, expected):
    assert normalize_firmware_version(value) == expected


@pytest.mark.parametrize(
    ("reported", "target", "expected"),
    [
        ("4.2.0", "v4.2.0-rc1", True),
        (" V4.2.0", "4.2.0", True),
        ("4.2.0", "4.2.1", False),
        ("4.2", "4.2.0", False),
        (None, None, False),
        ("", "", False),
        (None, "4.2.0", False),
    ],
)
def test_firmware_versions_match(reported, target, expected):
    assert firmware_versions_match(reported, target) is expected


def test_no_fleet_write_methods():
    for name in ("start_fleet_firmware_update", "cancel_fleet_firmware_update"):
        assert not hasattr(MysaClient, name)


# ---- sensor links ------------------------------------------------------------


async def test_sensor_links_empty(client, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/linking", json=body("linking_empty"))
    assert await client.get_sensor_links(DEVICE_ID) == []
    [url] = requests(aioclient_mock, "/linking")
    assert url.query["targetId"] == DEVICE_ID


async def test_sensor_links(client, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/linking", json=[*body("linking_hvac"), "junk"])
    links = await client.get_sensor_links("0000000000b0")
    assert all(isinstance(link, SensorLink) for link in links)
    assert [link.source_id for link in links] == [
        "0000000000b0",
        "0000000000c5",
        "0000000000c6",
        "0000000000c7",
    ]
    own, sensor, disabled, zero = links
    assert own.own_sensor
    assert own.participating
    assert sensor.enabled is None
    assert sensor.participating
    assert not sensor.own_sensor
    assert sensor.weight == 2
    assert sensor.link_id == "00000000-0000-4000-8000-0000000000d2"
    assert sensor.target_id == "0000000000b0"
    assert disabled.enabled is False
    assert not disabled.participating
    assert zero.weight == 0
    assert not zero.participating
    assert own.raw == body("linking_hvac")[0]


@pytest.mark.parametrize(
    ("entry", "participating"),
    [
        ({}, False),
        ({"weight": "1"}, False),
        ({"weight": 1, "enabled": "false"}, True),
        ({"weight": 0.5, "enabled": True, "linkId": 7}, True),
    ],
)
def test_sensor_link_lenient(entry, participating):
    link = SensorLink.from_api(entry)
    assert link.participating is participating
    assert not link.own_sensor
    if "linkId" in entry:
        assert link.link_id == "7"


@pytest.mark.parametrize("payload", [{}, {"links": []}, "text"])
async def test_sensor_links_malformed(client, aioclient_mock, payload):
    aioclient_mock.get(f"{API_BASE}/linking", json=payload)
    with pytest.raises(MysaApiError):
        await client.get_sensor_links(DEVICE_ID)


def test_sensor_links_are_read_only():
    for name in ("create_sensor_link", "update_sensor_link", "delete_sensor_link"):
        assert not hasattr(MysaClient, name)


# ---- tracing -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "template"),
    [
        (f"/devices/{DEVICE_ID}/drstate", "/devices/{id}/drstate"),
        (f"/ota/update/available/{DEVICE_ID}", "/ota/update/available/{id}"),
        ("/ota/update/abc?x=1", "/ota/update/{id}"),
        (f"/linking?targetId={DEVICE_ID}", "/linking"),
    ],
)
def test_new_routes_are_traced_without_ids(path, template):
    assert route_template(path) == template


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        # What both Baseboard Lites and third-party captures report.
        ({"inEvent": False, "state": "none"}, "none"),
        ({"inEvent": True, "state": "preheat"}, "preheat"),
        ({"inEvent": False}, None),
        ({"inEvent": False, "state": 3}, None),
    ],
)
def test_demand_response_state_text(section, expected):
    """drState.state is kept as sent, next to the in-event flag."""
    state = DeviceState.from_api("dev", {"drState": section})
    assert state.demand_response_state == expected
    assert state.in_demand_response is section["inEvent"]
