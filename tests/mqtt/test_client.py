"""Transport lifecycle and command-correlation tests for the MQTT client."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from typing import Any

import pytest

from mysa_client.mqtt import (
    AwsCredentials,
    CommandFailure,
    MqttConnectionError,
    MqttProtocolError,
    MysaMqttClient,
)

DEVICE = "device-one"
OTHER_DEVICE = "device-two"
MODEL = "BB-V2-0-L"
ID_TOKEN = (
    "header."
    + base64.urlsafe_b64encode(b'{"sub":"test-subject"}').decode().rstrip("=")
    + ".signature"
)


class FakeClient:
    """Small Paho-shaped fake that calls callbacks from its worker thread."""

    def __init__(
        self,
        *,
        publish_mode: str = "ack",
        suback_reason: int = 0,
        suback_mid_offset: int = 0,
        drop_after_connect: bool = False,
        fail_connect: bool = False,
    ) -> None:
        """Create a fake with configured acknowledgement and reconnect behavior."""
        self.publish_mode = publish_mode
        self.suback_reason = suback_reason
        self.suback_mid_offset = suback_mid_offset
        self.drop_after_connect = drop_after_connect
        self.fail_connect = fail_connect
        self.connect_failed = False
        self.disconnect_calls = 0
        self.constructor_thread = threading.get_ident()
        self.tls_thread: int | None = None
        self.callback_setup_threads: list[int] = []
        self.connected = False
        self.drop_requested = False
        self.subscriptions: list[list[tuple[str, int]]] = []
        self.publications: list[tuple[str, dict[str, Any], bool]] = []
        self._mid = 0
        self._sub_mid = 0
        self._stop_after_setup = False
        self.on_connect: Any = None
        self.on_subscribe: Any = None
        self.on_disconnect: Any = None
        self.on_message: Any = None
        self.on_publish: Any = None
        self.callback_setup_threads.clear()

    def __setattr__(self, name: str, value: Any) -> None:
        """Record which thread installs Paho callbacks in the test fake."""
        if (
            name
            in {
                "on_connect",
                "on_subscribe",
                "on_disconnect",
                "on_message",
                "on_publish",
            }
            and "callback_setup_threads" in self.__dict__
        ):
            self.callback_setup_threads.append(threading.get_ident())
        object.__setattr__(self, name, value)

    def ws_set_options(self, *, path: str) -> None:
        """Check that the client supplied a signed MQTT WebSocket path."""
        assert path.startswith("/mqtt?")

    def tls_set(self) -> None:
        """Accept the client's TLS configuration request."""
        self.tls_thread = threading.get_ident()

    def connect(self, _host: str, *, port: int, keepalive: int) -> int:
        """Accept the network connect call with the expected transport settings."""
        assert port == 443
        assert keepalive == 60
        if self.fail_connect:
            self.connect_failed = True
            raise OSError("synthetic connect failure")
        return 0

    def subscribe(self, topics: list[tuple[str, int]]) -> tuple[int, int]:
        """Record the exact filters and return the next subscription MID."""
        self.subscriptions.append(topics)
        self._sub_mid = len(self.subscriptions) + 20
        return 0, self._sub_mid

    def publish(
        self,
        topic: str,
        payload: str,
        *,
        qos: int,
        retain: bool,
    ) -> tuple[int, int]:
        """Record a publication and optionally return an immediate response."""
        assert qos == 1
        data = json.loads(payload)
        self.publications.append((topic, data, retain))
        self._mid += 1
        mid = self._mid
        if self.on_publish is not None and self.publish_mode != "no_puback":
            self.on_publish(self, None, mid, 0, None)
        if data.get("MsgType") == 6 and self.publish_mode == "echo":
            self._deliver(
                topic.replace("/in", "/out"),
                {
                    "Device": data["Device"],
                    "MsgType": 5,
                    "EchoID": data["EchoID"],
                },
            )
        elif data.get("msg") == 44:
            self._deliver_command_ack(topic, data)
        if self.publish_mode == "disconnect":
            self.drop_requested = True
            if self.on_disconnect is not None:
                self.on_disconnect(self, None, None, 1, None)
        return 0, mid

    def _deliver_command_ack(self, topic: str, request: dict[str, Any]) -> None:
        if self.publish_mode in {"no_ack", "disconnect"}:
            return
        device_id = request["dest"]["ref"]
        command = request["body"]["cmd"][0]
        reported = {
            "md": command.get("md", 3),
            "sp": command.get("sp", 22.0),
            "ho": command.get("ho", 1),
        }
        source_type = 1
        success = 1
        retained = False
        response_id = request["id"]
        source_ref = device_id
        if self.publish_mode == "wrong_response":
            response_id += 1
        elif self.publish_mode == "app_source":
            source_type = 100
        elif self.publish_mode == "wrong_device":
            source_ref = OTHER_DEVICE
        elif self.publish_mode == "retained":
            retained = True
        elif self.publish_mode == "mismatch_state":
            reported["sp"] += 0.5
        elif self.publish_mode == "device_rejected":
            success = 0
        self._deliver(
            topic.replace("/in", "/out"),
            {
                "src": {"type": source_type, "ref": source_ref},
                "msg": 44,
                "resp_id": response_id,
                "body": {"success": success, "state": reported},
            },
            retained=retained,
        )

    def _deliver(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        retained: bool = False,
    ) -> None:
        if self.on_message is not None:
            packet = type(
                "Packet",
                (),
                {
                    "topic": topic,
                    "payload": json.dumps(payload).encode(),
                    "retain": retained,
                },
            )()
            self.on_message(self, None, packet)

    def loop(self, *, timeout: float) -> int:
        """Drive callbacks as Paho's blocking network loop would."""
        if self.connect_failed:
            return 1
        if not self.connected:
            self.connected = True
            self.on_connect(self, None, {}, 0, None)
            self.on_subscribe(
                self,
                None,
                self._sub_mid + self.suback_mid_offset,
                [self.suback_reason] * sum(len(group) for group in self.subscriptions),
                None,
            )
            if self.drop_after_connect:
                self._stop_after_setup = True
        if self._stop_after_setup or self.drop_requested:
            return 1
        time.sleep(min(timeout, 0.002))
        return 0

    def disconnect(self) -> int:
        """Accept the client's graceful disconnect request."""
        self.disconnect_calls += 1
        return 0


