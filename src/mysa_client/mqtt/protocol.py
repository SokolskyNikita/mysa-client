"""Parsing and encoding for Mysa's legacy MQTT message envelopes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import json
import math
from typing import Any

from .errors import MqttProtocolError


class MessageKind(StrEnum):
    """Normalized types of messages received from an owned device topic."""

    TELEMETRY = "telemetry"
    ACKNOWLEDGEMENT = "acknowledgement"
    ECHO_REPLY = "echo_reply"
    OTHER = "other"
    MALFORMED = "malformed"


MAX_PAYLOAD_SIZE = 65_536


@dataclass(frozen=True, slots=True)
class MqttTelemetry:
    """Common live values in legacy MCM msg 40 telemetry."""

    room_temperature: float | None = None
    humidity: float | None = None
    setpoint: float | None = None
    duty_cycle: float | None = None


@dataclass(frozen=True, slots=True)
class DeviceCommandState:
    """Reported command state in a device acknowledgement."""

    mode: int | None = None
    setpoint: float | None = None
    hold: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class MqttMessage:
    """A normalized event with the original payload available for advanced use."""

    device_id: str = field(repr=False)
    kind: MessageKind
    received_at: datetime
    message_type: int | None = None
    source_type: int | None = None
    message_id: int | None = None
    response_id: int | None = None
    telemetry: MqttTelemetry | None = None
    state: DeviceCommandState | None = None
    success: bool | None = None
    echo_id: int | None = None
    payload: Mapping[str, Any] | None = field(default=None, repr=False)
    raw_payload: bytes = field(default=b"", repr=False)
    parse_error: str | None = None
    retained: bool = False


_LITE_MODELS = frozenset(
    {
        "BB-V2-0-L",
        "BB-V2-0-LTE",
        "V2-0-L",
        "V2-0-LTE",
        "V2-L",
    }
)


def supports_commands(model: str) -> bool:
    """Whether the model's MCM msg 44 command path is unconditional in the app."""
    return model.strip().upper() in _LITE_MODELS


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if abs(value) < 2**64 else None
    if isinstance(value, str):
        if len(value) > 20:
            return None
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if abs(parsed) < 2**64 else None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            return float(value) if math.isfinite(value) else None
        except (OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _success(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    numeric = _integer(value)
    if numeric in (0, 1):
        return numeric == 1
    return None


def parse_message(
    device_id: str,
    raw_payload: bytes,
    *,
    retained: bool = False,
) -> MqttMessage:
    """Normalize one JSON message; malformed input becomes a safe event."""
    received_at = datetime.now(UTC)
    if len(raw_payload) > MAX_PAYLOAD_SIZE:
        return MqttMessage(
            device_id=device_id,
            kind=MessageKind.MALFORMED,
            received_at=received_at,
            raw_payload=raw_payload[:MAX_PAYLOAD_SIZE],
            parse_error="payload exceeds 64 KiB",
            retained=retained,
        )
    try:
        decoded = json.loads(raw_payload)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return MqttMessage(
            device_id=device_id,
            kind=MessageKind.MALFORMED,
            received_at=received_at,
            raw_payload=raw_payload,
            parse_error="invalid JSON payload",
            retained=retained,
        )
    if not isinstance(decoded, Mapping):
        return MqttMessage(
            device_id=device_id,
            kind=MessageKind.MALFORMED,
            received_at=received_at,
            raw_payload=raw_payload,
            parse_error="payload must be a JSON object",
            retained=retained,
        )

    message_type = _integer(decoded.get("msg"))
    source = _mapping(decoded.get("src")) or {}
    source_type = _integer(source.get("type"))
    source_ref = source.get("ref")
    message_id = _integer(decoded.get("id"))
    response_id = _integer(decoded.get("resp_id"))
    body = _mapping(decoded.get("body")) or {}

    if message_type == 40:
        if source_type != 1 or source.get("ref") != device_id:
            return MqttMessage(
                device_id=device_id,
                kind=MessageKind.OTHER,
                received_at=received_at,
                message_type=message_type,
                source_type=source_type,
                message_id=message_id,
                payload=decoded,
                raw_payload=raw_payload,
                retained=retained,
            )
        telemetry_body = _mapping(body.get("reading")) or body
        telemetry = MqttTelemetry(
            room_temperature=_number(telemetry_body.get("ambTemp")),
            humidity=_number(telemetry_body.get("hum")),
            setpoint=_number(telemetry_body.get("stpt")),
            duty_cycle=_number(telemetry_body.get("dtyCycle")),
        )
        return MqttMessage(
            device_id=device_id,
            kind=MessageKind.TELEMETRY,
            received_at=received_at,
            message_type=message_type,
            source_type=source_type,
            message_id=message_id,
            telemetry=telemetry,
            payload=decoded,
            raw_payload=raw_payload,
            retained=retained,
        )

    if message_type == 44:
        raw_state = _mapping(body.get("state"))
        kind = (
            MessageKind.ACKNOWLEDGEMENT
            if source_type == 1 and source_ref == device_id and raw_state is not None
            else MessageKind.OTHER
        )
        state = (
            DeviceCommandState(
                mode=_integer(raw_state.get("md")),
                setpoint=_number(raw_state.get("sp")),
                hold=_integer(raw_state.get("ho")),
                raw=raw_state,
            )
            if raw_state is not None
            else None
        )
        return MqttMessage(
            device_id=device_id,
            kind=kind,
            received_at=received_at,
            message_type=message_type,
            source_type=source_type,
            message_id=message_id,
            response_id=response_id,
            state=state,
            success=_success(body.get("success")),
            payload=decoded,
            raw_payload=raw_payload,
            retained=retained,
        )

    if _integer(decoded.get("MsgType")) == 5 and decoded.get("Device") == device_id:
        return MqttMessage(
            device_id=device_id,
            kind=MessageKind.ECHO_REPLY,
            received_at=received_at,
            source_type=source_type,
            echo_id=_integer(decoded.get("EchoID")),
            payload=decoded,
            raw_payload=raw_payload,
            retained=retained,
        )

    return MqttMessage(
        device_id=device_id,
        kind=MessageKind.OTHER,
        received_at=received_at,
        message_type=message_type,
        source_type=source_type,
        message_id=message_id,
        response_id=response_id,
        echo_id=_integer(decoded.get("EchoID")),
        payload=decoded,
        raw_payload=raw_payload,
        retained=retained,
    )


def encode_thermostat_command(
    *,
    device_id: str,
    user_sub: str,
    request_id: int,
    command_time: int,
    setpoint: float | None = None,
    mode: str | None = None,
    hold: bool | None = None,
) -> tuple[dict[str, Any], dict[str, int | float]]:
    """Build a Lite msg 44 request and the state fields required to confirm it."""
    if setpoint is None and mode is None:
        raise MqttProtocolError("A setpoint or mode is required")
    command: dict[str, int | float] = {"tm": -1}
    expected: dict[str, int | float] = {}
    if setpoint is not None:
        if (
            isinstance(setpoint, bool)
            or not isinstance(setpoint, int | float)
            or not math.isfinite(setpoint)
            or not 5 <= setpoint <= 30
        ):
            raise MqttProtocolError("Setpoint must be finite and between 5 and 30 °C")
        value = round(float(setpoint), 2)
        command["sp"] = value
        expected["sp"] = value
    if mode is not None:
        normalized = mode.strip().lower()
        if normalized not in {"off", "heat"}:
            raise MqttProtocolError(
                "Legacy Lite commands support only off and heat modes"
            )
        legacy_mode = 1 if normalized == "off" else 3
        command["md"] = legacy_mode
        expected["md"] = legacy_mode
    if hold is not None:
        if not isinstance(hold, bool):
            raise MqttProtocolError("Hold must be true, false, or omitted")
        legacy_hold = 2 if hold else 1
        command["ho"] = legacy_hold
        expected["ho"] = legacy_hold
    envelope = {
        "ver": "1.0",
        "src": {"type": 100, "ref": user_sub},
        "dest": {"type": 1, "ref": device_id},
        "time": command_time,
        "msg": 44,
        "id": request_id,
        "body": {"ver": 1, "type": 5, "cmd": [command]},
    }
    return envelope, expected
