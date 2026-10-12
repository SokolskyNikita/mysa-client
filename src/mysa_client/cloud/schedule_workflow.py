"""Mysa schedule comparison, replacement, and restart recovery workflows.

The workflow accepts a transport and a tiny journal interface so standalone
callers can use it without Home Assistant. Journal records intentionally keep
the version-1 shape used by the Home Assistant integration.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
import json
import math
from typing import Any, NoReturn, Protocol

from .client import MysaClient
from .commands import ScheduleEventSpec, encode_schedule_events
from .const import Mode


class ScheduleJournal(Protocol):
    """Durable storage for one unfinished schedule transaction."""

    async def async_load(self) -> dict[str, Any] | None:
        """Read the current version-1 transaction, if any."""
        ...

    async def async_save(self, data: dict[str, Any]) -> None:
        """Durably save a transaction before its first write."""
        ...

    async def async_remove(self) -> None:
        """Remove a completed transaction."""
        ...


class ScheduleTransport(Protocol):
    """Cloud operations required to manage schedules."""

    async def get_all_schedules(self, home_id: str) -> list[dict[str, Any]]:
        """Read all legacy and modern schedules for a home."""
        ...

    async def remove_schedule(
        self, schedule: Mapping[str, Any], device_id: str
    ) -> None:
        """Detach a schedule from one device."""
        ...

    async def create_schedule(self, payload: Mapping[str, Any], device_id: str) -> None:
        """Create a modern or restore a native legacy schedule."""
        ...


class MysaScheduleTransport:
    """Adapt a standalone ``MysaClient`` to the workflow transport protocol."""

    def __init__(self, client: MysaClient) -> None:
        """Bind the Mysa client used for schedule operations."""
        self._client = client

    async def get_all_schedules(self, home_id: str) -> list[dict[str, Any]]:
        """Read all legacy and modern schedules for a home."""
        return await self._client.get_all_schedules(home_id)

    async def remove_schedule(
        self, schedule: Mapping[str, Any], device_id: str
    ) -> None:
        """Detach a schedule from one device."""
        await self._client.remove_schedule(schedule, device_id)

    async def create_schedule(self, payload: Mapping[str, Any], device_id: str) -> None:
        """Create a modern schedule or restore a native schedule snapshot."""
        if payload.get("origin"):
            await self._client.restore_native_schedule(payload)
            return
        await self._client.create_schedule(
            home_id=str(payload["homeId"]),
            device_id=device_id,
            device_type=str(payload["deviceType"]),
            events=list(payload["events"]),
            name=str(payload["name"]),
            reusable=bool(payload["reusable"]),
            device_ids=list(payload["deviceIds"]),
        )


class ScheduleWorkflowError(Exception):
    """A user-actionable schedule workflow failure with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        """Create a stable-coded schedule workflow error."""
        super().__init__(message)
        self.code = code
        self.message = message


def _raise_delete_not_confirmed() -> NoReturn:
    """Raise the verification error that starts the replacement rollback."""
    raise ScheduleWorkflowError(
        "schedule-delete-not-confirmed",
        "Mysa still reports an old schedule attached after deleting it.",
    )


def build_schedule_events(
    events: Iterable[Mapping[str, Any]],
    *,
    default_mode: Mode = Mode.HEAT,
    client_key: str = "mysa-client",
) -> list[dict[str, Any]]:
    """Encode semantic weekday events in the cloud schedule representation."""
    return encode_schedule_events(
        [
            ScheduleEventSpec(
                days=tuple(event["days"]),
                at=event["time"],
                mode=Mode[str(event.get("hvac_mode", default_mode.name)).upper()],
                temperature=event.get("temperature"),
                client_key=client_key,
            )
            for event in events
        ]
    )


def schedule_content_matches(
    actual: Mapping[str, Any], expected: Mapping[str, Any]
) -> bool:
    """Compare semantic schedule content across Mysa's GET normalization."""
    if actual.get("origin") or expected.get("origin"):
        return (
            actual.get("origin") == expected.get("origin")
            and actual.get("events") == expected.get("events")
            and actual.get("deviceType") == expected.get("deviceType")
            and actual.get("homeId") == expected.get("homeId")
        )
    actual_name = actual.get("name")
    expected_name = expected.get("name")
    generated_name = (
        isinstance(expected_name, str)
        and isinstance(expected.get("deviceIds"), list)
        and any(
            isinstance(device_id, str) and expected_name == f"Schedule-{device_id}"
            for device_id in expected["deviceIds"]
        )
    )
    names_match = actual_name == expected_name or (
        generated_name and actual_name == "Schedule"
    )
    actual_events = _canonical_schedule_events(actual.get("events"))
    expected_events = _canonical_schedule_events(expected.get("events"))
    return (
        names_match
        and all(
            actual.get(key) == expected.get(key)
            for key in ("homeId", "deviceType", "reusable")
        )
        and actual_events is not None
        and actual_events == expected_events
    )


