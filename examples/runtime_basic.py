"""Demonstrate Mysa domain helpers with synthetic, in-memory observations.

This example never creates a session, imports a transport, or contacts Mysa.
Install ``mysa-client`` (or run it from an editable checkout), then execute:

    python examples/runtime_basic.py
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
from typing import Any

from mysa_client.cloud import DeviceState, Mode, ModelFamily
from mysa_client.cloud.device_commands import climate_command
from mysa_client.cloud.reconciliation import (
    PendingCommandTracker,
    pending_command,
    reconcile,
)
from mysa_client.cloud.validation import validate_cloud_command
from mysa_client.runtime.state import DeviceView

DEVICE_ID = "synthetic-device"


def state_report(setpoint: float, observed_at: datetime) -> DeviceState:
    """Build a small synthetic cloud report without a client or HTTP session."""
    stamp = observed_at.timestamp()
    return DeviceState.from_api(
        DEVICE_ID,
        {
            "identity": {"reported": {"model": "BB-V2-0-L"}},
            "latestTelemetry": {
                "isConnected": True,
                "lastConnected": stamp,
                "reading": {
                    "timestamp": stamp,
                    "roomTemperature": 19.8,
                    "humidity": 42,
                    "dutyCycle": 0.35,
                },
            },
            "modes": {"reported": {"mode": int(Mode.HEAT), "timestamp": stamp}},
            "targetHeat": {
                "reported": {
                    "setpoint": setpoint,
                    "lockoutMin": 5,
                    "lockoutMax": 30,
                    "timestamp": stamp,
                }
            },
        },
    )


def main() -> None:
    """Run the no-network semantic workflow example."""
    parser = argparse.ArgumentParser(
        description="Show Mysa semantic helpers using synthetic data only."
    )
    parser.parse_args()

    submitted_at = datetime.now(UTC).replace(microsecond=0)
    before = state_report(20, submitted_at - timedelta(seconds=10))
    state = reconcile(DEVICE_ID, [before], None, None, submitted_at)

    # The caller supplies the freshness policy. This example accepts each
    # synthetic report for one minute from its own observation timestamp.
    def cloud_fresh(field: str) -> bool:
        observation = state.observations.get(field)
        return bool(
            observation and observation.fresh(datetime.now(UTC), timedelta(minutes=1))
        )

    view = DeviceView(
        state=state,
        family=ModelFamily.BASEBOARD_LITE,
        cloud_ok=True,
        cloud_fresh=cloud_fresh,
    )

    command = climate_command(
        ModelFamily.BASEBOARD_LITE,
        view.mode,
        temperature=21,
    )
    validate_cloud_command(
        ModelFamily.BASEBOARD_LITE,
        caps=None,
        state=state,
        expected=command.expected,
        command_value=lambda _field, fallback: fallback,
    )

    pending = pending_command(
        command.expected,
        state,
        submitted_at,
        stored=command.stored_fields,
    )
    tracker = PendingCommandTracker()
    tracker.track(DEVICE_ID, pending)

    # Simulate a later report from the device. No command is sent.
    reported_at = datetime.now(UTC)
    reported = state_report(21, reported_at)
    merged = reconcile(
        DEVICE_ID,
        [reported],
        None,
        state,
        reported_at + timedelta(seconds=1),
    )
    tracker.reconcile(
        {DEVICE_ID: merged},
        {},
        reported_at + timedelta(seconds=1),
    )

    result: dict[str, Any] = {
        "network": "none",
        "selected_state": {
            "temperature": view.temperature,
            "mode": view.mode.name.lower() if view.mode is not None else None,
            "humidity": view.measurement("humidity"),
            "heating_activity": view.action,
        },
        "semantic_command": {
            "kind": command.kind,
            "payload": command.payload,
            "expected": command.expected,
        },
        "confirmation": {
            "status": pending.status,
            "fields": pending.confirmation,
        },
    }
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
