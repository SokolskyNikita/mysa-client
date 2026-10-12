"""Cross-path routing tests without Home Assistant or transport SDKs."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from mysa_client.runtime.routing import (
    CommandRouter,
    RouteOutcome,
    RouteRequest,
)


class _Local:
    def __init__(self) -> None:
        self.available = True
        self.ready = True
        self.values: dict[str, Any] = {}
        self.supported = {"setpoint", "mode"}
        self.write: Any = None
        self.read: Any = None
        self.stopped = False
        self.calls: list[str] = []

    def supports(self, field: str) -> bool:
        return field in self.supported

    def start_write(self, values: Mapping[str, Any]) -> asyncio.Task[None]:
        self.calls.append("start_write")

        async def run() -> None:
            if self.write is not None:
                await self.write(values)
            self.values.update(values)

        return asyncio.create_task(run())

    async def async_read(self, fields: tuple[str, ...]) -> Mapping[str, Any]:
        if self.read is not None:
            return await self.read(fields)
        return {field: self.values[field] for field in fields if field in self.values}

    def mark_available(self) -> None:
        self.available = True

    def mark_unavailable(self, error: str) -> None:
        self.available = False

    async def async_stop(self) -> None:
        self.stopped = True
        self.calls.append("stop")


class _Cloud:
    def __init__(self) -> None:
        self.applied: dict[str, Any] = {}


def _router(
    local: _Local | None,
    cloud: _Cloud | None,
    *,
    timeout: float = 0.01,
    on_reconcile_ready: Any = None,
) -> CommandRouter[str, Any, _Cloud, Mapping[str, Any]]:
    async def read_state(value: _Cloud) -> Mapping[str, Any]:
        return value.applied

    return CommandRouter(
        device_id="device",
        name="thermostat",
        local=local,
        cloud_getter=lambda: cloud,
        cloud_read=read_state,
        write_timeout=timeout,
        is_local_error=lambda error: isinstance(error, OSError),
        is_local_rejection=lambda error: isinstance(error, ValueError),
        is_cloud_confirmation_error=lambda error: isinstance(error, OSError),
        is_ambiguous_cloud_error=lambda error: isinstance(error, ConnectionError),
        is_reconcile_error=lambda error: isinstance(error, OSError),
        on_reconcile_ready=on_reconcile_ready,
    )


async def _unavailable_read(_fields: tuple[str, ...]) -> Mapping[str, Any]:
    raise OSError("local read unavailable")


@pytest.mark.asyncio
async def test_local_acceptance_never_calls_cloud() -> None:
    local = _Local()
    cloud = _Cloud()
    router = _router(local, cloud)

    async def write_cloud(_cloud: _Cloud) -> None:
        pytest.fail("cloud should not be called for a successful local write")

    result = await router.async_route(
        local_values={"setpoint": 20},
        cloud_write=write_cloud,
        cloud_applied=None,
    )

    assert result.outcome is RouteOutcome.LOCAL_ACCEPTED
    assert local.values["setpoint"] == 20
    assert cloud.applied == {}


@pytest.mark.asyncio
async def test_rejected_local_command_is_not_replayed_through_cloud() -> None:
    local = _Local()
    local.write = lambda _values: _raise(ValueError("device rejected command"))
    cloud = _Cloud()
    router = _router(local, cloud)
    cloud_writes: list[int] = []

    async def write_cloud(_cloud: _Cloud) -> None:
        cloud_writes.append(1)

    result = await router.async_route(
        local_values={"setpoint": 20},
        cloud_write=write_cloud,
        cloud_applied=None,
    )

    assert result.outcome is RouteOutcome.LOCAL_REJECTED
    assert isinstance(result.error, ValueError)
    assert cloud_writes == []


@pytest.mark.asyncio
async def test_timed_out_local_write_is_confirmed_before_cloud_fallback() -> None:
    local = _Local()
    cloud = _Cloud()
    release = asyncio.Event()
    local.write = lambda _values: release.wait()
    local.read = lambda _fields: asyncio.sleep(0, result={"setpoint": 21})
    router = _router(local, cloud, timeout=0.001)
    cloud_writes: list[float] = []

    async def write_cloud(_cloud: _Cloud) -> None:
        cloud_writes.append(21)

    result = await router.async_route(
        local_values={"setpoint": 21},
        cloud_write=write_cloud,
        cloud_applied=None,
    )

    assert result.outcome is RouteOutcome.LOCAL_CONFIRMED
    assert cloud_writes == []
    assert router.pending_local is not None
    release.set()
    await asyncio.gather(router.pending_local, return_exceptions=True)
    await router.async_reconcile()


@pytest.mark.asyncio
async def test_cloud_reported_state_prevents_duplicate_after_local_timeout() -> None:
    local = _Local()
    local.write = lambda _values: asyncio.sleep(3600)
    local.read = _unavailable_read
    cloud = _Cloud()
    cloud.applied["setpoint"] = 21
    router = _router(local, cloud, timeout=0.001)
    writes: list[int] = []

    async def write_cloud(_cloud: _Cloud) -> None:
        writes.append(1)

    result = await router.async_route(
        local_values={"setpoint": 21},
        cloud_write=write_cloud,
        cloud_applied=lambda state: state.get("setpoint") == 21,
    )

    assert result.outcome is RouteOutcome.CLOUD_REPORTED
    assert writes == []
    assert router.pending_local is not None
    await router.async_stop()


@pytest.mark.asyncio
async def test_latest_cloud_intent_is_reasserted_after_late_write() -> None:
    local = _Local()
    local.read = _unavailable_read
    release = asyncio.Event()
    local.write = lambda _values: release.wait()
    cloud = _Cloud()
    applied: list[int] = []
    router: CommandRouter[str, Any, _Cloud, Mapping[str, Any]]
    reconciled = asyncio.Event()
    background: list[asyncio.Task[None]] = []

    def schedule() -> None:
        background.append(asyncio.create_task(router.async_reconcile()))
        reconciled.set()

    router = _router(local, cloud, timeout=0.001, on_reconcile_ready=schedule)

    async def write_22(target: _Cloud) -> None:
        target.applied["setpoint"] = 22
        applied.append(22)

    async def write_19(target: _Cloud) -> None:
        target.applied["setpoint"] = 19
        applied.append(19)

    async def write_18(target: _Cloud) -> None:
        target.applied["setpoint"] = 18
        applied.append(18)

    await router.async_route(
        local_values={"setpoint": 22},
        cloud_write=write_22,
        cloud_applied=None,
    )
    await router.async_route(
        local_values=None,
        cloud_write=write_19,
        cloud_applied=None,
        intent={"setpoint": 19},
    )
    await router.async_route(
        local_values=None,
        cloud_write=write_18,
        cloud_applied=None,
        intent={"setpoint": 18},
    )

    release.set()
    await reconciled.wait()
    await asyncio.gather(*background)

    assert applied == [22, 19, 18, 18]
    assert cloud.applied["setpoint"] == 18
    assert router.pending_local is None
    assert router.reconcile == {}


@pytest.mark.asyncio
async def test_multi_field_intent_replays_one_cloud_operation() -> None:
    local = _Local()
    local.read = _unavailable_read
    release = asyncio.Event()
    local.write = lambda _values: release.wait()
    cloud = _Cloud()
    replayed: list[int] = []
    router: CommandRouter[str, Any, _Cloud, Mapping[str, Any]]
    reconciled = asyncio.Event()
    background: list[asyncio.Task[None]] = []

    def schedule() -> None:
        background.append(asyncio.create_task(router.async_reconcile()))
        reconciled.set()

    router = _router(local, cloud, timeout=0.001, on_reconcile_ready=schedule)

    async def old_write(_target: _Cloud) -> None:
        pass

    async def combined_write(_target: _Cloud) -> None:
        replayed.append(1)

    await router.async_route(
        local_values={"setpoint": 22},
        cloud_write=old_write,
        cloud_applied=None,
    )
    await router.async_route(
        local_values=None,
        cloud_write=combined_write,
        cloud_applied=None,
        intent={"mode": "fan", "fan_speed": 3},
    )
    assert replayed == [1]
    release.set()
    await reconciled.wait()
    await asyncio.gather(*background)

    assert replayed == [1, 1]
    assert router.pending_local is None


@pytest.mark.asyncio
async def test_lost_cloud_response_keeps_new_intent_for_late_write_repair() -> None:
    local = _Local()
    local.read = _unavailable_read
    release = asyncio.Event()
    local.write = lambda _values: release.wait()
    cloud = _Cloud()
    updates: list[int] = []
    lose_first_response = True
    router: CommandRouter[str, Any, _Cloud, Mapping[str, Any]]
    reconciled = asyncio.Event()
    background: list[asyncio.Task[None]] = []

    def schedule() -> None:
        background.append(asyncio.create_task(router.async_reconcile()))
        reconciled.set()

    router = _router(local, cloud, timeout=0.001, on_reconcile_ready=schedule)

    async def write_old(target: _Cloud) -> None:
        target.applied["setpoint"] = 22
        updates.append(22)

    async def write_latest(target: _Cloud) -> None:
        nonlocal lose_first_response
        target.applied["setpoint"] = 18
        updates.append(18)
        if lose_first_response:
            lose_first_response = False
            raise ConnectionError("response lost after apply")

    await router.async_route(
        local_values={"setpoint": 22},
        cloud_write=write_old,
        cloud_applied=None,
    )
    with pytest.raises(ConnectionError):
        await router.async_route(
            local_values=None,
            cloud_write=write_latest,
            cloud_applied=None,
            intent={"setpoint": 18},
        )

    release.set()
    await reconciled.wait()
    await asyncio.gather(*background)

    assert updates == [22, 18, 18]
    assert cloud.applied["setpoint"] == 18
    assert router.pending_local is None


@pytest.mark.asyncio
async def test_cancelled_newer_command_is_reasserted_after_late_local_write() -> None:
    local = _Local()
    local.read = _unavailable_read
    release_local = asyncio.Event()
    local.write = lambda _values: release_local.wait()
    cloud = _Cloud()
    applied: list[int] = []
    newer_started = asyncio.Event()
    attempts = 0
    router: CommandRouter[str, Any, _Cloud, Mapping[str, Any]]
    reconciled = asyncio.Event()
    background: list[asyncio.Task[None]] = []

    def schedule() -> None:
        background.append(asyncio.create_task(router.async_reconcile()))
        reconciled.set()

    router = _router(local, cloud, timeout=0.001, on_reconcile_ready=schedule)

    async def write_old(_target: _Cloud) -> None:
        applied.append(22)

    async def write_new(_target: _Cloud) -> None:
        nonlocal attempts
        attempts += 1
        applied.append(18)
        if attempts == 1:
            newer_started.set()
            await asyncio.Future()

    await router.async_route(
        local_values={"setpoint": 22},
        cloud_write=write_old,
        cloud_applied=None,
    )
    command = asyncio.create_task(
        router.async_route(
            local_values=None,
            cloud_write=write_new,
            cloud_applied=None,
            intent={"setpoint": 18},
        )
    )
    await newer_started.wait()
    command.cancel()
    with pytest.raises(asyncio.CancelledError):
        await command

    release_local.set()
    await reconciled.wait()
    await asyncio.gather(*background)

    assert applied == [22, 18, 18]
    assert router.pending_local is None


@pytest.mark.asyncio
async def test_stopping_closes_local_session_before_cancel_and_reconciles() -> None:
    local = _Local()
    local.read = _unavailable_read
    release = asyncio.Event()

    async def waiting_write(_values: Mapping[str, Any]) -> None:
        try:
            await release.wait()
        except asyncio.CancelledError:
            local.calls.append("write_cancelled")
            raise

    local.write = waiting_write
    cloud = _Cloud()
    applied: list[int] = []
    router = _router(local, cloud, timeout=0.001)

    async def write_cloud(_target: _Cloud) -> None:
        applied.append(1)

    async def newer_cloud_write(_target: _Cloud) -> None:
        applied.append(2)

    await router.async_route(
        local_values={"setpoint": 20},
        cloud_write=write_cloud,
        cloud_applied=None,
    )
    await router.async_route(
        local_values=None,
        cloud_write=newer_cloud_write,
        cloud_applied=None,
        intent={"setpoint": 18},
    )
    pending = router.pending_local
    assert pending is not None
    assert not pending.done()

    await router.async_stop()

    assert local.stopped
    assert local.calls == ["start_write", "stop", "write_cancelled"]
    assert pending.cancelled()
    assert applied == [1, 2, 2]


@pytest.mark.asyncio
async def test_preparation_runs_under_the_command_lock() -> None:
    local = _Local()
    cloud = _Cloud()
    release = asyncio.Event()
    started = asyncio.Event()

    async def slow_write(_values: Mapping[str, Any]) -> None:
        started.set()
        await release.wait()

    local.write = slow_write
    router = _router(local, cloud, timeout=2)

    async def cloud_write(_target: _Cloud) -> None:
        pass

    first = asyncio.create_task(
        router.async_route(
            local_values={"setpoint": 22},
            cloud_write=cloud_write,
            cloud_applied=None,
        )
    )
    await started.wait()
    prepared = asyncio.Event()

    def prepare() -> RouteRequest[str, Any, _Cloud, Mapping[str, Any]]:
        prepared.set()
        return RouteRequest(None, cloud_write, None, {"setpoint": 18})

    second = asyncio.create_task(router.async_route_prepared(prepare))
    await asyncio.sleep(0)
    assert not prepared.is_set()
    release.set()
    await asyncio.gather(first, second)
    assert prepared.is_set()


async def _raise(error: BaseException) -> None:
    raise error
