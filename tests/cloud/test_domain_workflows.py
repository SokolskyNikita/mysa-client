"""Standalone schedule, account, and hold workflows."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import time
from typing import Any

import pytest

from mysa_client.cloud.account_workflow import (
    async_add_to_account,
    async_remove_from_account,
    can_claim,
    can_pair_locally,
)
from mysa_client.cloud.const import Mode
from mysa_client.cloud.exceptions import MysaConnectionError
from mysa_client.cloud.holds import async_apply_hold, resolve_hold
from mysa_client.cloud.schedule_workflow import (
    ScheduleWorkflow,
    ScheduleWorkflowError,
    schedule_content_matches,
)

DEVICE = "0a0b0c0d0e01"
OTHER = "0a0b0c0d0e02"
HOME = "home-1"


class MemoryJournal:
    """Simple journal that records the data passed to save."""

    def __init__(self) -> None:
        """Start with an empty journal."""
        self.data: dict[str, Any] | None = None
        self.saved: list[dict[str, Any]] = []

    async def async_load(self) -> dict[str, Any] | None:
        """Return a copy of the current record."""
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        """Save a copy and record the v1 shape."""
        self.data = deepcopy(data)
        self.saved.append(deepcopy(data))

    async def async_remove(self) -> None:
        """Remove the current record."""
        self.data = None


def _schedule(
    device_ids: list[str] | None = None, *, name: str = "Original schedule"
) -> dict[str, Any]:
    return {
        "id": "saved",
        "name": name,
        "homeId": HOME,
        "deviceType": "BB",
        "reusable": bool(device_ids and len(device_ids) > 1),
        "deviceIds": device_ids or [DEVICE],
        "events": [
            {
                "executionTime": "06:00:00",
                "dayOfWeek": "1",
                "clientKey": "Mysa-app",
                "state": {"modes": {"mode": 4}, "targetHeat": {"setpoint": 20}},
            }
        ],
    }


class MemoryScheduleTransport:
    """In-memory schedule API with controls for ambiguous outcomes."""

    def __init__(self, schedules: list[dict[str, Any]]) -> None:
        """Seed the fake cloud with schedule records."""
        self.schedules = deepcopy(schedules)
        self.next_create_error: Exception | None = None
        self.commit_then_error = False
        self.app_edit_on_create_error = False
        self.removed = asyncio.Event()
        self.pause_after_remove: asyncio.Event | None = None

    async def get_all_schedules(self, home_id: str) -> list[dict[str, Any]]:
        """Return current schedules."""
        return deepcopy(self.schedules)

    async def remove_schedule(self, schedule: dict[str, Any], device_id: str) -> None:
        """Remove or detach a schedule from its target device."""
        for index, current in enumerate(self.schedules):
            if current.get("id") != schedule.get("id"):
                continue
            ids = list(current.get("deviceIds") or [])
            if len(ids) > 1:
                current["deviceIds"] = [item for item in ids if item != device_id]
            else:
                self.schedules.pop(index)
            break
        self.removed.set()
        if self.pause_after_remove is not None:
            await self.pause_after_remove.wait()

    async def create_schedule(self, payload: dict[str, Any], device_id: str) -> None:
        """Create a schedule, optionally simulating a lost response."""
        error = self.next_create_error
        self.next_create_error = None
        if error is not None and not self.commit_then_error:
            if self.app_edit_on_create_error:
                self.schedules.append(
                    {
                        "id": "app-edit",
                        "name": "Edited in Mysa app",
                        "homeId": HOME,
                        "deviceType": "BB",
                        "reusable": False,
                        "deviceIds": [device_id],
                        "events": [],
                    }
                )
            raise error
        created = deepcopy(dict(payload))
        created["id"] = f"schedule-{len(self.schedules) + 1}"
        self.schedules.append(created)
        if error is not None:
            self.commit_then_error = False
            raise error


def _workflow(
    transport: MemoryScheduleTransport, journal: MemoryJournal
) -> ScheduleWorkflow:
    return ScheduleWorkflow(
        transport=transport,
        journal=journal,
        device_id=DEVICE,
        home_id=HOME,
        device_type="BB",
        client_key="HomeAssistant-test",
    )


def test_schedule_comparison_accepts_get_normalization() -> None:
    """Canonicalization accepts numeric and order changes without hiding edits."""
    expected = {
        **_schedule(),
        "name": f"Schedule-{DEVICE}",
        "events": [
            {
                "executionTime": "06:00:00",
                "dayOfWeek": "1",
                "clientKey": "HomeAssistant-test",
                "state": {"modes": {"mode": 4}, "targetHeat": {"setpoint": 20.0}},
            }
        ],
    }
    actual = deepcopy(expected)
    actual["name"] = "Schedule"
    actual["events"][0]["dayOfWeek"] = 1
    actual["events"][0]["clientKey"] = "v2-migration"
    actual["events"][0]["state"]["targetHeat"]["setpoint"] = 20
    assert schedule_content_matches(actual, expected)
    actual["events"][0]["state"]["targetHeat"]["setpoint"] = True
    assert not schedule_content_matches(actual, expected)


async def test_schedule_replacement_preserves_shared_attachment_and_journal_shape() -> (
    None
):
    """A successful replacement preserves the other device and v1 record shape."""
    transport = MemoryScheduleTransport([_schedule([DEVICE, OTHER])])
    journal = MemoryJournal()
    workflow = _workflow(transport, journal)
    await workflow.async_set(
        [{"days": ["tue"], "time": time(7, 30), "temperature": 21}]
    )

    assert journal.data is None
    assert set(journal.saved[0]) == {
        "operation",
        "home_id",
        "device_id",
        "old_schedules",
        "new_schedule",
    }
    other_schedule = next(s for s in transport.schedules if OTHER in s["deviceIds"])
    target_schedule = next(s for s in transport.schedules if DEVICE in s["deviceIds"])
    assert other_schedule["deviceIds"] == [OTHER]
    assert other_schedule["events"] == _schedule([DEVICE, OTHER])["events"]
    assert target_schedule["events"][0]["dayOfWeek"] == "2"


async def test_clear_keeps_shared_schedule_for_other_devices() -> None:
    """Clearing one thermostat retains shared schedule content and attachment."""
    transport = MemoryScheduleTransport([_schedule([DEVICE, OTHER])])
    journal = MemoryJournal()
    await _workflow(transport, journal).async_clear()

    assert journal.data is None
    assert journal.saved[0]["operation"] == "clear"
    assert set(journal.saved[0]) == {
        "operation",
        "home_id",
        "device_id",
        "old_schedules",
    }
    assert len(transport.schedules) == 1
    assert transport.schedules[0]["deviceIds"] == [OTHER]


async def test_interrupted_clear_does_not_remove_a_later_app_edit() -> None:
    """Clear recovery keeps a newly added Mysa schedule for user review."""
    old = _schedule()
    app_edit = _schedule(name="Edited in Mysa app")
    app_edit["id"] = "app-edit"
    journal = MemoryJournal()
    await journal.async_save(
        {
            "operation": "clear",
            "home_id": HOME,
            "device_id": DEVICE,
            "old_schedules": [old],
        }
    )
    transport = MemoryScheduleTransport([old, app_edit])
    with pytest.raises(ScheduleWorkflowError) as err:
        await _workflow(transport, journal).async_recover()

    assert err.value.code == "schedule-clear-app-edit"
    assert journal.data is not None
    assert transport.schedules == [old, app_edit]


async def test_lost_create_response_is_reconciled_as_success() -> None:
    """A committed create with a lost response is kept without a duplicate."""
    transport = MemoryScheduleTransport([_schedule()])
    transport.next_create_error = MysaConnectionError("response lost")
    transport.commit_then_error = True
    journal = MemoryJournal()

    await _workflow(transport, journal).async_set(
        [{"days": ["tue"], "time": time(7, 30), "temperature": 21}]
    )

    assert journal.data is None
    assert len([s for s in transport.schedules if DEVICE in s["deviceIds"]]) == 1


async def test_app_edit_is_kept_when_replacement_recovery_is_uncertain() -> None:
    """Recovery leaves a new app schedule untouched and retains its journal."""
    transport = MemoryScheduleTransport([_schedule()])
    transport.next_create_error = MysaConnectionError("replacement failed")
    transport.app_edit_on_create_error = True
    journal = MemoryJournal()

    with pytest.raises(ScheduleWorkflowError) as err:
        await _workflow(transport, journal).async_set(
            [{"days": ["tue"], "time": time(7, 30), "temperature": 21}]
        )
    assert err.value.code == "schedule-replacement-failed"

    assert journal.data is not None
    assert any(schedule["id"] == "app-edit" for schedule in transport.schedules)


async def test_cancelled_delete_leaves_recoverable_journal() -> None:
    """Cancellation after a delete leaves enough state to restore the old plan."""
    transport = MemoryScheduleTransport([_schedule()])
    transport.pause_after_remove = asyncio.Event()
    journal = MemoryJournal()
    workflow = _workflow(transport, journal)
    task = asyncio.create_task(
        workflow.async_set([{"days": ["tue"], "time": time(7, 30), "temperature": 21}])
    )
    await transport.removed.wait()
    assert journal.data is not None
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("the schedule request should preserve cancellation")

    await workflow.async_recover()
    assert journal.data is None
    assert len(transport.schedules) == 1
    assert transport.schedules[0]["events"] == _schedule()["events"]


class MemoryAccountClient:
    """Small fake for account enrollment and cleanup tests."""

    def __init__(self) -> None:
        """Start with a schedule and a shortcut referencing the device."""
        self.add_args: dict[str, Any] | None = None
        self.schedules = [{"deviceIds": [DEVICE]}]
        self.shortcuts = [
            {
                "Id": "shortcut-1",
                "AllDevices": True,
                "DeviceActions": {DEVICE: {"mode": 4}, OTHER: {"mode": 0}},
            }
        ]
        self.shortcut_update: tuple[str, dict[str, Any], bool] | None = None
        self.deleted = False
        self.fail_schedule_cleanup = False

    async def add_device(self, device_id: str, **kwargs: Any) -> bool:
        """Record account claim arguments."""
        self.add_args = {"device_id": device_id, **kwargs}
        return True

    async def get_all_schedules(self, home_id: str) -> list[dict[str, Any]]:
        """Return fake account schedules."""
        return self.schedules

    async def remove_schedule(self, schedule: dict[str, Any], device_id: str) -> None:
        """Remove a schedule or simulate a connectivity failure."""
        if self.fail_schedule_cleanup:
            raise MysaConnectionError("offline")
        self.schedules.remove(schedule)

    async def get_shortcuts(self, home_id: str) -> list[dict[str, Any]]:
        """Return fake account shortcuts."""
        return self.shortcuts

    async def update_shortcut(
        self,
        shortcut_id: str,
        device_actions: dict[str, Any],
        *,
        all_devices: bool = False,
    ) -> None:
        """Record the updated shortcut contents."""
        self.shortcut_update = (shortcut_id, device_actions, all_devices)

    async def delete_device(self, device_id: str) -> None:
        """Record device deletion."""
        self.deleted = True


def test_account_facts_and_claim_preserve_settings() -> None:
    """Provisioning uses client facts and the app-compatible small claim body."""
    assert can_claim(DEVICE, "BB-V2-0")
    assert not can_claim("not-a-mac", "BB-V2-0")
    assert can_pair_locally("BB-V2-0")
    assert not can_pair_locally("BB-V3-0")

    client = MemoryAccountClient()
    assert asyncio.run(
        async_add_to_account(
            client,
            device_id=DEVICE,
            model="BB-V2-0",
            name="Hallway",
            home_id=HOME,
            time_zone="America/Los_Angeles",
        )
    )
    assert client.add_args == {
        "device_id": DEVICE,
        "home_id": HOME,
        "model": "BB-V2-0",
        "name": "Hallway",
        "time_zone": "America/Los_Angeles",
        "app_defaults": False,
    }


async def test_account_removal_cleans_shortcuts_and_returns_best_effort_failures() -> (
    None
):
    """Cleanup results are reported while device removal still proceeds."""
    client = MemoryAccountClient()
    result = await async_remove_from_account(client, DEVICE, HOME)
    assert result.complete
    assert result.schedules_removed == 1
    assert result.shortcuts_updated == 1
    assert client.shortcut_update == (
        "shortcut-1",
        {OTHER: {"mode": 0}},
        True,
    )
    assert client.deleted

    client = MemoryAccountClient()
    client.fail_schedule_cleanup = True
    result = await async_remove_from_account(client, DEVICE, HOME)
    assert not result.complete
    assert result.error_type == "MysaConnectionError"
    assert client.deleted


async def test_hold_resolution_and_application_preserve_explicit_off() -> None:
    """The cloud hold and immediate writes use one resolved mode/setpoint."""
    command = resolve_hold(
        requested_mode=Mode.OFF,
        current_mode=Mode.HEAT,
        requested_temperature=19,
        current_temperature=20,
    )
    assert command.state == {"modes": {"mode": 0}}
    writes: list[Any] = []

    async def set_hold(state: Any, until: Any) -> None:
        writes.append(("hold", state, until))

    async def set_mode(mode: Mode) -> None:
        writes.append(("mode", mode))

    async def set_temperature(temperature: float) -> None:
        writes.append(("temperature", temperature))

    await async_apply_hold(
        command,
        set_hold=set_hold,
        current_mode=lambda: Mode.HEAT,
        current_temperature=lambda: 20,
        set_mode=set_mode,
        set_temperature=set_temperature,
    )
    assert writes == [("hold", {"modes": {"mode": 0}}, None), ("mode", Mode.OFF)]
