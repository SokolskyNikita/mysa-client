"""HomeKitDevice.healthy and the public essential-field set."""

from unittest.mock import AsyncMock, patch

from aiohomekit.model.characteristics import CharacteristicsTypes
import pytest

from mysa_client.homekit import (
    ESSENTIAL_FIELDS,
    Field,
    HomeKitCharacteristicReadError,
    HomeKitDevice,
    HomeKitProtocol,
)
from tests.homekit_helpers import (
    PAIRING_DATA,
    fake_characteristic_address,
    make_controller,
)


def _mysa_local_rule(device: HomeKitDevice) -> bool:
    """What Mysa Local 0.9 computes by hand in ``LocalThermostat``."""
    return device.ready and not (ESSENTIAL_FIELDS & device.failed_fields.keys())


def test_essential_fields_are_the_four_thermostat_fields() -> None:
    """The set Mysa Local copied, unchanged and immutable."""
    assert (
        frozenset(
            {
                Field.CURRENT_TEMPERATURE,
                Field.TARGET_TEMPERATURE,
                Field.CURRENT_ACTION,
                Field.TARGET_MODE,
            }
        )
        == ESSENTIAL_FIELDS
    )
    assert isinstance(ESSENTIAL_FIELDS, frozenset)


async def test_healthy_follows_discovery_and_essential_failures() -> None:
    """Ready and no failed essential field; optional failures don't count."""
    controller = await make_controller(paired=True)
    pairing = controller.aliases[PAIRING_DATA["AccessoryPairingID"]]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    seen = [(device.ready, device.healthy, _mysa_local_rule(device))]
    assert seen[-1] == (False, False, False)

    await device.async_connect()
    assert device.healthy
    assert device.healthy == _mysa_local_rule(device)

    target = fake_characteristic_address(
        pairing, CharacteristicsTypes.TEMPERATURE_TARGET
    )
    units = fake_characteristic_address(pairing, CharacteristicsTypes.TEMPERATURE_UNITS)
    with (
        patch.object(
            pairing,
            "get_characteristics",
            new=AsyncMock(return_value={target: {"status": -70402}}),
        ),
        pytest.raises(HomeKitCharacteristicReadError),
    ):
        await device.async_read((Field.TARGET_TEMPERATURE,))
    assert device.ready
    assert not device.healthy
    assert device.healthy == _mysa_local_rule(device)

    await device.async_read((Field.TARGET_TEMPERATURE,))
    assert device.healthy

    # Display units are not essential: a failed read keeps the device healthy.
    with patch.object(
        pairing,
        "get_characteristics",
        new=AsyncMock(return_value={units: {"status": -70402}}),
    ):
        await device.async_read((Field.DISPLAY_UNIT,))
    assert Field.DISPLAY_UNIT in device.failed_fields
    assert device.healthy
    assert device.healthy == _mysa_local_rule(device)
    await device.async_close()
