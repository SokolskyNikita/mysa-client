"""Protocol evidence and malformed-input tests for optional MQTT support."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mysa_client.mqtt import (
    MessageKind,
    MqttProtocolError,
    encode_thermostat_command,
    parse_message,
    supports_commands,
)

DEVICE = "a1b2c3d4e5f6"
FIXTURES = Path(__file__).resolve().parents[2] / "examples" / "mqtt"


def fixture(name: str) -> dict:
    """Load the sanitized device packet preserved during live investigation."""
    return json.loads((FIXTURES / f"{name}.json").read_text())["payload"]


def parse(payload: dict):
    """Decode a constructed variant of an observed device payload."""
    return parse_message(DEVICE, json.dumps(payload).encode())


def test_live_telemetry_fixture() -> None:
    event = parse(fixture("live-telemetry"))
    assert event.kind is MessageKind.TELEMETRY
    assert event.telemetry is not None
    assert event.telemetry.room_temperature == 22.3
    assert event.telemetry.humidity == 34.1
    assert event.telemetry.setpoint == 22
    assert event.telemetry.duty_cycle == 0
    assert event.received_at.utcoffset().total_seconds() == 0


def test_live_device_acknowledgement_fixture() -> None:
    payload = fixture("device-acknowledgement")
    event = parse(payload)
    assert event.kind is MessageKind.ACKNOWLEDGEMENT
    assert event.success is True
    assert event.response_id == 1791700000000
    assert event.message_id == 1234567890123456789
    assert event.state is not None
    assert (event.state.setpoint, event.state.mode, event.state.hold) == (22, 3, 1)
    assert event.state.raw["br"] == 100
    assert event.payload == payload
    assert DEVICE not in repr(event)
    assert "trig_src" not in repr(event)


def test_live_echo_fixture() -> None:
    event = parse(fixture("echo-reply"))
    assert event.kind is MessageKind.ECHO_REPLY
    assert event.echo_id == 418


@pytest.mark.parametrize("name", ["device-acknowledgement", "live-telemetry"])
@pytest.mark.parametrize(
    "source", [{"type": 100, "ref": DEVICE}, {"type": 1, "ref": "other"}, {}]
)
def test_app_or_wrong_device_message_is_not_device_evidence(
    name: str, source: dict
) -> None:
    payload = fixture(name)
    payload["src"] = source
    assert parse(payload).kind is MessageKind.OTHER


def test_echo_must_name_the_subscribed_device() -> None:
    payload = fixture("echo-reply")
    payload["Device"] = "other"
    assert parse(payload).kind is MessageKind.OTHER


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json",
        b"\xff",
        b"[]",
        b"null",
        b"42",
        b"[" * 1100 + b"]" * 1100,
        b'{"id":' + b"9" * 5000 + b"}",
    ],
    ids=["text", "non-utf8", "array", "null", "number", "deep-json", "huge-integer"],
)
def test_malformed_network_input_is_an_event(raw: bytes) -> None:
    event = parse_message(DEVICE, raw)
    assert event.kind is MessageKind.MALFORMED
    assert event.parse_error
    assert event.raw_payload == raw


def test_oversize_packet_does_not_expand_event_queue_memory_without_bound() -> None:
    event = parse_message(DEVICE, b"x" * 100_000)
    assert event.kind is MessageKind.MALFORMED
    assert len(event.raw_payload) <= 65_536


def test_unknown_notifications_are_preserved_for_consumers() -> None:
    payload = {"MsgType": 1, "Device": DEVICE, "Prev": 20, "Next": 21, "Source": 3}
    event = parse(payload)
    assert event.kind is MessageKind.OTHER
    assert event.payload == payload
    assert json.loads(event.raw_payload) == payload


@pytest.mark.parametrize(
    "value", [True, None, [], "NaN", "Infinity", "garbage", 10**400]
)
def test_non_numeric_telemetry_is_not_promoted_to_a_value(value: object) -> None:
    payload = fixture("live-telemetry")
    payload["body"]["stpt"] = value
    event = parse(payload)
    assert event.telemetry.setpoint is None


def test_retained_marker_survives_parsing() -> None:
    event = parse_message(
        DEVICE, json.dumps(fixture("device-acknowledgement")).encode(), retained=True
    )
    assert event.retained


def command(**kwargs):
    """Build an envelope for an explicitly chosen device and user subject."""
    return encode_thermostat_command(
        device_id=DEVICE,
        user_sub="user-pool-subject",
        request_id=1791700000000,
        command_time=1791700000,
        **kwargs,
    )


def test_command_uses_app_array_form_and_preserves_hold_unless_requested() -> None:
    payload, expected = command(setpoint=21.5)
    original = fixture("thermostat-command.array")
    original["src"]["ref"] = "user-pool-subject"
    assert payload == original
    assert expected == {"sp": 21.5}
    assert "ho" not in payload["body"]["cmd"][0]
    assert payload["src"]["type"] == 100
    assert payload["body"]["type"] == 5


@pytest.mark.parametrize(("mode", "wire"), [("off", 1), ("heat", 3)])
def test_mode_uses_the_mqtt_numbering(mode: str, wire: int) -> None:
    payload, expected = command(mode=mode, hold=False)
    assert payload["body"]["cmd"][0]["md"] == wire
    assert expected == {"md": wire, "ho": 1}


def test_explicit_hold_is_encoded() -> None:
    payload, expected = command(setpoint=20, hold=True)
    assert payload["body"]["cmd"][0]["ho"] == 2
    assert expected["ho"] == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"setpoint": True},
        {"setpoint": float("nan")},
        {"setpoint": float("inf")},
        {"setpoint": 4},
        {"setpoint": 31},
        {"mode": "cool"},
        {"mode": "auto"},
        {"setpoint": 20, "hold": 1},
    ],
)
def test_unsupported_commands_are_rejected_before_publication(kwargs: dict) -> None:
    with pytest.raises(MqttProtocolError):
        command(**kwargs)


@pytest.mark.parametrize(
    "model", ["BB-V1-1", "BB-V2-0", "INF-V1-0", "AC-V1", "unknown"]
)
def test_unvalidated_models_do_not_get_lite_commands(model: str) -> None:
    assert not supports_commands(model)


def test_lite_commands_are_opted_in_by_model() -> None:
    assert supports_commands("BB-V2-0-L")
