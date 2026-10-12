"""Reusable cloud observation and command reconciliation helpers.

These helpers do not poll or select transports. Callers pass each observation
source explicitly and decide when to fetch fresh state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from .const import EXPERIMENTAL_CLOUD_FAMILIES, model_family
from .models import DeviceRecord, DeviceState
from .observations import (
    MEASUREMENTS,
    STATE_FIELDS,
    TELEMETRY_FIELDS,
    Observation,
    observe,
    valid_value,
)

_LATEST_CONFIG = frozenset({"heat_setpoint", "mode", "button_lock"})
_PRIORITY = {"S": 3, "R": 2, "L": 1}
_ACTIVE_COMMAND_STATUSES = frozenset({"pending", "ambiguous", "stored"})
DEFAULT_CONFIRMATION_TIMEOUT = timedelta(minutes=2)


def reconcile(
    device_id: str,
    states: Sequence[DeviceState],
    record: DeviceRecord | None,
    previous: DeviceState | None,
    now: datetime,
) -> DeviceState:
    """Select independent reported fields without lending a sibling's clock."""
    if record and model_family(record.model) in EXPERIMENTAL_CLOUD_FAMILIES:
        record = None
    candidates: dict[str, list[Observation]] = {}
    for state in [*states, *([previous] if previous else [])]:
        for name, obs in (state.reported_observations or state.observations).items():
            if obs.usable(now):
                candidates.setdefault(name, []).append(obs)
    selected: dict[str, Observation] = {}
    for name, options in candidates.items():
        reported = [o for o in options if o.kind == "reported"]
        if not reported:
            continue
        timed = [o for o in reported if o.observed_at is not None]
        if (
            name in MEASUREMENTS
            or name == "connected"
            or (name in _LATEST_CONFIG and len(timed) == len(reported))
        ):
            if timed:
                selected[name] = max(
                    timed, key=lambda o: (o.observed_at, _PRIORITY.get(o.source, 0))
                )
        else:
            selected[name] = max(
                reported,
                key=lambda o: (
                    _PRIORITY.get(o.source, 0),
                    o.observed_at or datetime.min.replace(tzinfo=now.tzinfo),
                    o.fetched_at,
                ),
            )
    reported_selected = dict(selected)
    if record:
        stored = {
            "display_unit": record.display_unit,
            "control_type": record.duty_cycle_opt,
            "setpoint_min": record.setpoint_min,
            "setpoint_max": record.setpoint_max,
            "active_brightness": record.raw.get("MaxBrightness"),
            "idle_brightness": record.raw.get("MinBrightness"),
            "button_lock": {"Locked": 3, "Unlocked": 0}.get(
                str(record.raw.get("ButtonState"))
            ),
        }
        for name, value in stored.items():
            if valid_value(name, value) and (
                name == "display_unit" or name not in selected
            ):
                # A cached record is configuration, never a device observation.
                old = previous.observations.get(name) if previous else None
                selected[name] = (
                    old
                    if old and old.kind == "stored" and old.value == value
                    else observe(value, "REC", None, now, kind="stored")
                )
    # Bounds must be selected as a pair from one API source and revision.
    for low, high in (
        ("setpoint_min", "setpoint_max"),
        ("idle_brightness", "active_brightness"),
    ):
        pairs: list[tuple[Observation, Observation]] = []
        for state in [*states, *([previous] if previous else [])]:
            left = state.observations.get(low)
            right = state.observations.get(high)
            if (
                left
                and right
                and left.source == right.source
                and left.usable(now)
                and right.usable(now)
                and left.value <= right.value
            ):
                pairs.append((left, right))
        if record:
            left_value = (
                record.setpoint_min
                if low == "setpoint_min"
                else record.raw.get("MinBrightness")
            )
            right_value = (
                record.setpoint_max
                if high == "setpoint_max"
                else record.raw.get("MaxBrightness")
            )
            if (
                left_value is not None
                and right_value is not None
                and valid_value(low, left_value)
                and valid_value(high, right_value)
                and left_value <= right_value
            ):
                pairs.append(
                    (
                        observe(left_value, "REC", None, now, kind="stored"),
                        observe(right_value, "REC", None, now, kind="stored"),
                    )
                )
        if pairs:
            left, right = max(
                pairs,
                key=lambda pair: (
                    _PRIORITY.get(pair[0].source, 0),
                    pair[0].observed_at or datetime.min.replace(tzinfo=now.tzinfo),
                    pair[0].fetched_at,
                ),
            )
            selected[low], selected[high] = left, right
        else:
            selected.pop(low, None)
            selected.pop(high, None)
    base = next(
        (
            state
            for state in states
            if any(o.source == "S" for o in state.observations.values())
        ),
        previous,
    )
    base = base or DeviceState.from_api(device_id, {})
    values: dict[str, Any] = {
        name: selected[name].value if name in selected else None
        for name in (*STATE_FIELDS, *TELEMETRY_FIELDS, "connected")
    }
    room = selected.get("room_temperature")
    connection = selected.get("connected")
    return replace(
        base,
        **values,
        observations=selected,
        reported_observations=reported_selected,
        reading_time=room.observed_at if room else None,
        last_connected=connection.observed_at if connection else None,
    )


