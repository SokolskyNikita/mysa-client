"""Reusable live telemetry filtering, freshness, and renewal policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mysa_client.mqtt.protocol import MessageKind, MqttMessage, MqttTelemetry
from mysa_client.runtime.telemetry import (
    MqttLiveStatusRenewal,
    MqttTelemetryState,
)

DEVICE = "device-one"
OTHER = "device-two"


class _Clock:
    def __init__(self, *, monotonic: float = 100, wall: datetime | None = None):
        self.monotonic_value = monotonic
        self.wall_value = wall or datetime(2026, 10, 12, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.monotonic_value

    def utcnow(self) -> datetime:
        return self.wall_value


def _message(
    *,
    device_id: str = DEVICE,
    message_id: int | None = 1,
    received_at: datetime | None = None,
    humidity: float | None = 44.5,
    duty_cycle: float | None = 0.375,
    retained: bool = False,
    kind: MessageKind = MessageKind.TELEMETRY,
) -> MqttMessage:
    return MqttMessage(
        device_id=device_id,
        kind=kind,
        received_at=received_at or datetime(2026, 10, 12, tzinfo=UTC),
        message_id=message_id,
        telemetry=MqttTelemetry(humidity=humidity, duty_cycle=duty_cycle),
        retained=retained,
    )


def _state(clock: _Clock) -> MqttTelemetryState:
    state = MqttTelemetryState(monotonic=clock.monotonic, utcnow=clock.utcnow)
    state.set_devices({DEVICE: "BB-V2-0-L"})
    return state


def test_filters_allowlist_retained_duplicates_age_and_ranges() -> None:
    """Only valid unique messages for allowed devices refresh values."""
    clock = _Clock()
    state = _state(clock)

    assert not state.accept(_message(retained=True))
    assert not state.accept(_message(device_id=OTHER))
    assert not state.accept(_message(kind=MessageKind.MALFORMED))
    assert not state.accept(
        _message(received_at=clock.wall_value - timedelta(seconds=30))
    )
    assert state.accept(_message(message_id=2, humidity=100, duty_cycle=1))
    assert not state.accept(_message(message_id=2, humidity=10, duty_cycle=0.1))
    assert not state.accept(_message(message_id=3, humidity=100.1, duty_cycle=-0.1))

    assert state.value(DEVICE, "humidity", connected=True) == 100
    assert state.value(DEVICE, "duty_cycle", connected=True) == 1
    assert not state.has_value(OTHER, "humidity")


def test_each_field_expires_independently_and_allowlist_discards_removed_data() -> None:
    """Missing values do not renew another field and removed devices are forgotten."""
    clock = _Clock()
    state = _state(clock)
    assert state.accept(_message(message_id=1, humidity=40, duty_cycle=0.5))
    clock.monotonic_value += 20
    clock.wall_value += timedelta(seconds=20)
    assert state.accept(
        _message(
            message_id=2,
            received_at=clock.wall_value,
            humidity=45,
            duty_cycle=None,
        )
    )
    clock.monotonic_value += 10.1
    clock.wall_value += timedelta(seconds=10.1)

    assert state.value(DEVICE, "humidity", connected=True) == 45
    assert state.value(DEVICE, "duty_cycle", connected=True) is None
    assert state.has_value(DEVICE, "duty_cycle")
    assert state.value(DEVICE, "humidity", connected=False) is None

    state.set_devices({OTHER: "BB-V2-0-L"})
    assert not state.has_value(DEVICE, "humidity")
    assert not state.readings


def test_local_receipt_time_drives_expiry_and_visibility() -> None:
    """Thermostat wall-clock values cannot extend or shorten local freshness."""
    clock = _Clock()
    state = _state(clock)
    message = _message(
        received_at=clock.wall_value + timedelta(days=30),
        humidity=47.125,
        duty_cycle=0.625,
    )
    # A future client receipt timestamp is rejected as a clock anomaly.
    assert not state.accept(message)

    clock.wall_value += timedelta(seconds=1)
    assert state.accept(
        _message(
            message_id=2,
            received_at=clock.wall_value,
            humidity=47.125,
            duty_cycle=0.625,
        )
    )
    assert state.visible_keys(connected=True) == {
        (DEVICE, "humidity"),
        (DEVICE, "duty_cycle"),
    }
    state.note_visibility(connected=True)
    assert not state.visibility_changed(connected=True)

    clock.monotonic_value += 30.1
    assert state.visibility_changed(connected=True)
    assert not state.visible_keys(connected=True)


def test_live_status_renewal_handles_quiet_streams_and_new_sessions() -> None:
    """Renewal backs off for silent devices and refreshes a newly quiet stream."""
    clock = _Clock()
    state = _state(clock)
    renewal = MqttLiveStatusRenewal()
    renewal.begin_session(1)

    assert renewal.should_request(DEVICE, now=clock.monotonic(), readings=())
    renewal.request_started(DEVICE, now=clock.monotonic())
    renewal.request_finished(DEVICE, accepted=True, now=clock.monotonic())
    assert not renewal.should_request(DEVICE, now=249.9, readings=())
    assert renewal.should_request(DEVICE, now=250, readings=())

    clock.monotonic_value = 300
    assert state.accept(_message(message_id=3, received_at=clock.wall_value))
    renewal.begin_session(2)
    assert renewal.should_request(
        DEVICE, now=clock.monotonic(), readings=state.readings.values()
    )
    renewal.request_started(DEVICE, now=clock.monotonic())
    renewal.request_finished(DEVICE, accepted=True, now=clock.monotonic())
    clock.monotonic_value += 20
    assert renewal.should_request(
        DEVICE, now=clock.monotonic(), readings=state.readings.values()
    )