def _make_client(
    *,
    devices: dict[str, str] | None = None,
    publish_mode: str = "ack",
    connect_timeout: float = 1,
    command_timeout: float = 0.05,
    suback_reason: int = 0,
    suback_mid_offset: int = 0,
    factory: Any = None,
    token_provider: Any = None,
    token_calls: list[int] | None = None,
    credentials_calls: list[str] | None = None,
) -> tuple[MysaMqttClient, list[FakeClient]]:
    clients: list[FakeClient] = []

    def make_fake() -> FakeClient:
        if factory is not None:
            fake = factory(len(clients))
        else:
            fake = FakeClient(
                publish_mode=publish_mode,
                suback_reason=suback_reason,
                suback_mid_offset=suback_mid_offset,
            )
        clients.append(fake)
        return fake

    async def get_token() -> str:
        if token_calls is not None:
            token_calls.append(1)
        if token_provider is not None:
            return await token_provider()
        return ID_TOKEN

    def get_credentials(token: str) -> AwsCredentials:
        if credentials_calls is not None:
            credentials_calls.append(token)
        return AwsCredentials("access", "secret", "session")

    client = MysaMqttClient(
        get_token,
        devices or {DEVICE: MODEL},
        connect_timeout=connect_timeout,
        command_timeout=command_timeout,
        publish_timeout=0.05,
        shutdown_timeout=0.2,
        reconnect_min_delay=0.005,
        reconnect_max_delay=0.01,
        _client_factory=make_fake,
        _identity_credentials_provider=get_credentials,
        _sign_path=lambda _credentials: "/mqtt?signature=redacted",
    )
    return client, clients


async def _wait_until(predicate: Any, wait_timeout_seconds: float = 0.5) -> None:
    deadline = asyncio.get_running_loop().time() + wait_timeout_seconds
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("condition did not become true")
        await asyncio.sleep(0.002)


