"""Standalone tests for Mysa observation and pending-command semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mysa_client.cloud import DeviceRecord, DeviceState
from mysa_client.cloud.observations import observe
from mysa_client.cloud.reconciliation import (
    PendingCommandTracker,
    observation_issue_reason,
    pending_command,
    reconcile,
)

DEVICE_ID = "a1b2c3d4e5f6"


def _structured(now: datetime, **sections: object) -> DeviceState:
    return DeviceState.from_api(
        DEVICE_ID,
        {
            "latestTelemetry": {
                "isConnected": True,
                "lastConnected": now.timestamp(),
                "reading": {
                    "timestamp": now.timestamp(),
                    "roomTemperature": 21,
                    "humidity": 40,
                },
            },
            **sections,
        },
    )


def _legacy(now: datetime, **fields: object) -> DeviceState:
    return DeviceState.from_legacy(
        DEVICE_ID,
        {"CorrectedTemp": {"v": 22, "t": now.timestamp()}, **fields},
    )


def test_reconcile_keeps_per_field_clocks_and_rejects_future_values() -> None:
    now = datetime.now(UTC)
    state = reconcile(
        DEVICE_ID,
        [
            _structured(now),
            _legacy(
                now + timedelta(seconds=1),
                Humidity={"v": 50, "t": (now - timedelta(minutes=3)).timestamp()},
            ),
        ],
        None,
        None,
        now,
    )
    assert state.room_temperature == 22
    assert state.humidity == 40
    assert state.observations["room_temperature"].source == "R"

    future = _legacy(now + timedelta(days=1), Humidity={"v": 99, "t": "bad"})
    kept = reconcile(DEVICE_ID, [future], None, state, now + timedelta(minutes=2))
    assert kept.room_temperature == 22
    assert (
        kept.observations["room_temperature"] == state.observations["room_temperature"]
    )
    assert kept.humidity == 40
    missing = reconcile(DEVICE_ID, [], None, kept, now + timedelta(hours=1))
    assert missing.room_temperature == 22
    assert not missing.observations["room_temperature"].fresh(now + timedelta(hours=1))


def test_unknown_measurement_clock_cannot_replace_known_value() -> None:
    now = datetime.now(UTC)
    known = _structured(now)
    unknown = DeviceState.from_legacy(DEVICE_ID, {"CorrectedTemp": {"v": 99}})
    state = reconcile(DEVICE_ID, [unknown], None, known, now)
    assert state.room_temperature == 21


def test_record_values_are_stored_and_reported_values_remain_available() -> None:
    now = datetime.now(UTC)
    state = _structured(
        now,
        physicalInterface={"reported": {"timestamp": now.timestamp(), "format": "C"}},
    )
    record = DeviceRecord.from_api(DEVICE_ID, {"Format": "fahrenheit"})
    selected = reconcile(DEVICE_ID, [state], record, None, now)
    assert selected.display_unit == "F"
    assert selected.observations["display_unit"].kind == "stored"
    assert selected.reported_observations["display_unit"].value == "C"


def test_setpoint_bounds_are_never_mixed_across_sources() -> None:
    now = datetime.now(UTC)
    partial = _structured(
        now,
        targetHeat={"reported": {"lockoutMin": 29, "timestamp": now.timestamp()}},
    )
    record = DeviceRecord.from_api(DEVICE_ID, {"MinSetpoint": 5, "MaxSetpoint": 25})
    state = reconcile(DEVICE_ID, [partial], record, None, now)
    assert (state.setpoint_min, state.setpoint_max) == (5, 25)
    assert state.observations["setpoint_min"].kind == "stored"


def test_pending_confirmation_requires_changed_post_submission_report() -> None:
    now = datetime.now(UTC)
    old = _structured(
        now - timedelta(seconds=10),
        targetHeat={
            "reported": {
                "setpoint": 20,
                "timestamp": (now - timedelta(seconds=10)).timestamp(),
            }
        },
    )
    command = pending_command(
        {"heat_setpoint": 23}, old, now, timeout=timedelta(minutes=2)
    )
    command.reconcile(old, None, now + timedelta(seconds=5))
    assert command.status == "pending"

    current = _structured(
        now + timedelta(seconds=10),
        targetHeat={
            "reported": {
                "setpoint": 23,
                "timestamp": (now + timedelta(seconds=10)).timestamp(),
            }
        },
    )
    command.reconcile(current, None, now + timedelta(seconds=10))
    assert command.status == "confirmed"
    assert command.confirmation == {"heat_setpoint": "S"}

    expired = pending_command({"heat_setpoint": 24}, current, now)
    expired.reconcile(current, None, now + timedelta(minutes=3))
    assert expired.status == "expired"
    assert pending_command({"heat_setpoint": 23}, current, now).status == "unchanged"


def test_observation_issue_reason_classifies_command_readback() -> None:
    now = datetime.now(UTC)
    command = pending_command({"heat_setpoint": 23}, None, now)
    assert (
        observation_issue_reason(
            "heat_setpoint", observe(20, "S", None, now), command, now
        )
        == "missing_timestamp"
    )
    assert (
        observation_issue_reason(
            "heat_setpoint",
            observe(23, "S", (now - timedelta(seconds=1)).timestamp(), now),
            command,
            now,
        )
        == "before_submission"
    )
    assert (
        observation_issue_reason(
            "heat_setpoint", observe(20, "S", now.timestamp(), now), command, now
        )
        == "value_mismatch"
    )


def test_pending_command_tracker_supersedes_whole_command_and_reports_changes() -> None:
    now = datetime.now(UTC)
    tracker = PendingCommandTracker()
    first = pending_command({"heat_setpoint": 23, "mode": 4}, None, now)
    assert tracker.track(DEVICE_ID, first) == ()

    second = pending_command({"heat_setpoint": 24}, None, now)
    assert tracker.track(DEVICE_ID, second) == (first,)
    assert first.status == "superseded"
    assert tracker.pending[DEVICE_ID]["mode"] is first
    assert tracker.pending[DEVICE_ID]["heat_setpoint"] is second
    assert (
        tracker.command_value(
            DEVICE_ID, "heat_setpoint", 20, now + timedelta(seconds=1)
        )
        == 24
    )

    current = _structured(
        now + timedelta(seconds=10),
        targetHeat={
            "reported": {
                "setpoint": 24,
                "timestamp": (now + timedelta(seconds=10)).timestamp(),
            }
        },
    )
    changes = tracker.reconcile({DEVICE_ID: current}, {}, now + timedelta(seconds=10))
    assert len(changes) == 1
    assert changes[0][1] is second
    assert changes[0][2] == "pending"
    assert second.status == "confirmed"