@dataclass(slots=True)
class PendingCommand:
    """An accepted or ambiguous write tracked separately from device state."""

    expected: dict[str, Any]
    submitted_at: datetime
    expires_at: datetime
    baseline: dict[str, Observation]
    correlation_id: str = ""
    evidence: dict[str, Observation] = field(default_factory=dict)
    status: str = "pending"
    confirmation: dict[str, str] = field(default_factory=dict)
    stored_fields: dict[str, Any] = field(default_factory=dict)

    def reconcile(
        self, state: DeviceState | None, record: DeviceRecord | None, now: datetime
    ) -> None:
        """Require a post-submission report; a record can confirm storage only."""
        if self.status not in _ACTIVE_COMMAND_STATUSES:
            return
        for name, expected in self.expected.items():
            obs = state.reported_observations.get(name) if state else None
            old = self.baseline.get(name)
            if (
                obs
                and obs.kind == "reported"
                and obs.value == expected
                and obs.fetched_at >= self.submitted_at
                and obs.observed_at is not None
                and obs.observed_at >= self.submitted_at.replace(microsecond=0)
                and (old is None or old.value != obs.value)
            ):
                self.confirmation[name] = obs.source
                self.evidence[name] = obs
        if self.expected and len(self.confirmation) == len(self.expected):
            self.status = "confirmed"
        elif now >= self.expires_at:
            self.status = "expired"
        elif (
            record
            and self.stored_fields
            and all(
                record.raw.get(key) == value
                for key, value in self.stored_fields.items()
            )
        ):
            self.status = "stored"


def observation_issue_reason(
    name: str,
    observation: Observation,
    command: PendingCommand | None,
    now: datetime,
) -> str | None:
    """Classify an unusable or mismatching observation for diagnostics.

    This is a pure policy helper. Callers own logging, event history, and any
    transport-specific command lookup.
    """
    if not observation.usable(now):
        return "invalid_clock"
    if command and command.status in _ACTIVE_COMMAND_STATUSES:
        if observation.observed_at is None:
            return "missing_timestamp"
        if observation.observed_at < command.submitted_at.replace(microsecond=0):
            return "before_submission"
        if observation.value != command.expected.get(name):
            return "value_mismatch"
    return None


def pending_command(
    expected: Mapping[str, Any],
    state: DeviceState | None,
    now: datetime,
    stored: Mapping[str, Any] | None = None,
    *,
    timeout: timedelta = DEFAULT_CONFIRMATION_TIMEOUT,
) -> PendingCommand:
    """Snapshot the pre-write report and create a confirmation record."""
    baseline = dict(state.reported_observations or state.observations) if state else {}
    command = PendingCommand(
        dict(expected),
        now,
        now + timeout,
        baseline,
        stored_fields=dict(stored or {}),
    )
    command.confirmation = {
        name: "unchanged"
        for name, value in command.expected.items()
        if name in baseline
        and baseline[name].kind == "reported"
        and baseline[name].value == value
    }
    if command.expected and len(command.confirmation) == len(command.expected):
        command.status = "unchanged"
    return command


@dataclass(slots=True)
class PendingCommandTracker:
    """Track field ownership, supersession, and readback transitions per device."""

    pending: dict[str, dict[str, PendingCommand]] = field(default_factory=dict)

    def command_value(
        self, device_id: str, name: str, fallback: Any, now: datetime
    ) -> Any:
        """Return an unexpired command intent for dependent validation only."""
        command = self.pending.get(device_id, {}).get(name)
        if (
            command
            and command.status in (*_ACTIVE_COMMAND_STATUSES, "confirmed", "unchanged")
            and now < command.expires_at
        ):
            return command.expected.get(name, fallback)
        return fallback

    def track(
        self, device_id: str, command: PendingCommand
    ) -> tuple[PendingCommand, ...]:
        """Install a command and supersede prior commands field by field."""
        commands = self.pending.setdefault(device_id, {})
        keys = list(command.expected) or [
            f"record:{key}" for key in command.stored_fields
        ]
        superseded: dict[int, PendingCommand] = {}
        for key in keys:
            previous = commands.get(key)
            if previous and previous.status in _ACTIVE_COMMAND_STATUSES:
                previous.status = "superseded"
                superseded[id(previous)] = previous
            commands[key] = command
        return tuple(superseded.values())

    def reconcile(
        self,
        states: Mapping[str, DeviceState],
        records: Mapping[str, DeviceRecord],
        now: datetime,
    ) -> tuple[tuple[str, PendingCommand, str, dict[str, str]], ...]:
        """Update each unique command and return changed status snapshots."""
        changes: list[tuple[str, PendingCommand, str, dict[str, str]]] = []
        for device_id, commands in self.pending.items():
            seen: set[int] = set()
            for command in commands.values():
                if id(command) in seen:
                    continue
                seen.add(id(command))
                before_status = command.status
                before_confirmation = dict(command.confirmation)
                command.reconcile(states.get(device_id), records.get(device_id), now)
                if (before_status, before_confirmation) != (
                    command.status,
                    command.confirmation,
                ):
                    changes.append(
                        (device_id, command, before_status, before_confirmation)
                    )
        return tuple(changes)
