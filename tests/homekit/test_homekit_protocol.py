"""Behavioral tests for the isolated HomeKit protocol boundary."""

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohomekit.model.characteristics import CharacteristicsTypes
import pytest

from mysa_client.homekit import (
    Field,
    HomeKitCharacteristicReadError,
    HomeKitProtocol,
    HomeKitWriteRejectedError,
    LocalAction,
    LocalMode,
    PairingSession,
    pairing_identity,
    parse_advertisement,
    update_pairing_endpoint,
)
from tests.homekit_helpers import (
    PAIRING_DATA,
    fake_characteristic_address,
    make_controller,
)


async def test_device_reads_and_writes_semantic_values() -> None:
    """Wire enums and characteristic addresses stay behind HomeKitDevice."""
    controller = await make_controller(paired=True)
    protocol = HomeKitProtocol.from_controller(controller)
    device = protocol.load_pairing(PAIRING_DATA)
    events = []
    unsubscribe = device.add_listener(events.append)

    await device.async_connect()

    assert device.ready
    assert device.state[Field.CURRENT_TEMPERATURE] == pytest.approx(21.4)
    assert device.state[Field.TARGET_TEMPERATURE] == pytest.approx(21.5)
    assert device.state[Field.CURRENT_ACTION] == LocalAction.HEATING
    assert device.state[Field.TARGET_MODE] == LocalMode.HEAT
    assert device.state[Field.DISPLAY_UNIT] == "C"
    assert device.supported_modes == (LocalMode.OFF, LocalMode.HEAT)
    assert device.temperature_range == (5, 30, 0.5)

    await device.async_write(
        {
            Field.TARGET_MODE: LocalMode.OFF,
            Field.TARGET_TEMPERATURE: 19.5,
            Field.DISPLAY_UNIT: "F",
        }
    )

    assert device.state[Field.TARGET_MODE] == LocalMode.OFF
    assert device.state[Field.TARGET_TEMPERATURE] == pytest.approx(19.5)
    assert device.state[Field.DISPLAY_UNIT] == "F"
    assert events[-1].values == {
        Field.TARGET_MODE: LocalMode.OFF,
        Field.TARGET_TEMPERATURE: 19.5,
        Field.DISPLAY_UNIT: "F",
    }
    assert {
        Field.TARGET_MODE,
        Field.TARGET_TEMPERATURE,
        Field.DISPLAY_UNIT,
    } <= set().union(*(event.changed_fields for event in events))
    diagnostics = device.diagnostics()
    assert diagnostics["fields"][Field.TARGET_MODE.value]["valid_values"] == (
        LocalMode.OFF,
        LocalMode.HEAT,
    )
    assert diagnostics["fields"][Field.TARGET_MODE.value]["min_value"] is None
    assert "AccessoryPairingID" not in str(diagnostics)
    assert "serial" not in diagnostics
    unsubscribe()
    await device.async_close()


async def test_unknown_wire_enums_are_not_exposed() -> None:
    """Unknown HAP enum values become unknown semantic state, never raw ints."""
    controller = await make_controller(paired=True)
    pairing = controller.aliases[PAIRING_DATA["AccessoryPairingID"]]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    await device.async_connect()
    target_address = fake_characteristic_address(
        pairing, CharacteristicsTypes.HEATING_COOLING_TARGET
    )
    display_address = fake_characteristic_address(
        pairing, CharacteristicsTypes.TEMPERATURE_UNITS
    )

    async def invalid_values(
        _addresses: list[tuple[int, int]],
    ) -> Mapping[tuple[int, int], dict[str, Any]]:
        return {
            target_address: {"value": 99},
            display_address: {"value": 99},
        }

    with patch.object(
        pairing, "get_characteristics", new=AsyncMock(side_effect=invalid_values)
    ):
        values = await device.async_read((Field.TARGET_MODE, Field.DISPLAY_UNIT))

    assert values == {Field.TARGET_MODE: None, Field.DISPLAY_UNIT: None}
    assert device.state[Field.TARGET_MODE] is None
    assert device.state[Field.DISPLAY_UNIT] is None
    with pytest.raises(HomeKitWriteRejectedError):
        await device.async_write({Field.TARGET_MODE: "mystery"})
    await device.async_close()


