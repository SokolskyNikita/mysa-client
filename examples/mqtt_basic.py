"""Connect to Mysa MQTT, read events, and write only when explicitly requested."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import math
import os
from pathlib import Path
import tempfile
import time

import aiohttp

from mysa_client.cloud import MysaAuth, MysaClient
from mysa_client.mqtt import MessageKind, MysaMqttClient, supports_commands

USER_AGENT = "mysa-client-mqtt-example/0.1"


def save_secret(path: Path, value: str) -> None:
    """Atomically save a credential file with owner-only permissions."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary.name, 0o600)
            temporary.write(value)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def load_refresh_token(path: Path) -> str | None:
    """Load a saved refresh token without printing it."""
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None


def refresh_token_path() -> Path:
    """Resolve the caller's credential storage location."""
    return Path(
        os.environ.get(
            "MYSA_REFRESH_TOKEN_FILE",
            Path.home() / ".config" / "mysa-client" / "refresh-token",
        )
    ).expanduser()


def describe_event(event: object) -> str:
    """Summarize an event without printing a device ID or raw payload."""
    kind = getattr(event, "kind", MessageKind.OTHER)
    if kind is MessageKind.TELEMETRY:
        telemetry = getattr(event, "telemetry", None)
        if telemetry is None:
            return "Telemetry event"
        return (
            "Telemetry: "
            f"room={telemetry.room_temperature!r} °C, "
            f"setpoint={telemetry.setpoint!r} °C, "
            f"humidity={telemetry.humidity!r}%, "
            f"duty cycle={telemetry.duty_cycle!r}"
        )
    if kind is MessageKind.ACKNOWLEDGEMENT:
        state = getattr(event, "state", None)
        return (
            "Device acknowledgement: "
            f"success={getattr(event, 'success', None)!r}, "
            f"mode={getattr(state, 'mode', None)!r}, "
            f"setpoint={getattr(state, 'setpoint', None)!r} °C"
        )
    if kind is MessageKind.ECHO_REPLY:
        return f"Echo reply: id={getattr(event, 'echo_id', None)!r}"
    if kind is MessageKind.MALFORMED:
        return f"Malformed event: {getattr(event, 'parse_error', 'unknown error')}"
    return f"Other MQTT event: message type={getattr(event, 'message_type', None)!r}"


async def listen_events(mqtt: MysaMqttClient, duration: float) -> None:
    """Print parsed events for a bounded interval."""
    iterator = mqtt.events()
    deadline = time.monotonic() + duration
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            event = await asyncio.wait_for(anext(iterator), timeout=remaining)
        except TimeoutError:
            return
        print(describe_event(event))


async def main(args: argparse.Namespace) -> None:
    """Authenticate, subscribe to account devices, and optionally set a target."""
    if args.duration <= 0:
        raise ValueError("--duration must be positive")

    token_path = await asyncio.to_thread(refresh_token_path)
    email = os.environ.get("MYSA_EMAIL")
    password = os.environ.get("MYSA_PASSWORD")
    refresh_token = await asyncio.to_thread(load_refresh_token, token_path)
    if refresh_token is None and not (email and password):
        raise RuntimeError(
            "Set MYSA_EMAIL and MYSA_PASSWORD for first sign-in, or provide a "
            "saved refresh token and credentials for reauthentication"
        )

    requested_id = os.environ.get("MYSA_DEVICE_ID")
    requested_target = os.environ.get("MYSA_SET_CELSIUS")
    if requested_target is not None and not requested_id:
        raise RuntimeError(
            "Set MYSA_DEVICE_ID together with MYSA_SET_CELSIUS to explicitly "
            "target a thermostat"
        )
    target = None if requested_target is None else float(requested_target)
    if target is not None and not math.isfinite(target):
        raise ValueError("MYSA_SET_CELSIUS must be a finite Celsius value")

    async with aiohttp.ClientSession() as session:
        auth = MysaAuth(
            session,
            refresh_token,
            user_agent=USER_AGENT,
            on_refresh_token=lambda value: save_secret(token_path, value),
            email=email,
            password=password,
        )
        if refresh_token is None:
            await auth.sign_in(email or "", password or "")
        cloud = MysaClient(session, auth, user_agent=USER_AGENT)
        account = await cloud.bootstrap()
        devices = {
            device_id: record.model
            for device_id, record in account.devices.items()
            if record.model
        }
        if not devices:
            raise RuntimeError("No thermostats with a known model were discovered")
        if requested_id is not None and requested_id not in devices:
            raise RuntimeError("MYSA_DEVICE_ID was not found in account discovery")

        selected_ids = [requested_id] if requested_id else list(devices)
        if target is not None:
            model = devices[requested_id or ""]
            if not supports_commands(model):
                raise RuntimeError(
                    "MQTT setpoint commands currently support Baseboard Lite only"
                )
            capabilities = await cloud.get_capabilities(requested_id or "")
            limits = capabilities.heat_setpoint_range
            if limits is None or not limits[0] <= target <= limits[1]:
                raise ValueError(
                    "MYSA_SET_CELSIUS is outside the device's advertised heat range"
                )

        print(f"Discovered {len(devices)} thermostat(s).")
        print("Connecting to the legacy Mysa MQTT Identity Pool and subscribing.")
        async with MysaMqttClient(auth.async_get_id_token, devices) as mqtt:
            listener = asyncio.create_task(listen_events(mqtt, args.duration))
            try:
                for device_id in selected_ids:
                    live = await mqtt.request_live_status(device_id)
                    print(
                        "Live-status request: "
                        f"broker accepted={live.broker_accepted!r}, "
                        f"timed out={live.timed_out}"
                    )
                    echo = await mqtt.echo(device_id)
                    print(
                        "Echo check: "
                        f"broker accepted={echo.broker_accepted!r}, "
                        f"reply received={echo.echoed}, timed out={echo.timed_out}"
                    )

                if target is not None and requested_id is not None:
                    result = await mqtt.set_setpoint(requested_id, target)
                    print(
                        "Setpoint result: "
                        f"broker accepted={result.broker_accepted!r}, "
                        f"device confirmed={result.device_confirmed}, "
                        f"failure={result.failure.value if result.failure else None}"
                    )
                    if result.state is not None:
                        print(
                            "Acknowledged state: "
                            f"mode={result.state.mode!r}, "
                            f"setpoint={result.state.setpoint!r} °C"
                        )
                await listener
            finally:
                if not listener.done():
                    listener.cancel()
                with suppress(asyncio.CancelledError):
                    await listener


def parse_args() -> argparse.Namespace:
    """Parse bounded listening options before opening network sessions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--duration",
        type=float,
        default=20,
        help="seconds to listen for device events after status and echo requests",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
