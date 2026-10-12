"""HomeKit metadata refresh, bounded recovery, and capability discovery."""

import asyncio
from unittest.mock import AsyncMock, patch

from aiohomekit.model import Accessories, AccessoriesState
from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.testing import PairingTester
import pytest

from mysa_client.homekit import (
    Field,
    HomeKitCharacteristicReadError,
    HomeKitConnectionError,
    HomeKitProtocol,
    LocalMode,
)
from tests.homekit_helpers import HKID, PAIRING_DATA, make_controller

CLOCK = "mysa_client.homekit.device.monotonic"


def replace_profile(pairing, *, remove=(), offset=100, maximum=28):
    """Replace the advertised database, including IDs, limits, and mode support."""
    profile = pairing.accessories.serialize()
    for accessory in profile:
        for service in accessory["services"]:
            service["characteristics"] = [
                characteristic
                for characteristic in service["characteristics"]
                if characteristic["type"] not in remove
            ]
            for characteristic in service["characteristics"]:
                characteristic["iid"] += offset
                if characteristic["type"] == CharacteristicsTypes.TEMPERATURE_TARGET:
                    characteristic.update(minValue=7, maxValue=maximum, minStep=1)
                if (
                    characteristic["type"]
                    == CharacteristicsTypes.HEATING_COOLING_TARGET
                ):
                    characteristic["valid-values"] = [0, 1, 2]
    pairing._accessories_state = AccessoriesState(Accessories.from_list(profile), 2)
    pairing.testing = PairingTester(pairing, pairing.accessories)


@pytest.mark.parametrize("trigger", ["config", "reconnect", "backstop"])
async def test_refresh_replaces_addresses_values_limits_and_subscriptions(trigger):
    """Refresh remaps addresses and updates semantic capabilities and listeners."""
    controller = await make_controller()
    pairing = controller.aliases[HKID]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    events = []
    device.add_listener(events.append)
    with patch(CLOCK, return_value=0) as clock:
        await device.async_connect()
        old_subscriptions = set(pairing.subscriptions)
        replace_profile(pairing, remove=(CharacteristicsTypes.TEMPERATURE_UNITS,))
        if trigger == "config":
            for listener in tuple(pairing.config_changed_listeners):
                listener(2)
        elif trigger == "reconnect":
            for listener in tuple(pairing.listeners):
                listener({})
        else:
            clock.return_value = 900
        await device.async_read((Field.TARGET_TEMPERATURE,))
        assert device.temperature_range == (7, 28, 1)
        assert device.supported_modes == (LocalMode.OFF, LocalMode.HEAT, LocalMode.COOL)
        assert Field.DISPLAY_UNIT not in device.state
        assert Field.DISPLAY_UNIT not in device.capabilities
        assert not pairing.subscriptions & old_subscriptions
        assert len(pairing.subscriptions) == 4
        assert events[-1].capabilities_changed
        assert len(pairing.listeners) == len(pairing.config_changed_listeners) == 1
        await device.async_write({Field.TARGET_TEMPERATURE: 23})
        assert (await device.async_read((Field.TARGET_TEMPERATURE,)))[
            Field.TARGET_TEMPERATURE
        ] == 23
        # Repeated connect calls do not duplicate listeners or subscriptions.
        await device.async_connect()
        assert len(pairing.listeners) == len(pairing.config_changed_listeners) == 1
        assert len(pairing.subscriptions) == 4
        await device.async_close()
        assert not pairing.listeners
        assert not pairing.config_changed_listeners


