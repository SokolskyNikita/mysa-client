"""Protocol evidence and malformed-input tests for optional MQTT support."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mysa_client.cloud import Setpoint, SetpointRules
from mysa_client.mqtt import (
    MessageKind,
    MqttProtocolError,
    command_type,
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


def ruled(celsius: float) -> Setpoint:
    """A setpoint from rules wider than MQTT's 5-30 °C, so range checks stay ours."""
    return SetpointRules(homekit_range=(0, 40, 0.5)).setpoint(None, celsius)


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
    payload, expected = command(setpoint=ruled(21.5))
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
    payload, expected = command(setpoint=ruled(20), hold=True)
    assert payload["body"]["cmd"][0]["ho"] == 2
    assert expected["ho"] == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"setpoint": ruled(4.5)},
        {"setpoint": ruled(30.5)},
        {"mode": "cool"},
        {"mode": "auto"},
        {"setpoint": ruled(20), "hold": 1},
    ],
)
def test_unsupported_commands_are_rejected_before_publication(kwargs: dict) -> None:
    with pytest.raises(MqttProtocolError):
        command(**kwargs)


@pytest.mark.parametrize("value", [21.5, 21, True, float("nan"), float("inf"), "21.5"])
def test_command_setpoint_must_come_from_rules(value: object) -> None:
    """Plain numbers, including on-grid ones, never reach the wire."""
    with pytest.raises(TypeError, match="setpoint must be a Setpoint from"):
        command(setpoint=value)


def test_command_writes_the_ruled_celsius() -> None:
    """An off-grid request is sent as the rules' rounded value."""
    setpoint = SetpointRules(homekit_range=(5, 30, 0.5)).setpoint(None, 22.78)
    payload, expected = command(setpoint=setpoint)
    assert payload["body"]["cmd"][0]["sp"] == 23.0
    assert expected == {"sp": 23.0}


@pytest.mark.parametrize(
    "model", ["BB-V1-1", "BB-V2-0", "INF-V1-0", "AC-V1", "unknown"]
)
def test_unvalidated_models_do_not_get_lite_commands(model: str) -> None:
    assert not supports_commands(model)


def test_lite_commands_are_opted_in_by_model() -> None:
    assert supports_commands("BB-V2-0-L")


def test_baseboard_v1_status_is_telemetry() -> None:
    """The flat MsgType 0 payload quoted in kgelinas/Mysa_HA issue 43."""
    event = parse(
        {
            "Device": DEVICE,
            "Timestamp": 1794273775,
            "MsgType": 0,
            "MainTemp": 19.35,
            "ThermistorTemp": 0.0,
            "ComboTemp": 21.96,
            "Humidity": 43.0,
            "Current": 16.5,
            "SetPoint": 14.0,
            "Stream": 1,
        }
    )
    assert event.kind is MessageKind.TELEMETRY
    assert event.telemetry is not None
    assert event.telemetry.room_temperature == 19.35
    assert event.telemetry.humidity == 43.0
    assert event.telemetry.setpoint == 14.0
    assert event.telemetry.current == 16.5
    assert event.telemetry.duty_cycle is None


def test_baseboard_v1_status_must_name_the_subscribed_device() -> None:
    event = parse({"Device": "000000000000", "MsgType": 0, "MainTemp": 19.35})
    assert event.kind is MessageKind.OTHER
    assert event.telemetry is None


def in_floor_status(msg: int = 17, **body: object) -> dict:
    return {
        "ver": "1.0",
        "src": {"type": 1, "ref": DEVICE},
        "time": 1794273775,
        "msg": msg,
        "id": 42,
        "body": {
            "ambTemp": 16.2,
            "flrSnsrTemp": 22.2,
            "hum": 58,
            "trackedSnsr": 3,
            "stpt": 22,
            "heatStat": 1,
            "dutyCycle": 0.43,
            "lineVtg": 240,
            **body,
        },
    }