def _canonical_json_value(value: Any) -> Any:
    """Normalize integral floats recursively without conflating booleans."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else value
    if isinstance(value, list):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical_json_value(item) for key, item in value.items()}
    return value


def _canonical_schedule_events(events: Any) -> tuple[str, ...] | None:
    """Serialize events independent of order, numeric type, or client marker."""
    if not isinstance(events, list):
        return None
    canonical: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            return None
        semantic = _canonical_json_value(
            {key: value for key, value in event.items() if key != "clientKey"}
        )
        day = semantic.get("dayOfWeek")
        if isinstance(day, int) and not isinstance(day, bool):
            semantic["dayOfWeek"] = str(day)
        elif isinstance(day, str):
            with suppress(ValueError):
                semantic["dayOfWeek"] = str(int(day))
        try:
            canonical.append(
                json.dumps(
                    semantic,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
        except (TypeError, ValueError):
            return None
    return tuple(sorted(canonical))


def schedule_matches(
    actual: Mapping[str, Any], expected: Mapping[str, Any], device_ids: list[str]
) -> bool:
    """Compare schedule content and its exact set of attachments."""
    actual_ids = actual.get("deviceIds")
    return (
        schedule_content_matches(actual, expected)
        and isinstance(actual_ids, list)
        and set(actual_ids) == set(device_ids)
    )


def _restorable_schedule(
    schedule: dict[str, Any], home_id: str, device_type: str
) -> dict[str, Any]:
    """Extract a restorable snapshot while retaining native schedule origin."""
    if schedule.get("origin"):
        return deepcopy(schedule)
    return {
        "name": schedule["name"],
        "homeId": str(schedule.get("homeId") or home_id),
        "deviceType": str(schedule.get("deviceType") or device_type),
        "reusable": schedule["reusable"],
        "deviceIds": list(schedule["deviceIds"]),
        "events": deepcopy(schedule["events"]),
    }


def _validate_replaceable(schedules: list[dict[str, Any]]) -> None:
    """Check every existing schedule can be deleted and later recreated."""
    checks: tuple[tuple[Callable[[dict[str, Any]], bool], str, str], ...] = (
        (
            lambda schedule: schedule.get("id") is not None,
            "schedule-id-missing",
            "Mysa returned a schedule without an ID; it can't be safely replaced.",
        ),
        (
            lambda schedule: (
                isinstance(schedule.get("events"), list)
                and all(isinstance(event, dict) for event in schedule["events"])
            ),
            "schedule-events-unrestorable",
            "Mysa returned a schedule that can't be backed up; it wasn't changed.",
        ),
        (
            lambda schedule: (
                isinstance(schedule.get("deviceIds"), list)
                and all(
                    isinstance(device_id, str) for device_id in schedule["deviceIds"]
                )
            ),
            "schedule-device-ids-invalid",
            "Mysa returned a schedule without valid device IDs; it wasn't changed.",
        ),
        (
            lambda schedule: (
                isinstance(schedule.get("name"), str)
                and isinstance(schedule.get("reusable"), bool)
            ),
            "schedule-metadata-unrestorable",
            "Mysa returned a schedule without restorable metadata; it wasn't changed.",
        ),
    )
    for schedule in schedules:
        for check, code, message in checks:
            if not check(schedule):
                raise ScheduleWorkflowError(code, message)


@dataclass(slots=True)
class ScheduleWorkflow:
    """Run durable schedule edits for one Mysa thermostat.

    The caller owns per-device locking and cancellation task management. The
    workflow writes its journal before destructive operations and exposes
    ``async_recover`` for startup, retry, and cancellation recovery.
    """

    transport: ScheduleTransport
    journal: ScheduleJournal
    device_id: str
    home_id: str
    device_type: str
    client_key: str = "mysa-client"

    async def async_recover(self, *, for_clear: bool = False) -> None:
        """Reconcile an interrupted replacement or clear operation."""
        transaction = await self.journal.async_load()
        if not isinstance(transaction, dict):
            return
        operation = transaction.get("operation")
        if operation == "clear":
            await self._async_finish_clear(transaction)
            return
        if operation != "replace":
            raise ScheduleWorkflowError(
                "schedule-backup-unknown",
                "An unfinished Mysa schedule change has an unknown backup format. "
                "Review the Mysa schedule before making another change.",
            )
        if await self._async_restore_replacement(transaction) != "uncertain":
            return
        if for_clear:
            message = (
                "An earlier Mysa schedule change could not be reconciled safely. "
                "Review the schedule in the Mysa app before clearing it."
            )
        else:
            message = (
                "An earlier Mysa schedule change could not be reconciled safely. "
                "Review the schedule in the Mysa app before replacing it."
            )
        raise ScheduleWorkflowError("schedule-recovery-uncertain", message)

    async def async_set(self, events: Iterable[Mapping[str, Any]]) -> None:
        """Replace all schedules attached to this thermostat."""
        encoded_events = build_schedule_events(events, client_key=self.client_key)
        await self.async_set_encoded(encoded_events)

    async def async_set_encoded(self, encoded_events: list[dict[str, Any]]) -> None:
        """Replace schedules using events already encoded by this client."""
        if not encoded_events:
            raise ScheduleWorkflowError(
                "schedule-empty", "A schedule must include at least one event day."
            )
        await self.async_recover()
        existing = await self._async_schedules_for_device()
        _validate_replaceable(existing)
        new_payload: dict[str, Any] = {
            "name": f"Schedule-{self.device_id}",
            "homeId": self.home_id,
            "deviceType": self.device_type,
            "reusable": False,
            "deviceIds": [self.device_id],
            "events": encoded_events,
        }
        # Version-1 journal shape: keep this identical for existing HA stores.
        transaction: dict[str, Any] = {
            "operation": "replace",
            "home_id": self.home_id,
            "device_id": self.device_id,
            "old_schedules": deepcopy(existing),
            "new_schedule": new_payload,
        }
        await self.journal.async_save(transaction)
        try:
            for schedule in existing:
                await self.transport.remove_schedule(schedule, self.device_id)
            actual = await self.transport.get_all_schedules(self.home_id)
            attached = [
                schedule
                for schedule in actual
                if self.device_id in (schedule.get("deviceIds") or [])
            ]
            if attached:
                # The library error triggers transaction recovery below.
                _raise_delete_not_confirmed()
            for schedule in existing:
                actual = await self._async_preserve_other_devices(schedule, actual)
            await self.transport.create_schedule(new_payload, self.device_id)
            await self.journal.async_remove()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            try:
                outcome = await self._async_restore_replacement(transaction)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the journal on any write failure
                outcome = "uncertain"
            if outcome == "completed":
                return
            if outcome == "restored":
                raise
            raise ScheduleWorkflowError(
                "schedule-replacement-failed",
                "Replacing the Mysa schedule failed. The prior schedule is saved "
                "for recovery; review the Mysa app and try again when the cloud "
                "is available.",
            ) from err

    async def async_clear(self) -> None:
        """Delete every schedule attached to this thermostat."""
        await self.async_recover(for_clear=True)
        existing = await self._async_schedules_for_device()
        _validate_replaceable(existing)
        if not existing:
            return
        transaction: dict[str, Any] = {
            "operation": "clear",
            "home_id": self.home_id,
            "device_id": self.device_id,
            "old_schedules": deepcopy(existing),
        }
        await self.journal.async_save(transaction)
        await self._async_finish_clear(transaction)

    async def _async_schedules_for_device(self) -> list[dict[str, Any]]:
        schedules = await self.transport.get_all_schedules(self.home_id)
        return [
            schedule
            for schedule in schedules
            if self.device_id in (schedule.get("deviceIds") or [])
        ]

    async def _async_preserve_other_devices(
        self, schedule: dict[str, Any], all_schedules: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Recreate a detached shared schedule for devices still using it."""
        other_ids = [
            device_id
            for device_id in schedule["deviceIds"]
            if device_id != self.device_id
        ]
        if not other_ids:
            return all_schedules
        found_ids: set[str] = set()
        for actual in all_schedules:
            ids = actual.get("deviceIds")
            if isinstance(ids, list) and schedule_content_matches(actual, schedule):
                found_ids.update(
                    device_id for device_id in ids if device_id in other_ids
                )
        missing_ids = [
            device_id for device_id in other_ids if device_id not in found_ids
        ]
        if not missing_ids:
            return all_schedules
        changed_ids = {
            device_id
            for actual in all_schedules
            if not schedule_content_matches(actual, schedule)
            for device_id in (actual.get("deviceIds") or [])
            if device_id in missing_ids
        }
        if changed_ids:
            raise ScheduleWorkflowError(
                "schedule-shared-device-changed",
                "A shared Mysa schedule disappeared while another device's "
                "schedule changed. The saved schedule was kept for review.",
            )
        payload = _restorable_schedule(schedule, self.home_id, self.device_type)
        payload["deviceIds"] = missing_ids
        await self.transport.create_schedule(payload, self.device_id)
        return await self.transport.get_all_schedules(self.home_id)

    async def _async_restore_replacement(self, transaction: dict[str, Any]) -> str:
        """Restore deleted schedules, retaining the journal on uncertainty."""
        home_id = str(transaction["home_id"])
        old_schedules = transaction["old_schedules"]
        new_payload = transaction["new_schedule"]
        actual = await self.transport.get_all_schedules(home_id)
        attached = [
            schedule
            for schedule in actual
            if self.device_id in (schedule.get("deviceIds") or [])
        ]
        new_is_attached = any(
            schedule_matches(schedule, new_payload, [self.device_id])
            for schedule in attached
        )
        if new_is_attached:
            originals_still_attached = [
                schedule
                for schedule in attached
                if any(
                    str(schedule.get("id")) == str(old.get("id"))
                    and schedule_matches(schedule, old, list(old["deviceIds"]))
                    for old in old_schedules
                )
            ]
            for old in originals_still_attached:
                await self.transport.remove_schedule(old, self.device_id)
            if originals_still_attached:
                actual = await self.transport.get_all_schedules(home_id)
                attached = [
                    schedule
                    for schedule in actual
                    if self.device_id in (schedule.get("deviceIds") or [])
                ]
                if any(
                    schedule_matches(schedule, old, list(old["deviceIds"]))
                    for schedule in attached
                    for old in old_schedules
                ):
                    return "uncertain"
            if any(
                not schedule_matches(schedule, new_payload, [self.device_id])
                and not any(
                    schedule_matches(schedule, old, list(old["deviceIds"]))
                    for old in old_schedules
                )
                for schedule in attached
            ):
                return "uncertain"
            for old in old_schedules:
                actual = await self._async_preserve_other_devices(old, actual)
            await self.journal.async_remove()
            return "completed"

        expected_old = [
            schedule
            for schedule in attached
            if any(
                schedule_matches(schedule, old, list(old["deviceIds"]))
                for old in old_schedules
            )
        ]
        if len(expected_old) != len(attached):
            return "uncertain"

        for old in old_schedules:
            if any(
                schedule_content_matches(schedule, old)
                and self.device_id in (schedule.get("deviceIds") or [])
                for schedule in attached
            ):
                continue
            old_payload = _restorable_schedule(old, home_id, self.device_type)
            other_ids = [
                item for item in old_payload["deviceIds"] if item != self.device_id
            ]
            represented_elsewhere = {
                other_id
                for schedule in actual
                if schedule_content_matches(schedule, old)
                for other_id in (schedule.get("deviceIds") or [])
                if other_id in other_ids
            }
            scheduled_elsewhere = {
                other_id
                for schedule in actual
                for other_id in (schedule.get("deviceIds") or [])
                if other_id in other_ids
            }
            missing_other_ids = [
                other_id
                for other_id in other_ids
                if other_id not in represented_elsewhere
                and other_id not in scheduled_elsewhere
            ]
            old_payload["deviceIds"] = [self.device_id, *missing_other_ids]
            try:
                await self.transport.create_schedule(old_payload, self.device_id)
            except Exception:  # noqa: BLE001 - keep the journal on any restore failure
                return "uncertain"
            actual = await self.transport.get_all_schedules(home_id)
            attached = [
                schedule
                for schedule in actual
                if self.device_id in (schedule.get("deviceIds") or [])
            ]

        if any(
            not any(
                schedule_content_matches(schedule, old)
                and self.device_id in (schedule.get("deviceIds") or [])
                for schedule in attached
            )
            for old in old_schedules
        ):
            return "uncertain"
        await self.journal.async_remove()
        return "restored"

    async def _async_finish_clear(self, transaction: dict[str, Any]) -> None:
        """Finish an interrupted clear without deleting schedules added later."""
        home_id = str(transaction["home_id"])
        old_schedules = transaction["old_schedules"]
        actual = await self.transport.get_all_schedules(home_id)
        attached = [
            schedule
            for schedule in actual
            if self.device_id in (schedule.get("deviceIds") or [])
        ]
        if any(
            not any(
                str(schedule.get("id")) == str(old.get("id"))
                and schedule_matches(schedule, old, list(old["deviceIds"]))
                for old in old_schedules
            )
            for schedule in attached
        ):
            raise ScheduleWorkflowError(
                "schedule-clear-app-edit",
                "A new Mysa schedule appeared while an earlier clear was "
                "interrupted. Review the schedule in the Mysa app before "
                "clearing it again.",
            )
        for schedule in attached:
            await self.transport.remove_schedule(schedule, self.device_id)
        actual = await self.transport.get_all_schedules(home_id)
        attached = [
            schedule
            for schedule in actual
            if self.device_id in (schedule.get("deviceIds") or [])
        ]
        if attached:
            raise ScheduleWorkflowError(
                "schedule-clear-unconfirmed",
                "Mysa still reports a schedule attached after clearing it. "
                "A recovery backup was kept; try again when the cloud is available.",
            )
        for old in old_schedules:
            actual = await self._async_preserve_other_devices(old, actual)
        await self.journal.async_remove()
