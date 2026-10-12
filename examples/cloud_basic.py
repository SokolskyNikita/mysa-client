"""Read Mysa cloud state; send a heating target only when explicitly requested."""

from __future__ import annotations

import argparse
import asyncio
import math
import os
from pathlib import Path
import tempfile

import aiohttp

from mysa_client.cloud import Mode, MysaAuth, MysaClient

USER_AGENT = "mysa-client-example/0.1"


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
    """Load a previously saved refresh token without printing it."""
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None


def choose_device(devices: dict[str, object], requested_id: str | None) -> str:
    """Choose a discovered account device without printing its MAC-like ID."""
    if not devices:
        raise RuntimeError("No thermostats were found in this Mysa account")
    rows = list(devices.items())
    selected_id = requested_id or os.environ.get("MYSA_DEVICE_ID")
    if selected_id:
        if selected_id not in devices:
            raise RuntimeError("MYSA_DEVICE_ID was not present in account discovery")
        return selected_id
    if len(rows) == 1:
        return rows[0][0]
    for index, (_device_id, record) in enumerate(rows, start=1):
        name = getattr(record, "name", None) or "Unnamed thermostat"
        model = getattr(record, "model", None) or "model unknown"
        print(f"{index}. {name} ({model})")
    answer = input("Choose a thermostat number: ").strip()
    try:
        index = int(answer) - 1
    except ValueError as error:
        raise RuntimeError("Choose one of the listed thermostat numbers") from error
    if not 0 <= index < len(rows):
        raise RuntimeError("Choose one of the listed thermostat numbers")
    return rows[index][0]


def refresh_token_path() -> Path:
    """Resolve the caller's credential storage location."""
    return Path(
        os.environ.get(
            "MYSA_REFRESH_TOKEN_FILE",
            Path.home() / ".config" / "mysa-client" / "refresh-token",
        )
    ).expanduser()


async def main(args: argparse.Namespace) -> None:
    """Authenticate, discover, read, and optionally request a setpoint."""
    token_path = await asyncio.to_thread(refresh_token_path)
    email = os.environ.get("MYSA_EMAIL")
    password = os.environ.get("MYSA_PASSWORD")
    refresh_token = await asyncio.to_thread(load_refresh_token, token_path)
    if refresh_token is None and not (email and password):
        raise RuntimeError(
            "Set MYSA_EMAIL and MYSA_PASSWORD for first sign-in, or provide a saved "
            "refresh token and credentials for reauthentication"
        )

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
        client = MysaClient(session, auth, user_agent=USER_AGENT)

        account = await client.bootstrap()
        print(f"Discovered {len(account.devices)} thermostat(s) in the Mysa account.")
        device_id = choose_device(account.devices, args.device_id)
        state = await client.get_state(device_id)
        print(f"Model: {state.model or 'unknown'}")
        print(
            f"Reported mode: {state.mode.name if state.mode is not None else 'unknown'}"
        )
        print(f"Room temperature: {state.room_temperature!r} °C")
        print(f"Reported heating target: {state.heat_setpoint!r} °C")
        print(f"Reading timestamp: {state.reading_time!s}")

        requested = (
            args.set_celsius
            if args.set_celsius is not None
            else os.environ.get("MYSA_SET_CELSIUS")
        )
        if requested is None:
            return
        target = float(requested)
        if not math.isfinite(target):
            raise ValueError("Setpoint must be a finite Celsius value")
        capabilities = await client.get_capabilities(device_id)
        limits = capabilities.heat_setpoint_range
        if limits is None or not limits[0] <= target <= limits[1]:
            raise ValueError("Setpoint is outside the advertised heating range")
        if Mode.HEAT not in capabilities.modes:
            raise ValueError("This thermostat does not advertise heat mode")
        await client.set_temperature(device_id, Mode.HEAT, target)
        print("The cloud API accepted the heating-target request.")
        readback = await client.get_state(device_id)
        print(f"Reported heating target after readback: {readback.heat_setpoint!r} °C")
        print("The response and readback do not confirm physical heater operation.")


def parse_args() -> argparse.Namespace:
    """Parse optional actions before opening an HTTP session."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device-id", help="account device ID; otherwise prompt if needed"
    )
    parser.add_argument(
        "--set-celsius",
        type=float,
        help="explicitly request a heating target in Celsius",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
