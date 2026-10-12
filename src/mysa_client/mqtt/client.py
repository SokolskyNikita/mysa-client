"""Optional async runtime for Mysa's legacy AWS IoT MQTT protocol."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
import json
import math
import threading
import time
from typing import Any, Self, TypeVar
import uuid

from .auth import (
    AwsCredentials,
    get_identity_credentials,
    signed_websocket_path,
    user_pool_sub,
)
from .errors import (
    MqttAuthenticationError,
    MqttConnectionError,
    MqttError,
    MqttNotConnectedError,
    MqttProtocolError,
)
from .protocol import (
    DeviceCommandState,
    MessageKind,
    MqttMessage,
    encode_thermostat_command,
    parse_message,
    supports_commands,
)

REGION = "us-east-1"
IDENTITY_POOL_ID = "us-east-1:ebd95d52-9995-45da-b059-56b865a18379"
USER_POOL_ID = "us-east-1_GUFWfhI7g"
ENDPOINT = "a3q27gia9qg3zy-ats.iot.us-east-1.amazonaws.com"
_TOPIC_PREFIX = "/v1/dev/"
_TOPIC_SUFFIX = "/out"
_KEEPALIVE = 60
_QOS = 1
_T = TypeVar("_T")


class CommandFailure(StrEnum):
    """Why a command did not receive a complete confirmation."""

    TIMEOUT = "timeout"
    DISCONNECTED = "disconnected"
    PUBLISH_REJECTED = "publish_rejected"
    DEVICE_REJECTED = "device_rejected"


@dataclass(frozen=True, slots=True)
class PublishResult:
    """Broker acceptance for a QoS 1 non-command message."""

    broker_accepted: bool | None
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class EchoResult:
    """Broker acceptance and device echo response for a liveness probe."""

    broker_accepted: bool | None
    echoed: bool
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Separate the MQTT PUBACK from device-reported command confirmation."""

    request_id: int
    broker_accepted: bool | None
    device_confirmed: bool
    state: DeviceCommandState | None = None
    failure: CommandFailure | None = None


@dataclass(slots=True)
class _PendingCommand:
    device_id: str = field(repr=False)
    generation: int
    expected: Mapping[str, int | float]
    puback: asyncio.Future[bool | None]
    acknowledgement: asyncio.Future[MqttMessage]
    disconnected: asyncio.Future[None]
    rejected: asyncio.Future[None]
    mid: int | None = None


@dataclass(slots=True)
class _EchoWaiter:
    device_id: str = field(repr=False)
    generation: int
    future: asyncio.Future[None]


@dataclass(slots=True)
class _ConnectionCycle:
    client: Any
    ready: asyncio.Future[None]
    disconnected: asyncio.Future[None]
    stop: threading.Event
    generation: int