@pytest.mark.parametrize("msg", [17, 40])
def test_in_floor_status_uses_the_relay_state_not_duty_cycle(msg: int) -> None:
    event = parse(in_floor_status(msg))
    assert event.kind is MessageKind.TELEMETRY
    telemetry = event.telemetry
    assert telemetry is not None
    assert telemetry.room_temperature == 16.2
    assert telemetry.floor_temperature == 22.2
    assert telemetry.humidity == 58
    assert telemetry.setpoint == 22
    assert telemetry.duty_cycle == 1
    assert telemetry.tracked_sensor == "floor"
    assert telemetry.line_voltage == 240


def test_in_floor_fields_beside_the_body_are_read() -> None:
    payload = in_floor_status()
    payload["heatStat"] = payload["body"].pop("heatStat")
    payload["lineVtg"] = payload["body"].pop("lineVtg")
    telemetry = parse(payload).telemetry
    assert telemetry is not None
    assert telemetry.duty_cycle == 1
    assert telemetry.line_voltage == 240


@pytest.mark.parametrize(("wire", "sensor"), [(5, "ambient"), (4, None), ("x", None)])
def test_tracked_sensor_maps_only_known_values(
    wire: object, sensor: str | None
) -> None:
    telemetry = parse(in_floor_status(trackedSnsr=wire)).telemetry
    assert telemetry is not None
    assert telemetry.tracked_sensor == sensor


def test_ac_status_is_telemetry() -> None:
    event = parse(
        {
            "ver": "1.0",
            "src": {"type": 1, "ref": DEVICE},
            "msg": 30,
            "id": 43,
            "body": {"ambTemp": 24.5, "hum": 51, "stpt": 21, "dtyCycle": 0},
        }
    )
    assert event.kind is MessageKind.TELEMETRY
    assert event.telemetry is not None
    assert event.telemetry.room_temperature == 24.5
    assert event.telemetry.duty_cycle == 0
    assert event.telemetry.floor_temperature is None


@pytest.mark.parametrize("msg", [17, 30])
def test_status_from_another_source_is_not_telemetry(msg: int) -> None:
    payload = in_floor_status(msg)
    payload["src"] = {"type": 100, "ref": "user"}
    assert parse(payload).kind is MessageKind.OTHER


@pytest.mark.parametrize(
    ("model", "wire"),
    [
        ("BB-V1-1", 1),
        ("bb-v1-0", 1),
        ("INF-V1-0", 3),
        ("BB-V2-0", 4),
        ("BB-V2-0-L", 5),
        ("V2-L", 5),
        ("AC-V1-0", None),
        ("ST-V1-0", None),
        ("BB-V3-0", None),
    ],
)
def test_command_type_by_model(model: str, wire: int | None) -> None:
    assert command_type(model) == wire


@pytest.mark.parametrize("model", ["BB-V1-1", "BB-V2-0", "INF-V1-0"])
def test_experimental_commands_are_opt_in(model: str) -> None:
    assert not supports_commands(model)
    assert supports_commands(model, experimental=True)


@pytest.mark.parametrize("model", ["AC-V1-0", "ST-V1-0", "unknown"])
def test_experimental_commands_exclude_models_without_a_type(model: str) -> None:
    assert not supports_commands(model, experimental=True)
    with pytest.raises(MqttProtocolError):
        command(setpoint=ruled(20.0), model=model)


def test_command_carries_the_models_type() -> None:
    payload, _ = command(setpoint=ruled(20.0), model="BB-V1-1")
    assert payload["body"]["type"] == 1
    payload, _ = command(setpoint=ruled(20.0))
    assert payload["body"]["type"] == 5


def test_in_floor_command_allows_up_to_40() -> None:
    payload, expected = command(setpoint=ruled(40.0), model="INF-V1-0")
    assert payload["body"] == {"ver": 1, "type": 3, "cmd": [{"tm": -1, "sp": 40.0}]}
    assert expected == {"sp": 40.0}
    with pytest.raises(MqttProtocolError):
        command(setpoint=ruled(40.0), model="BB-V2-0")