@pytest.mark.asyncio
async def test_connect_limits_subscriptions_and_live_status_and_echo() -> None:
    """Only allowlisted topics are subscribed to; live-status and echo correlate."""
    client, clients = _make_client(
        devices={DEVICE: MODEL, OTHER_DEVICE: "BB-V2-0"}, publish_mode="echo"
    )
    await client.connect()
    assert client.connected
    assert clients[0].subscriptions == [
        [(f"/v1/dev/{DEVICE}/out", 1), (f"/v1/dev/{OTHER_DEVICE}/out", 1)]
    ]
    live = await client.request_live_status(DEVICE, timeout_seconds=90)
    assert live.broker_accepted is True
    assert clients[0].publications[0] == (
        f"/v1/dev/{DEVICE}/in",
        {
            "Timestamp": clients[0].publications[0][1]["Timestamp"],
            "Device": DEVICE,
            "MsgType": 11,
            "Timeout": 90,
        },
        False,
    )
    echo = await client.echo(DEVICE, timeout_seconds=0.1)
    assert echo.broker_accepted is True
    assert echo.echoed
    await client.close()
    assert not client.connected


@pytest.mark.asyncio
async def test_paho_creation_and_tls_setup_stay_off_event_loop() -> None:
    """Constructing Paho and preparing TLS cannot block Home Assistant's loop."""
    loop_thread = threading.get_ident()
    client, clients = _make_client()
    await client.connect()
    fake = clients[0]
    assert fake.constructor_thread != loop_thread
    assert fake.tls_thread is not None
    assert fake.tls_thread != loop_thread
    assert fake.callback_setup_threads == [loop_thread] * 5
    assert client.connection_generation == 1
    await client.close()


@pytest.mark.asyncio
async def test_publish_timeout_reports_unknown_broker_acceptance() -> None:
    """Missing PUBACK is reported as unknown, not as a rejected publish."""
    client, _clients = _make_client(publish_mode="no_puback")
    await client.connect()
    result = await client.request_live_status(DEVICE, timeout_seconds=30)
    assert result.broker_accepted is None
    assert result.timed_out
    await client.close()


@pytest.mark.asyncio
async def test_setpoint_requires_correlated_device_ack_and_reports_puback() -> None:
    """A command is confirmed only by the matching device-origin state report."""
    client, clients = _make_client()
    await client.connect()
    result = await client.set_setpoint(DEVICE, 22.5)
    assert result.broker_accepted is True
    assert result.device_confirmed
    assert result.failure is None
    assert result.state is not None
    assert result.state.setpoint == 22.5
    topic, request, retain = clients[0].publications[0]
    assert topic == f"/v1/dev/{DEVICE}/in"
    assert retain is False
    assert request["src"] == {"type": 100, "ref": "test-subject"}
    assert request["dest"] == {"type": 1, "ref": DEVICE}
    assert isinstance(request["body"]["cmd"], list)
    assert request["body"]["cmd"] == [{"tm": -1, "sp": 22.5}]
    await client.close()


@pytest.mark.parametrize(
    ("publish_mode", "failure"),
    [
        ("no_ack", CommandFailure.TIMEOUT),
        ("wrong_response", CommandFailure.TIMEOUT),
        ("app_source", CommandFailure.TIMEOUT),
        ("wrong_device", CommandFailure.TIMEOUT),
        ("retained", CommandFailure.TIMEOUT),
        ("mismatch_state", CommandFailure.TIMEOUT),
        ("device_rejected", CommandFailure.DEVICE_REJECTED),
    ],
)
@pytest.mark.asyncio
async def test_invalid_or_missing_ack_never_confirms(
    publish_mode: str,
    failure: CommandFailure,
) -> None:
    """Correlation, source, retain, success, and state must all be valid."""
    client, _clients = _make_client(
        publish_mode=publish_mode,
        command_timeout=0.02,
    )
    await client.connect()
    result = await client.set_setpoint(DEVICE, 22.5)
    assert result.broker_accepted is True
    assert result.device_confirmed is False
    assert result.failure is failure
    await client.close()


@pytest.mark.asyncio
async def test_command_requires_lite_model_and_allowlisted_device() -> None:
    """Unsupported commands and unknown account IDs are rejected locally."""
    client, _clients = _make_client(devices={DEVICE: "BB-V2-0", OTHER_DEVICE: MODEL})
    await client.connect()
    with pytest.raises(MqttProtocolError):
        await client.set_setpoint(DEVICE, 22)
    with pytest.raises(ValueError, match="allowlist"):
        await client.request_live_status("not-owned")
    await client.close()