async def test_missing_address_refreshes_once_then_obeys_cooldown():
    """A stale address triggers one refresh, then waits for its cooldown."""
    controller = await make_controller()
    pairing = controller.aliases[HKID]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    with patch(CLOCK, return_value=0) as clock:
        await device.async_connect()
        replace_profile(pairing)
        original = pairing.get_characteristics

        async def read(addresses):
            known = {
                (accessory.aid, characteristic.iid)
                for accessory in pairing.accessories
                for service in accessory.services
                for characteristic in service.characteristics
            }
            if not set(addresses) <= known:
                return {address: {"status": -70409} for address in addresses}
            return await original(addresses)

        with patch.object(
            pairing, "get_characteristics", AsyncMock(side_effect=read)
        ) as reads:
            assert await device.async_read((Field.TARGET_TEMPERATURE,))
            assert reads.await_count == 2
        with (
            patch.object(
                pairing, "get_characteristics", AsyncMock(return_value={})
            ) as reads,
            patch.object(
                pairing,
                "async_populate_accessories_state",
                wraps=pairing.async_populate_accessories_state,
            ) as refresh,
        ):
            for _ in range(3):
                with pytest.raises(HomeKitCharacteristicReadError):
                    await device.async_read((Field.TARGET_TEMPERATURE,))
            assert refresh.await_count == 0
            clock.return_value = 60
            with pytest.raises(HomeKitCharacteristicReadError):
                await device.async_read((Field.TARGET_TEMPERATURE,))
            assert refresh.await_count == 1
            assert reads.await_count == 5
    await device.async_close()


async def test_subscription_failure_preserves_polling_and_removed_values():
    """Failed subscription changes leave polling healthy and drop removed data."""
    controller = await make_controller()
    pairing = controller.aliases[HKID]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    await device.async_connect()
    old_subscriptions = set(pairing.subscriptions)
    replace_profile(pairing, remove=(CharacteristicsTypes.TEMPERATURE_UNITS,))
    for listener in tuple(pairing.config_changed_listeners):
        listener(2)
    with (
        patch.object(
            pairing,
            "unsubscribe",
            AsyncMock(side_effect=OSError("offline")),
        ),
        patch.object(
            pairing,
            "subscribe",
            AsyncMock(side_effect=OSError("offline")),
        ),
    ):
        await device.async_read((Field.CURRENT_TEMPERATURE,))
    assert device.ready
    assert Field.DISPLAY_UNIT not in device.state
    assert not pairing.subscriptions & old_subscriptions
    await device.async_close()


async def test_concurrent_refresh_and_internal_reconnect_do_not_loop():
    """Concurrent connect calls share one in-flight metadata refresh."""
    controller = await make_controller()
    pairing = controller.aliases[HKID]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    await device.async_connect()
    for listener in tuple(pairing.config_changed_listeners):
        listener(2)

    async def populate(**_kwargs):
        for listener in tuple(pairing.listeners):
            listener({})
        await asyncio.sleep(0)

    with patch.object(
        pairing, "async_populate_accessories_state", AsyncMock(side_effect=populate)
    ) as refresh:
        await asyncio.gather(*(device.async_connect() for _ in range(4)))
    assert refresh.await_count == 1
    await device.async_close()


async def test_optional_removed_field_read_remaps_without_disabling_device():
    """Removing an optional field makes it absent while keeping the device ready."""
    controller = await make_controller()
    pairing = controller.aliases[HKID]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    await device.async_connect()
    replace_profile(pairing, remove=(CharacteristicsTypes.TEMPERATURE_UNITS,))
    with patch.object(
        pairing, "get_characteristics", AsyncMock(return_value={})
    ) as read:
        assert await device.async_read((Field.DISPLAY_UNIT,)) == {}
    assert read.await_count == 1
    assert device.ready
    assert Field.DISPLAY_UNIT not in device.state
    await device.async_close()


async def test_failed_refresh_is_throttled_and_can_recover():
    """Failed refreshes are throttled, then retried after the cooldown."""
    controller = await make_controller()
    pairing = controller.aliases[HKID]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    pairing.available = False

    with (
        patch(CLOCK, return_value=1) as clock,
        patch.object(
            pairing,
            "async_populate_accessories_state",
            wraps=pairing.async_populate_accessories_state,
        ) as refresh,
    ):
        with pytest.raises(HomeKitConnectionError):
            await device.async_connect()
        for _ in range(3):
            await device.async_connect()
        assert refresh.await_count == 1
        pairing.available = True
        clock.return_value = 61
        await device.async_connect()
        assert refresh.await_count == 2
        assert device.ready
    await device.async_close()
