"""Reusable account enrollment and cleanup workflows for Mysa devices."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any, Protocol

from .const import WIFI_ONLY_FAMILIES, model_family
from .exceptions import MysaError

_MAC_ID = re.compile(r"[0-9a-f]{12}")


class AccountClient(Protocol):
    """Cloud operations needed by account workflows."""

    async def add_device(
        self,
        device_id: str,
        *,
        home_id: str,
        model: str,
        time_zone: str,
        name: str,
        app_defaults: bool = True,
    ) -> bool:
        """Claim a device using the supplied account metadata."""
        ...

    async def get_all_schedules(self, home_id: str) -> list[dict[str, Any]]:
        """Read all schedule formats attached to a home."""
        ...

    async def remove_schedule(
        self, schedule: Mapping[str, Any], device_id: str
    ) -> None:
        """Detach one schedule from the device."""
        ...

    async def get_shortcuts(self, home_id: str) -> list[dict[str, Any]]:
        """Read a home's shortcuts."""
        ...

    async def update_shortcut(
        self,
        shortcut_id: str,
        device_actions: Mapping[str, Any],
        *,
        all_devices: bool,
    ) -> None:
        """Replace the device actions for a shortcut."""
        ...

    async def delete_device(self, device_id: str) -> None:
        """Remove the device from the account."""
        ...


@dataclass(frozen=True, slots=True)
class AccountCleanupResult:
    """Best-effort schedule and shortcut cleanup performed before removal."""

    schedules_removed: int = 0
    shortcuts_updated: int = 0
    error_type: str | None = None

    @property
    def complete(self) -> bool:
        """Whether all requested cleanup operations completed."""
        return self.error_type is None


def can_claim(device_id: str, model: str | None) -> bool:
    """Whether the client can provision this Wi-Fi-only device by its MAC ID."""
    return bool(_MAC_ID.fullmatch(device_id)) and (
        model_family(model) in WIFI_ONLY_FAMILIES
    )


def can_pair_locally(model: str | None) -> bool:
    """Whether a model supports the HomeKit local protocol used by this client."""
    return model_family(model) in WIFI_ONLY_FAMILIES


async def async_add_to_account(
    client: AccountClient,
    *,
    device_id: str,
    model: str | None,
    name: str,
    home_id: str,
    time_zone: str,
) -> bool:
    """Use Mysa's small claim body so saved thermostat settings stay intact."""
    return await client.add_device(
        device_id,
        home_id=home_id,
        model=model or "",
        time_zone=time_zone,
        name=name,
        app_defaults=False,
    )


async def async_remove_from_account(
    client: AccountClient, device_id: str, home_id: str | None
) -> AccountCleanupResult:
    """Remove account references best-effort, then remove the thermostat itself.

    Schedule and shortcut cleanup mirrors the Mysa app. A cleanup error is
    returned for the caller to report, while account removal remains required.
    A failure deleting the device is allowed to propagate.
    """
    result = AccountCleanupResult()
    if home_id:
        schedule_count = 0
        shortcut_count = 0
        try:
            for schedule in await client.get_all_schedules(home_id):
                if device_id in (schedule.get("deviceIds") or []):
                    await client.remove_schedule(schedule, device_id)
                    schedule_count += 1
            for shortcut in await client.get_shortcuts(home_id):
                actions = shortcut.get("DeviceActions") or {}
                if isinstance(actions, dict) and device_id in actions:
                    await client.update_shortcut(
                        str(shortcut.get("Id")),
                        {
                            key: value
                            for key, value in actions.items()
                            if key != device_id
                        },
                        all_devices=bool(shortcut.get("AllDevices")),
                    )
                    shortcut_count += 1
        except MysaError as err:
            result = AccountCleanupResult(
                schedules_removed=schedule_count,
                shortcuts_updated=shortcut_count,
                error_type=type(err).__name__,
            )
        else:
            result = AccountCleanupResult(schedule_count, shortcut_count)
    await client.delete_device(device_id)
    return result
