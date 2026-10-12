"""Reusable state and renewal policy for Mysa's live MQTT telemetry.

This module does not create an MQTT client or supervise connections. The
application owns client lifecycle and reconnection; this runtime owns the
meaning and freshness of telemetry received over a connected session.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import math
import time
from types import MappingProxyType
from typing import Any

from mysa_client.mqtt.protocol import MessageKind, MqttMessage


@dataclass(frozen=True, slots=True)
class TelemetryReading:
    """One locally timed, validated field from a live device report."""

    value: float
    received_at: datetime
    received_monotonic: float
    expires: float


class MqttTelemetryState:
    """Filter and retain fresh per-field readings from an explicit allowlist."""

    def __init__(
        self,
        *,
        max_age: float = 30.0,
        deduplication_window: int = 128,
        monotonic: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] | None = None,
    ) -> None:
        """Create an empty state cache with injectable clocks for applications."""
        if not math.isfinite(max_age) or max_age <= 0:
            raise ValueError("max_age must be positive and finite")
        if (
            isinstance(deduplication_window, bool)
            or not isinstance(deduplication_window, int)
            or deduplication_window < 1
        ):
            raise ValueError("deduplication_window must be positive")
        self.max_age = max_age
        self._monotonic = monotonic
        self._utcnow = utcnow or (lambda: datetime.now(UTC))
        self._devices: dict[str, str] = {}
        self._readings: dict[tuple[str, str], TelemetryReading] = {}
        self._seen_fields: set[tuple[str, str]] = set()
        self._message_ids: dict[str, deque[int]] = {}
        self._deduplication_window = deduplication_window
        self._last_visible: frozenset[tuple[str, str]] = frozenset()
        self._last_connected: bool | None = None

    @property
    def devices(self) -> Mapping[str, str]:
        """A copy of the current device-ID-to-model allowlist."""
        return dict(self._devices)

    @property
    def readings(self) -> Mapping[tuple[str, str], TelemetryReading]:
        """A read-only view of retained readings, including expired values."""
        return MappingProxyType(self._readings)

    def set_devices(self, devices: Mapping[str, str]) -> bool:
        """Replace the device allowlist and discard state for removed devices."""
        updated = dict(devices)
        if updated == self._devices:
            return False
        self._devices = updated
        self._readings = {
            key: reading for key, reading in self._readings.items() if key[0] in updated
        }
        self._seen_fields = {key for key in self._seen_fields if key[0] in updated}
        self._message_ids = {
            device_id: ids
            for device_id, ids in self._message_ids.items()
            if device_id in updated
        }
        self._last_visible = frozenset(
            key for key in self._last_visible if key[0] in updated
        )
        return True

    def clear(self) -> None:
        """Discard current readings when the owning application closes a session."""
        self._readings.clear()
        self._last_visible = frozenset()
        self._last_connected = None

    def accept(self, message: MqttMessage) -> bool:
        """Accept valid, unique live telemetry and update fields independently."""
        if (
            message.device_id not in self._devices
            or message.retained
            or message.kind != MessageKind.TELEMETRY
            or message.telemetry is None
            or message.received_at.tzinfo is None
            or message.received_at.utcoffset() is None
        ):
            return False

        now_utc = self._utcnow()
        age = (now_utc - message.received_at).total_seconds()
        if age < 0 or age >= self.max_age:
            return False

        if message.message_id is not None:
            ids = self._message_ids.setdefault(
                message.device_id, deque(maxlen=self._deduplication_window)
            )
            if message.message_id in ids:
                return False
            ids.append(message.message_id)

        now = self._monotonic()
        received_monotonic = now - age
        expires = now + self.max_age - age
        changed = False
        for field, upper in (("humidity", 100.0), ("duty_cycle", 1.0)):
            value = getattr(message.telemetry, field)
            if value is None or not math.isfinite(value) or not 0 <= value <= upper:
                continue
            key = (message.device_id, field)
            self._readings[key] = TelemetryReading(
                value=value,
                received_at=message.received_at,
                received_monotonic=received_monotonic,
                expires=expires,
            )
            self._seen_fields.add(key)
            changed = True
        return changed

    def value(
        self,
        device_id: str,
        field: str,
        *,
        connected: bool,
        now: float | None = None,
    ) -> float | None:
        """Return a fresh value only while its owning MQTT session is connected."""
        reading = self._readings.get((device_id, field))
        if (
            connected
            and device_id in self._devices
            and reading is not None
            and (self._monotonic() if now is None else now) < reading.expires
        ):
            return reading.value
        return None

    def has_value(self, device_id: str, field: str) -> bool:
        """Whether valid telemetry has ever established this field's presence."""
        return (device_id, field) in self._seen_fields

    def visible_keys(
        self, *, connected: bool, now: float | None = None
    ) -> frozenset[tuple[str, str]]:
        """Return fields that currently have fresh readings."""
        current_time = self._monotonic() if now is None else now
        return frozenset(
            key
            for key in self._readings
            if self.value(*key, connected=connected, now=current_time) is not None
        )

    def note_visibility(self, *, connected: bool, now: float | None = None) -> None:
        """Record the visible state after an application notifies its listeners."""
        self._last_visible = self.visible_keys(connected=connected, now=now)
        self._last_connected = connected

    def visibility_changed(self, *, connected: bool, now: float | None = None) -> bool:
        """Detect expiry or connection transitions that require notification."""
        visible = self.visible_keys(connected=connected, now=now)
        changed = visible != self._last_visible or connected != self._last_connected
        if changed:
            self._last_visible = visible
            self._last_connected = connected
        return changed

    def diagnostics(
        self,
        device_id: str,
        *,
        connected: bool,
        dropped_events: int = 0,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Return safe freshness details without topic names or raw MQTT packets."""
        return {
            "connected": connected,
            "subscribed": device_id in self._devices,
            "dropped_events": dropped_events,
            "readings": {
                field: {
                    "value": reading.value,
                    "received_at": reading.received_at.isoformat(),
                    "fresh": self.value(dev, field, connected=connected, now=now)
                    is not None,
                }
                for (dev, field), reading in self._readings.items()
                if dev == device_id
            },
        }


class MqttLiveStatusRenewal:
    """Schedule live-stream renewals without owning MQTT connections or tasks."""

    def __init__(
        self,
        *,
        quiet_refresh_seconds: float = 20.0,
        renew_seconds: float = 150.0,
        retry_seconds: float = 60.0,
        stream_seconds: int = 180,
    ) -> None:
        """Configure renewal policy; the application performs due requests."""
        if any(
            not math.isfinite(value) or value <= 0
            for value in (quiet_refresh_seconds, renew_seconds, retry_seconds)
        ):
            raise ValueError("renewal intervals must be positive and finite")
        if (
            isinstance(stream_seconds, bool)
            or not isinstance(stream_seconds, int)
            or stream_seconds < 1
        ):
            raise ValueError("stream_seconds must be positive")
        self.quiet_refresh_seconds = quiet_refresh_seconds
        self.renew_seconds = renew_seconds
        self.retry_seconds = retry_seconds
        self.stream_seconds = stream_seconds
        self._generation: int | None = None
        self._due: dict[str, float] = {}
        self._requested: dict[str, float] = {}

    def begin_session(self, generation: int) -> None:
        """Reset per-session deadlines after the transport reports a new session."""
        if generation == self._generation:
            return
        self._generation = generation
        self._due.clear()
        self._requested.clear()

    def should_request(
        self,
        device_id: str,
        *,
        now: float,
        readings: Iterable[TelemetryReading],
    ) -> bool:
        """Whether a device needs a renewal or has gone quiet after reporting."""
        last_request = self._requested.get(device_id, 0.0)
        receipts = [reading.received_monotonic for reading in readings]
        quiet = bool(
            receipts
            and max(receipts) >= last_request
            and now - min(receipts) >= self.quiet_refresh_seconds
            and now - last_request >= self.quiet_refresh_seconds
        )
        return now >= self._due.get(device_id, 0.0) or quiet

    def request_started(self, device_id: str, *, now: float) -> None:
        """Record when the application began asking the device for live status."""
        self._requested[device_id] = now

    def request_finished(self, device_id: str, *, accepted: bool, now: float) -> None:
        """Set the next deadline from the broker's acceptance result."""
        interval = self.renew_seconds if accepted else self.retry_seconds
        self._due[device_id] = now + interval


__all__ = ["MqttLiveStatusRenewal", "MqttTelemetryState", "TelemetryReading"]