class MysaMqttClient:
    """Opt-in MQTT client for an explicit allowlist of account devices.

    Pass a token provider such as `auth.async_get_id_token`, and an account
    device-ID-to-model mapping. Only those IDs are subscribed to or published
    to. The optional Paho, boto3, and botocore dependencies load on connect.
    """

    def __init__(
        self,
        token_provider: Callable[[], Awaitable[str]],
        devices: Mapping[str, str],
        *,
        region: str = REGION,
        endpoint: str = ENDPOINT,
        identity_pool_id: str = IDENTITY_POOL_ID,
        user_pool_id: str = USER_POOL_ID,
        event_queue_size: int = 100,
        connect_timeout: float = 20,
        publish_timeout: float = 5,
        command_timeout: float = 15,
        shutdown_timeout: float = 7,
        reconnect_min_delay: float = 1,
        reconnect_max_delay: float = 20,
        _client_factory: Callable[[], Any] | None = None,
        _identity_credentials_provider: Callable[[str], AwsCredentials] | None = None,
        _sign_path: Callable[[AwsCredentials], str] | None = None,
    ) -> None:
        """Configure an allowlisted MQTT connection without opening it."""
        if not devices or any(
            not isinstance(device_id, str)
            or not device_id
            or any(char in device_id for char in "/#+\x00")
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in device_id)
            or not isinstance(model, str)
            or not model
            for device_id, model in devices.items()
        ):
            raise ValueError("devices must map non-empty device IDs to model names")
        if (
            isinstance(event_queue_size, bool)
            or not isinstance(event_queue_size, int)
            or event_queue_size < 1
        ):
            raise ValueError("event_queue_size must be positive")
        for name, value in (
            ("connect_timeout", connect_timeout),
            ("publish_timeout", publish_timeout),
            ("command_timeout", command_timeout),
            ("shutdown_timeout", shutdown_timeout),
        ):
            _validate_timeout(name, value)
        _validate_delay("reconnect_min_delay", reconnect_min_delay)
        _validate_delay("reconnect_max_delay", reconnect_max_delay)
        if reconnect_max_delay < reconnect_min_delay:
            raise ValueError("invalid reconnect delay bounds")
        if not region or not endpoint or "/" in endpoint:
            raise ValueError("region and bare MQTT endpoint are required")

        self._token_provider = token_provider
        self._devices = dict(devices)
        self._region = region
        self._endpoint = endpoint
        self._identity_pool_id = identity_pool_id
        self._user_pool_id = user_pool_id
        self._connect_timeout = connect_timeout
        self._publish_timeout = publish_timeout
        self._command_timeout = command_timeout
        self._shutdown_timeout = shutdown_timeout
        self._reconnect_min_delay = reconnect_min_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._client_factory = _client_factory
        self._identity_credentials_provider = _identity_credentials_provider
        self._sign_path = _sign_path

        self._events: asyncio.Queue[MqttMessage | None] = asyncio.Queue(
            maxsize=event_queue_size
        )
        self._dropped_events = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._first_ready: asyncio.Future[None] | None = None
        self._supervisor: asyncio.Task[None] | None = None
        self._close_requested: asyncio.Event | None = None
        self._connected = False
        self._ever_connected = False
        self._closing = False
        self._current_client: Any | None = None
        self._current_stop: threading.Event | None = None
        self._pending_commands: dict[int, _PendingCommand] = {}
        self._pending_pubacks: dict[tuple[int, int], asyncio.Future[bool | None]] = {}
        self._echo_waiters: dict[tuple[str, int], _EchoWaiter] = {}
        self._request_id = 0
        self._echo_id = int(time.time()) % 2_000_000_000
        self._generation = 0
        self._connection_generation = 0
        self._current_disconnected: asyncio.Future[None] | None = None

    @property
    def connected(self) -> bool:
        """Whether CONNECT and all allowlisted SUBACKs have succeeded."""
        return self._connected

    @property
    def connection_generation(self) -> int:
        """Count of MQTT sessions that completed CONNECT and all SUBACKs.

        The value increases after every successfully subscribed session,
        including automatic reconnects, and is retained across close/connect
        cycles on this client. Consumers can compare it to detect a reconnect
        that happened between their own polling ticks.
        """
        return self._connection_generation

    @property
    def dropped_events(self) -> int:
        """Number of events dropped because the bounded queue was full."""
        return self._dropped_events

    async def connect(self) -> None:
        """Connect and wait for broker acceptance of all device subscriptions."""
        if self._supervisor is not None:
            if self._connected:
                return
            raise MqttConnectionError("MQTT connection is already starting")
        self._loop = asyncio.get_running_loop()
        if self._closing:
            self._events = asyncio.Queue(maxsize=self._events.maxsize)
            self._dropped_events = 0
            self._ever_connected = False
        self._first_ready = self._loop.create_future()
        self._close_requested = asyncio.Event()
        self._closing = False
        self._supervisor = asyncio.create_task(self._supervise())
        try:
            await asyncio.wait_for(
                asyncio.shield(self._first_ready), timeout=self._connect_timeout
            )
        except TimeoutError:
            await self.close()
            raise MqttConnectionError(
                "Timed out waiting for MQTT subscriptions"
            ) from None
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        """Stop reconnecting and bound cleanup of the Paho network worker."""
        self._closing = True
        self._generation += 1
        self._set_first_error(
            MqttConnectionError("MQTT client closed before connecting")
        )
        if self._close_requested is not None:
            self._close_requested.set()
        if self._current_stop is not None:
            self._current_stop.set()
        if self._current_disconnected is not None:
            _set_future_result(self._current_disconnected, None)
        supervisor = self._supervisor
        if supervisor is not None and supervisor is not asyncio.current_task():
            try:
                await asyncio.wait_for(
                    asyncio.shield(supervisor), timeout=self._shutdown_timeout
                )
            except TimeoutError:
                supervisor.cancel()
                with suppress(TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(supervisor, timeout=1)
        self._connected = False
        self._fail_waiters(disconnected=True)
        self._queue_sentinel()
        self._supervisor = None
        self._current_client = None
        self._current_stop = None
        self._current_disconnected = None

    async def __aenter__(self) -> Self:
        """Connect and return this client for an async context."""
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        """Close the connection when leaving an async context."""
        await self.close()

    async def events(self) -> AsyncIterator[MqttMessage]:
        """Yield parsed events from the bounded queue; one consumer owns it."""
        while not self._closing:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def request_live_status(
        self,
        device_id: str,
        *,
        timeout_seconds: int = 180,
    ) -> PublishResult:
        """Ask an allowlisted device to publish live telemetry for 180 seconds."""
        self._require_device(device_id)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 180
        ):
            raise ValueError("timeout_seconds must be between 1 and 180")
        now = int(time.time())
        message = {
            "Timestamp": now,
            "Device": device_id,
            "MsgType": 11,
            "Timeout": timeout_seconds,
        }
        return await self._publish_payload(device_id, message)

    async def echo(
        self, device_id: str, *, timeout_seconds: float | None = None
    ) -> EchoResult:
        """Send the legacy echo probe and wait for the matching device reply."""
        self._require_device(device_id)
        duration = self._publish_timeout if timeout_seconds is None else timeout_seconds
        _validate_timeout("timeout_seconds", duration)
        self._require_connected()
        self._echo_id = (self._echo_id + 1) % 2_000_000_000
        echo_id = self._echo_id
        key = (device_id, echo_id)
        assert self._loop is not None
        echoed = self._loop.create_future()
        deadline = self._loop.time() + duration
        self._echo_waiters[key] = _EchoWaiter(device_id, self._generation, echoed)
        try:
            result = await self._publish_payload(
                device_id,
                {
                    "Timestamp": int(time.time()),
                    "Device": device_id,
                    "MsgType": 6,
                    "EventType": 4294967295,
                    "EchoID": echo_id,
                },
                timeout_seconds=duration,
            )
            if result.broker_accepted is False:
                return EchoResult(False, False)
            if echoed.done():
                return EchoResult(result.broker_accepted, True)
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                return EchoResult(
                    result.broker_accepted, False, timed_out=result.timed_out
                )
            try:
                await asyncio.wait_for(asyncio.shield(echoed), timeout=remaining)
            except TimeoutError:
                return EchoResult(result.broker_accepted, False, timed_out=True)
            return EchoResult(result.broker_accepted, True)
        finally:
            self._echo_waiters.pop(key, None)
            if not echoed.done():
                echoed.cancel()

    async def set_setpoint(
        self,
        device_id: str,
        celsius: float,
        *,
        mode: str | None = None,
        hold: bool | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        """Set a Lite target and optionally include mode or hold state."""
        return await self._command(
            device_id,
            setpoint=celsius,
            mode=mode,
            hold=hold,
            timeout_seconds=timeout_seconds,
        )

    async def set_mode(
        self,
        device_id: str,
        mode: str,
        *,
        setpoint: float | None = None,
        hold: bool | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        """Set Lite mode to `off` or `heat`, optionally with a target or hold."""
        return await self._command(
            device_id,
            setpoint=setpoint,
            mode=mode,
            hold=hold,
            timeout_seconds=timeout_seconds,
        )

    async def _command(
        self,
        device_id: str,
        *,
        setpoint: float | None,
        mode: str | None,
        hold: bool | None,
        timeout_seconds: float | None,
    ) -> CommandResult:
        self._require_device(device_id)
        if not supports_commands(self._devices[device_id]):
            raise MqttProtocolError(
                "Commands are currently supported only for Baseboard Lite"
            )
        self._require_connected()
        duration = self._command_timeout if timeout_seconds is None else timeout_seconds
        _validate_timeout("timeout_seconds", duration)
        assert self._loop is not None
        assert self._current_client is not None

        generation = self._generation
        deadline = self._loop.time() + duration
        request_id = self._next_request_id()
        try:
            id_token = await _await_bounded(self._get_token(), _remaining(deadline))
        except TimeoutError:
            return CommandResult(
                request_id,
                broker_accepted=None,
                device_confirmed=False,
                failure=CommandFailure.TIMEOUT,
            )
        if not self._connected or generation != self._generation:
            raise MqttNotConnectedError(
                "MQTT connection changed while preparing the command"
            )
        subject = user_pool_sub(id_token)
        envelope, expected = encode_thermostat_command(
            device_id=device_id,
            user_sub=subject,
            request_id=request_id,
            command_time=int(time.time()),
            setpoint=setpoint,
            mode=mode,
            hold=hold,
        )
        pending = _PendingCommand(
            device_id=device_id,
            generation=self._generation,
            expected=expected,
            puback=self._loop.create_future(),
            acknowledgement=self._loop.create_future(),
            disconnected=self._loop.create_future(),
            rejected=self._loop.create_future(),
        )
        self._pending_commands[request_id] = pending
        try:
            info = self._current_client.publish(
                f"/v1/dev/{device_id}/in",
                json.dumps(envelope, separators=(",", ":")),
                qos=_QOS,
                retain=False,
            )
            rc, mid = _publish_result(info)
            if rc != 0:
                self._pending_commands.pop(request_id, None)
                return CommandResult(
                    request_id,
                    broker_accepted=False,
                    device_confirmed=False,
                    failure=CommandFailure.PUBLISH_REJECTED,
                )
            pending.mid = mid
            self._pending_pubacks[(pending.generation, mid)] = pending.puback
            return await self._wait_for_command(request_id, pending, deadline)
        except asyncio.CancelledError:
            raise
        except MqttProtocolError:
            raise
        except MqttError:
            raise
        except Exception:  # noqa: BLE001 - sanitize credential-bearing Paho errors
            # Paho can include a signed request URL or message data in exceptions.
            return CommandResult(
                request_id,
                broker_accepted=False,
                device_confirmed=False,
                failure=CommandFailure.PUBLISH_REJECTED,
            )
        finally:
            self._pending_commands.pop(request_id, None)
            if pending.mid is not None:
                self._pending_pubacks.pop((pending.generation, pending.mid), None)
            for future in (
                pending.puback,
                pending.acknowledgement,
                pending.disconnected,
                pending.rejected,
            ):
                if not future.done():
                    future.cancel()

    async def _wait_for_command(
        self,
        request_id: int,
        pending: _PendingCommand,
        deadline: float,
    ) -> CommandResult:
        waiters: set[asyncio.Future[Any]] = {
            pending.puback,
            pending.acknowledgement,
            pending.disconnected,
            pending.rejected,
        }
        while waiters:
            done = {future for future in waiters if future.done()}
            if not done:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                done, waiters = await asyncio.wait(
                    waiters,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            if not done:
                break
            waiters.difference_update(done)
            if pending.acknowledgement in done:
                message = pending.acknowledgement.result()
                return CommandResult(
                    request_id,
                    broker_accepted=(
                        pending.puback.result() if pending.puback.done() else None
                    ),
                    device_confirmed=True,
                    state=message.state,
                )
            if pending.rejected in done:
                return CommandResult(
                    request_id,
                    broker_accepted=(
                        pending.puback.result() if pending.puback.done() else None
                    ),
                    device_confirmed=False,
                    failure=CommandFailure.DEVICE_REJECTED,
                )
            if pending.disconnected in done:
                return CommandResult(
                    request_id,
                    broker_accepted=(
                        pending.puback.result() if pending.puback.done() else None
                    ),
                    device_confirmed=False,
                    failure=CommandFailure.DISCONNECTED,
                )
            if pending.puback in done:
                accepted = pending.puback.result()
                if accepted is False:
                    return CommandResult(
                        request_id,
                        broker_accepted=False,
                        device_confirmed=False,
                        failure=CommandFailure.PUBLISH_REJECTED,
                    )
        return CommandResult(
            request_id,
            broker_accepted=pending.puback.result() if pending.puback.done() else None,
            device_confirmed=False,
            failure=CommandFailure.TIMEOUT,
        )

    async def _publish_payload(
        self,
        device_id: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> PublishResult:
        self._require_connected()
        assert self._loop is not None
        assert self._current_client is not None
        try:
            info = self._current_client.publish(
                f"/v1/dev/{device_id}/in",
                json.dumps(payload, separators=(",", ":")),
                qos=_QOS,
                retain=False,
            )
            rc, mid = _publish_result(info)
        except Exception:  # noqa: BLE001 - Paho errors may contain signed URLs
            return PublishResult(False)
        if rc != 0:
            return PublishResult(False)
        puback = self._loop.create_future()
        generation = self._generation
        self._pending_pubacks[(generation, mid)] = puback
        duration = self._publish_timeout if timeout_seconds is None else timeout_seconds
        _validate_timeout("timeout_seconds", duration)
        try:
            try:
                accepted = await asyncio.wait_for(
                    asyncio.shield(puback), timeout=duration
                )
            except TimeoutError:
                return PublishResult(None, timed_out=True)
            return PublishResult(accepted)
        finally:
            self._pending_pubacks.pop((generation, mid), None)
            if not puback.done():
                puback.cancel()

    async def _supervise(self) -> None:
        delay = self._reconnect_min_delay
        try:
            while not self._closing:
                try:
                    await self._connection_cycle()
                except asyncio.CancelledError:
                    raise
                except MqttError as err:
                    if not self._ever_connected:
                        self._set_first_error(err)
                        return
                except Exception:  # noqa: BLE001 - hide auth and transport internals
                    if not self._ever_connected:
                        self._set_first_error(
                            MqttConnectionError("Could not establish MQTT connection")
                        )
                        return
                self._connected = False
                self._fail_waiters(disconnected=True)
                await self._sleep_or_close(delay)
                delay = min(
                    max(delay * 2, self._reconnect_min_delay), self._reconnect_max_delay
                )
        finally:
            self._connected = False
            if not self._closing:
                self._fail_waiters(disconnected=True)

    async def _connection_cycle(self) -> None:
        assert self._loop is not None
        deadline = self._loop.time() + self._connect_timeout
        try:
            token = await _await_bounded(self._get_token(), _remaining(deadline))
        except TimeoutError:
            raise MqttConnectionError(
                "Timed out obtaining MQTT authentication"
            ) from None
        credentials_provider = self._identity_credentials_provider
        if credentials_provider is None:

            def credentials_provider(value: str) -> AwsCredentials:
                return get_identity_credentials(
                    value,
                    region=self._region,
                    identity_pool_id=self._identity_pool_id,
                    user_pool_id=self._user_pool_id,
                )

        try:
            credentials = await _await_bounded(
                asyncio.to_thread(credentials_provider, token), _remaining(deadline)
            )
        except TimeoutError:
            raise MqttConnectionError(
                "Timed out obtaining MQTT authentication"
            ) from None
        signer = self._sign_path
        if signer is None:

            def signer(value: AwsCredentials) -> str:
                return signed_websocket_path(
                    value, endpoint=self._endpoint, region=self._region
                )

        try:
            signed_path = await _await_bounded(
                asyncio.to_thread(signer, credentials), _remaining(deadline)
            )
        except TimeoutError:
            raise MqttConnectionError(
                "Timed out preparing MQTT authentication"
            ) from None
        if not signed_path.startswith("/mqtt?"):
            raise MqttAuthenticationError("Could not prepare the MQTT request")

        try:
            client = await _await_bounded(
                asyncio.to_thread(self._make_client), _remaining(deadline)
            )
        except TimeoutError:
            raise MqttConnectionError(
                "Timed out preparing the MQTT transport"
            ) from None
        self._generation += 1
        generation = self._generation
        ready: asyncio.Future[None] = self._loop.create_future()
        disconnected: asyncio.Future[None] = self._loop.create_future()
        stop = threading.Event()
        self._current_client = client
        self._current_stop = stop
        self._current_disconnected = disconnected
        cycle = _ConnectionCycle(client, ready, disconnected, stop, generation)
        worker: asyncio.Task[None] | None = None
        try:
            self._install_callbacks(client, signed_path, cycle)
            worker = asyncio.create_task(
                asyncio.to_thread(
                    self._network_loop,
                    client,
                    stop,
                    ready,
                    disconnected,
                    generation,
                )
            )
            done, _pending = await asyncio.wait(
                {ready, disconnected},
                timeout=_remaining(deadline),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise MqttConnectionError("Timed out waiting for MQTT subscriptions")
            if ready not in done:
                raise MqttConnectionError(
                    "The MQTT connection ended before subscriptions completed"
                )
            ready.result()
            self._connection_generation += 1
            self._connected = True
            self._ever_connected = True
            if self._first_ready is not None and not self._first_ready.done():
                self._first_ready.set_result(None)
            await disconnected
        finally:
            self._connected = False
            stop.set()
            if not ready.done():
                ready.cancel()
            _set_future_result(disconnected, None)
            if worker is not None:
                done, _pending = await asyncio.wait(
                    {worker}, timeout=self._shutdown_timeout
                )
                worker.add_done_callback(_consume_future_result)
                if not done:
                    worker.cancel()
            if generation == self._generation:
                self._current_client = None
                self._current_stop = None
                self._current_disconnected = None

    def _install_callbacks(
        self,
        client: Any,
        signed_path: str,
        cycle: _ConnectionCycle,
    ) -> None:
        loop = self._loop
        assert loop is not None
        ready = cycle.ready
        disconnected = cycle.disconnected
        stop = cycle.stop
        generation = cycle.generation
        subscription_mid: int | None = None

        def is_current() -> bool:
            return generation == self._generation and not self._closing

        try:
            client.ws_set_options(path=signed_path)
            if hasattr(client, "connect_timeout"):
                client.connect_timeout = min(self._connect_timeout, 8)
        except Exception:  # noqa: BLE001 - redact Paho configuration details
            raise MqttConnectionError("Could not configure MQTT transport") from None

        def on_connect(
            _client: Any,
            _userdata: Any,
            _flags: Any,
            reason_code: Any,
            _properties: Any = None,
        ) -> None:
            nonlocal subscription_mid
            if not is_current():
                return
            if _reason_value(reason_code) != 0:
                loop.call_soon_threadsafe(
                    _set_future_exception,
                    ready,
                    MqttConnectionError("The MQTT broker rejected the connection"),
                )
                stop.set()
                return
            subscriptions = [
                (f"/v1/dev/{device_id}/out", _QOS) for device_id in self._devices
            ]
            try:
                result = _client.subscribe(subscriptions)
                rc, mid = _subscribe_result(result)
                if rc == 0:
                    subscription_mid = mid
            except Exception:  # noqa: BLE001 - redact subscription client errors
                rc = 1
            if rc != 0:
                loop.call_soon_threadsafe(
                    _set_future_exception,
                    ready,
                    MqttConnectionError("The MQTT subscriptions could not be sent"),
                )
                stop.set()

        def on_subscribe(
            _client: Any,
            _userdata: Any,
            _mid: int,
            reason_codes: Any,
            _properties: Any = None,
        ) -> None:
            if not is_current():
                return
            if _mid != subscription_mid:
                loop.call_soon_threadsafe(
                    _set_future_exception,
                    ready,
                    MqttConnectionError(
                        "The MQTT broker returned an unexpected subscription"
                    ),
                )
                stop.set()
                return
            reasons = (
                reason_codes
                if isinstance(reason_codes, (list, tuple))
                else [reason_codes]
            )
            if len(reasons) != len(self._devices) or any(
                not _subscription_granted(reason) for reason in reasons
            ):
                loop.call_soon_threadsafe(
                    _set_future_exception,
                    ready,
                    MqttConnectionError(
                        "The MQTT broker rejected a device subscription"
                    ),
                )
                stop.set()
                return
            loop.call_soon_threadsafe(_set_future_result, ready, None)

        def on_disconnect(
            _client: Any,
            _userdata: Any,
            _disconnect_flags: Any = None,
            _reason_code: Any = None,
            _properties: Any = None,
        ) -> None:
            if not is_current():
                return
            loop.call_soon_threadsafe(
                self._mark_disconnected, ready, disconnected, generation
            )

        def on_message(_client: Any, _userdata: Any, packet: Any) -> None:
            if not is_current():
                return
            topic = getattr(packet, "topic", "")
            device_id = self._device_from_topic(topic)
            if device_id is None:
                return
            raw_payload = bytes(getattr(packet, "payload", b""))
            message = parse_message(
                device_id,
                raw_payload,
                retained=bool(getattr(packet, "retain", False)),
            )
            loop.call_soon_threadsafe(self._handle_message, message, generation)

        def on_publish(
            _client: Any,
            _userdata: Any,
            mid: int,
            reason_code: Any = 0,
            _properties: Any = None,
        ) -> None:
            if not is_current():
                return
            accepted = not _reason_failed(reason_code)
            loop.call_soon_threadsafe(self._handle_puback, mid, accepted, generation)

        client.on_connect = on_connect
        client.on_subscribe = on_subscribe
        client.on_disconnect = on_disconnect
        client.on_message = on_message
        client.on_publish = on_publish

    def _make_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        try:
            import paho.mqtt.client as mqtt

            return mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
                client_id=str(uuid.uuid4()),
                protocol=mqtt.MQTTv311,
                transport="websockets",
                reconnect_on_failure=False,
            )
        except Exception:  # noqa: BLE001 - optional import/config errors are redacted
            raise MqttConnectionError(
                "MQTT support requires the optional paho-mqtt dependency"
            ) from None

    def _network_loop(
        self,
        client: Any,
        stop: threading.Event,
        ready: asyncio.Future[None],
        disconnected: asyncio.Future[None],
        generation: int,
    ) -> None:
        try:
            # Paho builds the SSL context and may load certificate files here;
            # keep that blocking setup off Home Assistant's event loop.
            client.tls_set()
            result = client.connect(self._endpoint, port=443, keepalive=_KEEPALIVE)
            if result != 0:
                return
            while not stop.is_set():
                result = client.loop(timeout=1.0)
                if result != 0:
                    return
        except Exception:  # noqa: BLE001 - Paho exceptions may contain signed paths
            # Paho exception text can contain the credential-bearing websocket URL.
            return
        finally:
            with suppress(Exception):
                client.disconnect()
                client.loop(timeout=0.1)
            self._notify_network_stopped(ready, disconnected, generation)

    def _notify_network_stopped(
        self,
        ready: asyncio.Future[None],
        disconnected: asyncio.Future[None],
        generation: int,
    ) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(
                    self._mark_disconnected, ready, disconnected, generation
                )

    def _mark_disconnected(
        self,
        ready: asyncio.Future[None],
        disconnected: asyncio.Future[None],
        generation: int,
    ) -> None:
        if generation != self._generation:
            return
        if self._connected:
            self._connected = False
            self._fail_waiters(disconnected=True)
        if not ready.done() and not self._closing:
            _set_future_exception(
                ready,
                MqttConnectionError(
                    "The MQTT connection ended before subscriptions completed"
                ),
            )
        _set_future_result(disconnected, None)

    def _handle_message(self, message: MqttMessage, generation: int) -> None:
        if self._closing or generation != self._generation:
            return
        if self._events.full():
            with suppress(asyncio.QueueEmpty):
                self._events.get_nowait()
            self._dropped_events += 1
        self._events.put_nowait(message)
        if (
            message.kind is MessageKind.ACKNOWLEDGEMENT
            and message.response_id is not None
        ):
            pending = self._pending_commands.get(message.response_id)
            if (
                pending is not None
                and pending.generation == self._generation
                and pending.device_id == message.device_id
                and not message.retained
            ):
                if message.success is False:
                    _set_future_result(pending.rejected, None)
                elif message.success is True and _state_matches(
                    message.state, pending.expected
                ):
                    _set_future_result(pending.acknowledgement, message)
        if message.kind is MessageKind.ECHO_REPLY and message.echo_id is not None:
            waiter = self._echo_waiters.get((message.device_id, message.echo_id))
            if (
                waiter is not None
                and waiter.generation == generation
                and not message.retained
            ):
                _set_future_result(waiter.future, None)

    def _handle_puback(self, mid: int, accepted: bool, generation: int) -> None:
        future = self._pending_pubacks.pop((generation, mid), None)
        if future is not None:
            _set_future_result(future, accepted)

    def _fail_waiters(self, *, disconnected: bool) -> None:
        for pending in tuple(self._pending_commands.values()):
            if disconnected:
                _set_future_result(pending.disconnected, None)
        for future in tuple(self._pending_pubacks.values()):
            _set_future_result(future, None)
        self._pending_pubacks.clear()

    def _device_from_topic(self, topic: Any) -> str | None:
        if not isinstance(topic, str) or not topic.startswith(_TOPIC_PREFIX):
            return None
        if not topic.endswith(_TOPIC_SUFFIX):
            return None
        device_id = topic[len(_TOPIC_PREFIX) : -len(_TOPIC_SUFFIX)]
        if "/" in device_id or device_id not in self._devices:
            return None
        return device_id

    def _require_device(self, device_id: str) -> None:
        if device_id not in self._devices:
            raise ValueError("device is not in this client's allowlist")

    def _require_connected(self) -> None:
        if not self._connected or self._current_client is None:
            raise MqttNotConnectedError("MQTT client is not connected")

    def _next_request_id(self) -> int:
        now_ms = int(time.time() * 1000)
        self._request_id = max(now_ms, self._request_id + 1)
        return self._request_id

    async def _get_token(self) -> str:
        try:
            token = await self._token_provider()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - sanitize arbitrary token provider exceptions
            raise MqttAuthenticationError("The ID token provider failed") from None
        if not isinstance(token, str) or not token:
            raise MqttAuthenticationError("The ID token provider returned no token")
        return token

    async def _sleep_or_close(self, delay: float) -> None:
        if delay <= 0 or self._close_requested is None:
            return
        with suppress(TimeoutError):
            await asyncio.wait_for(self._close_requested.wait(), timeout=delay)

    def _set_first_error(self, error: MqttError) -> None:
        if self._first_ready is not None and not self._first_ready.done():
            self._first_ready.set_exception(error)

    def _queue_sentinel(self) -> None:
        if self._events.full():
            with suppress(asyncio.QueueEmpty):
                self._events.get_nowait()
        with suppress(asyncio.QueueFull):
            self._events.put_nowait(None)


def _reason_value(reason: Any) -> int | None:
    value = getattr(reason, "value", reason)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _reason_failed(reason: Any) -> bool:
    is_failure = getattr(reason, "is_failure", None)
    if isinstance(is_failure, bool):
        return is_failure
    value = _reason_value(reason)
    return value is None or not 0 <= value < 128


def _subscription_granted(reason: Any) -> bool:
    if _reason_failed(reason):
        return False
    return _reason_value(reason) in (0, 1)


def _publish_result(result: Any) -> tuple[int, int]:
    if hasattr(result, "rc") and hasattr(result, "mid"):
        return int(result.rc), int(result.mid)
    if isinstance(result, tuple) and len(result) >= 2:
        return int(result[0]), int(result[1])
    raise MqttConnectionError("The MQTT client returned an invalid publish result")


def _subscribe_result(result: Any) -> tuple[int, int]:
    if isinstance(result, tuple) and len(result) >= 2:
        return int(result[0]), int(result[1])
    raise MqttConnectionError("The MQTT client returned an invalid subscription result")


def _set_future_result(future: asyncio.Future[Any], result: Any) -> None:
    if not future.done():
        future.set_result(result)


def _set_future_exception(future: asyncio.Future[Any], error: Exception) -> None:
    if not future.done():
        future.set_exception(error)


def _validate_timeout(name: str, value: float) -> None:
    try:
        valid = (
            not isinstance(value, bool)
            and isinstance(value, int | float)
            and math.isfinite(value)
            and value > 0
        )
    except (OverflowError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite positive number")


def _validate_delay(name: str, value: float) -> None:
    try:
        valid = (
            not isinstance(value, bool)
            and isinstance(value, int | float)
            and math.isfinite(value)
            and value >= 0
        )
    except (OverflowError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite non-negative number")


def _remaining(deadline: float) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    return remaining


async def _await_bounded(awaitable: Awaitable[_T], wait_timeout_seconds: float) -> _T:
    """Wait without hanging shutdown on a cancellation-resistant provider."""
    _validate_timeout("wait_timeout_seconds", wait_timeout_seconds)
    task = asyncio.ensure_future(awaitable)
    try:
        done, _pending = await asyncio.wait({task}, timeout=wait_timeout_seconds)
    except asyncio.CancelledError:
        task.cancel()
        task.add_done_callback(_consume_future_result)
        raise
    if not done:
        task.cancel()
        task.add_done_callback(_consume_future_result)
        raise TimeoutError
    return task.result()


def _consume_future_result(future: asyncio.Future[Any]) -> None:
    with suppress(asyncio.CancelledError, Exception):
        future.result()


def _state_matches(
    state: DeviceCommandState | None,
    expected: Mapping[str, int | float],
) -> bool:
    if state is None:
        return False
    raw = state.raw
    for key, expected_value in expected.items():
        observed = raw.get(key)
        if isinstance(expected_value, int):
            if isinstance(observed, bool) or observed != expected_value:
                return False
            continue
        if isinstance(observed, bool) or not isinstance(observed, int | float):
            return False
        if (
            not math.isfinite(float(observed))
            or abs(float(observed) - expected_value) > 0.01
        ):
            return False
    return True
