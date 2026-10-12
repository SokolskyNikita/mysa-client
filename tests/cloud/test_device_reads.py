"""Single-record and firmware catalog HTTP contract regression tests."""

import pytest

from mysa_client.cloud import (
    DeviceRecord,
    FirmwareRecord,
    MysaApiError,
    MysaAuth,
    MysaClient,
    MysaNotFoundError,
)
from mysa_client.cloud.const import API_BASE, COGNITO_URL, LEGACY_API_BASE
from mysa_client.cloud.trace import route_template
from tests.cloud_helpers import DEVICE_ID, REFRESH_TOKEN, load_json, make_jwt


@pytest.fixture
def client(session, aioclient_mock):
    aioclient_mock.post(
        COGNITO_URL,
        json={"AuthenticationResult": {"IdToken": make_jwt(), "ExpiresIn": 3600}},
    )
    return MysaClient(
        session, MysaAuth(session, REFRESH_TOKEN, user_agent="test"), user_agent="test"
    )


async def test_single_device(client, aioclient_mock):
    payload = load_json("device_record.json")
    aioclient_mock.get(f"{API_BASE}/devices/{DEVICE_ID}", json=payload)
    record = await client.get_device(DEVICE_ID)
    assert isinstance(record, DeviceRecord)
    assert record.device_id == DEVICE_ID
    assert record.model == "BB-V2-0-L"
    assert record.name == "Example room"
    assert record.setpoint_min == 5
    assert record.raw == payload["Device"]
    assert client._device_models[DEVICE_ID] == record.model


async def test_firmware_catalog(client, aioclient_mock):
    payload = load_json("firmware_catalog.json")
    aioclient_mock.get(f"{API_BASE}/devices/firmware", json=payload)
    catalog = await client.get_firmware_catalog()
    assert list(catalog) == [DEVICE_ID]
    record = catalog[DEVICE_ID]
    assert isinstance(record, FirmwareRecord)
    assert record.device_id == DEVICE_ID
    assert record.installed_version == "3.17.5.13"
    assert record.raw == payload["Firmware"][DEVICE_ID]
    assert not hasattr(record, "allowed_version")
    assert not hasattr(record, "update_available")
    assert route_template("/devices/firmware") == "/devices/firmware"


@pytest.mark.parametrize("entry", [{}, {"InstalledVersion": None}, {"Future": True}])
async def test_unknown_catalog_version(client, aioclient_mock, entry):
    entry = {"Device": DEVICE_ID, **entry}
    aioclient_mock.get(
        f"{API_BASE}/devices/firmware", json={"Firmware": {DEVICE_ID: entry}}
    )
    record = (await client.get_firmware_catalog())[DEVICE_ID]
    assert record.installed_version is None
    assert record.raw == entry


async def test_empty_catalog(client, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/devices/firmware", json={"Firmware": {}})
    assert await client.get_firmware_catalog() == {}


@pytest.mark.parametrize(
    ("method", "path", "fixture"),
    [
        ("get_device", f"/devices/{DEVICE_ID}", "device_record.json"),
        ("get_firmware_catalog", "/devices/firmware", "firmware_catalog.json"),
    ],
)
async def test_legacy_fallback_and_not_found(
    client, aioclient_mock, method, path, fixture
):
    args = [DEVICE_ID] if method == "get_device" else []
    aioclient_mock.get(f"{API_BASE}{path}", status=404)
    aioclient_mock.get(f"{LEGACY_API_BASE}{path}", json=load_json(fixture))
    assert await getattr(client, method)(*args)
    # A repeated call uses the cached host, and a missing record stays an error.
    aioclient_mock.get(f"{LEGACY_API_BASE}{path}", status=404)
    with pytest.raises(MysaNotFoundError):
        await getattr(client, method)(*args)
    paths = [str(call[1]) for call in aioclient_mock.mock_calls]
    assert paths.count(f"{API_BASE}{path}") == 1
    assert paths.count(f"{LEGACY_API_BASE}{path}") == 2


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"Device": None},
        {"Device": []},
        {"Device": {}},
        {"Device": {"Id": "wrong"}},
    ],
)
async def test_malformed_device(client, aioclient_mock, payload):
    aioclient_mock.get(f"{API_BASE}/devices/{DEVICE_ID}", json=payload)
    with pytest.raises(MysaApiError, match="single-device record"):
        await client.get_device(DEVICE_ID)
    assert DEVICE_ID not in client._device_models


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"Firmware": []},
        {"Firmware": None},
        {"Firmware": {DEVICE_ID: None}},
        {"Firmware": {DEVICE_ID: {}}},
        {"Firmware": {DEVICE_ID: {"Device": "wrong"}}},
        {"Firmware": {DEVICE_ID: {"Device": DEVICE_ID, "InstalledVersion": 123}}},
    ],
)
async def test_malformed_catalog(client, aioclient_mock, payload):
    aioclient_mock.get(f"{API_BASE}/devices/firmware", json=payload)
    with pytest.raises(MysaApiError, match="firmware catalog"):
        await client.get_firmware_catalog()
