"""Discover, pair, read, observe, and optionally write a Mysa HomeKit device."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

from zeroconf.asyncio import AsyncZeroconf

from mysa_client.homekit import Field, HomeKitEvent, HomeKitProtocol

DISCOVERY_SECONDS = 8
DISCOVERY_POLL_INTERVAL = 0.25


def save_pairing(path: Path, pairing_data: dict[str, Any]) -> None:
    """Atomically save pairing keys with owner-only permissions."""
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
            json.dump(pairing_data, temporary, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


async def discover(
    protocol: HomeKitProtocol, seconds: float = DISCOVERY_SECONDS
) -> None:
    """Poll and print discoveries for a bounded interval."""
    print(f"Listening for HomeKit accessories for {seconds:g} seconds…")
    seen: set[str] = set()
    try:
        async with asyncio.timeout(seconds):
            while True:
                async for accessory in protocol.async_discover():
                    identity = accessory.id.casefold()
                    if identity in seen:
                        continue
                    seen.add(identity)
                    endpoint = accessory.endpoint
                    address = f" at {endpoint.host}:{endpoint.port}" if endpoint else ""
                    print(
                        f"id={accessory.id} name={accessory.name!r} "
                        f"model={accessory.model or 'unknown'} "
                        f"paired={accessory.paired}{address}"
                    )
                await asyncio.sleep(DISCOVERY_POLL_INTERVAL)
    except TimeoutError:
        pass


def event_summary(event: HomeKitEvent) -> str:
    """Format semantic event values without exposing raw HAP payloads."""
    values = {field.value: value for field, value in event.values.items()}
    failed = {field.value: status for field, status in event.failed_fields.items()}
    return json.dumps(
        {
            "values": values,
            "failed_fields": failed,
            "reconnected": event.reconnected,
            "refresh_required": event.refresh_required,
            "capabilities_changed": event.capabilities_changed,
        },
        default=str,
        sort_keys=True,
    )


async def run(args: argparse.Namespace) -> None:
    """Run the requested HomeKit operations and close all owned sessions."""
    pairing_path = args.pairing_file.expanduser()
    zeroconf = AsyncZeroconf()
    protocol = HomeKitProtocol(zeroconf)
    try:
        await protocol.async_start()

        pairing_data: dict[str, Any] | None = None
        if args.discover:
            await discover(protocol, seconds=DISCOVERY_SECONDS)

        if args.discover and not args.pair_id and not args.use_saved_pairing:
            return

        if args.pair_id:
            if not args.pin:
                raise RuntimeError("--pin is required with --pair-id")
            session = await protocol.async_start_pairing(args.pair_id)

            async def persist(data: Any) -> None:
                save_pairing(pairing_path, dict(data))

            pairing_data, info = await session.async_complete(
                args.pin,
                on_pairing_saved=persist,
            )
            print(
                f"Pairing saved to {pairing_path}; "
                f"model={info.model or 'unknown'}, firmware={info.firmware or 'unknown'}"
            )
        elif args.use_saved_pairing or (pairing_path.exists() and not args.discover):
            if not pairing_path.exists():
                raise RuntimeError(f"No saved pairing file at {pairing_path}")
            pairing_json = await asyncio.to_thread(
                pairing_path.read_text, encoding="utf-8"
            )
            pairing_data = json.loads(pairing_json)
        elif not args.discover:
            await discover(protocol, seconds=DISCOVERY_SECONDS)

        if pairing_data is None:
            return

        device = protocol.load_pairing(pairing_data)
        unsubscribe = device.add_listener(
            lambda event: print(f"event {event_summary(event)}")
        )
        try:
            await device.async_connect()
            fields = [
                Field.CURRENT_TEMPERATURE,
                Field.TARGET_TEMPERATURE,
                Field.CURRENT_ACTION,
                Field.TARGET_MODE,
            ]
            values = await device.async_read(fields)
            print(
                "Read: "
                + json.dumps(
                    {field.value: value for field, value in values.items()},
                    default=str,
                    sort_keys=True,
                )
            )

            if args.set_target_c is not None:
                if not math.isfinite(args.set_target_c):
                    raise ValueError("Setpoint must be a finite Celsius value")
                low, high, step = device.temperature_range
                if low is None or high is None or not low <= args.set_target_c <= high:
                    raise ValueError(
                        "Setpoint is outside the accessory's advertised range"
                    )
                if step is not None:
                    step_count = (args.set_target_c - low) / step
                    if not math.isclose(step_count, round(step_count), abs_tol=1e-6):
                        raise ValueError(
                            "Setpoint does not match the accessory's advertised step"
                        )
                await device.async_write({Field.TARGET_TEMPERATURE: args.set_target_c})
                print("The HomeKit accessory accepted the target write.")
                readback = await device.async_read([Field.TARGET_TEMPERATURE])
                print(
                    "Target readback: "
                    + json.dumps(
                        {field.value: value for field, value in readback.items()},
                        default=str,
                        sort_keys=True,
                    )
                )
                print("Readback is not proof of physical heater operation.")

            if args.listen_seconds > 0:
                print(
                    f"Listening for accessory events for {args.listen_seconds:g} seconds…"
                )
                await asyncio.sleep(args.listen_seconds)
        finally:
            unsubscribe()
            await device.async_close()
    finally:
        try:
            await protocol.async_stop()
        finally:
            await zeroconf.async_close()


def parse_args() -> argparse.Namespace:
    """Parse options without opening the network for --help."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--discover", action="store_true", help="list nearby accessories"
    )
    parser.add_argument(
        "--use-saved-pairing",
        action="store_true",
        help="read the saved pairing after discovery when --discover is also set",
    )
    parser.add_argument("--pair-id", help="HomeKit accessory ID shown by discovery")
    parser.add_argument("--pin", help="HomeKit setup code; used only with --pair-id")
    parser.add_argument(
        "--pairing-file",
        type=Path,
        default=Path(
            os.environ.get(
                "MYSA_PAIRING_FILE",
                Path.home() / ".config" / "mysa-client" / "pairing.json",
            )
        ),
        help="path for protected pairing credentials",
    )
    parser.add_argument(
        "--set-target-c",
        type=float,
        help="explicitly write a target temperature in Celsius",
    )
    parser.add_argument(
        "--listen-seconds",
        type=float,
        default=30,
        help="time to keep the event subscription open (0 disables listening)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