async def test_required_read_failures_report_semantic_fields() -> None:
    """The protocol reports missing values without exposing aid/iid pairs."""
    controller = await make_controller(paired=True)
    pairing = controller.aliases[PAIRING_DATA["AccessoryPairingID"]]
    device = HomeKitProtocol.from_controller(controller).load_pairing(PAIRING_DATA)
    await device.async_connect()
    target_address = fake_characteristic_address(
        pairing, CharacteristicsTypes.TEMPERATURE_TARGET
    )

    with (
        patch.object(
            pairing,
            "get_characteristics",
            new=AsyncMock(return_value={target_address: {"status": -70402}}),
        ),
        pytest.raises(HomeKitCharacteristicReadError) as error,
    ):
        await device.async_read((Field.TARGET_TEMPERATURE,))

    assert error.value.fields == {Field.TARGET_TEMPERATURE}
    assert str(target_address) not in str(error.value)
    assert device.failed_fields == {Field.TARGET_TEMPERATURE: "-70402"}
    await device.async_read((Field.TARGET_TEMPERATURE,))
    assert not device.failed_fields
    await device.async_close()


def test_advertisement_and_pairing_helpers_hide_txt_and_endpoint_keys() -> None:
    """Advertisement text and endpoint key names are protocol details."""
    advertisement = parse_advertisement(
        "Mysa-d4e5f6._hap._tcp.local.",
        {b"id": b"AA:BB:CC:DD:EE:FF", b"md": b"BB-V2-0-L", b"sf": b"0"},
        host="192.0.2.9",
        addresses=("192.0.2.9", "fe80::1"),
        port=57959,
    )

    assert advertisement is not None
    assert advertisement.id == "aa:bb:cc:dd:ee:ff"
    assert advertisement.name == "Mysa-d4e5f6"
    assert advertisement.paired
    assert advertisement.endpoint.addresses == ("192.0.2.9",)
    updated = update_pairing_endpoint(PAIRING_DATA, advertisement.endpoint)
    assert updated["AccessoryIP"] == "192.0.2.9"
    assert updated["AccessoryPort"] == 57959
    assert pairing_identity(updated) == pairing_identity(PAIRING_DATA)
    assert "AccessoryIP" not in pairing_identity(updated)


async def test_pairing_persists_before_accessory_info_read() -> None:
    """The callback durably records accepted keys before another HAP request."""
    controller = await make_controller(paired=True)
    pairing = controller.aliases[PAIRING_DATA["AccessoryPairingID"]]
    events: list[str] = []
    original_list = pairing.list_accessories_and_characteristics

    async def list_accessories() -> Any:
        events.append("read")
        return await original_list()

    async def save(_pairing: Mapping[str, Any]) -> None:
        events.append("save")
        assert events == ["save"]

    with patch.object(
        pairing, "list_accessories_and_characteristics", list_accessories
    ):
        session = PairingSession(lambda _pin: _return(pairing))
        data, info = await session.async_complete("111-22-333", on_pairing_saved=save)

    assert events == ["save", "read"]
    assert data == PAIRING_DATA
    assert info.model == "BB-V2-0-L"


async def test_pairing_storage_error_rolls_back_and_preserves_original_error() -> None:
    """Failed credential storage removes the accepted key before returning."""
    controller = await make_controller(paired=True)
    pairing = controller.aliases[PAIRING_DATA["AccessoryPairingID"]]
    removed: list[str] = []

    async def remove_pairing(pairing_id: str) -> None:
        removed.append(pairing_id)

    async def fail_to_save(_pairing: Mapping[str, Any]) -> None:
        raise OSError("storage is read-only")

    with patch.object(pairing, "remove_pairing", remove_pairing):
        session = PairingSession(lambda _pin: _return(pairing))
        with pytest.raises(OSError, match="storage is read-only"):
            await session.async_complete("111-22-333", on_pairing_saved=fail_to_save)

    assert removed == [PAIRING_DATA["iOSPairingId"]]


async def test_pairing_storage_callback_is_shielded_from_cancellation() -> None:
    """A cancellation waits for persistence and does not start accessory reads."""
    controller = await make_controller(paired=True)
    pairing = controller.aliases[PAIRING_DATA["AccessoryPairingID"]]
    save_started = asyncio.Event()
    release_save = asyncio.Event()
    reads: list[str] = []
    original_list = pairing.list_accessories_and_characteristics

    async def list_accessories() -> Any:
        reads.append("read")
        return await original_list()

    async def save(_pairing: Mapping[str, Any]) -> None:
        save_started.set()
        await release_save.wait()

    with patch.object(
        pairing, "list_accessories_and_characteristics", list_accessories
    ):
        session = PairingSession(lambda _pin: _return(pairing))
        task = asyncio.create_task(
            session.async_complete("111-22-333", on_pairing_saved=save)
        )
        await save_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release_save.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert reads == []


async def _return(value: Any) -> Any:
    return value