@pytest.mark.asyncio
async def test_reconnect_refreshes_token_credentials_and_subscribes_again() -> None:
    """A new connection cycle obtains fresh credentials and repeats SUBSCRIBE."""
    token_calls: list[int] = []
    credential_tokens: list[str] = []

    def factory(index: int) -> FakeClient:
        return FakeClient(drop_after_connect=index == 0)

    client, clients = _make_client(
        factory=factory,
        token_calls=token_calls,
        credentials_calls=credential_tokens,
    )
    await client.connect()
    assert client.connection_generation == 1
    await _wait_until(lambda: len(clients) >= 2 and client.connected)
    assert client.connection_generation >= 2
    assert len(token_calls) >= 2
    assert credential_tokens == [ID_TOKEN] * len(token_calls)
    assert clients[0].subscriptions == [[(f"/v1/dev/{DEVICE}/out", 1)]]
    assert clients[1].subscriptions == [[(f"/v1/dev/{DEVICE}/out", 1)]]
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("suback_reason", "suback_mid_offset"), [(128, 0), (0, 1)])
async def test_connect_rejects_failed_or_unexpected_suback(
    suback_reason: int,
    suback_mid_offset: int,
) -> None:
    """The initial connection is not ready until the exact SUBACK is accepted."""
    client, _clients = _make_client(
        suback_reason=suback_reason,
        suback_mid_offset=suback_mid_offset,
    )
    with pytest.raises(MqttConnectionError):
        await client.connect()


@pytest.mark.asyncio
async def test_connect_bounds_hanging_token_provider() -> None:
    """A stuck custom token provider cannot stall connect or close forever."""

    async def get_token() -> str:
        await asyncio.Future()
        return ID_TOKEN

    client, clients = _make_client(
        connect_timeout=0.03,
        token_provider=get_token,
    )
    with pytest.raises(MqttConnectionError):
        await asyncio.wait_for(client.connect(), timeout=0.2)
    assert clients == []
    await client.close()


@pytest.mark.asyncio
async def test_command_timeout_does_not_replay_after_reconnect() -> None:
    """A timed-out command is not replayed when the transport reconnects."""
    client, clients = _make_client(publish_mode="no_ack", command_timeout=0.02)
    await client.connect()
    result = await client.set_setpoint(DEVICE, 22.5)
    assert result.broker_accepted is True
    assert result.device_confirmed is False
    assert result.failure is CommandFailure.TIMEOUT
    assert len(clients[0].publications) == 1
    clients[0].drop_requested = True
    await _wait_until(lambda: len(clients) >= 2 and client.connected)
    assert clients[1].publications == []
    await client.close()


@pytest.mark.asyncio
async def test_disconnect_returns_unconfirmed_command_result() -> None:
    """A disconnect during publish cannot be reported as device confirmation."""
    client, clients = _make_client(publish_mode="disconnect", command_timeout=0.2)
    await client.connect()
    result = await client.set_setpoint(DEVICE, 22.5)
    assert result.broker_accepted is True
    assert result.device_confirmed is False
    assert result.failure is CommandFailure.DISCONNECTED
    assert len(clients[0].publications) == 1
    await client.close()


@pytest.mark.asyncio
async def test_cancelling_command_cleans_pending_correlations() -> None:
    """Caller cancellation removes command and PUBACK waiters immediately."""
    client, _clients = _make_client(publish_mode="no_ack", command_timeout=1)
    await client.connect()
    operation = asyncio.create_task(
        client.set_setpoint(DEVICE, 22.5, timeout_seconds=1)
    )
    await _wait_until(lambda: bool(client._pending_commands))
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert client._pending_commands == {}
    assert client._pending_pubacks == {}
    await client.close()


@pytest.mark.asyncio
async def test_network_worker_disconnects_client_after_connect_exception() -> None:
    """Transport cleanup runs even when Paho connect raises before CONNACK."""
    client, _clients = _make_client()
    fake = FakeClient(fail_connect=True)
    loop = asyncio.get_running_loop()
    client._network_loop(
        fake,
        asyncio.Event(),
        loop.create_future(),
        loop.create_future(),
        generation=1,
    )
    assert fake.disconnect_calls == 1
