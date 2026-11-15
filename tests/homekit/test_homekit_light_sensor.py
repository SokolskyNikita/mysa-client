"""The optional HomeKit Light Sensor service reported on in-floor models."""

from unittest.mock import AsyncMock, patch

from aiohomekit.model import Accessories
from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.testing import FakeController
import pytest

from mysa_client.homekit import (
    CharacteristicInfo,
    Field,
    HomeKitProtocol,
    HomeKitWriteRejectedError,
)
from tests.homekit_helpers import (
    HKID,
    PAIRING_DATA,
    fake_characteristic_address,
    load_json,
    make_controller,
    update_fake_characteristic,
)

# Copied from the INF-V1-0 (firmware 3.17.6.5) diagnostics in mysa-client#29.
LIGHT_SENSOR_SERVICE = {
    "iid": 512,
    "type": "00000084-0000-1000-8000-0026BB765291",
    "characteristics": [
        {
            "type": "00000023-0000-1000-8000-0026BB765291",
            "iid": 514,
            "perms": ["pr"],
            "format": "string",
            "value": "Light Sensor",
            "maxLen": 64,
        },
        {
            "type": "0000006B-0000-1000-8000-0026BB765291",
            "iid": 515,
            "perms": ["pr", "ev"],
            "format": "float",
            "value": 1,
            "unit": "lux",
            "minValue": 0,
            "maxValue": 1573,
            "minStep": 1,
        },
    ],
}


async def _light_sensor_device():
    profile = load_json("homekit_bb_v2_0_l.json")
    profile[0]["services"].append(LIGHT_SENSOR_SERVICE)
    controller = FakeController()
    await controller.async_start()
    pairing = await controller.add_paired_device(
        Accessories.from_list(profile), alias=HKID
    )
    pairing.pairing_data = dict(PAIRING_DATA)
    controller.aliases[HKID] = pairing
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    await device.async_connect()
    return pairing, device


async def test_light_level_is_mapped_only_when_advertised() -> None:
    """Models without the Light Sensor service never report the field."""
    controller = await make_controller(paired=True)
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    await device.async_connect()

    assert Field.AMBIENT_LIGHT not in device.supported_fields
    assert Field.AMBIENT_LIGHT not in device.state
    await device.async_close()


async def test_light_level_reads_events_and_limits() -> None:
    """The lux characteristic decodes to a float and follows value events."""
    pairing, device = await _light_sensor_device()
    events = []
    device.add_listener(events.append)

    assert device.ready
    assert Field.AMBIENT_LIGHT in device.supported_fields
    assert device.state[Field.AMBIENT_LIGHT] == 1.0
    assert isinstance(device.state[Field.AMBIENT_LIGHT], float)
    assert device.capabilities[Field.AMBIENT_LIGHT] == CharacteristicInfo(0, 1573, 1)
    assert fake_characteristic_address(
        pairing, CharacteristicsTypes.LIGHT_LEVEL_CURRENT
    ) in set(pairing.subscriptions)

    update_fake_characteristic(pairing, CharacteristicsTypes.LIGHT_LEVEL_CURRENT, 412)
    assert device.state[Field.AMBIENT_LIGHT] == 412.0
    assert events[-1].changed_fields == {Field.AMBIENT_LIGHT}

    values = await device.async_read((Field.CURRENT_TEMPERATURE, Field.AMBIENT_LIGHT))
    assert values[Field.AMBIENT_LIGHT] == 412.0
    assert device.diagnostics()["fields"]["ambient_light"]["max_value"] == 1573
    await device.async_close()


@pytest.mark.parametrize("wire_value", [None, "bright", True])
async def test_unexpected_light_values_decode_to_none(wire_value) -> None:
    """Non-numeric wire values become unknown instead of leaking through."""
    pairing, device = await _light_sensor_device()
    address = fake_characteristic_address(
        pairing, CharacteristicsTypes.LIGHT_LEVEL_CURRENT
    )

    with patch.object(
        pairing,
        "get_characteristics",
        new=AsyncMock(return_value={address: {"value": wire_value}}),
    ):
        values = await device.async_read((Field.AMBIENT_LIGHT,))

    assert values == {Field.AMBIENT_LIGHT: None}
    await device.async_close()


async def test_light_read_failure_is_not_essential() -> None:
    """A failed lux read is reported without failing the thermostat read."""
    pairing, device = await _light_sensor_device()
    temperature = fake_characteristic_address(
        pairing, CharacteristicsTypes.TEMPERATURE_CURRENT
    )
    light = fake_characteristic_address(
        pairing, CharacteristicsTypes.LIGHT_LEVEL_CURRENT
    )

    with patch.object(
        pairing,
        "get_characteristics",
        new=AsyncMock(
            return_value={temperature: {"value": 20.0}, light: {"status": -70402}}
        ),
    ):
        values = await device.async_read(
            (Field.CURRENT_TEMPERATURE, Field.AMBIENT_LIGHT)
        )

    assert values == {Field.CURRENT_TEMPERATURE: 20.0}
    assert device.failed_fields == {Field.AMBIENT_LIGHT: "-70402"}
    assert device.state[Field.AMBIENT_LIGHT] == 1.0
    await device.async_close()


async def test_light_level_cannot_be_written() -> None:
    """The sensor is read-only even though it has a HAP address."""
    pairing, device = await _light_sensor_device()

    with (
        patch.object(pairing, "put_characteristics", new=AsyncMock()) as put,
        pytest.raises(HomeKitWriteRejectedError, match="read-only"),
    ):
        await device.async_write({Field.AMBIENT_LIGHT: 50.0})

    put.assert_not_called()
    await device.async_close()