async def test_protocol_owns_only_missing_zeroconf_browsers(monkeypatch) -> None:
    """Standalone startup adds both HAP browsers and releases only those."""
    import mysa_client.homekit.controller as controller_module

    class FakeBrowser:
        def __init__(self, service_type: str) -> None:
            self.types = {service_type}

    class FakeAsyncZeroconf:
        def __init__(self) -> None:
            self.zeroconf = SimpleNamespace(listeners=[])
            self.async_browsers = {}

        async def async_add_service_listener(self, service_type, listener) -> None:
            browser = FakeBrowser(service_type)
            self.zeroconf.listeners.append(browser)
            self.async_browsers[listener] = browser

        async def async_remove_service_listener(self, listener) -> None:
            browser = self.async_browsers.pop(listener)
            self.zeroconf.listeners.remove(browser)

    class FakeController:
        def __init__(self, async_zeroconf) -> None:
            self.async_zeroconf = async_zeroconf
            self.stopped = False

        async def async_start(self) -> None:
            types = {
                service_type
                for browser in self.async_zeroconf.zeroconf.listeners
                for service_type in browser.types
            }
            assert types == set(controller_module._HAP_SERVICE_TYPES)

        async def async_stop(self) -> None:
            self.stopped = True
            assert len(self.async_zeroconf.zeroconf.listeners) == 2

    monkeypatch.setattr(controller_module, "AsyncServiceBrowser", FakeBrowser)
    async_zeroconf = FakeAsyncZeroconf()
    controller = FakeController(async_zeroconf)
    protocol = HomeKitProtocol(async_zeroconf, controller=controller)

    await protocol.async_start()
    assert len(async_zeroconf.zeroconf.listeners) == 2
    await protocol.async_stop()

    assert controller.stopped
    assert async_zeroconf.zeroconf.listeners == []
    assert async_zeroconf.async_browsers == {}


async def test_protocol_preserves_shared_zeroconf_browsers(monkeypatch) -> None:
    """HA-owned HAP browsers are reused and are not removed by this wrapper."""
    import mysa_client.homekit.controller as controller_module

    class FakeBrowser:
        def __init__(self, service_type: str) -> None:
            self.types = {service_type}

    class FakeAsyncZeroconf:
        def __init__(self) -> None:
            self.zeroconf = SimpleNamespace(
                listeners=[
                    FakeBrowser(service_type)
                    for service_type in controller_module._HAP_SERVICE_TYPES
                ]
            )
            self.async_browsers = {}

        async def async_add_service_listener(self, _service_type, _listener) -> None:
            raise AssertionError("existing HomeKit browsers must be reused")

        async def async_remove_service_listener(self, _listener) -> None:
            raise AssertionError("caller-owned HomeKit browsers must be preserved")

    class FakeController:
        async def async_start(self) -> None:
            return

        async def async_stop(self) -> None:
            return

    monkeypatch.setattr(controller_module, "AsyncServiceBrowser", FakeBrowser)
    async_zeroconf = FakeAsyncZeroconf()
    caller_browsers = list(async_zeroconf.zeroconf.listeners)
    protocol = HomeKitProtocol(async_zeroconf, controller=FakeController())

    await protocol.async_start()
    await protocol.async_stop()

    assert async_zeroconf.zeroconf.listeners == caller_browsers


async def test_protocol_start_failure_cleans_up_only_owned_browsers(
    monkeypatch,
) -> None:
    """A failed backend start leaves shared Zeroconf listeners intact."""
    import mysa_client.homekit.controller as controller_module

    class FakeBrowser:
        def __init__(self, service_type: str) -> None:
            self.types = {service_type}

    class FakeAsyncZeroconf:
        def __init__(self) -> None:
            self.zeroconf = SimpleNamespace(
                listeners=[FakeBrowser(controller_module.HAP_TYPE_TCP)]
            )
            self.async_browsers = {}

        async def async_add_service_listener(self, service_type, listener) -> None:
            browser = FakeBrowser(service_type)
            self.zeroconf.listeners.append(browser)
            self.async_browsers[listener] = browser

        async def async_remove_service_listener(self, listener) -> None:
            browser = self.async_browsers.pop(listener)
            self.zeroconf.listeners.remove(browser)

    class FakeController:
        def __init__(self) -> None:
            self.stopped = False

        async def async_start(self) -> None:
            raise RuntimeError("backend start failed")

        async def async_stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr(controller_module, "AsyncServiceBrowser", FakeBrowser)
    async_zeroconf = FakeAsyncZeroconf()
    caller_browser = async_zeroconf.zeroconf.listeners[0]
    controller = FakeController()
    protocol = HomeKitProtocol(async_zeroconf, controller=controller)

    with pytest.raises(RuntimeError, match="backend start failed"):
        await protocol.async_start()

    assert controller.stopped
    assert async_zeroconf.zeroconf.listeners == [caller_browser]
    assert async_zeroconf.async_browsers == {}
