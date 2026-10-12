"""Standalone fake HomeKit accessories shared by protocol tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiohomekit.controller.abstract import FinishPairing
from aiohomekit.model import Accessories
from aiohomekit.model.status_flags import StatusFlags
from aiohomekit.testing import FakeController, FakeDiscovery, FakePairing

FIXTURES = Path(__file__).parent / "fixtures"
HKID = "00:00:00:00:00:00"

PAIRING_DATA = {
    "AccessoryPairingID": HKID,
    "AccessoryLTPK": "00" * 32,
    "iOSPairingId": "11111111-2222-4333-8444-555555555555",
    "iOSDeviceLTSK": "11" * 32,
    "iOSDeviceLTPK": "22" * 32,
    "AccessoryIP": "192.0.2.10",
    "AccessoryIPs": ["192.0.2.10"],
    "AccessoryPort": 57959,
    "Connection": "IP",
}


def load_json(name: str) -> Any:
    """Load a standalone protocol fixture."""
    return json.loads((FIXTURES / name).read_text())


def lite_accessories() -> Accessories:
    """Build the Baseboard Lite accessory database."""
    return Accessories.from_list(load_json("homekit_bb_v2_0_l.json"))


class LiteDescription:
    """What zeroconf would tell us about an unpaired Lite."""

    name = "Mysa-d4e5f6"
    id = HKID
    model = "BB-V2-0-L"
    status_flags = StatusFlags.UNPAIRED
    config_num = 1
    state_num = 1
    category = 9


class LiteDiscovery(FakeDiscovery):
    """An unpaired Lite whose pairing flow returns realistic fake data."""

    def __init__(self, controller: FakeController, accessories: Accessories) -> None:
        """Create a discovery record for the fake Lite."""
        super().__init__(controller, HKID, accessories)
        self.description = LiteDescription()  # type: ignore[assignment]

    async def async_start_pairing(self, alias: str) -> FinishPairing:
        """Return a finisher that validates the code and creates a pairing."""

        async def finish(code: str) -> FakePairing:
            if code != self.pairing_code:
                from aiohomekit.exceptions import AuthenticationError

                raise AuthenticationError("M4")
            pairing = FakePairing(self.controller, dict(PAIRING_DATA), self.accessories)
            pairing.pairing_data = dict(PAIRING_DATA)
            self.controller.aliases[HKID] = pairing
            self.controller.pairings[HKID] = pairing
            return pairing

        return finish


async def make_controller(
    *, paired: bool = True, unpaired: bool = False
) -> FakeController:
    """Build a fake controller with a paired or discoverable Lite."""
    controller = FakeController()
    await controller.async_start()
    if paired:
        pairing = await controller.add_paired_device(lite_accessories(), alias=HKID)
        pairing.pairing_data = dict(PAIRING_DATA)
        controller.aliases[HKID] = pairing
    if unpaired:
        controller.discoveries[HKID] = LiteDiscovery(controller, lite_accessories())
    return controller


def update_fake_characteristic(
    pairing: FakePairing, characteristic_type: str, value: Any
) -> None:
    """Emit a value event for a characteristic in the fake accessory."""
    aid, iid = fake_characteristic_address(pairing, characteristic_type)
    pairing.testing.update_aid_iid([(aid, iid, value)])


def fake_characteristic_address(
    pairing: FakePairing, characteristic_type: str
) -> tuple[int, int]:
    """Find a fake accessory's HAP address by characteristic type."""
    for (aid, iid), characteristic in pairing.testing.characteristics.items():
        if characteristic.type == characteristic_type:
            return aid, iid
    raise AssertionError(f"Fake accessory has no characteristic {characteristic_type}")
